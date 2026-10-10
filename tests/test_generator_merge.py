import asyncio
from datetime import datetime

import pytest

from mailife.config_model import MaiLifeConfig
from mailife.generator import LifeGenerator
from mailife.store import Activity, LifeDocument, LifeStore, ShareItem, task_state
from mailife.tasks import TaskManager


NOW = datetime(2031, 4, 15, 10, 0)
STREAMS = [{"session_id": "stream-a", "platform": "test", "user_id": "synthetic-user"}]


class FakeGenerator(LifeGenerator):
    def __init__(self, store, answers):
        super().__init__(None, store)
        self.answers = list(answers)
        self.calls = 0
        self.during = None
        self.prompts = []

    async def _ask_json(self, config, system_prompt, user_prompt, retries=0):
        self.calls += 1
        self.prompts.append((system_prompt, user_prompt))
        if self.during:
            callback, self.during = self.during, None
            callback()
        return self.answers.pop(0)


def config_without_schedule():
    config = MaiLifeConfig()
    config.schedule.enabled = False
    return config


def generate(generator, config=None, force=False, streams=None):
    return asyncio.run(generator.generate_today(
        config or config_without_schedule(), NOW, force=force,
        share_streams=STREAMS if streams is None else streams,
    ))


def share_answer(time="12:00", title="午间分享", **extra):
    return {"shares": [{"time": time, "title": title, "intent": "自然提起日常", **extra}]}


def add_planner(store):
    return TaskManager(store).manage(
        action="add", stream_id="stream-a", now=NOW, timezone="local",
        title="提醒休息", intent="履行合成约定提醒休息", delay_minutes=30,
    )["task"]


def test_planner_task_does_not_block_automatic_generation(tmp_path):
    store = LifeStore(tmp_path)
    planned = add_planner(store)
    generator = FakeGenerator(store, [share_answer()])
    generated = generate(generator)
    assert generator.calls == 1
    assert len(generated.shares) == 2
    assert {item.source for item in generated.shares} == {"generated", "planner"}
    assert generated.generated_streams == ["stream-a"]
    assert any(item.id == planned["task_id"] for item in generated.shares)
    auto = next(item for item in generated.shares if item.source == "generated")
    assert auto.due_at == "2031-04-15T12:00:00"
    assert auto.id.startswith("task_")
    generate(generator)
    assert generator.calls == 1


def test_forced_regeneration_retains_planner_adopted_and_terminal(tmp_path):
    store = LifeStore(tmp_path)
    add_planner(store)
    generated = generate(FakeGenerator(store, [share_answer()]))
    original = next(item for item in generated.shares if item.source == "generated")
    TaskManager(store).manage(action="update", stream_id="stream-a", now=NOW, timezone="local", task_id=original.id, title="接管分享")
    store.mutate("2031-04-15", lambda doc: doc.shares.extend([
        ShareItem("terminal", "11:00", "已提交", stream_id="stream-a", intent="旧意图", source="generated", due_at="2031-04-15T11:00:00", state="submitted", fired=True),
        ShareItem("replaceable", "13:00", "可重新生成", stream_id="stream-a", source="generated", due_at="2031-04-15T13:00:00"),
        ShareItem("other", "14:00", "其他流", stream_id="stream-b", source="generated", due_at="2031-04-15T14:00:00"),
    ]))
    result = generate(FakeGenerator(store, [share_answer("15:00")]), force=True)
    ids = {item.id for item in result.shares}
    assert original.id in ids
    assert "terminal" in ids and "other" in ids
    assert "replaceable" not in ids
    assert len(result.shares) == 5


def test_legacy_source_protected_and_force_warns(tmp_path, caplog):
    store = LifeStore(tmp_path)
    store.save(LifeDocument("2031-04-15", "", shares=[ShareItem("old", "12:00", "旧分享", stream_id="stream-a")]))
    generator = FakeGenerator(store, [])
    result = generate(generator, force=True)
    assert generator.calls == 0
    assert result.shares[0].id == "old"
    assert "旧任务来源不明" in caplog.text


def test_unbound_legacy_records_are_protected_not_replaced(tmp_path):
    store = LifeStore(tmp_path)
    store.save(LifeDocument("2031-04-15", "", shares=[ShareItem("old", "12:00", "旧分享")]))
    result = generate(FakeGenerator(store, []), force=True)
    assert result.shares[0].stream_id == ""
    assert result.generated_streams == []


def test_valid_empty_shares_marks_done_instead_of_looping(tmp_path):
    store = LifeStore(tmp_path)
    generator = FakeGenerator(store, [{"shares": []}])
    result = generate(generator)
    assert result.shares == []
    assert result.generated_streams == ["stream-a"]
    generate(generator)
    assert generator.calls == 1


def test_all_silenced_shares_is_valid_generation_completion(tmp_path):
    store = LifeStore(tmp_path)
    result = generate(FakeGenerator(store, [share_answer("01:00")]))
    assert result.shares == []
    assert result.generated_streams == ["stream-a"]


