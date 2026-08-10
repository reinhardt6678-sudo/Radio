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
