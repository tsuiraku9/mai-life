import json
import tomllib
from copy import deepcopy
from pathlib import Path

import pytest

from mailife.config_model import MaiLifeConfig, ToolSection
from mailife.plugin import MaiLifePlugin
from mailife.tool_descriptions import DEFAULT_MODIFY_TOOL_DESCRIPTION, DEFAULT_QUERY_TOOL_DESCRIPTION


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
    assert sections["plugin"]["fields"]["admin_user_ids"]["label"] == "管理员"
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


def test_tools_section_shows_editable_defaults_and_reload_hints() -> None:
    schema = MaiLifePlugin.build_config_schema(
        plugin_id="mailife.mai-life",
        plugin_name="麦麦生活",
        plugin_version="1.3.0",
    )
    tools = schema["sections"]["tools"]
    assert tools["title"] == "规划器工具"
    assert set(tools["fields"]) == {"query_description", "modify_description"}
    assert schema["sections"]["share"]["order"] < tools["order"] < schema["sections"]["prompts"]["order"]
    for field_name, label, default, rows in (
        ("query_description", "查询工具描述", DEFAULT_QUERY_TOOL_DESCRIPTION, 2),
        ("modify_description", "日程与定时任务工具描述", DEFAULT_MODIFY_TOOL_DESCRIPTION, 4),
    ):
        field = tools["fields"][field_name]
        assert field["type"] == "string"
        assert field["default"] == default
        assert field["required"] is False
        assert field["label"] == label
        assert field["ui_type"] == "textarea"
        assert field["rows"] == rows
        assert "空白" in field["description"]
        assert "手动重载" in field["hint"]


def test_tools_model_and_missing_legacy_section_use_visible_defaults() -> None:
    assert ToolSection().model_dump() == {
        "query_description": DEFAULT_QUERY_TOOL_DESCRIPTION,
        "modify_description": DEFAULT_MODIFY_TOOL_DESCRIPTION,
    }
    old_config = MaiLifeConfig.model_validate({"plugin": {"config_version": "1.2.0"}})
    assert old_config.tools == ToolSection()
    assert old_config.plugin.config_version == "1.2.0"


@pytest.mark.parametrize(
    ("query_description", "modify_description"),
    [
        ("合成查询工具说明", "合成修改工具说明"),
        ("  保留首尾空格\n查询第二行  ", "\t修改说明\n"),
        ("", ""),
        (" \t\n", "\n　 "),
    ],
)
def test_tools_config_preserves_custom_empty_and_whitespace_values(
    query_description: str, modify_description: str
) -> None:
    supplied = {"query_description": query_description, "modify_description": modify_description}
    config = MaiLifeConfig.model_validate({"tools": supplied})
    assert config.tools.model_dump() == supplied
    # 描述回落属于组件注册逻辑，不在配置验证时改写用户保存的字符串。
    restored = MaiLifeConfig.model_validate(config.model_dump())
    assert restored.tools.model_dump() == supplied


def test_new_tools_section_does_not_rewrite_existing_config_values() -> None:
    supplied = {
        "plugin": {"enabled": True, "config_version": "1.2.0", "admin_user_ids": ["test:synthetic-admin"]},
        "generation": {"timezone": "UTC", "extra_persona": "合成补充人设\n保留这一行", "model": "test-task"},
        "schedule": {"allowed_streams": ["session:synthetic-stream"], "recent_inject_count": 1},
        "share": {
            "wake_planner": False,
            "stream_profiles": [
                {"stream": "session:synthetic-stream", "count_min": 2, "count_max": 4, "extra_prompt": "合成额外提示词"}
            ],
            "silence_start": "22:10",
            "silence_end": "06:20",
        },
        "prompts": {"schedule_system": "合成日程系统提示词", "wake_intent_template": "合成唤醒模板"},
    }
    before = deepcopy(supplied)
    config = MaiLifeConfig.model_validate(supplied)
    assert supplied == before
    for section_name, fields in supplied.items():
        section = getattr(config, section_name).model_dump()
        for field_name, expected in fields.items():
            assert section[field_name] == expected
    assert config.tools == ToolSection()


def test_plugin_does_not_register_config_upgrade_hook() -> None:
    hooks = [
        component["metadata"].get("hook", "")
        for component in MaiLifePlugin().get_components()
        if component["type"] == "HOOK_HANDLER"
    ]
    assert "config.upgrade" not in hooks
    assert not any("upgrade" in hook.lower() for hook in hooks)


def test_default_tool_descriptions_are_concise_and_actionable() -> None:
    for description in (DEFAULT_QUERY_TOOL_DESCRIPTION, DEFAULT_MODIFY_TOOL_DESCRIPTION):
        assert len(description) < 350
        assert "当前对话" in description
        assert "task_id" in description
        assert "调用示例" not in description
        assert "RPC" not in description
    assert "生活日程" in DEFAULT_QUERY_TOOL_DESCRIPTION
    assert "修改/取消" in DEFAULT_QUERY_TOOL_DESCRIPTION
    for required in ("target=schedule", "target=share", "title", "intent", "run_at", "delay_minutes", "pending"):
        assert required in DEFAULT_MODIFY_TOOL_DESCRIPTION
    assert "不等于消息已发送" in DEFAULT_MODIFY_TOOL_DESCRIPTION
    assert "不支持周期任务" in DEFAULT_MODIFY_TOOL_DESCRIPTION


def test_public_template_manifest_and_schema_use_same_proposed_version() -> None:
    plugin_dir = Path(__file__).resolve().parents[1]
    manifest = json.loads((plugin_dir / "_manifest.json").read_text(encoding="utf-8"))
    example = tomllib.loads((plugin_dir / "config.example.toml").read_text(encoding="utf-8"))
    assert manifest["version"] == MaiLifeConfig().plugin.config_version == example["plugin"]["config_version"] == "1.3.0"
    assert example["tools"] == ToolSection().model_dump()
    config = MaiLifeConfig.model_validate(example)
    assert config.tools == ToolSection()
