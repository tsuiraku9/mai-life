from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json

import pytest

from mailife.store import LifeDocument, LifeStore, ShareItem, document_from_dict, task_state
from mailife.tasks import TaskManager


NOW = datetime(2031, 4, 15, 12, 0)


def item(task_id="same", stream="a", **fields):
    return ShareItem(
        task_id, "12:10", "合成任务", stream_id=stream, intent="提醒合成事项",
        source="planner", due_at="2031-04-15T12:10:00", **fields,
    )


@pytest.mark.parametrize("date", ["../outside", "", "2031-4-15", "2031-02-30", "2031-04-15/../x", "20310415"])
def test_invalid_date_never_used_as_path(tmp_path, date):
    store = LifeStore(tmp_path)
    with pytest.raises(ValueError):
        store.load(date)
    with pytest.raises(ValueError):
        store.save(LifeDocument(date, ""))
    assert document_from_dict({"date": date}) is None
    assert list(tmp_path.iterdir()) == []


def test_same_id_cross_stream_is_never_marked_by_id_only(tmp_path):
    store = LifeStore(tmp_path)
    store.save(LifeDocument("2031-04-15", "", shares=[item(stream="a"), item(stream="b")]))
    assert store.mark_share_fired("2031-04-15", "same", NOW) is None
    assert all(task_state(task) == "pending" for _, task in store.list_tasks())
    assert store.mark_task_state("2031-04-15", "a", "same", "submitted", "host_accepted", NOW) is not None
    tasks = {task.stream_id: task for _, task in store.list_tasks()}
    assert tasks["a"].state == "submitted"
    assert tasks["b"].state == "pending"


def test_claim_is_compare_and_set_against_reschedule_or_cancel(tmp_path):
    store = LifeStore(tmp_path)
    store.save(LifeDocument("2031-04-15", "", shares=[item()]))
    stale = store.list_tasks()[0][1]
    manager = TaskManager(store)
    manager.manage(action="update", stream_id="a", task_id="same", delay_minutes=20, now=NOW, timezone="local")
    assert store.claim_task("2031-04-15", stale) is None
    fresh = store.list_tasks()[0][1]
    claim = store.claim_task("2031-04-15", fresh)
    assert claim is not None and claim.state == "submitting"
    assert store.claim_task("2031-04-15", fresh) is None
    with pytest.raises(ValueError):
        manager.manage(action="delete", stream_id="a", task_id="same", now=NOW, timezone="local")


def test_stale_expiration_does_not_overwrite_new_time(tmp_path):
    store = LifeStore(tmp_path)
    store.save(LifeDocument("2031-04-15", "", shares=[item()]))
    stale = store.list_tasks()[0][1]
    TaskManager(store).manage(action="update", stream_id="a", task_id="same", delay_minutes=60, now=NOW, timezone="local")
    assert store.mark_task_state("2031-04-15", "a", "same", "skipped", "missed", NOW, expected=stale) is None
    assert store.list_tasks()[0][1].state == "pending"


def test_interrupted_submission_not_blindly_retried_on_restart(tmp_path):
    store = LifeStore(tmp_path)
    store.save(LifeDocument("2031-04-15", "", shares=[item(state="submitting")]))
    restarted = LifeStore(tmp_path)
    task = restarted.list_tasks()[0][1]
    assert task.state == "consumed"
    assert task.state_reason == "submission_result_unknown"
    assert restarted.list_tasks(pending_only=True) == []
    assert restarted.load("2031-04-15").shares[0].fired is True


def test_interrupted_submission_timestamp_uses_configured_wall_clock(tmp_path):
    store = LifeStore(tmp_path)
    store.save(LifeDocument("2031-04-15", "", shares=[item(state="submitting")]))
    configured_now = datetime(2031, 4, 16, 0, 10)
    restarted = LifeStore(tmp_path, now=lambda: configured_now)
    restored = restarted.find_task("a", "same")[1]
    assert restored.state_reason == "submission_result_unknown"
    assert restored.fired_at == "2031-04-16T00:10:00"


