"""
recorder.py - 音频录制引擎

将接收到的音频流写入 WAV 文件，支持信号触发录制、
自动分段和 pre-roll 缓冲区。
"""

import os
import wave
import struct
import logging
import numpy as np
from datetime import datetime, timezone
from typing import Optional, Callable
from pathlib import Path

logger = logging.getLogger(__name__)


class AudioRecorder:
    """
    音频录制器。

    接收 float32 音频样本并写入 16-bit PCM WAV 文件。
    支持动态开始/停止录制、自动分段和文件命名。
    """

    def __init__(self, output_dir: str, sample_rate: int = 12000,
                 bit_depth: int = 16, max_duration: float = 300,
                 on_segment_complete: Callable = None):
        """
        初始化录音器。

        Args:
            output_dir: WAV 文件输出目录
            sample_rate: 采样率 (Hz)
            bit_depth: 位深度 (16 或 32)
            max_duration: 最长录音时长 (秒)，超过后自动分段
            on_segment_complete: 自动分段时的回调函数，参数为已完成分段的 info dict
        """
        self.output_dir = output_dir
        self.sample_rate = sample_rate
        self.bit_depth = bit_depth
        self.max_duration = max_duration
        self._on_segment_complete = on_segment_complete
        self._channels = 1  # 单声道

        # 确保输出目录存在
        os.makedirs(output_dir, exist_ok=True)

        # 录音状态
        self._wav_file: Optional[wave.Wave_write] = None
        self._current_path: Optional[str] = None
        self._samples_written = 0
        self._is_recording = False
        self._current_freq = 0.0
        self._current_node = ""
        self._recording_start_time: Optional[datetime] = None

    @classmethod
    def from_config(cls, config: dict) -> "AudioRecorder":
        """
        从配置字典创建录音器。

        Args:
            config: 完整配置字典 (包含 recording 和 receiver 部分)
        """
        rec_cfg = config.get("recording", {})
        recv_cfg = config.get("receiver", {})
        return cls(
            output_dir=rec_cfg.get("output_dir", "data/recordings"),
            sample_rate=recv_cfg.get("sample_rate", 12000),
            bit_depth=rec_cfg.get("bit_depth", 16),
            max_duration=rec_cfg.get("max_duration", 300),
        )

    @property
    def is_recording(self) -> bool:
        """是否正在录制。"""
        return self._is_recording

    @property
    def current_path(self) -> Optional[str]:
        """当前录音文件路径。"""
        return self._current_path

    @property
    def recording_duration(self) -> float:
        """当前录音已持续时间（秒）。"""
        if not self._is_recording:
            return 0.0
        return self._samples_written / self.sample_rate

    def start_recording(self, frequency_khz: float, node_name: str,
                        pre_roll_samples: np.ndarray = None) -> str:
        """
        开始录制。

        Args:
            frequency_khz: 当前频率 (kHz)
            node_name: 节点名称
            pre_roll_samples: pre-roll 缓冲区中的音频样本

        Returns:
            录音文件路径
        """
        if self._is_recording:
            self.stop_recording()

        self._current_freq = frequency_khz
        self._current_node = node_name
        self._recording_start_time = datetime.now(timezone.utc)

        # 生成文件名: 时间戳_频率_节点名.wav
        timestamp_str = self._recording_start_time.strftime("%Y%m%d_%H%M%S")
        safe_node = "".join(c if c.isalnum() or c in "-_" else "_"
                           for c in node_name)
        freq_str = f"{frequency_khz:.1f}kHz"
        filename = f"{timestamp_str}_{freq_str}_{safe_node}.wav"
        self._current_path = os.path.join(self.output_dir, filename)

        # 打开 WAV 文件
        self._wav_file = wave.open(self._current_path, 'wb')
        self._wav_file.setnchannels(self._channels)
        self._wav_file.setsampwidth(self.bit_depth // 8)
        self._wav_file.setframerate(self.sample_rate)

        self._samples_written = 0
        self._is_recording = True

        logger.info(f"[REC-START] {filename}")

        # 写入 pre-roll 数据
        if pre_roll_samples is not None and len(pre_roll_samples) > 0:
            self.write_samples(pre_roll_samples)

        return self._current_path

    def write_samples(self, samples: np.ndarray):
        """
        写入音频样本。

        Args:
            samples: float32 音频样本 (-1.0 到 1.0)
        """
        if not self._is_recording or self._wav_file is None:
            return

        # float32 -> int16 PCM
        clipped = np.clip(samples, -1.0, 1.0)
        if self.bit_depth == 16:
            pcm = (clipped * 32767).astype(np.int16)
        else:
            pcm = (clipped * 2147483647).astype(np.int32)

        self._wav_file.writeframes(pcm.tobytes())
        self._samples_written += len(samples)

        # 检查是否超过最大录音时长 → 自动分段
        if self.recording_duration >= self.max_duration:
            logger.info(f"录音达到最大时长 ({self.max_duration}s)，自动分段...")
            segment_info = self.stop_recording()
            # 通知上层此分段已完成（用于保存分段信息到数据库）
            if self._on_segment_complete and segment_info:
                try:
                    self._on_segment_complete(segment_info)
                except Exception as e:
                    logger.error(f"分段回调异常: {e}")
            # 开始新的录音段
            self.start_recording(self._current_freq, self._current_node)
            logger.info(f"新录音段: {self._current_path}")

    def stop_recording(self) -> Optional[dict]:
        """
        停止录制。

        Returns:
            录音信息字典，或 None（如果未在录制）
        """
        if not self._is_recording:
            return None

        info = {
            "path": self._current_path,
            "frequency_khz": self._current_freq,
            "node_name": self._current_node,
            "duration_seconds": self.recording_duration,
            "samples": self._samples_written,
            "start_time": self._recording_start_time.isoformat()
                          if self._recording_start_time else None,
        }

        # 关闭 WAV 文件
        if self._wav_file:
            try:
                self._wav_file.close()
            except Exception as e:
                logger.error(f"关闭 WAV 文件出错: {e}")
            self._wav_file = None

        self._is_recording = False

        logger.info(
            f"[REC-STOP] {os.path.basename(self._current_path)} "
            f"({info['duration_seconds']:.1f}s, "
            f"{self._samples_written} samples)"
        )

        return info

    def __del__(self):
        """确保 WAV 文件被正确关闭。"""
        if self._is_recording:
            self.stop_recording()
