"""
example_query.py - 数据查询示例

演示如何从 SQLite 数据库中查询监听数据。
运行方式: python example_query.py
"""

import sqlite3
import os

DB_PATH = "data/radio_monitor.db"

def main():
    if not os.path.exists(DB_PATH):
        print(f"[INFO] 数据库不存在: {DB_PATH}")
        print("[INFO] 请先运行 'python main.py monitor -f 11175' 采集数据")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 1. 查看监听会话历史
    print("=" * 60)
    print("  最近的监听会话")
    print("=" * 60)
    rows = conn.execute("""
        SELECT id, node_name, start_time, end_time, status, notes
        FROM sessions ORDER BY start_time DESC LIMIT 5
    """).fetchall()

    if rows:
        for r in rows:
            print(f"  #{r['id']} | {r['start_time'][:19]} | {r['node_name']} | {r['status']}")
            print(f"       {r['notes']}")
    else:
        print("  (暂无数据 - 请先运行 monitor 命令)")

    # 2. 查看最近检测到的信号
    print("\n" + "=" * 60)
    print("  最近检测到的信号")
    print("=" * 60)
    rows = conn.execute("""
        SELECT timestamp, frequency_khz, mode, duration_seconds,
               peak_rms, avg_rms, node_name, recording_path
        FROM signals ORDER BY timestamp DESC LIMIT 10
    """).fetchall()

    if rows:
        for r in rows:
            rec = os.path.basename(r['recording_path']) if r['recording_path'] else '-'
            print(f"  {r['timestamp'][:19]} | {r['frequency_khz']:>8.1f} kHz ({r['mode']}) | "
                  f"{r['duration_seconds']:.1f}s | RMS={r['peak_rms']:.4f}")
            print(f"       Recording: {rec}")
    else:
        print("  (暂无数据)")

    # 3. 频率活跃度统计
    print("\n" + "=" * 60)
    print("  频率活跃度统计")
    print("=" * 60)
    rows = conn.execute("""
        SELECT frequency_khz, mode,
               COUNT(*) as cnt,
               SUM(duration_seconds) as total_dur,
               AVG(peak_rms) as avg_rms,
               MAX(peak_rms) as max_rms
        FROM signals
        GROUP BY frequency_khz
        ORDER BY cnt DESC
    """).fetchall()

    if rows:
        print(f"  {'频率':>10} | {'信号数':>6} | {'总时长':>10} | {'平均RMS':>10} | {'峰值RMS':>10}")
        print("  " + "-" * 56)
        for r in rows:
            print(f"  {r['frequency_khz']:>8.1f} kHz | {r['cnt']:>6} | "
                  f"{(r['total_dur'] or 0):>8.0f}s | "
                  f"{(r['avg_rms'] or 0):>10.4f} | {(r['max_rms'] or 0):>10.4f}")
    else:
        print("  (暂无数据)")

    # 4. 调制类型分析
    print("\n" + "=" * 60)
    print("  调制类型识别结果")
    print("=" * 60)
    rows = conn.execute("""
        SELECT a.estimated_modulation, COUNT(*) as cnt,
               AVG(a.snr_db) as avg_snr,
               AVG(a.bandwidth_hz) as avg_bw
        FROM analysis a
        GROUP BY a.estimated_modulation
        ORDER BY cnt DESC
    """).fetchall()

    if rows:
        for r in rows:
            print(f"  {r['estimated_modulation']:>15} | "
                  f"{r['cnt']} signals | "
                  f"avg SNR={r['avg_snr']:.1f} dB | "
                  f"avg BW={r['avg_bw']:.0f} Hz")
    else:
        print("  (暂无分析数据)")

    # 5. 24小时活动分布
    print("\n" + "=" * 60)
    print("  24小时信号活动分布 (UTC)")
    print("=" * 60)
    rows = conn.execute("""
        SELECT CAST(strftime('%H', timestamp) AS INTEGER) as hour,
               COUNT(*) as cnt
        FROM signals
        GROUP BY hour ORDER BY hour
    """).fetchall()

    if rows:
        max_cnt = max(r['cnt'] for r in rows)
        for r in rows:
            bar = '#' * int(r['cnt'] / max_cnt * 30)
            print(f"  {r['hour']:02d}:00 | {r['cnt']:>4} | {bar}")
    else:
        print("  (暂无数据)")

    conn.close()
    print("\n[DONE] 查询完成")


if __name__ == "__main__":
    main()
