"""真实 SDK 组件收集与 fake 上下文的离线集成测试，不调用业务能力。"""

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import AsyncMock

import pytest
from maibot_sdk import CONFIG_RELOAD_SCOPE_SELF
from maibot_sdk.components import collect_components

from mailife.config_model import MaiLifeConfig, ShareStreamProfile
from mailife.injector import build_today_schedule_text
from mailife.plugin import MaiLifePlugin
from mailife.store import Activity, LifeDocument, LifeStore, ShareItem
from mailife.tool_descriptions import DEFAULT_MODIFY_TOOL_DESCRIPTION, DEFAULT_QUERY_TOOL_DESCRIPTION


_NOW = datetime(2026, 9, 2, 0, 30)
_TODAY = "2026-09-02"
_YESTERDAY = "2026-09-01"
_STREAM_A = "synthetic-stream-a"
_STREAM_B = "synthetic-stream-b"
_STREAM_C = "synthetic-stream-c"
_GHOST_STREAM = "synthetic-unregistered-stream"
_STREAMS = [
    {"session_id": _STREAM_A, "platform": "qq", "chat_type": "private", "user_id": "synthetic-a"},
    {"session_id": _STREAM_B, "platform": "qq", "chat_type": "private", "user_id": "synthetic-b"},
    {"session_id": _STREAM_C, "platform": "qq", "chat_type": "private", "user_id": "synthetic-c"},
]


def _deny_business(*args: Any, **kwargs: Any) -> None:
    del args, kwargs
    raise AssertionError("离线测试禁止真实网络、模型、消息或规划器调用")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("socket.create_connection", _deny_business)
    monkeypatch.setattr("socket.socket.connect", _deny_business)
    monkeypatch.setattr("socket.socket.connect_ex", _deny_business)


def _config() -> MaiLifeConfig:
    config = MaiLifeConfig()
    config.plugin.enabled = True
    config.generation.timezone = "Asia/Shanghai"
    config.generation.generate_on_load_if_missing = False
    config.generation.include_persona = False
    config.generation.include_history = False
    config.generation.include_knowledge = False
    config.schedule.allowed_streams = ["qq:private:synthetic-a"]
    config.schedule.recent_inject_count = 1
    config.schedule.inject_window_minutes = 60
    config.share.stream_profiles = [
        ShareStreamProfile(stream="qq:private:synthetic-a"),
        ShareStreamProfile(stream="qq:private:synthetic-b"),
    ]
    return config


@dataclass
class _Clock:
    now: datetime = _NOW

    def __call__(self) -> datetime:
        return self.now


@dataclass
class _Harness:
    plugin: MaiLifePlugin
    store: LifeStore
    ctx: Any
    clock: _Clock
    blocked: list[AsyncMock]


def _blocked_mock(blocked: list[AsyncMock]) -> AsyncMock:
    mock = AsyncMock(side_effect=_deny_business)
    blocked.append(mock)
    return mock


@pytest.fixture
def make_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    harnesses: list[_Harness] = []

    def make(config: MaiLifeConfig | None = None, streams: list[dict[str, Any]] | None = None) -> _Harness:
        plugin = MaiLifePlugin()
        plugin.set_plugin_config((config or _config()).model_dump(mode="python"))
        store = LifeStore(tmp_path / f"plugin-{len(harnesses)}")
        blocked: list[AsyncMock] = []
        ctx = SimpleNamespace(
            logger=logging.getLogger("mailife.synthetic-test-context"),
            paths=SimpleNamespace(data_dir=store.data_dir),
            chat=SimpleNamespace(get_all_streams=AsyncMock(return_value=deepcopy(_STREAMS if streams is None else streams))),
            llm=SimpleNamespace(generate=_blocked_mock(blocked)),
            send=SimpleNamespace(text=_blocked_mock(blocked)),
            config=SimpleNamespace(get=_blocked_mock(blocked)),
            maisaka=SimpleNamespace(proactive=SimpleNamespace(trigger=_blocked_mock(blocked))),
            api=SimpleNamespace(replace_dynamic_apis=_blocked_mock(blocked)),
        )
        plugin._set_context(ctx)  # type: ignore[arg-type]
        plugin._store = store
        plugin._generator = SimpleNamespace(  # type: ignore[assignment]
            modify=_blocked_mock(blocked), generate_today=_blocked_mock(blocked)
        )
        clock = _Clock()
        monkeypatch.setattr(plugin, "_now", clock)
        harness = _Harness(plugin, store, ctx, clock, blocked)
        harnesses.append(harness)
        return harness

    yield make
    for harness in harnesses:
        for mock in harness.blocked:
            mock.assert_not_called()


