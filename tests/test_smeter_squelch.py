"""
test_smeter_squelch.py - S-meter 判据 / AGC 配置 / 人声评分测试

这些用例锁住 93 小时实测得出的结论:
  * S-meter 在音频 AGC 之前测量, 所以音频电平被压平时它仍然有效
  * 节点 AGC 必须在连接时设成 0, 并用每个节点单独标定的固定增益
  * 录完之后按人声结构给录音打分, 供排序/过滤
"""

import time

import numpy as np
import pytest

from src.squelch import SquelchDetector, MODE_SMETER, VALID_MODES
from src.kiwi_client import KiwiSDRClient
from src.analyzer import SignalAnalyzer


def _det(**kw):
    d = {"mode": MODE_SMETER, "tail_time": 0.0, "pre_roll_seconds": 0.1,
         "sample_rate": 1000, "smeter_open_margin_db": 14.0,
         "smeter_close_margin_db": 10.0, "smeter_floor_min_samples": 5,
         "smeter_floor_window_seconds": 600.0}
    d.update(kw)
    return SquelchDetector(**d)


def _warm(det, dbm=-110.0, n=40, block=None):
    """喂够样本让 S-meter 底噪稳定下来。"""
    block = np.zeros(100, dtype=np.float32) if block is None else block
    for _ in range(n):
        det._last_smeter_calc = 0.0          # 绕过重算间隔
        det.process(block, dbm)


class TestSmeterMode:
    """S-meter 判据的状态机。"""

    def test_smeter_is_a_valid_mode(self):
        assert MODE_SMETER in VALID_MODES

    def test_no_signal_before_floor_is_known(self):
        """底噪还没测出来之前不判信号 (pre-roll 照常攒着)。"""
        det = _det(smeter_floor_min_samples=100)
        for _ in range(10):
            det.process(np.ones(100, dtype=np.float32), -20.0)
        assert det.warming_up is True
        assert det.is_open is False

    def test_opens_when_smeter_rises_above_floor(self):
        det = _det()
        _warm(det, -110.0)
        assert det.smeter_floor == pytest.approx(-110.0, abs=0.5)
        assert det.effective_smeter_open == pytest.approx(-96.0, abs=0.5)
        det._last_smeter_calc = 0.0
        det.process(np.zeros(100, dtype=np.float32), -90.0)   # 底噪 +20 dB
        assert det.is_open is True

    def test_stays_closed_just_below_margin(self):
        det = _det()
        _warm(det, -110.0)
        det._last_smeter_calc = 0.0
        det.process(np.zeros(100, dtype=np.float32), -97.0)   # 底噪 +13 dB
        assert det.is_open is False

    def test_hysteresis_close_is_below_open(self):
        det = _det()
        _warm(det, -110.0)
        assert det.effective_smeter_close < det.effective_smeter_open

    def test_closes_after_signal_drops(self):
        closed = []
        det = _det(tail_time=0.0)
        det.set_callbacks(on_close=lambda d, p, a: closed.append(d))
        _warm(det, -110.0)
        det._last_smeter_calc = 0.0
        det.process(np.zeros(100, dtype=np.float32), -80.0)
        assert det.is_open is True
        det._last_smeter_calc = 0.0
        det.process(np.zeros(100, dtype=np.float32), -110.0)
        assert det.is_open is False
        assert len(closed) == 1

    def test_loud_flat_audio_does_not_open_when_rf_is_quiet(self):
        """AGC 把音频钉在高电平, 但射频没信号 —— 不能误开。

        复现 v1.3.1 那次"静噪卡开、录了 213 个背靠背文件"的场景:
        任何 RMS 阈值在这里都会一直触发, S-meter 判据不会。
        """
        det = _det()
        loud = np.full(100, 0.5, dtype=np.float32)   # RMS 0.5, 高过任何 RMS 阈值
        _warm(det, -110.0, n=40, block=loud)
        assert det.is_open is False
        assert det.current_rms > 0.4                 # 音频确实很响

    def test_flat_audio_still_detects_signal_via_smeter(self):
        """音频电平一动不动 (被 AGC 压平), 但 S-meter 抬起来了。

        复现 v1.3.2 那次"一整天 0 条"的场景: 电平判据永远不会触发,
        S-meter 判据必须能判出来。
        """
        det = _det()
        flat = np.full(100, 0.1, dtype=np.float32)
        _warm(det, -115.0, n=40, block=flat)
        assert det.is_open is False
        det._last_smeter_calc = 0.0
        det.process(flat, -95.0)                     # 电平一模一样, 只有射频变了
        assert det.is_open is True

    def test_threshold_follows_a_rising_noise_floor(self):
        """底噪整体抬高时阈值跟着走, 不会从此一直误触发。"""
        det = _det()
        _warm(det, -120.0, n=40)
        low = det.effective_smeter_open
        # 底噪取窗口内第 10 百分位, 所以要喂够新样本把旧的挤出低位区
        _warm(det, -100.0, n=500)
        assert det.effective_smeter_open > low

    def test_from_config_defaults_to_smeter(self):
        det = SquelchDetector.from_config({"squelch": {}})
        assert det.mode == MODE_SMETER
        assert det.smeter_open_margin_db == 14.0
        assert det.smeter_close_margin_db == 10.0

    def test_from_config_reads_margins(self):
        det = SquelchDetector.from_config(
            {"squelch": {"mode": "smeter", "smeter_open_margin_db": 18.0,
                         "smeter_close_margin_db": 12.0}})
        assert det.smeter_open_margin_db == 18.0
        assert det.smeter_close_margin_db == 12.0

    def test_status_exposes_smeter_fields(self):
        det = _det()
        _warm(det, -110.0)
        st = det.get_status()
        for key in ("smeter_dbm", "smeter_floor", "smeter_open", "smeter_close"):
            assert key in st
        assert st["smeter_floor"] == pytest.approx(-110.0, abs=0.5)

    def test_process_without_smeter_never_opens_in_smeter_mode(self):
        """没传 S-meter 时不判信号, 但也不能崩。"""
        det = _det()
        for _ in range(20):
            det.process(np.ones(100, dtype=np.float32))
        assert det.is_open is False

    def test_max_open_forces_close_in_smeter_mode(self):
        closed = []
        det = _det(max_open_seconds=0.001, tail_time=99.0)
        det.set_callbacks(on_close=lambda d, p, a: closed.append(d))
        _warm(det, -110.0)
        det._last_smeter_calc = 0.0
        det.process(np.zeros(100, dtype=np.float32), -80.0)
        assert det.is_open is True
        time.sleep(0.01)
        det._last_smeter_calc = 0.0
        det.process(np.zeros(100, dtype=np.float32), -80.0)
        assert det.is_open is False
        assert len(closed) == 1


