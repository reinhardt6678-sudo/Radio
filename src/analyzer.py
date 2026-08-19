"""
analyzer.py - 信号元数据分析模块

对录制的音频进行频域和时域分析，提取信号特征，
包括带宽估算、SNR 计算、调制类型初步识别等。

所有频域指标都限制在解调通带内 (见 modes.py)。审计里的缺陷 03 就是把
3000-6000 Hz 的滤波器阻带当成了噪声基准 —— 那里本来就没有能量，分母被压低，
SNR 被系统性地扭曲，744 条记录里只有 6 条超过 10 dB。
"""

import os
import wave
import logging
import numpy as np
from typing import Dict, Optional, List, Tuple

from .modes import audio_passband, normalize_mode

try:
    from scipy import signal as scipy_signal
    from scipy.fft import fft, fftfreq
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

logger = logging.getLogger(__name__)


# ==================== 隶属度函数 ====================
# 特征打分用连续隶属度而不是"落在区间内 +1 分"。
# 旧的整数打分让 744 条记录里 690 条出现三向并列，最终由
# MODULATION_PROFILES 的字典顺序决定输出 —— 那不是在识别信号。

def _ramp(x: float, lo: float, hi: float) -> float:
    """低于 lo 记 0 分，高于 hi 记 1 分，中间线性过渡。"""
    if hi <= lo:
        return 1.0 if x >= hi else 0.0
    return float(np.clip((x - lo) / (hi - lo), 0.0, 1.0))


def _window(x: float, lo: float, hi: float, soft: float) -> float:
    """区间内记 1 分，区间外在 soft 宽度内线性衰减到 0。"""
    if lo <= x <= hi:
        return 1.0
    if x < lo:
        return float(np.clip(1.0 - (lo - x) / max(soft, 1e-9), 0.0, 1.0))
    return float(np.clip(1.0 - (x - hi) / max(soft, 1e-9), 0.0, 1.0))


