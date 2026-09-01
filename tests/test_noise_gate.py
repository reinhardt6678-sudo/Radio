"""
test_noise_gate.py - 入库噪声闸门单元测试

The classifier already labelled these segments correctly; it just ran after
db.record_signal() so nothing acted on the verdict. These tests pin down the gate
that now runs first: what it drops, what it must never drop, and the fact that a
dropped segment leaves neither a database row nor an orphan WAV.
分类器本来就把这些段判对了，只是它跑在 db.record_signal() 之后，判定没人用。
这些用例钉住现在跑在前面的那道闸门: 丢什么、绝不能丢什么，
以及被丢掉的段既不留数据库记录、也不留孤儿 WAV。

实测依据 (2026-09-01, 4724 kHz): 一小时 274 条全判 NOISE, SNR -9.2 ~ +2.3 dB;
同期 4 条真通联 SNR +4.5 ~ +17.7 dB。
"""

import os

import numpy as np
import pytest

from src.analyzer import SignalAnalyzer
from src.recorder import discard_recording


def _analysis(modulation="NOISE", confidence=0.95, snr_db=-6.0):
    """构造一份最小的分析结果字典。"""
    return {
        "estimated_modulation": modulation,
        "modulation_confidence": confidence,
        "snr_db": snr_db,
    }


class TestNoiseDiscardReason:
    """noise_discard_reason() 的判据边界。"""

    @pytest.fixture
    def analyzer(self):
        return SignalAnalyzer(fft_size=1024, sample_rate=12000)

    def test_discards_confident_noise(self, analyzer):
        """判为 NOISE、置信度够、SNR 够低 —— 三条都满足才丢。"""
        reason = analyzer.noise_discard_reason(_analysis())
        assert reason is not None
        assert "NOISE" in reason

    @staticmethod
    def _noise_confidence(snr_db, threshold_db=3.0):
        """复刻 _classify_modulation() 的 NOISE 分支置信度公式。"""
        return min(0.99, max(0.5, 0.5 + (threshold_db - snr_db) / 12.0))

    @pytest.mark.parametrize("snr_db", [-9.2, -5.0, -1.0])
    def test_discards_measured_noise_range(self, analyzer, snr_db):
        """实测噪声突发的 SNR 区间应当被拦下。"""
        assert analyzer.noise_discard_reason(
            _analysis(confidence=self._noise_confidence(snr_db),
                      snr_db=snr_db)) is not None

    @pytest.mark.parametrize("snr_db,discarded", [
        (-0.7, True),    # conf 0.808 >= 0.8
        (-0.6, True),    # conf 0.800 == 0.8, 判据是 >=
        (-0.5, False),   # conf 0.792 < 0.8 —— 置信度先卡住
        (-0.1, False),
    ])
    def test_confidence_gate_binds_before_snr_gate(self, analyzer,
                                                   snr_db, discarded):
        """
        真正的切点是 SNR <= -0.6 dB，不是配置里那个 0 dB。

        NOISE 分支的置信度完全由 SNR 推出，所以两道判据里更严的是置信度那道:
        conf >= 0.8 等价于 snr <= -0.6。这条用例把这个事实钉死 ——
        改动任一阈值时，这里会先响。
        """
        reason = analyzer.noise_discard_reason(
            _analysis(confidence=self._noise_confidence(snr_db), snr_db=snr_db))
        assert (reason is not None) is discarded

    @pytest.mark.parametrize("snr_db", [4.5, 8.0, 17.7])
    def test_keeps_real_signal_snr_range(self, analyzer, snr_db):
        """实测真通联的 SNR 区间一条都不许丢。"""
        assert analyzer.noise_discard_reason(
            _analysis(modulation="USB_VOICE", confidence=0.9,
                      snr_db=snr_db)) is None

    def test_keeps_non_noise_label(self, analyzer):
        """标签不是 NOISE 就不丢，哪怕 SNR 很低。"""
        assert analyzer.noise_discard_reason(
            _analysis(modulation="UNKNOWN", snr_db=-20.0)) is None

    def test_keeps_low_confidence(self, analyzer):
        """置信度不到门限就留着 —— 拿不准的一律保留。"""
        assert analyzer.noise_discard_reason(
            _analysis(confidence=0.6, snr_db=-6.0)) is None

    def test_keeps_snr_at_threshold(self, analyzer):
        """SNR 恰好等于阈值不丢 (判据是严格小于)。"""
        assert analyzer.noise_discard_reason(
            _analysis(confidence=0.99, snr_db=0.0)) is None

    def test_keeps_unanalysable(self, analyzer):
        """分析不出来 (None) 时必须保留，不能当成噪声。"""
        assert analyzer.noise_discard_reason(None) is None

    def test_keeps_missing_snr(self, analyzer):
        """缺 snr_db 字段时保留。"""
        assert analyzer.noise_discard_reason(
            {"estimated_modulation": "NOISE",
             "modulation_confidence": 0.99}) is None

    def test_disabled_keeps_everything(self):
        """discard_noise: false 时闸门整体关闭。"""
        analyzer = SignalAnalyzer(fft_size=1024, sample_rate=12000,
                                  discard_noise=False)
        assert analyzer.noise_discard_reason(_analysis()) is None

    def test_from_config_reads_thresholds(self):
        """from_config 读得到三个新键。"""
        analyzer = SignalAnalyzer.from_config({
            "analysis": {
                "discard_noise": False,
                "discard_noise_min_confidence": 0.7,
                "discard_noise_max_snr_db": -2.0,
            }
        })
        assert analyzer.discard_noise is False
        assert analyzer.discard_noise_min_confidence == 0.7
        assert analyzer.discard_noise_max_snr_db == -2.0

    def test_from_config_defaults_on(self):
        """配置里什么都不写时闸门默认开着。"""
        analyzer = SignalAnalyzer.from_config({})
        assert analyzer.discard_noise is True
        assert analyzer.discard_noise_min_confidence == 0.8
        assert analyzer.discard_noise_max_snr_db == 0.0


