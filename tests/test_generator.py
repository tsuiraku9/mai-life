from mailife.generator import parse_activities, parse_json_object, parse_shares, yesterday_tail_text
from mailife.store import Activity, LifeDocument


def test_parse_json_object_from_code_block() -> None:
    raw = """说明文字
```json
{"activities": [{"start": "08:00", "end": "09:00", "title": "起床"}]}
```
"""
    data = parse_json_object(raw)
    assert data is not None
    activities = parse_activities(data)
    assert activities[0].title == "起床"


def test_parse_skips_invalid_items() -> None:
    data = {
        "activities": [
            {"start": "08:00", "end": "09:00", "title": "起床"},
            {"start": "bad", "end": "09:00", "title": "坏"},
            "not-an-object",
        ],
        "shares": [
            {"time": "12:30", "title": "午饭"},
            {"time": "99:99", "title": "坏"},
        ],
    }
    assert [item.title for item in parse_activities(data)] == ["起床"]
    assert [item.title for item in parse_shares(data)] == ["午饭"]


def test_yesterday_tail_mentions_cross_day() -> None:
    document = LifeDocument(
        date="2026-08-28",
        generated_at="",
        activities=[
            Activity("a1", "21:00", "22:00", "洗澡"),
            Activity("a2", "23:00", "01:00", "睡觉"),
        ],
    )
    text = yesterday_tail_text(document)
    assert "跨天" in text
    assert "不要再写它" in text
