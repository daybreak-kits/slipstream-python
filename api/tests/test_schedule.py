from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from l03_domain.schedule import is_offline, next_available_ms, parse_schedule, rules_to_dict, schedule_to_dict

def ms(year: int, month: int, day: int, hour: int, minute: int = 0, tz_name: str = "Asia/Shanghai") -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz_name)).timestamp() * 1000)


def at(value: int, tz_name: str = "Asia/Shanghai") -> datetime:
    return datetime.fromtimestamp(value / 1000, ZoneInfo(tz_name))


def test_parse_reads_segments():
    schedule = parse_schedule(
        {
            "timezone": "Asia/Shanghai",
            "segments": [{"days": [1], "start": "23:00", "end": "08:00"}],
            "unused_legacy_field": [{"days": [2], "start": "12:00", "end": "14:00"}],
        }
    )

    assert schedule.timezone == "Asia/Shanghai"
    assert len(schedule.segments) == 1
    assert schedule.segments[0].days == (1,)
    assert schedule.segments[0].start.hour == 23


def test_cross_midnight_segment_belongs_to_start_day():
    schedule = parse_schedule({"segments": [{"days": [1], "start": "23:00", "end": "08:00"}]})

    assert is_offline(ms(2026, 11, 9, 22, 59), schedule) is False
    assert is_offline(ms(2026, 11, 9, 23, 0), schedule) is True
    assert is_offline(ms(2026, 11, 10, 2, 0), schedule) is True
    assert is_offline(ms(2026, 11, 10, 8, 0), schedule) is False


def test_next_available_after_cross_midnight_segment():
    schedule = parse_schedule({"segments": [{"days": [1], "start": "23:00", "end": "08:00"}]})

    result = next_available_ms(ms(2026, 11, 10, 2, 0), schedule)

    assert result == ms(2026, 11, 10, 8, 0)


def test_multiple_segments_are_a_union():
    schedule = parse_schedule(
        {
            "segments": [
                {"days": [2], "start": "12:00", "end": "14:00"},
                {"days": [2], "start": "20:00", "end": "22:00"},
            ]
        }
    )

    assert is_offline(ms(2026, 11, 10, 12, 30), schedule) is True
    assert is_offline(ms(2026, 11, 10, 15, 0), schedule) is False
    assert is_offline(ms(2026, 11, 10, 21, 0), schedule) is True
    assert next_available_ms(ms(2026, 11, 10, 12, 30), schedule) == ms(2026, 11, 10, 14, 0)


def test_whole_week_segment_never_has_a_next_available_moment():
    schedule = parse_schedule(
        {"segments": [{"days": [1, 2, 3, 4, 5, 6, 7], "start": "00:00", "end": "00:00"}]}
    )

    assert is_offline(ms(2026, 11, 10, 2, 0), schedule) is True
    assert next_available_ms(ms(2026, 11, 10, 2, 0), schedule) is None


def test_timezone_changes_the_evaluated_local_time():
    instant = ms(2026, 11, 9, 0, 30, tz_name="UTC")
    utc_schedule = parse_schedule(
        {"timezone": "UTC", "segments": [{"days": [1], "start": "00:00", "end": "01:00"}]}
    )
    shanghai_schedule = parse_schedule(
        {"timezone": "Asia/Shanghai", "segments": [{"days": [1], "start": "00:00", "end": "01:00"}]}
    )

    assert at(instant, "Asia/Shanghai").hour == 8
    assert is_offline(instant, utc_schedule) is True
    assert is_offline(instant, shanghai_schedule) is False


def test_empty_schedule_is_always_available():
    schedule = parse_schedule({})

    assert schedule.segments == ()
    assert is_offline(ms(2026, 11, 10, 2, 0), schedule) is False
    assert next_available_ms(ms(2026, 11, 10, 2, 0), schedule) == ms(2026, 11, 10, 2, 0)


def test_schedule_to_dict_round_trips_segments():
    raw = {
        "timezone": "Asia/Shanghai",
        "segments": [{"days": [1, 3], "start": "23:00", "end": "08:00"}],
    }

    assert schedule_to_dict(parse_schedule(raw)) == raw


def test_window_start_is_inclusive_and_end_is_exclusive():
    schedule = parse_schedule(
        {"segments": [{"days": [2], "start": "12:00", "end": "14:00"}]}
    )

    assert is_offline(ms(2026, 11, 10, 11, 59), schedule) is False
    assert is_offline(ms(2026, 11, 10, 12, 0), schedule) is True
    assert is_offline(ms(2026, 11, 10, 13, 59), schedule) is True
    assert is_offline(ms(2026, 11, 10, 14, 0), schedule) is False


def test_overlapping_segments_use_the_furthest_end_for_next_available():
    schedule = parse_schedule(
        {
            "segments": [
                {"days": [1], "start": "23:00", "end": "01:00"},
                {"days": [2], "start": "00:30", "end": "03:00"},
            ]
        }
    )

    assert is_offline(ms(2026, 11, 10, 0, 45), schedule) is True
    assert next_available_ms(ms(2026, 11, 10, 0, 45), schedule) == ms(2026, 11, 10, 3, 0)


