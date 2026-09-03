from mailife.prompts import DEFAULT_SCHEDULE_USER, DEFAULT_SHARE_USER, choose_template, render_template


def test_render_known_placeholders() -> None:
    text = render_template("今天是 {date}，角色 {bot_nickname}", {"date": "2026-08-29", "bot_nickname": "麦麦"})
    assert text == "今天是 2026-08-29，角色 麦麦"


def test_unknown_placeholder_kept() -> None:
    text = render_template("保留 {unknown} 和 {date}", {"date": "2026-08-29"})
    assert "{unknown}" in text
    assert "2026-08-29" in text


def test_json_example_not_eaten() -> None:
    template = '输出 {"activities":[{"start":"HH:MM"}]} 日期 {date}'
    text = render_template(template, {"date": "2026-08-29"})
    assert '{"activities":[{"start":"HH:MM"}]}' in text
    assert text.endswith("2026-08-29")


def test_choose_template_fallback() -> None:
    assert choose_template("  ", "默认") == "默认"
    assert choose_template("自定义", "默认") == "自定义"


def test_schedule_prompt_has_no_mood() -> None:
    assert "mood" not in DEFAULT_SCHEDULE_USER
    assert "心情" not in DEFAULT_SCHEDULE_USER


def test_schedule_prompt_includes_recent_days() -> None:
    assert "{recent_days_schedule}" in DEFAULT_SCHEDULE_USER
    assert "近几日日程" in DEFAULT_SCHEDULE_USER
    assert "不要生成几乎相同的安排" in DEFAULT_SCHEDULE_USER


def test_share_prompt_includes_silence_window() -> None:
    assert "{silence_start}" in DEFAULT_SHARE_USER
    assert "{silence_end}" in DEFAULT_SHARE_USER
    assert "静默时段" in DEFAULT_SHARE_USER
