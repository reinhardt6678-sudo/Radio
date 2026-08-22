"""
test_clean_recordings.py - 清理工具的"别删错"保护

清理判据是"满足一条即为垃圾"，对付噪声够用，但对边缘的真信号太狠:
实测 USB_VOICE 的频谱平坦度能到 0.87、峰均比能到 41 dB，而 HF 传播差的时候
真人声的带内 SNR 完全可能是负的、调制还会被判成 NOISE (2026-08-22 清理前
库里有 43 条 speech_score>=0.8 但 SNR<0 的记录)。这组用例盯的就是这些
"踩了判据但确实是真信号"的录音不会被删掉。
"""

import wave
import numpy as np
import pytest

from clean_recordings import RecordingClassifier


@pytest.fixture
def wav_path(tmp_path):
    """随便一个能被 wave 打开的文件 —— 分析结果由桩提供，内容不重要。"""
    p = tmp_path / "20260822_120000_11175.0kHz_TEST.wav"
    with wave.open(str(p), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(12000)
        wf.writeframes((np.zeros(12000, dtype=np.int16)).tobytes())
    return str(p)


class _StubAnalyzer:
    """把分析结果写死，好逐项摆弄判据。"""

    def __init__(self, **overrides):
        self.result = {
            "duration_seconds": 8.0,
            "rms": 0.05,
            "snr_db": 6.0,
            "spectral_flatness": 0.1,
            "crest_factor_db": 10.0,
            "estimated_modulation": "USB_VOICE",
            "speech_score": 0.0,
        }
        self.result.update(overrides)

    def analyze_file(self, path, mode=None):
        return self.result


def _classify(wav_path, **analysis):
    clf = RecordingClassifier(
        min_duration=0.0, min_snr=0.0,
        analyzer=_StubAnalyzer(**analysis),
    )
    return clf.classify(wav_path)


class TestJunkStillGetsDeleted:
    """保护不能宽到把噪声也保住。"""

    def test_plain_noise_is_junk(self, wav_path):
        r = _classify(wav_path, estimated_modulation="NOISE", snr_db=-5.0)
        assert r["is_junk"] is True
        assert "NOISE_TYPE" in r["reasons"]

    def test_noise_with_low_speech_score_is_junk(self, wav_path):
        r = _classify(wav_path, estimated_modulation="NOISE",
                      snr_db=-3.0, speech_score=0.2)
        assert r["is_junk"] is True

    def test_silent_is_junk(self, wav_path):
        r = _classify(wav_path, estimated_modulation="NOISE",
                      snr_db=-8.0, rms=0.0001)
        assert r["is_junk"] is True
        assert "SILENT" in r["reasons"]


class TestRealSignalsAreProtected:

    def test_speech_survives_negative_snr_and_noise_label(self, wav_path):
        """传播差的真人声: SNR 是负的、调制判成 NOISE，但语音分很高。"""
        r = _classify(wav_path, estimated_modulation="NOISE",
                      snr_db=-1.9, speech_score=1.0)
        assert r["reasons"], "判据该踩的还是踩了"
        assert r["is_junk"] is False
        assert r["protections"]

    def test_voice_survives_flat_spectrum(self, wav_path):
        """USB_VOICE 平坦度 0.87 会踩 NOISE_FLAT，但它是真信号。"""
        r = _classify(wav_path, estimated_modulation="USB_VOICE",
                      snr_db=3.0, spectral_flatness=0.87)
        assert "NOISE_FLAT" in r["reasons"]
        assert r["is_junk"] is False

    def test_voice_survives_high_crest(self, wav_path):
        """短促的 USB_VOICE 峰均比 41 dB 会踩 IMPULSE。"""
        r = _classify(wav_path, estimated_modulation="USB_VOICE",
                      snr_db=3.0, duration_seconds=2.5, crest_factor_db=41.0)
        assert "IMPULSE" in r["reasons"]
        assert r["is_junk"] is False

    def test_digital_modes_are_protected(self, wav_path):
        for mod in ("FSK", "PSK", "CW"):
            r = _classify(wav_path, estimated_modulation=mod,
                          snr_db=3.4, spectral_flatness=0.9)
            assert r["is_junk"] is False, f"{mod} 不该被删"

    def test_real_modulation_below_min_snr_is_not_protected(self, wav_path):
        """保护要有下限: 调制判对了但 SNR 还在底噪以下，就不算真信号。"""
        clf = RecordingClassifier(
            min_duration=0.0, min_snr=0.0,
            analyzer=_StubAnalyzer(estimated_modulation="USB_VOICE",
                                   snr_db=-4.0, speech_score=0.0),
        )
        r = clf.classify(wav_path)
        assert r["is_junk"] is True

    def test_threshold_is_configurable(self, wav_path):
        """把语音分门槛调高，原来保得住的就保不住了。"""
        clf = RecordingClassifier(
            min_duration=0.0, min_snr=0.0, keep_speech_score=0.95,
            analyzer=_StubAnalyzer(estimated_modulation="NOISE",
                                   snr_db=-2.0, speech_score=0.6),
        )
        assert clf.classify(wav_path)["is_junk"] is True
