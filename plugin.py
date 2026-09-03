"""麦麦生活插件入口。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
import asyncio
import json
import logging

from maibot_sdk import (
    CONFIG_RELOAD_SCOPE_SELF,
    Command,
    HookHandler,
    MaiBotPlugin,
    ON_BOT_CONFIG_RELOAD,
    Tool,
)
from maibot_sdk.types import (
    ErrorPolicy,
    HookMode,
    HookOrder,
    ToolParameterInfo,
    ToolParamType,
)

from .config_model import MaiLifeConfig, share_allowlist
from .generator import LifeGenerator, format_activities, format_shares
from .injector import build_inject_text, inject_into_items, inject_into_messages
from .prompts import DEFAULT_WAKE_INTENT_TEMPLATE, choose_template, render_template
from .scheduler import LifeScheduler
from .store import LifeDocument, LifeStore, ShareItem, shares_for_stream
from .streams import (
    filter_streams,
    format_stream_info,
    normalize_text,
    session_allowed,
    stream_session_id,
)
from .times import wall_clock_now, weekday_cn

logger = logging.getLogger("mailife.mai-life")

_QUERY_DESCRIPTION = (
    "查询角色今天或指定日期的生活日程，以及计划在聊天中分享的事情和提醒时间。"
    "当用户问在干什么、今天安排、待会要做什么、有没有要说的事时使用。"
)
_MODIFY_DESCRIPTION = (
    "修改角色的生活日程或分享提醒时间。"
    "当用户邀请角色改计划、加上一件事、取消分享、改提醒时间时使用。"
    "修改后以工具返回为准，不要假装已经改了却没调用本工具。"
)


class MaiLifePlugin(MaiBotPlugin):
    """麦麦生活：日程生成、分享提醒、规划器注入与工具。"""

    config_model = MaiLifeConfig
    config_reload_subscriptions = {ON_BOT_CONFIG_RELOAD}

    def __init__(self) -> None:
        super().__init__()
        self._store: LifeStore | None = None
        self._generator: LifeGenerator | None = None
        self._scheduler: LifeScheduler | None = None
        self._generate_lock = asyncio.Lock()
        self._cached_persona = ""
        self._cached_nickname = "麦麦"
        self._stream_cache: list[dict[str, Any]] = []
        self._stream_cache_at: datetime | None = None

    async def on_load(self) -> None:
        data_dir = self.ctx.paths.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self._store = LifeStore(data_dir)
        self._generator = LifeGenerator(self.ctx, self._store)
        self._scheduler = LifeScheduler(
            get_config=lambda: self.config,
            generate_today=self._generate_today,
            load_today=self._load_today,
            fire_share=self._fire_share,
            mark_share_fired=self._mark_share_fired,
        )
        await self._refresh_bot_profile()
        if self.config.plugin.enabled:
            await self._scheduler.start(
                generate_if_missing=self.config.generation.generate_on_load_if_missing
            )
            logger.info("麦麦生活已加载，调度器已启动")
        else:
            logger.info("麦麦生活已加载，但 plugin.enabled=false，调度器未启动")

    async def on_unload(self) -> None:
        if self._scheduler is not None:
            await self._scheduler.stop()
        logger.info("麦麦生活已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        del config_data, version
        if scope == ON_BOT_CONFIG_RELOAD:
            await self._refresh_bot_profile()
            return
        if scope != CONFIG_RELOAD_SCOPE_SELF:
            return
        if self._scheduler is None:
            return
        if self.config.plugin.enabled:
            await self._scheduler.start(
                generate_if_missing=self.config.generation.generate_on_load_if_missing
            )
            logger.info("麦麦生活配置已更新，调度器已重启")
        else:
            await self._scheduler.stop()
            logger.info("麦麦生活已关闭，调度器已停止")

    @HookHandler(
        "maisaka.planner.before_request",
        name="mai_life_inject_schedule",
        description="在规划器请求前注入最近几条日程",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        error_policy=ErrorPolicy.SKIP,
    )
    async def inject_schedule_handler(self, **kwargs: Any) -> dict[str, Any]:
        config = self.config
        if not config.plugin.enabled or not config.schedule.enabled:
            return {"action": "continue"}
        session_id = normalize_text(kwargs.get("session_id"))
        hook_log = self.ctx.logger
        if not session_id:
            hook_log.info("日程注入跳过：规划器未提供 session_id")
            return {"action": "continue"}
        streams = await self._cached_streams(config.schedule.stream_discovery_platform)
        if not session_allowed(session_id, config.schedule.allowed_streams, streams):
            hook_log.info(
                "日程注入跳过：聊天流未匹配白名单 session=%s allowlist=%s",
                session_id,
                config.schedule.allowed_streams,
            )
            return {"action": "continue"}
        now = self._now()
        document = self._load_today(now)
        text = build_inject_text(
            document,
            now,
            count=config.schedule.recent_inject_count,
            window_minutes=config.schedule.inject_window_minutes,
            template=config.prompts.inject_template,
            extra_values={
                "timezone": config.generation.timezone,
                "bot_nickname": self._cached_nickname,
            },
        )
        if not text:
            hook_log.info(
                "日程注入跳过：没有可注入的日程 session=%s date=%s activities=%s",
                session_id,
                now.strftime("%Y-%m-%d"),
                0 if document is None else len(document.activities),
            )
            return {"action": "continue"}
        result = dict(kwargs)
        items = kwargs.get("items")
        messages = kwargs.get("messages")
        if isinstance(items, list):
            result["items"] = inject_into_items(items, text, now=now)
            hook_log.info("已向规划器注入日程 session=%s chars=%s payload=items", session_id, len(text))
            return {"action": "continue", "modified_kwargs": result}
        if isinstance(messages, list):
            result["messages"] = inject_into_messages(messages, text)
            hook_log.info("已向规划器注入日程 session=%s chars=%s payload=messages", session_id, len(text))
            return {"action": "continue", "modified_kwargs": result}
        hook_log.warning(
            "日程注入跳过：规划器未提供 items 或 messages session=%s keys=%s",
            session_id,
            sorted(str(key) for key in kwargs),
        )
        return {"action": "continue"}

    @Tool(
        "mai_life_query",
        brief_description="查询角色的日程和分享提醒时间",
        detailed_description=_QUERY_DESCRIPTION,
        core_tool=True,
        parameters=[
            ToolParameterInfo(
                name="date",
                param_type=ToolParamType.STRING,
                description="要查询的日期，YYYY-MM-DD，可留空表示今天",
                required=False,
            ),
            ToolParameterInfo(
                name="target",
                param_type=ToolParamType.STRING,
                description="查询范围：schedule、share 或 both，默认 both",
                required=False,
            ),
        ],
    )
    async def handle_query(self, date: str = "", target: str = "both", **kwargs: Any) -> dict[str, Any]:
        try:
            return await self._query_life(date=date, target=target, kwargs=kwargs)
        except Exception as exc:
            logger.exception("查询生活记录失败")
            return {"success": False, "content": f"查询失败：{exc}"}

    @Tool(
        "mai_life_modify",
        brief_description="修改角色的日程或分享提醒时间",
        detailed_description=_MODIFY_DESCRIPTION,
        core_tool=True,
        parameters=[
            ToolParameterInfo(
                name="target",
                param_type=ToolParamType.STRING,
                description="修改目标：schedule 或 share",
                required=True,
            ),
            ToolParameterInfo(
                name="action",
                param_type=ToolParamType.STRING,
                description="动作：add、update、delete 或 replace_day",
                required=True,
            ),
            ToolParameterInfo(
                name="request",
                param_type=ToolParamType.STRING,
                description="自然语言或 JSON 描述要改什么，例如「下午三点改成学习」",
                required=True,
            ),
            ToolParameterInfo(
                name="date",
                param_type=ToolParamType.STRING,
                description="要修改的日期，YYYY-MM-DD，可留空表示今天",
                required=False,
            ),
        ],
    )
    async def handle_modify(
        self,
        target: str = "schedule",
        action: str = "update",
        request: str = "",
        date: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            return await self._modify_life(
                target=target,
                action=action,
                request=request,
                date=date,
                kwargs=kwargs,
            )
        except Exception as exc:
            logger.exception("修改生活记录失败")
            return {"success": False, "content": f"修改失败：{exc}"}

    @Command("mai_life_help", description="查看麦麦生活命令", pattern=r"^/mai_life_help$")
    async def handle_help(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        del kwargs
        text = (
            "麦麦生活命令：\n"
            "/mai_life_help 查看帮助\n"
            "/mai_life_status 查看开关、条数和下次生成时间\n"
            "/mai_life_generate 立即生成今日日程和分享任务\n"
            "/mai_life_show 查看今日日程和分享任务\n"
            "/mai_life_wake 立即对当前聊天流唤醒规划器（测试用）"
        )
        await self._reply(stream_id, text)
        return True, "已发送帮助", 2

    @Command("mai_life_status", description="查看麦麦生活状态", pattern=r"^/mai_life_status$")
    async def handle_status(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        del kwargs
        now = self._now()
        document = self._load_today(now)
        activity_count = len(document.activities) if document else 0
        share_count = len(document.shares) if document else 0
        pending = 0
        current_shares: list[ShareItem] = []
        if document:
            current_shares = shares_for_stream(document, stream_id) if stream_id else document.shares
            pending = sum(1 for item in current_shares if not item.fired)
            share_count = len(current_shares)
        text = (
            f"插件启用：{'是' if self.config.plugin.enabled else '否'}\n"
            f"日程功能：{'是' if self.config.schedule.enabled else '否'}，"
            f"白名单 {len(self.config.schedule.allowed_streams)} 项\n"
            f"分享功能：{'是' if self.config.share.enabled else '否'}，"
            f"白名单 {len(share_allowlist(self.config.share))} 项\n"
            f"今日日期：{now.strftime('%Y-%m-%d')} {now.strftime('%H:%M')}\n"
            f"今日日程：{activity_count} 条\n"
            f"当前聊天流分享：{share_count} 条，未提醒 {pending} 条\n"
            f"全部聊天流分享：{len(document.shares) if document else 0} 条\n"
            f"每日生成时刻：{self.config.generation.time}"
        )
        await self._reply(stream_id, text)
        return True, "已发送状态", 2

    @Command("mai_life_generate", description="立即生成今日生活记录", pattern=r"^/mai_life_generate$")
    async def handle_generate(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        del kwargs
        if not self.config.plugin.enabled:
            await self._reply(stream_id, "麦麦生活未启用")
            return True, "插件未启用", 2
        asyncio.create_task(self._generate_and_notify(stream_id), name="mai-life-manual-generate")
        await self._reply(stream_id, "正在生成今日日程和分享任务…")
        return True, "已开始生成", 2

    @Command("mai_life_show", description="查看今日日程和分享任务", pattern=r"^/mai_life_show$")
    async def handle_show(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        del kwargs
        now = self._now()
        document = self._load_today(now)
        if document is None:
            await self._reply(stream_id, "今天还没有生活记录。可用 /mai_life_generate 生成。")
            return True, "今日无记录", 2
        current_shares = shares_for_stream(document, stream_id) if stream_id else document.shares
        text = (
            f"{document.date} 日程：\n{format_activities(document.activities)}\n\n"
            f"{document.date} 当前聊天流分享任务：\n{format_shares(current_shares)}"
        )
        await self._reply(stream_id, text)
        return True, "已发送今日记录", 2

    @Command("mai_life_wake", description="立即唤醒当前聊天流的规划器", pattern=r"^/mai_life_wake$")
    async def handle_wake(self, stream_id: str = "", **kwargs: Any) -> tuple[bool, str, int]:
        del kwargs
        if not stream_id:
            return True, "无法获取当前聊天流", 2
        if not self.config.plugin.enabled:
            await self._reply(stream_id, "麦麦生活未启用")
            return True, "插件未启用", 2
        now = self._now()
        intent = render_template(
            choose_template(self.config.prompts.wake_intent_template, DEFAULT_WAKE_INTENT_TEMPLATE),
            self._wake_values(now, share_item="手动测试唤醒"),
        )
        result = await self.ctx.maisaka.proactive.trigger(
            stream_id=stream_id,
            intent=intent,
            reason="mai_life_manual_wake",
            metadata={"source": "mai-life", "manual": True},
        )
        await self._reply(stream_id, f"已请求唤醒规划器：{result}")
        return True, "已唤醒规划器", 2

    async def _query_life(self, date: str, target: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        if not self.config.plugin.enabled:
            return {"success": True, "content": "麦麦生活未启用"}
        session_id = self._tool_session_id(kwargs)
        now = self._now()
        query_date = normalize_text(date) or now.strftime("%Y-%m-%d")
        scope = normalize_text(target).lower() or "both"
        if scope not in {"schedule", "share", "both"}:
            return {"success": False, "content": "target 只能是 schedule、share 或 both"}

        streams = await self._cached_streams(self.config.schedule.stream_discovery_platform)
        want_schedule = scope in {"schedule", "both"}
        want_share = scope in {"share", "both"}
        if want_schedule and not session_allowed(session_id, self.config.schedule.allowed_streams, streams):
            want_schedule = False
        share_streams = await self._cached_streams("all_platforms")
        if want_share and not session_allowed(session_id, share_allowlist(self.config.share), share_streams):
            want_share = False
        if scope == "schedule" and not want_schedule:
            return {"success": True, "content": "当前聊天流未启用日程功能"}
        if scope == "share" and not want_share:
            return {"success": True, "content": "当前聊天流未启用分享提醒"}
        if not want_schedule and not want_share:
            return {"success": True, "content": "当前聊天流未启用麦麦生活"}

        document = None if self._store is None else self._store.load(query_date)
        if document is None:
            return {"success": True, "content": f"{query_date} 还没有生活记录"}
        parts: list[str] = []
        if want_schedule:
            if not self.config.schedule.enabled:
                parts.append("日程功能未启用")
            else:
                parts.append(f"{query_date} 日程：\n{format_activities(document.activities)}")
        if want_share:
            if not self.config.share.enabled:
                parts.append("分享功能未启用")
            else:
                current_shares = shares_for_stream(document, session_id) if session_id else document.shares
                parts.append(f"{query_date} 当前聊天流分享任务：\n{format_shares(current_shares)}")
        return {"success": True, "content": "\n\n".join(parts)}

    async def _modify_life(
        self,
        target: str,
        action: str,
        request: str,
        date: str,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.config.plugin.enabled:
            return {"success": False, "content": "麦麦生活未启用"}
        if self._generator is None:
            return {"success": False, "content": "生成器未初始化"}
        session_id = self._tool_session_id(kwargs)
        now = self._now()
        target_name = normalize_text(target).lower() or "schedule"
        action_name = normalize_text(action).lower() or "update"
        request_text = normalize_text(request)
        if target_name not in {"schedule", "share"}:
            return {"success": False, "content": "target 只能是 schedule 或 share"}
        if action_name not in {"add", "update", "delete", "replace_day"}:
            return {"success": False, "content": "action 只能是 add、update、delete 或 replace_day"}
        if not request_text:
            return {"success": False, "content": "request 不能为空"}

        if target_name == "schedule":
            streams = await self._cached_streams(self.config.schedule.stream_discovery_platform)
            if not self.config.schedule.enabled or not session_allowed(
                session_id, self.config.schedule.allowed_streams, streams
            ):
                return {"success": False, "content": "当前聊天流未启用日程功能"}
        else:
            streams = await self._cached_streams("all_platforms")
            if not self.config.share.enabled or not session_allowed(
                session_id, share_allowlist(self.config.share), streams
            ):
                return {"success": False, "content": "当前聊天流未启用分享提醒"}

        stream_info = ""
        if session_id:
            for stream in streams:
                if stream_session_id(stream) == session_id:
                    stream_info = format_stream_info(stream)
                    break
        document = await self._generator.modify(
            self.config,
            now,
            date=normalize_text(date) or now.strftime("%Y-%m-%d"),
            target=target_name,
            action=action_name,
            request=request_text,
            persona=self._cached_persona,
            bot_nickname=self._cached_nickname,
            stream_id=session_id if target_name == "share" else "",
            stream_info=stream_info,
        )
        if target_name == "share":
            current_shares = shares_for_stream(document, session_id) if session_id else document.shares
            content = f"已更新当前聊天流分享任务：\n{format_shares(current_shares)}"
        else:
            content = f"已更新日程：\n{format_activities(document.activities)}"
        return {"success": True, "content": content}

    async def _generate_today(self, force: bool) -> LifeDocument | None:
        if self._generator is None:
            return None
        async with self._generate_lock:
            now = self._now()
            history = await self._resolve_history()
            knowledge = await self._resolve_knowledge()
            share_streams = await self._list_share_streams()
            history_by_stream = await self._resolve_history_by_streams(share_streams)
            document = await self._generator.generate_today(
                self.config,
                now,
                force=force,
                persona=self._cached_persona,
                history=history,
                knowledge=knowledge,
                bot_nickname=self._cached_nickname,
                share_streams=share_streams,
                history_by_stream=history_by_stream,
            )
            if document is not None:
                logger.info(
                    "已保存生活记录 date=%s activities=%s shares=%s streams=%s",
                    document.date,
                    len(document.activities),
                    len(document.shares),
                    len({item.stream_id for item in document.shares if item.stream_id}),
                )
            return document

    async def _generate_and_notify(self, stream_id: str) -> None:
        try:
            document = await self._generate_today(True)
        except Exception:
            logger.exception("手动生成失败")
            await self._reply(stream_id, "生成失败，请查看日志")
            return
        if document is None:
            await self._reply(stream_id, "生成失败：没有得到可用的日程")
            return
        current_shares = shares_for_stream(document, stream_id) if stream_id else document.shares
        stream_count = len({item.stream_id for item in document.shares if item.stream_id})
        await self._reply(
            stream_id,
            f"已生成 {document.date}：日程 {len(document.activities)} 条，"
            f"当前聊天流分享 {len(current_shares)} 条，共 {stream_count} 个聊天流。",
        )

    def _load_today(self, now: datetime) -> LifeDocument | None:
        if self._store is None:
            return None
        return self._store.load(now.strftime("%Y-%m-%d"))

    def _mark_share_fired(self, date: str, share_id: str, now: datetime) -> None:
        if self._store is None:
            return
        self._store.mark_share_fired(date, share_id, now)

    async def _fire_share(self, item: ShareItem, document: LifeDocument, now: datetime) -> None:
        target_stream_id = normalize_text(item.stream_id)
        if target_stream_id:
            streams = [stream for stream in await self._list_share_streams() if stream_session_id(stream) == target_stream_id]
        else:
            streams = await self._list_share_streams()
        if not streams:
            logger.info("分享任务到期但没有匹配的聊天流: %s stream=%s", item.id, target_stream_id)
            self._mark_share_fired(document.date, item.id, now)
            return
        share_item = f"{item.time} {item.title}"
        if item.hint:
            share_item += f"（{item.hint}）"
        intent = item.intent.strip() or render_template(
            choose_template(self.config.prompts.wake_intent_template, DEFAULT_WAKE_INTENT_TEMPLATE),
            self._wake_values(now, share_item=share_item),
        )
        cooldown = self.config.share.cooldown_seconds
        triggered = False
        for stream in streams:
            stream_id = stream_session_id(stream)
            if not stream_id:
                continue
            if self._scheduler is not None and not self._scheduler.can_wake_stream(stream_id, now, cooldown):
                logger.info("聊天流仍在冷却，跳过唤醒: %s", stream_id)
                continue
            result = await self.ctx.maisaka.proactive.trigger(
                stream_id=stream_id,
                intent=intent,
                reason="mai_life_share",
                metadata={
                    "source": "mai-life",
                    "share_id": item.id,
                    "date": document.date,
                },
            )
            success = True
            if isinstance(result, dict) and result.get("success") is False:
                success = False
                logger.warning("唤醒规划器返回失败: stream=%s result=%s", stream_id, result)
            if success:
                triggered = True
                if self._scheduler is not None:
                    self._scheduler.remember_wake(stream_id, now)
        if triggered:
            self._mark_share_fired(document.date, item.id, now)
            logger.info("已唤醒规划器分享任务: %s %s", item.time, item.title)
        else:
            logger.warning("分享任务未能唤醒任何聊天流: %s", item.id)

    def _wake_values(self, now: datetime, share_item: str) -> dict[str, str]:
        return {
            "date": now.strftime("%Y-%m-%d"),
            "weekday": weekday_cn(now),
            "now": now.strftime("%Y-%m-%d %H:%M"),
            "timezone": self.config.generation.timezone,
            "bot_nickname": self._cached_nickname,
            "share_item": share_item,
            "recent_schedule": "",
            "persona": self._cached_persona,
        }

    async def _list_share_streams(self) -> list[dict[str, Any]]:
        streams = await self._cached_streams("all_platforms")
        return filter_streams(streams, share_allowlist(self.config.share))

    async def _cached_streams(self, platform: str) -> list[dict[str, Any]]:
        now = datetime.now()
        if self._stream_cache_at is not None and (now - self._stream_cache_at).total_seconds() < 60:
            return self._stream_cache
        try:
            streams = await self.ctx.chat.get_all_streams(platform or "all_platforms")
        except Exception:
            logger.exception("读取聊天流列表失败")
            return self._stream_cache
        if isinstance(streams, dict) and streams.get("success") is False:
            logger.warning("读取聊天流列表失败: %s", streams.get("error"))
            return self._stream_cache
        if not isinstance(streams, list):
            return self._stream_cache
        self._stream_cache = [item for item in streams if isinstance(item, dict)]
        self._stream_cache_at = now
        return self._stream_cache

    async def _resolve_history_by_streams(self, streams: list[dict[str, Any]]) -> dict[str, str]:
        result: dict[str, str] = {}
        for stream in streams:
            session_id = stream_session_id(stream)
            if not session_id:
                continue
            text = await self._resolve_history(stream_ids=[session_id])
            if text:
                result[session_id] = text
        return result

    async def _resolve_history(self, stream_ids: list[str] | None = None) -> str:
        config = self.config.generation
        if not config.include_history or config.history_message_limit <= 0:
            return ""
        resolved_ids = [normalize_text(item) for item in (stream_ids or []) if normalize_text(item)]
        if not resolved_ids:
            resolved_ids = [normalize_text(item) for item in config.history_stream_ids if normalize_text(item)]
        if not resolved_ids:
            streams = await self._cached_streams(self.config.schedule.stream_discovery_platform)
            resolved_ids = [
                stream_session_id(item)
                for item in filter_streams(streams, self.config.schedule.allowed_streams)
            ]
        stream_ids = resolved_ids
        if not stream_ids:
            return ""
        now = self._now()
        start = now.timestamp() - max(1, int(config.history_window_hours)) * 3600
        parts: list[str] = []
        for stream_id in stream_ids[:8]:
            try:
                messages = await self.ctx.message.get_by_time_in_chat(
                    stream_id,
                    str(start),
                    str(now.timestamp()),
                    limit=config.history_message_limit,
                    limit_mode="latest",
                    filter_mai=False,
                    filter_command=True,
                )
            except Exception:
                logger.exception("读取聊天历史失败: %s", stream_id)
                continue
            if isinstance(messages, dict) and messages.get("success") is False:
                continue
            if not messages:
                continue
            try:
                readable = await self.ctx.message.build_readable(messages)
            except Exception:
                readable = json.dumps(messages, ensure_ascii=False)[:2000]
            text = normalize_text(readable)
            if text:
                parts.append(f"聊天流 {stream_id}：\n{text}")
        return "\n\n".join(parts)

    async def _resolve_knowledge(self) -> str:
        config = self.config.generation
        if not config.include_knowledge or config.knowledge_search_limit <= 0:
            return ""
        try:
            payload = knowledge_search_args(
                self._now(),
                limit=config.knowledge_search_limit,
                window_hours=config.knowledge_window_hours,
            )
            result = await self.ctx.call_capability("knowledge.search", **payload)
        except Exception:
            logger.exception("知识库检索失败")
            return ""
        if isinstance(result, dict) and result.get("success") is False:
            return ""
        return normalize_text(result)

    async def _refresh_bot_profile(self) -> None:
        extra = self.config.generation.extra_persona.strip()
        system_persona = ""
        if self.config.generation.include_persona:
            try:
                system_persona = normalize_text(
                    await self.ctx.config.get("personality.personality", "")
                )
            except Exception:
                logger.exception("读取主程序人设失败")
        self._cached_persona = compose_persona(
            include_system=self.config.generation.include_persona,
            system_persona=system_persona,
            extra_persona=extra,
        )
        try:
            nickname = normalize_text(await self.ctx.config.get("bot.nickname", ""))
        except Exception:
            nickname = ""
        self._cached_nickname = nickname or "麦麦"

    def _now(self) -> datetime:
        timezone_name = self.config.generation.timezone
        if timezone_name not in {"", "local", "system"} and not self._timezone_ok(timezone_name):
            raise ValueError(f"未知时区: {timezone_name}")
        return wall_clock_now(timezone_name)

    @staticmethod
    def _timezone_ok(name: str) -> bool:
        try:
            wall_clock_now(name)
            return True
        except ValueError:
            return False

    @staticmethod
    def _tool_session_id(kwargs: dict[str, Any]) -> str:
        return (
            normalize_text(kwargs.get("stream_id"))
            or normalize_text(kwargs.get("chat_id"))
            or normalize_text(kwargs.get("session_id"))
        )

    async def _reply(self, stream_id: str, text: str) -> None:
        if not stream_id:
            return
        await self.ctx.send.text(text, stream_id)


KNOWLEDGE_SEARCH_QUERY = "角色最近的习惯、约定和日程相关记忆"


def knowledge_search_args(
    now: datetime,
    *,
    limit: int,
    window_hours: int,
) -> dict[str, Any]:
    """组装记忆检索参数。窗口大于 0 时走 hybrid 并带上时间范围。"""

    payload: dict[str, Any] = {
        "query": KNOWLEDGE_SEARCH_QUERY,
        "limit": max(1, int(limit)),
    }
    hours = int(window_hours)
    if hours <= 0:
        return payload
    end = now.timestamp()
    payload["mode"] = "hybrid"
    payload["time_start"] = end - hours * 3600
    payload["time_end"] = end
    return payload


def compose_persona(*, include_system: bool, system_persona: str, extra_persona: str) -> str:
    """读入人设开启时拼接主程序人设与补充人设，关闭时只用补充人设。"""

    extra = str(extra_persona or "").strip()
    if not include_system:
        return extra
    system = str(system_persona or "").strip()
    if system and extra:
        return f"{system}\n\n补充设定：{extra}"
    return system or extra


def create_plugin() -> MaiLifePlugin:
    """创建插件实例。"""

    return MaiLifePlugin()
