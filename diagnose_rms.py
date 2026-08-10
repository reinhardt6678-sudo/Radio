"""
RMS 诊断 v3 - 完整握手流程，正确消耗所有初始化消息后再发音频命令
"""
import asyncio, time, struct
import numpy as np
from websockets.asyncio.client import connect as ws_connect

async def main():
    host, port = "sdr.ironstonerange.com", 8075
    uri = f"ws://{host}:{port}/kiwi/{int(time.time())}/SND"
    print(f"连接 {host}:{port} ...")

    ws = await asyncio.wait_for(
        ws_connect(uri, additional_headers={"Origin": f"http://{host}:{port}"},
                   max_size=2**20, ping_interval=None), timeout=10)

    # Step 1: 发送 auth
    await ws.send("SET auth t=kiwi p=")

    # Step 2: 消耗所有握手消息直到 audio_init 或超时
    print("握手中...")
    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        if isinstance(msg, bytes) and len(msg) > 3:
            tag = msg[0:3]
            if tag == b"MSG":
                text = msg[3:].decode("ascii", errors="ignore")
                if "audio_init" in text:
                    print(f"  [OK] {text}")
                    break
                elif len(msg) < 100:
                    pass  # skip short msgs silently

    # Step 3: 发送音频命令
    commands = [
        "SET AR OK in=12000 out=12000",
        "SET squelch=0 max=0",
        "SET genattn=0",
        "SET gen=0 mix=-1",
        "SET agc=1 hang=0 thresh=-100 slope=6 decay=1000 manGain=50",
        "SET compression=0",
        "SET mod=usb low_cut=300 high_cut=3000 freq=11175.000",
        "SET keepalive",
    ]
    for cmd in commands:
        await ws.send(cmd)
    print("音频命令已发送\n")

    # Step 4: 收集 SND 帧
    rms_list = []
    frame_count = 0
    start = time.time()

    print(f"{'#':>4} | {'bytes':>5} | {'fl':>4} | {'seq':>5} | {'S-meter':>8} | {'RMS':>10} | {'peak':>8}")
    print("-" * 65)

    while time.time() - start < 15:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            print(f"错误: {e}")
            break

        if not isinstance(msg, bytes) or len(msg) < 4:
            continue

        tag = msg[0:3]
        body = msg[3:]

        if tag == b"SND" and len(body) >= 6:
            flags = body[0]
            seq = struct.unpack(">H", body[1:3])[0]
            smeter_raw = struct.unpack(">H", body[3:5])[0]
            smeter_dbm = (smeter_raw / 65535.0) * 150.0 - 160.0

            # 官方 kiwiclient: audio = body 后面的部分
            # body 结构: [flags(1)][seq(2)][smeter(2)][audio_pcm...]
            # 所以 audio = body[5:]
            audio_bytes = body[5:]
            # 确保偶数长度
            if len(audio_bytes) % 2 != 0:
                audio_bytes = audio_bytes[1:]  # 跳过一个额外标志字节

            if len(audio_bytes) >= 2:
                samples = np.frombuffer(audio_bytes, dtype=">i2").astype(np.float32) / 32768.0
                rms = float(np.sqrt(np.mean(samples ** 2)))
                peak = float(np.max(np.abs(samples)))
                rms_list.append(rms)
                frame_count += 1

                if frame_count <= 5 or frame_count % 20 == 0:
                    print(f"{frame_count:4d} | {len(msg):5d} | 0x{flags:02x} | {seq:5d} | {smeter_dbm:8.1f} | {rms:10.6f} | {peak:8.5f}")

    await ws.close()

    if not rms_list:
        print("\n没有收到任何 SND 帧!")
        return

    rms_arr = np.array(rms_list)
    print(f"\n{'=' * 60}")
    print(f"  RMS 统计 (共 {len(rms_list)} 帧, 11175 kHz USB)")
    print(f"{'=' * 60}")
    print(f"  底噪范围: {rms_arr.min():.6f} ~ {rms_arr.max():.6f}")
    print(f"  平均值:   {rms_arr.mean():.6f}")
    print(f"  中位数:   {np.median(rms_arr):.6f}")
    print(f"  标准差:   {rms_arr.std():.6f}")
    print()

    noise_floor = np.median(rms_arr)
    print(f"  config.yaml 当前阈值:")
    print(f"    open_threshold:  0.65  -> 能触发信号: {(rms_arr >= 0.65).sum()}/{len(rms_arr)}")
    print(f"    close_threshold: 0.61")
    print()
    print(f"  代码默认阈值:")
    print(f"    open_threshold:  0.02  -> 能触发信号: {(rms_arr >= 0.02).sum()}/{len(rms_list)}")
    print()

    suggested_open = round(noise_floor + max(4 * rms_arr.std(), 0.005), 4)
    suggested_close = round(noise_floor + max(3 * rms_arr.std(), 0.003), 4)
    print(f"  建议阈值 (基于实测底噪):")
    print(f"    open_threshold:  {suggested_open}")
    print(f"    close_threshold: {suggested_close}")

asyncio.run(main())
