import sys, os, requests
try:
    sys.stdout.reconfigure(encoding='utf-8')
except: pass

# Today's 3 EAM recordings from EAM.watch API
eams = [
    {
        "time": "2026-06-09 14:52:00",
        "sender": "AROMATIC",
        "message": "EEF4INCDXL52INPSTSSC3VBNO",
        "url": "https://eamwatch-production.s3.amazonaws.com/recordings/83d1fea5-698e-49c9-a85f-9016fc6ceb88/3cd782d1-700b-4f86-8f39-b27d13831afe",
    },
    {
        "time": "2026-06-09 13:43:00",
        "sender": "AROMATIC",
        "message": "EE3OPRHRJYWI7QH43B4DFLWLE",
        "url": "https://eamwatch-production.s3.amazonaws.com/recordings/e9b9187f-da30-4436-97c6-d20a8de7caec/9a51e87b-0bed-4591-a650-69ec8ce3df76",
    },
    {
        "time": "2026-06-09 13:40:00",
        "sender": "AROMATIC",
        "message": "EEZLMQ3JGBGAIMFKKEIWSSMM4C7OS3X",
        "url": "https://eamwatch-production.s3.amazonaws.com/recordings/798073d5-8449-4734-b928-f52cdc534e9d/d2efa04a-00ae-471e-92ca-50cd2dd80a51",
    },
]

out_dir = "data/eam_comparison/today"
os.makedirs(out_dir, exist_ok=True)

session = requests.Session()
session.headers["User-Agent"] = "MilRadio-Monitor/1.0"

for eam in eams:
    safe_time = eam["time"].replace(" ", "_").replace(":", "")
    filename = f"EAM_{safe_time}_{eam['sender']}.wav"
    filepath = os.path.join(out_dir, filename)

    print(f"[{eam['time']}] {eam['sender']}: {eam['message']}")
    print(f"  Downloading...")

    resp = session.get(eam["url"], timeout=30)
    if resp.status_code == 200:
        with open(filepath, "wb") as f:
            f.write(resp.content)
        print(f"  Saved: {filepath} ({len(resp.content)} bytes)")
    else:
        print(f"  FAILED: HTTP {resp.status_code}")

print(f"\nDone. Files saved to {out_dir}/")
