"""
test_web_api.py - Web 监听界面后端接口测试

覆盖新增的录音浏览/回放、频谱图和静噪在线调整接口，
特别是回放接口不能被路径穿越绕出录音目录。
"""

import asyncio
import wave

import numpy as np
import pytest

from aiohttp.test_utils import TestClient, TestServer

from src.db import Database
from src.web_server import WebMonitorServer


SR = 12000


def _write_wav(path, seconds=2.0, freq=1200.0, sample_rate=SR):
    t = np.arange(int(sample_rate * seconds)) / sample_rate
    pcm = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return path


@pytest.fixture
def env(tmp_path):
    """一套完整的 db + 录音目录 + 服务器。"""
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    db = Database(str(tmp_path / "web.db"))

    config = {
        "nodes": [{"host": "kiwi.example", "port": 8073, "name": "Test Node"}],
        "receiver": {"sample_rate": SR},
        "recording": {"output_dir": str(recordings)},
        "squelch": {"open_threshold": 0.10, "close_threshold": 0.085,
                    "tail_time": 3.0},
        "analysis": {"fft_size": 2048},
    }
    freq_data = {"hfgcs": {"description": "HFGCS",
                           "frequencies": [{"freq": 11175.0, "mode": "USB",
                                            "priority": "high"}]}}

    server = WebMonitorServer(config, db, freq_data)

    session_id = db.create_session("kiwi.example", "Test Node", [11175.0])
    wav_path = _write_wav(recordings / "20260601_120000_11175.0kHz_Test.wav")
    signal_id = db.record_signal(
        session_id=session_id, frequency_khz=11175.0, mode="USB",
        node_host="kiwi.example", node_name="Test Node",
        duration_seconds=2.0, peak_rms=0.3, avg_rms=0.2,
        s_meter_dbm=-92.0, recording_path=str(wav_path),
    )
    db.save_analysis(
        signal_id=signal_id, snr_db=21.5, bandwidth_hz=1800.0,
        estimated_modulation="USB_VOICE", modulation_confidence=0.72,
        demod_mode="USB", noise_floor_db=-78.0, envelope_rate_hz=4.2,
        envelope_depth=0.61, tone_count=5,
    )

    # 没有录音文件的信号
    orphan_id = db.record_signal(
        session_id=session_id, frequency_khz=8992.0, mode="USB",
        node_host="kiwi.example", node_name="Test Node",
        duration_seconds=1.0, peak_rms=0.1, avg_rms=0.05,
        recording_path=str(recordings / "missing.wav"),
    )

    yield {"server": server, "db": db, "signal_id": signal_id,
           "orphan_id": orphan_id, "recordings": recordings, "config": config}
    db.close()


def with_client(server, scenario):
    """
    在临时事件循环里起一个测试服务器，把客户端交给 scenario 协程。

    没有用 pytest-asyncio / aiohttp.pytest_plugin，避免给项目增加测试依赖。
    """
    async def runner():
        client = TestClient(TestServer(server.create_app()))
        await client.start_server()
        try:
            return await scenario(client)
        finally:
            await client.close()

    return asyncio.run(runner())


