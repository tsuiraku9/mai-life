from datetime import datetime

from mailife.times import (
    activity_interval,
    is_cross_day,
    is_in_time_window,
    is_valid_hhmm,
    next_daily_datetime,
    time_to_minutes,
    wall_clock_now,
    weekday_cn,
)


def test_parse_and_cross_day() -> None:
    assert is_valid_hhmm("08:30")
    assert not is_valid_hhmm("25:00")
    assert not is_valid_hhmm("ab:cd")
    assert is_cross_day("23:00", "01:00")
    assert not is_cross_day("08:00", "09:00")
    assert time_to_minutes("01:00") == 60


def test_activity_interval_cross_day() -> None:
    day = datetime(2026, 8, 29, 12, 0)
    start, end = activity_interval("23:00", "01:00", day)
    assert start.day == 29
    assert end.day == 30
    assert end.hour == 1


def test_silence_window_cross_midnight() -> None:
    assert is_in_time_window("00:00", "07:30", "01:00")
    assert not is_in_time_window("00:00", "07:30", "08:00")
    assert is_in_time_window("23:00", "07:00", "23:30")
    assert is_in_time_window("23:00", "07:00", "01:00")
    assert not is_in_time_window("23:00", "07:00", "12:00")


def test_next_daily_datetime() -> None:
    now = datetime(2026, 8, 29, 1, 0)
    assert next_daily_datetime(now, "01:30") == datetime(2026, 8, 29, 1, 30)
    later = datetime(2026, 8, 29, 2, 0)
    assert next_daily_datetime(later, "01:30") == datetime(2026, 8, 30, 1, 30)


def test_wall_clock_now_passthrough() -> None:
    now = datetime(2026, 8, 29, 10, 15)
    assert wall_clock_now("local", now) == now
    assert weekday_cn(now) == "六"
