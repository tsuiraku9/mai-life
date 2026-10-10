from copy import deepcopy
from datetime import datetime

import pytest

from mailife.injector import build_inject_text, inject_into_items, inject_into_messages, select_recent_activities
from mailife.store import Activity, LifeDocument


def _doc(*activities: Activity) -> LifeDocument:
    return LifeDocument(date="2026-08-29", generated_at="", activities=list(activities))


def test_select_prefers_ongoing() -> None:
    now = datetime(2026, 8, 29, 12, 10)
    activities = [
        Activity("a1", "08:00", "09:00", "早餐"),
        Activity("a2", "12:00", "13:00", "午饭"),
        Activity("a3", "18:00", "19:00", "晚饭"),
    ]
    selected = select_recent_activities(activities, now, count=1, window_minutes=180)
    assert selected[0][0].title == "午饭"


def test_select_fallback_outside_window() -> None:
    now = datetime(2026, 8, 29, 22, 0)
    activities = [
        Activity("a1", "08:00", "09:00", "早餐"),
        Activity("a2", "10:00", "11:00", "学习"),
    ]
    selected = select_recent_activities(activities, now, count=1, window_minutes=30)
    assert selected[0][0].title == "学习"


def test_inject_after_last_system() -> None:
    messages = [
        {"role": "system", "content": "人设"},
        {"role": "user", "content": "你好"},
    ]
    result = inject_into_messages(messages, "日程")
    assert result[1]["role"] == "system"
    assert result[1]["content"] == "日程"
    assert result[2]["role"] == "user"
    assert messages[1]["role"] == "user"


def test_inject_prepend_without_system() -> None:
    messages = [{"role": "user", "content": "你好"}]
    result = inject_into_messages(messages, "日程")
    assert result[0] == {"role": "system", "content": "日程"}


def test_inject_into_context_items() -> None:
    items = [
        {
            "item_type": "SystemMessageItem",
            "meta": {"item_id": "sys1", "logical_turn_id": None, "timestamp": "2026-08-31T08:00:00"},
            "parts": [{"type": "text", "text": "人设"}],
        },
        {
            "item_type": "UserMessageItem",
            "meta": {"item_id": "u1", "logical_turn_id": None, "timestamp": "2026-08-31T08:00:01"},
            "parts": [{"type": "text", "text": "你好"}],
        },
    ]
    result = inject_into_items(items, "当前生活日程")
    assert result[1]["item_type"] == "SystemMessageItem"
    assert result[1]["parts"][0]["text"] == "当前生活日程"
    assert result[1]["meta"]["logical_turn_id"] is None
    assert result[2]["item_type"] == "UserMessageItem"
    assert items[1]["item_type"] == "UserMessageItem"


def test_build_inject_text_contains_recent() -> None:
    now = datetime(2026, 8, 29, 12, 10)
    document = _doc(Activity("a2", "12:00", "13:00", "午饭", notes="食堂"))
    text = build_inject_text(document, now, count=3, window_minutes=180, template="")
    assert "午饭" in text
    assert "12:00-13:00" in text
    assert "食堂" in text
    assert "当前生活日程" in text
    assert "心情" not in text
    assert "状态：进行中" in text
    assert "来源：2026-08-29 日程" in text


def test_select_uses_half_open_intervals() -> None:
    now = datetime(2026, 8, 29, 13, 0)
    activities = [
        Activity("ended", "12:00", "13:00", "已结束午饭"),
        Activity("ongoing", "13:00", "14:00", "当前散步"),
    ]
    selected = select_recent_activities(activities, now, count=1, window_minutes=0)
    assert selected[0][0].id == "ongoing"


@pytest.mark.parametrize("payload", ["items", "messages"])
def test_cross_day_text_preserves_payload_format(payload: str) -> None:
    now = datetime(2026, 8, 29, 0, 30)
    yesterday = LifeDocument(
        date="2026-08-28",
        generated_at="",
        activities=[Activity("sleep", "23:00", "07:00", "昨日睡眠", notes="合成备注")],
    )
    text = build_inject_text(
        None, now, count=1, window_minutes=0, template="", yesterday=yesterday
    )
    assert "昨日睡眠" in text
    assert "昨日开始、今日结束" in text
    if payload == "messages":
        original = [
            {"role": "system", "content": "合成人设"},
            {"role": "user", "content": "合成提问"},
            {"role": "system", "content": "补充规则"},
            {"role": "assistant", "content": "合成回复"},
        ]
        snapshot = deepcopy(original)
        result = inject_into_messages(original, text)
        assert result[3] == {"role": "system", "content": text}
    else:
        original = [
            {"item_type": "SystemMessageItem", "parts": [{"type": "text", "text": "合成人设"}]},
            {"item_type": "UserMessageItem", "parts": [{"type": "text", "text": "合成提问"}]},
            {"item_type": "SystemMessageItem", "parts": [{"type": "text", "text": "补充规则"}]},
            {"item_type": "AssistantMessageItem", "parts": [{"type": "text", "text": "合成回复"}]},
        ]
        snapshot = deepcopy(original)
        result = inject_into_items(original, text, now=now)
        assert set(result[3]) == {"item_type", "meta", "parts"}
        assert result[3]["item_type"] == "SystemMessageItem"
        assert result[3]["parts"] == [{"type": "text", "text": text}]
        assert set(result[3]["meta"]) == {"item_id", "logical_turn_id", "timestamp"}
        assert result[3]["meta"]["item_id"]
        assert result[3]["meta"]["logical_turn_id"] is None
        assert result[3]["meta"]["timestamp"] == now.isoformat()
    assert result[:3] == original[:3]
    assert result[4:] == original[3:]
    assert original == snapshot


@pytest.mark.parametrize("payload", ["items", "messages"])
def test_empty_schedule_does_not_add_payload(payload: str) -> None:
    text = build_inject_text(
        None, datetime(2026, 8, 29, 8, 0), count=1, window_minutes=180, template=""
    )
    original = [{"item_type": "UserMessageItem"}] if payload == "items" else [{"role": "user"}]
    result = inject_into_items(original, text) if payload == "items" else inject_into_messages(original, text)
    assert text == ""
    assert result == original
    assert result is not original


def test_custom_template_and_extra_values_remain_compatible() -> None:
    now = datetime(2026, 8, 29, 12, 10)
    document = _doc(Activity("meal", "12:00", "13:00", "午饭"))
    text = build_inject_text(
        document,
        now,
        count=1,
        window_minutes=30,
        template="{bot_nickname} {date} 星期{weekday} {now}\n{recent_schedule}\n{unknown}",
        extra_values={"bot_nickname": "合成角色"},
    )
    assert text.startswith("合成角色 2026-08-29 星期六 2026-08-29 12:10\n")
    assert "午饭" in text
    assert text.endswith("{unknown}")
