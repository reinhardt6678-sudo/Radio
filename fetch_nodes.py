"""
从 KiwiSDR 公开节点列表获取当前在线、有空闲通道的节点，
筛选适合 HF 全频段监听的高质量节点。
"""
import json, re, sys, urllib.request

URL = "http://rx.linkfanel.net/kiwisdr_com.js"

def fetch_and_parse():
    print("正在从 rx.linkfanel.net 获取最新节点列表...")
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="ignore")
    
    # 提取 JSON 数组部分
    start = text.index("[")
    end = text.rindex("]") + 1
    json_text = text[start:end]
    # 修复可能的尾部逗号
    json_text = re.sub(r',\s*]', ']', json_text)
    nodes = json.loads(json_text)
    print(f"共获取 {len(nodes)} 个节点")
    return nodes

def parse_url(url_str):
    """从 url 字段提取 host 和 port"""
    url_str = url_str.replace("http://", "").replace("https://", "").rstrip("/")
    if ":" in url_str:
        parts = url_str.rsplit(":", 1)
        host = parts[0]
        try:
            port = int(parts[1])
        except ValueError:
            port = 8073
    else:
        host = url_str
        port = 8073
    return host, port

def parse_gps(gps_str):
    """解析 GPS 坐标 '(lat, lon)'"""
    m = re.match(r'\(([^,]+),\s*([^)]+)\)', gps_str)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None

def filter_nodes(nodes):
    """筛选条件:
    1. status=active, offline=no
    2. 频段覆盖 HF (至少 2-30 MHz)
    3. 有空闲通道 (users < users_max)
    4. 不使用 proxy (proxy 可能不稳定)
    5. SNR 较好
    """
    good = []
    for n in nodes:
        if n.get("status") != "active" or n.get("offline") != "no":
            continue
        
        # 检查空闲通道
        try:
            users = int(n.get("users", 0))
            users_max = int(n.get("users_max", 0))
        except (ValueError, TypeError):
            continue
        if users >= users_max:
            continue
        
        # 检查频段 - 要求能覆盖到至少 30MHz
        bands = n.get("bands", "")
        try:
            parts = bands.split("-")
            band_low = int(parts[0])
            band_high = int(parts[1])
        except (ValueError, IndexError):
            continue
        if band_high < 30000000:
            continue
        
        url = n.get("url", "")
        host, port = parse_url(url)
        
        # 跳过 proxy 节点 (可能不稳定)
        is_proxy = "proxy.kiwisdr.com" in host
        
        # 解析 SNR
        snr_str = n.get("snr", "0,0")
        try:
            snr_parts = snr_str.split(",")
            snr = int(snr_parts[0])
        except (ValueError, IndexError):
            snr = 0
        
        lat, lon = parse_gps(n.get("gps", ""))
        loc = n.get("loc", "")
        name_raw = n.get("name", "")
        # 清理名称中的特殊字符
        name_clean = re.sub(r'[█#*]+\s*', '', name_raw).strip()
        if len(name_clean) > 40:
            name_clean = name_clean[:40]
        
        good.append({
            "host": host,
            "port": port,
            "name": name_clean,
            "location": loc,
            "lat": lat,
            "lon": lon,
            "snr": snr,
            "users": users,
            "users_max": users_max,
            "free_slots": users_max - users,
            "is_proxy": is_proxy,
            "url": url,
        })
    
    # 优先选择: 非proxy > SNR高 > 空闲通道多
    good.sort(key=lambda x: (not x["is_proxy"], x["snr"], x["free_slots"]), reverse=True)
    return good

def main():
    nodes = fetch_and_parse()
    filtered = filter_nodes(nodes)
    
    print(f"\n筛选出 {len(filtered)} 个可用节点 (有空闲通道, HF全频段)")
    print(f"其中 {sum(1 for n in filtered if not n['is_proxy'])} 个直连节点, "
          f"{sum(1 for n in filtered if n['is_proxy'])} 个代理节点\n")
    
    # 显示前20个推荐节点
    print("=" * 100)
    print(f"{'排名':>4} {'名称':<35} {'地址':<35} {'SNR':>4} {'空闲':>4} {'位置':<20}")
    print("-" * 100)
    for i, n in enumerate(filtered[:20], 1):
        addr = f"{n['host']}:{n['port']}"
        proxy_tag = " [P]" if n['is_proxy'] else ""
        print(f"{i:>4} {n['name'][:34]:<35} {addr[:34]:<35} {n['snr']:>4} {n['free_slots']:>4} {n['location'][:19]:<20}{proxy_tag}")
    print("=" * 100)
    
    # 生成推荐的 YAML 配置
    print("\n\n===== 推荐写入 config.yaml 的节点配置 =====\n")
    recommended = []
    # 选择分布在不同地区的节点
    regions_seen = set()
    for n in filtered:
        # 简单地按位置关键字分类区域
        loc_lower = n['location'].lower()
        region = "other"
        for r in ["australia", "japan", "new zealand", "usa", "california", 
                   "united kingdom", "england", "germany", "europe",
                   "thailand", "indonesia", "india", "china"]:
            if r in loc_lower:
                region = r
                break
        
        if region not in regions_seen or len(recommended) < 10:
            recommended.append(n)
            regions_seen.add(region)
        
        if len(recommended) >= 10:
            break
    
    print("nodes:")
    for n in recommended:
        print(f'  - host: "{n["host"]}"')
        print(f'    port: {n["port"]}')
        print(f'    name: "{n["name"]}"')
        print(f'    location: "{n["location"]}"')
        if n["lat"] is not None:
            print(f'    lat: {n["lat"]}')
            print(f'    lon: {n["lon"]}')
        print()

if __name__ == "__main__":
    main()
