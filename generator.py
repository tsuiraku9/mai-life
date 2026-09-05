"""日程与分享任务的 LLM 生成、解析与修改。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
import json
import logging
import re

from .config_model import DEFAULT_SHARE_COUNT_MAX, DEFAULT_SHARE_COUNT_MIN, MaiLifeConfig, resolve_share_profile
from .prompts import (
    DEFAULT_MODIFY_SYSTEM,
    DEFAULT_MODIFY_USER,
    DEFAULT_SCHEDULE_SYSTEM,
    DEFAULT_SCHEDULE_USER,
    DEFAULT_SHARE_SYSTEM,
    DEFAULT_SHARE_USER,
    choose_template,
    render_template,
)
from .store import (
    Activity,
    LifeDocument,
    LifeStore,
    ShareItem,
    activity_from_dict,
    replace_stream_shares,
    share_from_dict,
    shares_for_stream,
)
from .streams import format_stream_info, normalize_text, stream_session_id
from .times import is_cross_day, is_in_time_window, is_valid_hhmm, weekday_cn

logger = logging.getLogger("mailife.mai-life")

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n\s*```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


def parse_json_object(text: str) -> dict[str, Any] | None:
    """从模型输出中提取 JSON 对象。"""

    raw = str(text or "").strip()
    if not raw:
        return None
    direct = _try_load_json(raw)
    if direct is not None:
        return direct
    for match in _JSON_BLOCK_RE.finditer(raw):
        parsed = _try_load_json(match.group(1))
        if parsed is not None:
            return parsed
    match = _JSON_OBJECT_RE.search(raw)
    if match:
        return _try_load_json(match.group(0))
    return None


def _try_load_json(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def parse_activities(data: dict[str, Any]) -> list[Activity]:
    """解析 activities 列表，跳过非法项。"""

    raw_items = data.get("activities")
    if not isinstance(raw_items, list):
        return []
    result: list[Activity] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        parsed = activity_from_dict(item, index)
        if parsed is not None:
            result.append(parsed)
    return result


def parse_shares(data: dict[str, Any]) -> list[ShareItem]:
    """解析 shares 列表，跳过非法项。"""

    raw_items = data.get("shares")
    if not isinstance(raw_items, list):
        return []
    result: list[ShareItem] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        parsed = share_from_dict(item, index)
        if parsed is not None:
            result.append(parsed)
    return result


def drop_shares_in_silence(
    shares: list[ShareItem],
    silence_start: str,
    silence_end: str,
) -> list[ShareItem]:
    """丢掉落在静默时段内的分享任务。开始与结束相同或时间非法则不过滤。"""

    if not is_valid_hhmm(silence_start) or not is_valid_hhmm(silence_end):
        return shares
    if silence_start == silence_end:
        return shares
    kept: list[ShareItem] = []
    for item in shares:
        if is_valid_hhmm(item.time) and is_in_time_window(silence_start, silence_end, item.time):
            logger.info("丢弃静默时段内的分享任务: %s %s", item.time, item.title)
            continue
        kept.append(item)
    return kept


def format_activities(activities: list[Activity]) -> str:
    """把活动列表格式化成可读文本。"""

    if not activities:
        return "（无）"
    lines = []
    for item in activities:
        suffix = "（跨天）" if is_cross_day(item.start, item.end) else ""
        line = f"- {item.start}-{item.end}{suffix} {item.title}"
        if item.notes:
            line += f" | {item.notes}"
        lines.append(line)
    return "\n".join(lines)


def format_shares(shares: list[ShareItem]) -> str:
    """把分享任务格式化成可读文本。"""

    if not shares:
        return "（无）"
    lines = []
    for item in shares:
        status = "已提醒" if item.fired else "未提醒"
        line = f"- {item.time} {item.title} [{status}]"
        if item.hint:
            line += f" | {item.hint}"
        lines.append(line)
    return "\n".join(lines)


def format_share_stream_label(stream: dict[str, Any] | None, stream_id: str) -> str:
    """管理员查看用的聊天流标题。"""

    session_id = stream_session_id(stream) if stream else normalize_text(stream_id)
    if not session_id:
        return "未绑定聊天流"
    if not stream:
        return f"聊天流 {session_id}"
    platform = normalize_text(stream.get("platform"))
    chat_type = normalize_text(stream.get("chat_type"))
    group_id = normalize_text(stream.get("group_id"))
    user_id = normalize_text(stream.get("user_id"))
    bits = [f"聊天流 {session_id}"]
    if platform:
        bits.append(platform)
    if chat_type == "group" and group_id:
        bits.append(f"群 {group_id}")
    elif user_id:
        bits.append(f"用户 {user_id}")
    elif chat_type:
        bits.append(chat_type)
    return " | ".join(bits)


def format_shares_by_stream(
    shares: list[ShareItem],
    streams: list[dict[str, Any]] | None = None,
) -> str:
    """按聊天流分组展示分享任务。"""

    if not shares:
        return "（无）"
    grouped: dict[str, list[ShareItem]] = {}
    for item in shares:
        grouped.setdefault(normalize_text(item.stream_id), []).append(item)
    labels = {
        stream_session_id(stream): format_share_stream_label(stream, stream_session_id(stream))
        for stream in streams or []
        if stream_session_id(stream)
    }
    parts: list[str] = []
    for stream_id in sorted(grouped, key=lambda key: (key == "", key)):
        header = labels.get(stream_id) if stream_id else "未绑定聊天流"
        if not header:
            header = format_share_stream_label(None, stream_id)
        parts.append(f"{header}：\n{format_shares(grouped[stream_id])}")
    return "\n\n".join(parts)


MAX_RECENT_SCHEDULE_DAYS = 14


def yesterday_tail_text(document: LifeDocument | None) -> str:
    """昨日尾部活动，供今天衔接。"""

    if document is None or not document.activities:
        return "（无昨日日程）"
    tail = document.activities[-2:] if len(document.activities) >= 2 else document.activities
    lines = [f"昨日日期：{document.date}"]
    for item in tail:
        suffix = "（跨天）" if is_cross_day(item.start, item.end) else ""
        lines.append(f"- {item.start}-{item.end}{suffix} {item.title}")
    last = document.activities[-1]
    if is_cross_day(last.start, last.end):
        lines.append(f"昨日最后一项 {last.start}-{last.end} {last.title} 会跨到今天，今天不要再写它。")
    return "\n".join(lines)


def load_recent_schedule_documents(
    load_date: Callable[[str], LifeDocument | None],
    now: datetime,
    days: int,
) -> list[LifeDocument]:
    """从昨天往前取出有日程的记录，最远不超过 MAX_RECENT_SCHEDULE_DAYS 天。"""

    count = max(0, min(MAX_RECENT_SCHEDULE_DAYS, int(days)))
    documents: list[LifeDocument] = []
    for offset in range(count, 0, -1):
        date = (now - timedelta(days=offset)).strftime("%Y-%m-%d")
        document = load_date(date)
        if document is not None and document.activities:
            documents.append(document)
    return documents


def recent_days_schedule_text(documents: list[LifeDocument]) -> str:
    """把近几日完整日程写成给模型看的文本，从早到晚排列。"""

    if not documents:
        return "（无近几日日程）"
    parts: list[str] = []
    for document in documents:
        try:
            day = datetime.strptime(document.date, "%Y-%m-%d")
            heading = f"{document.date} 星期{weekday_cn(day)}"
        except ValueError:
            heading = document.date
        parts.append(f"{heading}：\n{format_activities(document.activities)}")
    return "\n\n".join(parts)


def attach_recent_days_block(template: str, rendered: str, recent_text: str) -> str:
    """旧提示词没有近几日占位符时，把近几日日程插到生成要求前面。"""

    if "{recent_days_schedule}" in template:
        return rendered
    text = str(recent_text or "").strip()
    if not text or text.startswith("（"):
        return rendered
    block = f"【近几日日程】\n以下是最近几天的安排，今天不要生成几乎相同的日程。\n{text}\n\n"
    marker = "【生成要求】"
    if marker in rendered:
        return rendered.replace(marker, block + marker, 1)
    return f"{rendered.rstrip()}\n\n{block}".rstrip() + "\n"


class LifeGenerator:
    """调用 Host LLM 生成或修改生活记录。"""

    def __init__(self, ctx: Any, store: LifeStore) -> None:
        self.ctx = ctx
        self.store = store

    async def generate_today(
        self,
        config: MaiLifeConfig,
        now: datetime,
        *,
        force: bool = False,
        persona: str = "",
        history: str = "",
        knowledge: str = "",
        bot_nickname: str = "",
        share_streams: list[dict[str, Any]] | None = None,
        history_by_stream: dict[str, str] | None = None,
    ) -> LifeDocument | None:
        """生成今日全局日程，并为每个聊天流单独生成分享任务。"""

        date = now.strftime("%Y-%m-%d")
        existing = self.store.load(date)
        if not config.schedule.enabled and not config.share.enabled:
            logger.info("日程与分享均未启用，跳过生成 date=%s", date)
            return existing

        values = self._base_values(
            config,
            now,
            persona=persona,
            history=history,
            knowledge=knowledge,
            bot_nickname=bot_nickname,
        )
        yesterday = self.store.load((now - timedelta(days=1)).strftime("%Y-%m-%d"))
        values["yesterday_tail"] = yesterday_tail_text(yesterday)
        recent_days = max(0, int(config.schedule.recent_days))
        values["recent_days"] = str(recent_days)
        if recent_days > 0:
            recent_docs = load_recent_schedule_documents(self.store.load, now, recent_days)
            values["recent_days_schedule"] = recent_days_schedule_text(recent_docs)
        else:
            values["recent_days_schedule"] = "（未附带近几日日程）"

        document = existing or LifeDocument(
            date=date,
            generated_at=now.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        need_schedule = config.schedule.enabled and (force or not document.activities)
        if need_schedule:
            activities = await self._generate_activities(config, values)
            if not activities:
                logger.error("日程生成失败：模型未返回可用活动 date=%s", date)
                if existing is None:
                    return None
            else:
                document.activities = activities
                document.generated_at = now.strftime("%Y-%m-%dT%H:%M:%S")
        values["schedule_json"] = json.dumps(
            {"activities": [item.to_dict() for item in document.activities]},
            ensure_ascii=False,
            indent=2,
        )

        if config.share.enabled:
            await self._generate_shares_for_streams(
                config,
                values,
                document,
                share_streams or [],
                history_by_stream or {},
                force=force,
            )

        if not document.activities and not document.shares:
            return existing
        self.store.save(document)
        return document

    async def modify(
        self,
        config: MaiLifeConfig,
        now: datetime,
        *,
        date: str,
        target: str,
        action: str,
        request: str,
        persona: str = "",
        bot_nickname: str = "",
        stream_id: str = "",
        stream_info: str = "",
    ) -> LifeDocument:
        """按自然语言或 JSON 修改指定日期的日程或分享任务。"""

        date = date.strip() or now.strftime("%Y-%m-%d")
        document = self.store.load(date) or LifeDocument(
            date=date,
            generated_at=now.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        values = self._base_values(
            config,
            now,
            persona=persona,
            bot_nickname=bot_nickname,
        )
        values.update(
            {
                "date": date,
                "target": target,
                "action": action,
                "user_request": request,
                "schedule_json": json.dumps(
                    {"activities": [item.to_dict() for item in document.activities]},
                    ensure_ascii=False,
                    indent=2,
                ),
                "share_json": json.dumps(
                    {
                        "shares": [
                            item.to_dict()
                            for item in (
                                shares_for_stream(document, stream_id) if stream_id else document.shares
                            )
                        ]
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                "stream_id": stream_id,
                "stream_info": stream_info or stream_id or "（无）",
            }
        )
        system_prompt = choose_template(config.prompts.modify_system, DEFAULT_MODIFY_SYSTEM)
        user_prompt = render_template(
            choose_template(config.prompts.modify_user, DEFAULT_MODIFY_USER),
            values,
        )
        data = await self._ask_json(config, system_prompt, user_prompt)
        if data is None:
            raise RuntimeError("修改失败：模型没有返回合法 JSON")

        if target == "share":
            shares = parse_shares(data)
            if not shares and action != "delete":
                raise RuntimeError("修改失败：没有解析到合法的分享任务")
            stream_id = normalize_text(values.get("stream_id"))
            if stream_id:
                replace_stream_shares(document, stream_id, shares)
            else:
                document.shares = shares
        else:
            activities = parse_activities(data)
            if not activities and action != "delete":
                raise RuntimeError("修改失败：没有解析到合法的日程活动")
            document.activities = activities
        self.store.save(document)
        return document

    def _base_values(
        self,
        config: MaiLifeConfig,
        now: datetime,
        *,
        persona: str = "",
        history: str = "",
        knowledge: str = "",
        bot_nickname: str = "",
    ) -> dict[str, str]:
        return {
            "date": now.strftime("%Y-%m-%d"),
            "weekday": weekday_cn(now),
            "now": now.strftime("%Y-%m-%d %H:%M"),
            "timezone": config.generation.timezone,
            "bot_nickname": bot_nickname or "麦麦",
            "persona": persona or "（无人设）",
            "history": history or "（无）",
            "knowledge": knowledge or "（无）",
            "yesterday_tail": "（无）",
            "recent_days": "0",
            "recent_days_schedule": "（无）",
            "schedule_json": "{}",
            "share_json": "{}",
            "recent_schedule": "（无）",
            "user_request": "",
            "wake_time": config.schedule.wake_time,
            "sleep_time": config.schedule.sleep_time,
            "activity_count_min": str(config.schedule.activity_count_min),
            "activity_count_max": str(config.schedule.activity_count_max),
            "share_count_min": str(DEFAULT_SHARE_COUNT_MIN),
            "share_count_max": str(DEFAULT_SHARE_COUNT_MAX),
            "silence_start": config.share.silence_start,
            "silence_end": config.share.silence_end,
            "stream_info": "",
            "share_item": "",
            "target": "",
            "action": "",
            "extra_prompt": "（无）",
            "stream_id": "",
        }

    async def _generate_activities(self, config: MaiLifeConfig, values: dict[str, str]) -> list[Activity]:
        system_prompt = choose_template(config.prompts.schedule_system, DEFAULT_SCHEDULE_SYSTEM)
        template = choose_template(config.prompts.schedule_user, DEFAULT_SCHEDULE_USER)
        user_prompt = attach_recent_days_block(
            template,
            render_template(template, values),
            values.get("recent_days_schedule", ""),
        )
        data = await self._ask_json(config, system_prompt, user_prompt, retries=1)
        if data is None:
            return []
        return parse_activities(data)

    async def _generate_shares_for_streams(
        self,
        config: MaiLifeConfig,
        values: dict[str, str],
        document: LifeDocument,
        share_streams: list[dict[str, Any]],
        history_by_stream: dict[str, str],
        *,
        force: bool,
    ) -> None:
        if not share_streams:
            logger.info("分享白名单没有匹配到聊天流，跳过分享生成")
            return
        for stream in share_streams:
            session_id = stream_session_id(stream)
            if not session_id:
                continue
            if not force and shares_for_stream(document, session_id):
                continue
            count_min, count_max, extra_prompt = resolve_share_profile(config.share, stream)
            stream_values = dict(values)
            stream_values.update(
                {
                    "share_count_min": str(count_min),
                    "share_count_max": str(count_max),
                    "extra_prompt": extra_prompt or "（无）",
                    "history": history_by_stream.get(session_id) or values.get("history") or "（无）",
                    "stream_info": format_stream_info(stream),
                    "stream_id": session_id,
                    "share_json": json.dumps(
                        {"shares": [item.to_dict() for item in shares_for_stream(document, session_id)]},
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
            )
            shares = await self._generate_shares(config, stream_values)
            if not shares:
                logger.error("聊天流分享任务生成失败: session=%s", session_id)
                continue
            for item in shares:
                item.stream_id = session_id
            replace_stream_shares(document, session_id, shares)
            logger.info("已生成聊天流分享任务: session=%s count=%s", session_id, len(shares))

    async def _generate_shares(self, config: MaiLifeConfig, values: dict[str, str]) -> list[ShareItem]:
        system_prompt = choose_template(config.prompts.share_system, DEFAULT_SHARE_SYSTEM)
        user_prompt = render_template(
            choose_template(config.prompts.share_user, DEFAULT_SHARE_USER),
            values,
        )
        data = await self._ask_json(config, system_prompt, user_prompt, retries=1)
        if data is None:
            return []
        shares = parse_shares(data)
        kept = drop_shares_in_silence(
            shares,
            config.share.silence_start,
            config.share.silence_end,
        )
        if shares and not kept:
            logger.warning("分享任务全部落在静默时段内，已全部丢弃")
        elif len(kept) < len(shares):
            logger.info("已丢弃 %s 条静默时段内的分享任务", len(shares) - len(kept))
        return kept

    async def _ask_json(
        self,
        config: MaiLifeConfig,
        system_prompt: str,
        user_prompt: str,
        retries: int = 0,
    ) -> dict[str, Any] | None:
        last_error = ""
        attempts = max(0, retries) + 1
        for attempt in range(attempts):
            prompt = user_prompt
            if last_error:
                prompt = f"{user_prompt}\n\n上次输出无法解析：{last_error}。请只输出合法 JSON。"
            result = await self._llm_generate(config, system_prompt, prompt)
            if not result.get("success"):
                last_error = normalize_text(result.get("error") or result.get("response") or "LLM 调用失败")
                logger.warning("LLM 调用失败 attempt=%s error=%s", attempt + 1, last_error)
                continue
            response = str(result.get("response") or "")
            data = parse_json_object(response)
            if data is not None:
                return data
            last_error = "不是 JSON 对象"
            logger.warning("LLM 输出无法解析为 JSON attempt=%s", attempt + 1)
        return None

    async def _llm_generate(
        self,
        config: MaiLifeConfig,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "prompt": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "model": config.generation.model,
        }
        if config.generation.temperature >= 0:
            payload["temperature"] = config.generation.temperature
        if config.generation.max_tokens > 0:
            payload["max_tokens"] = config.generation.max_tokens
        timeout_ms = max(1000, int(config.generation.llm_timeout_ms))
        try:
            result = await self.ctx.call_capability(
                "llm.generate",
                timeout_ms=timeout_ms,
                **payload,
            )
        except Exception as exc:
            logger.exception("llm.generate 调用异常")
            return {"success": False, "error": str(exc)}
        if isinstance(result, dict):
            return result
        return {"success": False, "error": "LLM 返回结果不是对象"}
