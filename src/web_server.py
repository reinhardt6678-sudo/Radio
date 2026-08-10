"""
web_server.py - 实时 Web 监听界面后端

基于 aiohttp 的 Web 服务器，提供：
- 实时音频流状态 WebSocket 推送
- KiwiSDR 节点管理 API
- 信号监听控制 API
- 历史数据查询 API
"""

import asyncio
import json
import logging
import os
import time
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Set, Dict, List

from aiohttp import web

from .kiwi_client import KiwiSDRClient
from .node_manager import NodeManager
from .squelch import SquelchDetector
from .recorder import AudioRecorder
from .analyzer import SignalAnalyzer
from .db import Database

logger = logging.getLogger(__name__)


class WebMonitorServer:
    """实时 Web 监听界面服务器。"""

    def __init__(self, config: dict, db: Database, freq_data: dict):
        self.config = config
        self.db = db
        self.freq_data = freq_data  # 频率数据库原始数据
        self.host = "0.0.0.0"
        self.port = 8888

        # WebSocket 客户端集合
        self._ws_clients: Set[web.WebSocketResponse] = set()

        # 监听状态
        self._monitoring = False
        self._kiwi_client: Optional[KiwiSDRClient] = None
        self._squelch: Optional[SquelchDetector] = None
        self._recorder: Optional[AudioRecorder] = None
        self._analyzer: Optional[SignalAnalyzer] = None
        self._session_id: Optional[int] = None
        self._current_freq = 0.0
        self._current_mode = "USB"
        self._current_node = {}
        self._total_signals = 0
        self._monitor_start_time = 0.0

        # 实时数据
        self._current_rms = 0.0
        self._current_smeter = -120.0
        self._signal_active = False
        self._signal_audio_buffer = []
        self._rms_history = []  # 最近的 RMS 值用于图表
        self._reconnect_count = 0  # 自动重连次数

        # 分析器（使用统一工厂方法，避免配置默认值不一致）
        self._analyzer = SignalAnalyzer.from_config(config)

        # Web 应用目录
        self._web_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")

    def create_app(self) -> web.Application:
        """创建 aiohttp Web 应用。"""
        app = web.Application()

        # 静态页面
        app.router.add_get("/", self._handle_index)
        app.router.add_static("/static", self._web_dir, show_index=False)

        # WebSocket
        app.router.add_get("/ws", self._handle_websocket)

        # REST API
        app.router.add_get("/api/status", self._api_status)
        app.router.add_get("/api/nodes", self._api_nodes)
        app.router.add_post("/api/nodes/check", self._api_check_nodes)
        app.router.add_get("/api/frequencies", self._api_frequencies)
        app.router.add_get("/api/signals", self._api_signals)
        app.router.add_get("/api/stats", self._api_stats)
        app.router.add_post("/api/monitor/start", self._api_start_monitor)
        app.router.add_post("/api/monitor/stop", self._api_stop_monitor)

        return app

    async def start(self, host: str = "0.0.0.0", port: int = 8888):
        """启动 Web 服务器。"""
        self.host = host
        self.port = port
        app = self.create_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()

        logger.info(f"Web 监听界面已启动: http://localhost:{port}")
        print(f"\n  [WEB] Open browser: http://localhost:{port}\n")

        # 保持运行
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await runner.cleanup()

    # ==================== 页面处理 ====================

    async def _handle_index(self, request: web.Request) -> web.Response:
        """返回主页面。"""
        index_path = os.path.join(self._web_dir, "index.html")
        if os.path.exists(index_path):
            return web.FileResponse(index_path)
        return web.Response(text="index.html not found", status=404)

    # ==================== WebSocket ====================

    async def _handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket 连接处理。"""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.add(ws)
        logger.info(f"WebSocket 客户端连接 (共 {len(self._ws_clients)})")

        # 发送初始状态
        sq_cfg = self.config.get("squelch", {})
        await ws.send_json({
            "type": "init",
            "monitoring": self._monitoring,
            "frequency": self._current_freq,
            "mode": self._current_mode,
            "node": self._current_node.get("name", ""),
            "squelch_threshold": sq_cfg.get("open_threshold", 0.15),
        })

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_ws_message(ws, data)
                elif msg.type == web.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")
        finally:
            self._ws_clients.discard(ws)
            logger.info(f"WebSocket 客户端断开 (共 {len(self._ws_clients)})")

        return ws

    async def _handle_ws_message(self, ws: web.WebSocketResponse, data: dict):
        """处理 WebSocket 消息。"""
        action = data.get("action")
        if action == "ping":
            await ws.send_json({"type": "pong"})

    async def _broadcast_ws(self, message: dict):
        """向所有 WebSocket 客户端广播消息。"""
        if not self._ws_clients:
            return
        dead = set()
        for ws in self._ws_clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead

    # ==================== REST API ====================

    async def _api_status(self, request: web.Request) -> web.Response:
        """获取当前系统状态。"""
        elapsed = time.time() - self._monitor_start_time if self._monitoring else 0
        return web.json_response({
            "monitoring": self._monitoring,
            "frequency_khz": self._current_freq,
            "mode": self._current_mode,
            "node": self._current_node.get("name", ""),
            "node_host": self._current_node.get("host", ""),
            "current_rms": round(self._current_rms, 6),
            "smeter_dbm": round(self._current_smeter, 1),
            "signal_active": self._signal_active,
            "total_signals": self._total_signals,
            "elapsed_seconds": round(elapsed, 0),
            "session_id": self._session_id,
            "rms_history": self._rms_history[-100:],  # 最近100个点
            "reconnects": self._reconnect_count,
        })

    async def _api_nodes(self, request: web.Request) -> web.Response:
        """获取节点列表。"""
        nodes = self.config.get("nodes", [])
        db_nodes = self.db.get_all_nodes()
        # 合并数据库中的状态信息
        node_map = {(n["host"], n["port"]): n for n in db_nodes}
        result = []
        for n in nodes:
            key = (n["host"], n.get("port", 8073))
            db_info = node_map.get(key, {})
            result.append({
                **n,
                "is_available": bool(db_info.get("is_available", False)),
                "latency_ms": db_info.get("avg_latency_ms"),
                "last_check": db_info.get("last_check", ""),
            })
        return web.json_response(result)

    async def _api_check_nodes(self, request: web.Request) -> web.Response:
        """检查所有节点可用性。"""
        node_mgr = NodeManager(self.config, self.db)
        available = await node_mgr.check_all_nodes(timeout=15)
        return web.json_response({
            "total": len(self.config.get("nodes", [])),
            "available": len(available),
            "nodes": [{"host": n["host"], "name": n.get("name", ""),
                       "latency_ms": n.get("latency_ms")} for n in available]
        })

    async def _api_frequencies(self, request: web.Request) -> web.Response:
        """获取频率数据库。"""
        result = []
        for network_key, network_data in self.freq_data.items():
            if not isinstance(network_data, dict):
                continue
            for freq_info in network_data.get("frequencies", []):
                result.append({
                    "network": network_key,
                    "network_desc": network_data.get("description", ""),
                    **freq_info
                })
        return web.json_response(result)

    async def _api_signals(self, request: web.Request) -> web.Response:
        """获取最近的信号记录。"""
        try:
            days = min(max(int(request.query.get("days", 7)), 1), 365)
            limit = min(max(int(request.query.get("limit", 100)), 1), 1000)
        except (ValueError, TypeError):
            return web.json_response({"error": "参数无效"}, status=400)
        signals = self.db.get_all_signals(days=days, limit=limit)
        return web.json_response(signals)

    async def _api_stats(self, request: web.Request) -> web.Response:
        """获取统计数据。"""
        try:
            days = min(max(int(request.query.get("days", 7)), 1), 365)
        except (ValueError, TypeError):
            return web.json_response({"error": "参数无效"}, status=400)
        freq_stats = self.db.get_frequency_stats(days=days)
        hourly = self.db.get_hourly_activity(days=days)
        return web.json_response({
            "frequency_stats": freq_stats,
            "hourly_activity": hourly,
        })

    async def _api_start_monitor(self, request: web.Request) -> web.Response:
        """开始监听。"""
        if self._monitoring:
            return web.json_response({"error": "Already monitoring"}, status=400)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "无效的 JSON 请求体"}, status=400)

        try:
            freq_khz = float(data.get("frequency", 11175))
            if not (100 <= freq_khz <= 30000):
                return web.json_response({"error": "频率超出范围 (100-30000 kHz)"}, status=400)
        except (ValueError, TypeError):
            return web.json_response({"error": "无效的频率值"}, status=400)

        mode = data.get("mode", "USB").upper()
        if mode not in ("USB", "LSB", "AM", "CW", "CWN"):
            return web.json_response({"error": f"不支持的模式: {mode}"}, status=400)
        node_host = data.get("node_host", "")

        # 选择节点
        node = None
        for n in self.config.get("nodes", []):
            if node_host and n["host"] == node_host:
                node = n
                break
        if not node:
            # 取第一个或从数据库中选可用的
            db_nodes = self.db.get_available_nodes()
            if db_nodes:
                node = db_nodes[0]
            else:
                nodes = self.config.get("nodes", [])
                if nodes:
                    node = nodes[0]
                else:
                    return web.json_response({"error": "No nodes configured"}, status=400)

        # 异步启动监听
        asyncio.create_task(self._run_monitor(node, freq_khz, mode))

        return web.json_response({"status": "starting", "frequency": freq_khz,
                                   "mode": mode, "node": node.get("name", node["host"])})

    async def _api_stop_monitor(self, request: web.Request) -> web.Response:
        """停止监听。"""
        if not self._monitoring:
            return web.json_response({"error": "Not monitoring"}, status=400)

        self._monitoring = False
        return web.json_response({"status": "stopping"})

    # ==================== 监听逻辑 ====================

    async def _run_monitor(self, node: dict, freq_khz: float, mode: str):
        """执行实际的监听任务（带自动重连，支持 24/7 不间断监听）。"""
        self._loop = asyncio.get_running_loop()  # 保存事件循环引用，供同步回调使用
        self._current_freq = freq_khz
        self._current_mode = mode
        self._current_node = node
        self._total_signals = 0
        self._rms_history = []
        self._monitor_start_time = time.time()
        self._monitoring = True
        self._reconnect_count = 0

        # 重连参数
        MAX_BACKOFF = 60       # 最大重连等待秒数
        BASE_BACKOFF = 5       # 初始重连等待秒数
        MAX_NODE_FAILURES = 3  # 同一节点连续失败次数后切换节点

        # 创建会话
        host = node["host"]
        port = node.get("port", 8073)
        node_name = node.get("name", host)
        self._session_id = self.db.create_session(
            node_host=host, node_name=node_name,
            frequencies=[freq_khz], notes=f"Web monitor: {freq_khz} kHz (auto-reconnect)"
        )

        consecutive_failures = 0  # 当前节点连续失败次数

        # ===== 自动重连主循环 =====
        while self._monitoring:
            host = node["host"]
            port = node.get("port", 8073)
            node_name = node.get("name", host)
            self._current_node = node

            try:
                # 连接 KiwiSDR
                self._kiwi_client = KiwiSDRClient(host, port)
                connected = await self._kiwi_client.connect()

                if not connected:
                    consecutive_failures += 1
                    logger.warning(f"无法连接到 {node_name} (连续失败 {consecutive_failures} 次)")
                    await self._broadcast_ws({
                        "type": "reconnecting",
                        "message": f"无法连接到 {node_name}，准备重试...",
                        "attempt": self._reconnect_count + 1,
                        "node": node_name,
                    })

                    # 连续失败太多次，尝试切换节点
                    if consecutive_failures >= MAX_NODE_FAILURES:
                        alt_node = await self._find_alternative_node(node)
                        if alt_node:
                            logger.info(f"切换到备用节点: {alt_node.get('name', alt_node['host'])}")
                            node = alt_node
                            consecutive_failures = 0
                            await self._broadcast_ws({
                                "type": "node_switch",
                                "message": f"切换到备用节点: {alt_node.get('name', alt_node['host'])}",
                                "node": alt_node.get("name", alt_node["host"]),
                            })

                    # 指数退避等待
                    backoff = min(BASE_BACKOFF * (2 ** min(self._reconnect_count, 5)), MAX_BACKOFF)
                    self._reconnect_count += 1
                    logger.info(f"等待 {backoff}s 后重连 (第 {self._reconnect_count} 次)")
                    await self._broadcast_ws({
                        "type": "reconnect_wait",
                        "seconds": backoff,
                        "attempt": self._reconnect_count,
                    })
                    await asyncio.sleep(backoff)
                    continue

                # 连接成功，重置失败计数
                consecutive_failures = 0
                if self._reconnect_count > 0:
                    logger.info(f"重连成功! (第 {self._reconnect_count} 次重连)")
                    await self._broadcast_ws({
                        "type": "reconnected",
                        "message": f"已重新连接到 {node_name}",
                        "node": node_name,
                        "attempt": self._reconnect_count,
                    })

                await self._broadcast_ws({
                    "type": "monitor_started",
                    "frequency": freq_khz, "mode": mode, "node": node_name,
                })

                await self._kiwi_client.set_frequency(freq_khz, mode)

                # 静噪检测器（使用统一工厂方法）
                self._squelch = SquelchDetector.from_config(self.config)

                # 录音器
                self._recorder = AudioRecorder.from_config(self.config)

                self._signal_audio_buffer = []

                def on_signal_open(pre_roll_data, start_time):
                    self._signal_active = True
                    self._signal_audio_buffer.clear()
                    if len(pre_roll_data) > 0:
                        self._signal_audio_buffer.append(pre_roll_data.copy())
                    self._recorder.start_recording(freq_khz, node_name, pre_roll_data)

                def on_signal_audio(samples):
                    self._signal_audio_buffer.append(samples.copy())
                    self._recorder.write_samples(samples)

                def on_signal_close(duration, peak_rms, avg_rms):
                    self._signal_active = False
                    rec_info = self._recorder.stop_recording()
                    self._total_signals += 1

                    sig_id = self.db.record_signal(
                        session_id=self._session_id,
                        frequency_khz=freq_khz, mode=mode,
                        node_host=host, node_name=node_name,
                        duration_seconds=duration, peak_rms=peak_rms, avg_rms=avg_rms,
                        s_meter_dbm=self._current_smeter,
                        recording_path=rec_info["path"] if rec_info else None,
                        description="", network="",
                    )

                    # 频谱分析（使用统一方法避免重复代码）
                    sr = self.config.get("receiver", {}).get("sample_rate", 12000)
                    self._analyzer.analyze_and_save(
                        self.db, sig_id, self._signal_audio_buffer, sr
                    )

                    # 通过 WebSocket 推送信号事件 (fire-and-forget)
                    self._loop.create_task(self._broadcast_ws({
                        "type": "signal_detected",
                        "signal_number": self._total_signals,
                        "frequency_khz": freq_khz,
                        "duration": round(duration, 1),
                        "peak_rms": round(peak_rms, 4),
                        "avg_rms": round(avg_rms, 4),
                        "recording": os.path.basename(rec_info["path"]) if rec_info else None,
                    }))

                self._squelch.set_callbacks(
                    on_open=on_signal_open,
                    on_close=on_signal_close,
                    on_audio=on_signal_audio,
                )

                # 音频处理 + 实时推送
                _push_counter = [0]

                async def process_audio(samples, smeter):
                    self._current_rms = float(np.sqrt(np.mean(samples ** 2)))
                    self._current_smeter = smeter
                    self._squelch.process(samples)
                    self._rms_history.append(round(self._current_rms, 5))
                    if len(self._rms_history) > 500:
                        self._rms_history = self._rms_history[-500:]

                    # 每 5 次推送一次实时数据 (降低频率)
                    _push_counter[0] += 1
                    if _push_counter[0] % 5 == 0:
                        await self._broadcast_ws({
                            "type": "realtime",
                            "rms": round(self._current_rms, 5),
                            "smeter": round(smeter, 1),
                            "signal_active": self._signal_active,
                            "total_signals": self._total_signals,
                            "elapsed": round(time.time() - self._monitor_start_time, 0),
                            "reconnects": self._reconnect_count,
                        })

                await self._kiwi_client.start_audio_stream(process_audio)

                # 等待断开或用户停止
                while self._monitoring and self._kiwi_client.connected:
                    await asyncio.sleep(0.5)

                # 清理当前连接
                if self._kiwi_client:
                    await self._kiwi_client.stop_audio_stream()
                    await self._kiwi_client.disconnect()
                if self._recorder and self._recorder.is_recording:
                    self._recorder.stop_recording()

                # 如果是用户手动停止的，跳出重连循环
                if not self._monitoring:
                    logger.info("用户停止监听，退出重连循环")
                    break

                # KiwiSDR 断开了连接，准备重连
                self._reconnect_count += 1
                backoff = min(BASE_BACKOFF * (2 ** min(self._reconnect_count - 1, 5)), MAX_BACKOFF)
                logger.warning(f"KiwiSDR 连接断开，{backoff}s 后自动重连 (第 {self._reconnect_count} 次)")
                await self._broadcast_ws({
                    "type": "reconnecting",
                    "message": f"KiwiSDR 连接断开，{backoff}s 后自动重连...",
                    "attempt": self._reconnect_count,
                    "wait_seconds": backoff,
                    "node": node_name,
                })
                await asyncio.sleep(backoff)

            except Exception as e:
                logger.error(f"Monitor error: {e}", exc_info=True)
                await self._broadcast_ws({"type": "error", "message": str(e)})

                # 清理
                if self._kiwi_client:
                    try:
                        await self._kiwi_client.stop_audio_stream()
                        await self._kiwi_client.disconnect()
                    except Exception:
                        pass
                if self._recorder and self._recorder.is_recording:
                    try:
                        self._recorder.stop_recording()
                    except Exception:
                        pass

                if not self._monitoring:
                    break

                # 异常后重连
                self._reconnect_count += 1
                backoff = min(BASE_BACKOFF * (2 ** min(self._reconnect_count - 1, 5)), MAX_BACKOFF)
                logger.warning(f"异常后 {backoff}s 重连 (第 {self._reconnect_count} 次)")
                await self._broadcast_ws({
                    "type": "reconnecting",
                    "message": f"发生异常，{backoff}s 后自动重连...",
                    "attempt": self._reconnect_count,
                    "wait_seconds": backoff,
                    "node": node_name,
                })
                await asyncio.sleep(backoff)

        # ===== 监听彻底结束 =====
        self._monitoring = False
        self._signal_active = False
        if self._session_id:
            self.db.end_session(self._session_id)

        total_time = time.time() - self._monitor_start_time
        logger.info(
            f"Monitor stopped. Total time: {total_time:.0f}s, "
            f"Signals: {self._total_signals}, Reconnects: {self._reconnect_count}"
        )
        await self._broadcast_ws({"type": "monitor_stopped", "total_reconnects": self._reconnect_count})

    async def _find_alternative_node(self, current_node: dict) -> Optional[dict]:
        """
        查找备用 KiwiSDR 节点。

        当前节点连续失败时，尝试从配置中选择其他可用节点。
        """
        all_nodes = self.config.get("nodes", [])
        current_host = current_node["host"]

        # 先查数据库中的可用节点
        try:
            db_available = self.db.get_available_nodes()
            for n in db_available:
                if n["host"] != current_host:
                    logger.info(f"从数据库找到备用节点: {n.get('name', n['host'])}")
                    return n
        except Exception:
            pass

        # 从配置中选择不同的节点
        for n in all_nodes:
            if n["host"] != current_host:
                return n

        # 没有备用节点，返回当前节点继续尝试
        return None
