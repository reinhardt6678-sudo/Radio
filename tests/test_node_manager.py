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


class TestMutedNodeTier:
    """
    Nodes that send frames without audio rank below even a proven-deaf node.
    只发帧不出声的节点，排得比"证明听不见"的还靠后。

    K1VL Vermont, 2026-08-31: lowest latency of the seven (552 ms), clean handshake, no
    history at all -- so it landed in tier 1 and won on latency, then recorded five all-zero
    files. Latency and the handshake cannot see muteness; only the audio tally can.

    K1VL Vermont 2026-08-31: 七个节点里延迟最低 (552 ms)、握手干净、毫无历史记录 ——
    于是落进第 1 档并靠延迟胜出，接着录了五个全零文件。
    延迟和握手都看不见"哑音"，只有音频计数能看见。
    """

    def test_muted_node_is_worst_tier(self):
        health = {"h": {"dead": 1, "ok": 0}}
        assert node_quality_tier("h", {}, health) == 3

    def test_muted_ranks_below_proven_deaf(self):
        deaf = node_quality_tier("d", {"d": {"total": 500, "useful": 0}})
        muted = node_quality_tier("m", {}, {"m": {"dead": 3, "ok": 0}})
        assert deaf < muted

    def test_one_live_observation_clears_it(self):
        """收到过一次真音频就不再算哑音 —— 免得偶发抽风把节点永久判死。"""
        health = {"h": {"dead": 5, "ok": 1}}
        assert node_quality_tier("h", {}, health) == 1

    def test_good_history_survives_a_mute_observation(self):
        health = {"h": {"dead": 2, "ok": 4}}
        assert node_quality_tier("h", {"h": {"total": 100, "useful": 9}}, health) == 0

    def test_absent_audio_health_is_backwards_compatible(self):
        """老库没有这几列时，分档退回原来的三档。"""
        assert node_quality_tier("h", {"h": {"total": 100, "useful": 3}}) == 0
        assert node_quality_tier("h", {}, None) == 1
        assert node_quality_tier("h", {}, {}) == 1


class TestNodeAudioHealthStore:
    """音频活性计数的读写 / persisting the audio-liveness tally."""

    @pytest.fixture
    def db(self, tmp_path):
        d = Database(str(tmp_path / "t.db"))
        d.upsert_node(host="a.example", port=8073, name="A", location="",
                      lat=None, lon=None, is_available=True, latency_ms=10.0)
        yield d

    def test_starts_empty(self, db):
        assert db.get_node_audio_health()["a.example"] == {"dead": 0, "ok": 0}

    def test_records_dead_and_ok(self, db):
        db.record_node_audio("a.example", 8073, alive=False)
        db.record_node_audio("a.example", 8073, alive=False)
        db.record_node_audio("a.example", 8073, alive=True)
        assert db.get_node_audio_health()["a.example"] == {"dead": 2, "ok": 1}

    def test_unknown_node_is_a_noop(self, db):
        db.record_node_audio("nope.example", 8073, alive=False)
        assert "nope.example" not in db.get_node_audio_health()

    def test_feeds_the_tier_function(self, db):
        db.record_node_audio("a.example", 8073, alive=False)
        health = db.get_node_audio_health()
        assert node_quality_tier("a.example", {}, health) == 3
