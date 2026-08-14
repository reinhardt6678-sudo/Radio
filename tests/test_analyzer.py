"""
test_analyzer.py - 信号分析器单元测试

测试 SignalAnalyzer 的频谱分析、调制识别和 analyze_and_save 统一方法。
"""

import numpy as np
import pytest

from src.analyzer import SignalAnalyzer


class TestSignalAnalyzer:
    """SignalAnalyzer 核心功能测试。"""

    @pytest.fixture
    def analyzer(self):
        """创建默认分析器。"""
        return SignalAnalyzer(
            fft_size=1024,
            sample_rate=12000,
            window_type="hann",
            bandwidth_threshold_db=20,
        )

    def test_analyze_pure_tone(self, analyzer):
        """分析纯正弦波应检测到正确的峰值频率。"""
        sr = 12000
        duration = 2.0
        freq_hz = 1500
        t = np.arange(int(sr * duration)) / sr
        samples = (0.5 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)

        result = analyzer.analyze_samples(samples, sr)

        assert result is not None
        assert "peak_frequency_hz" in result
        # 峰值频率应接近 1500 Hz（允许 FFT 分辨率误差）
        assert abs(result["peak_frequency_hz"] - freq_hz) < 50
        assert result["snr_db"] > 0

    def test_analyze_noise(self, analyzer):
        """分析纯噪声应返回低 SNR。"""
        sr = 12000
        np.random.seed(42)
        samples = (np.random.randn(sr * 2) * 0.01).astype(np.float32)

        result = analyzer.analyze_samples(samples, sr)

        assert result is not None
        assert result["snr_db"] < 10  # 噪声的 SNR 应该很低

    def test_analyze_too_short(self, analyzer):
        """样本不足 1 秒应返回 None 或空结果。"""
        sr = 12000
        samples = np.zeros(100, dtype=np.float32)  # 远少于 1 秒
        result = analyzer.analyze_samples(samples, sr)
        # 短样本可能返回 None 或包含默认值的 dict
        # 只要不崩溃即可

    def test_from_config_factory(self):
        """from_config 应正确读取配置。"""
        config = {
            "analysis": {
                "fft_size": 2048,
                "window_type": "hamming",
                "bandwidth_threshold_db": 15,
            },
            "receiver": {"sample_rate": 8000},
        }
        a = SignalAnalyzer.from_config(config)
        assert a.fft_size == 2048
        assert a.window_type == "hamming"
        assert a.sample_rate == 8000

    def test_analyze_and_save_with_buffer(self, analyzer, tmp_path):
        """analyze_and_save 应完整走通分析+保存流程。"""
        from src.db import Database

        db_path = str(tmp_path / "test_analysis.db")
        db = Database(db_path)

        try:
            session_id = db.create_session(
                node_host="localhost:8073",
                node_name="t1",
                frequencies=[8992.0],
            )
            signal_id = db.record_signal(
                session_id=session_id,
                frequency_khz=8992.0,
                mode="USB",
                node_host="localhost:8073",
                node_name="t1",
                duration_seconds=3.0,
                peak_rms=0.2,
                avg_rms=0.08,
            )

            # 生成测试音频 buffer
            sr = 12000
            t = np.arange(sr * 2) / sr
            chunk1 = (0.3 * np.sin(2 * np.pi * 1200 * t)).astype(np.float32)
            chunk2 = (0.3 * np.sin(2 * np.pi * 1200 * t)).astype(np.float32)
            buffer = [chunk1, chunk2]

            analyzer.analyze_and_save(db, signal_id, buffer, sr)

            # 验证分析结果已保存
            stats = db.get_modulation_stats(days=1)
            assert len(stats) >= 1
        finally:
            db.close()

    def test_analyze_and_save_empty_buffer(self, analyzer, tmp_path):
        """空 buffer 调用 analyze_and_save 不应崩溃。"""
        from src.db import Database

        db_path = str(tmp_path / "test_empty.db")
        db = Database(db_path)

        try:
            session_id = db.create_session(
                node_host="localhost:8073",
                node_name="t1",
                frequencies=[5000.0],
            )
            signal_id = db.record_signal(
                session_id=session_id,
                frequency_khz=5000.0,
                mode="AM",
                node_host="localhost:8073",
                node_name="t1",
                duration_seconds=1.0,
                peak_rms=0.1,
                avg_rms=0.05,
            )

            # 空 buffer 和 None 都不应崩溃
            analyzer.analyze_and_save(db, signal_id, [], 12000)
            analyzer.analyze_and_save(db, signal_id, None, 12000)
        finally:
            db.close()


