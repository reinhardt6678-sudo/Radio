import sqlite3, os, sys
try:
    sys.stdout.reconfigure(encoding='utf-8')
except: pass

conn = sqlite3.connect('data/radio_monitor.db')
conn.row_factory = sqlite3.Row

cur = conn.execute("SELECT * FROM signals WHERE timestamp >= datetime('now', '-12 hours') ORDER BY timestamp DESC")
rows = cur.fetchall()
print(f"Signals in last 12h: {len(rows)}")
for r in rows:
    d = dict(r)
    path = d.get('recording_path', '') or ''
    exists = os.path.exists(path) if path else False
    fsize = os.path.getsize(path) if exists else 0
    print(f"  time={d['timestamp']}")
    print(f"    freq={d['frequency_khz']}kHz peak_rms={d['peak_rms']:.4f} dur={d['duration_seconds']:.1f}s")
    print(f"    recording={path} exists={exists} size={fsize}")
    print(f"    session_id={d['session_id']} node={d['node_name']}")
    print()

# Check current running sessions
cur2 = conn.execute("SELECT * FROM sessions WHERE status='running' ORDER BY start_time DESC LIMIT 5")
print("=== Running sessions ===")
for r in cur2:
    d = dict(r)
    print(f"  id={d['id']} start={d['start_time']} node={d['node_host']} freqs={d['frequencies']}")

conn.close()
