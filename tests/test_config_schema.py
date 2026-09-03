from mailife.plugin import MaiLifePlugin


def test_webui_field_labels_are_chinese() -> None:
    schema = MaiLifePlugin.build_config_schema(
        plugin_id="mailife.mai-life",
        plugin_name="麦麦生活",
        plugin_version="1.0.0",
    )
    sections = schema["sections"]
    assert sections["plugin"]["title"] == "插件"
    assert sections["share"]["title"] == "分享提醒"
    assert sections["plugin"]["fields"]["enabled"]["label"] == "启用插件"
    assert "count_min" not in sections["share"]["fields"]
    assert "count_max" not in sections["share"]["fields"]
    assert "extra_prompt" not in sections["share"]["fields"]
    assert "stream_discovery_platform" not in sections["share"]["fields"]
    assert "miss_tolerance_minutes" not in sections["share"]["fields"]
    assert "allowed_streams" not in sections["share"]["fields"]
    assert "allowed_streams" in sections["schedule"]["fields"]
    assert "stream_discovery_platform" in sections["schedule"]["fields"]
    assert sections["share"]["fields"]["stream_profiles"]["label"] == "启用的聊天流"
    assert sections["share"]["fields"]["silence_start"]["label"] == "静默开始"
    profile_fields = sections["share"]["fields"]["stream_profiles"]["item_fields"]
    assert profile_fields["stream"]["label"] == "聊天流"
    assert profile_fields["count_min"]["label"] == "生成条数下限"
    assert profile_fields["count_max"]["label"] == "生成条数上限"
    assert profile_fields["extra_prompt"]["label"] == "额外提示词"
    assert sections["generation"]["fields"]["time"]["label"] == "生成时刻"
    assert "persona_source" not in sections["generation"]["fields"]
    assert sections["generation"]["fields"]["include_persona"]["label"] == "读入人设"
    assert sections["generation"]["fields"]["extra_persona"]["label"] == "补充人设"
    assert sections["generation"]["fields"]["knowledge_window_hours"]["label"] == "记忆时间窗口（小时）"
    assert sections["schedule"]["fields"]["recent_days"]["label"] == "参考最近天数"
    english_labels = []
    for section in sections.values():
        for field in section["fields"].values():
            label = str(field.get("label") or "")
            if label.isascii() and "_" in label:
                english_labels.append(label)
    assert english_labels == []
