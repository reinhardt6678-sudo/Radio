#!/usr/bin/env python3
"""
clean_recordings.py - 无意义录音清理工具

扫描 data/recordings/ 中的 WAV 文件，通过音频分析自动识别
无意义的录音（噪声爆发、短促脉冲、纯底噪等），并可选择删除它们。

判定"无意义"的标准（满足任一即标记为垃圾）：
  1. 时长极短 (< 阈值秒)，只是噪声的短促触发
  2. SNR 极低 (< 阈值 dB)，几乎没有有效信号
  3. 频谱平坦度极高（接近白噪声）
  4. 分析器判定为 NOISE 类型
  5. "脉冲型"：时长短 + 峰均比高（如"滴"一声）

用法:
    python clean_recordings.py                   # 预览模式（只显示，不删除）
    python clean_recordings.py --delete           # 实际删除垃圾录音
    python clean_recordings.py --delete --clean-db  # 同时清理数据库记录
    python clean_recordings.py --min-duration 3   # 自定义最短时长阈值
    python clean_recordings.py --min-snr 5        # 自定义最低 SNR 阈值
"""

import argparse
import os
import sys
import io
import wave
import logging
from pathlib import Path
from typing import List, Dict, Optional

import yaml
import numpy as np

# 修复 Windows 控制台编码
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from src.analyzer import SignalAnalyzer
from src.db import Database

logger = logging.getLogger(__name__)


# ==================== 录音质量分类器 ====================

class RecordingClassifier:
    """
    录音质量分类器。

    分析 WAV 文件并判断其是否包含有意义的信号，
    还是仅仅是噪声爆发、脉冲干扰或底噪误触发。
    """

    # 分类结果
    JUNK_REASONS = {
        "TOO_SHORT": "时长极短 (噪声触发)",
        "LOW_SNR": "信噪比极低 (纯噪声)",
        "NOISE_FLAT": "频谱平坦 (白噪声/底噪)",
        "NOISE_TYPE": "分析器判定为 NOISE",
        "IMPULSE": "脉冲干扰 (如滴一声)",
        "SILENT": "近乎静音 (RMS 极低)",
    }

    def __init__(self,
                 min_duration: float = 2.0,
                 min_snr: float = 5.0,
                 max_flatness: float = 0.5,
                 impulse_max_duration: float = 3.0,
                 impulse_min_crest: float = 15.0,
                 silent_rms_threshold: float = 0.005,
                 analyzer: SignalAnalyzer = None):
        """
        初始化分类器。

        Args:
            min_duration: 最短有效时长(秒)，低于此值判定为垃圾
            min_snr: 最低有效 SNR(dB)，低于此值判定为噪声
            max_flatness: 频谱平坦度上限，超过此值判定为白噪声
            impulse_max_duration: 脉冲判定的最大时长(秒)
            impulse_min_crest: 脉冲判定的最低峰均比(dB)
            silent_rms_threshold: 静音判定的 RMS 上限
            analyzer: 信号分析器实例
        """
        self.min_duration = min_duration
        self.min_snr = min_snr
        self.max_flatness = max_flatness
        self.impulse_max_duration = impulse_max_duration
        self.impulse_min_crest = impulse_min_crest
        self.silent_rms_threshold = silent_rms_threshold
        self.analyzer = analyzer or SignalAnalyzer()

    def classify(self, wav_path: str) -> Dict:
        """
        分类单个录音文件。

        Args:
            wav_path: WAV 文件路径

        Returns:
            分类结果字典:
            {
                "path": str,
                "filename": str,
                "is_junk": bool,
                "reasons": list[str],
                "reason_details": list[str],
                "analysis": dict (分析器结果),
                "file_size_kb": float,
            }
        """
        filename = os.path.basename(wav_path)
        file_size_kb = os.path.getsize(wav_path) / 1024

        result = {
            "path": wav_path,
            "filename": filename,
            "is_junk": False,
            "reasons": [],
            "reason_details": [],
            "file_size_kb": file_size_kb,
            "analysis": None,
        }

        # 获取基本 WAV 信息
        try:
            with wave.open(wav_path, 'rb') as wf:
                framerate = wf.getframerate()
                n_frames = wf.getnframes()
                duration = n_frames / framerate
        except Exception as e:
            logger.warning(f"无法读取 WAV 文件 {filename}: {e}")
            result["is_junk"] = True
            result["reasons"].append("CORRUPT")
            result["reason_details"].append(f"文件损坏: {e}")
            return result

        # 运行完整分析
        analysis = self.analyzer.analyze_file(wav_path)
        if analysis is None:
            result["is_junk"] = True
            result["reasons"].append("ANALYZE_FAIL")
            result["reason_details"].append("分析失败")
            return result

        result["analysis"] = analysis
        duration = analysis["duration_seconds"]
        rms = analysis.get("rms", 0)
        snr = analysis.get("snr_db", 0)
        flatness = analysis.get("spectral_flatness", 0)
        crest = analysis.get("crest_factor_db", 0)
        modulation = analysis.get("estimated_modulation", "UNKNOWN")

        # ---- 检查各项指标 ----

        # 1. 时长过短
        if duration < self.min_duration:
            result["reasons"].append("TOO_SHORT")
            result["reason_details"].append(
                f"时长 {duration:.1f}s < {self.min_duration}s"
            )

        # 2. SNR 过低
        if snr < self.min_snr:
            result["reasons"].append("LOW_SNR")
            result["reason_details"].append(
                f"SNR {snr:.1f}dB < {self.min_snr}dB"
            )

        # 3. 频谱平坦度过高 (白噪声特征)
        if flatness > self.max_flatness:
            result["reasons"].append("NOISE_FLAT")
            result["reason_details"].append(
                f"频谱平坦度 {flatness:.3f} > {self.max_flatness}"
            )

        # 4. 分析器判定为 NOISE
        if modulation == "NOISE":
            result["reasons"].append("NOISE_TYPE")
            result["reason_details"].append(
                f"调制类型判定为 NOISE"
            )

        # 5. 脉冲干扰: 短 + 高峰均比
        if (duration < self.impulse_max_duration and
                crest > self.impulse_min_crest):
            result["reasons"].append("IMPULSE")
            result["reason_details"].append(
                f"脉冲: 时长 {duration:.1f}s, 峰均比 {crest:.1f}dB"
            )

        # 6. 近乎静音
        if rms < self.silent_rms_threshold:
            result["reasons"].append("SILENT")
            result["reason_details"].append(
                f"RMS {rms:.6f} < {self.silent_rms_threshold}"
            )

        # 综合判定: 满足 1 条即为垃圾
        result["is_junk"] = len(result["reasons"]) > 0

        return result


