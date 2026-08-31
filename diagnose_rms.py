#!/usr/bin/env python3
"""
diagnose_rms.py - 接收链路体检

回答一个问题: **守着某个频率收不到信号，到底卡在哪一层。**

分三种情况，输出会直接说是哪一种:
  1. 根本没有音频帧进来   -> 节点/网络/握手的问题，和频率无关
  2. 有音频，但 RMS 一直够不到静噪阈值 -> 阈值相对这个节点定高了
  3. 有音频，RMS 大部分时间都在阈值之上 -> 阈值定低了，会一直误触发

之前这个脚本自己抄了一份帧解析，停留在 v1.3.0 修复之前的布局
(seq 读成 2 字节大端、S-meter 读成 body[3:5]、音频取 body[5:])，
量出来的 S-meter 是错的，打印的阈值建议还停在早就废弃的 0.65。
现在直接复用 src.kiwi_client.KiwiSDRClient —— 体检用的解析和真正监听时
用的必须是同一份代码，否则量准了也没有意义。

用法:
    python diagnose_rms.py                      # config.yaml 的首选节点, 11175 USB, 30s
    python diagnose_rms.py -f 8992 -d 60        # 换频率和时长
    python diagnose_rms.py --node kphsdr.com    # 指定节点
    python diagnose_rms.py --all-nodes          # 逐个节点各测一遍，看哪个能听到
"""

import argparse
import asyncio
import io
import sys
from pathlib import Path

import numpy as np
import yaml

# Windows 控制台 GBK 编码无法输出 Unicode 字符
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from src.kiwi_client import KiwiSDRClient
from src.modes import normalize_mode
from src.schedule import check_targets, format_window

DEFAULT_FREQ = 11175.0
DEFAULT_MODE = "USB"
DEFAULT_DURATION = 30.0

# Peak-to-peak RMS spread at or below which the link counts as muted.
# Must match squelch.dead_audio_rms_spread, or the doctor and the monitor disagree.
# 判定链路哑音的 RMS 极差上限。
# 必须和 squelch.dead_audio_rms_spread 一致，否则体检和监听会各说各话。
DEAD_AUDIO_RMS_SPREAD = 1e-6