def _seed_schedule(harness: _Harness, today_state: str) -> None:
    harness.store.save(
        LifeDocument(
            date=_YESTERDAY,
            generated_at="2026-09-01T01:30:00",
            activities=[
                Activity("ended", "19:00", "20:00", "昨日已结束合成活动"),
                Activity("ended-cross", "23:10", "00:15", "昨日已结束跨日活动"),
                Activity("sleep", "23:00", "07:00", "昨日合成睡眠", notes="合成睡眠备注"),
            ],
        )
    )
    if today_state != "missing":
        activities = []
        if today_state == "generated":
            activities = [
                Activity("breakfast", "08:00", "09:00", "今日合成早餐"),
                Activity("lunch", "12:00", "13:00", "今日合成午饭"),
                Activity("sleep", "23:00", "07:00", "今夜合成睡眠"),
            ]
        harness.store.save(LifeDocument(date=_TODAY, generated_at="2026-09-02T00:05:00", activities=activities))


def _stored_bytes(harness: _Harness) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in harness.store.data_dir.glob("life-*.json")}


def _payload(kind: str) -> list[dict[str, Any]]:
    if kind == "messages":
        return [
            {"role": "system", "content": "合成人设"},
            {"role": "user", "content": "合成提问"},
            {"role": "system", "content": "合成补充规则"},
            {"role": "assistant", "content": "合成回复"},
        ]
    return [
        {"item_type": "SystemMessageItem", "parts": [{"type": "text", "text": "合成人设"}]},
        {"item_type": "UserMessageItem", "parts": [{"type": "text", "text": "合成提问"}]},
        {"item_type": "SystemMessageItem", "parts": [{"type": "text", "text": "合成补充规则"}]},
        {"item_type": "AssistantMessageItem", "parts": [{"type": "text", "text": "合成回复"}]},
    ]


def _injected_text(result: dict[str, Any], kind: str) -> str:
    inserted = result["modified_kwargs"][kind][3]
    if kind == "messages":
        assert set(inserted) == {"role", "content"}
        assert inserted["role"] == "system"
        return inserted["content"]
    assert set(inserted) == {"item_type", "meta", "parts"}
    assert inserted["item_type"] == "SystemMessageItem"
    assert set(inserted["meta"]) == {"item_id", "logical_turn_id", "timestamp"}
    assert inserted["meta"]["item_id"]
    assert inserted["meta"]["logical_turn_id"] is None
    assert inserted["parts"][0]["type"] == "text"
    return inserted["parts"][0]["text"]


@pytest.mark.parametrize("kind", ["items", "messages"])
@pytest.mark.parametrize("today_state", ["missing", "empty", "generated"])
def test_hook_carries_yesterday_for_every_today_state(make_plugin: Callable[..., _Harness], kind: str, today_state: str) -> None:
    harness = make_plugin()
    _seed_schedule(harness, today_state)
    stored = _stored_bytes(harness)
    payload = _payload(kind)
    original = deepcopy(payload)
    result = asyncio.run(harness.plugin.inject_schedule_handler(session_id=_STREAM_A, **{kind: payload}, synthetic_hint="保留"))
    assert result["action"] == "continue"
    modified = result["modified_kwargs"]
    assert modified["session_id"] == _STREAM_A
    assert modified["synthetic_hint"] == "保留"
    assert modified[kind][:3] == payload[:3]
    assert modified[kind][4:] == payload[3:]
    text = _injected_text(result, kind)
    assert "2026-09-01 23:00 → 2026-09-02 07:00" in text
    assert "昨日开始、今日结束" in text
    assert "状态：进行中" in text
    assert "昨日合成睡眠" in text
    assert "昨日已结束" not in text
    assert "今夜合成睡眠" not in text
    assert len([line for line in text.splitlines() if line.startswith("- ")]) == 1
    if kind == "items":
        assert modified[kind][3]["meta"]["timestamp"] == _NOW.isoformat()
    assert payload == original
    assert _stored_bytes(harness) == stored
    harness.ctx.chat.get_all_streams.assert_awaited_once_with("all_platforms")


