"""
test_smeter_capture.py - 信号强度必须取自信号期间 / signal strength must come from the signal

背景: 接收器过去在 on_close 里读 client.smeter。那已经是信号结束之后, 还要再加上
tail_time 秒的收尾, 此时电平早就掉回底噪 —— 于是库里每一条记录的 s_meter_dbm
存的都是底噪, 不是信号。2026-09-01 实测: 两条信号打开时 S-meter 是 -83.0 和
-81.9 dBm, 入库成了 -97.9 和 -98.9 dBm, 距当时底噪不到 0.6 dB, 偏差 15-17 dB。

这个错误从数据上看不出来: 存的是个合理的 dBm 数值, 只是取自错误的时刻。

Background: the receiver used to read client.smeter inside on_close. That runs after the
signal has ended, plus tail_time on top, by which point the level has fallen back to the
noise floor -- so every s_meter_dbm in the database held the floor, not the signal. Measured
2026-09-01: two signals reading -83.0 and -81.9 dBm while open were filed as -97.9 and
-98.9 dBm, within 0.6 dB of the floor -- wrong by 15-17 dB, and invisible in the data,
because a plausible dBm value taken at the wrong moment still looks like a reading.
"""

import numpy as np
import pytest

from src.squelch import SquelchDetector, MODE_SMETER


def _det(**kw):
    d = {"mode": MODE_SMETER, "tail_time": 0.0, "pre_roll_seconds": 0.1,
         "sample_rate": 1000, "smeter_open_margin_db": 14.0,
         "smeter_close_margin_db": 10.0, "smeter_floor_min_samples": 5,
         "smeter_floor_window_seconds": 600.0, "dead_audio_min_blocks": 0}
    d.update(kw)
    return SquelchDetector(**d)


def _feed(det, dbm, n=40, block=None):
    block = np.zeros(100, dtype=np.float32) if block is None else block
    for _ in range(n):
        det._last_smeter_calc = 0.0
        det.process(block, dbm)


class TestPeakCapturedDuringSignal:

    def test_peak_is_the_in_signal_level_not_the_floor(self):
        """
        整条改动的要点: 信号结束后电平掉回底噪, 存下来的必须仍是信号期间的值。
        The point of the whole change: the level falls back to the floor once the signal
        ends, and what gets stored must still be the value from while it was up.
        """
        det = _det()
        _feed(det, -110.0)                       # 建立底噪
        blk = np.zeros(100, dtype=np.float32)

        det._last_smeter_calc = 0.0
        det.process(blk, -80.0)                  # 信号来了
        assert det.is_open is True
        det._last_smeter_calc = 0.0
        det.process(blk, -78.0)                  # 更强
        det._last_smeter_calc = 0.0
        det.process(blk, -110.0)                 # 掉回底噪, 静噪关闭
        assert det.is_open is False

        assert det.last_peak_smeter_dbm == pytest.approx(-78.0)
        assert det.last_peak_smeter_dbm > -85.0, "存成底噪就是这个 bug 本身"

    def test_average_is_over_the_signal_only(self):
        det = _det()
        _feed(det, -110.0)
        blk = np.zeros(100, dtype=np.float32)
        for dbm in (-80.0, -84.0):
            det._last_smeter_calc = 0.0
            det.process(blk, dbm)
        det._last_smeter_calc = 0.0
        det.process(blk, -110.0)                 # 收尾那一块不该算进均值
        assert det.last_avg_smeter_dbm == pytest.approx(-82.0)

    def test_tail_blocks_do_not_drag_the_peak_down(self):
        """
        tail_time 期间电平已经低于关闭阈值, 那段不属于信号。
        把它算进来正是旧代码最终存下底噪的原因。
        """
        det = _det(tail_time=5.0)
        _feed(det, -110.0)
        blk = np.zeros(100, dtype=np.float32)
        det._last_smeter_calc = 0.0
        det.process(blk, -80.0)
        assert det.is_open is True
        for _ in range(5):                       # 静噪仍开着, 但电平已在关闭阈值之下
            det._last_smeter_calc = 0.0
            det.process(blk, -115.0)
        det.force_close("test")
        assert det.last_peak_smeter_dbm == pytest.approx(-80.0)
        assert det.last_avg_smeter_dbm == pytest.approx(-80.0)

    def test_values_survive_the_close_for_the_callback_to_read(self):
        """on_close 是三参数约定, 所以这两个值必须在回调里读得到。"""
        seen = {}
        det = _det()
        det.set_callbacks(on_close=lambda d, p, a: seen.update(
            peak=det.last_peak_smeter_dbm, avg=det.last_avg_smeter_dbm))
        _feed(det, -110.0)
        blk = np.zeros(100, dtype=np.float32)
        det._last_smeter_calc = 0.0
        det.process(blk, -80.0)
        det._last_smeter_calc = 0.0
        det.process(blk, -110.0)
        assert seen["peak"] == pytest.approx(-80.0)
        assert seen["avg"] == pytest.approx(-80.0)

    def test_second_signal_does_not_inherit_the_first(self):
        det = _det()
        _feed(det, -110.0)
        blk = np.zeros(100, dtype=np.float32)
        for dbm in (-70.0, -110.0):              # 第一段: 很强
            det._last_smeter_calc = 0.0
            det.process(blk, dbm)
        assert det.last_peak_smeter_dbm == pytest.approx(-70.0)
        for dbm in (-84.0, -110.0):              # 第二段: 弱得多
            det._last_smeter_calc = 0.0
            det.process(blk, dbm)
        assert det.last_peak_smeter_dbm == pytest.approx(-84.0), "上一段的峰值漏了过来"

    def test_none_when_no_smeter_was_supplied(self):
        """非 smeter 模式下调用方可以不传 S-meter, 那就没有值可存, 不能瞎编。"""
        det = _det(mode="absolute", open_threshold=0.05, close_threshold=0.04)
        loud = np.full(100, 0.5, dtype=np.float32)
        quiet = np.zeros(100, dtype=np.float32)
        det.process(loud)
        assert det.is_open is True
        det.process(quiet)
        assert det.is_open is False
        assert det.last_peak_smeter_dbm is None
        assert det.last_avg_smeter_dbm is None


class TestStoredAlongsideTheSignal:

    def test_record_signal_accepts_and_returns_both(self, tmp_path):
        from src.db import Database
        db = Database(str(tmp_path / "s.db"))
        sid = db.create_session("h", "n", [8992.0])
        gid = db.record_signal(session_id=sid, frequency_khz=8992.0, mode="USB",
                               node_host="h", node_name="n", duration_seconds=5.0,
                               peak_rms=0.2, avg_rms=0.05,
                               s_meter_dbm=-82.0, s_meter_avg_dbm=-85.5)
        row = next(r for r in db.get_all_signals(days=1) if r["id"] == gid)
        assert row["s_meter_dbm"] == pytest.approx(-82.0)
        assert row["s_meter_avg_dbm"] == pytest.approx(-85.5)
        db.close()

    def test_old_callers_omitting_the_average_still_work(self, tmp_path):
        from src.db import Database
        db = Database(str(tmp_path / "old.db"))
        sid = db.create_session("h", "n", [8992.0])
        gid = db.record_signal(session_id=sid, frequency_khz=8992.0, mode="USB",
                               node_host="h", node_name="n", duration_seconds=5.0,
                               peak_rms=0.2, avg_rms=0.05, s_meter_dbm=-82.0)
        row = next(r for r in db.get_all_signals(days=1) if r["id"] == gid)
        assert row["s_meter_avg_dbm"] is None
        db.close()
