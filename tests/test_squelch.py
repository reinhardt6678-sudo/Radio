"""
test_squelch.py - 静噪检测器单元测试

测试 SquelchDetector 的状态机转换、pre-roll 缓冲区和回调机制。
"""

import numpy as np
import pytest
from collections import deque

from src.squelch import SquelchDetector


class TestSquelchDetector:
    """SquelchDetector 核心功能测试。"""

    def _make_detector(self, **kwargs):
        """创建一个测试用的 SquelchDetector 实例。"""
        defaults = {
            "open_threshold": 0.1,
            "close_threshold": 0.05,
            "tail_time": 0.5,
            "pre_roll_seconds": 0.5,
            "sample_rate": 1000,
        }
        defaults.update(kwargs)
        return SquelchDetector(**defaults)

    def test_initial_state_closed(self):
        """初始状态应该是关闭的。"""
        det = self._make_detector()
        assert det.is_open is False

    def test_open_on_loud_signal(self):
        """超过 open_threshold 应打开静噪。"""
        opened = []
        det = self._make_detector(open_threshold=0.1, tail_time=0.0)
        det.set_callbacks(
            on_open=lambda pre_roll, start_time: opened.append(True)
        )

        # 发送超过阈值的信号
        loud = np.full(200, 0.5, dtype=np.float32)
        det.process(loud)

        assert det.is_open is True
        assert len(opened) >= 1

    def test_close_on_quiet_signal(self):
        """低于 close_threshold 应关闭静噪（tail_time 后）。"""
        closed = []
        det = self._make_detector(open_threshold=0.1, close_threshold=0.05,
                                   tail_time=0.0)
        det.set_callbacks(
            on_open=lambda pre_roll, start: None,
            on_close=lambda dur, peak, avg: closed.append(True),
        )

        # 先打开
        loud = np.full(200, 0.5, dtype=np.float32)
        det.process(loud)
        assert det.is_open is True

        # 再关闭 —— 发送足够多的安静数据
        quiet = np.full(2000, 0.001, dtype=np.float32)
        det.process(quiet)

        assert det.is_open is False
        assert len(closed) >= 1

    def test_pre_roll_uses_deque(self):
        """pre-roll 缓冲区应使用 deque 而非 list。"""
        det = self._make_detector()
        assert isinstance(det._pre_roll_blocks, deque)

    def test_pre_roll_captures_data(self):
        """打开前的音频应被 pre-roll 缓冲区捕获。"""
        pre_roll_data = []
        det = self._make_detector(open_threshold=0.1, pre_roll_seconds=0.5,
                                   sample_rate=1000)
        det.set_callbacks(
            on_open=lambda pre_roll, start: pre_roll_data.append(pre_roll),
        )

        # 先发送安静数据（会进入 pre-roll）
        quiet = np.full(300, 0.01, dtype=np.float32)
        det.process(quiet)

        # 再发送响亮数据（触发打开）
        loud = np.full(200, 0.5, dtype=np.float32)
        det.process(loud)

        assert len(pre_roll_data) == 1
        assert len(pre_roll_data[0]) > 0  # pre-roll 应包含之前的数据

    def test_from_config_factory(self):
        """from_config 工厂方法应正确读取配置。"""
        config = {
            "squelch": {
                "open_threshold": 0.15,
                "close_threshold": 0.08,
                "tail_time": 2.0,
            },
            "recording": {"pre_roll": 1.0},
            "receiver": {"sample_rate": 8000},
        }
        det = SquelchDetector.from_config(config)
        assert det.open_threshold == 0.15
        assert det.close_threshold == 0.08
        assert det.tail_time == 2.0
        assert det.sample_rate == 8000
