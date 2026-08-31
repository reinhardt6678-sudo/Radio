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


def _stub_probe(monkeypatch, available, latency_ms=12.0, error="连接超时"):
    """
    替掉真正的节点探测，返回一个记录被敲过哪些 host 的列表。

    探测本身是 test_connection 单开的一条短连接，跟正在收音的那条无关，
    所以测试里只要盯"敲没敲"和"敲完怎么决定"。
    """
    probed = []

    async def fake_probe(host, port=8073, timeout=10.0):
        probed.append(host)
        return {
            "host": host, "port": port,
            "available": available,
            "latency_ms": latency_ms if available else None,
            "error": None if available else error,
        }

    monkeypatch.setattr(receiver_mod, "test_connection", fake_probe)
    return probed


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

    def test_not_due_before_interval(self, env, monkeypatch):
        rx, _, _ = env
        probed = _stub_probe(monkeypatch, available=True)
        rx._preferred_node = dict(NODES[0])
        rx._left_preferred_at = time.time()

        assert asyncio.run(rx._should_return_to_preferred()) is False
        assert probed == [], "间隔没到就不该去敲门"

    def test_not_due_when_never_left(self, env, monkeypatch):
        rx, _, _ = env
        probed = _stub_probe(monkeypatch, available=True)
        rx._left_preferred_at = None

        assert asyncio.run(rx._should_return_to_preferred()) is False
        assert probed == []

    def test_due_and_probe_ok_returns_true(self, env, monkeypatch):
        rx, _, _ = env
        probed = _stub_probe(monkeypatch, available=True)
        rx._preferred_node = dict(NODES[0])
        rx._left_preferred_at = time.time() - receiver_mod.PREFERRED_RETRY_SECONDS - 1

        assert asyncio.run(rx._should_return_to_preferred()) is True
        assert probed == ["a.example"]

    def test_dead_preferred_does_not_break_current_leg(self, env, monkeypatch):
        """
        这条是这次修的正题: 首选节点敲不通时，绝对不能返回 True ——
        返回 True 就等于把手上正在收的连接掐掉，去连一个明知连不上的节点。
        """
        rx, _, _ = env
        probed = _stub_probe(monkeypatch, available=False)
        rx._preferred_node = dict(NODES[0])
        rx._left_preferred_at = time.time() - receiver_mod.PREFERRED_RETRY_SECONDS - 1

        assert asyncio.run(rx._should_return_to_preferred()) is False
        assert probed == ["a.example"], "该敲的门还是要敲"

    def test_failed_probe_doubles_the_interval(self, env, monkeypatch):
        rx, _, _ = env
        _stub_probe(monkeypatch, available=False)
        rx._preferred_node = dict(NODES[0])
        base = receiver_mod.PREFERRED_RETRY_SECONDS
        rx._preferred_retry_backoff = base
        rx._left_preferred_at = time.time() - base - 1

        asyncio.run(rx._should_return_to_preferred())
        assert rx._preferred_retry_backoff == base * 2

        # 计时重新起算，所以下一次不会立刻又敲
        assert asyncio.run(rx._should_return_to_preferred()) is False

    def test_failed_probe_interval_is_capped(self, env, monkeypatch):
        rx, _, _ = env
        _stub_probe(monkeypatch, available=False)
        rx._preferred_node = dict(NODES[0])
        rx._preferred_retry_backoff = receiver_mod.PREFERRED_RETRY_MAX_SECONDS

        for _ in range(3):
            rx._left_preferred_at = 0.0     # 强制到期
            asyncio.run(rx._should_return_to_preferred())

        assert rx._preferred_retry_backoff == receiver_mod.PREFERRED_RETRY_MAX_SECONDS

    def test_healthy_leg_on_preferred_resets_the_interval(self, env, monkeypatch):
        """在首选节点上重新听顺了，回访间隔要收回最短，不能一直停在放大后的值。"""
        rx, _, _ = env
        monkeypatch.setattr(receiver_mod, "HEALTHY_LEG_SECONDS", 0.0)
        rx._preferred_retry_backoff = receiver_mod.PREFERRED_RETRY_MAX_SECONDS

        _fake_legs(rx, ["disconnected"])
        asyncio.run(rx.monitor(node=dict(NODES[0]), frequencies=FREQS))

        assert rx._preferred_retry_backoff == receiver_mod.PREFERRED_RETRY_SECONDS

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


