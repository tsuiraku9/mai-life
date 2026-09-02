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
    assert sections["share"]["fields"]["count_min"]["label"] == "默认生成条数下限"
    assert sections["share"]["fields"]["extra_prompt"]["label"] == "默认额外提示词"
    assert "allowed_streams" not in sections["share"]["fields"]
    assert "allowed_streams" in sections["schedule"]["fields"]
    assert sections["share"]["fields"]["stream_profiles"]["label"] == "启用的聊天流"
    profile_fields = sections["share"]["fields"]["stream_profiles"]["item_fields"]
    assert profile_fields["stream"]["label"] == "聊天流"
    assert profile_fields["extra_prompt"]["label"] == "额外提示词"
    assert sections["generation"]["fields"]["time"]["label"] == "生成时刻"
    english_labels = []
    for section in sections.values():
        for field in section["fields"].values():
            label = str(field.get("label") or "")
            if label.isascii() and "_" in label:
                english_labels.append(label)
    assert english_labels == []
