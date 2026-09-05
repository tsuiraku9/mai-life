import asyncio
from datetime import datetime
from pathlib import Path
import tempfile

from mailife.config_model import MaiLifeConfig
from mailife.plugin import MaiLifePlugin
from mailife.store import Activity, LifeDocument, LifeStore, ShareItem


class _FakeSend:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def text(self, text: str, stream_id: str) -> None:
        self.messages.append((text, stream_id))


class _FakeChat:
    def __init__(self, streams: list[dict] | None = None) -> None:
        self.streams = list(streams or [])

    async def get_all_streams(self, platform: str = "all_platforms") -> list[dict]:
        del platform
        return list(self.streams)


class _FakeCtx:
    def __init__(self, streams: list[dict] | None = None) -> None:
        self.send = _FakeSend()
        self.chat = _FakeChat(streams)


def _plugin(*, admin_user_ids: list[str], streams: list[dict] | None = None) -> MaiLifePlugin:
    plugin = MaiLifePlugin()
    config = MaiLifeConfig()
    config.plugin.admin_user_ids = admin_user_ids
    plugin.set_plugin_config(config.model_dump(mode="python"))
    plugin._ctx = _FakeCtx(streams)  # type: ignore[assignment]
    return plugin


def test_commands_reject_non_admin() -> None:
    plugin = _plugin(admin_user_ids=["qq:admin"])
    result = asyncio.run(plugin.handle_help(stream_id="s1", platform="qq", user_id="ordinary"))
    assert result == (True, "没有权限", 2)
    assert plugin.ctx.send.messages[0][0] == "只有管理员可以使用麦麦生活命令"


def test_commands_reject_when_admin_list_empty() -> None:
    plugin = _plugin(admin_user_ids=[])
    result = asyncio.run(plugin.handle_status(stream_id="s1", platform="qq", user_id="anyone"))
    assert result == (True, "没有权限", 2)
    assert "尚未配置管理员" in plugin.ctx.send.messages[0][0]


def test_local_operator_can_use_commands_without_admin_list() -> None:
    plugin = _plugin(admin_user_ids=[])
    result = asyncio.run(plugin.handle_help(stream_id="s1", is_local_operator=True))
    assert result == (True, "已发送帮助", 2)
    assert "仅管理员" in plugin.ctx.send.messages[0][0]


def test_admin_can_show_all_stream_shares() -> None:
    streams = [
        {"session_id": "stream-a", "platform": "qq", "user_id": "111"},
        {"session_id": "stream-b", "platform": "webui", "user_id": "webui_user_xxx"},
    ]
    plugin = _plugin(admin_user_ids=["qq:admin"], streams=streams)
    with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as raw:
        store = LifeStore(Path(raw))
        store.save(
            LifeDocument(
                date="2026-09-05",
                generated_at="2026-09-05T01:30:00",
                activities=[Activity("a1", "08:00", "09:00", "起床")],
                shares=[
                    ShareItem("s1", "12:00", "午饭", stream_id="stream-a"),
                    ShareItem("s2", "18:00", "晚饭", stream_id="stream-b"),
                ],
            )
        )
        plugin._store = store
        plugin._now = lambda: datetime(2026, 9, 5, 12, 0)  # type: ignore[method-assign]
        result = asyncio.run(plugin.handle_show(stream_id="stream-a", platform="qq", user_id="admin"))

    assert result == (True, "已发送今日记录", 2)
    text = plugin.ctx.send.messages[0][0]
    assert "起床" in text
    assert "午饭" in text
    assert "晚饭" in text
    assert "stream-a" in text
    assert "stream-b" in text
    assert "全聊天流分享任务" in text
