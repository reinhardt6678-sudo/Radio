"""
test_squelch.py - 静噪检测器单元测试

测试 SquelchDetector 的状态机转换、pre-roll 缓冲区和回调机制。
"""

import numpy as np
import pytest
from collections import deque

from src.squelch import SquelchDetector, MODE_ADAPTIVE


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

    def test_from_config_reads_adaptive_keys(self):
        """自适应模式的配置项应被读到。"""
        config = {
            "squelch": {
                "mode": "adaptive",
                "open_margin_db": 8.0,
                "close_margin_db": 4.0,
                "max_open_seconds": 120,
            },
            "recording": {"max_duration": 300},
        }
        det = SquelchDetector.from_config(config)
        assert det.mode == MODE_ADAPTIVE
        assert det.open_margin_db == 8.0
        assert det.close_margin_db == 4.0
        assert det.max_open_seconds == 120

    def test_max_open_seconds_beats_recorder_split(self):
        """
        没配 max_open_seconds 时按录音分段推导，并且必须比录音器先收尾。

        录音器按已写入样本数分段，pre-roll 先占掉一段: 录音器先切的话，
        那个 WAV 不会有任何数据库记录。
        """
        config = {"recording": {"max_duration": 300, "pre_roll": 2.0}}
        det = SquelchDetector.from_config(config)
        recorder_splits_at = 300 - 2.0   # 录音器写满 300 秒音频的时刻
        assert det.max_open_seconds < recorder_splits_at

    def test_max_open_seconds_explicit_wins(self):
        det = SquelchDetector.from_config({
            "squelch": {"max_open_seconds": 120},
            "recording": {"max_duration": 300},
        })
        assert det.max_open_seconds == 120


class TestNoiseFloor:
    """底噪统计 —— 静噪开着的时候也必须继续更新。"""

    def _feed(self, det, level, blocks=80, size=64):
        for _ in range(blocks):
            det.process(np.full(size, level, dtype=np.float32))

    def test_floor_measures_noise(self):
        det = SquelchDetector(open_threshold=0.5, close_threshold=0.4,
                              floor_min_blocks=20)
        self._feed(det, 0.09)
        assert det.is_open is False
        assert det.noise_floor == pytest.approx(0.09, rel=0.05)

    def test_floor_keeps_updating_while_open(self):
        """
        回归: 底噪只在静噪关闭时统计的话，阈值一旦低于底噪，
        静噪就一直开着，底噪被冻结在监听刚起步时的数值上，
        "按底噪设定"照着这个死值只会把阈值调得更低。
        """
        det = SquelchDetector(open_threshold=0.01, close_threshold=0.004,
                              tail_time=0.0, floor_min_blocks=20,
                              max_open_seconds=0)
        det.FLOOR_RECALC_INTERVAL = 0.0

        # 开头几块是接近静音的 (连接刚建立，音频还没真正上来)
        self._feed(det, 0.0038, blocks=30)
        frozen_floor = det.noise_floor
        assert frozen_floor == pytest.approx(0.0038, rel=0.05)

        # 之后真实音频进来，阈值远低于底噪 -> 静噪一直打开
        self._feed(det, 0.09, blocks=400)
        assert det.is_open is True
        assert det.noise_floor > frozen_floor * 10
        assert det.noise_floor == pytest.approx(0.09, rel=0.05)

    def test_suggested_thresholds_follow_floor(self):
        det = SquelchDetector(open_threshold=0.5, close_threshold=0.4,
                              floor_min_blocks=20)
        self._feed(det, 0.09)
        suggested_open, suggested_close = det.suggested_thresholds()
        assert suggested_open == pytest.approx(0.09 * 2.0, rel=0.06)
        assert suggested_close == pytest.approx(0.09 * 1.413, rel=0.06)
        assert suggested_close < suggested_open


