"""日程与分享任务的 LLM 生成、解析与修改。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
import json
import logging
import re

from .config_model import MaiLifeConfig, resolve_share_profile
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
from .times import is_cross_day, weekday_cn

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
            "schedule_json": "{}",
            "share_json": "{}",
            "recent_schedule": "（无）",
            "user_request": "",
            "wake_time": config.schedule.wake_time,
            "sleep_time": config.schedule.sleep_time,
            "activity_count_min": str(config.schedule.activity_count_min),
            "activity_count_max": str(config.schedule.activity_count_max),
            "share_count_min": str(config.share.count_min),
            "share_count_max": str(config.share.count_max),
            "stream_info": "",
            "share_item": "",
            "target": "",
            "action": "",
            "extra_prompt": "（无）",
            "stream_id": "",
        }

    async def _generate_activities(self, config: MaiLifeConfig, values: dict[str, str]) -> list[Activity]:
        system_prompt = choose_template(config.prompts.schedule_system, DEFAULT_SCHEDULE_SYSTEM)
        user_prompt = render_template(
            choose_template(config.prompts.schedule_user, DEFAULT_SCHEDULE_USER),
            values,
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
        return parse_shares(data)

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
