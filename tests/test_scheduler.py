from datetime import datetime

from mailife.scheduler import share_due_status
from mailife.store import ShareItem


def test_share_due_status_window() -> None:
    item = ShareItem(id="s1", time="12:00", title="午饭")
    assert share_due_status(item, datetime(2026, 8, 29, 11, 59), miss_tolerance_minutes=30) == "pending"
    assert share_due_status(item, datetime(2026, 8, 29, 12, 10), miss_tolerance_minutes=30) == "due"
    assert share_due_status(item, datetime(2026, 8, 29, 13, 00), miss_tolerance_minutes=30) == "missed"
    item.fired = True
    assert share_due_status(item, datetime(2026, 8, 29, 12, 10), miss_tolerance_minutes=30) == "fired"
