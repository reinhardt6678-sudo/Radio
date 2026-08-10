"""
db.py - SQLite 数据库管理模块

管理监听会话、信号记录和分析结果的持久化存储。
"""

import sqlite3
import os
import json
import threading
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pathlib import Path


class Database:
    """SQLite 数据库封装，管理所有元数据存储。"""

    def __init__(self, db_path: str):
        """
        初始化数据库连接。

        Args:
            db_path: SQLite 数据库文件路径
        """
        self.db_path = db_path
        self._lock = threading.Lock()  # 线程安全写入锁
        # 确保目录存在
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")  # 提高并发写入性能
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self):
        """创建所有必要的数据表。"""
        cursor = self.conn.cursor()

        # 监听会话表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time TEXT NOT NULL,
                end_time TEXT,
                node_host TEXT,
                node_name TEXT,
                frequencies TEXT,
                status TEXT DEFAULT 'running',
                notes TEXT
            )
        """)

        # 信号记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                timestamp TEXT NOT NULL,
                frequency_khz REAL NOT NULL,
                mode TEXT,
                node_host TEXT,
                node_name TEXT,
                duration_seconds REAL,
                peak_rms REAL,
                avg_rms REAL,
                s_meter_dbm REAL,
                recording_path TEXT,
                description TEXT,
                network TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
        """)

        # 信号分析结果表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                peak_frequency_hz REAL,
                bandwidth_hz REAL,
                snr_db REAL,
                estimated_modulation TEXT,
                spectral_centroid_hz REAL,
                spectral_flatness REAL,
                crest_factor_db REAL,
                energy_total REAL,
                fft_peak_magnitudes TEXT,
                notes TEXT,
                FOREIGN KEY (signal_id) REFERENCES signals(id)
            )
        """)

        # 节点状态表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                name TEXT,
                location TEXT,
                lat REAL,
                lon REAL,
                last_check TEXT,
                is_available INTEGER DEFAULT 0,
                avg_latency_ms REAL,
                total_connections INTEGER DEFAULT 0,
                total_failures INTEGER DEFAULT 0,
                UNIQUE(host, port)
            )
        """)

        # 创建索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_timestamp
            ON signals(timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_frequency
            ON signals(frequency_khz)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_session
            ON signals(session_id)
        """)

        self.conn.commit()

    # ==================== 会话操作 ====================

    def create_session(self, node_host: str, node_name: str,
                       frequencies: List[float], notes: str = "") -> int:
        """创建新的监听会话，返回会话ID。"""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO sessions (start_time, node_host, node_name, frequencies, notes)
                VALUES (?, ?, ?, ?, ?)
            """, (
                datetime.now(timezone.utc).isoformat(),
                node_host,
                node_name,
                json.dumps(frequencies),
                notes
            ))
            self.conn.commit()
            return cursor.lastrowid

    def end_session(self, session_id: int, status: str = "completed"):
        """结束监听会话。"""
        with self._lock:
            self.conn.execute("""
                UPDATE sessions SET end_time = ?, status = ?
                WHERE id = ?
            """, (datetime.now(timezone.utc).isoformat(), status, session_id))
            self.conn.commit()

    def get_recent_sessions(self, limit: int = 20) -> List[Dict]:
        """获取最近的监听会话。"""
        cursor = self.conn.execute("""
            SELECT * FROM sessions ORDER BY start_time DESC LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

    # ==================== 信号操作 ====================

    def record_signal(self, session_id: int, frequency_khz: float,
                      mode: str, node_host: str, node_name: str,
                      duration_seconds: float, peak_rms: float,
                      avg_rms: float, s_meter_dbm: float = None,
                      recording_path: str = None, description: str = "",
                      network: str = "") -> int:
        """记录一个检测到的信号，返回信号ID。"""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO signals
                (session_id, timestamp, frequency_khz, mode, node_host, node_name,
                 duration_seconds, peak_rms, avg_rms, s_meter_dbm,
                 recording_path, description, network)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                datetime.now(timezone.utc).isoformat(),
                frequency_khz,
                mode,
                node_host,
                node_name,
                duration_seconds,
                peak_rms,
                avg_rms,
                s_meter_dbm,
                recording_path,
                description,
                network
            ))
            self.conn.commit()
            return cursor.lastrowid

    def get_signals_by_session(self, session_id: int) -> List[Dict]:
        """获取指定会话的所有信号记录。"""
        cursor = self.conn.execute("""
            SELECT * FROM signals WHERE session_id = ?
            ORDER BY timestamp ASC
        """, (session_id,))
        return [dict(row) for row in cursor.fetchall()]

    def get_signals_by_frequency(self, frequency_khz: float,
                                  days: int = 7) -> List[Dict]:
        """获取指定频率在最近N天内的信号记录。"""
        cursor = self.conn.execute("""
            SELECT * FROM signals
            WHERE frequency_khz = ?
            AND timestamp >= datetime('now', ?)
            ORDER BY timestamp DESC
        """, (frequency_khz, f"-{days} days"))
        return [dict(row) for row in cursor.fetchall()]

    def get_all_signals(self, days: int = 7, limit: int = 500) -> List[Dict]:
        """获取最近N天的所有信号记录。"""
        cursor = self.conn.execute("""
            SELECT * FROM signals
            WHERE timestamp >= datetime('now', ?)
            ORDER BY timestamp DESC
            LIMIT ?
        """, (f"-{days} days", limit))
        return [dict(row) for row in cursor.fetchall()]

    def get_frequency_stats(self, days: int = 7) -> List[Dict]:
        """获取各频率的统计数据。"""
        cursor = self.conn.execute("""
            SELECT
                frequency_khz,
                mode,
                network,
                COUNT(*) as signal_count,
                SUM(duration_seconds) as total_duration,
                AVG(duration_seconds) as avg_duration,
                AVG(peak_rms) as avg_peak_rms,
                MAX(peak_rms) as max_peak_rms,
                AVG(s_meter_dbm) as avg_s_meter
            FROM signals
            WHERE timestamp >= datetime('now', ?)
            GROUP BY frequency_khz
            ORDER BY signal_count DESC
        """, (f"-{days} days",))
        return [dict(row) for row in cursor.fetchall()]

    def get_hourly_activity(self, days: int = 7) -> List[Dict]:
        """获取按小时统计的活动数据。"""
        cursor = self.conn.execute("""
            SELECT
                strftime('%H', timestamp) as hour,
                COUNT(*) as signal_count,
                SUM(duration_seconds) as total_duration
            FROM signals
            WHERE timestamp >= datetime('now', ?)
            GROUP BY hour
            ORDER BY hour ASC
        """, (f"-{days} days",))
        return [dict(row) for row in cursor.fetchall()]

    # ==================== 分析操作 ====================

    def save_analysis(self, signal_id: int,
                      peak_frequency_hz: float = None,
                      bandwidth_hz: float = None,
                      snr_db: float = None,
                      estimated_modulation: str = None,
                      spectral_centroid_hz: float = None,
                      spectral_flatness: float = None,
                      crest_factor_db: float = None,
                      energy_total: float = None,
                      fft_peak_magnitudes: List[float] = None,
                      notes: str = "") -> int:
        """保存信号分析结果。"""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO analysis
                (signal_id, timestamp, peak_frequency_hz, bandwidth_hz, snr_db,
                 estimated_modulation, spectral_centroid_hz, spectral_flatness,
                 crest_factor_db, energy_total, fft_peak_magnitudes, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal_id,
                datetime.now(timezone.utc).isoformat(),
                peak_frequency_hz,
                bandwidth_hz,
                snr_db,
                estimated_modulation,
                spectral_centroid_hz,
                spectral_flatness,
                crest_factor_db,
                energy_total,
                json.dumps(fft_peak_magnitudes) if fft_peak_magnitudes else None,
                notes
            ))
            self.conn.commit()
            return cursor.lastrowid

    def get_analysis_by_signal(self, signal_id: int) -> Optional[Dict]:
        """获取指定信号的分析结果。"""
        cursor = self.conn.execute("""
            SELECT * FROM analysis WHERE signal_id = ?
        """, (signal_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_modulation_stats(self, days: int = 7) -> List[Dict]:
        """获取调制类型统计。"""
        cursor = self.conn.execute("""
            SELECT
                a.estimated_modulation,
                COUNT(*) as count,
                AVG(a.snr_db) as avg_snr,
                AVG(a.bandwidth_hz) as avg_bandwidth
            FROM analysis a
            JOIN signals s ON a.signal_id = s.id
            WHERE s.timestamp >= datetime('now', ?)
            GROUP BY a.estimated_modulation
            ORDER BY count DESC
        """, (f"-{days} days",))
        return [dict(row) for row in cursor.fetchall()]

    # ==================== 节点操作 ====================

    def upsert_node(self, host: str, port: int, name: str = "",
                    location: str = "", lat: float = None,
                    lon: float = None, is_available: bool = False,
                    latency_ms: float = None):
        """插入或更新节点信息。"""
        with self._lock:
            self.conn.execute("""
                INSERT INTO nodes (host, port, name, location, lat, lon,
                                 last_check, is_available, avg_latency_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(host, port) DO UPDATE SET
                    name = excluded.name,
                    location = excluded.location,
                    lat = excluded.lat,
                    lon = excluded.lon,
                    last_check = excluded.last_check,
                    is_available = excluded.is_available,
                    avg_latency_ms = excluded.avg_latency_ms,
                    total_connections = total_connections + CASE WHEN excluded.is_available THEN 1 ELSE 0 END,
                    total_failures = total_failures + CASE WHEN excluded.is_available THEN 0 ELSE 1 END
            """, (
                host, port, name, location, lat, lon,
                datetime.now(timezone.utc).isoformat(),
                1 if is_available else 0,
                latency_ms
            ))
            self.conn.commit()

    def get_available_nodes(self) -> List[Dict]:
        """获取所有可用节点。"""
        cursor = self.conn.execute("""
            SELECT * FROM nodes WHERE is_available = 1
            ORDER BY avg_latency_ms ASC
        """)
        return [dict(row) for row in cursor.fetchall()]

    def get_all_nodes(self) -> List[Dict]:
        """获取所有节点。"""
        cursor = self.conn.execute("""
            SELECT * FROM nodes ORDER BY name ASC
        """)
        return [dict(row) for row in cursor.fetchall()]

    # ==================== 工具方法 ====================

    def close(self):
        """关闭数据库连接。"""
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