class SignalAnalyzer:
    """
    信号分析器。

    对音频文件或实时音频数据执行频谱分析、特征提取和调制类型识别。
    """

    # 语音标签按解调模式确定 —— 接收机知道自己在解什么，
    # 不需要（也不可能）从解调后的音频里猜 USB 还是 AM
    VOICE_LABELS = {
        "USB": "USB_VOICE",
        "LSB": "LSB_VOICE",
        "AM": "AM_VOICE",
        "NFM": "FM_VOICE",
    }

    # 各调制类型的可读描述
    MODULATION_DESCRIPTIONS = {
        "USB_VOICE": "上边带语音",
        "LSB_VOICE": "下边带语音",
        "AM_VOICE": "调幅语音",
        "FM_VOICE": "调频语音",
        "VOICE": "语音",
        "CW": "连续波/莫尔斯电码",
        "CARRIER": "未调制载波/单音",
        "FSK": "频移键控 (STANAG 等)",
        "PSK": "相移键控/数据",
        "NOISE": "噪声/无信号",
        "UNKNOWN": "特征不足以判定",
    }

    def __init__(self, fft_size: int = 4096, window_type: str = "hann",
                 bandwidth_threshold_db: float = 20,
                 sample_rate: int = 12000,
                 noise_percentile: float = 20.0,
                 noise_snr_threshold_db: float = 3.0,
                 min_confidence: float = 0.35):
        """
        初始化分析器。

        Args:
            fft_size: FFT 点数
            window_type: 窗函数类型 (hann/hamming/blackman)
            bandwidth_threshold_db: 带宽估算阈值 (低于峰值 N dB)
            sample_rate: 默认采样率
            noise_percentile: 带内噪声基底取第几百分位 (对占满通带的信号更稳健)
            noise_snr_threshold_db: 低于该带内 SNR 判定为噪声
            min_confidence: 调制判定的最低置信度，低于此值输出 UNKNOWN
        """
        self.fft_size = fft_size
        self.window_type = window_type
        self.bandwidth_threshold_db = bandwidth_threshold_db
        self.sample_rate = sample_rate
        self.noise_percentile = noise_percentile
        self.noise_snr_threshold_db = noise_snr_threshold_db
        self.min_confidence = min_confidence

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
            noise_percentile=ana_cfg.get("noise_percentile", 20.0),
            noise_snr_threshold_db=ana_cfg.get("noise_snr_threshold_db", 3.0),
            min_confidence=ana_cfg.get("min_confidence", 0.35),
        )

    def analyze_and_save(self, db, signal_id: int,
                         audio_buffer: list, sample_rate: int = None,
                         mode: str = None):
        """
        分析音频缓冲区并保存结果到数据库 (消除重复代码)。

        Args:
            db: Database 实例
            signal_id: 信号 ID
            audio_buffer: 音频块列表 [np.ndarray, ...]
            sample_rate: 采样率，默认使用 self.sample_rate
            mode: 解调模式 (决定通带和语音标签)
        """
        sr = sample_rate or self.sample_rate
        if not audio_buffer:
            return None
        all_samples = np.concatenate(audio_buffer)
        if len(all_samples) < sr:  # 至少 1 秒
            return None
        analysis = self.analyze_samples(all_samples, sr, mode=mode)
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
            modulation_confidence=analysis.get("modulation_confidence"),
            demod_mode=analysis.get("demod_mode"),
            noise_floor_db=analysis.get("noise_floor_db"),
            envelope_rate_hz=analysis.get("envelope_rate_hz"),
            envelope_depth=analysis.get("envelope_depth"),
            tone_count=analysis.get("tone_count"),
            syllabic_ratio=analysis.get("syllabic_ratio"),
            passband_tilt_db=analysis.get("passband_tilt_db"),
            speech_score=analysis.get("speech_score"),
        )
        return analysis

    @staticmethod
    def load_wav(wav_path: str) -> Optional[Tuple[np.ndarray, int]]:
        """
        读取 WAV 文件为归一化的 float 样本。

        Returns:
            (samples, sample_rate)，读取失败返回 None
        """
        if not os.path.exists(wav_path):
            logger.error(f"文件不存在: {wav_path}")
            return None

        try:
            with wave.open(wav_path, 'rb') as wf:
                n_channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                framerate = wf.getframerate()
                raw_data = wf.readframes(wf.getnframes())

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

            return samples, framerate

        except Exception as e:
            logger.error(f"读取 WAV 失败: {wav_path} - {e}")
            return None

    def analyze_file(self, wav_path: str, mode: str = None) -> Optional[Dict]:
        """
        分析 WAV 文件。

        Args:
            wav_path: WAV 文件路径
            mode: 录音时使用的解调模式 (决定分析通带)，默认 USB

        Returns:
            分析结果字典
        """
        loaded = self.load_wav(wav_path)
        if loaded is None:
            return None
        samples, framerate = loaded
        return self.analyze_samples(samples, framerate, mode=mode)

    def analyze_samples(self, samples: np.ndarray,
                        sample_rate: int = None,
                        mode: str = None) -> Dict:
        """
        分析音频样本数组。

        Args:
            samples: float32 音频样本 (-1.0 到 1.0)
            sample_rate: 采样率
            mode: 解调模式 (USB/LSB/AM/CW)，决定分析通带

        Returns:
            分析结果字典
        """
        sr = sample_rate or self.sample_rate
        samples = np.asarray(samples, dtype=np.float64)
        duration = len(samples) / sr
        demod_mode = normalize_mode(mode)
        band = audio_passband(demod_mode, sr)

        result = {
            "duration_seconds": duration,
            "sample_rate": sr,
            "total_samples": len(samples),
            "demod_mode": demod_mode,
            "passband_hz": [band[0], band[1]],
        }

        if len(samples) < 2:
            result.update(self._time_domain_analysis(samples))
            result.update({
                "snr_db": 0.0, "bandwidth_hz": 0.0, "noise_floor_db": -120.0,
                "peak_frequency_hz": 0.0, "spectral_centroid_hz": 0.0,
                "spectral_flatness": 0.0, "band_flatness": 0.0,
                "tone_count": 0, "envelope_rate_hz": 0.0, "envelope_depth": 0.0,
                "syllabic_ratio": 0.0, "passband_tilt_db": 0.0,
                "speech_score": 0.0, "is_speech": False,
                "estimated_modulation": "NOISE", "modulation_confidence": 0.0,
                "modulation_scores": {}, "modulation_description": "噪声/无信号",
            })
            return result

        # === 时域分析 ===
        result.update(self._time_domain_analysis(samples))

        # === 频域分析 (全部限制在解调通带内) ===
        result.update(self._frequency_domain_analysis(samples, sr, band))

        # === 包络分析: 语音音节率 / 键控率 ===
        result.update(self._envelope_analysis(samples, sr))

        # === 人声结构评分 (录完之后给这段录音打分，供排序/过滤) ===
        result.update(self._speech_analysis(samples, sr, band))

        # === 调制类型估算 ===
        label, confidence, scores = self._classify_modulation(result, demod_mode)
        result["estimated_modulation"] = label
        result["modulation_confidence"] = confidence
        result["modulation_scores"] = scores
        result["modulation_description"] = self.MODULATION_DESCRIPTIONS.get(label, label)

        logger.info(
            f"分析完成: 时长={duration:.1f}s, "
            f"带内SNR={result.get('snr_db', 0):.1f}dB, "
            f"占用带宽={result.get('bandwidth_hz', 0):.0f}Hz, "
            f"调制={label} (置信度 {confidence:.2f})"
        )

        return result

    # ==================== 时域 ====================

    def _time_domain_analysis(self, samples: np.ndarray) -> Dict:
        """时域分析: RMS、峰值、峰均比等。"""
        if len(samples) == 0:
            return {"rms": 0.0, "peak_amplitude": 0.0,
                    "energy_total": 0.0, "crest_factor_db": 0.0}

        rms = float(np.sqrt(np.mean(samples ** 2)))
        peak = float(np.max(np.abs(samples)))
        energy = float(np.sum(samples ** 2))

        # 峰均比 (Crest Factor) in dB
        crest_factor_db = 0.0
        if rms > 0 and peak > 0:
            crest_factor_db = float(20 * np.log10(peak / rms))

        return {
            "rms": rms,
            "peak_amplitude": peak,
            "energy_total": energy,
            "crest_factor_db": crest_factor_db,
        }

    # ==================== 频域 ====================

    def _segment_length(self, n_samples: int) -> int:
        """
        选择 Welch 分段长度。

        分段数太少时噪声基底的分位数估计方差很大，所以短信号自动缩短分段，
        保证至少 8 段参与平均。
        """
        if n_samples <= 0:
            return 256
        target = max(256, n_samples // 8)
        nperseg = 1 << int(np.floor(np.log2(target)))
        return int(max(64, min(nperseg, self.fft_size, n_samples)))

    def _power_spectrum(self, samples: np.ndarray, sample_rate: int,
                        nperseg: int = None) -> Tuple[np.ndarray, np.ndarray]:
        """估算功率谱，返回 (频率, 每个 bin 的功率)。"""
        nperseg = int(min(nperseg, len(samples))) if nperseg \
            else self._segment_length(len(samples))

        if HAS_SCIPY:
            freqs, power = scipy_signal.welch(
                samples, fs=sample_rate, nperseg=nperseg,
                window=self.window_type, scaling='spectrum'
            )
            return freqs, np.asarray(power, dtype=np.float64)

        # 无 scipy 回退: 手动分段 FFT (50% 重叠, 与 welch 一致)
        # 归一化要和 welch(scaling='spectrum') 对齐: 幅度 A 的正弦返回 A²/2,
        # 少乘这个 2 会让所有绝对 dB 读数比装了 scipy 时高 3 dB
        window = np.hanning(nperseg)
        norm = 2.0 * (window.sum() / 2.0) ** 2
        hop = max(1, nperseg // 2)
        n_segments = max(1, 1 + (len(samples) - nperseg) // hop)
        power_sum = np.zeros(nperseg // 2 + 1)
        for i in range(n_segments):
            start = i * hop
            segment = samples[start:start + nperseg]
            if len(segment) < nperseg:
                segment = np.pad(segment, (0, nperseg - len(segment)))
            spectrum = np.abs(np.fft.rfft(segment * window)) ** 2
            power_sum += spectrum
        power = power_sum / (n_segments * norm)
        freqs = np.fft.rfftfreq(nperseg, 1.0 / sample_rate)
        return freqs, power

    def _frequency_domain_analysis(self, samples: np.ndarray,
                                   sample_rate: int,
                                   band: Tuple[float, float]) -> Dict:
        """频域分析: 带内 SNR、占用带宽、音调结构等。"""
        result = {}
        freqs, power = self._power_spectrum(samples, sample_rate)
        magnitude_avg = np.sqrt(np.maximum(power, 0) + 1e-20)
        magnitude_db = 10 * np.log10(np.maximum(power, 1e-20))

        # 频谱质心与平坦度用整个频谱 (保持与历史数据可比)
        total_mag = float(np.sum(magnitude_avg))
        result["spectral_centroid_hz"] = (
            float(np.sum(freqs * magnitude_avg) / total_mag) if total_mag > 0 else 0.0
        )
        geo_mean = float(np.exp(np.mean(np.log(magnitude_avg + 1e-10))))
        arith_mean = float(np.mean(magnitude_avg))
        result["spectral_flatness"] = float(geo_mean / (arith_mean + 1e-10))

        # 带内指标
        lo, hi = band
        band_mask = (freqs >= lo) & (freqs <= hi)
        if not np.any(band_mask):
            band_mask = np.ones_like(freqs, dtype=bool)

        band_freqs = freqs[band_mask]
        band_power = power[band_mask]

        result.update(self._band_metrics(band_freqs, band_power))
        result.update(self._tone_analysis(band_freqs, band_power,
                                          result["noise_floor_power"]))

        # 带内平坦度 —— 比全频谱平坦度更能反映信号本身
        band_mag = np.sqrt(np.maximum(band_power, 0) + 1e-20)
        band_geo = float(np.exp(np.mean(np.log(band_mag + 1e-20))))
        band_arith = float(np.mean(band_mag))
        result["band_flatness"] = float(band_geo / (band_arith + 1e-20))

        # 保留旧口径的 -N dB 带宽 (限制在通带内)，便于和历史数据对照
        result["bandwidth_20db_hz"] = self._estimate_bandwidth(
            magnitude_db[band_mask], band_freqs
        )

        # 前 10 个频谱峰值 (带内)
        top_indices = np.argsort(band_power)[-10:][::-1]
        result["fft_peak_magnitudes"] = [
            {"freq_hz": float(band_freqs[i]),
             "magnitude_db": float(10 * np.log10(max(band_power[i], 1e-20)))}
            for i in top_indices
        ]

        result.pop("noise_floor_power", None)
        return result

    def _band_metrics(self, band_freqs: np.ndarray,
                      band_power: np.ndarray) -> Dict:
        """
        带内噪声基底、SNR 和占用带宽。

        噪声基底取带内功率的低分位数 (默认第 20 百分位)：即使信号占满整个
        通带，最弱的那部分 bin 仍然接近底噪，比"拿阻带当噪声"稳健得多。
        SNR = 扣除底噪后的信号功率 / 底噪总功率。
        """
        n_bins = len(band_power)
        if n_bins == 0:
            return {"noise_floor_power": 0.0, "noise_floor_db": -120.0,
                    "snr_db": 0.0, "bandwidth_hz": 0.0,
                    "peak_frequency_hz": 0.0, "band_power_db": -120.0}

        noise_floor = float(np.percentile(band_power, self.noise_percentile))
        peak_idx = int(np.argmax(band_power))
        band_total = float(np.sum(band_power))

        metrics = {
            "noise_floor_power": noise_floor,
            "noise_floor_db": float(10 * np.log10(max(noise_floor, 1e-20))),
            "peak_frequency_hz": float(band_freqs[peak_idx]),
            "band_power_db": float(10 * np.log10(max(band_total, 1e-20))),
        }

        if noise_floor <= 0:
            # 数字静音: 没有底噪可比，SNR 无意义
            metrics["snr_db"] = 0.0
            metrics["bandwidth_hz"] = 0.0
            return metrics

        excess = np.clip(band_power - noise_floor, 0.0, None)
        signal_power = float(np.sum(excess))
        noise_power = noise_floor * n_bins

        snr_db = 10 * np.log10(max(signal_power, 1e-20) / noise_power)
        metrics["snr_db"] = float(np.clip(snr_db, -30.0, 60.0))
        metrics["bandwidth_hz"] = self._occupied_bandwidth(band_freqs, excess)
        return metrics

    @staticmethod
    def _occupied_bandwidth(freqs: np.ndarray, excess: np.ndarray,
                            fraction: float = 0.90) -> float:
        """
        占用带宽: 扣除底噪后包含 fraction 能量的频率跨度。

        旧的"峰值 -20 dB"口径测的其实是接收机的滤波器：全库 744 条记录带宽
        min 932 / max 2846 / avg 2760 Hz，三种"不同"调制的平均带宽只差 25 Hz。
        扣掉底噪之后，纯音会收敛到几十 Hz，语音在 1-2.5 kHz，才有区分度。
        """
        total = float(np.sum(excess))
        if total <= 0 or len(freqs) < 2:
            return 0.0

        cumulative = np.cumsum(excess) / total
        margin = (1.0 - fraction) / 2.0
        lo_idx = int(np.searchsorted(cumulative, margin))
        hi_idx = int(np.searchsorted(cumulative, 1.0 - margin))
        lo_idx = min(lo_idx, len(freqs) - 1)
        hi_idx = min(hi_idx, len(freqs) - 1)

        bin_width = float(freqs[1] - freqs[0])
        return float(max(freqs[hi_idx] - freqs[lo_idx], bin_width))

    def _tone_analysis(self, band_freqs: np.ndarray, band_power: np.ndarray,
                       noise_floor: float, tone_snr_db: float = 10.0,
                       min_separation_hz: float = 50.0,
                       max_tones: int = 24) -> Dict:
        """
        数一数带内有几个明显的窄带音调，以及最强两个音调的间距。

        CW 是单音，FSK 是双音（间距 = 频移），语音/数据是连续谱。
        这个特征在固定通带下依然有效，而带宽没有。

        上限要留得足够高: 实测 FSK 稳定在 8 个、语音 6 个、CW 5 个，而 2400 Bd
        的 PSK 是连续谱、有多少格数多少格。上限压到 8 会让 PSK 和 FSK 在这个
        特征上完全一样。
        """
        empty = {"tone_count": 0, "tone_spacing_hz": 0.0, "tone_purity": 0.0}
        if len(band_power) == 0 or noise_floor <= 0:
            return empty

        threshold = noise_floor * (10 ** (tone_snr_db / 10.0))
        order = np.argsort(band_power)[::-1]
        picked: List[int] = []

        for idx in order:
            if band_power[idx] < threshold:
                break
            if all(abs(band_freqs[idx] - band_freqs[j]) >= min_separation_hz
                   for j in picked):
                picked.append(int(idx))
            if len(picked) >= max_tones:
                break

        if not picked:
            return empty

        total_excess = float(np.sum(np.clip(band_power - noise_floor, 0.0, None)))
        strongest = float(band_power[picked[0]] - noise_floor)

        spacing = 0.0
        if len(picked) >= 2:
            spacing = float(abs(band_freqs[picked[0]] - band_freqs[picked[1]]))

        return {
            "tone_count": len(picked),
            "tone_spacing_hz": spacing,
            "tone_purity": float(strongest / total_excess) if total_excess > 0 else 0.0,
        }

    def _estimate_bandwidth(self, magnitude_db: np.ndarray,
                            freqs: np.ndarray) -> float:
        """
        旧口径带宽估算 (峰值 - N dB)，保留用于和历史数据对照。

        注意: 在固定通带下这个值基本等于滤波器宽度，不要用它做调制判定。
        """
        if len(magnitude_db) == 0:
            return 0.0

        peak_db = float(np.max(magnitude_db))
        threshold = peak_db - self.bandwidth_threshold_db

        above_threshold = magnitude_db >= threshold
        if not np.any(above_threshold):
            return 0.0

        indices = np.where(above_threshold)[0]
        return float(freqs[indices[-1]] - freqs[indices[0]])

    # ==================== 包络 ====================

    def _speech_analysis(self, samples: np.ndarray, sample_rate: int,
                         band: Tuple[float, float]) -> Dict:
        """
        人声结构评分。

        判据来自 93 小时实测: 真通联和噪声的区别不在音量, 而在
          1) 音节调制 —— 人说话是一句一句的, 包络能量集中在 0.5-4 Hz;
             噪声的包络能量往高频跑
          2) 通带倾斜 —— 语音在 USB 通带里低频端更强
        这两个量都是"形状", 不是电平, 所以节点 AGC 开不开都成立
        (电平类判据在 AGC 开着时会失效, 见 squelch.py)。

        speech_score 是 0-1 的连续分, 用来给录音排序;
        is_speech 复现实验里的判定阈值。
        """
        empty = {"syllabic_ratio": 0.0, "passband_tilt_db": 0.0,
                 "speech_score": 0.0, "is_speech": False}
        block = max(1, int(round(sample_rate / 100.0)))   # 100 Hz 包络
        if len(samples) < block * 32:
            return empty

        # --- 包络调制谱 ---
        n = len(samples) // block * block
        env = np.abs(samples[:n]).reshape(-1, block).mean(axis=1)
        e = env - env.mean()
        if len(e) < 16 or not np.any(e):
            return empty
        spec = np.abs(np.fft.rfft(e * np.hanning(len(e))))
        fr = np.fft.rfftfreq(len(e), block / float(sample_rate))
        total = spec[(fr > 0.5) & (fr <= 20.0)].sum()
        if total <= 0:
            return empty
        syllabic = float(spec[(fr >= 0.5) & (fr < 4.0)].sum() / total)

        # --- 通带倾斜: 低端 vs 高端 ---
        lo0, hi0 = band
        mid = lo0 + (hi0 - lo0) * 0.35
        win = np.hanning(len(samples))
        psd = np.abs(np.fft.rfft(samples * win)) ** 2
        f2 = np.fft.rfftfreq(len(samples), 1.0 / sample_rate)
        low = psd[(f2 >= lo0) & (f2 < mid)]
        high = psd[(f2 >= hi0 - (hi0 - lo0) * 0.35) & (f2 < hi0)]
        if low.size == 0 or high.size == 0 or high.mean() <= 0:
            tilt = 0.0
        else:
            tilt = float(10 * np.log10(max(low.mean(), 1e-30) / high.mean()))

        score = float(np.sqrt(_ramp(syllabic, 0.20, 0.40)
                              * _ramp(tilt, 0.0, 2.5)))
        return {"syllabic_ratio": syllabic,
                "passband_tilt_db": tilt,
                "speech_score": score,
                "is_speech": bool(syllabic > 0.30 and tilt > 1.0)}

    def _envelope_analysis(self, samples: np.ndarray, sample_rate: int,
                           envelope_rate_hz: float = 200.0) -> Dict:
        """
        包络调制分析。

        语音的音节率集中在 2-8 Hz，莫尔斯键控在 5-40 Hz 且深度接近 100%，
        FSK/PSK 是恒包络 (深度极低)。这是固定通带下仍然有效的判别特征。
        """
        empty = {
            "envelope_rate_hz": 0.0,
            "envelope_depth": 0.0,
            "keying_rate_hz": 0.0,
            "envelope_sample_rate": 0.0,
        }

        hop = max(1, int(round(sample_rate / envelope_rate_hz)))
        n_blocks = len(samples) // hop
        if n_blocks < 32:
            return empty

        blocks = samples[:n_blocks * hop].reshape(n_blocks, hop)
        envelope = np.sqrt(np.mean(blocks ** 2, axis=1))
        mean_env = float(np.mean(envelope))
        if mean_env <= 0:
            return empty

        env_sr = sample_rate / hop
        centered = envelope - mean_env
        window = np.hanning(n_blocks)
        # 单边幅度谱: 乘 2 / 窗函数增益
        spectrum = np.abs(np.fft.rfft(centered * window)) * 2.0 / np.sum(window)
        env_freqs = np.fft.rfftfreq(n_blocks, 1.0 / env_sr)

        def peak_in(lo: float, hi: float) -> Tuple[float, float]:
            mask = (env_freqs >= lo) & (env_freqs <= hi)
            if not np.any(mask):
                return 0.0, 0.0
            idx = int(np.argmax(spectrum[mask]))
            return float(env_freqs[mask][idx]), float(spectrum[mask][idx])

        syllabic_hz, syllabic_mag = peak_in(1.5, 20.0)
        keying_hz, _ = peak_in(1.5, min(80.0, env_sr / 2))

        return {
            "envelope_rate_hz": syllabic_hz,
            "envelope_depth": float(syllabic_mag / mean_env),
            "keying_rate_hz": keying_hz,
            "envelope_sample_rate": float(env_sr),
        }

    # ==================== 调制识别 ====================

    def _classify_modulation(self, f: Dict,
                             mode: str) -> Tuple[str, float, Dict[str, float]]:
        """
        基于带内特征估算调制类型。

        每个类别按连续隶属度加权打分，得分几乎不会并列；即便并列，输出也会
        因为置信度不足而变成 UNKNOWN，而不是悄悄取字典里的第一个键。

        Returns:
            (标签, 置信度 0-1, 各类别得分)
        """
        snr = float(f.get("snr_db", 0.0))
        occupied_bw = float(f.get("bandwidth_hz", 0.0))
        crest = float(f.get("crest_factor_db", 0.0))
        flatness = float(f.get("band_flatness", 0.0))
        tone_count = int(f.get("tone_count", 0))
        tone_spacing = float(f.get("tone_spacing_hz", 0.0))
        tone_purity = float(f.get("tone_purity", 0.0))
        env_rate = float(f.get("envelope_rate_hz", 0.0))
        env_depth = float(f.get("envelope_depth", 0.0))
        keying_rate = float(f.get("keying_rate_hz", 0.0))

        # 带内 SNR 过低 = 没有信号，其余特征都没有意义
        if snr < self.noise_snr_threshold_db:
            deficit = self.noise_snr_threshold_db - snr
            confidence = float(np.clip(0.5 + deficit / 12.0, 0.5, 0.99))
            return "NOISE", round(confidence, 3), {"NOISE": round(confidence, 3)}

        constant_envelope = 1.0 - _ramp(env_depth, 0.06, 0.25)
        # "少数几个离散音调" —— 数出一大把说明是连续谱，不是移频键控
        tone_term = (1.0 if 2 <= tone_count <= 4
                     else 0.7 if 5 <= tone_count <= 8
                     else 0.2 if 9 <= tone_count <= 12 else 0.0)

        # 证据权重
        raw = {
            # 语音: 2-8 Hz 音节包络 + 明显的包络起伏 + 占据大半个话音带
            "VOICE": (
                0.40 * _window(env_rate, 2.0, 8.0, soft=2.0)
                + 0.30 * _ramp(env_depth, 0.08, 0.30)
                + 0.20 * _window(occupied_bw, 600.0, 3000.0, soft=500.0)
                + 0.10 * _window(crest, 8.0, 22.0, soft=5.0)
            ),
            # CW: 极窄 + 键控造成的深度包络起伏
            "CW": (
                0.30 * _window(occupied_bw, 0.0, 250.0, soft=200.0)
                + 0.20 * (1.0 if tone_count <= 2 else 0.6 if tone_count <= 5 else 0.2)
                + 0.35 * _ramp(env_depth, 0.25, 0.80)
                + 0.15 * _window(keying_rate, 4.0, 40.0, soft=8.0)
            ),
            # 未调载波: 极窄 + 恒定 + 能量集中在一个音调上
            "CARRIER": (
                0.40 * _window(occupied_bw, 0.0, 150.0, soft=200.0)
                + 0.30 * (1.0 - _ramp(env_depth, 0.10, 0.30))
                + 0.30 * _ramp(tone_purity, 0.30, 0.80)
            ),
            # FSK: 少数几个音调 + 稳定间距 + 恒包络 + 能量集中在音调上
            "FSK": (
                0.30 * tone_term
                + 0.20 * _window(tone_spacing, 60.0, 1200.0, soft=300.0)
                + 0.20 * constant_envelope
                + 0.15 * _window(occupied_bw, 100.0, 2000.0, soft=600.0)
                + 0.15 * _ramp(tone_purity, 0.08, 0.30)
            ),
            # PSK/数据: 恒包络 + 连续谱 + 没有音节率
            "PSK": (
                0.35 * constant_envelope
                + 0.25 * _ramp(occupied_bw, 400.0, 1200.0)
                + 0.20 * _window(flatness, 0.10, 0.65, soft=0.15)
                + 0.20 * (1.0 - _window(env_rate, 2.0, 8.0, soft=2.0))
            ),
        }

        # 否决条件: 缺了这些，加权得分再高也不成立。
        # 例如一个 20 Hz 宽的信号无论其它特征如何都不可能是宽带数据波形 ——
        # 旧打分器没有这一层，所以什么都能"并列第一"。
        gates = {
            "VOICE": min(_ramp(occupied_bw, 250.0, 700.0),
                         _ramp(env_depth, 0.05, 0.12)),
            "CW": _window(occupied_bw, 0.0, 400.0, soft=350.0),
            "CARRIER": (_window(occupied_bw, 0.0, 200.0, soft=250.0)
                        * (1.0 - _ramp(env_depth, 0.15, 0.35))
                        * (1.0 if tone_count <= 2 else 0.3 if tone_count <= 4 else 0.0)),
            # FSK 是恒包络的: 包络深度大说明是键控或语音，不是移频
            "FSK": ((1.0 if tone_count >= 2 else 0.0)
                    * (1.0 - _ramp(env_depth, 0.25, 0.60))),
            "PSK": _ramp(occupied_bw, 200.0, 600.0),
        }

        scores = {k: raw[k] * gates[k] for k in raw}

        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        top_label, top_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0

        # 置信度同时看绝对得分和领先幅度：并列 = 没有把握
        margin = (top_score - second_score) / top_score if top_score > 0 else 0.0
        confidence = float(np.clip(top_score * (0.4 + 0.6 * margin), 0.0, 1.0))

        # 包络分析需要足够长的样本，拿不到时不许给高置信度
        if not f.get("envelope_sample_rate"):
            confidence = min(confidence, self.min_confidence * 0.9)

        rounded = {k: round(v, 3) for k, v in scores.items()}
        if confidence < self.min_confidence:
            return "UNKNOWN", round(confidence, 3), rounded

        if top_label == "VOICE":
            return self.VOICE_LABELS.get(mode, "VOICE"), round(confidence, 3), rounded
        return top_label, round(confidence, 3), rounded

    # ==================== 实时频谱 ====================

    def live_spectrum(self, samples: np.ndarray, sample_rate: int = None,
                      mode: str = None, n_bins: int = 128,
                      f_max: float = 4000.0,
                      db_floor: float = -110.0,
                      db_ceil: float = -10.0) -> Dict:
        """
        为 Web 瀑布图计算一列频谱。

        Args:
            samples: 最近一小段音频
            sample_rate: 采样率
            mode: 解调模式 (用于带内噪声/SNR)
            n_bins: 输出的频率格数
            f_max: 显示的最高频率
            db_floor/db_ceil: 量化范围

        Returns:
            {bins: [0-255], f_max, noise_floor_db, snr_db, peak_frequency_hz}
        """
        sr = sample_rate or self.sample_rate
        samples = np.asarray(samples, dtype=np.float64)
        if len(samples) < 256:
            return {"bins": [], "f_max": f_max, "noise_floor_db": db_floor,
                    "snr_db": 0.0, "peak_frequency_hz": 0.0, "peak_db": db_floor}

        # 分段平均而不是单次 FFT: 单次 FFT 的每个 bin 都是指数分布，
        # 低分位数会明显低于真实噪声均值，实时 SNR 会凭空虚高 5 dB 以上。
        nperseg = int(min(512, 1 << int(np.floor(np.log2(len(samples) // 4 or 1)))))
        freqs, power = self._power_spectrum(samples, sr, nperseg=max(nperseg, 64))

        # 带内噪声基底与 SNR (和离线分析同一套口径)
        band = audio_passband(mode, sr)
        band_mask = (freqs >= band[0]) & (freqs <= band[1])
        if np.any(band_mask):
            metrics = self._band_metrics(freqs[band_mask], power[band_mask])
        else:
            metrics = {"noise_floor_db": db_floor, "snr_db": 0.0,
                       "peak_frequency_hz": 0.0}

        # 按显示范围重采样到 n_bins 格
        top = min(f_max, sr / 2)
        edges = np.linspace(0.0, top, n_bins + 1)
        idx = np.clip(np.searchsorted(freqs, edges) , 0, len(power))
        bins = []
        for i in range(n_bins):
            lo, hi = idx[i], max(idx[i] + 1, idx[i + 1])
            chunk = power[lo:hi]
            value = float(np.max(chunk)) if len(chunk) else 1e-20
            db = 10 * np.log10(max(value, 1e-20))
            scaled = (db - db_floor) / (db_ceil - db_floor)
            bins.append(int(np.clip(scaled * 255, 0, 255)))

        peak_idx = int(np.argmax(power))
        return {
            "bins": bins,
            "f_max": float(top),
            "noise_floor_db": round(float(metrics.get("noise_floor_db", db_floor)), 1),
            "snr_db": round(float(metrics.get("snr_db", 0.0)), 1),
            "peak_frequency_hz": round(float(freqs[peak_idx]), 1),
            "peak_db": round(float(10 * np.log10(max(power[peak_idx], 1e-20))), 1),
        }

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
            n_hops = max(1, len(samples) // hop)
            Sxx = np.zeros((self.fft_size // 2, n_hops))
            for i in range(n_hops):
                start = i * hop
                seg = samples[start:start + self.fft_size // 2]
                if len(seg) < self.fft_size // 2:
                    seg = np.pad(seg, (0, self.fft_size // 2 - len(seg)))
                Sxx[:, i] = np.abs(np.fft.fft(seg, self.fft_size))[:self.fft_size // 2]

            freqs = np.linspace(0, sr / 2, self.fft_size // 2)
            times = np.arange(n_hops) * hop / sr
            Sxx_db = 10 * np.log10(Sxx + 1e-10)

        return times, freqs, Sxx_db
