import sqlite3
from datetime import datetime, timedelta, timezone

conn = sqlite3.connect('data/radio_monitor.db')
conn.row_factory = sqlite3.Row

# Last signal
cur = conn.execute("SELECT * FROM signals ORDER BY timestamp DESC LIMIT 1")
row = cur.fetchone()
if row:
    d = dict(row)
    print(f"Last signal: {d['timestamp']} | {d['frequency_khz']}kHz | peak_rms={d['peak_rms']} | dur={d['duration_seconds']}s")

# Signals in last 48 hours
print("\n=== Signals in last 48 hours ===")
cur = conn.execute("""
    SELECT * FROM signals 
    WHERE timestamp >= datetime('now', '-48 hours')
    ORDER BY timestamp DESC LIMIT 20
""")
rows = cur.fetchall()
print(f"Count: {len(rows)}")
for r in rows:
    d = dict(r)
    print(f"  {d['timestamp']} | {d['frequency_khz']}kHz | peak={d['peak_rms']:.4f} | dur={d['duration_seconds']:.1f}s | {d['node_name']}")

# Signal count per day (last 7 days)
print("\n=== Daily signal count (last 7 days) ===")
cur = conn.execute("""
    SELECT date(timestamp) as day, COUNT(*) as cnt, 
           AVG(peak_rms) as avg_peak, AVG(duration_seconds) as avg_dur
    FROM signals 
    WHERE timestamp >= datetime('now', '-7 days')
    GROUP BY date(timestamp)
    ORDER BY day DESC
""")
for r in cur.fetchall():
    d = dict(r)
    print(f"  {d['day']}: {d['cnt']} signals, avg_peak={d['avg_peak']:.4f}, avg_dur={d['avg_dur']:.1f}s")

# Current session #30 - check if it has any signals
print("\n=== Current session #30 signals ===")
cur = conn.execute("SELECT COUNT(*) as cnt FROM signals WHERE session_id = 30")
row = cur.fetchone()
print(f"Session 30 signals: {dict(row)['cnt']}")

# Session 28 and 27 signals 
for sid in [28, 27, 26]:
    cur = conn.execute("SELECT COUNT(*) as cnt FROM signals WHERE session_id = ?", (sid,))
    row = cur.fetchone()
    print(f"Session {sid} signals: {dict(row)['cnt']}")

conn.close()
