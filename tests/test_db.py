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