def test_invalid_or_failed_output_not_marked_done(tmp_path):
    store = LifeStore(tmp_path)
    for invalid in [None, {}, {"shares": "not-list"}, {"shares": [{"time": "bad", "title": "错误"}]}]:
        result = generate(FakeGenerator(store, [invalid]))
        assert result is None
        assert store.load("2031-04-15") is None
    assert generate(FakeGenerator(store, [share_answer()])).generated_streams == ["stream-a"]


def test_model_cannot_forge_identity_origin_stream_or_state(tmp_path):
    store = LifeStore(tmp_path)
    result = generate(FakeGenerator(store, [share_answer(id="forged", source="planner", state="submitted", fired=True, stream_id="other", due_at="2099-04-15T12:00:00")]))
    task = result.shares[0]
    assert task.id != "forged" and task.source == "generated"
    assert task.stream_id == "stream-a" and task_state(task) == "pending"
    assert task.due_at == "2031-04-15T12:00:00"


def test_generation_merge_retains_concurrent_crud_and_firing(tmp_path):
    store = LifeStore(tmp_path)
    original = generate(FakeGenerator(store, [share_answer()])).shares[0]
    generator = FakeGenerator(store, [share_answer("15:00")])

    def during():
        add_planner(store)
        store.mark_task_state("2031-04-15", "stream-a", original.id, "submitted", "host_accepted", NOW)

    generator.during = during
    result = generate(generator, force=True)
    assert len(result.shares) == 3
    assert next(item for item in result.shares if item.id == original.id).state == "submitted"
    assert any(item.source == "planner" for item in result.shares)


def test_generation_merge_keeps_task_adopted_while_model_worked(tmp_path):
    store = LifeStore(tmp_path)
    original = generate(FakeGenerator(store, [share_answer()])).shares[0]
    generator = FakeGenerator(store, [share_answer("15:00")])
    generator.during = lambda: TaskManager(store).manage(action="update", stream_id="stream-a", now=NOW, timezone="local", task_id=original.id, title="接管分享")
    result = generate(generator, force=True)
    assert any(item.id == original.id and item.source == "planner" for item in result.shares)
    assert len(result.shares) == 2


def test_schedule_modification_merge_keeps_new_tasks_and_states(tmp_path):
    store = LifeStore(tmp_path)
    store.save(LifeDocument("2031-04-15", "", activities=[Activity("a", "11:00", "12:00", "散步")]))
    generator = FakeGenerator(store, [{"activities": [{"start": "12:00", "end": "13:00", "title": "看书"}]}])
    generator.during = lambda: add_planner(store)
    result = asyncio.run(generator.modify(MaiLifeConfig(), NOW, date="2031-04-15", target="schedule", action="update", request="改为看书"))
    assert result.activities[0].title == "看书"
    assert result.shares[0].source == "planner"


@pytest.mark.parametrize("changes", [
    {"run_at": "2031-04-15 13:00"},
    {"intent": "已约定提醒，与原自由分享不同"},
    {"run_at": "2031-04-15 13:00", "intent": "已约定提醒，与原自由分享不同"},
])
def test_adopted_time_and_intent_protects_original_generated_signature(tmp_path, changes):
    store = LifeStore(tmp_path)
    original = generate(FakeGenerator(store, [share_answer()])).shares[0]
    manager = TaskManager(store)
    manager.manage(action="update", stream_id="stream-a", now=NOW, timezone="local", task_id=original.id, **changes)
    store = LifeStore(tmp_path)
    result = generate(FakeGenerator(store, [share_answer()]), force=True)
    assert len(result.shares) == 1
    assert result.shares[0].id == original.id
    assert result.shares[0].due_at == ("2031-04-15T13:00:00" if "run_at" in changes else "2031-04-15T12:00:00")
    assert result.shares[0].source == "planner"
    assert result.shares[0].intent == changes.get("intent", original.intent)
    assert result.shares[0].generation_key == original.generation_key


def test_cancelled_or_adopted_task_not_reintroduced_with_new_id(tmp_path):
    store = LifeStore(tmp_path)
    original = generate(FakeGenerator(store, [share_answer()])).shares[0]
    TaskManager(store).manage(action="delete", stream_id="stream-a", now=NOW, timezone="local", task_id=original.id)
    result = generate(FakeGenerator(store, [share_answer()]), force=True)
    assert len(result.shares) == 1
    assert result.shares[0].id == original.id and result.shares[0].state == "cancelled"


def test_same_stream_same_time_terminal_not_reintroduced(tmp_path):
    store = LifeStore(tmp_path)
    original = generate(FakeGenerator(store, [share_answer()])).shares[0]
    store.mark_task_state("2031-04-15", "stream-a", original.id, "submitted", "host_accepted", NOW)
    result = generate(FakeGenerator(store, [share_answer()]), force=True)
    assert len(result.shares) == 1
    assert result.shares[0].state == "submitted"


