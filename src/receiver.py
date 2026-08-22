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
from .node_manager import (
    NODE_QUALITY_MIN_SNR_DB,
    node_quality_tier,
)

logger = logging.getLogger(__name__)

# --- 断线重连参数 ---
# 公共 KiwiSDR 节点随时会把人踢下来: K1VL 是 ip_limit=240 (单 IP 每天 240 分钟),
# 忙的节点直接回 too_busy。以前这里掉线就退出，一次掉线能把后面几个小时整段空掉,
# 所以命令行 monitor 也必须自己重连并换节点。
RECONNECT_BASE_BACKOFF = 5.0   # 首次重连等待秒数
RECONNECT_MAX_BACKOFF = 60.0   # 退避上限
MAX_NODE_FAILURES = 3          # 同一节点连续失败几次后换下一个
HEALTHY_LEG_SECONDS = 60.0     # 连上后至少听满这么久，才算这个节点是好的

# 被迫离开首选节点后，每隔这么久回去试一次。
# 节点接收质量的分档规则在 node_manager 里，收发两边共用一套。
PREFERRED_RETRY_SECONDS = 30 * 60


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
        self._left_preferred_at: Optional[float] = None  # 何时被迫离开首选节点

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

        # 会话只建一次: 重连和换节点都算同一次监听，
        # 否则一天下来数据库里会散落一堆几分钟的碎会话，统计全乱
        freq_list = [f.freq_khz for f in frequencies]
        self._session_id = self.db.create_session(
            node_host=host,
            node_name=node_name,
            frequencies=freq_list,
            notes=f"监听模式: {len(frequencies)} 个频率 (自动重连)"
        )

        logger.info(f"=== 开始监听会话 #{self._session_id} ===")
        logger.info(f"节点: {node_name} ({host}:{port})")
        logger.info(f"频率: {', '.join(f'{f.freq_khz} kHz' for f in frequencies)}")

        start_time = time.time()
        reconnect_count = 0
        consecutive_failures = 0
        tried_hosts = set()

        # 首选节点 = 这轮监听一开始挑中的那个。被迫换走之后要定期回来看看，
        # 否则就会像 2026-08-21 那次: 08:26 掉到瑞士节点，原节点早恢复了也不回去，
        # 5.5 小时一条真信号都没有
        preferred_node = node
        self._left_preferred_at = None

        try:
            # ===== 断线重连主循环 =====
            # 一次 _monitor_* 调用只对应一条连接，它返回的原因决定要不要再连一次
            while self._running:
                host = node["host"]
                port = node.get("port", 8073)
                node_name = node.get("name", host)
                tried_hosts.add(host)

                # duration 是整轮监听的总时长，重连后要接着扣，不能每次从头算
                remaining = duration
                if duration > 0:
                    remaining = duration - (time.time() - start_time)
                    if remaining <= 0:
                        break

                leg_start = time.time()
                try:
                    if len(frequencies) == 1:
                        reason = await self._monitor_single_frequency(
                            host, port, node_name, frequencies[0], remaining
                        )
                    else:
                        reason = await self._monitor_multiple_frequencies(
                            host, port, node_name, frequencies, remaining
                        )
                except asyncio.CancelledError:
                    logger.info("监听被取消")
                    break
                except Exception as e:
                    logger.error(f"监听异常: {e}", exc_info=True)
                    reason = "error"

                if reason in ("duration", "stopped") or not self._running:
                    break

                if reason == "try_preferred":
                    node = preferred_node
                    self._left_preferred_at = None
                    consecutive_failures = 0
                    tried_hosts = {preferred_node["host"]}
                    logger.info(
                        f"回到首选节点: "
                        f"{preferred_node.get('name', preferred_node['host'])}"
                    )
                    continue  # 主动回去，不用退避

                # 连上就被踢 (too_busy) 和压根连不上要一样对待: 都不重置退避,
                # 否则会在同一个坏节点上无退避地空转
                if time.time() - leg_start >= HEALTHY_LEG_SECONDS:
                    consecutive_failures = 0
                    reconnect_count = 0
                    tried_hosts = {host}
                else:
                    consecutive_failures += 1

                if consecutive_failures >= MAX_NODE_FAILURES:
                    alt = self._pick_alternative_node(node, tried_hosts)
                    if alt:
                        if alt["host"] in tried_hosts:
                            # 一圈节点全试过了，清空重来
                            tried_hosts.clear()
                        if (node["host"] == preferred_node["host"]
                                and self._left_preferred_at is None):
                            # 只在真正离开首选节点那一刻记时，别每次换节点都重置
                            self._left_preferred_at = time.time()
                        node = alt
                        consecutive_failures = 0
                        logger.info(f"切换到备用节点: {alt.get('name', alt['host'])}")
                    else:
                        logger.warning("没有别的节点可换，继续重试当前节点")

                backoff = min(
                    RECONNECT_BASE_BACKOFF * (2 ** min(reconnect_count, 5)),
                    RECONNECT_MAX_BACKOFF,
                )
                reconnect_count += 1
                self._report_status(
                    f"[RECONNECT] {reason} | 等待 {backoff:.0f}s 后重连 "
                    f"(第 {reconnect_count} 次) | 目标节点 "
                    f"{node.get('name', node['host'])}"
                )
                if not await self._sleep_interruptible(backoff):
                    break

        finally:
            elapsed = time.time() - start_time
            self.db.end_session(self._session_id,
                               status="completed" if self._running else "cancelled")
            self._running = False
            logger.info(
                f"=== 监听会话结束: 时长 {elapsed:.0f}s, "
                f"检测到 {self._total_signals} 个信号, "
                f"重连 {reconnect_count} 次 ==="
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
                                         duration: float) -> str:
        """
        单频率持续监听 —— 一次调用只负责一条连接。

        Returns:
            退出原因，由 monitor() 决定要不要重连:
            connect_failed / disconnected / duration / stopped
        """
        client = KiwiSDRClient.from_config(host, port, self.config)
        connected = await client.connect()

        if not connected:
            logger.error(f"无法连接到节点: {node_name}")
            return "connect_failed"

        reason = "stopped"

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
                    reason = "duration"
                    break

                if self._should_return_to_preferred():
                    reason = "try_preferred"
                    break

                if not client.connected:
                    if client.audio_frames == 0:
                        logger.warning(
                            "KiwiSDR 连接结束，且全程没有收到任何音频帧 —— "
                            "这个节点多半有问题，接下来会换一个；"
                            "想单独验链路用 python diagnose_rms.py"
                        )
                    else:
                        logger.warning(
                            f"KiwiSDR 连接断开 (共收到 {client.audio_frames} 个音频帧)"
                        )
                    reason = "disconnected"
                    break

            # 停止并清理。
            # 先让静噪正常收尾，正在录的那段信号才会入库
            squelch.force_close("monitor-stop")
            await client.stop_audio_stream()
            if recorder.is_recording:
                recorder.stop_recording()

        finally:
            await client.disconnect()

        return reason

    async def _monitor_multiple_frequencies(self, host: str, port: int,
                                             node_name: str,
                                             frequencies: List[FrequencyTarget],
                                             duration: float) -> str:
        """
        多频率轮询监听 —— 一次调用只负责一条连接。

        Returns:
            退出原因，由 monitor() 决定要不要重连:
            connect_failed / disconnected / duration / stopped
        """
        client = KiwiSDRClient.from_config(host, port, self.config)
        connected = await client.connect()

        if not connected:
            logger.error(f"无法连接到节点: {node_name}")
            return "connect_failed"

        reason = "stopped"

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
                    logger.info(f"已达到设定的监听时长 ({duration}s)")
                    reason = "duration"
                    break
                if not client.connected:
                    logger.warning(
                        f"KiwiSDR 连接断开 "
                        f"(共收到 {client.audio_frames} 个音频帧)"
                    )
                    reason = "disconnected"
                    break
                if self._should_return_to_preferred():
                    reason = "try_preferred"
                    break

        finally:
            await client.disconnect()

        return reason

    def _should_return_to_preferred(self) -> bool:
        """被迫离开首选节点够久了，该回去看看它恢复没有。"""
        return (self._left_preferred_at is not None
                and time.time() - self._left_preferred_at >= PREFERRED_RETRY_SECONDS)

    def _pick_alternative_node(self, current_node: dict,
                               tried_hosts: set) -> Optional[dict]:
        """
        挑一个还没试过的备用节点。

        候选一律从 config 的 nodes 里取 —— 只有配置里才有每个节点单独标定过的
        man_gain，用数据库那份记录去建客户端会把增益标定丢掉，底噪立刻就不对了。

        排序按"这个节点到底听不听得见"来，延迟只是最后的平手判据: HF 收得到
        什么取决于地理位置，延迟最低的节点完全可能离发射台半个地球。

        Returns:
            备用节点; 配置里只有当前这一个节点时返回 None
        """
        candidates = [n for n in (self.config.get("nodes") or []) if n.get("host")]
        if not candidates:
            return None

        available = set()
        latency = {}
        quality = {}
        try:
            for n in self.db.get_available_nodes():
                available.add(n["host"])
                if n.get("avg_latency_ms") is not None:
                    latency[n["host"]] = n["avg_latency_ms"]
            quality = self.db.get_node_signal_quality(NODE_QUALITY_MIN_SNR_DB)
        except Exception:
            # 数据库读不出来不算错，退化成按配置顺序挑
            pass

        def rank(n):
            host = n["host"]
            stat = quality.get(host) or {}
            return (
                node_quality_tier(host, quality),
                -(stat.get("useful") or 0),
                0 if host in available else 1,
                latency.get(host, float("inf")),
            )

        candidates.sort(key=rank)

        fresh = [n for n in candidates if n["host"] not in tried_hosts]
        if fresh:
            return fresh[0]

        # 一圈都试过了: 回到排名最好的那个从头再来，但别原地重选当前节点
        others = [n for n in candidates
                  if n["host"] != current_node.get("host")]
        return others[0] if others else None

    async def _sleep_interruptible(self, seconds: float) -> bool:
        """
        分段等待，等待期间随时能被 stop() 打断。

        直接 await asyncio.sleep(60) 的话，Ctrl+C 之后还得干等满一分钟才退出。

        Returns:
            True = 睡满了; False = 中途被 stop() 打断
        """
        deadline = time.time() + seconds
        while self._running:
            left = deadline - time.time()
            if left <= 0:
                break
            await asyncio.sleep(min(1.0, left))
        return self._running

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
