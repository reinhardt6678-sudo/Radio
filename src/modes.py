"""
modes.py - 解调模式与通带定义

接收端设的带通滤波器决定了音频里可能存在信号的频率范围，分析端必须用同一个
范围。审计里的缺陷 03（SNR 把 3000-6000 Hz 滤波器阻带当噪声基准）正是这两处
各写各的造成的，所以这张表由 kiwi_client 和 analyzer 共用。
"""

from typing import Tuple

# 解调滤波器通带 (相对载波的 RF 偏移, Hz)
# LSB 在载波下方，所以是负的
DEMOD_FILTERS = {
    "USB": (300.0, 3000.0),
    "LSB": (-3000.0, -300.0),
    "AM": (-4500.0, 4500.0),
    "CW": (400.0, 900.0),
    "CWN": (400.0, 900.0),
    "NFM": (-6000.0, 6000.0),
}

# 解调之后音频里的有效频率范围 (Hz)
# LSB 解调后频谱被翻正，和 USB 一样落在正频率上
AUDIO_PASSBANDS = {
    "USB": (300.0, 3000.0),
    "LSB": (300.0, 3000.0),
    "AM": (100.0, 4500.0),
    "CW": (300.0, 1000.0),
    "CWN": (300.0, 1000.0),
    "NFM": (100.0, 5000.0),
}

DEFAULT_MODE = "USB"


def normalize_mode(mode: str) -> str:
    """规范化模式名，未知模式回落到 USB。"""
    m = (mode or DEFAULT_MODE).upper()
    return m if m in AUDIO_PASSBANDS else DEFAULT_MODE


def demod_filter(mode: str) -> Tuple[float, float]:
    """返回该模式的解调滤波器 (low_cut, high_cut)，单位 Hz。"""
    return DEMOD_FILTERS[normalize_mode(mode)]


def audio_passband(mode: str, sample_rate: float = None) -> Tuple[float, float]:
    """
    返回解调后音频里的有效频率范围 (low, high)，单位 Hz。

    Args:
        mode: 解调模式
        sample_rate: 采样率，给定时上边界会被限制在奈奎斯特频率以内
    """
    low, high = AUDIO_PASSBANDS[normalize_mode(mode)]
    if sample_rate:
        nyquist = sample_rate / 2.0
        high = min(high, nyquist * 0.98)
        low = min(low, high - 1.0)
    return low, high