def load_yaml(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


async def measure(host: str, port: int, freq_khz: float, mode: str,
                  duration: float) -> dict:
    """
    连到一个节点上收 duration 秒音频，返回逐块 RMS 和链路统计。
    """
    result = {
        "host": host, "port": port,
        "connected": False,
        "rms": [],
        "smeter": [],
        "audio_frames": 0,
        "dropped_frames": 0,
        "error": None,
    }

    client = KiwiSDRClient(host, port)
    try:
        if not await client.connect(timeout=15):
            result["error"] = "握手失败 (节点满员/需要密码/无法连接)"
            return result
        result["connected"] = True

        await client.set_frequency(freq_khz, mode)

        rms_list, smeter_list = [], []

        async def on_audio(samples, smeter):
            if len(samples):
                rms_list.append(float(np.sqrt(np.mean(samples ** 2))))
                smeter_list.append(float(smeter))

        await client.start_audio_stream(on_audio)

        # 分段等待，节点中途掉线时不用干等满
        waited = 0.0
        while waited < duration and client.connected:
            await asyncio.sleep(0.5)
            waited += 0.5

        await client.stop_audio_stream()

        result["rms"] = rms_list
        result["smeter"] = smeter_list
        result["audio_frames"] = client.audio_frames
        result["dropped_frames"] = client.dropped_frames
        if not client.connected and waited < duration:
            result["error"] = f"测量中途连接断开 (第 {waited:.0f}s)"

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        await client.disconnect()

    return result


def report(result: dict, open_th: float, close_th: float,
           freq_khz: float, mode: str, duration: float) -> bool:
    """打印单个节点的体检结果，返回这个节点是否算"链路正常"。"""
    name = f"{result['host']}:{result['port']}"
    print(f"\n{'=' * 66}")
    print(f"  {name}  |  {freq_khz:.1f} kHz {mode}  |  {duration:.0f}s")
    print(f"{'=' * 66}")

    if not result["connected"]:
        print(f"  [X] 连不上: {result['error']}")
        return False

    rms = np.array(result["rms"], dtype=float)
    frames = result["audio_frames"]

    print(f"  音频帧: {frames}   丢帧: {result['dropped_frames']}   "
          f"RMS 块数: {len(rms)}")
    if result["error"]:
        print(f"  [!] {result['error']}")

    # --- 情况 1: 没有音频 ---
    if len(rms) == 0:
        print("\n  [X] 握手成功，但一个音频帧都没有收到。")
        print("      这一层就断了，和频率、阈值都没有关系。常见原因:")
        print("        - 节点通道被占满 / 对匿名用户有时长限制")
        print("        - 出口把 8073 之类的端口挡掉了 (KiwiSDR 走 ws://，不是 443)")
        print("        - 该节点当前不可用，换一个再试: python diagnose_rms.py --all-nodes")
        return False

    # --- 情况 1b: 有帧但没有声音 ---
    # 这个脚本本来就量出了 min/median/max，只是从没说破"三个数一样 = 节点是哑的"。
    # K1VL Vermont 实测: 470 帧、丢 0 帧、S-meter 正常，而 RMS 三个统计量全是 0.00003。
    # --- Case 1b: frames but no sound ---
    # The numbers were always printed; what was missing was saying out loud that identical
    # min/median/max means the node is muted. Measured on K1VL Vermont: 470 frames,
    # 0 dropped, healthy S-meter, and all three RMS statistics equal to 0.00003.
    rms_spread = float(rms.max() - rms.min())
    if rms_spread <= DEAD_AUDIO_RMS_SPREAD:
        print(f"\n  [X] 收到了 {frames} 个音频帧，但每一块的 RMS 都是同一个值 "
              f"{rms.max():.6f} (极差 {rms_spread:.2e})。")
        print("      这个节点在发帧但不出声，录下来的会是一整串零样本。")
        print("      和阈值、频率都没有关系，换一个节点: "
              "python diagnose_rms.py --all-nodes")
        print(f"\n  [X] Received {frames} audio frames, but every block has the same RMS "
              f"{rms.max():.6f} (spread {rms_spread:.2e}).")
        print("      This node sends frames without audio; recordings would be all zeros.")
        print("      Unrelated to threshold or frequency -- try another node.")
        return False

    noise_floor = float(np.percentile(rms, 20))
    median = float(np.median(rms))
    above_open = int((rms >= open_th).sum())
    above_ratio = above_open / len(rms)

    smeter = np.array(result["smeter"], dtype=float) if result["smeter"] else None

    print(f"\n  RMS   最小 {rms.min():.5f}  底噪(P20) {noise_floor:.5f}  "
          f"中位 {median:.5f}  最大 {rms.max():.5f}")
    if smeter is not None and len(smeter):
        print(f"  S-meter  {smeter.min():.1f} ~ {smeter.max():.1f} dBm  "
              f"(中位 {np.median(smeter):.1f})")

    print(f"\n  当前阈值 open={open_th}  close={close_th}")
    print(f"  能触发静噪的块: {above_open}/{len(rms)} ({above_ratio * 100:.1f}%)")

    # --- 判定 ---
    healthy = True
    print()
    if above_open == 0:
        headroom = open_th / noise_floor if noise_floor > 0 else float("inf")
        print(f"  [X] 这段时间里没有任何一块能越过 open_threshold。")
        print(f"      阈值是本节点底噪的 {headroom:.1f} 倍 —— 只要这个倍数一直这么高，"
              f"守多久都不会有信号。")
        healthy = False
    elif above_ratio > 0.5:
        print(f"  [X] 超过一半的块都在阈值之上，静噪基本处于常开状态，"
              f"录下来的多半是底噪。")
        healthy = False
    else:
        print(f"  [OK] 阈值落在底噪和峰值之间，静噪能正常开合。")

    suggested_open = round(noise_floor * 2.0, 4)      # 底噪 +6 dB
    suggested_close = round(noise_floor * 1.7, 4)
    print(f"\n  按本节点实测底噪建议: open_threshold={suggested_open}  "
          f"close_threshold={suggested_close}")
    print(f"  (阈值是跟着节点 AGC 走的绝对电平，换节点就要重新量一次;"
          f" Web 界面上有\"按底噪设定\"可以直接套用)")

    return healthy


async def main():
    parser = argparse.ArgumentParser(
        description="KiwiSDR 接收链路体检 - 定位'守着频率收不到信号'卡在哪一层")
    parser.add_argument("-f", "--freq", type=float, default=DEFAULT_FREQ,
                        help=f"频率 kHz (默认 {DEFAULT_FREQ})")
    parser.add_argument("-m", "--mode", type=str, default=DEFAULT_MODE,
                        choices=["USB", "LSB", "AM", "CW", "CWN"],
                        help=f"解调模式 (默认 {DEFAULT_MODE})")
    parser.add_argument("-d", "--duration", type=float, default=DEFAULT_DURATION,
                        help=f"每个节点测量时长 秒 (默认 {DEFAULT_DURATION:.0f})")
    parser.add_argument("--node", type=str, default=None,
                        help="指定节点 host (默认用 config.yaml 里的第一个)")
    parser.add_argument("--all-nodes", action="store_true",
                        help="逐个测 config.yaml 里的所有节点")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("--freq-file", default="frequencies.yaml")
    args = parser.parse_args()

    config = load_yaml(args.config)
    nodes = config.get("nodes", [])
    if not nodes:
        print("[ERROR] config.yaml 里没有配置任何节点")
        return 1

    sq = config.get("squelch", {})
    open_th = sq.get("open_threshold", 0.10)
    close_th = sq.get("close_threshold", 0.085)
    mode = normalize_mode(args.mode)

    # 选节点
    if args.all_nodes:
        targets = nodes
    elif args.node:
        targets = [n for n in nodes if args.node.lower() in n["host"].lower()]
        if not targets:
            print(f"[ERROR] config.yaml 里没有匹配 '{args.node}' 的节点")
            return 1
    else:
        targets = nodes[:1]

    print("=" * 66)
    print("  接收链路体检")
    print("=" * 66)
    print(f"  频率: {args.freq:.1f} kHz {mode}")
    print(f"  节点: {len(targets)} 个   每个测 {args.duration:.0f}s")
    print(f"  阈值: open={open_th}  close={close_th}  (来自 {args.config})")

    # 活跃时段提示
    freq_data = load_yaml(args.freq_file)
    for line in check_targets(freq_data, [args.freq]):
        print(line)

    healthy_nodes = []
    for node in targets:
        result = await measure(
            node["host"], node.get("port", 8073),
            args.freq, mode, args.duration,
        )
        if report(result, open_th, close_th, args.freq, mode, args.duration):
            healthy_nodes.append(node.get("name", node["host"]))

    if len(targets) > 1:
        print(f"\n{'=' * 66}")
        print(f"  汇总: {len(healthy_nodes)}/{len(targets)} 个节点链路正常")
        if healthy_nodes:
            print(f"  可用: {', '.join(healthy_nodes)}")
        print(f"{'=' * 66}")

    return 0


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(main()))