@pytest.mark.parametrize("kind", ["items", "messages"])
@pytest.mark.parametrize("today_state", ["missing", "empty", "generated"])
@pytest.mark.parametrize("after_end", [timedelta(), timedelta(minutes=1)])
def test_hook_excludes_carry_at_and_after_end(make_plugin: Callable[..., _Harness], kind: str, today_state: str, after_end: timedelta) -> None:
    harness = make_plugin()
    _seed_schedule(harness, today_state)
    harness.clock.now = datetime(2026, 9, 2, 7, 0) + after_end
    stored = _stored_bytes(harness)
    result = asyncio.run(harness.plugin.inject_schedule_handler(session_id=_STREAM_A, **{kind: _payload(kind)}))
    if today_state == "generated":
        text = _injected_text(result, kind)
        assert "今日合成早餐" in text
        assert "昨日合成睡眠" not in text
        assert "昨日已结束" not in text
    else:
        assert result == {"action": "continue"}
    assert _stored_bytes(harness) == stored


@pytest.mark.parametrize("kind", ["items", "messages"])
@pytest.mark.parametrize("gate", ["plugin_disabled", "schedule_disabled", "unlisted", "missing_session", "empty_allowlist"])
def test_hook_respects_switches_and_allowlist(make_plugin: Callable[..., _Harness], kind: str, gate: str) -> None:
    config = _config()
    session_id = _STREAM_A
    if gate == "plugin_disabled":
        config.plugin.enabled = False
    elif gate == "schedule_disabled":
        config.schedule.enabled = False
    elif gate == "empty_allowlist":
        config.schedule.allowed_streams = []
    elif gate == "unlisted":
        session_id = _STREAM_B
    else:
        session_id = ""
    harness = make_plugin(config)
    _seed_schedule(harness, "generated")
    stored = _stored_bytes(harness)
    payload = _payload(kind)
    original = deepcopy(payload)
    result = asyncio.run(harness.plugin.inject_schedule_handler(session_id=session_id, **{kind: payload}))
    assert result == {"action": "continue"}
    assert payload == original
    assert _stored_bytes(harness) == stored


@pytest.mark.parametrize("today_state", ["missing", "empty", "generated"])
@pytest.mark.parametrize("date", ["", _TODAY])
def test_query_today_uses_full_shared_carry_view(make_plugin: Callable[..., _Harness], today_state: str, date: str) -> None:
    harness = make_plugin()
    _seed_schedule(harness, today_state)
    stored = _stored_bytes(harness)
    result = asyncio.run(harness.plugin.handle_query(date=date, target="schedule", stream_id=_STREAM_A))
    assert result["success"] is True
    expected = build_today_schedule_text(harness.store.load(_TODAY), harness.store.load(_YESTERDAY), _NOW)
    assert result["content"] == f"{_TODAY} 日程：\n{expected}"
    assert "昨日合成睡眠" in result["content"]
    assert "昨日已结束" not in result["content"]
    assert result["timezone"] == "Asia/Shanghai"
    if today_state == "generated":
        assert "今日合成早餐" in result["content"]
        assert "今日合成午饭" in result["content"]
        tonight = next(line for line in result["content"].splitlines() if "今夜合成睡眠" in line)
        assert "2026-09-02 23:00 → 2026-09-03 07:00" in tonight
        assert "状态：待开始" in tonight
        assert len([line for line in result["content"].splitlines() if line.startswith("- ")]) == 4
    assert _stored_bytes(harness) == stored


@pytest.mark.parametrize("today_state", ["missing", "empty", "generated"])
@pytest.mark.parametrize("after_end", [timedelta(), timedelta(minutes=1)])
def test_query_today_excludes_finished_carry(make_plugin: Callable[..., _Harness], today_state: str, after_end: timedelta) -> None:
    harness = make_plugin()
    _seed_schedule(harness, today_state)
    harness.clock.now = datetime(2026, 9, 2, 7, 0) + after_end
    stored = _stored_bytes(harness)
    result = asyncio.run(harness.plugin.handle_query(target="schedule", stream_id=_STREAM_A))
    assert result["success"] is True
    assert "昨日合成睡眠" not in result["content"]
    assert "昨日已结束" not in result["content"]
    assert _stored_bytes(harness) == stored


def test_query_explicit_history_returns_original_record(make_plugin: Callable[..., _Harness]) -> None:
    harness = make_plugin()
    _seed_schedule(harness, "generated")
    stored = _stored_bytes(harness)
    result = asyncio.run(harness.plugin.handle_query(date=_YESTERDAY, target="schedule", chat_id=_STREAM_A))
    assert result["success"] is True
    assert f"{_YESTERDAY} 日程：" in result["content"]
    assert "昨日合成睡眠" in result["content"]
    assert "昨日已结束合成活动" in result["content"]
    assert "昨日已结束跨日活动" in result["content"]
    assert "今日合成早餐" not in result["content"]
    assert "今夜合成睡眠" not in result["content"]
    assert "状态：进行中" not in result["content"]
    assert _stored_bytes(harness) == stored


