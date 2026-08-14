#!/usr/bin/env python3
"""
main.py - 军事无线电信号接收与元数据分析系统 主入口

使用方法:
    python main.py nodes              # 测试可用 KiwiSDR 节点
    python main.py scan               # 扫描所有配置频率
    python main.py monitor            # 启动持续监听
    python main.py monitor -f 11175   # 监听指定频率
    python main.py analyze <file>     # 分析指定录音文件
    python main.py report             # 生成分析报告
    python main.py clean              # 清理无意义的录音文件
"""

import argparse
import asyncio
import logging
import signal
import sys
import os
import io
from pathlib import Path

# 修复 Windows 控制台 GBK 编码无法输出 Unicode 字符的问题
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import yaml

from src.db import Database
from src.node_manager import NodeManager
from src.receiver import SignalReceiver, FrequencyTarget
from src.analyzer import SignalAnalyzer
from src.reporter import Reporter
from src.web_server import WebMonitorServer
from src.schedule import check_targets, format_window
from src.exceptions import ConfigError


# ==================== 日志配置 ====================
def setup_logging(verbose: bool = False):
    """配置日志格式和级别。"""
    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S"
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(handler)

    # 降低第三方库的日志级别
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("matplotlib").setLevel(logging.WARNING)


# ==================== 配置加载 ====================
def load_config(config_path: str = "config.yaml") -> dict:
    """加载主配置文件。"""
    path = Path(config_path)
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_frequency_data(freq_path: str = "frequencies.yaml") -> dict:
    """加载频率数据库的原始 YAML 数据 (保留 active_hours 等字段)。"""
    path = Path(freq_path)
    if not path.exists():
        raise ConfigError(f"频率文件不存在: {freq_path}")

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def targets_from_data(data: dict) -> list:
    """把频率数据库原始数据转换为 FrequencyTarget 列表。"""
    targets = []
    for network_key, network_data in (data or {}).items():
        if not isinstance(network_data, dict):
            continue
        for freq_info in network_data.get("frequencies", []):
            targets.append(FrequencyTarget(
                freq_khz=freq_info["freq"],
                mode=freq_info.get("mode", "USB"),
                description=freq_info.get("description", ""),
                network=network_key,
                priority=freq_info.get("priority", "medium"),
                active_hours=freq_info.get("active_hours", ""),
            ))

    return targets


def load_frequencies(freq_path: str = "frequencies.yaml") -> list:
    """
    加载频率数据库并转换为 FrequencyTarget 列表。

    Returns:
        FrequencyTarget 列表
    """
    return targets_from_data(load_frequency_data(freq_path))


# ==================== 子命令实现 ====================

async def cmd_nodes(config: dict, db: Database, args):
    """测试并列出可用的 KiwiSDR 节点。"""
    print("\n[*] 正在检查 KiwiSDR 节点可用性...\n")

    node_mgr = NodeManager(config, db)
    await node_mgr.check_all_nodes(timeout=args.timeout)

    print("\n" + node_mgr.format_node_table())


async def cmd_scan(config: dict, db: Database, args):
    """扫描所有配置频率。"""
    all_freqs = load_frequencies(args.freq_file)

    # 按优先级过滤
    if args.priority:
        all_freqs = [f for f in all_freqs if f.priority == args.priority]

    # 按网络过滤
    if args.network:
        all_freqs = [f for f in all_freqs
                     if args.network.lower() in f.network.lower()]

    if not all_freqs:
        print("[ERROR] 没有匹配的频率")
        return

    print(f"\n[SCAN] 准备扫描 {len(all_freqs)} 个频率...\n")

    # 选择节点
    node = await _select_node(config, db, args)
    if not node:
        return

    # 执行扫描
    receiver = SignalReceiver(config, db)
    results = await receiver.scan(node, all_freqs,
                                   dwell_time=args.dwell)

    # 输出结果
    reporter = Reporter(db, config)
    reporter.print_scan_results(results)


