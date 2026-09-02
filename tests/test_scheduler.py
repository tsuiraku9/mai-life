from datetime import datetime

from mailife.config_model import MaiLifeConfig
from mailife.scheduler import LifeScheduler, share_due_status
from mailife.store import Activity, LifeDocument, ShareItem


def test_share_due_status_window() -> None:
    item = ShareItem(id="s1", time="12:00", title="午饭")
    assert share_due_status(item, datetime(2026, 8, 29, 11, 59), miss_tolerance_minutes=30) == "pending"
    assert share_due_status(item, datetime(2026, 8, 29, 12, 10), miss_tolerance_minutes=30) == "due"
    assert share_due_status(item, datetime(2026, 8, 29, 13, 00), miss_tolerance_minutes=30) == "missed"
    item.fired = True
    assert share_due_status(item, datetime(2026, 8, 29, 12, 10), miss_tolerance_minutes=30) == "fired"


def test_needs_backfill_when_shares_missing() -> None:
    async def _unused_generate(force: bool):
        del force
        return None

    async def _unused_fire(item, document, now):
        del item, document, now

    scheduler = LifeScheduler(
        get_config=MaiLifeConfig,
        generate_today=_unused_generate,
        load_today=lambda now: None,
        fire_share=_unused_fire,
        mark_share_fired=lambda *args: None,
    )
    config = MaiLifeConfig()
    empty = LifeDocument(date="2026-09-02", generated_at="")
    empty.activities = [Activity("a1", "08:00", "09:00", "早餐")]
    assert scheduler._needs_backfill(config, None) is True
    assert scheduler._needs_backfill(config, empty) is True
    empty.shares = [ShareItem(id="s1", time="12:00", title="午饭")]
    assert scheduler._needs_backfill(config, empty) is False
