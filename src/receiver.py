"""
receiver.py - 信号接收控制器

协调 KiwiSDR 客户端、静噪检测器和录制器的工作流程，
管理多频率监听和扫描。
"""

import asyncio
import logging
import time
import numpy as np
from datetime import datetime, timezone
from typing import List, Dict, Optional, Callable

from .kiwi_client import KiwiSDRClient
from .squelch import SquelchDetector
from .recorder import AudioRecorder
from .analyzer import SignalAnalyzer
from .db import Database

logger = logging.getLogger(__name__)


class FrequencyTarget:
    """监听频率目标。"""

    def __init__(self, freq_khz: float, mode: str = "USB",
                 description: str = "", network: str = "",
                 priority: str = "medium", active_hours: str = ""):
        self.freq_khz = freq_khz
        self.mode = mode
        self.description = description
        self.network = network
        self.priority = priority
        self.active_hours = active_hours

    def __repr__(self):
        return f"<{self.freq_khz} kHz {self.mode} ({self.description})>"


class SignalReceiver:
    """
    信号接收控制器。

    管理从 KiwiSDR 节点接收音频、检测信号活动、
    录制和分析的完整工作流程。
    """

    def __init__(self, config: dict, db: Database):
        """
        初始化接收控制器。

        Args:
            config: 完整配置字典
            db: 数据库实例
        """
        self.config = config
        self.db = db

        # 接收参数
        recv_cfg = config.get("receiver", {})
        self.sample_rate = recv_cfg.get("sample_rate", 12000)
        self.scan_dwell_time = recv_cfg.get("scan_dwell_time", 30)

        # 静噪配置
        sq_cfg = config.get("squelch", {})
        self.squelch_config = sq_cfg

        # 录制配置
        rec_cfg = config.get("recording", {})
        self.recording_config = rec_cfg

        # 分析器（使用统一工厂方法，避免配置默认值不一致）
        self.analyzer = SignalAnalyzer.from_config(config)

        # 运行状态
        self._running = False
        self._session_id: Optional[int] = None
        self._total_signals = 0
        self._status_callback: Optional[Callable] = None

    async def monitor(self, node: dict, frequencies: List[FrequencyTarget],
                      duration: float = 0,
                      status_callback: Callable = None):
        """
        固定监听模式 - 在指定频率上持续监听。

        Args:
            node: KiwiSDR 节点信息
            frequencies: 要监听的频率列表
            duration: 监听时长（秒），0 = 无限
            status_callback: 状态更新回调
        """
        self._status_callback = status_callback
        self._running = True
        self._total_signals = 0

        host = node["host"]
        port = node.get("port", 8073)
        node_name = node.get("name", host)

        # 创建会话
        freq_list = [f.freq_khz for f in frequencies]
        self._session_id = self.db.create_session(
            node_host=host,
            node_name=node_name,
            frequencies=freq_list,
            notes=f"监听模式: {len(frequencies)} 个频率"
        )

        logger.info(f"=== 开始监听会话 #{self._session_id} ===")
        logger.info(f"节点: {node_name} ({host}:{port})")
        logger.info(f"频率: {', '.join(f'{f.freq_khz} kHz' for f in frequencies)}")

        start_time = time.time()

        try:
            # 如果只有一个频率，直接固定监听
            if len(frequencies) == 1:
                await self._monitor_single_frequency(
                    host, port, node_name, frequencies[0], duration
                )
            else:
                # 多频率轮询监听
                await self._monitor_multiple_frequencies(
                    host, port, node_name, frequencies, duration
                )

        except asyncio.CancelledError:
            logger.info("监听被取消")
        except Exception as e:
            logger.error(f"监听异常: {e}", exc_info=True)
        finally:
            elapsed = time.time() - start_time
            self.db.end_session(self._session_id,
                               status="completed" if self._running else "cancelled")
            self._running = False
            logger.info(
                f"=== 监听会话结束: 时长 {elapsed:.0f}s, "
                f"检测到 {self._total_signals} 个信号 ==="
            )

    async def scan(self, node: dict, frequencies: List[FrequencyTarget],
                   dwell_time: float = None) -> List[Dict]:
        """
        扫描模式 - 快速扫描所有频率检测活动。

        Args:
            node: KiwiSDR 节点
            frequencies: 频率列表
            dwell_time: 每个频率停留时间（秒）

        Returns:
            各频率扫描结果
        """
        dwell = dwell_time or self.scan_dwell_time
        host = node["host"]
        port = node.get("port", 8073)
        node_name = node.get("name", host)
        results = []

        logger.info(f"[SCAN] freq_count={len(frequencies)}, dwell={dwell}s")

        client = KiwiSDRClient.from_config(host, port, self.config)
        connected = await client.connect()

        if not connected:
            logger.error(f"无法连接到节点: {node_name}")
            return results

        try:
            for freq_target in frequencies:
                logger.info(f"  扫描 {freq_target.freq_khz} kHz ({freq_target.mode})...")

                await client.set_frequency(freq_target.freq_khz, freq_target.mode)

                # 收集音频数据
                audio_buffer = []

                async def collect_audio(samples, smeter):
                    audio_buffer.append(samples.copy())

                await client.start_audio_stream(collect_audio)
                await asyncio.sleep(dwell)
                await client.stop_audio_stream()

                # 分析收集到的数据
                if audio_buffer:
                    all_samples = np.concatenate(audio_buffer)
                    rms = float(np.sqrt(np.mean(all_samples ** 2)))
                    analysis = self.analyzer.analyze_samples(
                        all_samples, self.sample_rate, mode=freq_target.mode
                    )

                    scan_result = {
                        "frequency_khz": freq_target.freq_khz,
                        "mode": freq_target.mode,
                        "description": freq_target.description,
                        "network": freq_target.network,
                        "rms": rms,
                        "snr_db": analysis.get("snr_db", 0),
                        "bandwidth_hz": analysis.get("bandwidth_hz", 0),
                        "estimated_modulation": analysis.get("estimated_modulation", "UNKNOWN"),
                        "modulation_confidence": analysis.get("modulation_confidence", 0.0),
                        "noise_floor_db": analysis.get("noise_floor_db"),
                        "has_signal": rms > self.squelch_config.get("open_threshold", 0.02),
                        "s_meter_dbm": client.smeter,
                    }
                    results.append(scan_result)

                    status = "[ACTIVE]" if scan_result["has_signal"] else "[QUIET]"
                    logger.info(
                        f"    {status} | RMS={rms:.4f} | "
                        f"SNR={analysis.get('snr_db', 0):.1f}dB"
                    )

        finally:
            await client.disconnect()

        return results

    async def _monitor_single_frequency(self, host: str, port: int,
                                         node_name: str,
                                         freq: FrequencyTarget,
                                         duration: float):
        """单频率持续监听。"""
        client = KiwiSDRClient.from_config(host, port, self.config)
        connected = await client.connect()

        if not connected:
            logger.error(f"无法连接到节点: {node_name}")
            return

        try:
            await client.set_frequency(freq.freq_khz, freq.mode)

            # 创建静噪检测器（使用统一工厂方法）
            squelch = SquelchDetector.from_config(self.config)

            # 创建录音器
            recorder = AudioRecorder.from_config(self.config)

            # 信号分析用的缓冲区
            signal_audio_buffer = []

            def on_signal_open(pre_roll_data, start_time):
                """信号开始回调。"""
                signal_audio_buffer.clear()
                if len(pre_roll_data) > 0:
                    signal_audio_buffer.append(pre_roll_data.copy())
                recorder.start_recording(freq.freq_khz, node_name, pre_roll_data)
                self._report_status(f"[SIGNAL-ON] {freq.freq_khz} kHz")

            def on_signal_audio(samples):
                """信号活跃期间音频回调。"""
                signal_audio_buffer.append(samples.copy())
                recorder.write_samples(samples)

            def on_signal_close(signal_duration, peak_rms, avg_rms):
                """信号结束回调。"""
                rec_info = recorder.stop_recording()
                self._total_signals += 1

                # 保存信号记录到数据库
                signal_id = self.db.record_signal(
                    session_id=self._session_id,
                    frequency_khz=freq.freq_khz,
                    mode=freq.mode,
                    node_host=host,
                    node_name=node_name,
                    duration_seconds=signal_duration,
                    peak_rms=peak_rms,
                    avg_rms=avg_rms,
                    s_meter_dbm=client.smeter,
                    recording_path=rec_info["path"] if rec_info else None,
                    description=freq.description,
                    network=freq.network,
                )

                # 对录制的信号做频谱分析（使用统一方法避免重复代码）
                # 传入解调模式: SNR 和调制判定都要按对应通带来算
                self.analyzer.analyze_and_save(
                    self.db, signal_id, signal_audio_buffer, self.sample_rate,
                    mode=freq.mode
                )

                self._report_status(
                    f"[SIGNAL-OFF] #{self._total_signals}: "
                    f"{signal_duration:.1f}s, peak_RMS={peak_rms:.4f}"
                )

            squelch.set_callbacks(
                on_open=on_signal_open,
                on_close=on_signal_close,
                on_audio=on_signal_audio,
            )

            # 音频流处理
            start_time = time.time()

            async def process_audio(samples, smeter):
                squelch.process(samples, smeter)

            await client.start_audio_stream(process_audio)

            # 等待直到 duration 到期或被取消
            report_interval = 30.0
            next_report = start_time + report_interval
            while self._running:
                await asyncio.sleep(1)

                # 定期状态报告。
                # 用下一次报告的时间戳来判断，不能写成 int(elapsed) % 30 == 0:
                # sleep(1) 每轮都会多走几毫秒，int(elapsed) 偶尔会从 29 跳到 31，
                # 那一次报告就被整个跳过了。
                now = time.time()
                elapsed = now - start_time
                if now >= next_report:
                    next_report = now + report_interval
                    status = squelch.get_status()
                    # 带上音频帧数: RMS 恒为 0 且帧数不涨 = 根本没有音频进来，
                    # 和"有音频但没有信号"是两回事，日志里必须能分得开。
                    # 再带上底噪和生效阈值: "阈值压在底噪以下 → 静噪一直开着 →
                    # 一条记录都不出"这种情况，光看 signals=0 是看不出来的。
                    self._report_status(
                        f"[MONITORING] {freq.freq_khz} kHz | "
                        f"RMS={status['current_rms']:.4f} | "
                        f"底噪={status['noise_floor']:.4f} | "
                        f"阈值={status['effective_open']:.4f}/"
                        f"{status['effective_close']:.4f} | "
                        f"静噪={'开' if status['is_open'] else '关'} | "
                        f"signals={self._total_signals} | "
                        f"frames={client.audio_frames} "
                        f"(dropped={client.dropped_frames}) | "
                        f"elapsed={elapsed:.0f}s"
                    )

                if duration > 0 and elapsed >= duration:
                    logger.info(f"已达到设定的监听时长 ({duration}s)")
                    break

                if not client.connected:
                    if client.audio_frames == 0:
                        logger.warning(
                            "KiwiSDR 连接结束，且全程没有收到任何音频帧 —— "
                            "换个节点重试，或用 python diagnose_rms.py 单独验一次链路"
                        )
                    else:
                        logger.warning(
                            f"KiwiSDR 连接断开 (共收到 {client.audio_frames} 个音频帧)。"
                            "命令行 monitor 不会自动重连，需要长时间监听请用 "
                            "python main.py web"
                        )
                    break

            # 停止并清理。
            # 先让静噪正常收尾，正在录的那段信号才会入库
            squelch.force_close("monitor-stop")
            await client.stop_audio_stream()
            if recorder.is_recording:
                recorder.stop_recording()

        finally:
            await client.disconnect()

    async def _monitor_multiple_frequencies(self, host: str, port: int,
                                             node_name: str,
                                             frequencies: List[FrequencyTarget],
                                             duration: float):
        """多频率轮询监听。"""
        client = KiwiSDRClient.from_config(host, port, self.config)
        connected = await client.connect()

        if not connected:
            logger.error(f"无法连接到节点: {node_name}")
            return

        try:
            start_time = time.time()
            freq_idx = 0
            dwell = self.scan_dwell_time

            while self._running:
                freq = frequencies[freq_idx % len(frequencies)]
                logger.info(f"[SWITCH] {freq.freq_khz} kHz ({freq.description})")

                await client.set_frequency(freq.freq_khz, freq.mode)

                # 创建临时静噪检测器和录音器（使用统一工厂方法）
                squelch = SquelchDetector.from_config(self.config)
                squelch.tail_time = min(squelch.tail_time, dwell / 3)

                recorder = AudioRecorder.from_config(self.config)

                signal_audio_buffer = []

                def make_callbacks(f, r, buf):
                    def on_open(pre_roll, start):
                        buf.clear()
                        if len(pre_roll) > 0:
                            buf.append(pre_roll.copy())
                        r.start_recording(f.freq_khz, node_name, pre_roll)

                    def on_audio(samples):
                        buf.append(samples.copy())
                        r.write_samples(samples)

                    def on_close(dur, peak, avg):
                        rec_info = r.stop_recording()
                        self._total_signals += 1
                        sig_id = self.db.record_signal(
                            session_id=self._session_id,
                            frequency_khz=f.freq_khz, mode=f.mode,
                            node_host=host, node_name=node_name,
                            duration_seconds=dur, peak_rms=peak, avg_rms=avg,
                            s_meter_dbm=client.smeter,
                            recording_path=rec_info["path"] if rec_info else None,
                            description=f.description, network=f.network,
                        )
                        self.analyzer.analyze_and_save(
                            self.db, sig_id, buf, self.sample_rate, mode=f.mode
                        )

                    return on_open, on_audio, on_close

                on_open, on_audio, on_close = make_callbacks(
                    freq, recorder, signal_audio_buffer
                )
                squelch.set_callbacks(on_open=on_open, on_close=on_close, on_audio=on_audio)

                async def process_audio(samples, smeter):
                    squelch.process(samples, smeter)

                await client.start_audio_stream(process_audio)
                await asyncio.sleep(dwell)
                # 切频率前先收尾，否则这一段的信号记录会被直接丢掉
                squelch.force_close("frequency-switch")
                await client.stop_audio_stream()

                if recorder.is_recording:
                    recorder.stop_recording()

                freq_idx += 1

                # 检查持续时间
                elapsed = time.time() - start_time
                if duration > 0 and elapsed >= duration:
                    break
                if not client.connected:
                    break

        finally:
            await client.disconnect()

    def stop(self):
        """停止接收。"""
        self._running = False

    def _report_status(self, message: str):
        """输出状态信息。"""
        logger.info(message)
        if self._status_callback:
            try:
                self._status_callback(message)
            except Exception:
                pass
