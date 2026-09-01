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
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Set, Dict, List

from aiohttp import web

from .kiwi_client import KiwiSDRClient
from .node_manager import NodeManager
from .squelch import (SquelchDetector, MODE_ABSOLUTE, MODE_SMETER,
                      VALID_MODES, db_to_ratio)
from .recorder import AudioRecorder, discard_recording
from .analyzer import SignalAnalyzer
from .modes import audio_passband, normalize_mode
from .db import Database

logger = logging.getLogger(__name__)


class WebMonitorServer:
    """实时 Web 监听界面服务器。"""

    # 每积累这么多样本推一列频谱 (12000 Hz 下约 6 次/秒)
    SPECTRUM_CHUNK = 2048
    # 瀑布图的频率格数与显示上限
    SPECTRUM_BINS = 128
    SPECTRUM_F_MAX = 4000.0

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
        # 被噪声闸门挡在库外的段数 / segments the noise gate kept out of the database
        self._discarded_signals = 0
        self._monitor_start_time = 0.0

        # 实时数据
        self._current_rms = 0.0
        self._current_smeter = -127.0   # KiwiSDR S-meter 下限
        self._signal_active = False
        self._signal_audio_buffer = []
        self._rms_history = []  # 最近的 RMS 值用于图表
        self._smeter_history = []
        self._reconnect_count = 0  # 自动重连次数

        # 实时频谱 / 噪声基底
        self._spectrum_buffer: List[np.ndarray] = []
        self._spectrum_samples = 0
        self._last_spectrum: Dict = {}
        self._live_snr_db = 0.0
        self._live_noise_floor_db = -120.0
        # 底噪由 SquelchDetector 统一维护 (监听中实时同步过来)，
        # 这里只保留最后一次的值，供停止监听后继续显示
        self._rms_noise_floor = 0.0
        self._dropped_frames = 0

        # 最近信号（带 signal_id，供页面直接回放）
        self._recent_signals: deque = deque(maxlen=50)

        # 分析器（使用统一工厂方法，避免配置默认值不一致）
        self._analyzer = SignalAnalyzer.from_config(config)

        # Web 应用目录
        self._web_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")

        # 录音目录（回放接口只允许访问这个目录里的文件）
        self._recordings_dir = Path(
            config.get("recording", {}).get("output_dir", "data/recordings")
        ).resolve()

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
        app.router.add_get("/api/sessions", self._api_sessions)
        app.router.add_get("/api/recordings", self._api_recordings)
        app.router.add_get("/api/recordings/{signal_id}/audio", self._api_recording_audio)
        app.router.add_get("/api/recordings/{signal_id}/spectrogram",
                           self._api_recording_spectrogram)
        app.router.add_get("/api/squelch", self._api_get_squelch)
        app.router.add_post("/api/squelch", self._api_set_squelch)
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
        squelch_state = self._squelch_state()
        await ws.send_json({
            "type": "init",
            "monitoring": self._monitoring,
            "frequency": self._current_freq,
            "mode": self._current_mode,
            "node": self._current_node.get("name", ""),
            "squelch_threshold": squelch_state["open_threshold"],
            "squelch_close": squelch_state["close_threshold"],
            "squelch": squelch_state,
            "passband": list(audio_passband(self._current_mode)),
            "spectrum_bins": self.SPECTRUM_BINS,
            "spectrum_f_max": self.SPECTRUM_F_MAX,
            "recent_signals": list(self._recent_signals)[-20:],
            "rms_history": self._rms_history[-200:],
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
            # 被噪声闸门挡下的段数 —— 和 total_signals 分开，界面上才能看出
            # "安静"和"全是宽带噪声"的区别
            # Kept apart from total_signals so the UI can tell a quiet band from
            # one drowning in wideband noise.
            "discarded_signals": self._discarded_signals,
            "elapsed_seconds": round(elapsed, 0),
            "session_id": self._session_id,
            "rms_history": self._rms_history[-100:],  # 最近100个点
            "smeter_history": self._smeter_history[-100:],
            "reconnects": self._reconnect_count,
            "snr_db": round(self._live_snr_db, 1),
            "noise_floor_db": round(self._live_noise_floor_db, 1),
            "rms_noise_floor": round(self._rms_noise_floor, 5),
            "dropped_frames": self._dropped_frames,
            "passband": list(audio_passband(self._current_mode)),
            "squelch": self._squelch_state(),
            "spectrum": self._last_spectrum,
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
        return web.json_response({
            "days": days,
            "frequency_stats": self.db.get_frequency_stats(days=days),
            "hourly_activity": self.db.get_hourly_activity(days=days),
            "modulation_stats": self.db.get_modulation_stats(days=days),
            "snr_distribution": self.db.get_snr_distribution(days=days),
            "daily_activity": self.db.get_daily_activity(days=max(days, 14)),
        })

    async def _api_sessions(self, request: web.Request) -> web.Response:
        """获取最近的监听会话。"""
        try:
            limit = min(max(int(request.query.get("limit", 20)), 1), 200)
        except (ValueError, TypeError):
            return web.json_response({"error": "参数无效"}, status=400)
        return web.json_response(self.db.get_recent_sessions(limit=limit))

    # ==================== 录音浏览与回放 ====================

    def _resolve_recording(self, row: dict) -> Optional[Path]:
        """
        把数据库里的录音路径解析成真实文件路径。

        只允许访问配置的录音目录，避免路径穿越。
        """
        raw = (row or {}).get("recording_path")
        if not raw:
            return None
        try:
            path = Path(raw).resolve()
        except (OSError, ValueError):
            return None
        if path != self._recordings_dir and self._recordings_dir not in path.parents:
            logger.warning(f"拒绝访问录音目录之外的文件: {raw}")
            return None
        return path if path.is_file() else None

    async def _api_recordings(self, request: web.Request) -> web.Response:
        """获取信号+分析记录（带录音文件信息），供页面浏览回放。"""
        try:
            days = min(max(int(request.query.get("days", 7)), 1), 365)
            limit = min(max(int(request.query.get("limit", 100)), 1), 500)
            min_snr = request.query.get("min_snr")
            min_snr = float(min_snr) if min_snr not in (None, "") else None
            freq = request.query.get("frequency")
            freq = float(freq) if freq not in (None, "") else None
        except (ValueError, TypeError):
            return web.json_response({"error": "参数无效"}, status=400)

        with_file = request.query.get("with_recording", "0") in ("1", "true", "yes")
        rows = self.db.get_signals_with_analysis(
            days=days, limit=limit, frequency_khz=freq,
            with_recording=with_file, min_snr_db=min_snr,
        )

        for row in rows:
            path = self._resolve_recording(row)
            row["has_audio"] = path is not None
            row["file_size_kb"] = round(path.stat().st_size / 1024, 1) if path else 0
            row["filename"] = os.path.basename(row.get("recording_path") or "")
            # 不把服务器上的绝对路径暴露给页面
            row.pop("recording_path", None)

        return web.json_response(rows)

    async def _api_recording_audio(self, request: web.Request) -> web.Response:
        """回放录音文件。"""
        try:
            signal_id = int(request.match_info["signal_id"])
        except (ValueError, TypeError, KeyError):
            return web.json_response({"error": "无效的信号 ID"}, status=400)

        row = self.db.get_signal_with_analysis(signal_id)
        if not row:
            return web.json_response({"error": "信号不存在"}, status=404)

        path = self._resolve_recording(row)
        if path is None:
            return web.json_response({"error": "录音文件不存在"}, status=404)

        return web.FileResponse(path, headers={
            "Content-Type": "audio/wav",
            "Cache-Control": "no-cache",
        })

    async def _api_recording_spectrogram(self, request: web.Request) -> web.Response:
        """
        录音的频谱图 + 包络，用于页面上的缩略图。

        analyzer.get_spectrogram() 早就写好了，但界面一直没用过它。
        """
        try:
            signal_id = int(request.match_info["signal_id"])
            max_bins = min(max(int(request.query.get("bins", 96)), 16), 256)
            max_cols = min(max(int(request.query.get("cols", 180)), 16), 600)
        except (ValueError, TypeError, KeyError):
            return web.json_response({"error": "参数无效"}, status=400)

        row = self.db.get_signal_with_analysis(signal_id)
        if not row:
            return web.json_response({"error": "信号不存在"}, status=404)

        path = self._resolve_recording(row)
        if path is None:
            return web.json_response({"error": "录音文件不存在"}, status=404)

        loaded = await asyncio.get_running_loop().run_in_executor(
            None, SignalAnalyzer.load_wav, str(path)
        )
        if loaded is None:
            return web.json_response({"error": "录音读取失败"}, status=500)

        samples, sample_rate = loaded
        mode = normalize_mode(row.get("demod_mode") or row.get("mode"))
        f_lo, f_hi = audio_passband(mode, sample_rate)

        times, freqs, sxx_db = await asyncio.get_running_loop().run_in_executor(
            None, self._analyzer.get_spectrogram, samples, sample_rate
        )

        # 只保留通带附近，并降采样到页面能画得下的尺寸
        keep = (freqs >= max(0.0, f_lo - 300)) & (freqs <= min(f_hi + 500, sample_rate / 2))
        if not np.any(keep):
            keep = np.ones_like(freqs, dtype=bool)
        freqs = freqs[keep]
        sxx_db = sxx_db[keep, :]

        f_step = max(1, len(freqs) // max_bins)
        t_step = max(1, sxx_db.shape[1] // max_cols)
        freqs = freqs[::f_step]
        sxx_db = sxx_db[::f_step, ::t_step]
        times = times[::t_step]

        # 量化到 0-255，页面直接当灰度画。
        # 动态范围按这段录音自己的分布定，弱信号和强信号都能看清
        db_floor = float(np.percentile(sxx_db, 25))
        db_ceil = float(np.percentile(sxx_db, 99.7))
        if db_ceil - db_floor < 20.0:
            db_ceil = db_floor + 20.0
        scaled = np.clip((sxx_db - db_floor) / (db_ceil - db_floor), 0, 1)
        matrix = (scaled * 255).astype(np.uint8)

        # 包络 (每列 RMS)，画波形缩略图
        env_cols = min(len(samples), 400)
        block = max(1, len(samples) // env_cols)
        usable = (len(samples) // block) * block
        envelope = np.sqrt(np.mean(
            samples[:usable].reshape(-1, block) ** 2, axis=1
        )) if usable else np.zeros(1)

        return web.json_response({
            "signal_id": signal_id,
            "duration_seconds": float(len(samples) / sample_rate),
            "sample_rate": sample_rate,
            "mode": mode,
            "freq_min": float(freqs[0]) if len(freqs) else 0.0,
            "freq_max": float(freqs[-1]) if len(freqs) else 0.0,
            "passband": [f_lo, f_hi],
            "n_freq": int(matrix.shape[0]),
            "n_time": int(matrix.shape[1]),
            "db_floor": round(db_floor, 1),
            "db_ceil": round(db_ceil, 1),
            # 按时间列展开: [t0f0, t0f1, ... ]
            "matrix": matrix.T.flatten().tolist(),
            "envelope": [round(float(v), 5) for v in envelope],
        })

    # ==================== 静噪阈值 ====================

    def _squelch_state(self) -> dict:
        """
        静噪配置 + 实测底噪 + 当前生效阈值。

        页面、/api/status、/api/squelch 和 WebSocket 推送共用一份，
        避免几个地方各算各的又对不上。
        """
        sq_cfg = self.config.get("squelch", {})
        det = self._squelch

        state = {
            "mode": sq_cfg.get("mode", MODE_SMETER),
            "smeter_open_margin_db": sq_cfg.get("smeter_open_margin_db", 14.0),
            "smeter_close_margin_db": sq_cfg.get("smeter_close_margin_db", 10.0),
            "smeter_floor": None,
            "smeter_open": None,
            "smeter_close": None,
            "smeter_dbm": None,
            "open_threshold": sq_cfg.get("open_threshold", 0.15),
            "close_threshold": sq_cfg.get("close_threshold", 0.085),
            "tail_time": sq_cfg.get("tail_time", 3.0),
            "rms_noise_floor": round(self._rms_noise_floor, 5),
            "floor_samples": 0,
            "warming_up": False,
            "is_open": False,
            "threshold_below_floor": False,
            "stuck_open": False,
            "forced_closes": 0,
        }

        if det is not None:
            suggested_open, suggested_close = det.suggested_thresholds()
            state.update({
                "mode": det.mode,
                "effective_open": round(det.effective_open_threshold, 5),
                "effective_close": round(det.effective_close_threshold, 5),
                "floor_samples": det.floor_sample_count,
                "warming_up": det.warming_up,
                "is_open": det.is_open,
                "threshold_below_floor": det.threshold_below_floor,
                "stuck_open": det.stuck_open,
                "forced_closes": det.forced_closes,
                "smeter_open_margin_db": det.smeter_open_margin_db,
                "smeter_close_margin_db": det.smeter_close_margin_db,
                "smeter_floor": (round(det.smeter_floor, 1)
                                 if det.smeter_floor is not None else None),
                "smeter_open": (round(det.effective_smeter_open, 1)
                                if det.effective_smeter_open is not None else None),
                "smeter_close": (round(det.effective_smeter_close, 1)
                                 if det.effective_smeter_close is not None else None),
                "smeter_dbm": (round(det.current_smeter, 1)
                               if det.current_smeter is not None else None),
            })
        else:
            # 还没开始监听: 生效阈值就是配置值，建议值按最后一次测到的底噪算
            state["effective_open"] = state["open_threshold"]
            state["effective_close"] = state["close_threshold"]
            suggested_open = round(
                self._rms_noise_floor * db_to_ratio(sq_cfg.get("open_margin_db", 6.0)), 5)
            suggested_close = round(
                self._rms_noise_floor * db_to_ratio(sq_cfg.get("close_margin_db", 3.0)), 5)

        state["suggested_open"] = suggested_open
        state["suggested_close"] = suggested_close
        return state

    async def _api_get_squelch(self, request: web.Request) -> web.Response:
        """当前静噪设置与实测噪声基底。"""
        state = self._squelch_state()
        state["current_rms"] = round(self._current_rms, 5)
        state["samples"] = state["floor_samples"]
        return web.json_response(state)

    async def _api_set_squelch(self, request: web.Request) -> web.Response:
        """
        在线调整静噪阈值。

        改动会立刻作用到正在跑的检测器上，不用停下监听重来。
        """
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "无效的 JSON 请求体"}, status=400)

        sq_cfg = self.config.setdefault("squelch", {})
        updated = {}

        try:
            if "mode" in data:
                value = str(data["mode"]).lower()
                if value not in VALID_MODES:
                    return web.json_response(
                        {"error": f"静噪模式只能是 {' / '.join(VALID_MODES)}"}, status=400)
                updated["mode"] = value
            if "open_margin_db" in data:
                value = float(data["open_margin_db"])
                if not (0.0 < value <= 40.0):
                    return web.json_response({"error": "打开余量需在 0-40 dB 之间"},
                                             status=400)
                updated["open_margin_db"] = value
            if "close_margin_db" in data:
                value = float(data["close_margin_db"])
                if not (0.0 < value <= 40.0):
                    return web.json_response({"error": "关闭余量需在 0-40 dB 之间"},
                                             status=400)
                updated["close_margin_db"] = value
            if "smeter_open_margin_db" in data:
                value = float(data["smeter_open_margin_db"])
                if not (0.0 < value <= 60.0):
                    return web.json_response(
                        {"error": "S-meter 打开余量需在 0-60 dB 之间"}, status=400)
                updated["smeter_open_margin_db"] = value
            if "smeter_close_margin_db" in data:
                value = float(data["smeter_close_margin_db"])
                if not (0.0 < value <= 60.0):
                    return web.json_response(
                        {"error": "S-meter 关闭余量需在 0-60 dB 之间"}, status=400)
                updated["smeter_close_margin_db"] = value
            if "open_threshold" in data:
                value = float(data["open_threshold"])
                if not (0.0 < value <= 1.0):
                    return web.json_response({"error": "开启阈值需在 0-1 之间"}, status=400)
                updated["open_threshold"] = value
            if "close_threshold" in data:
                value = float(data["close_threshold"])
                if not (0.0 < value <= 1.0):
                    return web.json_response({"error": "关闭阈值需在 0-1 之间"}, status=400)
                updated["close_threshold"] = value
            if "tail_time" in data:
                value = float(data["tail_time"])
                if not (0.0 <= value <= 60.0):
                    return web.json_response({"error": "尾部延迟需在 0-60 秒之间"}, status=400)
                updated["tail_time"] = value
        except (ValueError, TypeError):
            return web.json_response({"error": "参数必须是数字"}, status=400)

        if not updated:
            return web.json_response({"error": "没有可更新的参数"}, status=400)

        sm_open = updated.get("smeter_open_margin_db",
                              sq_cfg.get("smeter_open_margin_db", 14.0))
        sm_close = updated.get("smeter_close_margin_db",
                               sq_cfg.get("smeter_close_margin_db", 10.0))
        if sm_close >= sm_open:
            return web.json_response(
                {"error": "S-meter 关闭余量必须小于打开余量 (否则没有滞后)"}, status=400)

        open_th = updated.get("open_threshold", sq_cfg.get("open_threshold", 0.15))
        close_th = updated.get("close_threshold", sq_cfg.get("close_threshold", 0.085))
        if close_th >= open_th:
            return web.json_response(
                {"error": "关闭阈值必须低于开启阈值 (否则没有滞后)"}, status=400)

        sq_cfg.update(updated)

        # 作用到正在运行的检测器
        if self._squelch:
            self._squelch.open_threshold = open_th
            self._squelch.close_threshold = close_th
            if "mode" in updated:
                self._squelch.mode = updated["mode"]
            if "open_margin_db" in updated:
                self._squelch.open_margin_db = updated["open_margin_db"]
            if "close_margin_db" in updated:
                self._squelch.close_margin_db = updated["close_margin_db"]
            if "smeter_open_margin_db" in updated:
                self._squelch.smeter_open_margin_db = updated["smeter_open_margin_db"]
            if "smeter_close_margin_db" in updated:
                self._squelch.smeter_close_margin_db = updated["smeter_close_margin_db"]
            if "tail_time" in updated:
                self._squelch.tail_time = updated["tail_time"]

        # 关闭阈值压在底噪以下时静噪永远关不掉 —— 值照样生效，
        # 但必须明确告诉页面，别让它安安静静地跑一整天不出记录
        state = self._squelch_state()
        warning = None
        if state["mode"] == MODE_SMETER:
            if state["smeter_close_margin_db"] <= 0:
                warning = ("S-meter 关闭余量 <= 0: 静噪打开后关不掉，"
                           "不会产生信号记录")
                logger.warning(warning)
        elif state["threshold_below_floor"]:
            warning = (
                f"关闭阈值 {state['effective_close']:.4f} 不高于实测底噪 "
                f"{state['rms_noise_floor']:.4f}: 静噪会一直开着，不会产生信号记录。"
                f"建议 {state['suggested_open']:.4f} / {state['suggested_close']:.4f}，"
                f"或改用自适应模式"
            )
            logger.warning(warning)

        logger.info(f"静噪设置已更新: {updated}")
        await self._broadcast_ws({
            "type": "squelch_updated",
            "open_threshold": open_th,
            "close_threshold": close_th,
            "tail_time": sq_cfg.get("tail_time", 3.0),
            "mode": state["mode"],
            "effective_open": state["effective_open"],
            "effective_close": state["effective_close"],
            "warning": warning,
        })
        return web.json_response({"status": "ok", "warning": warning, **sq_cfg})

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
        self._discarded_signals = 0
        self._rms_history = []
        self._smeter_history = []
        self._monitor_start_time = time.time()
        self._monitoring = True
        self._reconnect_count = 0
        self._spectrum_buffer = []
        self._spectrum_samples = 0
        self._last_spectrum = {}
        self._rms_noise_floor = 0.0
        self._signal_active = False
        self._dropped_frames = 0
        self._recent_signals.clear()

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
                self._kiwi_client = KiwiSDRClient.from_config(host, port, self.config, node)
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
                    "node_host": host,
                    "passband": list(audio_passband(mode)),
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

                    # 频谱分析在入库之前跑，结论要用来决定入不入库
                    # 传入解调模式: SNR 和调制判定按对应通带来算
                    # The analysis runs before the write so its verdict can gate it.
                    sr = self.config.get("receiver", {}).get("sample_rate", 12000)
                    analysis = self._analyzer.analyze_buffer(
                        self._signal_audio_buffer, sr, mode=mode
                    )

                    # 和命令行监听同一道噪声闸门 / same noise gate as CLI monitoring
                    reason = self._analyzer.noise_discard_reason(analysis)
                    if reason:
                        self._discarded_signals += 1
                        deleted = discard_recording(rec_info)
                        logger.info(
                            f"[NOISE-DISCARD] #{self._discarded_signals} "
                            f"{freq_khz:.1f} kHz: {reason} — not filed"
                            f"{', recording deleted' if deleted else ''} "
                            f"/ 判为噪声，未入库"
                            f"{'，录音已删除' if deleted else ''}"
                        )
                        return

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
                    analysis = self._analyzer.save_analysis_result(
                        self.db, sig_id, analysis
                    ) or {}

                    entry = {
                        "type": "signal_detected",
                        "signal_id": sig_id,
                        "signal_number": self._total_signals,
                        "frequency_khz": freq_khz,
                        "mode": mode,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "duration": round(duration, 1),
                        "peak_rms": round(peak_rms, 4),
                        "avg_rms": round(avg_rms, 4),
                        "smeter_dbm": round(self._current_smeter, 1),
                        "snr_db": round(analysis.get("snr_db", 0.0), 1),
                        "bandwidth_hz": round(analysis.get("bandwidth_hz", 0.0)),
                        "modulation": analysis.get("estimated_modulation", "UNKNOWN"),
                        "modulation_confidence": analysis.get("modulation_confidence", 0.0),
                        "envelope_rate_hz": round(analysis.get("envelope_rate_hz", 0.0), 1),
                        "has_audio": bool(rec_info),
                        "recording": os.path.basename(rec_info["path"]) if rec_info else None,
                    }
                    self._recent_signals.append(entry)

                    # 通过 WebSocket 推送信号事件 (fire-and-forget)
                    self._loop.create_task(self._broadcast_ws(entry))

                self._squelch.set_callbacks(
                    on_open=on_signal_open,
                    on_close=on_signal_close,
                    on_audio=on_signal_audio,
                )

                # 音频处理 + 实时推送
                _push_counter = [0]
                sample_rate = self.config.get("receiver", {}).get("sample_rate", 12000)

                async def process_audio(samples, smeter):
                    self._current_rms = float(np.sqrt(np.mean(samples ** 2)))
                    self._current_smeter = smeter
                    self._squelch.process(samples, smeter)
                    # 以检测器状态为准，不靠回调来维持这个标志:
                    # 强制收尾/断线重连时回调不一定成对出现
                    self._signal_active = self._squelch.is_open

                    self._rms_history.append(round(self._current_rms, 5))
                    if len(self._rms_history) > 500:
                        self._rms_history = self._rms_history[-500:]
                    self._smeter_history.append(round(smeter, 1))
                    if len(self._smeter_history) > 500:
                        self._smeter_history = self._smeter_history[-500:]

                    # 滚动噪声基底由检测器维护 (静噪开着的时候也继续统计)。
                    # 这里原来只在静噪关闭时累积: 阈值一旦低于底噪，静噪就
                    # 一直开着，底噪被冻结在监听刚起步那几秒的数值上，
                    # "按底噪设定"照着这个死值只会把阈值调得更低。
                    self._rms_noise_floor = self._squelch.noise_floor

                    if self._kiwi_client:
                        self._dropped_frames = self._kiwi_client.dropped_frames

                    # 累积到一定长度算一列频谱 (瀑布图)
                    self._spectrum_buffer.append(samples)
                    self._spectrum_samples += len(samples)
                    if self._spectrum_samples >= self.SPECTRUM_CHUNK:
                        chunk = np.concatenate(self._spectrum_buffer)
                        self._spectrum_buffer = []
                        self._spectrum_samples = 0
                        spectrum = self._analyzer.live_spectrum(
                            chunk, sample_rate, mode=mode,
                            n_bins=self.SPECTRUM_BINS, f_max=self.SPECTRUM_F_MAX,
                        )
                        self._last_spectrum = spectrum
                        self._live_snr_db = spectrum.get("snr_db", 0.0)
                        self._live_noise_floor_db = spectrum.get("noise_floor_db", -120.0)
                        await self._broadcast_ws({
                            "type": "spectrum",
                            "bins": spectrum["bins"],
                            "f_max": spectrum["f_max"],
                            "snr_db": spectrum["snr_db"],
                            "noise_floor_db": spectrum["noise_floor_db"],
                            "peak_frequency_hz": spectrum["peak_frequency_hz"],
                            "signal_active": self._signal_active,
                        })

                    # 每 5 次推送一次实时数据 (降低频率)
                    _push_counter[0] += 1
                    if _push_counter[0] % 5 == 0:
                        await self._broadcast_ws({
                            "type": "realtime",
                            "rms": round(self._current_rms, 5),
                            "smeter": round(smeter, 1),
                            "snr_db": round(self._live_snr_db, 1),
                            "noise_floor_db": round(self._live_noise_floor_db, 1),
                            "rms_noise_floor": round(self._rms_noise_floor, 5),
                            "dropped_frames": self._dropped_frames,
                            "signal_active": self._signal_active,
                            "total_signals": self._total_signals,
                            "elapsed": round(time.time() - self._monitor_start_time, 0),
                            "reconnects": self._reconnect_count,
                            "squelch_mode": self._squelch.mode,
                            "effective_open": round(
                                self._squelch.effective_open_threshold, 5),
                            "effective_close": round(
                                self._squelch.effective_close_threshold, 5),
                            "threshold_below_floor": self._squelch.threshold_below_floor,
                            "signal_seconds": round(self._squelch.signal_duration, 0),
                        })

                await self._kiwi_client.start_audio_stream(process_audio)

                # 等待断开或用户停止
                while self._monitoring and self._kiwi_client.connected:
                    await asyncio.sleep(0.5)

                # 清理当前连接。
                # 先把正在录的那段信号收尾: 直接停录音器的话，WAV 留在磁盘上
                # 却没有任何数据库记录 —— 长时间监听里每次重连都会丢一段。
                self._close_open_signal(
                    "monitor-stop" if not self._monitoring else "disconnect")
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
                try:
                    self._close_open_signal("error")
                except Exception:
                    pass
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

    def _close_open_signal(self, reason: str):
        """
        断线/停止/异常时，把正在进行的信号正常收尾。

        走 SquelchDetector.force_close() 而不是直接停录音器，
        这样 on_close 回调照常跑: 录音落库、分析入库、页面收到事件。
        """
        if self._squelch and self._squelch.is_open:
            logger.info(f"监听中断，强制结束当前信号 (reason={reason})")
            try:
                self._squelch.force_close(reason)
            except Exception as e:
                logger.error(f"强制结束信号失败: {e}", exc_info=True)
        self._signal_active = False

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
