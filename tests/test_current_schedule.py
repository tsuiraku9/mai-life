from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from mailife.injector import build_inject_text, build_recent_schedule_text, build_today_schedule_text
from mailife.store import Activity, LifeDocument


def _document(day: str, *activities: Activity) -> LifeDocument:
    return LifeDocument(date=day, generated_at="", activities=list(activities))


def _yesterday() -> LifeDocument:
    return _document(
        "2026-08-28",
        Activity("old-meal", "18:00", "19:00", "昨日已结束晚饭"),
        Activity("sleep", "23:00", "07:00", "昨日睡眠", notes="合成睡眠备注"),
    )


def _inject(document: LifeDocument | None, yesterday: LifeDocument | None, now: datetime, count: int = 3) -> str:
    return build_inject_text(
        document, now, count=count, window_minutes=60, template="", yesterday=yesterday
    )


@pytest.mark.parametrize("today_state", ["missing", "empty", "generated"])
def test_overnight_carry_is_available_with_every_today_state(today_state: str) -> None:
    now = datetime(2026, 8, 29, 0, 30)
    today = None
    if today_state == "empty":
        today = _document("2026-08-29")
    elif today_state == "generated":
        today = _document("2026-08-29", Activity("breakfast", "08:00", "09:00", "今日早餐"))
        today.generated_at = "2026-08-29T00:05:00"
    yesterday = _yesterday()
    snapshot = deepcopy((today, yesterday))
    for text in (_inject(today, yesterday, now), build_today_schedule_text(today, yesterday, now)):
        assert "昨日睡眠" in text
        assert "2026-08-28 23:00 → 2026-08-29 07:00" in text
        assert "昨日开始、今日结束" in text
        assert "状态：进行中" in text
        assert "来源：2026-08-28 日程" in text
        assert "合成睡眠备注" in text
        assert "昨日已结束晚饭" not in text
    assert (today, yesterday) == snapshot


@pytest.mark.parametrize("elapsed", [timedelta(), timedelta(microseconds=1), timedelta(hours=1)])
def test_carry_disappears_at_and_after_its_end(elapsed: timedelta) -> None:
    now = datetime(2026, 8, 29, 7, 0) + elapsed
    yesterday = _yesterday()
    assert _inject(None, yesterday, now) == ""
    assert "昨日睡眠" not in build_today_schedule_text(None, yesterday, now)
    today = _document("2026-08-29", Activity("breakfast", "07:00", "08:00", "今日早餐"))
    for text in (_inject(today, yesterday, now), build_today_schedule_text(today, yesterday, now)):
        assert "昨日睡眠" not in text
        assert "昨日已结束晚饭" not in text
        assert "今日早餐" in text


def test_carry_is_included_until_last_microsecond() -> None:
    now = datetime(2026, 8, 29, 7, 0) - timedelta(microseconds=1)
    assert "昨日睡眠" in _inject(None, _yesterday(), now, count=1)


def test_count_one_prefers_carry_even_over_today_ongoing() -> None:
    now = datetime(2026, 8, 29, 0, 30)
    today = _document("2026-08-29", Activity("reading", "00:00", "01:00", "今日阅读"))
    text = _inject(today, _yesterday(), now, count=1)
    activity_lines = [line for line in text.splitlines() if line.startswith("- ")]
    assert len(activity_lines) == 1
    assert activity_lines[0].startswith("- 23:00-07:00(次日) 昨日睡眠")
    assert "状态：进行中" in activity_lines[0]
    assert "来源冲突" in activity_lines[0]
    assert "2026-08-29 日程「今日阅读」" in activity_lines[0]


def test_future_tonight_with_same_hhmm_is_not_ongoing() -> None:
    now = datetime(2026, 8, 29, 0, 30)
    today = _document("2026-08-29", Activity("sleep", "23:00", "07:00", "今夜睡眠"))
    text = build_today_schedule_text(today, _yesterday(), now)
    yesterday_line = next(line for line in text.splitlines() if "昨日睡眠" in line)
    tonight_line = next(line for line in text.splitlines() if "今夜睡眠" in line)
    assert "2026-08-28 23:00 → 2026-08-29 07:00" in yesterday_line
    assert "状态：进行中" in yesterday_line
    assert "2026-08-29 23:00 → 2026-08-30 07:00" in tonight_line
    assert "今日开始、明日结束" in tonight_line
    assert "状态：待开始" in tonight_line
    assert "状态：进行中" not in tonight_line
    assert "来源冲突" not in text
    injected = _inject(today, _yesterday(), now, count=1)
    assert "昨日睡眠" in injected
    assert "今夜睡眠" not in injected
    assert "状态：待开始" in _inject(today, None, now, count=1)