# ==================== 合成信号工具 ====================

SR = 12000


def _band_noise(n, level=0.02, seed=1, lo=300, hi=3000):
    """模拟接收机滤波之后的底噪: 通带内是噪声，阻带几乎为空。"""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    spectrum = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, 1 / SR)
    spectrum[(freqs < lo) | (freqs > hi)] *= 0.02
    y = np.fft.irfft(spectrum, n)
    return y / np.sqrt(np.mean(y ** 2)) * level


def _voice_like(n, level=0.35, syllable_hz=4.0, seed=2):
    """音节包络 + 谐波簇，模拟语音。"""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SR
    tone = np.zeros(n)
    for k, f0 in enumerate([320, 640, 960, 1400, 1900, 2400], 1):
        tone += (1.0 / k) * np.sin(2 * np.pi * f0 * t + rng.random() * 6)
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * syllable_hz * t)
    return tone / np.max(np.abs(tone)) * envelope * level


def _keyed_cw(n, level=0.3, tone_hz=700, dot=0.06):
    """莫尔斯键控的单音。"""
    t = np.arange(n) / SR
    keying = ((t % (2 * dot)) < dot).astype(float)
    smooth = np.hanning(120) / np.sum(np.hanning(120))
    keying = np.convolve(keying, smooth, mode="same")
    return level * keying * np.sin(2 * np.pi * tone_hz * t)


def _fsk(n, level=0.3, center=1615, shift=170, baud=45.0, seed=3):
    """双音 FSK (恒包络)。"""
    rng = np.random.default_rng(seed)
    symbols = (rng.random(int(n / SR * baud) + 2) > 0.5).astype(float)
    stream = np.repeat(symbols, int(SR / baud))[:n]
    if len(stream) < n:
        stream = np.pad(stream, (0, n - len(stream)))
    instantaneous = center + shift * stream
    return level * np.sin(2 * np.pi * np.cumsum(instantaneous) / SR)


class TestInBandSNR:
    """缺陷 03: SNR 必须在解调通带内估计，不能拿滤波器阻带当噪声基准。"""

    @pytest.fixture
    def analyzer(self):
        return SignalAnalyzer(fft_size=4096, sample_rate=SR)

    def test_clean_voice_has_high_snr(self, analyzer):
        """清晰的语音带内 SNR 应当在 20 dB 以上。"""
        n = SR * 6
        samples = _voice_like(n) + _band_noise(n, 0.01)
        result = analyzer.analyze_samples(samples, SR, mode="USB")

        assert result["snr_db"] > 20, (
            f"带内 SNR 只有 {result['snr_db']:.1f} dB —— "
            "审计里 744 条记录只有 6 条超过 10 dB 就是这个毛病"
        )

    def test_filtered_noise_is_not_mistaken_for_signal(self, analyzer):
        """
        只有底噪的录音不能因为阻带是空的就报出高 SNR。

        旧实现把 3000-6000 Hz 的滤波器阻带算进噪声区，分母被压低，
        纯底噪也能报出正的 SNR。
        """
        n = SR * 6
        result = analyzer.analyze_samples(_band_noise(n, 0.05), SR, mode="USB")

        assert result["snr_db"] < 3
        assert result["estimated_modulation"] == "NOISE"

    def test_stopband_does_not_shift_snr(self, analyzer):
        """把阻带填上噪声不应显著改变带内 SNR (说明它没参与计算)。"""
        n = SR * 6
        signal = _voice_like(n) + _band_noise(n, 0.01)
        # 同样的信号，只是阻带里多了能量
        polluted = signal + _band_noise(n, 0.01, seed=9, lo=3500, hi=5800)

        snr_a = analyzer.analyze_samples(signal, SR, mode="USB")["snr_db"]
        snr_b = analyzer.analyze_samples(polluted, SR, mode="USB")["snr_db"]

        assert abs(snr_a - snr_b) < 2.0

    def test_noise_floor_reported(self, analyzer):
        """噪声基底应当作为独立读数给出。"""
        n = SR * 4
        result = analyzer.analyze_samples(_band_noise(n, 0.05), SR, mode="USB")

        assert result["noise_floor_db"] < 0
        assert result["passband_hz"] == [300.0, 3000.0]

    def test_passband_follows_mode(self, analyzer):
        """CW 模式的通带更窄，落在通带外的音调不应被当成信号。"""
        n = SR * 4
        t = np.arange(n) / SR
        # 2500 Hz 在 USB 通带内，但在 CW 通带 (300-1000 Hz) 之外
        samples = 0.3 * np.sin(2 * np.pi * 2500 * t) + _band_noise(n, 0.01)

        usb = analyzer.analyze_samples(samples, SR, mode="USB")
        cw = analyzer.analyze_samples(samples, SR, mode="CW")

        assert usb["snr_db"] > 20
        assert cw["snr_db"] < usb["snr_db"] - 10