@pytest.mark.parametrize("gate", ["plugin_disabled", "schedule_disabled", "unlisted"])
def test_schedule_query_obeys_switches_and_allowlist(make_plugin: Callable[..., _Harness], gate: str) -> None:
    config = _config()
    if gate == "plugin_disabled":
        config.plugin.enabled = False
    elif gate == "schedule_disabled":
        config.schedule.enabled = False
    harness = make_plugin(config)
    _seed_schedule(harness, "generated")
    session = _STREAM_B if gate == "unlisted" else _STREAM_A
    result = asyncio.run(harness.plugin.handle_query(target="schedule", stream_id=session))
    assert "未启用" in result["content"]
    assert "昨日合成睡眠" not in result["content"]
    assert "今日合成早餐" not in result["content"]


def _task(task_id: str, stream_id: str, title: str, *, source: str = "planner", due_at: str = "2026-09-02T01:00:00") -> ShareItem:
    return ShareItem(
        id=task_id, time=due_at[11:16], title=title, intent=f"履行合成意图：{title}",
        stream_id=stream_id, source=source, due_at=due_at,
    )


def test_structured_tasks_crud_are_isolated_and_never_call_llm(make_plugin: Callable[..., _Harness]) -> None:
    harness = make_plugin()
    _seed_schedule(harness, "generated")
    yesterday = harness.store.load(_YESTERDAY)
    assert yesterday is not None
    yesterday.shares = [_task("generated-a", _STREAM_A, "合成自动分享", source="generated")]
    harness.store.save(yesterday)
    today = harness.store.load(_TODAY)
    assert today is not None
    today.shares = [_task("foreign-b", _STREAM_B, "不可泄漏的其他流任务")]
    harness.store.save(today)
    activities = deepcopy(today.activities)

    async def run() -> None:
        created = await harness.plugin.handle_modify(
            target="share", action="add", title="合成休息提醒", intent="履行约定：提醒起身休息",
            delay_minutes=15, request_id="synthetic-crud-request", stream_id=_STREAM_A,
        )
        assert created["success"] is True and created["operation"] == "created"
        record = created["task"]
        task_id = record["task_id"]
        assert record["owner_date"] == _TODAY
        assert record["run_at"] == "2026-09-02T00:45:00"
        assert record["timezone"] == "Asia/Shanghai"
        assert record["source"] == "planner" and record["state"] == "pending"
        assert record["request_id"] == "synthetic-crud-request"
        for listed in (
            await harness.plugin.handle_modify(target="share", action="list", stream_id=_STREAM_A),
            await harness.plugin.handle_query(target="share", stream_id=_STREAM_A),
        ):
            assert listed["success"] is True
            assert {item["task_id"] for item in listed["tasks"]} == {"generated-a", task_id}
            assert "不可泄漏的其他流任务" not in listed["content"]
        combined = await harness.plugin.handle_query(target="both", stream_id=_STREAM_A)
        assert "昨日合成睡眠" in combined["content"]
        assert "合成自动分享" in combined["content"]
        assert "合成休息提醒" in combined["content"]
        updated = await harness.plugin.handle_modify(
            target="share", action="update", task_id=task_id, date=record["owner_date"],
            title="合成改期提醒", run_at="2026-09-03 09:30:00", stream_id=_STREAM_A,
        )
        assert updated["success"] is True and updated["operation"] == "updated"
        assert updated["task"]["task_id"] == task_id
        assert updated["task"]["owner_date"] == _TODAY
        assert updated["task"]["run_at"] == "2026-09-03T09:30:00"
        assert not harness.store.path_for("2026-09-03").exists()
        due_date = await harness.plugin.handle_query(target="share", date="2026-09-03", stream_id=_STREAM_A)
        assert [item["task_id"] for item in due_date["tasks"]] == [task_id]
        owner_date = await harness.plugin.handle_query(target="share", date=_TODAY, stream_id=_STREAM_A)
        assert [item["task_id"] for item in owner_date["tasks"]] == ["generated-a"]
        cancelled = await harness.plugin.handle_modify(
            target="share", action="delete", task_id=task_id, date=_TODAY, stream_id=_STREAM_A,
        )
        assert cancelled["success"] is True and cancelled["operation"] == "cancelled"
        assert cancelled["task"]["state"] == "cancelled"
        pending = await harness.plugin.handle_modify(target="share", action="list", stream_id=_STREAM_A)
        assert [item["task_id"] for item in pending["tasks"]] == ["generated-a"]
        terminal = await harness.plugin.handle_query(target="share", date="2026-09-03", stream_id=_STREAM_A)
        assert terminal["tasks"][0]["state"] == "cancelled"

    asyncio.run(run())
    saved = harness.store.load(_TODAY)
    assert saved is not None
    assert saved.activities == activities
    assert next(item for item in saved.shares if item.id == "foreign-b") == today.shares[0]
    assert harness.store.load(_YESTERDAY) == yesterday


