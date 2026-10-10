from datetime import datetime, timedelta

import pytest

from mailife.store import LifeDocument, LifeStore, ShareItem, task_due_at
from mailife.tasks import TaskManager


NOW = datetime(2031, 4, 15, 23, 50)
ZONE = "Asia/Shanghai"


def manage(manager: TaskManager, **fields):
    return manager.manage(
        stream_id=fields.pop("stream_id", "stream-a"),
        now=fields.pop("now", NOW), timezone=ZONE, **fields,
    )


def add(manager: TaskManager, **fields):
    args = {"action": "add", "title": "喝水提醒", "intent": "履行约定，提醒对方喝水", "delay_minutes": 15}
    args.update(fields)
    return manage(manager, **args)


def test_cross_day_add_and_reschedule_keep_owner(tmp_path):
    store = LifeStore(tmp_path)
    manager = TaskManager(store)
    created = add(manager)["task"]
    assert created["owner_date"] == "2031-04-15"
    assert created["run_at"] == "2031-04-16T00:05:00"
    assert created["source"] == "planner"
    assert created["state"] == "pending"
    assert created["timezone"] == ZONE
    assert created["task_id"].startswith("task_")
    assert len(manager.list("stream-a", ZONE)) == 1
    assert manager.list("stream-a", ZONE, "2031-04-15") == []
    assert len(manager.list("stream-a", ZONE, "2031-04-16")) == 1
    result = manage(manager, action="update", task_id=created["task_id"], run_at="2031-04-18 09:30:12")
    assert result["task"]["owner_date"] == created["owner_date"]
    assert result["task"]["run_at"] == "2031-04-18T09:30:12"
    assert store.load("2031-04-18") is None
    restarted = TaskManager(LifeStore(tmp_path))
    assert restarted.list("stream-a", ZONE)[0]["run_at"] == "2031-04-18T09:30:12"


@pytest.mark.parametrize("fields", [
    {"run_at": "明天早上", "delay_minutes": None},
    {"run_at": "2031-04-16 00:05", "delay_minutes": 15},
    {"run_at": "2031-04-15 20:00", "delay_minutes": None},
    {"run_at": "2031-02-30 00:05", "delay_minutes": None},
    {"run_at": "2031-04-16T00:05:00+08:00", "delay_minutes": None},
    {"delay_minutes": 0}, {"delay_minutes": -1}, {"delay_minutes": True},
    {"delay_minutes": "15"}, {"delay_minutes": float("inf")},
    {"delay_minutes": float("nan")}, {"delay_minutes": 1e300},
    {"delay_minutes": None}, {"title": ""}, {"intent": ""},
    {"date": "../outside"}, {"date": "2031-04-16"},
    {"request": "把下午改为休息"},
])
def test_add_rejects_invalid_or_ambiguous_inputs(tmp_path, fields):
    store = LifeStore(tmp_path)
    with pytest.raises(ValueError):
        add(TaskManager(store), **fields)
    assert store.list_tasks() == []


def test_fractional_delay_is_future(tmp_path):
    record = add(TaskManager(LifeStore(tmp_path)), delay_minutes=0.5)["task"]
    assert record["run_at"] == "2031-04-15T23:50:30"


def test_idempotent_relative_retry_returns_original_time_even_after_due(tmp_path):
    manager = TaskManager(LifeStore(tmp_path))
    first = add(manager, request_id="synthetic-once")
    second = add(manager, request_id="synthetic-once", now=NOW + timedelta(minutes=60))
    assert second["operation"] == "existing"
    assert first["task"] == second["task"]
    with pytest.raises(ValueError):
        add(manager, request_id="synthetic-once", intent="另一种意图")
    with pytest.raises(ValueError):
        add(manager, request_id="synthetic-once", run_at="2031-04-17 12:00", delay_minutes=None)
    assert len(manager.list("stream-a", ZONE)) == 1


