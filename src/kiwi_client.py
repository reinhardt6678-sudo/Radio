"""
kiwi_client.py - 轻量级 KiwiSDR WebSocket 客户端

通过 WebSocket 连接到 KiwiSDR 接收节点，接收实时音频流。
实现了 KiwiSDR 的握手协议和音频数据帧解析。
"""

import asyncio
import struct
import logging
import time
import numpy as np
from typing import Optional, Callable, Awaitable

from .modes import demod_filter, normalize_mode

try:
    import websockets
    from websockets.asyncio.client import connect as ws_connect
except ImportError:
    raise ImportError("请安装 websockets: pip install websockets")

logger = logging.getLogger(__name__)


class KiwiSDRClient:
    """
    KiwiSDR WebSocket 客户端。

    通过 WebSocket 协议连接到 KiwiSDR 节点，支持：
    - 频率调谐
    - 模式切换 (AM/USB/LSB/CW)
    - 实时音频流接收
    - S-meter 信号强度读取
    """

    # KiwiSDR 帧标识 - 每帧前 3 字节是 tag，不是 1 字节
    # (只比首字节会把 'STA' 之类的帧误判成音频)
    TAG_AUDIO = b'SND'       # 音频样本数据
    TAG_MSG = b'MSG'         # 服务器消息
    TAG_WATERFALL = b'W/F'   # waterfall 数据 (本项目不使用)

    # SND 帧头部长度: tag(3) + flags(1) + seq(4) + smeter(2)
    SND_HEADER_LEN = 10

    # S-meter 原始值是 12 位
    SMETER_MASK = 0x0FFF
    SMETER_BIAS_DB = 127.0   # dBm = 0.1 * raw - 127

    # 默认连接参数
    DEFAULT_PORT = 8073
    SAMPLE_RATE = 12000      # KiwiSDR 默认音频采样率

    def __init__(self, host: str, port: int = 8073,
                 password: str = None):
        """
        初始化 KiwiSDR 客户端。

        Args:
            host: KiwiSDR 节点地址
            port: WebSocket 端口 (默认 8073)
            password: 节点密码 (如需要)
        """
        self.host = host
        self.port = port
        self.password = password or ""
        self.ws: Optional[websockets.ClientProtocol] = None
        self.connected = False
        self._last_seq: Optional[int] = None
        self._dropped_frames = 0
        self._audio_frames = 0
        self._last_smeter = -127.0  # dBm
        self._current_freq = 0.0
        self._current_mode = "USB"
        self._audio_callback: Optional[Callable] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None

    @property
    def smeter(self) -> float:
        """最近的 S-meter 读数 (dBm)。"""
        return self._last_smeter

    @property
    def last_seq(self) -> Optional[int]:
        """最近一帧的序列号 (服务器侧帧计数器)。"""
        return self._last_seq

    @property
    def dropped_frames(self) -> int:
        """按 seq 跳变统计出的丢帧数。"""
        return self._dropped_frames

    @property
    def audio_frames(self) -> int:
        """已收到的音频帧数。"""
        return self._audio_frames

    # KiwiSDR 服务器拒绝消息 - 必须精确匹配 MSG 前缀
    # (不能用模糊匹配，因为 load_cfg 包含完整配置 JSON 会误报)
    _REJECT_MESSAGES = [
        "MSG too_busy",      # 服务器满员
        "MSG redirect",      # 重定向到其他节点
        "MSG down",          # 服务器关闭
        "MSG locked",        # 需要密码
        "MSG ip_limit",      # IP 连接数限制
    ]

    async def connect(self, timeout: float = 10.0) -> bool:
        """
        连接到 KiwiSDR 节点并完成握手。

        Args:
            timeout: 连接超时时间（秒）

        Returns:
            连接是否成功
        """
        uri = f"ws://{self.host}:{self.port}/kiwi/{int(time.time())}/SND"
        logger.info(f"正在连接 KiwiSDR: {uri}")

        try:
            self.ws = await asyncio.wait_for(
                ws_connect(
                    uri,
                    additional_headers={"Origin": f"http://{self.host}:{self.port}"},
                    max_size=2**20,  # 1MB max message size
                    ping_interval=None,  # 我们自己管理 keepalive
                ),
                timeout=timeout
            )

            # KiwiSDR 握手序列
            await self._send_cmd("SET auth t=kiwi p=%s" % self.password)

            # 等待服务器响应 - 收集握手阶段所有消息
            auth_ok = False
            deadline = time.time() + 5.0
            while time.time() < deadline:
                try:
                    msg = await asyncio.wait_for(self.ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                msg_text = None
                if isinstance(msg, str):
                    msg_text = msg
                elif isinstance(msg, bytes) and len(msg) > 1:
                    try:
                        msg_text = msg.decode('ascii', errors='ignore')
                    except Exception:
                        pass

                if msg_text:
                    logger.debug(f"握手消息: {msg_text[:120]}")

                    # 在 auth 确认之前检查服务器拒绝
                    # (只匹配精确的 MSG 前缀，避免 load_cfg 配置数据误报)
                    if not auth_ok:
                        for reject_msg in self._REJECT_MESSAGES:
                            if msg_text.startswith(reject_msg):
                                logger.error(f"服务器拒绝连接: {msg_text[:100]}")
                                await self.ws.close()
                                return False

                    # 检查握手成功标识
                    if "client_public_ip" in msg_text:
                        auth_ok = True
                        logger.debug("收到 auth 响应")
                    if "rx_chans" in msg_text:
                        auth_ok = True
                        logger.debug(f"收到频道信息: {msg_text[:80]}")

                    # auth 成功后，收到 audio_init 表示服务器准备好了
                    if auth_ok and "audio_init" in msg_text:
                        break
                    # 超大的 load_cfg 消息不需要等完
                    if auth_ok and msg_text.startswith("MSG load_cfg"):
                        continue

            if not auth_ok:
                logger.warning(f"握手超时(未收到 auth 响应)，继续尝试...")

            # 设置音频参数 (out=12000 是 KiwiSDR 原生采样率)
            await self._send_cmd("SET AR OK in=12000 out=12000")
            await self._send_cmd("SET squelch=0 max=0")
            await self._send_cmd("SET genattn=0")
            await self._send_cmd("SET gen=0 mix=-1")
            await self._send_cmd("SET agc=1 hang=0 thresh=-100 slope=6 decay=1000 manGain=50")
            await self._send_cmd("SET compression=0")

            self.connected = True
            logger.info(f"已连接到 KiwiSDR: {self.host}:{self.port}")

            # 启动 keepalive
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())

            return True

        except asyncio.TimeoutError:
            logger.error(f"连接超时: {self.host}:{self.port}")
            return False
        except websockets.exceptions.ConnectionClosed as e:
            logger.error(f"连接被服务器关闭: {self.host}:{self.port} - {e}")
            return False
        except Exception as e:
            logger.error(f"连接失败: {self.host}:{self.port} - {e}")
            return False

    async def set_frequency(self, freq_khz: float, mode: str = "USB"):
        """
        设置接收频率和模式。

        Args:
            freq_khz: 频率 (kHz)
            mode: 调制模式 (AM/USB/LSB/CW/CWN)
        """
        if not self.connected:
            raise RuntimeError("未连接到 KiwiSDR")

        self._current_freq = freq_khz
        self._current_mode = normalize_mode(mode)

        # 带通滤波器和分析端共用 modes.py 里的同一张表，
        # 保证 analyzer 算 SNR 时用的通带就是这里真正设下去的通带
        low_cut, high_cut = demod_filter(self._current_mode)

        cmd = (f"SET mod={self._current_mode.lower()} "
               f"low_cut={low_cut:.0f} high_cut={high_cut:.0f} freq={freq_khz:.3f}")
        await self._send_cmd(cmd)
        logger.info(f"调谐到: {freq_khz} kHz ({self._current_mode})")

    async def start_audio_stream(self, callback: Callable[[np.ndarray, float], Awaitable[None]]):
        """
        开始接收音频流。

        Args:
            callback: 音频回调函数，参数为 (samples: np.ndarray, smeter: float)
                      samples 是 float32 归一化音频样本数组
        """
        self._audio_callback = callback
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def stop_audio_stream(self):
        """停止音频流接收。"""
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None
        self._audio_callback = None

    async def disconnect(self):
        """断开 KiwiSDR 连接。"""
        self.connected = False

        if self._keepalive_task:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None

        logger.info(f"已断开 KiwiSDR: {self.host}:{self.port}")

    # ==================== 内部方法 ====================

    async def _send_cmd(self, cmd: str):
        """发送文本命令到 KiwiSDR。"""
        if self.ws:
            await self.ws.send(cmd)
            logger.debug(f"发送: {cmd}")

    async def _wait_for_msg(self, prefix: str, timeout: float = 5.0) -> Optional[str]:
        """等待特定前缀的消息。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(self.ws.recv(), timeout=1.0)
                if isinstance(msg, str):
                    logger.debug(f"收到文本: {msg[:100]}")
                    if prefix in msg:
                        return msg
                elif isinstance(msg, bytes):
                    # 握手阶段可能收到二进制数据，忽略
                    tag = chr(msg[0]) if len(msg) > 0 else '?'
                    logger.debug(f"收到二进制: tag={tag}, len={len(msg)}")
                    # 检查是否是包含文本的 MSG
                    if len(msg) > 1:
                        try:
                            text = msg[1:].decode('ascii', errors='ignore')
                            if prefix in text:
                                return text
                        except Exception:
                            pass
            except asyncio.TimeoutError:
                continue
        return None

    async def _receive_loop(self):
        """主接收循环 - 持续接收并解析音频数据。"""
        logger.info("音频接收循环启动")
        _frame_count = 0
        _last_data_time = time.time()  # 看门狗: 记录最后收到数据的时间
        _WATCHDOG_TIMEOUT = 30.0       # 30 秒无数据判定连接丢失
        try:
            while self.connected and self.ws:
                try:
                    msg = await asyncio.wait_for(self.ws.recv(), timeout=5.0)
                    _last_data_time = time.time()  # 收到任何数据都刷新
                except asyncio.TimeoutError:
                    # 检查看门狗超时
                    if time.time() - _last_data_time > _WATCHDOG_TIMEOUT:
                        logger.warning(
                            f"{_WATCHDOG_TIMEOUT:.0f} 秒无数据，判定连接丢失 "
                            f"(共收到 {_frame_count} 帧)"
                        )
                        self.connected = False
                        return
                    continue

                if isinstance(msg, bytes) and len(msg) > 0:
                    if self._is_audio_frame(msg):
                        # 'SND' = 音频数据帧
                        _frame_count += 1
                        if _frame_count == 1:
                            logger.info("收到首个音频帧，数据流正常")
                        await self._process_audio_data(msg)

                    else:
                        # 其他二进制帧 - KiwiSDR 把 MSG 等也以二进制发送
                        # 尝试完整解码为文本来检查内容
                        try:
                            text = msg.decode('ascii', errors='ignore')
                        except Exception:
                            text = ""

                        if text:
                            # 检查服务器拒绝/断开消息 (精确匹配 MSG 前缀)
                            for reject_msg in self._REJECT_MESSAGES:
                                if text.startswith(reject_msg):
                                    logger.warning(f"服务器通知: {text[:120]}")
                                    self.connected = False
                                    return

                            # 记录服务器消息 (MSG 类型)
                            if "MSG" in text or "ERR" in text:
                                logger.debug(f"服务器消息: {text[:120]}")

                elif isinstance(msg, str):
                    # 文本消息 (少数 KiwiSDR 版本可能使用文本)
                    for keyword in self._REJECT_MESSAGES:
                        if keyword in msg:
                            logger.warning(f"服务器通知(text): {msg[:120]}")
                            self.connected = False
                            return
                    logger.debug(f"文本消息: {msg[:80]}")

        except asyncio.CancelledError:
            logger.info(f"音频接收循环取消 (共收到 {_frame_count} 帧)")
            raise
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"KiwiSDR 连接已关闭: {e} (共收到 {_frame_count} 帧)")
            self.connected = False
        except Exception as e:
            logger.error(f"接收循环异常: {e} (共收到 {_frame_count} 帧)")
            self.connected = False

    def _is_audio_frame(self, data: bytes) -> bool:
        """判断是否是音频帧 (必须比较完整的 3 字节 tag)。"""
        return len(data) >= 3 and data[0:3] == self.TAG_AUDIO

    async def _process_audio_data(self, data: bytes):
        """
        解析 KiwiSDR 音频数据帧。

        官方 kiwiclient 的布局 (kiwi/client.py::_process_aud):
            flags, seq = struct.unpack('<BI', body[0:5])
            smeter,    = struct.unpack('>H',  body[5:7])
            data       = body[7:]

        即:
        - tag  = data[0:3]  -> 'SND' (3 字节)
        - body = data[3:]:
          - body[0]:   flags
          - body[1:5]: 序列号 (little-endian uint32, 4 字节)
          - body[5:7]: S-meter (big-endian uint16, 低 12 位有效)
          - body[7:]:  PCM 音频样本 (big-endian int16, 未压缩)

        注意 seq 是 4 字节小端而非 2 字节大端。按 2 字节读会让整个帧偏移
        2 字节：S-meter 位置读到的是帧计数器的高位字节，音频起点提前 2 字节
        导致每帧多出 1 个假样本 —— 那 2 个 S-meter 字节被当成了一个 int16，
        在录音里表现为 12000/512 = 23.4375 Hz 的帧率谐波嗡声。
        """
        if len(data) < self.SND_HEADER_LEN + 2:
            return

        body = data[3:]
        flags, seq = struct.unpack('<BI', body[0:5])
        smeter_raw = struct.unpack('>H', body[5:7])[0]

        # S-meter 原始值转换为 dBm
        # KiwiSDR 服务器发的是 (dBm + 127) * 10，取低 12 位
        self._last_smeter = 0.1 * (smeter_raw & self.SMETER_MASK) - self.SMETER_BIAS_DB

        # 按 seq 统计丢帧 (seq 是 uint32，会回绕)
        if self._last_seq is not None:
            gap = (seq - self._last_seq) & 0xFFFFFFFF
            if 1 < gap < 1000:
                self._dropped_frames += gap - 1
        self._last_seq = seq
        self._audio_frames += 1

        # 提取音频样本: body[7:] = data[10:]
        audio_bytes = body[7:]

        # 确保数据长度是偶数（int16 样本）
        if len(audio_bytes) % 2 != 0:
            audio_bytes = audio_bytes[:-1]

        if len(audio_bytes) < 2:
            return

        # 解析 big-endian int16 样本
        try:
            samples = np.frombuffer(audio_bytes, dtype='>i2').astype(np.float32)
            # 归一化到 [-1.0, 1.0]
            samples = samples / 32768.0
        except ValueError:
            return

        # 调用回调
        if self._audio_callback and len(samples) > 0:
            try:
                await self._audio_callback(samples, self._last_smeter)
            except Exception as e:
                logger.error(f"音频回调异常: {e}")

    async def _keepalive_loop(self):
        """发送 keepalive 消息防止连接超时。"""
        try:
            while self.connected:
                await asyncio.sleep(5)
                if self.ws and self.connected:
                    await self._send_cmd("SET keepalive")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Keepalive 异常: {e}")


async def test_connection(host: str, port: int = 8073,
                          timeout: float = 10.0) -> dict:
    """
    快速测试 KiwiSDR 节点可用性。

    Args:
        host: 节点地址
        port: 端口
        timeout: 超时时间

    Returns:
        包含 available, latency_ms, error 的字典
    """
    result = {
        "host": host,
        "port": port,
        "available": False,
        "latency_ms": None,
        "error": None
    }

    start = time.time()
    client = KiwiSDRClient(host, port)

    try:
        success = await client.connect(timeout=timeout)
        elapsed = (time.time() - start) * 1000

        if success:
            result["available"] = True
            result["latency_ms"] = round(elapsed, 1)
        else:
            result["error"] = "连接握手失败"

    except Exception as e:
        result["error"] = str(e)
    finally:
        await client.disconnect()

    return result
