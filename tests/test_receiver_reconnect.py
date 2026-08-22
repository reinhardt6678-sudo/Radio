"""
test_receiver_reconnect.py - 命令行 monitor 的断线重连与换节点

背景: 公共 KiwiSDR 节点随时会踢人 (K1VL 是 ip_limit=240 分钟/IP，
忙的节点直接回 too_busy)。以前掉线就直接退出，一次掉线能把后面几个小时
整段空掉 —— 这组用例盯的就是"掉线之后还得自己接着听"。
"""

import asyncio
import time

import pytest

from src import receiver as receiver_mod
from src.db import Database
from src.receiver import FrequencyTarget, SignalReceiver


NODES = [
    {"host": "a.example", "port": 8073, "name": "A", "man_gain": 70},
    {"host": "b.example", "port": 8073, "name": "B", "man_gain": 71},
    {"host": "c.example", "port": 8073, "name": "C", "man_gain": 72},
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """接收器 + 干净的库，退避时间压到 0 免得测试真的等。"""
    db = Database(str(tmp_path / "reconnect.db"))
    config = {
        "nodes": [dict(n) for n in NODES],
        "receiver": {"sample_rate": 12000, "scan_dwell_time": 1},
        "recording": {"output_dir": str(tmp_path / "rec")},
        "squelch": {"open_threshold": 0.10, "close_threshold": 0.085},
        "analysis": {},
    }
    monkeypatch.setattr(receiver_mod, "RECONNECT_BASE_BACKOFF", 0.0)
    monkeypatch.setattr(receiver_mod, "RECONNECT_MAX_BACKOFF", 0.0)
    rx = SignalReceiver(config, db)
    yield rx, db, config
    db.close()


FREQS = [
    FrequencyTarget(11175.0, "USB", "HFGCS 日间主频"),
    FrequencyTarget(8992.0, "USB", "HFGCS 全天"),
]


def _fake_legs(rx, reasons):
    """
    用一串预设的退出原因替掉真正的连接，返回每条连接用的节点 host。

    reasons 用完之后强制 stop()，避免测试里跑成死循环。
    """
    used_hosts = []
    pending = list(reasons)

    async def fake_leg(host, port, node_name, freqs, duration):
        used_hosts.append(host)
        if not pending:
            rx.stop()
            return "stopped"
        return pending.pop(0)

    rx._monitor_multiple_frequencies = fake_leg
    return used_hosts


# ==================== 备用节点挑选 ====================

class TestPickAlternativeNode:

    def test_skips_tried_hosts(self, env):
        rx, _, _ = env
        alt = rx._pick_alternative_node(NODES[0], {"a.example"})
        assert alt["host"] != "a.example"

    def test_prefers_db_available_and_low_latency(self, env):
        rx, _, _ = env
        # c 探测过可用且延迟最低，尽管它在配置里排最后
        rx.db.get_available_nodes = lambda: [
            {"host": "c.example", "avg_latency_ms": 100.0},
            {"host": "b.example", "avg_latency_ms": 900.0},
        ]
        alt = rx._pick_alternative_node(NODES[0], {"a.example"})
        assert alt["host"] == "c.example"

    def test_returned_node_carries_config_gain(self, env):
        """必须返回配置里那份 —— 数据库那份没有 man_gain，会把增益标定丢掉。"""
        rx, _, _ = env
        rx.db.get_available_nodes = lambda: [{"host": "b.example",
                                             "avg_latency_ms": 10.0}]
        alt = rx._pick_alternative_node(NODES[0], {"a.example"})
        assert alt["man_gain"] == 71

    def test_wraps_around_when_all_tried(self, env):
        """一圈全试过了也不能返回 None，否则重连循环就卡死在坏节点上。"""
        rx, _, _ = env
        tried = {n["host"] for n in NODES}
        alt = rx._pick_alternative_node(NODES[0], tried)
        assert alt is not None
        assert alt["host"] != "a.example"

    def test_none_when_no_other_node(self, env):
        rx, _, config = env
        config["nodes"] = [dict(NODES[0])]
        assert rx._pick_alternative_node(NODES[0], {"a.example"}) is None

    def test_survives_db_error(self, env):
        """数据库读不出来只影响排序，不能让整个重连挂掉。"""
        rx, _, _ = env

        def boom():
            raise RuntimeError("db gone")

        rx.db.get_available_nodes = boom
        assert rx._pick_alternative_node(NODES[0], {"a.example"}) is not None


# ==================== 重连主循环 ====================

class TestMonitorReconnect:

    def test_reconnects_after_disconnect(self, env):
        """掉线不再是终点: 断三次要能接着连第四次。"""
        rx, _, _ = env
        hosts = _fake_legs(rx, ["disconnected"] * 3)

        asyncio.run(rx.monitor(node=dict(NODES[0]), frequencies=FREQS))

        assert len(hosts) == 4  # 3 次掉线 + 最后一条

    def test_one_session_across_reconnects(self, env):
        """重连和换节点算同一次监听，不能在库里散成一堆碎会话。"""
        rx, db, _ = env
        _fake_legs(rx, ["disconnected", "connect_failed", "disconnected"])

        asyncio.run(rx.monitor(node=dict(NODES[0]), frequencies=FREQS))

        assert db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1

    def test_switches_node_after_repeated_failures(self, env):
        """同一节点连着失败就得换 —— K1VL 撞了 ip_limit 之后重连它是白搭。"""
        rx, _, _ = env
        hosts = _fake_legs(rx, ["connect_failed"] * 5)

        asyncio.run(rx.monitor(node=dict(NODES[0]), frequencies=FREQS))

        assert hosts[:3] == ["a.example"] * 3      # 先在原节点试满 3 次
        assert hosts[3] != "a.example"             # 然后换掉

    def test_duration_ends_run_without_reconnect(self, env):
        """到时长是正常收工，不能再往下重连。"""
        rx, _, _ = env
        hosts = _fake_legs(rx, ["duration", "disconnected"])

        asyncio.run(rx.monitor(node=dict(NODES[0]), frequencies=FREQS,
                               duration=3600))

        assert len(hosts) == 1

    def test_duration_not_restarted_on_reconnect(self, env):
        """重连后剩余时长要接着扣，否则 --duration 会被无限续期。"""
        rx, _, _ = env
        seen = []

        async def fake_leg(host, port, node_name, freqs, duration):
            seen.append(duration)
            if len(seen) >= 3:
                rx.stop()
                return "stopped"
            await asyncio.sleep(0.05)
            return "disconnected"

        rx._monitor_multiple_frequencies = fake_leg

        asyncio.run(rx.monitor(node=dict(NODES[0]), frequencies=FREQS,
                               duration=10))

        assert seen == sorted(seen, reverse=True)
        assert seen[-1] < 10

    def test_stop_breaks_out(self, env):
        """Ctrl+C 之后不能被退避拖住，也不能继续重连。"""
        rx, _, _ = env
        hosts = []

        async def fake_leg(host, port, node_name, freqs, duration):
            hosts.append(host)
            rx.stop()
            return "disconnected"

        rx._monitor_multiple_frequencies = fake_leg

        asyncio.run(rx.monitor(node=dict(NODES[0]), frequencies=FREQS))

        assert len(hosts) == 1

    def test_leg_exception_is_retried_not_fatal(self, env):
        """一条连接炸了只报废这一条，整轮监听得活下来。"""
        rx, _, _ = env
        calls = []

        async def fake_leg(host, port, node_name, freqs, duration):
            calls.append(host)
            if len(calls) == 1:
                raise OSError("socket exploded")
            rx.stop()
            return "stopped"

        rx._monitor_multiple_frequencies = fake_leg

        asyncio.run(rx.monitor(node=dict(NODES[0]), frequencies=FREQS))

        assert len(calls) == 2

    def test_single_frequency_path_also_reconnects(self, env):
        """单频监听走的是另一条分支，同样不能掉线就退。"""
        rx, _, _ = env
        calls = []

        async def fake_leg(host, port, node_name, freq, duration):
            calls.append(host)
            if len(calls) >= 3:
                rx.stop()
                return "stopped"
            return "disconnected"

        rx._monitor_single_frequency = fake_leg

        asyncio.run(rx.monitor(node=dict(NODES[0]), frequencies=FREQS[:1]))

        assert len(calls) == 3


# ==================== 可打断等待 ====================

class TestSleepInterruptible:

    def test_returns_true_when_slept_through(self, env):
        rx, _, _ = env
        rx._running = True
        assert asyncio.run(rx._sleep_interruptible(0.01)) is True

    def test_returns_false_when_stopped(self, env):
        rx, _, _ = env
        rx._running = False
        assert asyncio.run(rx._sleep_interruptible(30)) is False


# ==================== 按接收质量挑节点 ====================

class TestNodeQualityRanking:
    """
    换节点不能只看延迟。2026-08-21 实测: HB3YQQ 瑞士延迟最低，
    连了 5.5 小时 85 条记录 0 条真信号 —— 因为 HFGCS 发射台在美国。
    """

    def _quality(self, rx, mapping):
        rx.db.get_node_signal_quality = lambda min_snr_db=6.0: mapping

    def test_proven_good_beats_low_latency_unknown(self, env):
        """延迟低但没记录的，不该压过真收到过东西的。"""
        rx, _, _ = env
        rx.db.get_available_nodes = lambda: [
            {"host": "b.example", "avg_latency_ms": 5.0},
            {"host": "c.example", "avg_latency_ms": 800.0},
        ]
        self._quality(rx, {"c.example": {"total": 300, "useful": 9}})

        assert rx._pick_alternative_node(NODES[0], {"a.example"})["host"] == "c.example"

    def test_proven_deaf_ranks_below_unknown(self, env):
        """没试过的还有机会，试了几百次一条都没有的排最后。"""
        rx, _, _ = env
        rx.db.get_available_nodes = lambda: [
            {"host": "b.example", "avg_latency_ms": 5.0},
            {"host": "c.example", "avg_latency_ms": 10.0},
        ]
        self._quality(rx, {"b.example": {"total": 200, "useful": 0}})

        assert rx._pick_alternative_node(NODES[0], {"a.example"})["host"] == "c.example"

    def test_more_useful_signals_wins(self, env):
        rx, _, _ = env
        self._quality(rx, {
            "b.example": {"total": 100, "useful": 2},
            "c.example": {"total": 100, "useful": 20},
        })

        assert rx._pick_alternative_node(NODES[0], {"a.example"})["host"] == "c.example"

    def test_latency_still_breaks_ties(self, env):
        """同一档里没别的可比，才轮到延迟说话。"""
        rx, _, _ = env
        rx.db.get_available_nodes = lambda: [
            {"host": "c.example", "avg_latency_ms": 20.0},
            {"host": "b.example", "avg_latency_ms": 900.0},
        ]
        self._quality(rx, {})

        assert rx._pick_alternative_node(NODES[0], {"a.example"})["host"] == "c.example"

    def test_quality_query_error_is_not_fatal(self, env):
        rx, _, _ = env

        def boom(min_snr_db=6.0):
            raise RuntimeError("no such column")

        rx.db.get_node_signal_quality = boom
        assert rx._pick_alternative_node(NODES[0], {"a.example"}) is not None


# ==================== 回首选节点 ====================

class TestReturnToPreferred:
    """
    被迫换走之后要回来。今天的教训: 08:26 掉到瑞士节点，原节点早恢复了，
    5.5 小时没人把它切回去。
    """

    def test_not_due_before_interval(self, env):
        rx, _, _ = env
        rx._left_preferred_at = time.time()
        assert rx._should_return_to_preferred() is False

    def test_not_due_when_never_left(self, env):
        rx, _, _ = env
        rx._left_preferred_at = None
        assert rx._should_return_to_preferred() is False

    def test_due_after_interval(self, env):
        rx, _, _ = env
        rx._left_preferred_at = time.time() - receiver_mod.PREFERRED_RETRY_SECONDS - 1
        assert rx._should_return_to_preferred() is True

    def test_marks_departure_time_when_switching_away(self, env):
        rx, _, _ = env
        _fake_legs(rx, ["connect_failed"] * 3)

        asyncio.run(rx.monitor(node=dict(NODES[0]), frequencies=FREQS))

        assert rx._left_preferred_at is not None

    def test_departure_time_not_reset_by_later_switches(self, env):
        """A→B→C 三次换节点，计时点必须停在离开 A 那一刻。"""
        rx, _, _ = env
        stamps = []

        async def fake_leg(host, port, node_name, freqs, duration):
            stamps.append(rx._left_preferred_at)
            if len(stamps) >= 9:
                rx.stop()
                return "stopped"
            return "connect_failed"

        rx._monitor_multiple_frequencies = fake_leg

        asyncio.run(rx.monitor(node=dict(NODES[0]), frequencies=FREQS))

        marked = [s for s in stamps if s is not None]
        assert marked, "换走之后应该记下离开首选节点的时刻"
        assert len(set(marked)) == 1, "后续换节点不能重置这个时刻"

    def test_goes_back_to_preferred(self, env):
        """收到 try_preferred 就得切回首选节点，而且不走退避。"""
        rx, _, _ = env
        hosts = []

        async def fake_leg(host, port, node_name, freqs, duration):
            hosts.append(host)
            if len(hosts) <= 3:
                return "connect_failed"      # 逼它换走
            if len(hosts) == 4:
                return "try_preferred"       # 时候到了，回去
            rx.stop()
            return "stopped"

        rx._monitor_multiple_frequencies = fake_leg

        asyncio.run(rx.monitor(node=dict(NODES[0]), frequencies=FREQS))

        assert hosts[3] != "a.example"       # 已经换走了
        assert hosts[4] == "a.example"       # 又回来了

    def test_return_clears_departure_time(self, env):
        rx, _, _ = env
        hosts = []

        async def fake_leg(host, port, node_name, freqs, duration):
            hosts.append(host)
            if len(hosts) <= 3:
                return "connect_failed"
            if len(hosts) == 4:
                return "try_preferred"
            rx.stop()
            return "stopped"

        rx._monitor_multiple_frequencies = fake_leg

        asyncio.run(rx.monitor(node=dict(NODES[0]), frequencies=FREQS))

        assert rx._left_preferred_at is None
