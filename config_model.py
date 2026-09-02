"""mai-life 配置模型。"""

from __future__ import annotations

from typing import Any, Literal

from maibot_sdk import Field, PluginConfigBase
from pydantic import model_validator

from .prompts import (
    DEFAULT_INJECT_TEMPLATE,
    DEFAULT_MODIFY_SYSTEM,
    DEFAULT_MODIFY_USER,
    DEFAULT_SCHEDULE_SYSTEM,
    DEFAULT_SCHEDULE_USER,
    DEFAULT_SHARE_SYSTEM,
    DEFAULT_SHARE_USER,
    DEFAULT_WAKE_INTENT_TEMPLATE,
)


def _ui(label: str, *, textarea: bool = False, placeholder: str = "", hint: str = "") -> dict[str, Any]:
    extra: dict[str, Any] = {"label": label}
    if textarea:
        extra["x-widget"] = "textarea"
        extra["rows"] = 6
    if placeholder:
        extra["placeholder"] = placeholder
    if hint:
        extra["hint"] = hint
    return extra


class PluginSection(PluginConfigBase):
    """插件总开关和配置版本。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(
        default=False,
        description="关闭后停止生成、注入和到点唤醒",
        json_schema_extra=_ui("启用插件"),
    )
    config_version: str = Field(
        default="1.1.0",
        description="配置结构版本，一般不用改",
        json_schema_extra=_ui("配置版本"),
    )


class GenerationSection(PluginConfigBase):
    """每日自动生成的时间、模型和上下文来源。"""

    __ui_label__ = "每日生成"
    __ui_icon__ = "calendar"
    __ui_order__ = 1

    time: str = Field(
        default="01:30",
        description="每天在这个时刻生成日程和分享任务，格式 HH:MM",
        json_schema_extra=_ui("生成时刻", placeholder="01:30"),
    )
    timezone: str = Field(
        default="local",
        description="local 表示系统本地时间，也可填 Asia/Shanghai",
        json_schema_extra=_ui("时区", placeholder="local"),
    )
    model: str = Field(
        default="planner",
        description="生成使用的模型任务名，空则用 Host 默认",
        json_schema_extra=_ui("生成模型", placeholder="planner"),
    )
    max_tokens: int = Field(
        default=4096,
        description="单次生成允许的最大输出长度",
        json_schema_extra=_ui("最大输出 Token"),
    )
    temperature: float = Field(
        default=-1.0,
        description="小于 0 表示使用 Host 默认温度",
        json_schema_extra=_ui("采样温度"),
    )
    llm_timeout_ms: int = Field(
        default=120000,
        description="单次 LLM 调用超时时间",
        json_schema_extra=_ui("LLM 超时（毫秒）"),
    )
    generate_on_load_if_missing: bool = Field(
        default=True,
        description="插件启动时如果今天还没有记录，立刻生成",
        json_schema_extra=_ui("启动时补生成"),
    )
    include_persona: bool = Field(
        default=True,
        description="生成时是否读入人设",
        json_schema_extra=_ui("读入人设"),
    )
    include_history: bool = Field(
        default=True,
        description="生成时是否读入最近聊天",
        json_schema_extra=_ui("读入聊天记录"),
    )
    include_knowledge: bool = Field(
        default=True,
        description="生成时是否检索记忆",
        json_schema_extra=_ui("检索记忆"),
    )
    persona_source: Literal["system", "extra", "both"] = Field(
        default="both",
        description="system 用主程序人格，extra 用下方补充人设，both 拼接两者",
        json_schema_extra=_ui("人设来源"),
    )
    extra_persona: str = Field(
        default="",
        description="补充人设，仅在人设来源为 extra 或 both 时使用",
        json_schema_extra=_ui("补充人设", textarea=True),
    )
    history_message_limit: int = Field(
        default=30,
        description="生成时最多读取的最近消息条数",
        json_schema_extra=_ui("历史消息条数"),
    )
    history_window_hours: int = Field(
        default=24,
        description="只读取最近多少小时内的聊天",
        json_schema_extra=_ui("历史时间窗口（小时）"),
    )
    knowledge_search_limit: int = Field(
        default=5,
        description="检索记忆条数，0 表示不检索",
        json_schema_extra=_ui("记忆检索条数"),
    )
    history_stream_ids: list[str] = Field(
        default_factory=list,
        description="生成日程时读取历史的聊天流。空则使用日程白名单",
        json_schema_extra=_ui("日程历史聊天流"),
    )


class ScheduleSection(PluginConfigBase):
    """角色全局日程的生成与规划器注入。"""

    __ui_label__ = "日程"
    __ui_icon__ = "clock"
    __ui_order__ = 2

    enabled: bool = Field(
        default=True,
        description="关闭后不再生成日程，也不再注入规划器",
        json_schema_extra=_ui("启用日程"),
    )
    allowed_streams: list[str] = Field(
        default_factory=list,
        description="启用日程注入的聊天流。支持 all、session:<id>、<platform>:private:<id>、<platform>:<user_id>",
        json_schema_extra=_ui("启用的聊天流", placeholder="webui:webui_user_xxx"),
    )
    stream_discovery_platform: str = Field(
        default="all_platforms",
        description="解析白名单时扫描的平台，all_platforms 表示所有平台",
        json_schema_extra=_ui("扫描平台"),
    )
    activity_count_min: int = Field(
        default=8,
        description="每天至少生成多少条活动",
        json_schema_extra=_ui("活动数量下限"),
    )
    activity_count_max: int = Field(
        default=14,
        description="每天最多生成多少条活动",
        json_schema_extra=_ui("活动数量上限"),
    )
    wake_time: str = Field(
        default="08:00",
        description="角色通常醒来的时间",
        json_schema_extra=_ui("起床时间", placeholder="08:00"),
    )
    sleep_time: str = Field(
        default="01:00",
        description="角色通常入睡的时间",
        json_schema_extra=_ui("入睡时间", placeholder="01:00"),
    )
    recent_inject_count: int = Field(
        default=3,
        description="每次规划时注入最近几条日程",
        json_schema_extra=_ui("注入条数"),
    )
    inject_window_minutes: int = Field(
        default=180,
        description="优先取当前时间前后这个窗口内的活动",
        json_schema_extra=_ui("注入时间窗口（分钟）"),
    )


class ShareStreamProfile(PluginConfigBase):
    """单个聊天流的分享启用项，可覆盖条数和额外提示词。"""

    stream: str = Field(
        default="",
        description="聊天流标识。填写后即启用该聊天流。例如 qq:private:123、session:xxx 或 all",
        json_schema_extra=_ui("聊天流", placeholder="qq:private:123"),
    )
    count_min: int = Field(
        default=0,
        description="该聊天流每天最少生成几条。0 表示使用默认下限",
        json_schema_extra=_ui("生成条数下限"),
    )
    count_max: int = Field(
        default=0,
        description="该聊天流每天最多生成几条。0 表示使用默认上限",
        json_schema_extra=_ui("生成条数上限"),
    )
    extra_prompt: str = Field(
        default="",
        description="只作用于该聊天流的额外提示词，会拼到分享生成提示词后面",
        json_schema_extra=_ui("额外提示词", textarea=True),
    )


class ShareSection(PluginConfigBase):
    """按聊天流隔离的分享任务生成与到点唤醒。"""

    __ui_label__ = "分享提醒"
    __ui_icon__ = "bell"
    __ui_order__ = 3

    enabled: bool = Field(
        default=True,
        description="关闭后不再为各聊天流生成分享任务，也不再到点唤醒",
        json_schema_extra=_ui("启用分享提醒"),
    )
    stream_profiles: list[ShareStreamProfile] = Field(
        default_factory=list,
        description="填写聊天流即启用该聊天流的分享生成和到点唤醒，每个聊天流单独生成一份。条数填 0、额外提示词留空则使用下面的默认值。支持 all、session:<id>、<platform>:private:<id>、<platform>:<user_id>",
        json_schema_extra=_ui("启用的聊天流"),
    )
    stream_discovery_platform: str = Field(
        default="all_platforms",
        description="解析白名单时扫描的平台，all_platforms 表示所有平台",
        json_schema_extra=_ui("扫描平台"),
    )
    count_min: int = Field(
        default=3,
        description="聊天流未单独填写条数时使用的默认下限",
        json_schema_extra=_ui("默认生成条数下限"),
    )
    count_max: int = Field(
        default=6,
        description="聊天流未单独填写条数时使用的默认上限",
        json_schema_extra=_ui("默认生成条数上限"),
    )
    extra_prompt: str = Field(
        default="",
        description="聊天流未单独填写额外提示词时使用的默认值",
        json_schema_extra=_ui("默认额外提示词", textarea=True),
    )

    @model_validator(mode="before")
    @classmethod
    def _merge_legacy_allowed_streams(cls, data: Any) -> Any:
        """把旧配置里的 share.allowed_streams 并进 stream_profiles。"""

        if not isinstance(data, dict):
            return data
        allowed_raw = data.get("allowed_streams")
        if not isinstance(allowed_raw, list):
            return data
        allowed = [str(item).strip() for item in allowed_raw if str(item).strip()]
        if not allowed:
            return data
        profiles = list(data.get("stream_profiles") or [])
        existing: set[str] = set()
        for profile in profiles:
            if isinstance(profile, dict):
                existing.add(str(profile.get("stream") or "").strip())
            else:
                existing.add(str(getattr(profile, "stream", "") or "").strip())
        missing = [item for item in allowed if item not in existing]
        if not missing:
            return data
        merged = dict(data)
        merged["stream_profiles"] = profiles + [{"stream": item} for item in missing]
        return merged

    wake_planner: bool = Field(
        default=True,
        description="到点后是否唤醒对应聊天流的规划器",
        json_schema_extra=_ui("到点唤醒规划器"),
    )
    patrol_interval_seconds: int = Field(
        default=60,
        description="检查分享任务是否到期的间隔",
        json_schema_extra=_ui("巡检间隔（秒）"),
    )
    cooldown_seconds: int = Field(
        default=180,
        description="同一聊天流两次唤醒的最小间隔",
        json_schema_extra=_ui("唤醒冷却（秒）"),
    )
    silence_start: str = Field(
        default="00:00",
        description="静默开始时间，该时段不唤醒规划器",
        json_schema_extra=_ui("静默开始", placeholder="00:00"),
    )
    silence_end: str = Field(
        default="07:30",
        description="静默结束时间",
        json_schema_extra=_ui("静默结束", placeholder="07:30"),
    )
    miss_tolerance_minutes: int = Field(
        default=30,
        description="过点后仍可补触发的宽限时间",
        json_schema_extra=_ui("过点宽限（分钟）"),
    )


class PromptSection(PluginConfigBase):
    """各部分基础提示词。留空则使用内置默认值。"""

    __ui_label__ = "提示词"
    __ui_icon__ = "file-text"
    __ui_order__ = 4

    schedule_system: str = Field(
        default=DEFAULT_SCHEDULE_SYSTEM,
        description="日程生成的系统提示词",
        json_schema_extra=_ui("日程系统提示词", textarea=True),
    )
    schedule_user: str = Field(
        default=DEFAULT_SCHEDULE_USER,
        description="日程生成的用户提示词，可使用占位符",
        json_schema_extra=_ui("日程用户提示词", textarea=True),
    )
    share_system: str = Field(
        default=DEFAULT_SHARE_SYSTEM,
        description="分享任务生成的系统提示词",
        json_schema_extra=_ui("分享系统提示词", textarea=True),
    )
    share_user: str = Field(
        default=DEFAULT_SHARE_USER,
        description="分享任务生成的用户提示词，可使用 {extra_prompt} {stream_info} 等占位符",
        json_schema_extra=_ui("分享用户提示词", textarea=True),
    )
    modify_system: str = Field(
        default=DEFAULT_MODIFY_SYSTEM,
        description="修改日程或分享任务时的系统提示词",
        json_schema_extra=_ui("修改系统提示词", textarea=True),
    )
    modify_user: str = Field(
        default=DEFAULT_MODIFY_USER,
        description="修改日程或分享任务时的用户提示词",
        json_schema_extra=_ui("修改用户提示词", textarea=True),
    )
    inject_template: str = Field(
        default=DEFAULT_INJECT_TEMPLATE,
        description="写入规划器的日程注入文本",
        json_schema_extra=_ui("规划器注入模板", textarea=True),
    )
    wake_intent_template: str = Field(
        default=DEFAULT_WAKE_INTENT_TEMPLATE,
        description="到点唤醒规划器时的意图文本",
        json_schema_extra=_ui("唤醒意图模板", textarea=True),
    )


class MaiLifeConfig(PluginConfigBase):
    """麦麦生活插件完整配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    generation: GenerationSection = Field(default_factory=GenerationSection)
    schedule: ScheduleSection = Field(default_factory=ScheduleSection)
    share: ShareSection = Field(default_factory=ShareSection)
    prompts: PromptSection = Field(default_factory=PromptSection)


