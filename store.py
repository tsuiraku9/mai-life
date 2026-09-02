"""日程与分享任务持久化。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
import json

from .times import is_valid_hhmm


def _text(value: Any) -> str:
    return str(value or "").strip()


@dataclass
class Activity:
    """一条日程活动。"""

    id: str
    start: str
    end: str
    title: str
    notes: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class ShareItem:
    """一条分享任务。"""

    id: str
    time: str
    title: str
    hint: str = ""
    intent: str = ""
    stream_id: str = ""
    fired: bool = False
    fired_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LifeDocument:
    """某一天的生活记录。"""

    date: str
    generated_at: str
    activities: list[Activity] = field(default_factory=list)
    shares: list[ShareItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in self.shares:
            grouped.setdefault(item.stream_id, []).append(item.to_dict())
        return {
            "date": self.date,
            "generated_at": self.generated_at,
            "activities": [item.to_dict() for item in self.activities],
            "shares": [item.to_dict() for item in self.shares],
            "shares_by_stream": grouped,
        }


def shares_for_stream(document: LifeDocument, stream_id: str) -> list[ShareItem]:
    """取出某个聊天流的分享任务。"""

    stream_id = _text(stream_id)
    return [item for item in document.shares if item.stream_id == stream_id]


def replace_stream_shares(document: LifeDocument, stream_id: str, shares: list[ShareItem]) -> None:
    """替换某个聊天流的分享任务，保留其他聊天流。"""

    stream_id = _text(stream_id)
    kept = [item for item in document.shares if item.stream_id != stream_id]
    assigned: list[ShareItem] = []
    for item in shares:
        item.stream_id = stream_id
        assigned.append(item)
    document.shares = kept + assigned


def make_item_id(prefix: str, time_str: str, index: int) -> str:
    """按时间与序号生成稳定 ID。"""

    compact = _text(time_str).replace(":", "")
    return f"{prefix}_{compact}_{index:02d}"


def activity_from_dict(raw: dict[str, Any], index: int) -> Activity | None:
    """从字典构造活动。缺字段则返回 None。"""

    start = _text(raw.get("start"))
    end = _text(raw.get("end"))
    title = _text(raw.get("title"))
    if not title or not is_valid_hhmm(start) or not is_valid_hhmm(end):
        return None
    item_id = _text(raw.get("id")) or make_item_id("act", start, index)
    return Activity(
        id=item_id,
        start=start,
        end=end,
        title=title,
        notes=_text(raw.get("notes")),
    )


def share_from_dict(raw: dict[str, Any], index: int) -> ShareItem | None:
    """从字典构造分享任务。缺字段则返回 None。"""

    time_str = _text(raw.get("time"))
    title = _text(raw.get("title"))
    if not title or not is_valid_hhmm(time_str):
        return None
    item_id = _text(raw.get("id")) or make_item_id("share", time_str, index)
    fired = bool(raw.get("fired"))
    return ShareItem(
        id=item_id,
        time=time_str,
        title=title,
        hint=_text(raw.get("hint")),
        intent=_text(raw.get("intent")),
        stream_id=_text(raw.get("stream_id")),
        fired=fired,
        fired_at=_text(raw.get("fired_at")),
    )


def document_from_dict(raw: dict[str, Any]) -> LifeDocument | None:
    """从字典构造生活记录。"""

    date = _text(raw.get("date"))
    if not date:
        return None
    activities: list[Activity] = []
    for index, item in enumerate(raw.get("activities") or []):
        if not isinstance(item, dict):
            continue
        parsed = activity_from_dict(item, index)
        if parsed is not None:
            activities.append(parsed)
    shares: list[ShareItem] = []
    raw_by_stream = raw.get("shares_by_stream")
    if isinstance(raw_by_stream, dict):
        for stream_id, items in raw_by_stream.items():
            if not isinstance(items, list):
                continue
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                parsed = share_from_dict({**item, "stream_id": stream_id}, index)
                if parsed is not None:
                    shares.append(parsed)
    else:
        for index, item in enumerate(raw.get("shares") or []):
            if not isinstance(item, dict):
                continue
            parsed = share_from_dict(item, index)
            if parsed is not None:
                shares.append(parsed)
    return LifeDocument(
        date=date,
        generated_at=_text(raw.get("generated_at")),
        activities=activities,
        shares=shares,
    )


class LifeStore:
    """按日期把生活记录存到 data_dir。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, date: str) -> Path:
        return self.data_dir / f"life-{date}.json"

    def load(self, date: str) -> LifeDocument | None:
        path = self.path_for(date)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return document_from_dict(raw)

    def save(self, document: LifeDocument) -> None:
        path = self.path_for(document.date)
        path.write_text(
            json.dumps(document.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def exists(self, date: str) -> bool:
        return self.path_for(date).is_file()

    def mark_share_fired(self, date: str, share_id: str, fired_at: datetime) -> ShareItem | None:
        """标记分享任务已触发并写回。"""

        document = self.load(date)
        if document is None:
            return None
        target: ShareItem | None = None
        for item in document.shares:
            if item.id == share_id:
                item.fired = True
                item.fired_at = fired_at.strftime("%Y-%m-%dT%H:%M:%S")
                target = item
                break
        if target is None:
            return None
        self.save(document)
        return target