def test_overlapping_sources_are_kept_and_explicitly_marked() -> None:
    now = datetime(2026, 8, 29, 0, 30)
    today = _document("2026-08-29", Activity("reading", "00:00", "01:00", "今日阅读"))
    yesterday = _yesterday()
    snapshot = deepcopy((today, yesterday))
    for text in (_inject(today, yesterday, now), build_today_schedule_text(today, yesterday, now)):
        lines = [line for line in text.splitlines() if line.startswith("- ")]
        assert len(lines) == 2
        assert all("来源冲突（时段重叠）" in line for line in lines)
        assert all("状态：进行中" in line for line in lines)
        assert any("来源：2026-08-28 日程" in line for line in lines)
        assert any("来源：2026-08-29 日程" in line for line in lines)
    assert (today, yesterday) == snapshot


def test_adjacent_intervals_are_not_conflicting() -> None:
    now = datetime(2026, 8, 29, 6, 30)
    today = _document("2026-08-29", Activity("breakfast", "07:00", "08:00", "今日早餐"))
    text = build_today_schedule_text(today, _yesterday(), now)
    assert "昨日睡眠" in text
    assert "今日早餐" in text
    assert "来源冲突" not in text


def test_today_selection_preserves_window_and_count_with_carry() -> None:
    now = datetime(2026, 8, 29, 0, 30)
    today = _document(
        "2026-08-29",
        Activity("soon", "00:50", "01:10", "今日邻近计划"),
        Activity("far", "10:00", "11:00", "今日远处计划"),
    )
    text = _inject(today, _yesterday(), now, count=3)
    assert len([line for line in text.splitlines() if line.startswith("- ")]) == 2
    assert "昨日睡眠" in text
    assert "今日邻近计划" in text
    assert "今日远处计划" not in text
    one = _inject(today, _yesterday(), now, count=1)
    assert len([line for line in one.splitlines() if line.startswith("- ")]) == 1
    assert "- 00:50-01:10 今日邻近计划" not in one


def test_today_fallback_is_independent_of_carry_window() -> None:
    now = datetime(2026, 8, 29, 0, 30)
    today = _document(
        "2026-08-29",
        Activity("nearer", "08:00", "09:00", "今日较近计划"),
        Activity("farther", "18:00", "19:00", "今日较远计划"),
    )
    text = _inject(today, _yesterday(), now, count=2)
    lines = [line for line in text.splitlines() if line.startswith("- ")]
    assert len(lines) == 2
    assert "昨日睡眠" in lines[0]
    assert "今日较近计划" in lines[1]
    assert "状态：待开始" in lines[1]
    assert "今日较远计划" not in text


def test_today_only_keeps_existing_nearest_fallback_and_end_status() -> None:
    now = datetime(2026, 8, 29, 22, 0)
    today = _document(
        "2026-08-29",
        Activity("breakfast", "08:00", "09:00", "今日早餐"),
        Activity("study", "10:00", "11:00", "今日学习"),
    )
    text = _inject(today, _yesterday(), now, count=1)
    assert "今日学习" in text
    assert "状态：已结束" in text
    assert "今日早餐" not in text
    assert "昨日睡眠" not in text
    at_end = _inject(today, None, datetime(2026, 8, 29, 11, 0), count=1)
    assert "状态：已结束" in at_end
    assert "状态：进行中" not in at_end


def test_query_uses_full_same_view_without_injection_limits() -> None:
    now = datetime(2026, 8, 29, 6, 30)
    today = _document(
        "2026-08-29",
        Activity("breakfast", "08:00", "09:00", "今日早餐"),
        Activity("lunch", "12:00", "13:00", "今日午饭"),
        Activity("dinner", "18:00", "19:00", "今日晚饭"),
        Activity("sleep", "23:00", "07:00", "今夜睡眠"),
    )
    yesterday = _yesterday()
    query = build_today_schedule_text(today, yesterday, now)
    assert len([line for line in query.splitlines() if line.startswith("- ")]) == 5
    assert "昨日已结束晚饭" not in query
    for activity in today.activities:
        assert activity.title in query
    recent = build_recent_schedule_text(today, now, count=1, window_minutes=60, yesterday=yesterday)
    assert recent in query
    assert _inject(today, yesterday, now, count=0) == ""
    assert "昨日睡眠" in query


