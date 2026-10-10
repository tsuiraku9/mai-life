import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mailife.config_model import MaiLifeConfig, ShareStreamProfile
from mailife.plugin import MaiLifePlugin
from mailife.scheduler import LifeScheduler, share_due_status
from mailife.store import LifeDocument, LifeStore, ShareItem


NOW = datetime(2031, 4, 16, 0, 10)
OWNER = "2031-04-15"


def task(task_id="t1", stream="a", **fields):
    return ShareItem(
        task_id, "23:59", "合成到期意图", stream_id=stream,
        source=fields.pop("source", "planner"), due_at=fields.pop("due_at", "2031-04-15T23:59:00"),
        intent=fields.pop("intent", "履行合成提醒约定"), **fields,
    )


class FakeProactive:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.during = None

    async def trigger(self, **kwargs):
        self.calls.append(kwargs)
        if self.during:
            await self.during()
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class FakeChat:
    def __init__(self, streams=None):
        self.streams = [{"session_id": "a", "platform": "test", "user_id": "synthetic"}] if streams is None else streams

    async def get_all_streams(self, platform):
        return self.streams


def setup_plugin(tmp_path, tasks=None, results=None, streams=None):
    plugin = MaiLifePlugin()
    config = MaiLifeConfig()
    config.plugin.enabled = True
    config.generation.generate_on_load_if_missing = False
    config.share.stream_profiles = [ShareStreamProfile(stream="all")]
    plugin.set_plugin_config(config.model_dump(mode="python"))
    plugin._store = LifeStore(tmp_path)
    plugin._store.save(LifeDocument(OWNER, "", shares=[task()] if tasks is None else tasks))
    proactive = FakeProactive([{"success": True, "queued": True}] if results is None else results)
    plugin._ctx = SimpleNamespace(chat=FakeChat(streams), maisaka=SimpleNamespace(proactive=proactive))

    async def generate(force):
        raise AssertionError("离线巡检不得调用真实生成")

    plugin._scheduler = LifeScheduler(
        get_config=lambda: plugin.config, generate_today=generate, load_today=plugin._load_today,
        fire_share=plugin._fire_share, mark_share_fired=plugin._mark_share_fired, store=plugin._store,
    )
    return plugin, proactive


def tick(plugin, now=NOW):
    plugin._now = lambda: now

    async def run():
        with patch("mailife.scheduler.wall_clock_now", return_value=now):
            await plugin._scheduler._patrol_tick(plugin.config)
    asyncio.run(run())


def stored(plugin, task_id="t1", stream="a"):
    return plugin._store.find_task(stream, task_id)[1]


def test_old_hhmm_due_is_anchored_to_owner_across_midnight():
    old = ShareItem("old", "23:59", "旧分享")
    assert share_due_status(old, NOW, owner_date=OWNER, miss_tolerance_minutes=30) == "due"
    assert share_due_status(old, datetime(2031, 4, 16, 0, 29), owner_date=OWNER, miss_tolerance_minutes=30) == "due"
    assert share_due_status(old, datetime(2031, 4, 16, 0, 29, 1), owner_date=OWNER, miss_tolerance_minutes=30) == "missed"


def test_patrol_previous_owner_when_today_document_missing(tmp_path):
    plugin, proactive = setup_plugin(tmp_path)
    assert plugin._store.load("2031-04-16") is None
    tick(plugin)
    assert len(proactive.calls) == 1
    assert proactive.calls[0]["reason"] == "mai_life_task"
    assert proactive.calls[0]["intent"] == "履行合成提醒约定"
    assert proactive.calls[0]["metadata"]["owner_date"] == OWNER
    assert stored(plugin).state == "submitted"
    assert stored(plugin).fired is True
    tick(plugin)
    assert len(proactive.calls) == 1


def test_failure_retries_pending_then_submitted(tmp_path):
    plugin, proactive = setup_plugin(tmp_path, results=[{"success": False, "error": "synthetic rejection"}, {"success": True}])
    tick(plugin)
    assert stored(plugin).state == "pending"
    assert stored(plugin).state_reason == "host_rejected"
    tick(plugin, NOW + timedelta(minutes=1))
    assert stored(plugin).state == "submitted"
    assert len(proactive.calls) == 2


def test_expired_failure_skipped_no_late_burst(tmp_path):
    plugin, proactive = setup_plugin(tmp_path, results=[{"success": False}])
    tick(plugin)
    tick(plugin, NOW + timedelta(minutes=20))
    assert stored(plugin).state == "skipped"
    assert stored(plugin).state_reason == "missed_tolerance"
    assert len(proactive.calls) == 1