class TestRecordingsApi:
    """录音浏览与回放。"""

    def test_list_recordings(self, env):
        async def scenario(c):
            resp = await c.get("/api/recordings?days=30")
            assert resp.status == 200
            return await resp.json()

        rows = with_client(env["server"], scenario)
        assert len(rows) == 2
        found = [r for r in rows if r["id"] == env["signal_id"]][0]
        assert found["has_audio"] is True
        assert found["estimated_modulation"] == "USB_VOICE"
        assert found["modulation_confidence"] == 0.72
        assert found["snr_db"] == 21.5
        assert found["filename"].endswith(".wav")
        # 不应把服务器上的路径暴露出去
        assert "recording_path" not in found

    def test_missing_file_flagged(self, env):
        async def scenario(c):
            return await (await c.get("/api/recordings?days=30")).json()

        rows = with_client(env["server"], scenario)
        orphan = [r for r in rows if r["id"] == env["orphan_id"]][0]
        assert orphan["has_audio"] is False

    def test_filter_with_recording(self, env):
        async def scenario(c):
            resp = await c.get(
                "/api/recordings?days=30&with_recording=1&min_snr=10")
            return await resp.json()

        rows = with_client(env["server"], scenario)
        assert [r["id"] for r in rows] == [env["signal_id"]]

    def test_play_audio(self, env):
        async def scenario(c):
            resp = await c.get(f"/api/recordings/{env['signal_id']}/audio")
            return resp.status, resp.headers.get("Content-Type"), await resp.read()

        status, content_type, body = with_client(env["server"], scenario)
        assert status == 200
        assert content_type == "audio/wav"
        assert body[:4] == b"RIFF"

    def test_play_missing_audio(self, env):
        async def scenario(c):
            return (await c.get(f"/api/recordings/{env['orphan_id']}/audio")).status

        assert with_client(env["server"], scenario) == 404

    def test_play_unknown_signal(self, env):
        async def scenario(c):
            return (await c.get("/api/recordings/999999/audio")).status

        assert with_client(env["server"], scenario) == 404

    def test_bad_signal_id(self, env):
        async def scenario(c):
            return (await c.get("/api/recordings/abc/audio")).status

        assert with_client(env["server"], scenario) == 400

    def test_path_traversal_rejected(self, env, tmp_path):
        """数据库里的路径指向录音目录之外时必须拒绝。"""
        _write_wav(tmp_path / "secret.wav")
        db = env["db"]
        session_id = db.create_session("h", "n", [1.0])
        bad_id = db.record_signal(
            session_id=session_id, frequency_khz=1.0, mode="USB",
            node_host="h", node_name="n", duration_seconds=1.0,
            peak_rms=0.1, avg_rms=0.1,
            recording_path=str(env["recordings"] / ".." / "secret.wav"),
        )

        async def scenario(c):
            return (await c.get(f"/api/recordings/{bad_id}/audio")).status

        assert with_client(env["server"], scenario) == 404

    def test_spectrogram(self, env):
        async def scenario(c):
            resp = await c.get(
                f"/api/recordings/{env['signal_id']}/spectrogram?bins=32&cols=40")
            assert resp.status == 200
            return await resp.json()

        data = with_client(env["server"], scenario)
        assert data["n_freq"] > 0 and data["n_time"] > 0
        assert len(data["matrix"]) == data["n_freq"] * data["n_time"]
        assert all(0 <= v <= 255 for v in data["matrix"][:50])
        assert data["passband"] == [300.0, 3000.0]
        assert len(data["envelope"]) > 0


class TestSquelchApi:
    """静噪阈值在线调整。"""

    def test_get_squelch(self, env):
        async def scenario(c):
            return await (await c.get("/api/squelch")).json()

        data = with_client(env["server"], scenario)
        assert data["open_threshold"] == 0.10
        assert "suggested_open" in data

    def test_set_squelch(self, env):
        async def scenario(c):
            return (await c.post("/api/squelch", json={
                "open_threshold": 0.2, "close_threshold": 0.15})).status

        assert with_client(env["server"], scenario) == 200
        assert env["config"]["squelch"]["open_threshold"] == 0.2

    def test_close_must_be_below_open(self, env):
        async def scenario(c):
            return (await c.post("/api/squelch", json={
                "open_threshold": 0.1, "close_threshold": 0.5})).status

        assert with_client(env["server"], scenario) == 400

    def test_rejects_out_of_range(self, env):
        async def scenario(c):
            return (await c.post("/api/squelch",
                                 json={"open_threshold": 5.0})).status

        assert with_client(env["server"], scenario) == 400

    def test_rejects_garbage(self, env):
        async def scenario(c):
            return (await c.post("/api/squelch",
                                 json={"open_threshold": "abc"})).status

        assert with_client(env["server"], scenario) == 400

    def test_applies_to_running_detector(self, env):
        from src.squelch import SquelchDetector
        server = env["server"]
        server._squelch = SquelchDetector(open_threshold=0.1, close_threshold=0.08)

        async def scenario(c):
            return (await c.post("/api/squelch", json={
                "open_threshold": 0.25, "close_threshold": 0.2,
                "tail_time": 5.0})).status

        assert with_client(server, scenario) == 200
        assert server._squelch.open_threshold == 0.25
        assert server._squelch.close_threshold == 0.2
        assert server._squelch.tail_time == 5.0


class TestStatusApi:
    """状态与统计接口。"""

    def test_status_reports_new_fields(self, env):
        async def scenario(c):
            return await (await c.get("/api/status")).json()

        data = with_client(env["server"], scenario)
        for key in ("snr_db", "noise_floor_db", "rms_noise_floor",
                    "dropped_frames", "passband", "squelch"):
            assert key in data
        assert data["passband"] == [300.0, 3000.0]

    def test_stats_includes_modulation_and_snr(self, env):
        async def scenario(c):
            return await (await c.get("/api/stats?days=30")).json()

        data = with_client(env["server"], scenario)
        assert any(m["estimated_modulation"] == "USB_VOICE"
                   for m in data["modulation_stats"])
        assert data["snr_distribution"]
        assert "daily_activity" in data

    def test_stats_rejects_bad_params(self, env):
        async def scenario(c):
            return (await c.get("/api/stats?days=abc")).status

        assert with_client(env["server"], scenario) == 400

    def test_sessions(self, env):
        async def scenario(c):
            return await (await c.get("/api/sessions")).json()

        assert len(with_client(env["server"], scenario)) >= 1
