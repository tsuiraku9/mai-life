from datetime import datetime

from mailife.config_model import MaiLifeConfig
from mailife.plugin import (
    MaiLifePlugin,
    command_caller_identity,
    compose_persona,
    create_plugin,
    is_admin_user,
    knowledge_search_args,
)


def test_create_plugin_and_lifecycle_methods() -> None:
    instance = create_plugin()
    assert isinstance(instance, MaiLifePlugin)
    assert callable(instance.on_load)
    assert callable(instance.on_unload)
    assert callable(instance.on_config_update)

    components = instance.get_components()
    names = {item["name"] for item in components}
    types = {item["type"] for item in components}
    assert "mai_life_query" in names
    assert "mai_life_modify" in names
    assert "mai_life_inject_schedule" in names
    assert "mai_life_help" in names
    assert "ACTION" not in types


def test_compose_persona_switch() -> None:
    assert compose_persona(include_system=False, system_persona="主程序人设", extra_persona="补充") == "补充"
    assert compose_persona(include_system=False, system_persona="主程序人设", extra_persona="") == ""
    assert (
        compose_persona(include_system=True, system_persona="主程序人设", extra_persona="补充")
        == "主程序人设\n\n补充设定：补充"
    )
    assert compose_persona(include_system=True, system_persona="主程序人设", extra_persona="") == "主程序人设"
    assert compose_persona(include_system=True, system_persona="", extra_persona="补充") == "补充"


def test_knowledge_search_args_unlimited_without_window() -> None:
    now = datetime(2026, 9, 3, 12, 0)
    payload = knowledge_search_args(now, limit=5, window_hours=0)
    assert payload["query"]
    assert payload["limit"] == 5
    assert "mode" not in payload
    assert "time_start" not in payload
    assert "time_end" not in payload


def test_knowledge_search_args_hybrid_with_window() -> None:
    now = datetime(2026, 9, 3, 12, 0)
    payload = knowledge_search_args(now, limit=3, window_hours=24)
    assert payload["mode"] == "hybrid"
    assert payload["limit"] == 3
    assert payload["time_end"] == now.timestamp()
    assert payload["time_start"] == now.timestamp() - 24 * 3600


def test_knowledge_window_hours_default() -> None:
    assert MaiLifeConfig().generation.knowledge_window_hours == 168


def test_persona_source_removed_from_config() -> None:
    config = MaiLifeConfig.model_validate({"generation": {"persona_source": "extra", "extra_persona": "只要补充"}})
    assert not hasattr(config.generation, "persona_source")
    assert config.generation.include_persona is True
    assert config.generation.extra_persona == "只要补充"


def test_admin_user_ids_default_empty() -> None:
    assert MaiLifeConfig().plugin.admin_user_ids == []


def test_is_admin_user_matches_platform_user() -> None:
    admins = ["qq:123456", "webui:webui_user_xxx"]
    assert is_admin_user("qq", "123456", admins) is True
    assert is_admin_user("QQ", "123456", admins) is True
    assert is_admin_user("qq", "999", admins) is False
    assert is_admin_user("discord", "123456", admins) is False
    assert is_admin_user("webui", "webui_user_xxx", admins) is True
    assert is_admin_user("qq", "123456", ["qq:private:123456"]) is True
    assert is_admin_user("", "123456", ["123456"]) is True
    assert is_admin_user("qq", "123456", ["123456"]) is False
    assert is_admin_user("qq", "123456", []) is False
    assert is_admin_user("qq", "123456", [], is_local_operator=True) is True
    assert is_admin_user("", "", ["qq:123456"]) is False


def test_command_caller_identity_reads_host_fields() -> None:
    platform, user_id, is_local = command_caller_identity(
        {
            "platform": "qq",
            "user_id": "123456",
            "is_local_operator": False,
            "message": {
                "platform": "webui",
                "message_info": {"user_info": {"user_id": "spoofed"}},
            },
        }
    )
    assert (platform, user_id, is_local) == ("qq", "123456", False)
    nested_platform, nested_user, nested_local = command_caller_identity(
        {
            "is_local_operator": True,
            "message": {
                "platform": "webui",
                "message_info": {"user_info": {"user_id": "webui_user_xxx"}},
            },
        }
    )
    assert (nested_platform, nested_user, nested_local) == ("webui", "webui_user_xxx", True)