# ==================== 清理执行器 ====================

def scan_recordings(recordings_dir: str, classifier: RecordingClassifier,
                    verbose: bool = False) -> tuple:
    """
    扫描并分类所有录音文件。

    Args:
        recordings_dir: 录音目录
        classifier: 分类器实例
        verbose: 是否显示详情

    Returns:
        (junk_list, good_list) 两个列表
    """
    if not os.path.isdir(recordings_dir):
        print(f"[ERROR] 录音目录不存在: {recordings_dir}")
        return [], []

    wav_files = sorted([
        os.path.join(recordings_dir, f)
        for f in os.listdir(recordings_dir)
        if f.lower().endswith('.wav')
    ])

    if not wav_files:
        print("[INFO] 录音目录中没有 WAV 文件")
        return [], []

    print(f"\n[SCAN] 正在分析 {len(wav_files)} 个录音文件...\n")

    junk_list = []
    good_list = []

    for i, wav_path in enumerate(wav_files, 1):
        filename = os.path.basename(wav_path)
        # 进度显示
        if i % 20 == 0 or i == len(wav_files):
            print(f"  进度: {i}/{len(wav_files)}")

        result = classifier.classify(wav_path)

        if result["is_junk"]:
            junk_list.append(result)
            if verbose:
                reasons_str = ", ".join(result["reason_details"])
                print(f"  [JUNK] {filename} - {reasons_str}")
        else:
            good_list.append(result)

    return junk_list, good_list