def test_request_binding_survives_later_reschedule_and_intent_change(tmp_path):
    store = LifeStore(tmp_path)
    manager = TaskManager(store)
    first = add(manager, request_id="stable-key")["task"]
    manage(manager, action="update", task_id=first["task_id"], intent="改后的意图", run_at="2031-04-17 12:00")
    restarted = TaskManager(LifeStore(tmp_path))
    retried = add(restarted, request_id="stable-key", now=NOW + timedelta(minutes=50))
    assert retried["operation"] == "existing"
    assert retried["task"]["task_id"] == first["task_id"]
    assert retried["task"]["run_at"] == "2031-04-17T12:00:00"
    with pytest.raises(ValueError):
        add(restarted, request_id="stable-key", delay_minutes=20)
    assert len(store.list_tasks()) == 1


def test_exact_dedupe_no_fuzzy_merge_or_cross_stream_match(tmp_path):
    manager = TaskManager(LifeStore(tmp_path))
    first = add(manager)
    assert add(manager)["operation"] == "existing"
    other_intent = add(manager, intent="另一个不同的意图")
    other_time = add(manager, delay_minutes=16)
    other_stream = add(manager, stream_id="stream-b")
    assert len(manager.list("stream-a", ZONE)) == 3
    assert len(manager.list("stream-b", ZONE)) == 1
    ids = {result["task"]["task_id"] for result in [first, other_intent, other_time, other_stream]}
    assert len(ids) == 4


def test_exact_duplicate_add_adopts_generated_and_binds_retry_key(tmp_path):
    store = LifeStore(tmp_path)
    store.save(LifeDocument("2031-04-15", "", shares=[
        ShareItem("auto", "00:05", "喝水提醒", intent="履行约定，提醒对方喝水", stream_id="stream-a", source="generated", due_at="2031-04-16T00:05:00"),
    ]))
    manager = TaskManager(store)
    result = add(manager, request_id="first-key")
    assert result["operation"] == "existing"
    assert result["task"]["source"] == "planner"
    second_key = add(manager, request_id="second-key", title="相同意图的另一个标题")
    assert second_key["operation"] == "existing"
    retry = add(manager, request_id="second-key", title="相同意图的另一个标题", now=NOW + timedelta(minutes=3))
    assert retry["task"]["task_id"] == "auto"
    assert retry["task"]["run_at"] == "2031-04-16T00:05:00"
    assert len(store.list_tasks()) == 1


def test_optional_title_change_and_cancel_do_not_clear_other_tasks(tmp_path):
    store = LifeStore(tmp_path)
    manager = TaskManager(store)
    first, other = add(manager), add(manager, intent="另一个意图")
    updated = manage(manager, action="update", task_id=first["task"]["task_id"], title="新标题")
    assert updated["task"]["title"] == "新标题"
    assert updated["task"]["run_at"] == first["task"]["run_at"]
    cancelled = manage(manager, action="delete", task_id=first["task"]["task_id"])
    assert cancelled["task"]["state"] == "cancelled"
    assert manager.list("stream-a", ZONE)[0]["task_id"] == other["task"]["task_id"]
    assert len(manager.list("stream-a", ZONE, "2031-04-16")) == 2
    with pytest.raises(ValueError):
        manage(manager, action="update", task_id=first["task"]["task_id"], delay_minutes=20)


@pytest.mark.parametrize("state", ["submitting", "submitted", "skipped", "cancelled", "consumed"])
@pytest.mark.parametrize("action", ["update", "delete"])
def test_non_pending_never_reactivated(tmp_path, state, action):
    store = LifeStore(tmp_path)
    manager = TaskManager(store)
    task = add(manager)["task"]
    store.mark_task_state(task["owner_date"], "stream-a", task["task_id"], state, "synthetic", NOW)
    with pytest.raises(ValueError):
        fields = {"delay_minutes": 20} if action == "update" else {}
        manage(manager, action=action, task_id=task["task_id"], **fields)