@pytest.mark.parametrize("identity_key", ["stream_id", "chat_id", "session_id", "missing"])
def test_task_add_requires_registered_host_stream(make_plugin: Callable[..., _Harness], identity_key: str) -> None:
    config = _config()
    config.share.stream_profiles = [ShareStreamProfile(stream="all")]
    harness = make_plugin(config)
    identity = {} if identity_key == "missing" else {identity_key: _GHOST_STREAM}
    stored = _stored_bytes(harness)
    result = asyncio.run(harness.plugin.handle_modify(
        target="share", action="add", title="不可安排的任务", intent="合成意图", delay_minutes=15, **identity
    ))
    assert result["success"] is False
    assert "流" in result["content"]
    assert _stored_bytes(harness) == stored


@pytest.mark.parametrize("gate", ["plugin_disabled", "share_disabled", "empty_allowlist", "unlisted"])
def test_task_add_respects_switches_and_allowlist(make_plugin: Callable[..., _Harness], gate: str) -> None:
    config = _config()
    if gate == "plugin_disabled":
        config.plugin.enabled = False
    elif gate == "share_disabled":
        config.share.enabled = False
    elif gate == "empty_allowlist":
        config.share.stream_profiles = []
    harness = make_plugin(config)
    session = _STREAM_C if gate == "unlisted" else _STREAM_A
    result = asyncio.run(harness.plugin.handle_modify(
        target="share", action="add", title="不可安排的任务", intent="合成意图", delay_minutes=15, stream_id=session
    ))
    assert result["success"] is False
    assert "未启用" in result["content"]
    assert _stored_bytes(harness) == {}


@pytest.mark.parametrize("action", ["update", "delete"])
def test_task_id_cannot_modify_another_stream(make_plugin: Callable[..., _Harness], action: str) -> None:
    harness = make_plugin()
    harness.store.save(LifeDocument(date=_TODAY, generated_at="", shares=[_task("foreign-b", _STREAM_B, "其他流原任务")]))
    stored = _stored_bytes(harness)
    extra = {"title": "越权标题"} if action == "update" else {}
    result = asyncio.run(harness.plugin.handle_modify(
        target="share", action=action, task_id="foreign-b", date=_TODAY, stream_id=_STREAM_A, **extra
    ))
    assert result["success"] is False
    assert _stored_bytes(harness) == stored
    listed = asyncio.run(harness.plugin.handle_query(target="share", stream_id=_STREAM_A))
    assert listed["tasks"] == []
    assert "其他流原任务" not in listed["content"]


def test_explicit_cross_stream_parameter_is_rejected(make_plugin: Callable[..., _Harness]) -> None:
    harness = make_plugin()
    result = asyncio.run(harness.plugin.handle_modify(
        target="share", action="add", title="越权目标", intent="合成意图", delay_minutes=15,
        stream_id=_STREAM_A, target_stream_id=_STREAM_B,
    ))
    assert result["success"] is False
    assert "跨流" in result["content"]
    assert _stored_bytes(harness) == {}


def test_task_query_rejects_unregistered_stream_even_with_all_allowlist(make_plugin: Callable[..., _Harness]) -> None:
    config = _config()
    config.share.stream_profiles = [ShareStreamProfile(stream="all")]
    harness = make_plugin(config)
    harness.store.save(LifeDocument(date=_TODAY, generated_at="", shares=[_task("ghost", _GHOST_STREAM, "不应泄漏的失效流任务")]))
    stored = _stored_bytes(harness)
    result = asyncio.run(harness.plugin.handle_query(target="share", stream_id=_GHOST_STREAM))
    assert not result.get("tasks")
    assert "不应泄漏的失效流任务" not in result["content"]
    assert _stored_bytes(harness) == stored