async def cmd_monitor(config: dict, db: Database, args):
    """启动持续监听。"""
    freq_data = load_frequency_data(args.freq_file)
    all_freqs = targets_from_data(freq_data)

    # 选择频率
    if args.frequency:
        # 用户指定了具体频率
        freq_values = [float(f) for f in args.frequency]
        selected_freqs = [f for f in all_freqs if f.freq_khz in freq_values]
        # 如果数据库中没有，创建临时目标
        for fv in freq_values:
            if not any(f.freq_khz == fv for f in selected_freqs):
                selected_freqs.append(FrequencyTarget(
                    freq_khz=fv,
                    mode=args.mode or "USB",
                    description="用户自定义频率"
                ))
    else:
        # 默认只监听 high 优先级频率
        selected_freqs = [f for f in all_freqs if f.priority == "high"]
        if not selected_freqs:
            selected_freqs = all_freqs[:3]

    if not selected_freqs:
        print("[ERROR] 没有要监听的频率")
        return

    print(f"\n[MONITOR] 准备监听 {len(selected_freqs)} 个频率:")
    for f in selected_freqs:
        print(f"   - {f.freq_khz:>10.1f} kHz ({f.mode}) "
              f"[{format_window(f.active_hours)}] - {f.description}")

    # 活跃时段提示: frequencies.yaml 里的 active_hours 以前从没被读过，
    # "频率没错但时段不对"因此完全不可见
    for line in check_targets(freq_data, [f.freq_khz for f in selected_freqs]):
        print(line)

    # 选择节点
    node = await _select_node(config, db, args)
    if not node:
        return

    print(f"\n[NODE] 节点: {node.get('name', node['host'])}")
    print(f"[TIME] 时长: {'无限' if args.duration == 0 else f'{args.duration}s'}")
    print("[INFO] 按 Ctrl+C 停止监听\n")

    # 创建接收器并启动监听
    receiver = SignalReceiver(config, db)

    # 注册信号处理器
    def signal_handler(sig, frame):
        print("\n\n[STOP] 正在停止监听...")
        receiver.stop()

    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal_handler)

    def status_print(msg):
        print(f"  {msg}")

    await receiver.monitor(
        node=node,
        frequencies=selected_freqs,
        duration=args.duration,
        status_callback=status_print
    )


