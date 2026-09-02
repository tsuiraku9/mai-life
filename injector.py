"""规划器 messages 注入。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from .prompts import DEFAULT_INJECT_TEMPLATE, choose_template, render_template
from .store import Activity, LifeDocument
from .times import activity_interval, weekday_cn


def format_activity_line(activity: Activity, *, cross_day: bool = False) -> str:
    """把一条活动格式化成注入文本。"""

    suffix = "(次日)" if cross_day else ""
    line = f"- {activity.start}-{activity.end}{suffix} {activity.title}"
    if activity.notes:
        line += f" | {activity.notes}"
    return line


def select_recent_activities(
    activities: list[Activity],
    now: datetime,
    *,
    count: int,
    window_minutes: int,
    schedule_date: datetime | None = None,
) -> list[tuple[Activity, tuple[datetime, datetime]]]:
    """选取当前时间附近最近的若干活动。窗口内没有则取全局最近。"""

    if count <= 0 or not activities:
        return []
    day = schedule_date or now.replace(hour=0, minute=0, second=0, microsecond=0)
    scored: list[tuple[tuple[int, float], Activity, tuple[datetime, datetime]]] = []
    for activity in activities:
        try:
            interval = activity_interval(activity.start, activity.end, day)
        except ValueError:
            continue
        start_dt, end_dt = interval
        ongoing = start_dt <= now <= end_dt
        if ongoing:
            distance = 0.0
        elif now < start_dt:
            distance = (start_dt - now).total_seconds()
        else:
            distance = (now - end_dt).total_seconds()
        scored.append(((0 if ongoing else 1, distance), activity, interval))
    if not scored:
        return []
    scored.sort(key=lambda item: item[0])
    window = timedelta(minutes=max(0, int(window_minutes)))
    in_window = [
        (activity, interval)
        for _score, activity, interval in scored
        if interval[0] - window <= now <= interval[1] + window
    ]
    selected = in_window[:count] if in_window else [(item[1], item[2]) for item in scored[:count]]
    return selected


def build_recent_schedule_text(
    document: LifeDocument | None,
    now: datetime,
    *,
    count: int,
    window_minutes: int,
) -> str:
    """生成注入用的最近日程正文。没有活动时返回空字符串。"""

    if document is None:
        return ""
    try:
        day = datetime.strptime(document.date, "%Y-%m-%d")
    except ValueError:
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    selected = select_recent_activities(
        document.activities,
        now,
        count=count,
        window_minutes=window_minutes,
        schedule_date=day,
    )
    if not selected:
        return ""
    lines = []
    for activity, (start_dt, end_dt) in selected:
        lines.append(format_activity_line(activity, cross_day=end_dt.date() > start_dt.date()))
    return "\n".join(lines)


def build_inject_text(
    document: LifeDocument | None,
    now: datetime,
    *,
    count: int,
    window_minutes: int,
    template: str,
    extra_values: dict[str, object] | None = None,
) -> str:
    """渲染注入规划器的完整文本。"""

    recent = build_recent_schedule_text(
        document,
        now,
        count=count,
        window_minutes=window_minutes,
    )
    if not recent:
        return ""
    values: dict[str, object] = {
        "date": now.strftime("%Y-%m-%d"),
        "weekday": weekday_cn(now),
        "now": now.strftime("%Y-%m-%d %H:%M"),
        "recent_schedule": recent,
    }
    if extra_values:
        values.update(extra_values)
    return render_template(choose_template(template, DEFAULT_INJECT_TEMPLATE), values).strip()


def inject_into_messages(messages: list[Any], text: str) -> list[Any]:
    """在最后一条 system 消息后插入注入文本。没有 system 则插到最前。"""

    content = str(text or "").strip()
    if not content:
        return list(messages)
    inserted = {"role": "system", "content": content}
    result = list(messages)
    last_system = -1
    for index, item in enumerate(result):
        if isinstance(item, dict) and str(item.get("role") or "") == "system":
            last_system = index
    if last_system >= 0:
        result.insert(last_system + 1, inserted)
    else:
        result.insert(0, inserted)
    return result


def build_system_item(text: str, *, now: datetime | None = None) -> dict[str, Any]:
    """构造 MaiBot 1.2+ 规划器 Hook 使用的 SystemMessageItem。"""

    stamp = now or datetime.now()
    return {
        "item_type": "SystemMessageItem",
        "meta": {
            "item_id": uuid4().hex,
            "logical_turn_id": None,
            "timestamp": stamp.isoformat(),
        },
        "parts": [{"type": "text", "text": str(text)}],
    }


def inject_into_items(items: list[Any], text: str, *, now: datetime | None = None) -> list[Any]:
    """在最后一条 SystemMessageItem 后插入日程 Item。没有则插到最前。"""

    content = str(text or "").strip()
    if not content:
        return list(items)
    inserted = build_system_item(content, now=now)
    result = list(items)
    last_system = -1
    for index, item in enumerate(result):
        if isinstance(item, dict) and str(item.get("item_type") or "") == "SystemMessageItem":
            last_system = index
    if last_system >= 0:
        result.insert(last_system + 1, inserted)
    else:
        result.insert(0, inserted)
    return result