@pytest.mark.parametrize("identity_key", ["stream_id", "chat_id"])
def test_nested_action_data_cannot_override_host_current_stream(make_plugin: Callable[..., _Harness], identity_key: str) -> None:
    harness = make_plugin()
    harness.store.save(LifeDocument(
        date=_TODAY, generated_at="", shares=[
            _task("current-a", _STREAM_A, "当前流合成任务"),
            _task("foreign-b", _STREAM_B, "其他流合成任务"),
        ],
    ))
    identity = {identity_key: _STREAM_A}
    nested = {
        "stream_id": _STREAM_B, "chat_id": _STREAM_B, "session_id": _STREAM_B,
        "kwargs": {"stream_id": _STREAM_B},
    }
    result = asyncio.run(harness.plugin.handle_query(target="share", action_data=nested, **identity))
    assert result["success"] is True
    assert [item["task_id"] for item in result["tasks"]] == ["current-a"]
    assert "其他流合成任务" not in result["content"]
    created = asyncio.run(harness.plugin.handle_modify(
        target="share", action="add", title="按真实当前流安排", intent="合成意图", delay_minutes=15,
        action_data=nested, **identity,
    ))
    assert created["success"] is True
    saved = harness.store.load(_TODAY)
    assert saved is not None
    new_item = next(item for item in saved.shares if item.id == created["task"]["task_id"])
    assert new_item.stream_id == _STREAM_A
    stored = _stored_bytes(harness)
    denied = asyncio.run(harness.plugin.handle_modify(
        target="share", action="delete", task_id="foreign-b", action_data=nested, **identity,
    ))
    assert denied["success"] is False
    assert _stored_bytes(harness) == stored


@pytest.mark.parametrize("identity", [
    {"session_id": _STREAM_A},
    {"action_data": {"stream_id": _STREAM_A, "chat_id": _STREAM_A}},
    {"session_id": _STREAM_B, "action_data": {"stream_id": _STREAM_A}},
])
def test_tools_reject_session_or_nested_identity_without_host_top_level_stream(make_plugin: Callable[..., _Harness], identity: dict[str, Any]) -> None:
    harness = make_plugin()
    _seed_schedule(harness, "generated")
    document = harness.store.load(_TODAY)
    assert document is not None
    document.shares = [
        _task("current-a", _STREAM_A, "不应泄漏的当前流任务"),
        _task("foreign-b", _STREAM_B, "不应泄漏的其他流任务"),
    ]
    harness.store.save(document)
    stored = _stored_bytes(harness)
    for target in ("schedule", "share", "both"):
        queried = asyncio.run(harness.plugin.handle_query(target=target, **identity))
        assert not queried.get("tasks")
        assert "不应泄漏" not in queried["content"]
        assert "昨日合成睡眠" not in queried["content"]
    added = asyncio.run(harness.plugin.handle_modify(
        target="share", action="add", title="不应安排的任务", intent="合成意图", delay_minutes=15, **identity,
    ))
    assert added["success"] is False
    listed = asyncio.run(harness.plugin.handle_modify(target="share", action="list", **identity))
    assert listed["success"] is False
    assert _stored_bytes(harness) == stored


def test_wake_planner_false_warns_without_discarding_task(make_plugin: Callable[..., _Harness]) -> None:
    config = _config()
    config.share.wake_planner = False
    harness = make_plugin(config)
    created = asyncio.run(harness.plugin.handle_modify(
        target="share", action="add", title="关闭唤醒的合成提醒", intent="合成意图", delay_minutes=15, stream_id=_STREAM_A
    ))
    assert created["success"] is True
    assert created["task"]["state"] == "pending"
    assert "wake_planner=false" in created["warning"]
    assert "不会自动唤醒规划器" in created["warning"]
    assert created["warning"] in created["content"]
    updated = asyncio.run(harness.plugin.handle_modify(
        target="share", action="update", task_id=created["task"]["task_id"], title="合成提醒更新", stream_id=_STREAM_A
    ))
    assert updated["success"] is True
    assert "不会自动唤醒规划器" in updated["warning"]
    listed = asyncio.run(harness.plugin.handle_query(target="share", stream_id=_STREAM_A))
    assert [item["task_id"] for item in listed["tasks"]] == [created["task"]["task_id"]]


def _tools(components: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item["metadata"] for item in components if item["name"] in {"mai_life_query", "mai_life_modify"}}


def _set_tool_descriptions(plugin: MaiLifePlugin, query: str, modify: str) -> dict[str, Any]:
    data = plugin.config.model_dump(mode="python")
    data["tools"]["query_description"] = query
    data["tools"]["modify_description"] = modify
    plugin.set_plugin_config(data)
    return data