async def cmd_analyze(config: dict, db: Database, args):
    """分析指定的录音文件。"""
    file_path = args.file

    if not os.path.exists(file_path):
        print(f"[ERROR] 文件不存在: {file_path}")
        return

    print(f"\n[ANALYZE] 正在分析: {file_path}\n")

    # 统一工厂方法，保证和监听时用的是同一套分析参数
    analyzer = SignalAnalyzer.from_config(config)
    mode = getattr(args, "mode", None) or "USB"

    result = analyzer.analyze_file(file_path, mode=mode)

    if result is None:
        print("[ERROR] 分析失败")
        return

    # 格式化输出
    print("=" * 50)
    print("  [RESULT] 信号分析结果")
    print("=" * 50)
    print(f"  文件: {os.path.basename(file_path)}")
    print(f"  时长: {result['duration_seconds']:.2f}s")
    print(f"  采样率: {result['sample_rate']} Hz")
    print(f"  样本数: {result['total_samples']}")
    print("-" * 50)
    print(f"  [TIME-DOMAIN] 时域分析:")
    print(f"     RMS 能量: {result['rms']:.6f}")
    print(f"     峰值幅度: {result['peak_amplitude']:.6f}")
    print(f"     峰均比: {result['crest_factor_db']:.1f} dB")
    print(f"     总能量: {result['energy_total']:.4f}")
    print("-" * 50)
    band = result.get("passband_hz", [0, 0])
    print(f"  [FREQ-DOMAIN] 频域分析 (通带 {band[0]:.0f}-{band[1]:.0f} Hz, "
          f"模式 {result.get('demod_mode', '?')}):")
    print(f"     峰值频率: {result.get('peak_frequency_hz', 0):.1f} Hz")
    print(f"     频谱质心: {result.get('spectral_centroid_hz', 0):.1f} Hz")
    print(f"     占用带宽: {result.get('bandwidth_hz', 0):.1f} Hz "
          f"(旧口径 -{analyzer.bandwidth_threshold_db:.0f}dB: "
          f"{result.get('bandwidth_20db_hz', 0):.1f} Hz)")
    print(f"     带内 SNR: {result.get('snr_db', 0):.1f} dB "
          f"(噪声基底 {result.get('noise_floor_db', 0):.1f} dB)")
    print(f"     频谱平坦度: {result.get('spectral_flatness', 0):.6f}")
    print("-" * 50)
    print(f"  [FEATURES] 判别特征:")
    print(f"     包络音节率: {result.get('envelope_rate_hz', 0):.1f} Hz "
          f"(深度 {result.get('envelope_depth', 0):.2f})")
    print(f"     键控率: {result.get('keying_rate_hz', 0):.1f} Hz")
    print(f"     音调数: {result.get('tone_count', 0)} "
          f"(间距 {result.get('tone_spacing_hz', 0):.0f} Hz, "
          f"纯度 {result.get('tone_purity', 0):.2f})")
    print("-" * 50)
    print(f"  [MODULATION] 估计调制类型: {result.get('estimated_modulation', 'UNKNOWN')} "
          f"({result.get('modulation_description', '')})")
    print(f"     置信度: {result.get('modulation_confidence', 0):.2f}")
    scores = result.get("modulation_scores", {})
    if scores:
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        print(f"     各类别得分: " +
              ", ".join(f"{k}={v:.2f}" for k, v in ranked))
    print("=" * 50)

    # 显示前 5 个频谱峰值
    peaks = result.get("fft_peak_magnitudes", [])
    if peaks:
        print("\n  [PEAKS] 频谱主要峰值:")
        for i, p in enumerate(peaks[:5], 1):
            print(f"     {i}. {p['freq_hz']:.1f} Hz ({p['magnitude_db']:.1f} dB)")


async def cmd_report(config: dict, db: Database, args):
    """生成分析报告。"""
    reporter = Reporter(db, config)
    path = reporter.generate_full_report()
    print(f"\n[OK] 报告已生成: {path}")


async def cmd_web(config: dict, db: Database, args):
    """启动 Web 监听界面。"""
    # 加载频率数据
    freq_path = args.freq_file if hasattr(args, 'freq_file') else "frequencies.yaml"
    with open(freq_path, "r", encoding="utf-8") as f:
        freq_data = yaml.safe_load(f)

    server = WebMonitorServer(config, db, freq_data)
    await server.start(host=args.host, port=args.port)


async def cmd_clean(config: dict, db: Database, args):
    """清理无意义的录音文件。"""
    from clean_recordings import RecordingClassifier, scan_recordings, \
        print_report, delete_junk

    # 确定录音目录
    recordings_dir = config.get("recording", {}).get(
        "output_dir", "data/recordings"
    )

    # 创建分析器 (统一工厂方法)
    analyzer = SignalAnalyzer.from_config(config)

    # 创建分类器 (解调模式决定 SNR 与调制判定的通带)
    demod_mode = getattr(args, "mode", None) or "USB"
    classifier = RecordingClassifier(
        min_duration=args.min_duration,
        min_snr=args.min_snr,
        analyzer=analyzer,
        mode=demod_mode,
    )

    mode_str = '⚠ 删除模式' if args.delete else '👁 预览模式'
    print(f"\n[CLEAN] 录音清理 ({mode_str})")
    print(f"  录音目录: {recordings_dir}")
    print(f"  解调模式: {demod_mode}")
    print(f"  最短时长: {args.min_duration}s | 最低 SNR: {args.min_snr} dB\n")

    junk_list, good_list = scan_recordings(
        recordings_dir, classifier, verbose=args.verbose if hasattr(args, 'verbose') else False
    )

    print_report(junk_list, good_list)

    if args.delete and junk_list:
        deleted, db_cleaned = delete_junk(
            junk_list, db, args.clean_db
        )
        junk_size_mb = sum(r["file_size_kb"] for r in junk_list) / 1024
        print(f"\n[DONE] 已删除 {deleted} 个垃圾文件，释放 {junk_size_mb:.1f} MB")
        if args.clean_db:
            print(f"[DONE] 已清理 {db_cleaned} 条数据库记录")
    elif not args.delete and junk_list:
        print(f"\n[TIP] 预览模式，加 --delete 参数实际删除:")
        print(f"       python main.py clean --delete")