def test_query_shows_all_today_statuses_in_chronological_order() -> None:
    now = datetime(2026, 8, 29, 12, 0)
    today = _document(
        "2026-08-29",
        Activity("future", "18:00", "19:00", "合成晚饭"),
        Activity("ongoing", "12:00", "13:00", "合成午饭"),
        Activity("ended", "08:00", "09:00", "合成早餐"),
    )
    lines = build_today_schedule_text(today, None, now).splitlines()
    assert "合成早餐" in lines[0] and "状态：已结束" in lines[0]
    assert "合成午饭" in lines[1] and "状态：进行中" in lines[1]
    assert "合成晚饭" in lines[2] and "状态：待开始" in lines[2]


def test_yesterday_is_never_a_whole_document_fallback() -> None:
    now = datetime(2026, 8, 29, 0, 30)
    yesterday = _document(
        "2026-08-28",
        Activity("ended-cross", "23:00", "00:15", "昨日已结束跨日活动"),
        Activity("ended", "18:00", "19:00", "昨日晚饭"),
        Activity("non-cross", "06:00", "07:00", "昨日晨间活动"),
    )
    assert _inject(None, yesterday, now) == ""
    text = build_today_schedule_text(None, yesterday, now)
    assert text == "暂无今日日程。"
    assert "昨日" not in text


@pytest.mark.parametrize("slot", ["today", "yesterday"])
@pytest.mark.parametrize("bad_date", ["invalid-date", "2026-02-30", "", "2026-08-27"])
def test_invalid_or_mismatched_source_dates_are_not_fabricated(slot: str, bad_date: str) -> None:
    now = datetime(2026, 8, 29, 0, 30)
    bad = _document(bad_date, Activity("bad", "23:00", "07:00", "不应出现的活动"))
    today = bad if slot == "today" else _document("2026-08-29", Activity("good", "00:00", "01:00", "有效今日活动"))
    yesterday = _yesterday() if slot == "today" else bad
    for text in (_inject(today, yesterday, now), build_today_schedule_text(today, yesterday, now)):
        assert "不应出现的活动" not in text
        assert "已跳过" in text
        assert "源日期" in text
        assert ("昨日睡眠" if slot == "today" else "有效今日活动") in text
    assert _inject(bad, None, now) == ""
    invalid_only = build_today_schedule_text(bad, None, now)
    assert "已跳过今日文档" in invalid_only
    assert "不应出现的活动" not in invalid_only


def test_invalid_activity_times_are_skipped_without_losing_valid_carry() -> None:
    now = datetime(2026, 8, 29, 0, 30)
    today = _document("2026-08-29", Activity("bad", "not-time", "07:00", "非法今日活动"))
    yesterday = _yesterday()
    yesterday.activities.append(Activity("bad-yesterday", "23:00", "25:00", "非法昨日活动"))
    for text in (_inject(today, yesterday, now), build_today_schedule_text(today, yesterday, now)):
        assert "昨日睡眠" in text
        assert "非法今日活动" not in text
        assert "非法昨日活动" not in text


def test_carry_uses_real_source_dates_across_month_boundary() -> None:
    now = datetime(2026, 9, 1, 0, 0)
    yesterday = _document("2026-08-31", Activity("sleep", "23:00", "07:00", "月末睡眠"))
    today = _document("2026-09-01", Activity("sleep", "23:00", "07:00", "月初今夜睡眠"))
    text = build_today_schedule_text(today, yesterday, now)
    assert "2026-08-31 23:00 → 2026-09-01 07:00" in text
    assert "2026-09-01 23:00 → 2026-09-02 07:00" in text
    assert "来源冲突" not in text


def test_empty_query_without_any_document_is_explicit() -> None:
    assert build_today_schedule_text(None, None, datetime(2026, 8, 29, 0, 30)) == "暂无今日日程。"