def test_overdue_pending_requires_future_time_for_update(tmp_path):
    manager = TaskManager(LifeStore(tmp_path))
    task = add(manager)["task"]
    late = NOW + timedelta(minutes=16)
    with pytest.raises(ValueError):
        manage(manager, action="update", task_id=task["task_id"], title="改名", now=late)
    result = manage(manager, action="update", task_id=task["task_id"], delay_minutes=10, now=late)
    assert result["task"]["run_at"] == "2031-04-16T00:16:00"


def test_generated_task_adoption_protects_other_source_and_stream(tmp_path):
    store = LifeStore(tmp_path)
    store.save(LifeDocument("2031-04-15", "", shares=[
        ShareItem("legacy-id", "23:59", "旧分享", stream_id="stream-a"),
        ShareItem("legacy-id", "23:59", "其他流", stream_id="stream-b"),
        ShareItem("generated", "23:59", "自动分享", intent="自然分享", stream_id="stream-a", source="generated", due_at="2031-04-15T23:59:00"),
    ]))
    manager = TaskManager(store)
    changed = manage(manager, action="update", task_id="generated", title="已接管的分享")
    assert changed["task"]["source"] == "planner"
    with pytest.raises(ValueError):
        manage(manager, action="update", task_id="legacy-id", title="只改标题")
    adopted = manage(manager, action="update", task_id="legacy-id", intent="履行约定，提醒休息")
    assert adopted["task"]["source"] == "planner"
    assert adopted["task"]["run_at"] == "2031-04-15T23:59:00"
    assert manager.list("stream-b", ZONE)[0]["title"] == "其他流"


def test_cross_stream_and_legacy_owner_collision(tmp_path):
    store = LifeStore(tmp_path)
    for day in ["2031-04-15", "2031-04-16"]:
        store.save(LifeDocument(day, "", shares=[ShareItem("same", "23:59", "旧任务", intent="旧意图", stream_id="stream-a")]))
    manager = TaskManager(store)
    with pytest.raises(ValueError):
        manage(manager, action="delete", task_id="same")
    with pytest.raises(ValueError):
        manage(manager, action="delete", task_id="same", stream_id="stream-b")
    result = manage(manager, action="delete", task_id="same", date="2031-04-15")
    assert result["task"]["owner_date"] == "2031-04-15"
    assert len(manager.list("stream-a", ZONE)) == 1


def test_disabled_wake_warning_and_structured_list(tmp_path):
    manager = TaskManager(LifeStore(tmp_path))
    saved = add(manager, wake_planner=False)
    assert "不会自动唤醒" in saved["warning"]
    listed = manage(manager, action="list")
    assert listed["success"] is True
    assert listed["tasks"][0] == saved["task"]
    assert "task_id=" in listed["content"]
    assert "已发出" not in listed["content"]


@pytest.mark.parametrize("action,fields", [
    ("replace_day", {}), ("cron", {}), ("list", {"title": "多余"}),
    ("update", {"title": "无ID"}), ("update", {"task_id": "missing"}),
    ("delete", {"task_id": "missing", "delay_minutes": 1}),
])
def test_invalid_actions_and_conditional_parameters(tmp_path, action, fields):
    with pytest.raises(ValueError):
        manage(TaskManager(LifeStore(tmp_path)), action=action, **fields)


def test_terminal_and_legacy_records_truthful_on_date_query(tmp_path):
    store = LifeStore(tmp_path)
    old = ShareItem("old", "23:59", "旧任务", stream_id="stream-a", fired=True)
    store.save(LifeDocument("2031-04-15", "", shares=[old]))
    manager = TaskManager(store)
    assert manager.list("stream-a", ZONE) == []
    assert manager.list("stream-a", ZONE, "2031-04-15")[0]["state"] == "consumed"
    assert task_due_at(old, "2031-04-15") == datetime(2031, 4, 15, 23, 59)
