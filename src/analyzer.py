"""
analyzer.py - 信号元数据分析模块

对录制的音频进行频域和时域分析，提取信号特征，
包括带宽估算、SNR 计算、调制类型初步识别等。
"""

import os
import wave
import logging
import numpy as np
from typing import Dict, Optional, List, Tuple

try:
    from scipy import signal as scipy_signal
    from scipy.fft import fft, fftfreq
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

logger = logging.getLogger(__name__)


class SignalAnalyzer:
    """
    信号分析器。

    对音频文件或实时音频数据执行频谱分析、特征提取和调制类型识别。
    """

    # 常见调制类型的频谱特征
    MODULATION_PROFILES = {
        "USB_VOICE": {
            "description": "上边带语音",
            "bandwidth_range": (200, 3500),
            "flatness_range": (0.01, 0.3),
            "crest_range": (6, 20),
        },
        "AM_VOICE": {
            "description": "调幅语音",
            "bandwidth_range": (200, 9000),
            "flatness_range": (0.01, 0.3),
            "crest_range": (6, 20),
        },
        "CW": {
            "description": "连续波/莫尔斯电码",
            "bandwidth_range": (10, 500),
            "flatness_range": (0.0001, 0.01),
            "crest_range": (3, 8),
        },
        "FSK": {
            "description": "频移键控 (STANAG 等)",
            "bandwidth_range": (100, 3000),
            "flatness_range": (0.001, 0.05),
            "crest_range": (2, 6),
        },
        "PSK": {
            "description": "相移键控",
            "bandwidth_range": (100, 3000),
            "flatness_range": (0.05, 0.5),
            "crest_range": (3, 8),
        },
        "NOISE": {
            "description": "噪声/无信号",
            "bandwidth_range": (3000, 20000),
            "flatness_range": (0.3, 1.0),
            "crest_range": (1, 4),
        },
    }

    def __init__(self, fft_size: int = 4096, window_type: str = "hann",
                 bandwidth_threshold_db: float = 20,
                 sample_rate: int = 12000):
        """
        初始化分析器。

        Args:
            fft_size: FFT 点数
            window_type: 窗函数类型 (hann/hamming/blackman)
            bandwidth_threshold_db: 带宽估算阈值 (低于峰值 N dB)
            sample_rate: 默认采样率
        """
        self.fft_size = fft_size
        self.window_type = window_type
        self.bandwidth_threshold_db = bandwidth_threshold_db
        self.sample_rate = sample_rate

    @classmethod
    def from_config(cls, config: dict) -> "SignalAnalyzer":
        """从配置字典创建分析器。"""
        ana_cfg = config.get("analysis", {})
        recv_cfg = config.get("receiver", {})
        return cls(
            fft_size=ana_cfg.get("fft_size", 4096),
            window_type=ana_cfg.get("window_type", "hann"),
            bandwidth_threshold_db=ana_cfg.get("bandwidth_threshold_db", 20),
            sample_rate=recv_cfg.get("sample_rate", 12000),
        )

    def analyze_and_save(self, db, signal_id: int,
                         audio_buffer: list, sample_rate: int = None):
        """
        分析音频缓冲区并保存结果到数据库 (消除重复代码)。

        Args:
            db: Database 实例
            signal_id: 信号 ID
            audio_buffer: 音频块列表 [np.ndarray, ...]
            sample_rate: 采样率，默认使用 self.sample_rate
        """
        sr = sample_rate or self.sample_rate
        if not audio_buffer:
            return None
        import numpy as np
        all_samples = np.concatenate(audio_buffer)
        if len(all_samples) < sr:  # 至少 1 秒
            return None
        analysis = self.analyze_samples(all_samples, sr)
        db.save_analysis(
            signal_id=signal_id,
            peak_frequency_hz=analysis.get("peak_frequency_hz"),
            bandwidth_hz=analysis.get("bandwidth_hz"),
            snr_db=analysis.get("snr_db"),
            estimated_modulation=analysis.get("estimated_modulation"),
            spectral_centroid_hz=analysis.get("spectral_centroid_hz"),
            spectral_flatness=analysis.get("spectral_flatness"),
            crest_factor_db=analysis.get("crest_factor_db"),
            energy_total=analysis.get("energy_total"),
        )
        return analysis

    def analyze_file(self, wav_path: str) -> Optional[Dict]:
        """
        分析 WAV 文件。

        Args:
            wav_path: WAV 文件路径

        Returns:
            分析结果字典
        """
        if not os.path.exists(wav_path):
            logger.error(f"文件不存在: {wav_path}")
            return None

        try:
            with wave.open(wav_path, 'rb') as wf:
                n_channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                framerate = wf.getframerate()
                n_frames = wf.getnframes()

                # 读取所有帧
                raw_data = wf.readframes(n_frames)

            # 转换为 numpy 数组
            if sample_width == 2:
                samples = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32)
                samples /= 32768.0
            elif sample_width == 4:
                samples = np.frombuffer(raw_data, dtype=np.int32).astype(np.float32)
                samples /= 2147483648.0
            else:
                logger.error(f"不支持的位深度: {sample_width * 8} bit")
                return None

            # 如果是立体声，取第一声道
            if n_channels > 1:
                samples = samples[::n_channels]

            return self.analyze_samples(samples, framerate)

        except Exception as e:
            logger.error(f"分析文件失败: {wav_path} - {e}")
            return None

    def analyze_samples(self, samples: np.ndarray,
                        sample_rate: int = None) -> Dict:
        """
        分析音频样本数组。

        Args:
            samples: float32 音频样本 (-1.0 到 1.0)
            sample_rate: 采样率

        Returns:
            分析结果字典
        """
        sr = sample_rate or self.sample_rate
        duration = len(samples) / sr

        result = {
            "duration_seconds": duration,
            "sample_rate": sr,
            "total_samples": len(samples),
        }

        # === 时域分析 ===
        result.update(self._time_domain_analysis(samples))

        # === 频域分析 ===
        result.update(self._frequency_domain_analysis(samples, sr))

        # === 调制类型估算 ===
        result["estimated_modulation"] = self._estimate_modulation(result)

        logger.info(
            f"分析完成: 时长={duration:.1f}s, "
            f"SNR={result.get('snr_db', 0):.1f}dB, "
            f"带宽={result.get('bandwidth_hz', 0):.0f}Hz, "
            f"调制={result['estimated_modulation']}"
        )

        return result

    def _time_domain_analysis(self, samples: np.ndarray) -> Dict:
        """时域分析: RMS、峰值、峰均比等。"""
        rms = float(np.sqrt(np.mean(samples ** 2)))
        peak = float(np.max(np.abs(samples)))
        energy = float(np.sum(samples ** 2))

        # 峰均比 (Crest Factor) in dB
        crest_factor_db = 0.0
        if rms > 0:
            crest_factor_db = float(20 * np.log10(peak / rms))

        return {
            "rms": rms,
            "peak_amplitude": peak,
            "energy_total": energy,
            "crest_factor_db": crest_factor_db,
        }

    def _frequency_domain_analysis(self, samples: np.ndarray,
                                    sample_rate: int) -> Dict:
        """频域分析: FFT、频谱质心、带宽、SNR 等。"""
        result = {}

        # 功率谱估算 (Power Spectral Density)
        if HAS_SCIPY:
            # 使用 Welch 方法 — 比手动分段 FFT 更快且有 50% 重叠
            freqs, psd = scipy_signal.welch(
                samples, fs=sample_rate, nperseg=self.fft_size,
                window=self.window_type, scaling='spectrum'
            )
            magnitude_avg = np.sqrt(psd + 1e-20)  # 转换为幅度
        else:
            # 无 scipy 回退: 手动分段 FFT
            window = np.hanning(self.fft_size)
            n_segments = max(1, len(samples) // self.fft_size)
            magnitude_sum = np.zeros(self.fft_size // 2)
            for i in range(n_segments):
                start = i * self.fft_size
                segment = samples[start:start + self.fft_size]
                if len(segment) < self.fft_size:
                    segment = np.pad(segment, (0, self.fft_size - len(segment)))
                windowed = segment * window
                spectrum = np.abs(np.fft.fft(windowed))
                magnitude_sum += spectrum[:self.fft_size // 2]
            magnitude_avg = magnitude_sum / n_segments
            freqs = np.linspace(0, sample_rate / 2, self.fft_size // 2)

        # 转换为 dB
        magnitude_db = 20 * np.log10(magnitude_avg + 1e-10)

        # 峰值频率
        peak_idx = np.argmax(magnitude_avg)
        result["peak_frequency_hz"] = float(freqs[peak_idx])

        # 频谱质心 (Spectral Centroid)
        total_mag = np.sum(magnitude_avg)
        if total_mag > 0:
            result["spectral_centroid_hz"] = float(
                np.sum(freqs * magnitude_avg) / total_mag
            )
        else:
            result["spectral_centroid_hz"] = 0.0

        # 频谱平坦度 (Spectral Flatness) - 区分有调信号 vs 噪声
        geo_mean = np.exp(np.mean(np.log(magnitude_avg + 1e-10)))
        arith_mean = np.mean(magnitude_avg)
        result["spectral_flatness"] = float(geo_mean / (arith_mean + 1e-10))

        # 信号带宽估算
        result["bandwidth_hz"] = self._estimate_bandwidth(
            magnitude_db, freqs
        )

        # SNR 估算
        result["snr_db"] = self._estimate_snr(magnitude_avg, freqs)

        # 保存前 10 个峰值频率及其幅度
        top_indices = np.argsort(magnitude_avg)[-10:][::-1]
        result["fft_peak_magnitudes"] = [
            {"freq_hz": float(freqs[i]), "magnitude_db": float(magnitude_db[i])}
            for i in top_indices
        ]

        return result

    def _estimate_bandwidth(self, magnitude_db: np.ndarray,
                            freqs: np.ndarray) -> float:
        """
        估算信号带宽。

        使用 "峰值 - N dB" 方法：找到频谱中低于峰值 N dB 的边界频率。
        """
        peak_db = np.max(magnitude_db)
        threshold = peak_db - self.bandwidth_threshold_db

        above_threshold = magnitude_db >= threshold
        if not np.any(above_threshold):
            return 0.0

        indices = np.where(above_threshold)[0]
        low_freq = float(freqs[indices[0]])
        high_freq = float(freqs[indices[-1]])

        return high_freq - low_freq

    def _estimate_snr(self, magnitude: np.ndarray,
                      freqs: np.ndarray) -> float:
        """
        估算信噪比 (SNR)。

        方法：将频谱分为信号区域（峰值附近）和噪声区域（两侧），
        计算信号功率与噪声功率的比值。
        """
        peak_idx = np.argmax(magnitude)
        n = len(magnitude)

        # 信号区域: 峰值两侧 10% 范围
        signal_width = max(1, n // 10)
        signal_start = max(0, peak_idx - signal_width)
        signal_end = min(n, peak_idx + signal_width)
        signal_power = np.mean(magnitude[signal_start:signal_end] ** 2)

        # 噪声区域: 信号区域之外
        noise_regions = np.concatenate([
            magnitude[:max(1, signal_start)],
            magnitude[min(n-1, signal_end):]
        ])
        if len(noise_regions) == 0:
            return 0.0
        noise_power = np.mean(noise_regions ** 2)

        if noise_power <= 0:
            return 60.0  # 极低噪声

        snr_db = float(10 * np.log10(signal_power / noise_power))
        return max(0, snr_db)

    def _estimate_modulation(self, analysis: Dict) -> str:
        """
        基于频谱特征估算调制类型。

        使用简单的规则匹配，比较带宽、频谱平坦度和峰均比
        与已知调制类型的特征范围。
        """
        bandwidth = analysis.get("bandwidth_hz", 0)
        flatness = analysis.get("spectral_flatness", 0)
        crest = analysis.get("crest_factor_db", 0)
        snr = analysis.get("snr_db", 0)

        # 如果 SNR 很低，可能是噪声
        if snr < 3:
            return "NOISE"

        best_match = "UNKNOWN"
        best_score = 0

        for mod_type, profile in self.MODULATION_PROFILES.items():
            score = 0

            # 带宽匹配
            bw_min, bw_max = profile["bandwidth_range"]
            if bw_min <= bandwidth <= bw_max:
                score += 2

            # 平坦度匹配
            fl_min, fl_max = profile["flatness_range"]
            if fl_min <= flatness <= fl_max:
                score += 1

            # 峰均比匹配
            cr_min, cr_max = profile["crest_range"]
            if cr_min <= crest <= cr_max:
                score += 1

            if score > best_score:
                best_score = score
                best_match = mod_type

        return best_match

    def get_spectrogram(self, samples: np.ndarray,
                        sample_rate: int = None) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        生成频谱图数据（用于可视化）。

        Args:
            samples: 音频样本
            sample_rate: 采样率

        Returns:
            (times, frequencies, spectrogram_db) 三元组
        """
        sr = sample_rate or self.sample_rate

        if HAS_SCIPY:
            freqs, times, Sxx = scipy_signal.spectrogram(
                samples, fs=sr,
                nperseg=self.fft_size // 4,
                noverlap=self.fft_size // 8,
                window=self.window_type
            )
            Sxx_db = 10 * np.log10(Sxx + 1e-10)
        else:
            # 简化版频谱图
            hop = self.fft_size // 4
            n_hops = len(samples) // hop
            Sxx = np.zeros((self.fft_size // 2, n_hops))
            for i in range(n_hops):
                start = i * hop
                seg = samples[start:start + self.fft_size // 2]
                if len(seg) < self.fft_size // 2:
                    seg = np.pad(seg, (0, self.fft_size // 2 - len(seg)))
                Sxx[:len(seg), i] = np.abs(np.fft.fft(seg, self.fft_size))[:self.fft_size // 2]

            freqs = np.linspace(0, sr / 2, self.fft_size // 2)
            times = np.arange(n_hops) * hop / sr
            Sxx_db = 10 * np.log10(Sxx + 1e-10)

        return times, freqs, Sxx_db