def print_report(junk_list: List[Dict], good_list: List[Dict]):
    """打印分类报告。"""
    total = len(junk_list) + len(good_list)
    junk_size_mb = sum(r["file_size_kb"] for r in junk_list) / 1024
    good_size_mb = sum(r["file_size_kb"] for r in good_list) / 1024

    print("\n" + "=" * 70)
    print("  录音清理分析报告")
    print("=" * 70)
    print(f"  总文件数:     {total}")
    print(f"  有效录音:     {len(good_list)} ({good_size_mb:.1f} MB)")
    print(f"  垃圾录音:     {len(junk_list)} ({junk_size_mb:.1f} MB)")
    print(f"  垃圾占比:     {len(junk_list)/max(total,1)*100:.1f}%")
    print("-" * 70)

    if not junk_list:
        print("  没有发现垃圾录音！所有文件都是有效的。")
        print("=" * 70)
        return

    # 按原因统计
    reason_counts = {}
    for r in junk_list:
        for reason in r["reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    print("  垃圾录音原因分布:")
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
        desc = RecordingClassifier.JUNK_REASONS.get(reason, reason)
        print(f"    {desc}: {count} 个")

    print("-" * 70)
    print("  垃圾录音列表 (前 30 个):")
    print(f"  {'文件名':<50} {'时长':>6} {'SNR':>6} {'原因'}")
    print("  " + "-" * 66)

    for r in junk_list[:30]:
        analysis = r.get("analysis") or {}
        dur = analysis.get("duration_seconds", 0)
        snr = analysis.get("snr_db", 0)
        reasons = ", ".join(r["reasons"])
        print(f"  {r['filename']:<50} {dur:>5.1f}s {snr:>5.1f}dB  {reasons}")

    if len(junk_list) > 30:
        print(f"  ... 还有 {len(junk_list) - 30} 个垃圾文件未显示")

    print("=" * 70)


def delete_junk(junk_list: List[Dict], db: Database = None,
                clean_db: bool = False) -> int:
    """
    删除垃圾录音文件。

    Args:
        junk_list: 垃圾文件列表
        db: 数据库实例（用于清理 DB 记录）
        clean_db: 是否同时清理数据库中的对应记录

    Returns:
        成功删除的文件数
    """
    deleted = 0
    db_cleaned = 0

    for r in junk_list:
        path = r["path"]
        filename = r["filename"]

        try:
            os.remove(path)
            deleted += 1
            logger.debug(f"已删除: {filename}")
        except OSError as e:
            print(f"  [WARN] 删除失败: {filename} - {e}")
            continue

        # 清理数据库记录
        if clean_db and db:
            try:
                # 查找并删除 signals 表中对应的记录
                cursor = db.conn.execute(
                    "SELECT id FROM signals WHERE recording_path LIKE ?",
                    (f"%{filename}%",)
                )
                signal_ids = [row["id"] for row in cursor.fetchall()]

                for sid in signal_ids:
                    # 先删除 analysis 记录
                    db.conn.execute(
                        "DELETE FROM analysis WHERE signal_id = ?", (sid,)
                    )
                    # 再删除 signal 记录
                    db.conn.execute(
                        "DELETE FROM signals WHERE id = ?", (sid,)
                    )
                    db_cleaned += 1

                if signal_ids:
                    db.conn.commit()
            except Exception as e:
                logger.warning(f"清理数据库记录失败 ({filename}): {e}")

    return deleted, db_cleaned


# ==================== 主入口 ====================

def main():
    parser = argparse.ArgumentParser(
        description="MilRadio 录音清理工具 - 自动识别并清理无意义的录音文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python clean_recordings.py                    # 预览模式（只分析不删除）
  python clean_recordings.py --delete           # 删除垃圾录音
  python clean_recordings.py --delete --clean-db  # 删除文件 + 清理数据库
  python clean_recordings.py --min-duration 3   # 最短有效时长 3 秒
  python clean_recordings.py --min-snr 8        # 最低有效 SNR 8 dB
  python clean_recordings.py -v                 # 显示每个文件的分析详情
        """
    )

    parser.add_argument("--delete", action="store_true",
                        help="实际删除垃圾文件（默认只预览）")
    parser.add_argument("--clean-db", action="store_true",
                        help="同时清理数据库中对应的信号记录")
    parser.add_argument("--recordings-dir", type=str, default=None,
                        help="录音目录路径 (默认: config.yaml 中的配置)")
    parser.add_argument("--min-duration", type=float, default=2.0,
                        help="最短有效时长 (秒, 默认: 2.0)")
    parser.add_argument("--min-snr", type=float, default=5.0,
                        help="最低有效 SNR (dB, 默认: 5.0)")
    parser.add_argument("--max-flatness", type=float, default=0.5,
                        help="频谱平坦度上限 (默认: 0.5)")
    parser.add_argument("--impulse-max-dur", type=float, default=3.0,
                        help="脉冲判定最大时长 (秒, 默认: 3.0)")
    parser.add_argument("--impulse-min-crest", type=float, default=15.0,
                        help="脉冲判定最低峰均比 (dB, 默认: 15.0)")
    parser.add_argument("-c", "--config", default="config.yaml",
                        help="配置文件路径")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="显示每个文件的分析详情")

    args = parser.parse_args()

    # 配置日志
    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        format="%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S",
        level=level,
    )

    # LOGO
    print("""
    ===================================================
    |   MilRadio - 录音清理工具                         |
    |   自动识别并清理无意义的录音文件                    |
    ===================================================
    """)

    # 加载配置
    config = {}
    config_path = Path(args.config)
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

    # 确定录音目录
    recordings_dir = args.recordings_dir
    if not recordings_dir:
        recordings_dir = config.get("recording", {}).get(
            "output_dir", "data/recordings"
        )

    # 创建分析器
    ana_cfg = config.get("analysis", {})
    analyzer = SignalAnalyzer(
        fft_size=ana_cfg.get("fft_size", 4096),
        window_type=ana_cfg.get("window_type", "hann"),
        bandwidth_threshold_db=ana_cfg.get("bandwidth_threshold_db", 20),
    )

    # 创建分类器
    classifier = RecordingClassifier(
        min_duration=args.min_duration,
        min_snr=args.min_snr,
        max_flatness=args.max_flatness,
        impulse_max_duration=args.impulse_max_dur,
        impulse_min_crest=args.impulse_min_crest,
        analyzer=analyzer,
    )

    # 显示当前参数
    print(f"  录音目录:   {recordings_dir}")
    print(f"  最短时长:   {args.min_duration}s")
    print(f"  最低 SNR:   {args.min_snr} dB")
    print(f"  平坦度上限: {args.max_flatness}")
    print(f"  模式:       {'⚠ 删除模式' if args.delete else '👁 预览模式 (只分析不删除)'}")
    if args.clean_db:
        print(f"  数据库:     ⚠ 同时清理数据库记录")
    print()

    # 扫描并分类
    junk_list, good_list = scan_recordings(
        recordings_dir, classifier, verbose=args.verbose
    )

    # 打印报告
    print_report(junk_list, good_list)

    # 执行删除
    if args.delete and junk_list:
        print(f"\n[ACTION] 准备删除 {len(junk_list)} 个垃圾文件...")

        # 连接数据库（如果需要清理 DB）
        db = None
        if args.clean_db:
            db_path = config.get("database", {}).get(
                "path", "data/radio_monitor.db"
            )
            db = Database(db_path)

        deleted, db_cleaned = delete_junk(junk_list, db, args.clean_db)

        if db:
            db.close()

        junk_size_mb = sum(r["file_size_kb"] for r in junk_list) / 1024
        print(f"\n[DONE] 已删除 {deleted} 个垃圾文件，释放 {junk_size_mb:.1f} MB")
        if args.clean_db:
            print(f"[DONE] 已清理 {db_cleaned} 条数据库记录")

    elif not args.delete and junk_list:
        print(f"\n[TIP] 这是预览模式，要实际删除请加 --delete 参数:")
        print(f"       python clean_recordings.py --delete")
        print(f"       python clean_recordings.py --delete --clean-db  # 同时清理数据库")


if __name__ == "__main__":
    main()