class TestStuckSquelch:
    """阈值低于底噪 -> 静噪关不掉 -> 一条信号记录都不出。"""

    def _feed(self, det, level, blocks=80, size=64):
        for _ in range(blocks):
            det.process(np.full(size, level, dtype=np.float32))

    def test_threshold_below_floor_is_flagged(self):
        det = SquelchDetector(open_threshold=0.010, close_threshold=0.004,
                              tail_time=0.0, floor_min_blocks=20,
                              max_open_seconds=0)
        det.FLOOR_RECALC_INTERVAL = 0.0
        self._feed(det, 0.09, blocks=60)

        assert det.is_open is True
        assert det.threshold_below_floor is True

    def test_forced_close_emits_signal(self):
        """
        回归: 电平一直压在关闭阈值之上时，只在关闭时计数的实现
        会永远停在 0 个信号。到点必须强制收尾并落一条记录。
        """
        closed = []
        det = SquelchDetector(open_threshold=0.010, close_threshold=0.004,
                              tail_time=0.0, floor_min_blocks=20,
                              max_open_seconds=300)
        det.FLOOR_RECALC_INTERVAL = 0.0
        det.set_callbacks(on_close=lambda dur, peak, avg: closed.append(dur))

        self._feed(det, 0.09, blocks=60)
        assert det.is_open is True
        assert closed == []

        # 把开始时间往前挪，等价于已经连续打开了 301 秒
        det._signal_start_time -= 301
        det.process(np.full(64, 0.09, dtype=np.float32))

        assert len(closed) == 1
        assert closed[0] >= 300
        assert det.forced_closes == 1
        assert det.stuck_open is True

    def test_long_transmission_splits_without_stuck_flag(self):
        """电平确实回落的长信号只是分段，不该报"卡死"。"""
        closed = []
        det = SquelchDetector(open_threshold=0.05, close_threshold=0.02,
                              tail_time=5.0, max_open_seconds=300,
                              floor_min_blocks=20)
        det.set_callbacks(on_close=lambda dur, peak, avg: closed.append(dur))

        det.process(np.full(64, 0.3, dtype=np.float32))
        det._signal_start_time -= 301
        # 收尾这一块已经回到底噪，只是尾部延迟还没到
        det.process(np.full(64, 0.01, dtype=np.float32))

        assert len(closed) == 1
        assert det.forced_closes == 1
        assert det.stuck_open is False

    def test_force_close_records_in_progress_signal(self):
        """断线时正在录的信号要能正常收尾入库。"""
        closed = []
        det = SquelchDetector(open_threshold=0.05, close_threshold=0.02,
                              tail_time=3.0)
        det.set_callbacks(on_close=lambda dur, peak, avg: closed.append(peak))

        det.process(np.full(64, 0.3, dtype=np.float32))
        assert det.is_open is True

        assert det.force_close("disconnect") is True
        assert det.is_open is False
        assert len(closed) == 1
        assert closed[0] == pytest.approx(0.3, rel=0.01)

        # 已经关闭时再调用是空操作
        assert det.force_close("disconnect") is False
        assert len(closed) == 1


class TestAdaptiveMode:
    """自适应阈值: 阈值跟着实测底噪走。"""

    def _feed(self, det, level, blocks=80, size=64):
        for _ in range(blocks):
            det.process(np.full(size, level, dtype=np.float32))

    def test_thresholds_follow_noise_floor(self):
        det = SquelchDetector(open_threshold=0.10, close_threshold=0.085,
                              mode=MODE_ADAPTIVE, tail_time=0.0,
                              floor_min_blocks=20)
        det.FLOOR_RECALC_INTERVAL = 0.0

        # 底噪 0.09: 固定阈值 0.10 几乎贴着底噪，自适应会抬到 0.18
        self._feed(det, 0.09, blocks=60)
        assert det.noise_floor == pytest.approx(0.09, rel=0.05)
        assert det.effective_open_threshold == pytest.approx(0.18, rel=0.06)
        assert det.effective_close_threshold == pytest.approx(0.127, rel=0.06)
        assert det.effective_close_threshold < det.effective_open_threshold
        assert det.is_open is False
        assert det.threshold_below_floor is False

    def test_opens_on_signal_above_floor(self):
        opened = []
        det = SquelchDetector(mode=MODE_ADAPTIVE, tail_time=0.0,
                              floor_min_blocks=20)
        det.FLOOR_RECALC_INTERVAL = 0.0
        det.set_callbacks(on_open=lambda pre_roll, start: opened.append(True))

        self._feed(det, 0.09, blocks=60)
        assert not opened

        det.process(np.full(64, 0.4, dtype=np.float32))
        assert det.is_open is True
        assert len(opened) == 1

    def test_waits_for_floor_before_judging(self):
        """
        底噪还没测出来之前不判信号。

        这时候退回到一个没在本节点校准过的绝对阈值，
        正好是这套系统之前踩坑的地方 —— 宁可等两秒。
        """
        det = SquelchDetector(open_threshold=0.2, close_threshold=0.1,
                              mode=MODE_ADAPTIVE, floor_min_blocks=1000)
        det.process(np.full(64, 0.9, dtype=np.float32))
        assert det.noise_floor == 0.0
        assert det.warming_up is True
        assert det.is_open is False
        # pre-roll 照常在攒，底噪出来后信号开头不会丢
        assert det._pre_roll_total_samples > 0

    def test_silent_stream_is_not_a_signal(self):
        """链路全 0 时底噪为 0，不能把数字静音当成信号。"""
        det = SquelchDetector(mode=MODE_ADAPTIVE, min_open_threshold=0.005,
                              floor_min_blocks=20)
        det.FLOOR_RECALC_INTERVAL = 0.0
        for _ in range(60):
            det.process(np.zeros(64, dtype=np.float32))
        assert det.is_open is False
        assert det.effective_open_threshold >= 0.005
