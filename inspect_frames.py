import asyncio, time
from websockets.asyncio.client import connect as ws_connect

async def main():
    uri = f"ws://kphsdr.com:8073/kiwi/{int(time.time())}/SND"
    ws = await asyncio.wait_for(
        ws_connect(uri, additional_headers={"Origin": "http://kphsdr.com:8073"},
                   max_size=2**20, ping_interval=None), timeout=10)

    # 完整的 KiwiSDR 握手序列
    await ws.send("SET auth t=kiwi p=")
    await asyncio.sleep(0.5)

    # 吃掉握手消息
    for _ in range(20):
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=0.3)
            if isinstance(msg, bytes):
                text = msg.decode("ascii", errors="ignore")
                if "audio_init" in text:
                    print(f"[HANDSHAKE] audio_init: {text}")
                elif text.startswith("MSG ") and len(msg) < 200:
                    print(f"[HANDSHAKE] {text[:80]}")
        except asyncio.TimeoutError:
            break

    # 发送音频启动命令
    cmds = [
        "SET AR OK in=12000 out=12000",
        "SET squelch=0 max=0",
        "SET genattn=0",
        "SET gen=0 mix=-1",
        "SET agc=1 hang=0 thresh=-100 slope=6 decay=1000 manGain=50",
        "SET compression=0",
        "SET mod=usb low_cut=300 high_cut=3000 freq=11175.000",
        "SET keepalive",
    ]
    for cmd in cmds:
        await ws.send(cmd)
        print(f"[SENT] {cmd}")

    # 等待更长时间看音频帧
    print("\n等待音频数据 (最多 8 秒)...")
    deadline = time.time() + 8
    count = 0
    audio_count = 0
    while time.time() < deadline:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        if isinstance(msg, bytes):
            count += 1
            is_msg = msg[:4] == b"MSG "
            hex_head = " ".join(f"{b:02x}" for b in msg[:16])
            tag = msg[0]
            tag_chr = chr(tag) if 32 <= tag < 127 else "?"

            if is_msg:
                text = msg.decode("ascii", errors="ignore")
                if len(msg) < 200:
                    print(f"  MSG: {text[:100]}")
            else:
                audio_count += 1
                print(f"  AUDIO #{audio_count}: len={len(msg)}  byte0=0x{tag:02x}({tag_chr})  hex=[{hex_head}]")
                if audio_count >= 5:
                    print(f"\n成功! 收到 {audio_count} 个音频帧")
                    break

    if audio_count == 0:
        print(f"\n未收到任何音频帧! (共收到 {count} 个 MSG 帧)")

    await ws.close()

asyncio.run(main())
