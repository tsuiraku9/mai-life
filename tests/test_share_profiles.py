from mailife.config_model import MaiLifeConfig, ShareStreamProfile, resolve_share_profile, share_allowlist
from mailife.store import LifeDocument, ShareItem, document_from_dict, replace_stream_shares, shares_for_stream


def test_resolve_share_profile_default() -> None:
    config = MaiLifeConfig()
    count_min, count_max, extra = resolve_share_profile(
        config.share,
        {"session_id": "s1", "platform": "qq", "user_id": "111"},
    )
    assert count_min == 3
    assert count_max == 6
    assert extra == ""


def test_resolve_share_profile_override() -> None:
    config = MaiLifeConfig()
    config.share.stream_profiles = [
        ShareStreamProfile(stream="qq:private:111", count_min=2, count_max=2, extra_prompt="只聊学习"),
    ]
    count_min, count_max, extra = resolve_share_profile(
        config.share,
        {"session_id": "s1", "platform": "qq", "user_id": "111"},
    )
    assert (count_min, count_max, extra) == (2, 2, "只聊学习")
    other_min, other_max, other_extra = resolve_share_profile(
        config.share,
        {"session_id": "s2", "platform": "qq", "user_id": "222"},
    )
    assert (other_min, other_max, other_extra) == (3, 6, "")


def test_resolve_share_profile_zero_counts_use_builtin_default() -> None:
    config = MaiLifeConfig()
    config.share.stream_profiles = [
        ShareStreamProfile(stream="qq:private:111", count_min=0, count_max=0, extra_prompt="只聊学习"),
    ]
    count_min, count_max, extra = resolve_share_profile(
        config.share,
        {"session_id": "s1", "platform": "qq", "user_id": "111"},
    )
    assert (count_min, count_max, extra) == (3, 6, "只聊学习")


def test_share_allowlist_comes_from_profiles() -> None:
    config = MaiLifeConfig()
    assert share_allowlist(config.share) == []
    config.share.stream_profiles = [
        ShareStreamProfile(stream="qq:private:111"),
        ShareStreamProfile(stream="all"),
        ShareStreamProfile(stream=""),
    ]
    assert share_allowlist(config.share) == ["qq:private:111", "all"]


def test_legacy_allowed_streams_merge_into_profiles() -> None:
    config = MaiLifeConfig.model_validate(
        {
            "share": {
                "allowed_streams": ["qq:private:111", "all"],
                "stream_profiles": [
                    {"stream": "qq:private:111", "count_min": 2, "count_max": 2, "extra_prompt": "学习"}
                ],
            }
        }
    )
    streams = [profile.stream for profile in config.share.stream_profiles]
    assert streams == ["qq:private:111", "all"]
    assert config.share.stream_profiles[0].extra_prompt == "学习"


def test_legacy_share_defaults_migrate_into_profiles() -> None:
    config = MaiLifeConfig.model_validate(
        {
            "share": {
                "stream_discovery_platform": "qq",
                "count_min": 4,
                "count_max": 8,
                "extra_prompt": "默认要求",
                "miss_tolerance_minutes": 10,
                "stream_profiles": [
                    {"stream": "qq:private:111"},
                    {
                        "stream": "qq:private:222",
                        "count_min": 1,
                        "count_max": 2,
                        "extra_prompt": "只要学习",
                    },
                ],
            }
        }
    )
    first, second = config.share.stream_profiles
    assert (first.count_min, first.count_max, first.extra_prompt) == (4, 8, "默认要求")
    assert (second.count_min, second.count_max, second.extra_prompt) == (1, 2, "只要学习")
    assert not hasattr(config.share, "count_min")
    assert not hasattr(config.share, "miss_tolerance_minutes")


def test_resolve_share_profile_prefers_specific_over_all() -> None:
    config = MaiLifeConfig()
    config.share.stream_profiles = [
        ShareStreamProfile(stream="all", extra_prompt="日常"),
        ShareStreamProfile(stream="qq:private:111", extra_prompt="只聊学习"),
    ]
    specific = resolve_share_profile(
        config.share,
        {"session_id": "s1", "platform": "qq", "user_id": "111"},
    )
    wildcard = resolve_share_profile(
        config.share,
        {"session_id": "s2", "platform": "qq", "user_id": "222"},
    )
    assert specific == (3, 6, "只聊学习")
    assert wildcard == (3, 6, "日常")


def test_shares_isolated_by_stream() -> None:
    document = LifeDocument(date="2026-08-30", generated_at="")
    replace_stream_shares(
        document,
        "s1",
        [ShareItem(id="a", time="12:00", title="午饭")],
    )
    replace_stream_shares(
        document,
        "s2",
        [ShareItem(id="b", time="18:00", title="晚饭")],
    )
    assert [item.title for item in shares_for_stream(document, "s1")] == ["午饭"]
    assert [item.title for item in shares_for_stream(document, "s2")] == ["晚饭"]
    replace_stream_shares(document, "s1", [ShareItem(id="c", time="13:00", title="奶茶")])
    assert [item.title for item in shares_for_stream(document, "s1")] == ["奶茶"]
    assert [item.title for item in shares_for_stream(document, "s2")] == ["晚饭"]


def test_load_shares_by_stream() -> None:
    document = document_from_dict(
        {
            "date": "2026-08-30",
            "generated_at": "",
            "activities": [],
            "shares_by_stream": {
                "s1": [{"time": "12:30", "title": "午饭"}],
            },
        }
    )
    assert document is not None
    assert document.shares[0].stream_id == "s1"
    assert document.shares[0].title == "午饭"