class TestAgcConfig:
    """AGC 只能在连接时设定, 且每个节点用自己标定的增益。"""

    def test_default_is_agc_off(self):
        c = KiwiSDRClient("h", 8073)
        assert c.agc is False
        assert "agc=0" in c.agc_command()

    def test_agc_command_uses_man_gain(self):
        c = KiwiSDRClient("h", 8073, agc=False, man_gain=82)
        assert "manGain=82" in c.agc_command()
        assert "agc=0" in c.agc_command()

    def test_agc_on_command(self):
        c = KiwiSDRClient("h", 8073, agc=True)
        assert "agc=1" in c.agc_command()

    def test_from_config_prefers_node_gain(self):
        cfg = {"receiver": {"agc": False, "man_gain": 70},
               "nodes": [{"host": "a.example", "man_gain": 86}]}
        c = KiwiSDRClient.from_config("a.example", 8073, cfg)
        assert c.man_gain == 86
        assert c.agc is False

    def test_from_config_falls_back_to_receiver_gain(self):
        cfg = {"receiver": {"agc": False, "man_gain": 70},
               "nodes": [{"host": "other.example", "man_gain": 86}]}
        c = KiwiSDRClient.from_config("a.example", 8073, cfg)
        assert c.man_gain == 70

    def test_from_config_honours_agc_true(self):
        c = KiwiSDRClient.from_config("a.example", 8073,
                                      {"receiver": {"agc": True}})
        assert c.agc is True

    def test_from_config_tolerates_empty_config(self):
        c = KiwiSDRClient.from_config("a.example", 8073, {})
        assert c.agc is False
        assert c.man_gain == 70


class TestSpeechScore:
    """录完之后的人声结构评分。"""

    def _analyzer(self):
        return SignalAnalyzer(fft_size=4096, sample_rate=12000)

    def _speechlike(self, sr=12000, dur=5.0):
        """低频为主 + 3 Hz 音节包络, 模拟 USB 语音。"""
        t = np.arange(int(sr * dur)) / sr
        carrier = sum(np.sin(2 * np.pi * f * t) / (i + 1)
                      for i, f in enumerate([400, 700, 1100]))
        env = 0.5 + 0.5 * np.sin(2 * np.pi * 3.0 * t)
        return (carrier * env * 0.2).astype(np.float64)

    def test_speech_scores_higher_than_noise(self):
        an = self._analyzer()
        rng = np.random.default_rng(0)
        noise = rng.normal(0, 0.05, 12000 * 5)
        sp = an.analyze_samples(self._speechlike(), 12000, mode="USB")
        ns = an.analyze_samples(noise, 12000, mode="USB")
        assert sp["speech_score"] > ns["speech_score"]

    def test_syllabic_ratio_is_high_for_syllabic_envelope(self):
        an = self._analyzer()
        r = an.analyze_samples(self._speechlike(), 12000, mode="USB")
        assert r["syllabic_ratio"] > 0.30

    def test_fields_present_and_in_range(self):
        an = self._analyzer()
        r = an.analyze_samples(self._speechlike(), 12000, mode="USB")
        for key in ("syllabic_ratio", "passband_tilt_db",
                    "speech_score", "is_speech"):
            assert key in r
        assert 0.0 <= r["speech_score"] <= 1.0

    def test_short_input_does_not_crash(self):
        an = self._analyzer()
        r = an.analyze_samples(np.zeros(10), 12000, mode="USB")
        assert r["speech_score"] == 0.0
        assert r["is_speech"] is False


class TestSpeechScorePersisted:
    """评分要落库, 这样界面上才能按可信度排序。"""

    def test_speech_score_round_trips(self, tmp_path):
        from src.db import Database
        db = Database(str(tmp_path / "t.db"))
        sid = db.record_signal(session_id=None, frequency_khz=11175.0,
                               mode="USB", node_host="h", node_name="n",
                               duration_seconds=5.0, peak_rms=0.1, avg_rms=0.05)
        db.save_analysis(signal_id=sid, syllabic_ratio=0.42,
                         passband_tilt_db=3.1, speech_score=0.77)
        got = db.get_analysis_by_signal(sid)
        assert got["speech_score"] == pytest.approx(0.77)
        assert got["syllabic_ratio"] == pytest.approx(0.42)
        assert got["passband_tilt_db"] == pytest.approx(3.1)
