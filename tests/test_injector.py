from datetime import datetime

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
