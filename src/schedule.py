"""
schedule.py - 频率活跃时段 (active_hours) 解析

frequencies.yaml 里每个频率都写了 active_hours，但在此之前没有任何代码读过它，
纯粹是注释。结果是: 在 03:00 UTC 去守 11175 这种"频率没错、但时段不对"的情况，
系统一句话都不会说，看上去就只是"一个信号都没有"。

这里把它解析出来，让监听启动时至少能提示一句，并给出当前时段在线的频率。

支持的写法:
    "10:00-22:00 UTC"   固定时段 (UTC)
    "22:00-06:00 UTC"   跨零点
    "全天" / "24小时"    始终活跃
无法识别的写法一律按"始终活跃"处理 —— 宁可不提示，也不要误报。
"""

import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple

# "10:00-22:00 UTC" / "10:00 - 22:00"
_RANGE_RE = re.compile(
    r"(?P<h1>\d{1,2}):(?P<m1>\d{2})\s*[-~—]\s*(?P<h2>\d{1,2}):(?P<m2>\d{2})"
)

# 表示"全天在线"的写法
_ALWAYS = ("全天", "24小时", "24h", "24 小时", "always", "h24")


def parse_active_hours(text: Optional[str]) -> Optional[Tuple[int, int]]:
    """
    把 active_hours 解析成 (起, 止) 两个"距 UTC 零点的分钟数"。

    Returns:
        (start_min, end_min)，始终活跃或无法识别时返回 None。
        跨零点的时段满足 start_min > end_min。
    """
    if not text:
        return None

    normalized = str(text).strip().lower()
    if any(token in normalized for token in _ALWAYS):
        return None

    match = _RANGE_RE.search(normalized)
    if not match:
        return None

    h1, m1 = int(match.group("h1")), int(match.group("m1"))
    h2, m2 = int(match.group("h2")), int(match.group("m2"))
    if not (0 <= h1 <= 24 and 0 <= h2 <= 24 and m1 < 60 and m2 < 60):
        return None

    start = (h1 % 24) * 60 + m1
    end = (h2 % 24) * 60 + m2
    if start == end:
        return None  # 覆盖满一整天，等同于全天

    return start, end


def is_active(text: Optional[str], now: datetime = None) -> bool:
    """
    当前 (UTC) 是否落在该频率的活跃时段内。

    无法解析或标注为全天的一律返回 True。
    """
    window = parse_active_hours(text)
    if window is None:
        return True

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    minute_of_day = now.astimezone(timezone.utc).hour * 60 + now.minute

    start, end = window
    if start < end:
        return start <= minute_of_day < end
    # 跨零点: 例如 22:00-06:00
    return minute_of_day >= start or minute_of_day < end


def format_window(text: Optional[str]) -> str:
    """把 active_hours 显示成统一的样子。"""
    window = parse_active_hours(text)
    if window is None:
        return "全天"
    start, end = window
    return f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d} UTC"


def active_frequencies(freq_data: dict, now: datetime = None) -> List[dict]:
    """
    从 frequencies.yaml 的原始数据里挑出当前时段在线的频率。

    Args:
        freq_data: yaml.safe_load(frequencies.yaml) 的结果
        now: 判定时刻，默认当前 UTC

    Returns:
        [{freq, mode, description, network, priority, active_hours}, ...]
    """
    now = now or datetime.now(timezone.utc)
    result = []

    for network_key, network_data in (freq_data or {}).items():
        if not isinstance(network_data, dict):
            continue
        for info in network_data.get("frequencies", []):
            if not isinstance(info, dict) or "freq" not in info:
                continue
            if is_active(info.get("active_hours"), now):
                result.append({**info, "network": network_key})

    return result


def check_targets(freq_data: dict, freq_khz_list: List[float],
                  now: datetime = None) -> List[str]:
    """
    检查要监听的频率现在是否在活跃时段，返回给用户看的提示行。

    Args:
        freq_data: frequencies.yaml 的原始数据
        freq_khz_list: 本次要监听的频率 (kHz)
        now: 判定时刻，默认当前 UTC

    Returns:
        提示信息列表；全部都在时段内时返回空列表。
    """
    now = now or datetime.now(timezone.utc)
    lines = []

    # 建一张 freq -> info 的表
    known = {}
    for network_key, network_data in (freq_data or {}).items():
        if not isinstance(network_data, dict):
            continue
        for info in network_data.get("frequencies", []):
            if isinstance(info, dict) and "freq" in info:
                known.setdefault(float(info["freq"]), {**info, "network": network_key})

    off_window = []
    for freq_khz in freq_khz_list:
        info = known.get(float(freq_khz))
        if info and not is_active(info.get("active_hours"), now):
            off_window.append(info)

    if not off_window:
        return lines

    lines.append(
        f"[NOTE] 当前 UTC {now.astimezone(timezone.utc).strftime('%H:%M')}，"
        f"以下频率不在标注的活跃时段内:"
    )
    for info in off_window:
        lines.append(
            f"       {info['freq']:.1f} kHz 活跃时段 "
            f"{format_window(info.get('active_hours'))} "
            f"({info.get('description', '')})"
        )

    # 给出同一时段内在线的高优先级频率作为替代
    alternatives = [
        f for f in active_frequencies(freq_data, now)
        if f.get("priority") == "high"
        and float(f["freq"]) not in {float(x) for x in freq_khz_list}
    ]
    if alternatives:
        listed = ", ".join(
            f"{f['freq']:.1f} kHz" for f in alternatives[:5]
        )
        lines.append(f"       当前时段标注为活跃的高优先级频率: {listed}")

    lines.append(
        "       活跃时段只是传播经验值，不会拦截监听 —— 收不到信号时先换到上面的频率试试。"
    )
    return lines
