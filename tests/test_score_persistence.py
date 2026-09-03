"""
test_score_persistence.py - 调制判定的依据必须留下来 / the evidence behind a verdict must survive

背景: 五类加权得分过去算完就扔 —— 不打日志、不入库。于是特征几乎相同的两条记录
得到不同标签时无从解释, 对分类器的任何改动也没有 before/after 可比。
更要紧的是, 数据库里 NOISE 和 USB_VOICE 长得一样, 而 NOISE 是在打分之前
由一道 SNR 闸门返回的 —— 这个区别一度让本仓库的缺陷诊断错了两天。

Background: the five per-class scores used to be computed and dropped -- never logged,
never stored. Two records with near-identical features could get different labels with no
way to explain it, and no change to the classifier could be verified. Worse, NOISE and
USB_VOICE look the same in the database even though NOISE is returned by an SNR gate
*before* scoring -- a distinction that sent this repo's own diagnosis wrong for two days.
"""

import json

import numpy as np
import pytest
import yaml

from src.analyzer import SignalAnalyzer
from src.db import Database


@pytest.fixture
def env(tmp_path):
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    db = Database(str(tmp_path / "scores.db"))
    an = SignalAnalyzer.from_config(cfg)
    sid = db.create_session("h", "n", [8992.0])
    yield an, db, sid
    db.close()


def _row(an, db, sid, samples):
    gid = db.record_signal(session_id=sid, frequency_khz=8992.0, mode="USB",
                           node_host="h", node_name="n", duration_seconds=2.0,
                           peak_rms=0.3, avg_rms=0.1)
    res = an.analyze_buffer([samples.astype(np.float32)], an.sample_rate, mode="USB")
    an.save_analysis_result(db, gid, res)
    return db.get_analysis_by_signal(gid), res


def _scored_signal(sr, seed=3):
    """带音节起伏的信号, SNR 足够高, 能越过闸门进入打分。"""
    rng = np.random.default_rng(seed)
    t = np.arange(2 * sr) / sr
    return (0.3 * np.sin(2 * np.pi * 1200 * t) * (1 + 0.8 * np.sin(2 * np.pi * 4 * t))
            + 0.01 * rng.normal(0, 1, len(t)))


def _pure_noise(sr, seed=1):
    return 0.01 * np.random.default_rng(seed).normal(0, 1, 2 * sr)


class TestScoresPersisted:

    def test_scores_round_trip_as_json(self, env):
        an, db, sid = env
        row, res = _row(an, db, sid, _scored_signal(an.sample_rate))
        assert row["modulation_scores"] is not None
        assert json.loads(row["modulation_scores"]) == res["modulation_scores"]

    def test_scored_verdict_keeps_all_five_classes(self, env):
        an, db, sid = env
        row, _ = _row(an, db, sid, _scored_signal(an.sample_rate))
        scores = json.loads(row["modulation_scores"])
        assert set(scores) == {"VOICE", "CW", "CARRIER", "FSK", "PSK"}

    def test_gated_noise_is_distinguishable_from_a_scored_verdict(self, env):
        """
        这是整条改动的要点: 光看标签分不出"被闸门拦下"和"打过分", 看得分就分得出。
        The point of the whole change: the label cannot tell "gated" from "scored";
        the stored scores can.
        """
        an, db, sid = env
        gated, _ = _row(an, db, sid, _pure_noise(an.sample_rate))
        scored, _ = _row(an, db, sid, _scored_signal(an.sample_rate))

        assert gated["estimated_modulation"] == "NOISE"
        g = json.loads(gated["modulation_scores"])
        s = json.loads(scored["modulation_scores"])
        assert list(g) == ["NOISE"], "闸门路径应当只留下一个 NOISE 键"
        assert len(s) == 5, "打分路径应当留下五类"

    def test_tone_features_persisted(self, env):
        """CW / CARRIER / FSK 的唯一依据, 不存就无法离线重建这三类。"""
        an, db, sid = env
        row, res = _row(an, db, sid, _scored_signal(an.sample_rate))
        for col in ("tone_spacing_hz", "tone_purity", "keying_rate_hz"):
            assert row[col] is not None, f"{col} 没有落库"
            assert row[col] == pytest.approx(res[col], rel=1e-6)


class TestBackwardsCompatible:

    def test_save_analysis_still_works_without_the_new_arguments(self, tmp_path):
        """老调用方不传新参数也要能写入 —— 新列留空, 不报错。"""
        db = Database(str(tmp_path / "old.db"))
        sid = db.create_session("h", "n", [8992.0])
        gid = db.record_signal(session_id=sid, frequency_khz=8992.0, mode="USB",
                               node_host="h", node_name="n", duration_seconds=1.0,
                               peak_rms=0.1, avg_rms=0.05)
        db.save_analysis(signal_id=gid, snr_db=5.0, estimated_modulation="USB_VOICE")
        row = db.get_analysis_by_signal(gid)
        assert row["estimated_modulation"] == "USB_VOICE"
        assert row["modulation_scores"] is None
        assert row["tone_spacing_hz"] is None
        db.close()

    def test_empty_scores_stored_as_json_not_null(self, tmp_path):
        """
        空字典是"压根没分析", 和"没传这个参数"是两回事。
        判真假值会把前者悄悄存成 NULL, 所以必须判 is not None。
        """
        db = Database(str(tmp_path / "empty.db"))
        sid = db.create_session("h", "n", [8992.0])
        gid = db.record_signal(session_id=sid, frequency_khz=8992.0, mode="USB",
                               node_host="h", node_name="n", duration_seconds=1.0,
                               peak_rms=0.1, avg_rms=0.05)
        db.save_analysis(signal_id=gid, modulation_scores={})
        row = db.get_analysis_by_signal(gid)
        assert row["modulation_scores"] == "{}"
        assert json.loads(row["modulation_scores"]) == {}
        db.close()

    def test_old_database_gains_the_columns(self, tmp_path):
        """老库重新打开时自动补列, 不丢历史数据。"""
        import sqlite3
        path = str(tmp_path / "legacy.db")
        db = Database(path)
        sid = db.create_session("h", "n", [8992.0])
        gid = db.record_signal(session_id=sid, frequency_khz=8992.0, mode="USB",
                               node_host="h", node_name="n", duration_seconds=1.0,
                               peak_rms=0.1, avg_rms=0.05)
        db.save_analysis(signal_id=gid, snr_db=1.0)
        db.close()

        # 模拟老库: 把新列删掉 (SQLite 3.35+ 支持 DROP COLUMN)
        raw = sqlite3.connect(path)
        for col in ("modulation_scores", "tone_spacing_hz", "tone_purity",
                    "keying_rate_hz"):
            try:
                raw.execute(f"ALTER TABLE analysis DROP COLUMN {col}")
            except sqlite3.OperationalError:
                pytest.skip("这个 SQLite 版本不支持 DROP COLUMN")
        raw.commit()
        raw.close()

        db2 = Database(path)
        cols = {d[1] for d in db2.conn.execute("PRAGMA table_info(analysis)")}
        assert {"modulation_scores", "tone_spacing_hz", "tone_purity",
                "keying_rate_hz"} <= cols
        assert db2.get_analysis_by_signal(gid)["snr_db"] == 1.0   # 历史数据还在
        db2.close()
