"""
test_node_manager.py - 节点择优

延迟只说明网络快慢，说明不了这个节点收不收得到目标频率。2026-08-21 实测:
HB3YQQ 瑞士延迟最低，连着 5.5 小时、85 条记录、0 条真信号，因为 HFGCS
发射台在美国。这组用例盯的就是"别再按延迟挑一个听不见的节点"。
"""

import pytest

from src.db import Database
from src.node_manager import (
    NODE_QUALITY_MIN_SAMPLES,
    NodeManager,
    node_quality_tier,
)


class TestNodeQualityTier:
    """历史记录分档: 0 = 收到过, 1 = 还不知道, 2 = 有实据说明听不见。"""

    def test_proven_good(self):
        assert node_quality_tier("h", {"h": {"total": 100, "useful": 3}}) == 0

    def test_one_useful_signal_is_enough(self):
        assert node_quality_tier("h", {"h": {"total": 999, "useful": 1}}) == 0

    def test_unknown_when_too_few_samples(self):
        assert node_quality_tier("h", {"h": {"total": 5, "useful": 0}}) == 1

    def test_unknown_when_never_seen(self):
        assert node_quality_tier("h", {}) == 1

    def test_unknown_when_quality_is_none(self):
        assert node_quality_tier("h", None) == 1

    def test_proven_deaf(self):
        stat = {"total": NODE_QUALITY_MIN_SAMPLES, "useful": 0}
        assert node_quality_tier("h", {"h": stat}) == 2

    def test_unknown_ranks_above_proven_deaf(self):
        """没试过的还有机会，试了几百次一条都没有的排最后。"""
        deaf = node_quality_tier("d", {"d": {"total": 500, "useful": 0}})
        unknown = node_quality_tier("u", {})
        assert unknown < deaf


class TestGetBestNode:

    @pytest.fixture
    def mgr(self, tmp_path):
        db = Database(str(tmp_path / "nodes.db"))
        config = {"nodes": []}
        manager = NodeManager(config, db)
        manager._available_nodes = [
            {"host": "fast.example", "name": "Fast", "latency_ms": 10.0},
            {"host": "slow.example", "name": "Slow", "latency_ms": 900.0},
            {"host": "mid.example", "name": "Mid", "latency_ms": 300.0},
        ]
        yield manager
        db.close()

    def _quality(self, mgr, mapping):
        mgr.db.get_node_signal_quality = lambda min_snr_db=6.0: mapping

    def test_none_when_nothing_available(self, mgr):
        mgr._available_nodes = []
        assert mgr.get_best_node() is None

    def test_falls_back_to_latency_without_history(self, mgr):
        self._quality(mgr, {})
        assert mgr.get_best_node()["host"] == "fast.example"

    def test_proven_good_beats_lower_latency(self, mgr):
        """这是今天这个 bug 的核心: 快 ≠ 听得见。"""
        self._quality(mgr, {"slow.example": {"total": 300, "useful": 9}})
        assert mgr.get_best_node()["host"] == "slow.example"

    def test_more_useful_signals_wins_within_tier(self, mgr):
        self._quality(mgr, {
            "fast.example": {"total": 100, "useful": 2},
            "slow.example": {"total": 100, "useful": 20},
        })
        assert mgr.get_best_node()["host"] == "slow.example"

    def test_proven_deaf_ranks_last(self, mgr):
        self._quality(mgr, {"fast.example": {"total": 200, "useful": 0}})
        assert mgr.get_best_node()["host"] == "mid.example"

    def test_db_error_degrades_to_latency(self, mgr):
        """读不出历史就退化成按延迟挑，不能让选节点整个挂掉。"""
        def boom(min_snr_db=6.0):
            raise RuntimeError("no such table")

        mgr.db.get_node_signal_quality = boom
        assert mgr.get_best_node()["host"] == "fast.example"
