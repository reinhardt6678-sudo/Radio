"""
test_schedule.py - active_hours 解析测试

frequencies.yaml 里的 active_hours 在 v1.3.1 之前从没被任何代码读过。
这里把解析规则钉死，尤其是跨零点的时段和"全天"的各种写法。
"""

from datetime import datetime, timezone

import pytest

from src.schedule import (
    active_frequencies,
    check_targets,
    format_window,
    is_active,
    parse_active_hours,
)


def utc(hour, minute=0):
    return datetime(2026, 8, 14, hour, minute, tzinfo=timezone.utc)


class TestParse:
    def test_plain_range(self):
        assert parse_active_hours("10:00-22:00 UTC") == (600, 1320)

    def test_range_without_utc_suffix(self):
        assert parse_active_hours("00:00-10:00") == (0, 600)

    def test_range_with_spaces(self):
        assert parse_active_hours("12:00 - 20:00 UTC") == (720, 1200)

    @pytest.mark.parametrize("text", ["全天", "24小时", "24h", "always"])
    def test_always_on(self, text):
        assert parse_active_hours(text) is None

    @pytest.mark.parametrize("text", ["", None, "看情况", "白天"])
    def test_unparseable_is_always_on(self, text):
        """认不出来的写法宁可不提示，也不要误报。"""
        assert parse_active_hours(text) is None

    def test_full_day_range_is_always_on(self):
        assert parse_active_hours("00:00-00:00 UTC") is None

    def test_invalid_minutes_rejected(self):
        assert parse_active_hours("10:75-22:00") is None


class TestIsActive:
    def test_inside_window(self):
        assert is_active("10:00-22:00 UTC", utc(15)) is True

    def test_before_window(self):
        assert is_active("10:00-22:00 UTC", utc(9, 59)) is False

    def test_start_is_inclusive(self):
        assert is_active("10:00-22:00 UTC", utc(10, 0)) is True

    def test_end_is_exclusive(self):
        assert is_active("10:00-22:00 UTC", utc(22, 0)) is False

    def test_wraps_past_midnight(self):
        """22:00-06:00 这种跨零点的时段不能被判成空区间。"""
        assert is_active("22:00-06:00 UTC", utc(23)) is True
        assert is_active("22:00-06:00 UTC", utc(3)) is True
        assert is_active("22:00-06:00 UTC", utc(12)) is False

    def test_always_on_is_always_active(self):
        assert is_active("全天", utc(4)) is True

    def test_naive_datetime_treated_as_utc(self):
        naive = datetime(2026, 8, 14, 15, 0)
        assert is_active("10:00-22:00 UTC", naive) is True


class TestFormatWindow:
    def test_range(self):
        assert format_window("10:00-22:00 UTC") == "10:00-22:00 UTC"

    def test_always(self):
        assert format_window("全天") == "全天"


# --- 一份精简的频率库，结构和 frequencies.yaml 一致 ---
FREQ_DATA = {
    "hfgcs": {
        "description": "HFGCS",
        "frequencies": [
            {"freq": 4724.0, "mode": "USB", "active_hours": "00:00-10:00 UTC",
             "priority": "high", "description": "夜间主频"},
            {"freq": 8992.0, "mode": "USB", "active_hours": "全天",
             "priority": "high", "description": "全天主频"},
            {"freq": 11175.0, "mode": "USB", "active_hours": "10:00-22:00 UTC",
             "priority": "high", "description": "日间主频"},
        ],
    },
    "reference": {
        "description": "参考",
        "frequencies": [
            {"freq": 5000.0, "mode": "AM", "active_hours": "24小时",
             "priority": "low", "description": "WWV"},
        ],
    },
}


class TestActiveFrequencies:
    def test_daytime_selection(self):
        freqs = {f["freq"] for f in active_frequencies(FREQ_DATA, utc(15))}
        assert freqs == {8992.0, 11175.0, 5000.0}

    def test_nighttime_selection(self):
        freqs = {f["freq"] for f in active_frequencies(FREQ_DATA, utc(3))}
        assert freqs == {4724.0, 8992.0, 5000.0}

    def test_network_key_attached(self):
        for f in active_frequencies(FREQ_DATA, utc(15)):
            assert "network" in f

    def test_empty_data(self):
        assert active_frequencies({}, utc(15)) == []


class TestCheckTargets:
    def test_silent_when_in_window(self):
        """时段对得上时不应该多说话。"""
        assert check_targets(FREQ_DATA, [11175.0], utc(15)) == []

    def test_warns_when_out_of_window(self):
        lines = check_targets(FREQ_DATA, [11175.0], utc(3))
        assert lines, "03:00 UTC 守 11175 应该给出提示"
        text = "\n".join(lines)
        assert "11175.0 kHz" in text
        assert "10:00-22:00 UTC" in text

    def test_suggests_in_window_alternatives(self):
        text = "\n".join(check_targets(FREQ_DATA, [11175.0], utc(3)))
        assert "4724.0 kHz" in text, "应该提示当前时段在线的高优先级频率"

    def test_does_not_suggest_the_target_itself(self):
        lines = check_targets(FREQ_DATA, [11175.0, 4724.0], utc(3))
        suggestion = [l for l in lines if "高优先级频率" in l]
        for line in suggestion:
            assert "4724.0 kHz" not in line

    def test_unknown_frequency_is_ignored(self):
        """频率库里没有的自定义频率没有时段信息，不该报警。"""
        assert check_targets(FREQ_DATA, [12345.0], utc(3)) == []