class TestModulationClassification:
    """缺陷 02: 判定必须由信号特征决定，而不是 MODULATION_PROFILES 的字典顺序。"""

    @pytest.fixture
    def analyzer(self):
        return SignalAnalyzer(fft_size=4096, sample_rate=SR)

    def test_voice_classified_as_voice(self, analyzer):
        n = SR * 6
        result = analyzer.analyze_samples(
            _voice_like(n) + _band_noise(n, 0.01), SR, mode="USB")

        assert result["estimated_modulation"] == "USB_VOICE"
        assert result["modulation_confidence"] > 0.35

    def test_cw_classified_as_cw(self, analyzer):
        n = SR * 6
        result = analyzer.analyze_samples(
            _keyed_cw(n) + _band_noise(n, 0.01, lo=300, hi=1000), SR, mode="CW")

        assert result["estimated_modulation"] == "CW"

    def test_fsk_classified_as_fsk(self, analyzer):
        n = SR * 6
        result = analyzer.analyze_samples(
            _fsk(n) + _band_noise(n, 0.01), SR, mode="USB")

        assert result["estimated_modulation"] == "FSK"

    def test_steady_carrier_is_not_voice(self, analyzer):
        """未调制载波不该被算成语音 —— 旧打分器 93% 的记录都是 USB_VOICE。"""
        n = SR * 6
        t = np.arange(n) / SR
        samples = 0.3 * np.sin(2 * np.pi * 1000 * t) + _band_noise(n, 0.01)
        result = analyzer.analyze_samples(samples, SR, mode="USB")

        assert result["estimated_modulation"] == "CARRIER"

    def test_bandwidth_discriminates(self, analyzer):
        """
        占用带宽必须能区分信号，不能全库都是同一个数。

        审计: 三个"不同"调制类型的平均带宽只差 25 Hz (<1%)，
        因为量的其实是接收机 300-3000 Hz 的固定通带。
        """
        n = SR * 6
        t = np.arange(n) / SR
        tone = 0.3 * np.sin(2 * np.pi * 1000 * t) + _band_noise(n, 0.01)
        voice = _voice_like(n) + _band_noise(n, 0.01)

        bw_tone = analyzer.analyze_samples(tone, SR, mode="USB")["bandwidth_hz"]
        bw_voice = analyzer.analyze_samples(voice, SR, mode="USB")["bandwidth_hz"]

        assert bw_tone < 100, "纯音的占用带宽应当只有几十 Hz"
        assert bw_voice > 500, "语音的占用带宽应当接近整个话音带"
        assert bw_voice > bw_tone * 5

    def test_mode_decides_voice_label(self, analyzer):
        """USB 还是 AM 由接收机的解调模式决定，不该从音频里猜。"""
        n = SR * 6
        samples = _voice_like(n) + _band_noise(n, 0.01)

        assert analyzer.analyze_samples(
            samples, SR, mode="USB")["estimated_modulation"] == "USB_VOICE"
        assert analyzer.analyze_samples(
            samples, SR, mode="AM")["estimated_modulation"] == "AM_VOICE"

    def test_scores_are_not_tied(self, analyzer):
        """每个类别都应拿到互不相同的分数，不存在"并列取第一个键"。"""
        n = SR * 6
        result = analyzer.analyze_samples(
            _voice_like(n) + _band_noise(n, 0.01), SR, mode="USB")

        scores = result["modulation_scores"]
        assert len(scores) >= 4
        top_two = sorted(scores.values(), reverse=True)[:2]
        assert top_two[0] - top_two[1] > 0.05

    def test_ties_report_unknown(self, analyzer):
        """真的并列时应当输出 UNKNOWN，而不是悄悄取字典里的第一个键。"""
        # 构造一组让多个类别打平的特征
        features = {
            "snr_db": 10.0,
            "bandwidth_hz": 700.0,
            "crest_factor_db": 9.0,
            "band_flatness": 0.3,
            "tone_count": 2,
            "tone_spacing_hz": 300.0,
            "tone_purity": 0.1,
            "envelope_rate_hz": 5.0,
            "envelope_depth": 0.12,
            "keying_rate_hz": 5.0,
            "envelope_sample_rate": 200.0,
        }
        label, confidence, scores = analyzer._classify_modulation(features, "USB")

        top_two = sorted(scores.values(), reverse=True)[:2]
        if top_two[0] - top_two[1] < 0.05:
            assert label == "UNKNOWN"
        assert 0.0 <= confidence <= 1.0

    def test_tone_count_separates_tonal_from_continuous(self, analyzer):
        """
        音调数要能区分"几个离散音调"和"连续谱"。

        上限压得太低时，2400 Bd 的 PSK 会和 FSK 一样顶到上限，
        这个特征就等于没有。
        """
        n = SR * 6
        t = np.arange(n) / SR

        fsk = analyzer.analyze_samples(_fsk(n) + _band_noise(n, 0.01), SR)

        rng = np.random.default_rng(4)
        bits = (rng.random(int(6 * 2400)) > 0.5).astype(float) * 2 - 1
        spb = int(SR / 2400)
        shaped = np.repeat(bits, spb)[:n]
        shaped = np.pad(shaped, (0, max(0, n - len(shaped))))
        taper = np.hanning(spb * 4) / np.sum(np.hanning(spb * 4))
        psk_wave = 0.3 * np.convolve(shaped, taper, mode="same") * np.sin(2 * np.pi * 1800 * t)
        psk = analyzer.analyze_samples(psk_wave + _band_noise(n, 0.01), SR)

        assert fsk["tone_count"] <= 8
        assert psk["tone_count"] > fsk["tone_count"]
        assert psk["estimated_modulation"] == "PSK"

    def test_short_sample_gets_low_confidence(self, analyzer):
        """样本太短拿不到包络特征时不许给高置信度。"""
        n = SR // 20  # 50 ms
        t = np.arange(n) / SR
        result = analyzer.analyze_samples(
            0.3 * np.sin(2 * np.pi * 1000 * t), SR, mode="USB")

        assert result["modulation_confidence"] <= analyzer.min_confidence

    def test_confidence_persisted(self, analyzer, tmp_path):
        """置信度等新特征应当落库。"""
        from src.db import Database

        db = Database(str(tmp_path / "conf.db"))
        try:
            session_id = db.create_session("h", "n", [11175.0])
            signal_id = db.record_signal(
                session_id=session_id, frequency_khz=11175.0, mode="USB",
                node_host="h", node_name="n", duration_seconds=6.0,
                peak_rms=0.3, avg_rms=0.2,
            )
            n = SR * 6
            samples = (_voice_like(n) + _band_noise(n, 0.01)).astype(np.float32)
            analyzer.analyze_and_save(db, signal_id, [samples], SR, mode="USB")

            row = db.get_signal_with_analysis(signal_id)
            assert row["estimated_modulation"] == "USB_VOICE"
            assert row["modulation_confidence"] > 0.35
            assert row["demod_mode"] == "USB"
            assert row["noise_floor_db"] is not None
            assert row["envelope_rate_hz"] > 0
        finally:
            db.close()