def test_sdk_final_metadata_has_complete_default_descriptions_and_schema(make_plugin: Callable[..., _Harness]) -> None:
    harness = make_plugin()
    components = harness.plugin.get_components()
    tools = _tools(components)
    expected = {"mai_life_query": DEFAULT_QUERY_TOOL_DESCRIPTION, "mai_life_modify": DEFAULT_MODIFY_TOOL_DESCRIPTION}
    for name, description in expected.items():
        assert tools[name]["description"] == description
        assert tools[name]["brief_description"] == description
        assert tools[name]["core_tool"] is True
        assert tools[name]["invoke_method"] == "plugin.invoke_action"
        assert tools[name]["handler_name"] == {"mai_life_query": "handle_query", "mai_life_modify": "handle_modify"}[name]
        component = next(item for item in components if item["name"] == name)
        assert component["type"] == "TOOL"
        assert "task_id" in description
        assert len(description) < 350
    query = {item["name"]: item for item in tools["mai_life_query"]["parameters"]}
    assert query["target"]["enum_values"] == ["schedule", "share", "both"]
    modify = {item["name"]: item for item in tools["mai_life_modify"]["parameters"]}
    assert modify["target"]["required"] is True
    assert modify["action"]["required"] is True
    assert modify["action"]["enum_values"] == ["list", "add", "update", "delete", "replace_day"]
    assert {"task_id", "title", "intent", "run_at", "delay_minutes", "request_id"}.issubset(modify)
    hook = next(item for item in components if item["name"] == "mai_life_inject_schedule")
    assert hook["metadata"]["error_policy"] == "skip"


@pytest.mark.parametrize("blank", ["", " \t\n "])
def test_sdk_metadata_uses_custom_then_restores_default_on_blank(make_plugin: Callable[..., _Harness], blank: str) -> None:
    harness = make_plugin()
    defaults = _tools(harness.plugin.get_components())
    _set_tool_descriptions(harness.plugin, "  合成查询完整说明\n查询示例  ", "  合成修改完整说明\n修改示例  ")
    custom = _tools(harness.plugin.get_components())
    assert custom["mai_life_query"]["description"] == "合成查询完整说明\n查询示例"
    assert custom["mai_life_modify"]["description"] == "合成修改完整说明\n修改示例"
    for name in custom:
        assert custom[name]["brief_description"] == custom[name]["description"]
        assert custom[name]["parameters"] == defaults[name]["parameters"]
    _set_tool_descriptions(harness.plugin, blank, blank)
    restored = _tools(harness.plugin.get_components())
    assert restored["mai_life_query"]["description"] == DEFAULT_QUERY_TOOL_DESCRIPTION
    assert restored["mai_life_modify"]["description"] == DEFAULT_MODIFY_TOOL_DESCRIPTION
    assert custom["mai_life_query"]["description"] == "合成查询完整说明\n查询示例"


def test_self_config_update_requires_reload_without_mutating_registered_metadata(make_plugin: Callable[..., _Harness], caplog: pytest.LogCaptureFixture) -> None:
    harness = make_plugin()
    registered = harness.plugin.get_components()
    snapshot = deepcopy(registered)
    registered_descriptions = harness.plugin._registered_tool_descriptions
    updated = _set_tool_descriptions(harness.plugin, "更新后的合成查询说明", "更新后的合成修改说明")
    # 生命周期回调可以调用 fake 调度器，但不能触发真实业务或组件元数据同步。
    scheduler = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    harness.plugin._scheduler = scheduler  # type: ignore[assignment]
    with caplog.at_level(logging.INFO, logger="mailife.mai-life"):
        asyncio.run(harness.plugin.on_config_update(CONFIG_RELOAD_SCOPE_SELF, updated, "synthetic-version"))
    assert "需手动重载插件" in caplog.text
    assert "不热更新组件元数据" in caplog.text
    assert "已刷新工具" not in caplog.text
    assert registered == snapshot
    assert harness.plugin._registered_tool_descriptions == registered_descriptions
    scheduler.start.assert_awaited_once_with(generate_if_missing=False)
    scheduler.stop.assert_not_called()
    recollected = _tools(harness.plugin.get_components())
    assert recollected["mai_life_query"]["description"] == "更新后的合成查询说明"
    assert _tools(registered)["mai_life_query"]["description"] == DEFAULT_QUERY_TOOL_DESCRIPTION