# ==================== 辅助函数 ====================

async def _select_node(config: dict, db: Database, args) -> dict:
    """选择一个可用的 KiwiSDR 节点。"""
    node_mgr = NodeManager(config, db)

    # 如果用户指定了节点
    if hasattr(args, 'node') and args.node:
        node = node_mgr.get_node_by_name(args.node)
        if node:
            # 验证连通性
            available = await node_mgr.check_all_nodes(timeout=10)
            if any(n["host"] == node["host"] for n in available):
                return node
            else:
                print(f"[WARN] 指定的节点 '{args.node}' 不可用")
        else:
            print(f"[WARN] 未找到节点: {args.node}")

    # 自动选择最佳节点
    print("[SEARCH] 正在查找可用节点...")
    available = await node_mgr.check_all_nodes(timeout=10)

    if not available:
        print("[ERROR] 没有可用的 KiwiSDR 节点")
        print("[TIP] 提示: 请检查网络连接，或在 config.yaml 中添加更多节点")
        return None

    best = node_mgr.get_best_node()
    print(f"[OK] 选择节点: {best.get('name', best['host'])} "
          f"(延迟 {best.get('latency_ms', 0):.0f}ms)")
    return best


# ==================== 主入口 ====================

def main():
    """主入口函数。"""
    parser = argparse.ArgumentParser(
        description="MilRadio - 军事无线电信号接收与元数据分析系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py nodes                       # 检查节点可用性
  python main.py scan                        # 扫描所有军事频率
  python main.py scan --network hfgcs        # 只扫描 HFGCS 频率
  python main.py monitor                     # 监听高优先级频率
  python main.py monitor -f 11175 8992       # 监听指定频率
  python main.py monitor --duration 300      # 监听 5 分钟
  python main.py analyze recording.wav       # 分析录音文件
  python main.py report                      # 生成分析报告
  python main.py clean                       # 预览垃圾录音
  python main.py clean --delete              # 删除垃圾录音
        """
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="显示详细调试信息")
    parser.add_argument("-c", "--config", default="config.yaml",
                        help="配置文件路径 (默认: config.yaml)")

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # --- nodes 命令 ---
    p_nodes = subparsers.add_parser("nodes", help="测试 KiwiSDR 节点可用性")
    p_nodes.add_argument("--timeout", type=float, default=10,
                         help="连接超时时间 (秒)")

    # --- scan 命令 ---
    p_scan = subparsers.add_parser("scan", help="扫描所有配置频率")
    p_scan.add_argument("--freq-file", default="frequencies.yaml",
                        help="频率数据库文件")
    p_scan.add_argument("--network", type=str, default=None,
                        help="只扫描指定网络 (如 hfgcs, nato)")
    p_scan.add_argument("--priority", type=str, default=None,
                        choices=["high", "medium", "low"],
                        help="只扫描指定优先级")
    p_scan.add_argument("--dwell", type=float, default=None,
                        help="每频率停留时间 (秒)")
    p_scan.add_argument("--node", type=str, default=None,
                        help="指定使用的节点名称")

    # --- monitor 命令 ---
    p_monitor = subparsers.add_parser("monitor", help="启动持续监听")
    p_monitor.add_argument("-f", "--frequency", nargs="+", type=float,
                           help="指定监听频率 (kHz)，可多个")
    p_monitor.add_argument("-m", "--mode", type=str, default=None,
                           choices=["USB", "LSB", "AM", "CW"],
                           help="调制模式")
    p_monitor.add_argument("--duration", type=float, default=0,
                           help="监听时长 (秒)，0=无限")
    p_monitor.add_argument("--freq-file", default="frequencies.yaml",
                           help="频率数据库文件")
    p_monitor.add_argument("--node", type=str, default=None,
                           help="指定使用的节点名称")

    # --- analyze 命令 ---
    p_analyze = subparsers.add_parser("analyze", help="分析录音文件")
    p_analyze.add_argument("file", type=str, help="WAV 录音文件路径")
    p_analyze.add_argument("-m", "--mode", type=str, default="USB",
                           choices=["USB", "LSB", "AM", "CW", "CWN"],
                           help="录音时使用的解调模式 (决定分析通带)")

    # --- report 命令 ---
    p_report = subparsers.add_parser("report", help="生成分析报告")

    # --- web 命令 ---
    p_web = subparsers.add_parser("web", help="启动 Web 实时监听界面")
    p_web.add_argument("--host", type=str, default="0.0.0.0",
                       help="Web 服务器监听地址 (默认: 0.0.0.0)")
    p_web.add_argument("--port", type=int, default=8888,
                       help="Web 服务器端口 (默认: 8888)")
    p_web.add_argument("--freq-file", default="frequencies.yaml",
                       help="频率数据库文件")

    # --- clean 命令 ---
    p_clean = subparsers.add_parser("clean", help="清理无意义的录音文件")
    p_clean.add_argument("--delete", action="store_true",
                         help="实际删除垃圾文件（默认只预览）")
    p_clean.add_argument("--clean-db", action="store_true",
                         help="同时清理数据库中对应的信号记录")
    p_clean.add_argument("--min-duration", type=float, default=2.0,
                         help="最短有效时长 (秒, 默认: 2.0)")
    p_clean.add_argument("--min-snr", type=float, default=5.0,
                         help="最低有效 SNR (dB, 默认: 5.0)")
    p_clean.add_argument("-m", "--mode", type=str, default="USB",
                         choices=["USB", "LSB", "AM", "CW", "CWN"],
                         help="录音时的解调模式, 决定分析通带 (默认: USB)")

    # 解析参数
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        print("\n[TIP] 试试: python main.py nodes")
        sys.exit(0)

    # 初始化
    setup_logging(verbose=args.verbose)

    # Windows 兼容性: 使用 SelectorEventLoop 避免 ProactorEventLoop 与某些库不兼容
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    db = None
    try:
        config = load_config(args.config)
        db = Database(config.get("database", {}).get("path", "data/radio_monitor.db"))

        # LOGO
        print("""
    ===================================================
    |   MilRadio - 军事无线电信号接收与分析系统          |
    |   v1.2  |  KiwiSDR Network Monitor              |
    ===================================================
        """)

        # 确保数据目录存在
        os.makedirs("data/recordings", exist_ok=True)
        os.makedirs("reports", exist_ok=True)

        # 路由子命令
        if args.command == "nodes":
            asyncio.run(cmd_nodes(config, db, args))
        elif args.command == "scan":
            asyncio.run(cmd_scan(config, db, args))
        elif args.command == "monitor":
            asyncio.run(cmd_monitor(config, db, args))
        elif args.command == "analyze":
            asyncio.run(cmd_analyze(config, db, args))
        elif args.command == "report":
            asyncio.run(cmd_report(config, db, args))
        elif args.command == "web":
            asyncio.run(cmd_web(config, db, args))
        elif args.command == "clean":
            asyncio.run(cmd_clean(config, db, args))
    except KeyboardInterrupt:
        print("\n\n[BYE] 再见!")
    except ConfigError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
    finally:
        if db:
            db.close()


if __name__ == "__main__":
    main()
