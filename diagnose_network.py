"""
网络诊断脚本 - 逐层排查 KiwiSDR 连接问题
检查: DNS解析 → TCP连接 → WebSocket握手 → KiwiSDR协议
"""
import asyncio
import socket
import time
import sys

# 测试节点 (从 config.yaml)
NODES = [
    ("sdr.ironstonerange.com", 8075, "VK5PH Australia"),
    ("kphsdr.com", 8073, "KPH California"),
    ("g3sdr.com", 8073, "G3SDR UK"),
    ("la6lukiwisdrth.ddns.net", 8073, "LA6LU Thailand"),
    ("sdr.k1vl.com", 8073, "K1VL Vermont"),
    ("114.16.17.147", 8073, "Minowa Japan"),
    ("178.237.95.113", 8073, "HB3YQQ Switzerland"),
    ("greatlakesreceiver.hopto.me", 8073, "Great Lakes Michigan"),
]

def test_dns(host):
    """测试 DNS 解析"""
    try:
        ip = socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
        return True, ip
    except socket.gaierror as e:
        return False, str(e)

def test_tcp(host, port, timeout=5):
    """测试 TCP 连接"""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True, "OK"
    except socket.timeout:
        return False, "超时"
    except ConnectionRefusedError:
        return False, "连接被拒绝"
    except OSError as e:
        return False, str(e)

async def test_websocket(host, port, timeout=10):
    """测试 WebSocket 连接 + KiwiSDR 握手"""
    try:
        import websockets
        from websockets.asyncio.client import connect as ws_connect
    except ImportError:
        return False, "websockets 未安装", {}

    uri = f"ws://{host}:{port}/kiwi/{int(time.time())}/SND"
    details = {"messages": []}

    try:
        ws = await asyncio.wait_for(
            ws_connect(
                uri,
                additional_headers={"Origin": f"http://{host}:{port}"},
                max_size=2**20,
                ping_interval=None,
            ),
            timeout=timeout
        )

        # 发送 auth
        await ws.send("SET auth t=kiwi p=")

        # 收集握手消息 (最多 5 秒)
        auth_ok = False
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                if isinstance(msg, str):
                    details["messages"].append(msg[:150])
                    if "client_public_ip" in msg or "rx_chans" in msg:
                        auth_ok = True
                    if "too_busy" in msg:
                        await ws.close()
                        return False, "服务器满员 (too_busy)", details
                    if "down" in msg or "redirect" in msg:
                        await ws.close()
                        return False, f"服务器拒绝: {msg[:80]}", details
                elif isinstance(msg, bytes):
                    text = msg.decode('ascii', errors='ignore')
                    details["messages"].append(f"[BIN:{len(msg)}B] {text[:100]}")
                    if "client_public_ip" in text or "rx_chans" in text:
                        auth_ok = True
                    if "too_busy" in text:
                        await ws.close()
                        return False, "服务器满员 (too_busy)", details
                if auth_ok:
                    # 尝试发送音频请求
                    await ws.send("SET AR OK in=12000 out=12000")
                    await ws.send("SET mod=usb low_cut=300 high_cut=3000 freq=11175.000")
                    # 等待看是否收到音频帧
                    try:
                        frame = await asyncio.wait_for(ws.recv(), timeout=3.0)
                        if isinstance(frame, bytes) and len(frame) > 5:
                            tag = chr(frame[0])
                            details["first_frame_tag"] = tag
                            details["first_frame_len"] = len(frame)
                            await ws.close()
                            return True, f"完全成功! 收到音频帧 (tag={tag}, {len(frame)}B)", details
                    except asyncio.TimeoutError:
                        await ws.close()
                        return True, "握手成功但未收到音频帧", details
            except asyncio.TimeoutError:
                continue

        await ws.close()
        if auth_ok:
            return True, "握手成功", details
        else:
            return False, "握手超时(无 auth 响应)", details

    except asyncio.TimeoutError:
        return False, "WebSocket 连接超时", details
    except ConnectionRefusedError:
        return False, "WebSocket 连接被拒绝", details
    except OSError as e:
        return False, f"网络错误: {e}", details
    except Exception as e:
        return False, f"异常: {type(e).__name__}: {e}", details


