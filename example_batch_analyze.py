"""
example_batch_analyze.py - 批量分析录音文件示例

扫描 data/recordings 目录，对所有 WAV 文件进行频谱分析。
运行方式: python example_batch_analyze.py
"""

import os
import sys

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(__file__))

from src.analyzer import SignalAnalyzer


def main():
    recordings_dir = "data/recordings"

    if not os.path.exists(recordings_dir):
        print(f"[INFO] 录音目录不存在: {recordings_dir}")
        print("[INFO] 请先运行 'python main.py monitor -f 11175' 采集数据")
        return

    wav_files = [f for f in os.listdir(recordings_dir) if f.endswith('.wav')]

    if not wav_files:
        print("[INFO] 录音目录为空，暂无文件可分析")
        return

    print(f"找到 {len(wav_files)} 个录音文件\n")

    analyzer = SignalAnalyzer(fft_size=4096)

    results = []
    for i, filename in enumerate(sorted(wav_files), 1):
        filepath = os.path.join(recordings_dir, filename)
        print(f"[{i}/{len(wav_files)}] 分析: {filename}")

        result = analyzer.analyze_file(filepath)
        if result:
            results.append({"file": filename, **result})
            print(f"  Duration:    {result['duration_seconds']:.1f}s")
            print(f"  Modulation:  {result['estimated_modulation']}")
            print(f"  SNR:         {result.get('snr_db', 0):.1f} dB")
            print(f"  Bandwidth:   {result.get('bandwidth_hz', 0):.0f} Hz")
            print(f"  Peak Freq:   {result.get('peak_frequency_hz', 0):.0f} Hz")
            print()
        else:
            print(f"  [SKIP] 分析失败\n")

    # 汇总统计
    if results:
        print("=" * 60)
        print("  批量分析汇总")
        print("=" * 60)
        print(f"  总文件数:     {len(wav_files)}")
        print(f"  成功分析:     {len(results)}")

        total_dur = sum(r['duration_seconds'] for r in results)
        print(f"  总录音时长:   {total_dur:.1f}s ({total_dur/60:.1f} min)")

        avg_snr = sum(r.get('snr_db', 0) for r in results) / len(results)
        print(f"  平均 SNR:     {avg_snr:.1f} dB")

        # 调制类型统计
        mod_counts = {}
        for r in results:
            mod = r.get('estimated_modulation', 'UNKNOWN')
            mod_counts[mod] = mod_counts.get(mod, 0) + 1

        print(f"\n  调制类型分布:")
        for mod, cnt in sorted(mod_counts.items(), key=lambda x: -x[1]):
            print(f"    {mod:>15}: {cnt} ({cnt/len(results)*100:.0f}%)")


if __name__ == "__main__":
    main()
