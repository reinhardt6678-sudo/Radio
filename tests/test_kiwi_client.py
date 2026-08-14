"""
test_kiwi_client.py - KiwiSDR 帧解析单元测试

审计发现的缺陷 01 就藏在 _process_audio_data() 里：SND 帧的 body 布局被写成
flags(1) + seq(2,大端) + smeter(2)，而真实布局是 flags(1) + seq(4,小端) +
smeter(2,大端)。偏移 2 字节的后果是每帧多解出 1 个假样本（帧率谐波嗡声），
且 S-meter 列记录的其实是帧计数器的高位字节。

这里用构造好的帧把布局钉死。
"""

import asyncio
import struct

import numpy as np
import pytest

from src.kiwi_client import KiwiSDRClient


# ==================== 帧构造工具 ====================

def make_snd_frame(samples_i16: np.ndarray, seq: int = 0,
                   smeter_raw: int = 0, flags: int = 0) -> bytes:
    """
    按官方 kiwiclient 的布局构造一个 SND 帧。

    tag(3) + flags(1) + seq(4, 小端) + smeter(2, 大端) + PCM(大端 int16)
    """
    body = struct.pack('<BI', flags, seq)
    body += struct.pack('>H', smeter_raw)
    body += samples_i16.astype('>i2').tobytes()
    return b'SND' + body


def feed(client: KiwiSDRClient, frame: bytes) -> list:
    """把一个帧喂进客户端，返回回调收到的样本块列表。"""
    received = []

    async def callback(samples, smeter):
        received.append((samples, smeter))

    client._audio_callback = callback
    asyncio.run(client._process_audio_data(frame))
    return received


@pytest.fixture
def client():
    return KiwiSDRClient("test.example", 8073)


# ==================== 帧解析 ====================

class TestFrameParsing:
    """SND 帧布局解析测试。"""

    def test_sample_count_exact(self, client):
        """解出的样本数必须与写入的完全一致（不能多出假样本）。"""
        samples = np.arange(-256, 256, dtype=np.int16)
        received = feed(client, make_snd_frame(samples, seq=7, smeter_raw=300))

        assert len(received) == 1
        decoded, _ = received[0]
        assert len(decoded) == len(samples), (
            "样本数不符：偏移错误会把 S-meter 的 2 个字节当成一个音频样本"
        )

    def test_sample_values(self, client):
        """样本值应为大端 int16 归一化到 [-1, 1)。"""
        samples = np.array([0, 1000, -1000, 32767, -32768], dtype=np.int16)
        received = feed(client, make_snd_frame(samples, seq=1, smeter_raw=512))

        decoded, _ = received[0]
        np.testing.assert_allclose(decoded, samples / 32768.0, rtol=1e-6)

    def test_smeter_conversion(self, client):
        """S-meter: dBm = 0.1 * (raw & 0x0FFF) - 127。"""
        samples = np.zeros(64, dtype=np.int16)
        feed(client, make_snd_frame(samples, seq=0, smeter_raw=350))

        assert client.smeter == pytest.approx(0.1 * 350 - 127.0)

    def test_smeter_not_polluted_by_seq(self, client):
        """帧计数器变大时不能污染 S-meter（旧代码里正是它冒充了 S-meter）。"""
        samples = np.zeros(64, dtype=np.int16)
        readings = []
        for seq in (0, 65536, 131072, 3_000_000):
            feed(client, make_snd_frame(samples, seq=seq, smeter_raw=270))
            readings.append(client.smeter)

        assert readings == [pytest.approx(-100.0)] * 4

    def test_smeter_masked_to_12_bits(self, client):
        """S-meter 只有 12 位有效，高 4 位应被屏蔽。"""
        samples = np.zeros(32, dtype=np.int16)
        feed(client, make_snd_frame(samples, seq=0, smeter_raw=0xF000 | 500))

        assert client.smeter == pytest.approx(0.1 * 500 - 127.0)

    def test_sequence_number_little_endian(self, client):
        """seq 是 4 字节小端。"""
        samples = np.zeros(32, dtype=np.int16)
        feed(client, make_snd_frame(samples, seq=70000, smeter_raw=0))

        assert client.last_seq == 70000

    def test_dropped_frames_counted(self, client):
        """seq 跳变说明丢帧，应被统计出来。"""
        samples = np.zeros(32, dtype=np.int16)
        for seq in (10, 11, 15):  # 12/13/14 丢失
            feed(client, make_snd_frame(samples, seq=seq, smeter_raw=0))

        assert client.dropped_frames == 3

    def test_no_frame_rate_artifact(self, client):
        """
        连续解多帧后拼接的音频里不应出现帧率谐波。

        每帧多注入 1 个假样本会在 12000/512 = 23.4375 Hz 及其谐波上产生尖峰，
        这是审计在全部 747 段录音里量到的那个嗡声。
        """
        sr = 12000
        block = 512
        n_frames = 120
        tone_hz = 1200.0

        chunks = []
        for i in range(n_frames):
            t = (np.arange(block) + i * block) / sr
            pcm = (np.sin(2 * np.pi * tone_hz * t) * 8000).astype(np.int16)
            received = feed(client, make_snd_frame(
                pcm, seq=i, smeter_raw=300 + i  # S-meter 持续抖动
            ))
            chunks.append(received[0][0])

        audio = np.concatenate(chunks)
        assert len(audio) == block * n_frames

        spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
        freqs = np.fft.rfftfreq(len(audio), 1 / sr)

        frame_rate = sr / block  # 23.4375 Hz
        median_level = float(np.median(spectrum))
        for harmonic in range(1, 6):
            f0 = frame_rate * harmonic
            band = (freqs > f0 - 2) & (freqs < f0 + 2)
            peak = float(np.max(spectrum[band]))
            assert peak < median_level * 50, (
                f"{f0:.1f} Hz 处出现帧率谐波，说明每帧仍在注入假样本"
            )

    def test_short_frame_ignored(self, client):
        """长度不足的帧不应崩溃，也不应产生回调。"""
        received = feed(client, b'SND' + b'\x00' * 5)
        assert received == []

    def test_odd_pcm_length_truncated(self, client):
        """PCM 字节数为奇数时丢弃最后半个样本，而不是报错。"""
        samples = np.arange(10, dtype=np.int16)
        frame = make_snd_frame(samples, seq=1, smeter_raw=100) + b'\x7f'
        received = feed(client, frame)

        decoded, _ = received[0]
        assert len(decoded) == len(samples)


class TestFrameDispatch:
    """帧类型分发测试。"""

    def test_audio_tag_recognised(self, client):
        assert client._is_audio_frame(b'SND' + b'\x00' * 16)

    def test_msg_frame_not_treated_as_audio(self, client):
        """MSG 帧以 'S' 之外的字节开头，但历史代码只比较首字节。"""
        assert not client._is_audio_frame(b'MSG client_public_ip=1.2.3.4')

    def test_status_frame_not_treated_as_audio(self, client):
        """以 'S' 开头但不是 SND 的帧不能当音频解析。"""
        assert not client._is_audio_frame(b'STA something')