def test_same_stream_cooldown_delays_other_task_not_discards_it(tmp_path):
    plugin, proactive = setup_plugin(tmp_path, tasks=[task("t1"), task("t2", intent="第二个不同意图")], results=[{"success": True}, {"success": True}])
    tick(plugin)
    assert stored(plugin, "t1").state == "submitted"
    assert stored(plugin, "t2").state == "pending"
    tick(plugin, NOW + timedelta(minutes=2))
    assert len(proactive.calls) == 1
    tick(plugin, NOW + timedelta(minutes=3))
    assert stored(plugin, "t2").state == "submitted"
    assert len(proactive.calls) == 2


def test_other_stream_not_affected_by_cooldown_or_id_collision(tmp_path):
    streams = [{"session_id": stream, "platform": "test", "user_id": stream} for stream in ["a", "b"]]
    plugin, proactive = setup_plugin(tmp_path, tasks=[task(stream="a"), task(stream="b")], streams=streams, results=[{"success": True}, {"success": True}])
    tick(plugin)
    assert len(proactive.calls) == 2
    assert stored(plugin, stream="a").state == "submitted"
    assert stored(plugin, stream="b").state == "submitted"


@pytest.mark.parametrize("result", [TimeoutError("synthetic timeout"), None, {"queued": True}, "unknown"])
def test_unknown_result_not_claimed_success_or_blindly_retried(tmp_path, result):
    plugin, proactive = setup_plugin(tmp_path, results=[result])
    tick(plugin)
    assert stored(plugin).state == "consumed"
    assert stored(plugin).state_reason == "submission_result_unknown"
    tick(plugin)
    assert len(proactive.calls) == 1


def test_unknown_result_conservatively_occupies_cooldown_and_restart(tmp_path):
    plugin, proactive = setup_plugin(tmp_path, tasks=[task("t1"), task("t2", intent="第二个意图")], results=[TimeoutError("synthetic lost receipt"), {"success": True}])
    tick(plugin)
    assert stored(plugin, "t1").state_reason == "submission_result_unknown"
    assert stored(plugin, "t2").state == "pending"
    assert len(proactive.calls) == 1
    plugin._store = LifeStore(tmp_path)
    plugin._scheduler = LifeScheduler(
        get_config=lambda: plugin.config, generate_today=plugin._scheduler._generate_today,
        load_today=plugin._load_today, fire_share=plugin._fire_share,
        mark_share_fired=plugin._mark_share_fired, store=plugin._store,
    )
    tick(plugin, NOW + timedelta(minutes=1))
    assert len(proactive.calls) == 1
    tick(plugin, NOW + timedelta(minutes=3))
    assert len(proactive.calls) == 2
    assert stored(plugin, "t2").state == "submitted"


def test_long_discovery_rechecks_tolerance_before_claim(tmp_path):
    plugin, proactive = setup_plugin(tmp_path)
    clock = [NOW]
    plugin._now = lambda: clock[0]

    async def slow_discovery():
        clock[0] = NOW + timedelta(minutes=20)
        return [{"session_id": "a"}]

    plugin._list_share_streams = slow_discovery
    async def run():
        with patch("mailife.scheduler.wall_clock_now", side_effect=lambda *args: clock[0]):
            await plugin._scheduler._patrol_tick(plugin.config)
    asyncio.run(run())
    assert proactive.calls == []
    assert stored(plugin).state == "skipped"


def test_long_rpc_updates_clock_for_next_candidate_and_cooldown(tmp_path):
    streams = [{"session_id": stream} for stream in ["a", "b"]]
    plugin, proactive = setup_plugin(tmp_path, tasks=[task("t1", due_at="2031-04-16T00:09:00"), task("t2", stream="b", due_at="2031-04-16T00:09:30")], streams=streams)
    clock = [NOW]
    plugin._now = lambda: clock[0]
    async def advance():
        clock[0] = NOW + timedelta(minutes=31)
    proactive.during = advance
    async def run():
        with patch("mailife.scheduler.wall_clock_now", side_effect=lambda *args: clock[0]):
            await plugin._scheduler._patrol_tick(plugin.config)
    asyncio.run(run())
    assert len(proactive.calls) == 1
    assert stored(plugin, "t2", stream="b").state == "skipped"
    assert stored(plugin, "t1").fired_at == "2031-04-16T00:41:00"
    assert not plugin._scheduler.can_wake_stream("a", clock[0] + timedelta(minutes=1), 180)