@pytest.mark.parametrize("adopt", ["add", "update"])
def test_preexisting_generated_record_acquires_signature_before_adoption(tmp_path, adopt):
    store = LifeStore(tmp_path)
    original = ShareItem("preexisting", "12:00", "合成旧自动任务", stream_id="stream-a", intent="自然提起日常", source="generated", due_at="2031-04-15T12:00:00")
    store.save(LifeDocument("2031-04-15", "", shares=[original]))
    manager = TaskManager(store)
    if adopt == "add":
        manager.manage(action="add", stream_id="stream-a", now=NOW, timezone="local", title="合成接管标题", intent=original.intent, run_at=original.due_at)
    manager.manage(action="update", stream_id="stream-a", now=NOW, timezone="local", task_id=original.id, run_at="2031-04-15 13:00")
    store = LifeStore(tmp_path)
    result = generate(FakeGenerator(store, [share_answer()]), force=True)
    assert len(result.shares) == 1
    assert result.shares[0].id == original.id
    assert result.shares[0].generation_key


@pytest.mark.parametrize("different", ["same", "stream", "day", "intent"])
def test_generation_deduplicates_same_full_signature_from_other_owner(tmp_path, different):
    store = LifeStore(tmp_path)
    original = ShareItem(
        "previous-owner", "12:00", "昨日日文件的合成提醒", source="planner",
        stream_id="stream-b" if different == "stream" else "stream-a",
        intent="另一合成意图" if different == "intent" else "自然提起日常",
        due_at="2031-04-16T12:00:00" if different == "day" else "2031-04-15T12:00:00",
    )
    store.save(LifeDocument("2031-04-14", "", shares=[original]))
    result = generate(FakeGenerator(store, [share_answer()]))
    assert result.generated_streams == ["stream-a"]
    assert len(store.list_tasks()) == (1 if different == "same" else 2)
    assert store.find_task(original.stream_id, original.id)[0] == "2031-04-14"


@pytest.mark.parametrize("stream_id", ["", "stream-a"])
def test_schedule_model_never_sees_unified_tasks_even_in_custom_prompt(tmp_path, stream_id):
    store = LifeStore(tmp_path)
    store.save(LifeDocument("2031-04-15", "", activities=[Activity("a", "11:00", "12:00", "合成原日程")], shares=[
        ShareItem("private-a", "12:00", "synthetic-private-title-a", stream_id="stream-a", intent="synthetic-private-intent-a"),
        ShareItem("private-b", "12:00", "synthetic-private-title-b", stream_id="stream-b", intent="synthetic-private-intent-b"),
    ]))
    config = MaiLifeConfig()
    config.prompts.modify_user = "{schedule_json}\nsynthetic-share-block:{share_json}\n{user_request}"
    generator = FakeGenerator(store, [{"activities": [{"start": "12:00", "end": "13:00", "title": "合成新日程"}]}])
    result = asyncio.run(generator.modify(config, NOW, date="2031-04-15", target="schedule", action="update", request="修改日程", stream_id=stream_id))
    prompt = generator.prompts[0][1]
    assert "合成原日程" in prompt
    assert "synthetic-share-block:{}" in prompt
    assert "synthetic-private" not in prompt
    assert [item.id for item in result.shares] == ["private-a", "private-b"]


@pytest.mark.parametrize("answer", [
    {}, {"activities": "bad"}, {"activities": None}, {"activities": [None]},
    {"activities": [{"start": "12:00", "end": "bad", "title": "非法活动"}]},
    {"activities": [{"start": "12:00", "end": "13:00", "title": "合成合法活动"}, {}]},
])
@pytest.mark.parametrize("action", ["delete", "update"])
def test_schedule_modify_rejects_missing_or_partial_list_without_writing(tmp_path, answer, action):
    store = LifeStore(tmp_path)
    store.save(LifeDocument("2031-04-15", "", activities=[Activity("a", "11:00", "12:00", "合成原日程")]))
    path = store.path_for("2031-04-15")
    before = path.read_bytes()
    generator = FakeGenerator(store, [answer])
    with pytest.raises(RuntimeError):
        asyncio.run(generator.modify(MaiLifeConfig(), NOW, date="2031-04-15", target="schedule", action=action, request="合成变更请求"))
    assert path.read_bytes() == before


def test_schedule_delete_accepts_explicit_empty_list_and_preserves_tasks(tmp_path):
    store = LifeStore(tmp_path)
    store.save(LifeDocument("2031-04-15", "", activities=[Activity("a", "11:00", "12:00", "合成原日程")]))
    planned = add_planner(store)
    result = asyncio.run(FakeGenerator(store, [{"activities": []}]).modify(MaiLifeConfig(), NOW, date="2031-04-15", target="schedule", action="delete", request="删除所有合成日程"))
    assert result.activities == []
    assert [item.id for item in result.shares] == [planned["task_id"]]
