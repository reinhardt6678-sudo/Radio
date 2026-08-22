"""
test_db.py - 数据库模块单元测试

测试 Database 的 CRUD 操作、异常保护和会话管理。
"""

import os
import tempfile
import pytest

from src.db import Database


class TestDatabase:
    """Database 核心功能测试。"""

    @pytest.fixture
    def db(self, tmp_path):
        """创建一个临时数据库实例。"""
        db_path = str(tmp_path / "test.db")
        database = Database(db_path)
        yield database
        database.close()

    def test_create_and_query_session(self, db):
        """创建会话并查询。"""
        session_id = db.create_session(
            node_host="localhost:8073",
            node_name="test-node",
            frequencies=[8992.0, 6730.0],
        )
        assert session_id > 0

        sessions = db.get_recent_sessions(limit=10)
        assert len(sessions) >= 1

    def test_save_and_query_signal(self, db):
        """保存信号记录并查询。"""
        session_id = db.create_session(
            node_host="localhost:8073",
            node_name="n1",
            frequencies=[8992.0],
        )
        signal_id = db.record_signal(
            session_id=session_id,
            frequency_khz=8992.0,
            mode="USB",
            node_host="localhost:8073",
            node_name="n1",
            duration_seconds=5.2,
            peak_rms=0.123,
            avg_rms=0.05,
        )
        assert signal_id > 0

        signals = db.get_all_signals(days=1)
        assert len(signals) >= 1
        assert signals[0]["frequency_khz"] == 8992.0

    def test_save_analysis(self, db):
        """保存频谱分析数据。"""
        session_id = db.create_session(
            node_host="localhost:8073",
            node_name="n1",
            frequencies=[6730.0],
        )
        signal_id = db.record_signal(
            session_id=session_id,
            frequency_khz=6730.0,
            mode="USB",
            node_host="localhost:8073",
            node_name="n1",
            duration_seconds=10.0,
            peak_rms=0.2,
            avg_rms=0.08,
        )

        db.save_analysis(
            signal_id=signal_id,
            peak_frequency_hz=1500.0,
            bandwidth_hz=3000.0,
            snr_db=15.5,
            estimated_modulation="USB_VOICE",
            spectral_centroid_hz=1800.0,
            spectral_flatness=0.3,
            crest_factor_db=8.0,
            energy_total=1234.5,
        )

        # 验证通过 get_modulation_stats 查询
        stats = db.get_modulation_stats(days=1)
        assert len(stats) >= 1

    def test_frequency_stats(self, db):
        """频率统计应正确汇总。"""
        session_id = db.create_session(
            node_host="localhost:8073",
            node_name="n1",
            frequencies=[5000.0],
        )

        for _ in range(3):
            db.record_signal(
                session_id=session_id,
                frequency_khz=5000.0,
                mode="AM",
                node_host="localhost:8073",
                node_name="n1",
                duration_seconds=2.0,
                peak_rms=0.1,
                avg_rms=0.05,
            )

        stats = db.get_frequency_stats(days=1)
        found = [s for s in stats if s["frequency_khz"] == 5000.0]
        assert len(found) == 1
        assert found[0]["signal_count"] == 3

    def test_close_exception_protection(self, db):
        """close() 应该不抛出异常（即使底层连接有问题）。"""
        # 强制关闭后再次关闭不应报错
        db.close()
        db.close()  # 第二次不应抛出

    def test_context_manager(self, tmp_path):
        """with 语句上下文管理器应正常工作。"""
        db_path = str(tmp_path / "ctx_test.db")
        with Database(db_path) as db:
            session_id = db.create_session(
                node_host="localhost:8073",
                node_name="ctx",
                frequencies=[1234.0],
            )
            assert session_id > 0

    def _seed_snr(self, db, values):
        """写入若干条只关心 snr_db 的分析记录。"""
        session_id = db.create_session("h", "n", [11175.0])
        for snr in values:
            signal_id = db.record_signal(
                session_id=session_id, frequency_khz=11175.0, mode="USB",
                node_host="h", node_name="n", duration_seconds=5.0,
                peak_rms=0.2, avg_rms=0.1,
            )
            db.save_analysis(signal_id=signal_id, snr_db=snr)

    def test_snr_distribution_buckets(self, db):
        """
        SNR 分档必须向下取整。

        带内 SNR 对纯底噪是负值，而 SQLite 的 CAST 向零截断
        (-7.9/5 = -1.58 → -1)，直接乘 5 会把 -7.9 放进 -5 档，
        让 0 档横跨 -5 到 +5。
        """
        self._seed_snr(db, [-7.9, -2.0, 0.0, 3.0, 7.9, 21.0])

        buckets = {row["bucket_db"]: row["count"]
                   for row in db.get_snr_distribution(days=1)}

        assert buckets == {-10: 1, -5: 1, 0: 2, 5: 1, 20: 1}

    def test_snr_distribution_boundaries(self, db):
        """档位边界值归属下一档 (左闭右开)。"""
        self._seed_snr(db, [-5.0, -0.001, 5.0])

        buckets = {row["bucket_db"]: row["count"]
                   for row in db.get_snr_distribution(days=1)}

        assert buckets == {-5: 2, 5: 1}

    def test_signals_with_analysis_join(self, db):
        """联表查询应带出分析字段，没有分析结果的信号也要返回。"""
        session_id = db.create_session("h", "n", [8992.0])
        analysed = db.record_signal(
            session_id=session_id, frequency_khz=8992.0, mode="USB",
            node_host="h", node_name="n", duration_seconds=6.0,
            peak_rms=0.3, avg_rms=0.2, recording_path="data/recordings/a.wav",
        )
        db.save_analysis(
            signal_id=analysed, snr_db=18.0, estimated_modulation="USB_VOICE",
            modulation_confidence=0.68, demod_mode="USB", noise_floor_db=-72.0,
            envelope_rate_hz=4.1, envelope_depth=0.55, tone_count=6,
        )
        db.record_signal(
            session_id=session_id, frequency_khz=8992.0, mode="USB",
            node_host="h", node_name="n", duration_seconds=1.0,
            peak_rms=0.1, avg_rms=0.05,
        )

        rows = db.get_signals_with_analysis(days=1)
        assert len(rows) == 2

        row = db.get_signal_with_analysis(analysed)
        assert row["modulation_confidence"] == 0.68
        assert row["demod_mode"] == "USB"
        assert row["tone_count"] == 6

        only_snr = db.get_signals_with_analysis(days=1, min_snr_db=10)
        assert [r["id"] for r in only_snr] == [analysed]

        with_file = db.get_signals_with_analysis(days=1, with_recording=True)
        assert [r["id"] for r in with_file] == [analysed]

    def test_migration_adds_columns(self, tmp_path):
        """老数据库缺少新列时应自动补上，且不丢原有数据。"""
        import sqlite3

        db_path = str(tmp_path / "legacy.db")
        legacy = sqlite3.connect(db_path)
        legacy.execute("""
            CREATE TABLE analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                peak_frequency_hz REAL, bandwidth_hz REAL, snr_db REAL,
                estimated_modulation TEXT, spectral_centroid_hz REAL,
                spectral_flatness REAL, crest_factor_db REAL,
                energy_total REAL, fft_peak_magnitudes TEXT, notes TEXT
            )
        """)
        legacy.execute(
            "INSERT INTO analysis (signal_id, timestamp, snr_db, "
            "estimated_modulation) VALUES (1, '2026-06-01T00:00:00', 4.2, 'CW')")
        legacy.commit()
        legacy.close()

        with Database(db_path) as db:
            columns = {row[1] for row in
                       db.conn.execute("PRAGMA table_info(analysis)")}
            for name in ("modulation_confidence", "demod_mode", "noise_floor_db",
                         "envelope_rate_hz", "envelope_depth", "tone_count"):
                assert name in columns

            row = db.conn.execute(
                "SELECT snr_db, estimated_modulation, modulation_confidence "
                "FROM analysis WHERE signal_id = 1").fetchone()
            assert row[0] == 4.2
            assert row[1] == "CW"
            assert row[2] is None