def test_atomic_failure_preserves_old_file_and_index(tmp_path, monkeypatch):
    store = LifeStore(tmp_path)
    store.save(LifeDocument("2031-04-15", "", shares=[item()]))
    old = store.path_for("2031-04-15").read_bytes()

    def fail(*args):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr("mailife.store.os.replace", fail)
    with pytest.raises(OSError):
        store.mutate("2031-04-15", lambda doc: doc.shares.clear())
    assert store.path_for("2031-04-15").read_bytes() == old
    assert len(store.list_tasks()) == 1
    assert len(list(tmp_path.iterdir())) == 1


def test_corrupt_or_wrong_date_is_not_overwritten_by_crud(tmp_path):
    path = tmp_path / "life-2031-04-15.json"
    path.write_text("{truncated", encoding="utf-8")
    store = LifeStore(tmp_path)
    assert store.list_tasks() == []
    with pytest.raises(ValueError):
        store.mutate("2031-04-15", lambda doc: doc.shares.append(item()))
    assert path.read_text(encoding="utf-8") == "{truncated"
    path.write_text(json.dumps({"date": "2031-04-16"}), encoding="utf-8")
    with pytest.raises(ValueError):
        store.load("2031-04-15")


@pytest.mark.parametrize("activities", [1, 0, "bad", "", {}, None, {"start": "12:00"}])
def test_bad_activities_file_does_not_block_other_day_or_get_overwritten(tmp_path, activities):
    path = tmp_path / "life-2031-04-15.json"
    path.write_text(json.dumps({"date": "2031-04-15", "activities": activities, "shares": []}), encoding="utf-8")
    original = path.read_bytes()
    good = tmp_path / "life-2031-04-16.json"
    good.write_text(json.dumps(LifeDocument("2031-04-16", "", shares=[item()]).to_dict()), encoding="utf-8")
    store = LifeStore(tmp_path)
    assert [(date, task.id) for date, task in store.list_tasks()] == [("2031-04-16", "same")]
    with pytest.raises(ValueError):
        store.load("2031-04-15")
    with pytest.raises(ValueError):
        store.mutate("2031-04-15", lambda doc: doc.shares.append(item()))
    assert path.read_bytes() == original


def test_concurrent_adds_are_serialized_and_unique(tmp_path):
    store = LifeStore(tmp_path)
    manager = TaskManager(store)

    def create(index):
        return manager.manage(
            action="add", stream_id="a", now=NOW, timezone="local", title="合成提醒",
            intent="履行合成约定", delay_minutes=10, request_id="same-request",
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create, range(12)))
    assert sum(result["operation"] == "created" for result in results) == 1
    assert len(store.list_tasks()) == 1
    assert len({result["task"]["task_id"] for result in results}) == 1


def test_index_rebuild_retains_old_consumed_and_cross_date_due(tmp_path):
    raw = {
        "date": "2031-04-15", "shares": [
            {"id": "old", "time": "12:10", "title": "旧任务", "stream_id": "a", "fired": True},
            item("future").to_dict() | {"due_at": "2031-04-18T12:00:00", "time": "12:00"},
        ],
    }
    (tmp_path / "life-2031-04-15.json").write_text(json.dumps(raw), encoding="utf-8")
    (tmp_path / "life-not-a-day.json").write_text("{}", encoding="utf-8")
    store = LifeStore(tmp_path)
    assert store.list_tasks(due_date="2031-04-18")[0][0] == "2031-04-15"
    old = store.find_task("a", "old")[1]
    assert old.source == "legacy" and old.state == "consumed"
    assert old.state_reason == "legacy_result_unknown"
    assert len(store.list_tasks(pending_only=True)) == 1


def test_invalid_new_due_never_falls_back_to_time(tmp_path):
    raw = {"date": "2031-04-15", "shares": [item().to_dict() | {"due_at": "bad"}]}
    (tmp_path / "life-2031-04-15.json").write_text(json.dumps(raw), encoding="utf-8")
    store = LifeStore(tmp_path)
    task = store.list_tasks()[0][1]
    assert task.state == "skipped" and task.state_reason == "invalid_due_at"
    assert store.list_tasks(pending_only=True) == []
