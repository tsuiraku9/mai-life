from datetime import datetime
from pathlib import Path
import tempfile

from mailife.store import LifeDocument, LifeStore, activity_from_dict, document_from_dict, share_from_dict


def test_parse_activity_and_share() -> None:
    activity = activity_from_dict({"start": "08:30", "end": "09:00", "title": "起床"}, 0)
    assert activity is not None
    assert activity.id.startswith("act_0830")
    assert activity_from_dict({"start": "25:00", "end": "09:00", "title": "坏"}, 0) is None
    share = share_from_dict({"time": "12:30", "title": "午饭"}, 1)
    assert share is not None
    assert share.id.startswith("share_1230")


def test_roundtrip() -> None:
    with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as raw:
        store = LifeStore(Path(raw))
        _roundtrip(store)


def _roundtrip(store: LifeStore) -> None:
    raw = {
        "date": "2026-08-29",
        "generated_at": "2026-08-29T01:30:00",
        "activities": [{"start": "08:30", "end": "09:00", "title": "起床", "mood": "困", "notes": "闹钟"}],
        "shares": [{"time": "12:30", "title": "午饭", "hint": "提一句"}],
    }
    document = document_from_dict(raw)
    assert document is not None
    store.save(document)
    loaded = store.load("2026-08-29")
    assert loaded is not None
    assert loaded.activities[0].title == "起床"
    assert loaded.activities[0].notes == "闹钟"
    assert "mood" not in loaded.activities[0].to_dict()
    assert loaded.shares[0].title == "午饭"
    grouped = loaded.to_dict()["shares_by_stream"]
    assert "" in grouped or loaded.shares[0].stream_id in grouped
    store.mark_share_fired("2026-08-29", loaded.shares[0].id, datetime(2026, 8, 29, 12, 31))
    again = store.load("2026-08-29")
    assert again is not None
    assert again.shares[0].fired is True


def test_missing_file_returns_none() -> None:
    with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as raw:
        store = LifeStore(Path(raw))
        assert store.load("2026-01-01") is None
    empty = LifeDocument(date="2026-01-01", generated_at="")
    assert empty.activities == []