def test_submission_blocks_mutation_while_await_and_keeps_new_task(tmp_path):
    plugin, proactive = setup_plugin(tmp_path)
    rejected = []

    async def during():
        result = await plugin.handle_modify(target="share", action="delete", task_id="t1", stream_id="a")
        rejected.append(result)
        added = await plugin.handle_modify(target="share", action="add", title="新任务", intent="新意图", delay_minutes=20, stream_id="a")
        assert added["success"] is True

    plugin._now = lambda: NOW
    proactive.during = during
    tick(plugin)
    assert rejected[0]["success"] is False
    assert stored(plugin).state == "submitted"
    assert len(plugin._store.list_tasks()) == 2
    assert len(plugin._store.list_tasks(pending_only=True)) == 1


def test_cancel_during_stream_discovery_prevents_stale_submission(tmp_path):
    plugin, proactive = setup_plugin(tmp_path)

    async def cancel_then_list():
        plugin._store.mark_task_state(OWNER, "a", "t1", "cancelled", "synthetic", NOW)
        return [{"session_id": "a"}]

    plugin._list_share_streams = cancel_then_list
    tick(plugin)
    assert proactive.calls == []
    assert stored(plugin).state == "cancelled"


def test_missing_stream_kept_pending_until_tolerance(tmp_path):
    plugin, proactive = setup_plugin(tmp_path, streams=[])
    tick(plugin)
    assert stored(plugin).state == "pending"
    tick(plugin, NOW + timedelta(minutes=20))
    assert stored(plugin).state == "skipped"
    assert proactive.calls == []


def test_unbound_legacy_not_broadcast_to_every_stream(tmp_path):
    plugin, proactive = setup_plugin(tmp_path, tasks=[task(stream="", source="legacy", due_at="")])
    tick(plugin)
    assert stored(plugin, stream="").state_reason == "legacy_stream_unknown"
    assert proactive.calls == []


@pytest.mark.parametrize("section,field", [("plugin", "enabled"), ("share", "enabled"), ("share", "wake_planner")])
def test_off_switches_do_not_submit(tmp_path, section, field):
    plugin, proactive = setup_plugin(tmp_path)
    config = plugin.config.model_dump(mode="python")
    config[section][field] = False
    plugin.set_plugin_config(config)
    tick(plugin)
    assert stored(plugin).state == "pending"
    assert proactive.calls == []


def test_empty_allowlist_never_wakes(tmp_path):
    plugin, proactive = setup_plugin(tmp_path)
    config = plugin.config.model_dump(mode="python")
    config["share"]["stream_profiles"] = []
    plugin.set_plugin_config(config)
    tick(plugin)
    assert proactive.calls == []


def test_submitted_persists_restart_and_future_owner_is_detected(tmp_path):
    plugin, proactive = setup_plugin(tmp_path, tasks=[task(), task("future", due_at="2031-04-19T12:00:00")])
    tick(plugin)
    restarted = LifeStore(tmp_path)
    assert restarted.find_task("a", "t1")[1].state == "submitted"
    assert [item.id for _, item in restarted.list_tasks(pending_only=True)] == ["future"]


def test_restart_restores_same_stream_cooldown(tmp_path):
    plugin, proactive = setup_plugin(tmp_path, tasks=[task("t1"), task("t2", intent="第二个不同意图")], results=[{"success": True}])
    tick(plugin)
    plugin._store = LifeStore(tmp_path)
    plugin._scheduler = LifeScheduler(
        get_config=lambda: plugin.config, generate_today=plugin._scheduler._generate_today,
        load_today=plugin._load_today, fire_share=plugin._fire_share,
        mark_share_fired=plugin._mark_share_fired, store=plugin._store,
    )
    tick(plugin, NOW + timedelta(minutes=1))
    assert stored(plugin, "t2").state == "pending"
    assert len(proactive.calls) == 1


def test_backfill_does_not_treat_planner_only_as_generated(tmp_path):
    plugin, _ = setup_plugin(tmp_path)
    config = plugin.config.model_copy(deep=True)
    config.schedule.enabled = False
    document = LifeDocument("2031-04-15", "", shares=[task()])
    assert plugin._scheduler._needs_backfill(config, document)
    document.generated_streams = ["a"]
    assert not plugin._scheduler._needs_backfill(config, document)


def test_explicit_intent_not_replaced_by_optional_share_template(tmp_path):
    plugin, proactive = setup_plugin(tmp_path)
    tick(plugin)
    assert "可以不说" not in proactive.calls[0]["intent"]


def test_generated_without_intent_keeps_existing_optional_share_template(tmp_path):
    plugin, proactive = setup_plugin(tmp_path, tasks=[task(source="generated", intent="")])
    tick(plugin)
    assert proactive.calls[0]["reason"] == "mai_life_share"
    assert "可以不说" in proactive.calls[0]["intent"]
