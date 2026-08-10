"""
squelch.py - 静噪检测 / 信号活动检测 (VOX)

通过分析音频流的 RMS 能量来检测信号活动，
在信号出现时触发录制，在信号消失后延迟关闭。
"""

import numpy as np
import logging
import time
from typing import Optional, Callable, List
from collections import deque

logger = logging.getLogger(__name__)


class SquelchDetector:
    """
    信号活动检测器（静噪/VOX）。

    使用 RMS 能量阈值检测信号活动，具有以下特性：
    - 开/关阈值分离（滞后），避免在阈值附近频繁切换
    - 可配置的尾部延迟（tail_time），防止截断信号末尾
    - pre-roll 缓冲区，保留信号开始前的音频
    """

    def __init__(self, open_threshold: float = 0.02,
                 close_threshold: float = 0.015,
                 tail_time: float = 3.0,
                 pre_roll_seconds: float = 2.0,
                 sample_rate: int = 12000,
                 window_size: int = 1024):
        """
        初始化静噪检测器。

        Args:
            open_threshold: 静噪打开阈值 (RMS, 0-1)
            close_threshold: 静噪关闭阈值 (RMS, 通常 < open_threshold)
            tail_time: 信号消失后继续录制的秒数
            pre_roll_seconds: 保留信号开始前的音频秒数
            sample_rate: 音频采样率
            window_size: RMS 计算窗口大小（采样点数）
        """
        self.open_threshold = open_threshold
        self.close_threshold = close_threshold
        self.tail_time = tail_time
        self.pre_roll_seconds = pre_roll_seconds
        self.sample_rate = sample_rate
        self.window_size = window_size

        # 状态
        self.is_open = False           # 静噪是否打开（有信号）
        self._last_above_time = 0.0    # 上次高于阈值的时间
        self._signal_start_time = 0.0  # 当前信号开始时间

        # RMS 历史记录
        self._rms_history: deque = deque(maxlen=100)
        self._current_rms = 0.0
        self._peak_rms = 0.0

        # Pre-roll 块级缓冲区 (存储完整音频块而非逐样本)
        self._pre_roll_max_samples = int(pre_roll_seconds * sample_rate)
        self._pre_roll_blocks: deque = deque()
        self._pre_roll_total_samples = 0

        # 回调
        self._on_open: Optional[Callable] = None   # 信号开始
        self._on_close: Optional[Callable] = None  # 信号结束
        self._on_audio: Optional[Callable] = None   # 活跃期间的音频

    @classmethod
    def from_config(cls, config: dict) -> "SquelchDetector":
        """
        从配置字典创建静噪检测器。

        Args:
            config: 完整配置字典 (包含 squelch 和 recording/receiver 部分)
        """
        sq_cfg = config.get("squelch", {})
        rec_cfg = config.get("recording", {})
        recv_cfg = config.get("receiver", {})
        return cls(
            open_threshold=sq_cfg.get("open_threshold", 0.02),
            close_threshold=sq_cfg.get("close_threshold", 0.015),
            tail_time=sq_cfg.get("tail_time", 3.0),
            pre_roll_seconds=rec_cfg.get("pre_roll", 2.0),
            sample_rate=recv_cfg.get("sample_rate", 12000),
        )

    def set_callbacks(self,
                      on_open: Callable = None,
                      on_close: Callable = None,
                      on_audio: Callable = None):
        """
        设置事件回调。

        Args:
            on_open: 信号开始时调用 (pre_roll_samples, signal_start_time)
            on_close: 信号结束时调用 (duration, peak_rms, avg_rms)
            on_audio: 信号活跃期间每收到音频块时调用 (samples)
        """
        self._on_open = on_open
        self._on_close = on_close
        self._on_audio = on_audio

    def process(self, samples: np.ndarray) -> bool:
        """
        处理一块音频样本，更新静噪状态。

        Args:
            samples: float32 音频样本数组 (-1.0 到 1.0)

        Returns:
            当前是否有信号活动
        """
        now = time.time()

        # 计算 RMS
        rms = self._compute_rms(samples)
        self._current_rms = rms
        self._rms_history.append(rms)

        if not self.is_open:
            # 当前静噪关闭状态 - 保存到 pre-roll 缓冲区
            self._pre_roll_blocks.append(samples.copy())
            self._pre_roll_total_samples += len(samples)
            # 修剪超出上限的旧块
            while (self._pre_roll_total_samples > self._pre_roll_max_samples
                   and len(self._pre_roll_blocks) > 1):
                removed = self._pre_roll_blocks.popleft()
                self._pre_roll_total_samples -= len(removed)

            # 检查是否应该打开静噪
            if rms >= self.open_threshold:
                self.is_open = True
                self._signal_start_time = now
                self._last_above_time = now
                self._peak_rms = rms

                logger.info(f"[SIGNAL-ON] RMS={rms:.4f} (threshold={self.open_threshold})")

                # 回调: 信号开始，传递 pre-roll 数据
                if self._on_open:
                    if self._pre_roll_blocks:
                        pre_roll_data = np.concatenate(self._pre_roll_blocks)
                    else:
                        pre_roll_data = np.array([], dtype=np.float32)
                    self._on_open(pre_roll_data, self._signal_start_time)

                # 当前块也作为活跃音频
                if self._on_audio:
                    self._on_audio(samples)
        else:
            # 当前静噪打开状态 - 有信号
            if rms >= self.close_threshold:
                self._last_above_time = now
                self._peak_rms = max(self._peak_rms, rms)

            # 传递活跃音频
            if self._on_audio:
                self._on_audio(samples)

            # 检查是否应该关闭静噪 (信号消失 + tail_time 已过)
            if rms < self.close_threshold:
                elapsed_since_signal = now - self._last_above_time
                if elapsed_since_signal >= self.tail_time:
                    self.is_open = False
                    duration = now - self._signal_start_time

                    # 计算平均 RMS
                    avg_rms = float(np.mean(list(self._rms_history)))

                    logger.info(
                        f"[SIGNAL-OFF] duration={duration:.1f}s, "
                        f"peak_RMS={self._peak_rms:.4f}, "
                        f"avg_RMS={avg_rms:.4f}"
                    )

                    # 回调: 信号结束
                    if self._on_close:
                        self._on_close(duration, self._peak_rms, avg_rms)

                    # 重置
                    self._peak_rms = 0.0
                    self._rms_history.clear()
                    self._pre_roll_blocks.clear()
                    self._pre_roll_total_samples = 0

        return self.is_open

    def _compute_rms(self, samples: np.ndarray) -> float:
        """计算 RMS (Root Mean Square) 能量。"""
        if len(samples) == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples ** 2)))

    @property
    def current_rms(self) -> float:
        """当前 RMS 能量值。"""
        return self._current_rms

    @property
    def signal_duration(self) -> float:
        """当前信号已持续时间（秒），如果无信号则返回 0。"""
        if self.is_open:
            return time.time() - self._signal_start_time
        return 0.0

    def get_status(self) -> dict:
        """获取检测器当前状态。"""
        return {
            "is_open": self.is_open,
            "current_rms": self._current_rms,
            "peak_rms": self._peak_rms,
            "signal_duration": self.signal_duration,
            "open_threshold": self.open_threshold,
            "close_threshold": self.close_threshold,
        }