# ==================== 哑音节点 / muted nodes ====================

class TestMutedNodeHandling:
    """
    A node sending frames without audio must be left immediately, not retried with backoff.
    只发帧不出声的节点必须立刻离开，而不是退避重试。

    Retrying is pointless here in a way that is not true of a dropped connection: the node
    connects fine, has low latency and keeps streaming, so attempt 101 yields the same zeros
    as attempt 1. K1VL Vermont, 2026-08-31: 552 ms latency, clean handshake, 470 frames,
    0 dropped -- and every sample the constant 0.00003.

    这里的重试和普通掉线不一样，是彻底没有意义的: 节点连得上、延迟低、帧照发,
    第 101 次尝试和第 1 次拿到的是同一串零。
    K1VL Vermont 2026-08-31: 延迟 552 ms、握手干净、470 帧、丢 0 帧,
    而每个采样都是常数 0.00003。
    """

    def test_switches_node_without_waiting_for_failure_count(self, env, monkeypatch):
        rx, _, _ = env
        _stub_probe(monkeypatch, available=True)
        used = _fake_legs(rx, ["audio_dead"])

        asyncio.run(rx.monitor(dict(NODES[0]), FREQS, duration=0))

        # 第一条连接在 a，判定哑音后立刻换走 —— 没有在 a 上耗掉 MAX_NODE_FAILURES 次
        assert used[0] == "a.example"
        assert len(used) > 1
        assert used[1] != "a.example"

    def test_a_muted_leg_does_not_count_as_healthy(self, env, monkeypatch):
        """
        哑音连接可以持续很久 (帧一直在收)，不能因为"这条腿活得够长"就重置失败计数。
        A muted leg can last a long time because frames keep arriving; its longevity must
        not reset the failure counter.
        """
        rx, _, _ = env
        _stub_probe(monkeypatch, available=True)
        monkeypatch.setattr(receiver_mod, "HEALTHY_LEG_SECONDS", 0.0)
        used = _fake_legs(rx, ["audio_dead"])

        asyncio.run(rx.monitor(dict(NODES[0]), FREQS, duration=0))

        assert used[1] != "a.example"

    def test_muted_observation_demotes_the_node_next_time(self, env):
        """记进库之后，下一轮挑节点时它应当排到最后。"""
        rx, db, _ = env
        for n in NODES:
            db.upsert_node(host=n["host"], port=n["port"], name=n["name"],
                           location="", lat=None, lon=None,
                           is_available=True, latency_ms=10.0)
        # a 延迟最低，但被观测到哑音
        db.record_node_audio("a.example", 8073, alive=False)
        rx.db.get_available_nodes = lambda: [
            {"host": "a.example", "avg_latency_ms": 1.0},
            {"host": "b.example", "avg_latency_ms": 900.0},
        ]

        alt = rx._pick_alternative_node(NODES[2], set())
        assert alt["host"] == "b.example"

    def test_live_node_still_wins_on_latency(self, env):
        """没有哑音记录时，原来的择优逻辑不受影响。"""
        rx, db, _ = env
        for n in NODES:
            db.upsert_node(host=n["host"], port=n["port"], name=n["name"],
                           location="", lat=None, lon=None,
                           is_available=True, latency_ms=10.0)
        rx.db.get_available_nodes = lambda: [
            {"host": "a.example", "avg_latency_ms": 1.0},
            {"host": "b.example", "avg_latency_ms": 900.0},
        ]
        alt = rx._pick_alternative_node(NODES[2], set())
        assert alt["host"] == "a.example"

    def test_note_node_audio_survives_db_failure(self, env):
        """记账失败不能拖垮监听 —— 它只是为了让下次选得更准。"""
        rx, _, _ = env

        def boom(*a, **kw):
            raise RuntimeError("db down")

        rx.db.record_node_audio = boom
        rx._note_node_audio("a.example", 8073, "A", alive=False)   # 不抛异常即通过