class TestNodeSignalQuality:
    """按节点统计历史接收质量 —— 换节点时用它判断"这个节点听不听得见"。"""

    @pytest.fixture
    def db(self, tmp_path):
        database = Database(str(tmp_path / "quality.db"))
        yield database
        database.close()

    def _signal(self, db, session_id, host, snr):
        sid = db.record_signal(
            session_id=session_id, frequency_khz=11175.0, mode="USB",
            node_host=host, node_name=host, duration_seconds=3.0,
            peak_rms=0.1, avg_rms=0.05,
        )
        if snr is not None:
            db.save_analysis(signal_id=sid, snr_db=snr)
        return sid

    def test_counts_total_and_useful_per_node(self, db):
        s = db.create_session(node_host="a", node_name="a", frequencies=[11175.0])
        self._signal(db, s, "good.example", 12.0)
        self._signal(db, s, "good.example", -5.0)
        self._signal(db, s, "deaf.example", -7.0)
        self._signal(db, s, "deaf.example", -6.0)

        q = db.get_node_signal_quality(min_snr_db=6.0)

        assert q["good.example"] == {"total": 2, "useful": 1}
        assert q["deaf.example"] == {"total": 2, "useful": 0}

    def test_threshold_is_applied(self, db):
        s = db.create_session(node_host="a", node_name="a", frequencies=[11175.0])
        self._signal(db, s, "x.example", 5.0)

        assert db.get_node_signal_quality(min_snr_db=6.0)["x.example"]["useful"] == 0
        assert db.get_node_signal_quality(min_snr_db=3.0)["x.example"]["useful"] == 1

    def test_signal_without_analysis_counts_as_not_useful(self, db):
        """录了但没分析出结果的，不能算这个节点"听见了"。"""
        s = db.create_session(node_host="a", node_name="a", frequencies=[11175.0])
        self._signal(db, s, "y.example", None)

        assert db.get_node_signal_quality()["y.example"] == {"total": 1, "useful": 0}

    def test_empty_db_returns_empty(self, db):
        assert db.get_node_signal_quality() == {}
