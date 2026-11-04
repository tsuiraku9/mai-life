"""用于识别已发布默认配置的历史正文。"""

DEFAULT_SHARE_USER_V1_3_0 = """【日期】{date} 星期{weekday} 现在 {now}
【角色名】{bot_nickname}
【数量】生成 {share_count_min} 到 {share_count_max} 条分享任务。
【静默时段】{silence_start} 到 {silence_end}。不要把任何分享任务的 time 安排在这个区间内（可跨天）。开始与结束相同表示不设静默时段。

【人设】
{persona}

【今日日程】
{schedule_json}

【当前聊天流】
{stream_info}

【该聊天流最近聊天】
{history}

【额外要求】
{extra_prompt}

【生成要求】
- 这些分享任务只给当前聊天流使用，内容要贴合这个聊天流的关系与语境。
- 每条分享任务是角色可能在这个聊天里随口提起的小事，不要写成系统通知或闹钟。
- 每条需要 time（HH:MM）、title，可选 hint、intent。
- time 应落在角色清醒时段，错开密集安排，且不得落在静默时段内。
- intent 是唤醒规划器时给规划器看的意图，说明「可以考虑分享什么」，并强调不要像播报。
- 严格输出：{"shares":[{"time":"HH:MM","title":"...","hint":"...","intent":"..."}]}
"""