class TestGateOnRealAudio:
    """端到端: 真正跑一遍分析器，而不是喂构造好的字典。"""

    @pytest.fixture
    def analyzer(self):
        return SignalAnalyzer(fft_size=1024, sample_rate=12000)

    def test_wideband_noise_is_discarded(self, analyzer):
        """整个通带铺满的白噪声 —— 就是那 274 条的形状。"""
        sr = 12000
        np.random.seed(42)
        samples = (0.05 * np.random.randn(sr * 3)).astype(np.float32)

        analysis = analyzer.analyze_buffer([samples], sr, mode="USB")
        assert analysis["estimated_modulation"] == "NOISE"
        assert analyzer.noise_discard_reason(analysis) is not None

    def test_clean_tone_is_kept(self, analyzer):
        """通带内的干净单音必须留下。"""
        sr = 12000
        t = np.arange(sr * 3) / sr
        samples = (0.5 * np.sin(2 * np.pi * 1500 * t)).astype(np.float32)

        analysis = analyzer.analyze_buffer([samples], sr, mode="USB")
        assert analysis["snr_db"] > 0
        assert analyzer.noise_discard_reason(analysis) is None

    def test_short_buffer_yields_none_and_is_kept(self, analyzer):
        """不足 1 秒无法分析，闸门必须放行而不是丢弃。"""
        sr = 12000
        samples = np.zeros(sr // 2, dtype=np.float32)

        analysis = analyzer.analyze_buffer([samples], sr, mode="USB")
        assert analysis is None
        assert analyzer.noise_discard_reason(analysis) is None

    def test_empty_buffer_yields_none(self, analyzer):
        """空缓冲区返回 None。"""
        assert analyzer.analyze_buffer([], 12000) is None


class TestDiscardRecording:
    """discard_recording() 的文件处理。"""

    def test_deletes_the_file(self, tmp_path):
        wav = tmp_path / "seg.wav"
        wav.write_bytes(b"RIFF____WAVE")

        assert discard_recording({"path": str(wav)}) is True
        assert not wav.exists()

    def test_none_rec_info(self):
        """当时没在录音 (rec_info 为 None) 不算失败。"""
        assert discard_recording(None) is False

    def test_missing_path(self, tmp_path):
        """文件已经不在了也不报错。"""
        assert discard_recording(
            {"path": str(tmp_path / "gone.wav")}) is False


class TestReceiverGateWiring:
    """
    闸门在 SignalReceiver 里的接线。

    上面几组测的是判据本身，这组测的是"判据的结论真的被用上了":
    计数、删文件、以及告诉调用方别再往下走。
    """

    @pytest.fixture
    def rx(self, tmp_path):
        from src.db import Database
        from src.receiver import SignalReceiver

        db = Database(str(tmp_path / "gate.db"))
        rx = SignalReceiver({
            "nodes": [],
            "receiver": {"sample_rate": 12000},
            "recording": {"output_dir": str(tmp_path / "rec")},
            "squelch": {},
            "analysis": {},
        }, db)
        yield rx
        db.close()

    def test_rejects_and_deletes(self, rx, tmp_path):
        wav = tmp_path / "noise.wav"
        wav.write_bytes(b"RIFF____WAVE")

        rejected = rx._reject_as_noise(_analysis(), {"path": str(wav)}, 4724.0)

        assert rejected is True
        assert not wav.exists()
        assert rx._discarded_signals == 1
        assert rx._total_signals == 0

    def test_keeps_real_signal(self, rx, tmp_path):
        wav = tmp_path / "voice.wav"
        wav.write_bytes(b"RIFF____WAVE")

        rejected = rx._reject_as_noise(
            _analysis(modulation="USB_VOICE", confidence=0.9, snr_db=12.0),
            {"path": str(wav)}, 4724.0)

        assert rejected is False
        assert wav.exists()
        assert rx._discarded_signals == 0

    def test_unanalysable_is_kept(self, rx):
        """分析不出来时不许拦 —— 拿不准的一律放行。"""
        assert rx._reject_as_noise(None, None, 4724.0) is False
        assert rx._discarded_signals == 0

    def test_status_message_is_bilingual(self, rx, tmp_path):
        """告警行要中英双语，两种语言的关键词都得在。"""
        wav = tmp_path / "noise.wav"
        wav.write_bytes(b"RIFF____WAVE")
        seen = []
        rx._status_callback = seen.append

        rx._reject_as_noise(_analysis(), {"path": str(wav)}, 4724.0)

        assert len(seen) == 1
        assert "[NOISE-DISCARD]" in seen[0]
        assert "not filed" in seen[0]
        assert "recording deleted" in seen[0]
        assert "判为噪声，未入库" in seen[0]
        assert "录音已删除" in seen[0]
        assert "4724.0 kHz" in seen[0]


class TestAnalyzeAndSaveStillWorks:
    """拆分之后 analyze_and_save 的行为不能变 —— 它还有别的调用方。"""

    class _FakeDB:
        def __init__(self):
            self.saved = []

        def save_analysis(self, **kwargs):
            self.saved.append(kwargs)

    def test_composes_analyze_and_save(self):
        sr = 12000
        t = np.arange(sr * 2) / sr
        samples = (0.5 * np.sin(2 * np.pi * 1500 * t)).astype(np.float32)

        analyzer = SignalAnalyzer(fft_size=1024, sample_rate=sr)
        db = self._FakeDB()
        result = analyzer.analyze_and_save(db, 7, [samples], sr, mode="USB")

        assert result is not None
        assert len(db.saved) == 1
        assert db.saved[0]["signal_id"] == 7

    def test_short_buffer_saves_nothing(self):
        analyzer = SignalAnalyzer(fft_size=1024, sample_rate=12000)
        db = self._FakeDB()

        assert analyzer.analyze_and_save(
            db, 7, [np.zeros(100, dtype=np.float32)], 12000) is None
        assert db.saved == []
