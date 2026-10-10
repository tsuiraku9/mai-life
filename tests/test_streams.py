from mailife.streams import filter_streams, session_allowed, stream_match_keys


def test_stream_match_keys() -> None:
    keys = stream_match_keys(
        {
            "session_id": "abc",
            "platform": "qq",
            "group_id": "123",
            "user_id": "456",
        }
    )
    assert "session:abc" in keys
    assert "qq:group:123" in keys
    assert "qq:private:456" in keys


def test_empty_allowlist_matches_nothing() -> None:
    streams = [{"session_id": "abc", "platform": "qq"}]
    assert filter_streams(streams, []) == []
    assert not session_allowed("abc", [])


def test_all_allowlist() -> None:
    streams = [{"session_id": "a"}, {"session_id": "b"}]
    matched = filter_streams(streams, ["all"])
    assert len(matched) == 2
    assert session_allowed("a", ["all"])


def test_webui_photo_studio_style_allowlist() -> None:
    session_id = "synthetic-webui-session"
    user_id = "webui_user_synthetic_fixture"
    streams = [
        {
            "session_id": session_id,
            "platform": "webui",
            "user_id": user_id,
            "chat_type": "private",
        }
    ]
    assert session_allowed(session_id, [f"webui:{user_id}"], streams)
    assert session_allowed(session_id, [f"webui:private:{user_id}"], streams)
    matched = filter_streams(streams, [f"webui:{user_id}"])
    assert [item["session_id"] for item in matched] == [session_id]


def test_session_and_group_allowlist() -> None:
    streams = [
        {"session_id": "s1", "platform": "qq", "group_id": "999"},
        {"session_id": "s2", "platform": "qq", "user_id": "111"},
    ]
    matched = filter_streams(streams, ["qq:group:999"])
    assert [item["session_id"] for item in matched] == ["s1"]
    assert session_allowed("s2", ["qq:private:111"], streams)
    assert not session_allowed("s1", ["qq:private:111"], streams)
