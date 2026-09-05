from datetime import datetime

from mailife.generator import (
    attach_recent_days_block,
    drop_shares_in_silence,
    format_shares_by_stream,
    load_recent_schedule_documents,
    parse_activities,
    parse_json_object,
    parse_shares,
    recent_days_schedule_text,
    yesterday_tail_text,
)
from mailife.store import Activity, LifeDocument, ShareItem


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


def test_drop_shares_in_silence_window() -> None:
    shares = [
        ShareItem(id="a", time="01:00", title="夜话"),
        ShareItem(id="b", time="08:00", title="早餐"),
        ShareItem(id="c", time="23:30", title="睡前"),
    ]
    kept = drop_shares_in_silence(shares, "00:00", "07:30")
    assert [item.title for item in kept] == ["早餐", "睡前"]
    assert drop_shares_in_silence(shares, "00:00", "00:00") == shares
    overnight = drop_shares_in_silence(shares, "23:00", "07:00")
    assert [item.title for item in overnight] == ["早餐"]


def test_format_shares_by_stream_groups_all_chats() -> None:
    shares = [
        ShareItem(id="a", time="12:00", title="午饭", stream_id="stream-a"),
        ShareItem(id="b", time="18:00", title="晚饭", stream_id="stream-b", hint="提一句"),
        ShareItem(id="c", time="21:00", title="夜话", stream_id=""),
    ]
    streams = [
        {"session_id": "stream-a", "platform": "qq", "chat_type": "private", "user_id": "111"},
        {"session_id": "stream-b", "platform": "webui", "user_id": "webui_user_xxx"},
    ]
    text = format_shares_by_stream(shares, streams)
    assert "午饭" in text
    assert "晚饭" in text
    assert "夜话" in text
    assert "聊天流 stream-a | qq | 用户 111" in text
    assert "聊天流 stream-b | webui | 用户 webui_user_xxx" in text
    assert "未绑定聊天流" in text
    assert text.index("stream-a") < text.index("stream-b")
    assert text.index("stream-b") < text.index("未绑定聊天流")
    assert format_shares_by_stream([]) == "（无）"


def test_recent_days_schedule_text_oldest_first() -> None:
    older = LifeDocument(
        date="2026-09-01",
        generated_at="",
        activities=[Activity("a1", "08:00", "09:00", "早餐")],
    )
    newer = LifeDocument(
        date="2026-09-02",
        generated_at="",
        activities=[Activity("a2", "12:00", "13:00", "午饭")],
    )
    text = recent_days_schedule_text([older, newer])
    assert text.index("2026-09-01") < text.index("2026-09-02")
    assert "早餐" in text
    assert "午饭" in text
    assert recent_days_schedule_text([]) == "（无近几日日程）"


def test_load_recent_schedule_documents_skips_empty() -> None:
    now = datetime(2026, 9, 3, 1, 30)
    store = {
        "2026-09-02": LifeDocument(
            date="2026-09-02",
            generated_at="",
            activities=[Activity("a1", "08:00", "09:00", "早餐")],
        ),
        "2026-09-01": LifeDocument(date="2026-09-01", generated_at="", activities=[]),
    }
    docs = load_recent_schedule_documents(store.get, now, 3)
    assert [item.date for item in docs] == ["2026-09-02"]
    assert load_recent_schedule_documents(store.get, now, 0) == []
    called: list[str] = []
    load_recent_schedule_documents(lambda date: called.append(date) or None, now, 30)
    assert len(called) == 14


def test_attach_recent_days_block_for_legacy_prompt() -> None:
    template = "前文\n【生成要求】\n- 输出 JSON"
    rendered = template
    recent = "2026-09-02 星期三：\n- 08:00-09:00 早餐"
    text = attach_recent_days_block(template, rendered, recent)
    assert text.index("近几日日程") < text.index("【生成要求】")
    assert "早餐" in text
    already = attach_recent_days_block(
        "【近几日日程】\n{recent_days_schedule}\n【生成要求】",
        "【近几日日程】\n已填充\n【生成要求】",
        recent,
    )
    assert already == "【近几日日程】\n已填充\n【生成要求】"
    assert attach_recent_days_block(template, rendered, "（无近几日日程）") == rendered


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
