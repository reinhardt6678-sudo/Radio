import sqlite3

conn = sqlite3.connect('data/radio_monitor.db')
conn.row_factory = sqlite3.Row

# Check recent sessions
print("=== Recent Sessions ===")
cursor = conn.execute("SELECT * FROM sessions ORDER BY start_time DESC LIMIT 5")
for row in cursor:
    d = dict(row)
    print(f"  ID={d['id']} start={d['start_time']} end={d['end_time']} status={d['status']} node={d['node_host']}")

print()

# Check signals in the last 3 hours
print("=== Signals in last 3 hours ===")
cursor = conn.execute("""
    SELECT * FROM signals 
    WHERE timestamp >= datetime('now', '-3 hours')
    ORDER BY timestamp DESC
    LIMIT 20
""")
signals = cursor.fetchall()
print(f"Count: {len(signals)}")
for s in signals:
    d = dict(s)
    print(f"  ID={d['id']} time={d['timestamp']} freq={d['frequency_khz']}kHz peak_rms={d['peak_rms']} dur={d['duration_seconds']}s")

print()

# Check total signal count
cursor = conn.execute("SELECT COUNT(*) as total FROM signals")
row = cursor.fetchone()
print(f"Total signals in DB: {row['total']}")

# Check last signal time
cursor = conn.execute("SELECT timestamp FROM signals ORDER BY timestamp DESC LIMIT 1")
row = cursor.fetchone()
if row:
    print(f"Last signal time: {row['timestamp']}")
else:
    print("No signals in database")

# Also check current running session
print()
print("=== Running sessions ===")
cursor = conn.execute("SELECT * FROM sessions WHERE status='running'")
for row in cursor:
    d = dict(row)
    print(f"  ID={d['id']} start={d['start_time']} node={d['node_host']} freqs={d['frequencies']}")

conn.close()