def test_window_follows_the_start_day_across_a_week_boundary():
    # Sunday 23:00-02:00 belongs to Sunday, so it covers Monday 01:00.
    schedule = parse_schedule(
        {"segments": [{"days": [7], "start": "23:00", "end": "02:00"}]}
    )

    assert is_offline(ms(2026, 11, 15, 23, 30), schedule) is True
    assert is_offline(ms(2026, 11, 16, 1, 0), schedule) is True
    assert next_available_ms(ms(2026, 11, 16, 1, 0), schedule) == ms(2026, 11, 16, 2, 0)
    assert is_offline(ms(2026, 11, 16, 2, 0), schedule) is False


def test_unknown_timezone_falls_back_to_default_instead_of_crashing():
    schedule = parse_schedule(
        {
            "timezone": "Mars/Olympus_Mons",
            "segments": [{"days": [1], "start": "00:00", "end": "01:00"}],
        }
    )

    assert schedule.timezone == "Asia/Shanghai"
    assert is_offline(ms(2026, 11, 9, 0, 30), schedule) is True


# --- ADR-070 §5: v2 rule kinds ----------------------------------------------


def test_v2_daily_rule_covers_every_day():
    schedule = parse_schedule(
        {"rules": [{"kind": "daily", "start": "22:00", "end": "02:00"}]}
    )

    assert is_offline(ms(2026, 11, 9, 21, 59), schedule) is False
    assert is_offline(ms(2026, 11, 9, 22, 0), schedule) is True
    assert is_offline(ms(2026, 11, 10, 1, 30), schedule) is True
    assert is_offline(ms(2026, 11, 10, 2, 0), schedule) is False
    assert is_offline(ms(2026, 11, 11, 23, 30), schedule) is True


def test_v2_monthly_rule_skips_months_without_the_day():
    schedule = parse_schedule(
        {
            "rules": [
                {"kind": "monthly", "days_of_month": [15, 31], "start": "00:00", "end": "01:00"}
            ]
        }
    )

    assert is_offline(ms(2026, 4, 15, 0, 30), schedule) is True
    assert is_offline(ms(2026, 4, 15, 1, 0), schedule) is False
    assert is_offline(ms(2026, 6, 15, 0, 30), schedule) is True
    # 4 月没有 31 号：规则当月直接落空，而不是前移到 30 号。
    assert is_offline(ms(2026, 4, 30, 0, 30), schedule) is False


def test_v2_leap_day_rule_hits_only_in_leap_years():
    schedule = parse_schedule(
        {
            "rules": [
                {"kind": "monthly", "days_of_month": [29], "start": "10:00", "end": "11:00"}
            ]
        }
    )

    assert is_offline(ms(2028, 2, 29, 10, 30), schedule) is True
    assert is_offline(ms(2027, 2, 28, 10, 30), schedule) is False


def test_v2_spring_forward_start_skips_that_day():
    # America/New_York 2026-03-08 春令时：本地 02:00 不存在，当天窗口整体跳过。
    schedule = parse_schedule(
        {
            "timezone": "America/New_York",
            "rules": [{"kind": "daily", "start": "02:00", "end": "04:00"}],
        }
    )
    local_0330_edt = int(datetime(2026, 3, 8, 7, 30, tzinfo=ZoneInfo("UTC")).timestamp() * 1000)
    next_day_start = int(datetime(2026, 3, 9, 2, 30, tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000)

    assert is_offline(local_0330_edt, schedule) is False
    assert is_offline(next_day_start, schedule) is True


def test_v2_same_instant_differs_across_timezones():
    shanghai = parse_schedule(
        {"timezone": "Asia/Shanghai", "rules": [{"kind": "daily", "start": "09:00", "end": "10:00"}]}
    )
    utc = parse_schedule(
        {"timezone": "UTC", "rules": [{"kind": "daily", "start": "09:00", "end": "10:00"}]}
    )
    moment = ms(2026, 11, 10, 9, 30)  # 上海 09:30，同一时刻 UTC 是 01:30

    assert is_offline(moment, shanghai) is True
    assert is_offline(moment, utc) is False


def test_v1_segments_and_v2_rules_are_equivalent():
    legacy = parse_schedule({"segments": [{"days": [2, 4], "start": "09:00", "end": "11:00"}]})
    modern = parse_schedule(
        {"rules": [{"kind": "weekly", "days": [2, 4], "start": "09:00", "end": "11:00"}]}
    )

    for moment in (ms(2026, 11, 10, 10, 0), ms(2026, 11, 10, 12, 0), ms(2026, 11, 11, 10, 0)):
        assert is_offline(moment, legacy) is is_offline(moment, modern)


def test_rules_to_dict_round_trips_the_v2_document():
    schedule = parse_schedule(
        {
            "timezone": "Asia/Shanghai",
            "rules": [
                {"kind": "daily", "start": "22:00", "end": "02:00"},
                {"kind": "monthly", "days_of_month": [15, 31], "start": "00:00", "end": "01:00"},
            ],
        }
    )

    assert rules_to_dict(schedule) == {
        "version": 2,
        "timezone": "Asia/Shanghai",
        "rules": [
            {"kind": "daily", "days": [1, 2, 3, 4, 5, 6, 7], "start": "22:00", "end": "02:00"},
            {"kind": "monthly", "days_of_month": [15, 31], "start": "00:00", "end": "01:00"},
        ],
    }
    assert parse_schedule(rules_to_dict(schedule)).rules == schedule.rules
