# -*- coding: utf-8 -*-
"""
compare_eam.py - 对比 EAM.watch 公开录音与本地静噪阈值

从 EAM.watch API 下载最近的 EAM 录音，分析音频 RMS，
与本地 config.yaml 中的静噪阈值对比，诊断为什么没检测到信号。

用法:
  python compare_eam.py              # 下载并分析最近的录音
  python compare_eam.py --pages 3    # 抓取更多页
"""

import argparse
import io
import json
import os
import struct
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# Windows 终端编码兼容
import locale
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
import yaml

try:
    import requests
except ImportError:
    print("需要安装 requests: pip install requests")
    sys.exit(1)

# ============================================================
# 配置
# ============================================================

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.yaml"
OUTPUT_DIR = BASE_DIR / "data" / "eam_comparison"

EAM_API_URL = "https://eam.watch/api/messages"
HEADERS = {
    "User-Agent": "MilRadio-Monitor/1.0 (Research)",
    "Accept": "application/json",
}


def load_config():
    """加载本地配置。"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_eam_messages(pages=2):
    """从 EAM.watch API 获取最近的消息。"""
    all_messages = []
    session = requests.Session()
    session.headers.update(HEADERS)

    for page in range(1, pages + 1):
        print(f"  [EAM.watch] 获取第 {page} 页...")
        try:
            resp = session.get(EAM_API_URL, params={"page": page}, timeout=20)
            if resp.status_code != 200:
                print(f"  [ERROR] API 返回 {resp.status_code}")
                break

            data = resp.json()
            messages = data.get("data", [])
            if not messages:
                break

            all_messages.extend(messages)
            time.sleep(0.5)  # 友好抓取

        except Exception as e:
            print(f"  [ERROR] 请求失败: {e}")
            break

    return all_messages


def download_recording(url, session, timeout=30):
    """下载录音文件，返回字节数据。"""
    try:
        resp = session.get(url, timeout=timeout)
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        print(f"    [下载失败] {e}")
    return None


def decode_audio(audio_bytes):
    """
    解码音频数据为 float32 numpy 数组。
    支持 WAV 和原始 PCM。
    """
    if not audio_bytes or len(audio_bytes) < 44:
        return None, 0

    # 尝试 WAV 解码
    try:
        with io.BytesIO(audio_bytes) as buf:
            with wave.open(buf, "rb") as wf:
                n_channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                sample_rate = wf.getframerate()
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)

                if sample_width == 2:
                    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                elif sample_width == 4:
                    samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
                elif sample_width == 1:
                    samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
                else:
                    return None, 0

                # 如果是多声道，取第一个
                if n_channels > 1:
                    samples = samples[::n_channels]

                return samples, sample_rate
    except Exception:
        pass

    # 尝试作为原始 16-bit PCM
    try:
        samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        return samples, 12000  # 假设 12kHz
    except Exception:
        pass

    return None, 0


def analyze_audio(samples, sample_rate, window_size=1024):
    """
    分析音频的 RMS 特性，模拟静噪检测器的行为。
    返回分析结果字典。
    """
    if samples is None or len(samples) == 0:
        return None

    # 整体统计
    overall_rms = float(np.sqrt(np.mean(samples ** 2)))
    peak_amplitude = float(np.max(np.abs(samples)))
    duration = len(samples) / sample_rate

    # 分窗计算 RMS (模拟静噪检测器)
    n_windows = len(samples) // window_size
    window_rms = []
    for i in range(n_windows):
        chunk = samples[i * window_size: (i + 1) * window_size]
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        window_rms.append(rms)

    window_rms = np.array(window_rms)

    # 统计
    result = {
        "duration_s": duration,
        "sample_rate": sample_rate,
        "total_samples": len(samples),
        "overall_rms": overall_rms,
        "peak_amplitude": peak_amplitude,
        "window_rms_mean": float(np.mean(window_rms)) if len(window_rms) > 0 else 0,
        "window_rms_max": float(np.max(window_rms)) if len(window_rms) > 0 else 0,
        "window_rms_min": float(np.min(window_rms)) if len(window_rms) > 0 else 0,
        "window_rms_median": float(np.median(window_rms)) if len(window_rms) > 0 else 0,
        "window_rms_std": float(np.std(window_rms)) if len(window_rms) > 0 else 0,
        "n_windows": len(window_rms),
    }

    return result


def simulate_squelch(samples, sample_rate, open_threshold, close_threshold,
                     tail_time=3.0, window_size=1024):
    """
    模拟静噪检测器，返回会检测到多少个信号。
    """
    n_windows = len(samples) // window_size
    is_open = False
    signals = []
    current_signal_start = 0
    last_above_time = 0
    peak_rms = 0

    time_per_window = window_size / sample_rate

    for i in range(n_windows):
        chunk = samples[i * window_size: (i + 1) * window_size]
        rms = float(np.sqrt(np.mean(chunk ** 2)))
        current_time = i * time_per_window

        if not is_open:
            if rms >= open_threshold:
                is_open = True
                current_signal_start = current_time
                last_above_time = current_time
                peak_rms = rms
        else:
            if rms >= close_threshold:
                last_above_time = current_time
                peak_rms = max(peak_rms, rms)
            elif current_time - last_above_time >= tail_time:
                # 信号结束
                duration = current_time - current_signal_start
                signals.append({
                    "start": current_signal_start,
                    "duration": duration,
                    "peak_rms": peak_rms,
                })
                is_open = False
                peak_rms = 0

    # 如果录音结束时还在信号中
    if is_open:
        final_time = n_windows * time_per_window
        duration = final_time - current_signal_start
        signals.append({
            "start": current_signal_start,
            "duration": duration,
            "peak_rms": peak_rms,
        })

    return signals


def main():
    parser = argparse.ArgumentParser(description="对比 EAM.watch 录音与本地阈值")
    parser.add_argument("--pages", type=int, default=2, help="抓取 API 页数 (默认 2)")
    parser.add_argument("--max-downloads", type=int, default=10, help="最多下载录音数 (默认 10)")
    parser.add_argument("--save-audio", action="store_true", help="保存下载的音频文件")
    args = parser.parse_args()

    # 加载配置
    config = load_config()
    sq_cfg = config.get("squelch", {})
    open_threshold = sq_cfg.get("open_threshold", 0.15)
    close_threshold = sq_cfg.get("close_threshold", 0.13)
    tail_time = sq_cfg.get("tail_time", 3.0)
    window_size = sq_cfg.get("window_size", 1024)

    print("=" * 70)
    print("  EAM.watch 录音 vs 本地静噪阈值 对比分析")
    print("=" * 70)
    print(f"\n本地配置:")
    print(f"  open_threshold  = {open_threshold}")
    print(f"  close_threshold = {close_threshold}")
    print(f"  tail_time       = {tail_time}s")
    print(f"  window_size     = {window_size}")
    print()

    # 获取 EAM 消息
    print("[1/3] 获取 EAM.watch 最新消息...")
    messages = fetch_eam_messages(pages=args.pages)
    print(f"  获取到 {len(messages)} 条消息\n")

    if not messages:
        print("[ERROR] 没有获取到消息，退出")
        return

    # 筛选有录音的消息
    messages_with_recordings = [
        m for m in messages
        if m.get("recordings") and len(m["recordings"]) > 0
    ]
    print(f"  其中 {len(messages_with_recordings)} 条有录音\n")

    # 下载并分析
    print("[2/3] 下载录音并分析...")
    print("-" * 70)

    session = requests.Session()
    session.headers.update(HEADERS)
    results = []

    if args.save_audio:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    for msg in messages_with_recordings:
        if downloaded >= args.max_downloads:
            break

        msg_time = msg.get("time", "?")
        msg_sender = msg.get("sender", "?")
        msg_type = msg.get("type", "?")
        msg_text = msg.get("message", "")[:30]
        rec_url = msg["recordings"][0].get("link", "")

        if not rec_url:
            continue

        print(f"\n  [{msg_time}] {msg_sender} ({msg_type})")
        print(f"  消息: {msg_text}...")
        print(f"  下载录音...")

        audio_bytes = download_recording(rec_url, session)
        if not audio_bytes:
            print(f"    [FAIL] 下载失败")
            continue

        downloaded += 1
        print(f"    [DL] {len(audio_bytes)} bytes")

        # 保存音频
        if args.save_audio:
            safe_name = f"eam_{msg_time.replace(' ', '_').replace(':', '')}.wav"
            save_path = OUTPUT_DIR / safe_name
            with open(save_path, "wb") as f:
                f.write(audio_bytes)

        # 解码
        samples, sample_rate = decode_audio(audio_bytes)
        if samples is None:
            print(f"    [FAIL] 无法解码音频")
            continue

        print(f"    [AUDIO] {len(samples)} samples @ {sample_rate} Hz "
              f"({len(samples)/sample_rate:.1f}s)")

        # 分析 RMS
        analysis = analyze_audio(samples, sample_rate, window_size)
        if not analysis:
            continue

        # 模拟静噪检测
        detected_signals = simulate_squelch(
            samples, sample_rate,
            open_threshold, close_threshold,
            tail_time, window_size
        )

        # 判定
        would_detect = len(detected_signals) > 0
        status = "[YES] 会检测到" if would_detect else "[MISS] 会漏掉"

        print(f"    Overall RMS: {analysis['overall_rms']:.4f}")
        print(f"    Window RMS: min={analysis['window_rms_min']:.4f}, "
              f"mean={analysis['window_rms_mean']:.4f}, "
              f"max={analysis['window_rms_max']:.4f}")
        print(f"    阈值={open_threshold} → {status} "
              f"(检测到 {len(detected_signals)} 个信号段)")

        if detected_signals:
            for i, sig in enumerate(detected_signals):
                print(f"      信号 #{i+1}: start={sig['start']:.1f}s, "
                      f"dur={sig['duration']:.1f}s, peak_rms={sig['peak_rms']:.4f}")

        # 测试不同阈值
        test_thresholds = [0.02, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20]
        threshold_results = {}
        for th in test_thresholds:
            sigs = simulate_squelch(
                samples, sample_rate, th, th * 0.85,
                tail_time, window_size
            )
            threshold_results[th] = len(sigs)

        print(f"    不同阈值检测结果:")
        for th, count in threshold_results.items():
            marker = " <-- 当前" if abs(th - open_threshold) < 0.001 else ""
            bar = "█" * min(count * 3, 30) if count > 0 else "·"
            print(f"      {th:.2f}: {count:2d} 个信号 {bar}{marker}")

        results.append({
            "time": msg_time,
            "sender": msg_sender,
            "type": msg_type,
            "message": msg_text,
            "analysis": analysis,
            "detected_signals": len(detected_signals),
            "would_detect": would_detect,
            "threshold_results": threshold_results,
        })

    # 汇总
    print("\n" + "=" * 70)
    print("  [3/3] 汇总分析")
    print("=" * 70)

    if not results:
        print("  没有成功分析的录音")
        return

    total = len(results)
    detected = sum(1 for r in results if r["would_detect"])
    missed = total - detected

    print(f"\n  总共分析: {total} 条 EAM 录音")
    print(f"  当前阈值 ({open_threshold}) 会检测到: {detected} ({detected/total*100:.0f}%)")
    print(f"  当前阈值 ({open_threshold}) 会漏掉:   {missed} ({missed/total*100:.0f}%)")

    # RMS 分布
    all_rms = [r["analysis"]["overall_rms"] for r in results]
    all_max_rms = [r["analysis"]["window_rms_max"] for r in results]
    print(f"\n  EAM 录音 RMS 统计:")
    print(f"    Overall RMS: {min(all_rms):.4f} ~ {max(all_rms):.4f} "
          f"(mean={np.mean(all_rms):.4f})")
    print(f"    Max窗口 RMS: {min(all_max_rms):.4f} ~ {max(all_max_rms):.4f} "
          f"(mean={np.mean(all_max_rms):.4f})")
    print(f"    你的阈值:    {open_threshold}")

    # 推荐
    print(f"\n  [TIP] 推荐阈值分析:")
    for th in [0.02, 0.05, 0.08, 0.10, 0.12, 0.15]:
        detect_count = sum(1 for r in results if r["threshold_results"].get(th, 0) > 0)
        pct = detect_count / total * 100
        marker = " <-- 当前" if abs(th - open_threshold) < 0.001 else ""
        rec = " * 推荐" if 0.08 <= th <= 0.12 and pct > 80 else ""
        print(f"    阈值 {th:.2f}: 检测率 {pct:5.1f}% ({detect_count}/{total}){marker}{rec}")

    # 结论
    print(f"\n  {'='*50}")
    if missed > total * 0.5:
        print(f"  [!!] 结论: 当前阈值 {open_threshold} 太高!")
        print(f"  大部分 EAM 信号的 RMS 低于你的阈值。")
        optimal = np.percentile(all_max_rms, 10) * 0.9
        print(f"  建议将 open_threshold 降到 {optimal:.3f} 左右")
        print(f"  (在 config.yaml 中修改 squelch.open_threshold)")
    elif missed > 0:
        print(f"  [!!] 部分信号会被漏掉，可以考虑适当降低阈值")
    else:
        print(f"  [OK] 当前阈值可以检测到所有 EAM 信号")
        print(f"  如果一晚上没信号，问题可能在连接/节点/频率选择")

    # 保存结果
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT_DIR / f"comparison_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  详细结果已保存: {report_path}")


if __name__ == "__main__":
    main()