def test_component_descriptions_do_not_pollute_other_instances_or_decorators(make_plugin: Callable[..., _Harness]) -> None:
    first = make_plugin()
    second = make_plugin()
    second_registered = second.plugin.get_components()
    second_snapshot = deepcopy(second_registered)
    _set_tool_descriptions(first.plugin, "实例一合成查询", "实例一合成修改")
    first_metadata = _tools(first.plugin.get_components())
    assert first_metadata["mai_life_query"]["description"] == "实例一合成查询"
    assert second_registered == second_snapshot
    assert _tools(second.plugin.get_components())["mai_life_query"]["description"] == DEFAULT_QUERY_TOOL_DESCRIPTION
    _set_tool_descriptions(second.plugin, "实例二合成查询", "实例二合成修改")
    assert _tools(second.plugin.get_components())["mai_life_query"]["description"] == "实例二合成查询"
    assert first_metadata["mai_life_query"]["description"] == "实例一合成查询"
    # SDK 原始收集读取装饰器共享信息；不应被任一实例的个性化说明修改。
    raw = _tools(collect_components(first.plugin))
    assert raw["mai_life_query"]["description"] == DEFAULT_QUERY_TOOL_DESCRIPTION
    assert raw["mai_life_modify"]["description"] == DEFAULT_MODIFY_TOOL_DESCRIPTION
    third = make_plugin()
    third_metadata = _tools(third.plugin.get_components())
    assert third_metadata["mai_life_query"]["description"] == DEFAULT_QUERY_TOOL_DESCRIPTION
    assert third_metadata["mai_life_modify"]["description"] == DEFAULT_MODIFY_TOOL_DESCRIPTION


@pytest.mark.parametrize("target", ["share", "both"])
def test_platform_scoped_cache_keeps_real_non_schedule_stream_available(make_plugin: Callable[..., _Harness], target: str) -> None:
    config = _config()
    config.schedule.stream_discovery_platform = "qq"
    config.share.stream_profiles = [ShareStreamProfile(stream="all")]
    web_stream = {"session_id": "synthetic-web-stream", "platform": "webui", "user_id": "synthetic-web-user"}
    harness = make_plugin(config, streams=_STREAMS + [web_stream])
    harness.ctx.chat.get_all_streams.side_effect = lambda platform: deepcopy(_STREAMS if platform == "qq" else _STREAMS + [web_stream])

    async def run():
        await harness.plugin._cached_streams("qq")
        queried = await harness.plugin.handle_query(target=target, stream_id=web_stream["session_id"])
        assert "当前聊天流跨日期待处理任务" in queried["content"]
        added = await harness.plugin.handle_modify(target="share", action="add", title="合成跨平台提醒", intent="履行合成约定", delay_minutes=10, stream_id=web_stream["session_id"])
        assert added["success"] is True
        assert harness.store.find_task(web_stream["session_id"], added["task"]["task_id"])
        assert [args.args[0] for args in harness.ctx.chat.get_all_streams.await_args_list] == ["qq", "all_platforms"]

    asyncio.run(run())


def test_share_only_query_does_not_discover_schedule_platform(make_plugin: Callable[..., _Harness]) -> None:
    config = _config()
    config.schedule.stream_discovery_platform = "qq"
    harness = make_plugin(config)
    result = asyncio.run(harness.plugin.handle_query(target="share", stream_id=_STREAM_A))
    assert result["success"] is True
    harness.ctx.chat.get_all_streams.assert_awaited_once_with("all_platforms")


@pytest.mark.parametrize("stop", ["unload", "disable"])
def test_manual_generation_cancelled_before_unload_or_disable_returns(make_plugin: Callable[..., _Harness], monkeypatch: pytest.MonkeyPatch, stop: str) -> None:
    harness = make_plugin()
    plugin = harness.plugin
    monkeypatch.setattr(plugin, "_require_command_admin", AsyncMock(return_value=None))
    monkeypatch.setattr(plugin, "_reply", AsyncMock())
    scheduler = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    plugin._scheduler = scheduler  # type: ignore[assignment]

    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        cancelled = asyncio.Event()

        async def delayed_generate(force):
            assert force
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            harness.store.save(LifeDocument(_TODAY, "synthetic-late-result"))
            return harness.store.load(_TODAY)

        monkeypatch.setattr(plugin, "_generate_today", delayed_generate)
        await plugin.handle_generate(stream_id=_STREAM_A)
        await started.wait()
        task = next(iter(plugin._manual_generation_tasks))
        if stop == "unload":
            await plugin.on_unload()
        else:
            updated = plugin.config.model_dump(mode="python")
            updated["plugin"]["enabled"] = False
            plugin.set_plugin_config(updated)
            await plugin.on_config_update(CONFIG_RELOAD_SCOPE_SELF, updated, "synthetic-version")
        assert cancelled.is_set()
        assert task.done() and task.cancelled()
        assert plugin._manual_generation_tasks == set()
        release.set()
        await asyncio.sleep(0)
        assert harness.store.load(_TODAY) is None

    asyncio.run(run())
    scheduler.stop.assert_awaited_once()