def share_allowlist(share: ShareSection) -> list[str]:
    """分享启用名单：stream_profiles 里填写的聊天流。"""

    from .streams import normalize_allowlist

    return normalize_allowlist([profile.stream for profile in share.stream_profiles])


def resolve_share_profile(share: ShareSection, stream: dict[str, Any]) -> tuple[int, int, str]:
    """解析某个聊天流的分享条数和额外提示词。具体聊天流优先于 all。"""

    from .streams import normalize_text, stream_matches_entry

    specific: ShareStreamProfile | None = None
    wildcard: ShareStreamProfile | None = None
    for profile in share.stream_profiles:
        entry = normalize_text(profile.stream)
        if not entry:
            continue
        if entry.lower() == "all":
            if wildcard is None:
                wildcard = profile
            continue
        if specific is None and stream_matches_entry(stream, entry):
            specific = profile
    profile = specific or wildcard
    if profile is None:
        count_min = max(1, int(share.count_min))
        count_max = max(count_min, int(share.count_max))
        return count_min, count_max, str(share.extra_prompt or "").strip()
    count_min = profile.count_min if profile.count_min > 0 else share.count_min
    count_max = profile.count_max if profile.count_max > 0 else share.count_max
    extra = str(profile.extra_prompt or "").strip() or str(share.extra_prompt or "").strip()
    count_min = max(1, int(count_min))
    count_max = max(count_min, int(count_max))
    return count_min, count_max, extra
