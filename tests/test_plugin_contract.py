from mailife.plugin import MaiLifePlugin, create_plugin


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