async def main():
    print("=" * 80)
    print("  KiwiSDR 网络连接诊断")
    print("=" * 80)

    # 先测试基本网络
    print("\n[0] 基本网络检查...")
    for test_host in ["8.8.8.8", "1.1.1.1"]:
        ok, info = test_tcp(test_host, 53, timeout=3)
        print(f"  DNS 服务 {test_host}:53 → {'✓' if ok else '✗'} {info}")

    # 测试一个常规 HTTP 端口
    ok, info = test_tcp("google.com", 80, timeout=5)
    print(f"  HTTP google.com:80 → {'✓' if ok else '✗'} {info}")

    # 测试一个非标准端口（类似 8073）
    ok, info = test_tcp("google.com", 443, timeout=5)
    print(f"  HTTPS google.com:443 → {'✓' if ok else '✗'} {info}")

    print("\n" + "=" * 80)
    print(f"  逐节点诊断 ({len(NODES)} 个节点)")
    print("=" * 80)

    all_results = []

    for host, port, name in NODES:
        print(f"\n--- {name} ({host}:{port}) ---")

        # Step 1: DNS
        if not host.replace(".", "").isdigit():  # 跳过 IP 地址
            ok, info = test_dns(host)
            print(f"  [1] DNS 解析: {'✓' if ok else '✗'} → {info}")
            if not ok:
                all_results.append((name, "DNS_FAIL"))
                continue
        else:
            print(f"  [1] DNS 解析: - (直接 IP)")

        # Step 2: TCP
        ok, info = test_tcp(host, port, timeout=5)
        print(f"  [2] TCP 连接: {'✓' if ok else '✗'} → {info}")
        if not ok:
            all_results.append((name, f"TCP_FAIL: {info}"))
            continue

        # Step 3: WebSocket + KiwiSDR
        print(f"  [3] WebSocket + KiwiSDR 握手...")
        ok, info, details = await test_websocket(host, port, timeout=12)
        print(f"       {'✓' if ok else '✗'} → {info}")
        if details.get("messages"):
            for i, m in enumerate(details["messages"][:3]):
                print(f"       消息[{i}]: {m[:100]}")

        all_results.append((name, "OK" if ok else f"WS_FAIL: {info}"))

    # 总结
    print("\n" + "=" * 80)
    print("  诊断总结")
    print("=" * 80)
    ok_count = sum(1 for _, r in all_results if r == "OK")
    print(f"\n  可用: {ok_count}/{len(all_results)}")
    for name, result in all_results:
        symbol = "✓" if result == "OK" else "✗"
        print(f"  {symbol} {name:.<35} {result}")

    if ok_count == 0:
        print("\n  ⚠ 所有节点都失败了！可能的原因：")
        tcp_fails = [r for _, r in all_results if "TCP_FAIL" in r]
        dns_fails = [r for _, r in all_results if "DNS_FAIL" in r]
        ws_fails = [r for _, r in all_results if "WS_FAIL" in r]

        if len(dns_fails) > len(all_results) // 2:
            print("    → DNS 解析大面积失败: 检查网络连接和 DNS 设置")
        if len(tcp_fails) > len(all_results) // 2:
            if any("超时" in r for _, r in all_results):
                print("    → TCP 连接超时: 防火墙/代理可能封锁了 8073 端口")
                print("    → 尝试: 关闭 VPN/代理, 或换个网络环境")
            if any("拒绝" in r for _, r in all_results):
                print("    → TCP 连接被拒绝: 节点可能确实离线")
        if len(ws_fails) > len(all_results) // 2:
            if any("too_busy" in r for _, r in all_results):
                print("    → 多个节点满员: 这些公共节点用户太多")
            else:
                print("    → WebSocket 握手失败: 可能是网络代理干扰了 WS 协议")
                print("    → 尝试: 关闭系统代理/VPN 后重试")

    print()

if __name__ == "__main__":
    asyncio.run(main())
