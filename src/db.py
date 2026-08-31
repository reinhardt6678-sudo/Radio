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

        # Node table increments: audio liveness tally. A node can be reachable, fast and
        # still deliver nothing but digital silence -- latency and handshake cannot see that,
        # so it has to be remembered across runs or the same node gets picked again.
        # 节点表的增量字段: 音频活性计数。一个节点可以连得上、延迟很低，
        # 却只送数字静音 —— 延迟和握手都看不出来，所以必须跨进程记住，
        # 否则下次还会挑中同一个。
        self._migrate_columns(cursor, "nodes", {
            "audio_dead_checks": "INTEGER DEFAULT 0",
            "audio_ok_checks": "INTEGER DEFAULT 0",
            "last_audio_check": "TEXT",
        })

        # 分析表的增量字段 (老数据库直接补列，不丢历史数据)
        self._migrate_columns(cursor, "analysis", {
            "modulation_confidence": "REAL",
            "demod_mode": "TEXT",
            "noise_floor_db": "REAL",
            "envelope_rate_hz": "REAL",
            "envelope_depth": "REAL",
            "tone_count": "INTEGER",
            # 人声结构评分 (见 analyzer._speech_analysis)
            "syllabic_ratio": "REAL",
            "passband_tilt_db": "REAL",
            "speech_score": "REAL",
        })

        # 创建索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_timestamp
            ON signals(timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_analysis_signal
            ON analysis(signal_id)
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

    @staticmethod
    def _migrate_columns(cursor, table: str, columns: Dict[str, str]):
        """给已存在的表补上缺失的列 (SQLite 不支持 ADD COLUMN IF NOT EXISTS)。"""
        existing = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
        for name, col_type in columns.items():
            if name not in existing:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")

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
                      notes: str = "",
                      modulation_confidence: float = None,
                      demod_mode: str = None,
                      noise_floor_db: float = None,
                      envelope_rate_hz: float = None,
                      envelope_depth: float = None,
                      tone_count: int = None,
                      syllabic_ratio: float = None,
                      passband_tilt_db: float = None,
                      speech_score: float = None) -> int:
        """保存信号分析结果。"""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO analysis
                (signal_id, timestamp, peak_frequency_hz, bandwidth_hz, snr_db,
                 estimated_modulation, spectral_centroid_hz, spectral_flatness,
                 crest_factor_db, energy_total, fft_peak_magnitudes, notes,
                 modulation_confidence, demod_mode, noise_floor_db,
                 envelope_rate_hz, envelope_depth, tone_count,
                 syllabic_ratio, passband_tilt_db, speech_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?)
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
                notes,
                modulation_confidence,
                demod_mode,
                noise_floor_db,
                envelope_rate_hz,
                envelope_depth,
                tone_count,
                syllabic_ratio,
                passband_tilt_db,
                speech_score,
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

    def get_signals_with_analysis(self, days: int = 7, limit: int = 200,
                                  frequency_khz: float = None,
                                  with_recording: bool = False,
                                  min_snr_db: float = None) -> List[Dict]:
        """
        获取信号记录及其分析结果 (供 Web 录音浏览用)。

        Args:
            days: 最近 N 天
            limit: 最多返回条数
            frequency_khz: 只看某个频率
            with_recording: 只返回有录音文件的记录
            min_snr_db: 只返回带内 SNR 不低于该值的记录
        """
        where = ["s.timestamp >= datetime('now', ?)"]
        params: List[Any] = [f"-{days} days"]

        if frequency_khz is not None:
            where.append("s.frequency_khz = ?")
            params.append(frequency_khz)
        if with_recording:
            where.append("s.recording_path IS NOT NULL AND s.recording_path != ''")
        if min_snr_db is not None:
            where.append("a.snr_db >= ?")
            params.append(min_snr_db)

        params.append(limit)
        cursor = self.conn.execute(f"""
            SELECT
                s.id, s.session_id, s.timestamp, s.frequency_khz, s.mode,
                s.node_host, s.node_name, s.duration_seconds, s.peak_rms,
                s.avg_rms, s.s_meter_dbm, s.recording_path, s.description,
                s.network,
                a.snr_db, a.bandwidth_hz, a.peak_frequency_hz,
                a.estimated_modulation, a.modulation_confidence,
                a.spectral_flatness, a.crest_factor_db, a.noise_floor_db,
                a.envelope_rate_hz, a.envelope_depth, a.tone_count,
                   a.syllabic_ratio, a.passband_tilt_db, a.speech_score,
                a.demod_mode
            FROM signals s
            LEFT JOIN analysis a ON a.signal_id = s.id
            WHERE {' AND '.join(where)}
            ORDER BY s.timestamp DESC
            LIMIT ?
        """, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_signal_with_analysis(self, signal_id: int) -> Optional[Dict]:
        """获取单条信号及其分析结果。"""
        cursor = self.conn.execute("""
            SELECT s.*, a.snr_db, a.bandwidth_hz, a.peak_frequency_hz,
                   a.estimated_modulation, a.modulation_confidence,
                   a.spectral_flatness, a.crest_factor_db, a.noise_floor_db,
                   a.envelope_rate_hz, a.envelope_depth, a.tone_count,
                   a.syllabic_ratio, a.passband_tilt_db, a.speech_score,
                   a.demod_mode
            FROM signals s
            LEFT JOIN analysis a ON a.signal_id = s.id
            WHERE s.id = ?
        """, (signal_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_snr_distribution(self, days: int = 7) -> List[Dict]:
        """
        带内 SNR 的分布 (5 dB 一档)，用于判断读数是否健康。

        带内 SNR 对纯底噪是负值，而 SQLite 的 CAST 是向零截断而非向下取整
        (-7.9 → -1 而不是 -2)，所以负数要单独往下推一档，否则 0 那一档会
        横跨 -5 到 +5，正好把这个指标最该说明问题的区间搅乱。
        """
        cursor = self.conn.execute("""
            SELECT
                CAST(a.snr_db / 5.0 AS INTEGER) * 5
                    - (CASE WHEN a.snr_db < CAST(a.snr_db / 5.0 AS INTEGER) * 5
                            THEN 5 ELSE 0 END) AS bucket_db,
                COUNT(*) as count
            FROM analysis a
            JOIN signals s ON a.signal_id = s.id
            WHERE s.timestamp >= datetime('now', ?) AND a.snr_db IS NOT NULL
            GROUP BY bucket_db
            ORDER BY bucket_db ASC
        """, (f"-{days} days",))
        return [dict(row) for row in cursor.fetchall()]

    def get_daily_activity(self, days: int = 14) -> List[Dict]:
        """按天统计信号数量与总时长。"""
        cursor = self.conn.execute("""
            SELECT
                date(timestamp) as day,
                COUNT(*) as signal_count,
                SUM(duration_seconds) as total_duration
            FROM signals
            WHERE timestamp >= datetime('now', ?)
            GROUP BY day
            ORDER BY day ASC
        """, (f"-{days} days",))
        return [dict(row) for row in cursor.fetchall()]

    def get_modulation_stats(self, days: int = 7) -> List[Dict]:
        """获取调制类型统计。"""
        cursor = self.conn.execute("""
            SELECT
                a.estimated_modulation,
                COUNT(*) as count,
                AVG(a.snr_db) as avg_snr,
                AVG(a.bandwidth_hz) as avg_bandwidth,
                AVG(a.modulation_confidence) as avg_confidence
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

    def get_node_signal_quality(self, min_snr_db: float = 6.0) -> Dict[str, Dict]:
        """
        按节点统计历史接收质量。

        用来回答"这个节点到底听不听得见我要的频率" —— 光看连通性和延迟不够，
        HF 收得到什么取决于地理位置，延迟最低的节点可能离发射台半个地球。

        Args:
            min_snr_db: 带内 SNR 到这个数才算"真听见了"

        Returns:
            {node_host: {"total": 总信号数, "useful": 达标条数}}
        """
        cursor = self.conn.execute("""
            SELECT s.node_host AS host,
                   COUNT(*) AS total,
                   SUM(CASE WHEN a.snr_db >= ? THEN 1 ELSE 0 END) AS useful
            FROM signals s
            LEFT JOIN analysis a ON a.signal_id = s.id
            WHERE s.node_host IS NOT NULL
            GROUP BY s.node_host
        """, (min_snr_db,))
        return {
            row["host"]: {"total": row["total"], "useful": row["useful"] or 0}
            for row in cursor.fetchall()
        }

    def record_node_audio(self, host: str, port: int, alive: bool):
        """
        Record one observation of whether a node actually delivered audio.

        Called once per connection leg by the receiver. Kept as two counters rather than a
        single flag so a node that glitched once is not condemned forever -- see
        node_manager.node_quality_tier for how they are read.

        记录一次"该节点到底出没出音频"的观测。

        由接收器每条连接调用一次。用两个计数器而不是一个标志位，
        是为了不让偶尔抽风一次的节点被永久判死 ——
        怎么读见 node_manager.node_quality_tier。

        Args:
            host: 节点地址 / node address
            port: 端口 / port
            alive: True = 收到了真实起伏的音频 / genuinely varying audio was received
        """
        column = "audio_ok_checks" if alive else "audio_dead_checks"
        with self._lock:
            self.conn.execute(f"""
                UPDATE nodes
                   SET {column} = COALESCE({column}, 0) + 1,
                       last_audio_check = ?
                 WHERE host = ? AND port = ?
            """, (datetime.now(timezone.utc).isoformat(), host, port))
            self.conn.commit()

    def get_node_audio_health(self) -> Dict[str, Dict]:
        """
        Per-node audio liveness tally.
        按节点统计音频活性。

        Returns:
            {node_host: {"dead": 哑音次数 / mute observations,
                         "ok": 正常次数 / live observations}}
        """
        cursor = self.conn.execute("""
            SELECT host,
                   COALESCE(audio_dead_checks, 0) AS dead,
                   COALESCE(audio_ok_checks, 0)   AS ok
              FROM nodes
        """)
        return {
            row["host"]: {"dead": row["dead"], "ok": row["ok"]}
            for row in cursor.fetchall()
        }

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
