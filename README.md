# 麦麦生活（mai-life）

MaiBot 第三方插件：每天按可配置提示词生成角色日程，以及要在聊天里分享的事情和提醒时间；聊天时把最近几条日程注入规划器；到点唤醒规划器，由人格决定说不说、怎么说。

日程是角色全局一份。聊天流白名单只决定「在哪些对话里注入」和「在哪些对话里提醒」。

## 功能

- 每天定时生成日程和分享任务，提示词全部可改
- 聊天时通过 `maisaka.planner.before_request` 改写 `messages`，注入最近几条日程
- 到点调用 `ctx.maisaka.proactive.trigger` 唤醒规划器，不直接套模板发言
- 向规划器暴露两个核心工具：`mai_life_query`、`mai_life_modify`
- 日程和分享提醒各自有聊天流白名单；空白名单不生效

## 安装

把本仓库放到 MaiBot 的 `plugins/mai-life` 目录后重启。Docker 部署时，插件目录一般对应容器内 `/MaiMBot/plugins`。

启动后在 WebUI 启用插件，填写日程白名单，并在分享提醒里添加要启用的聊天流。

`config.toml` 由 Runner 根据配置模型自动生成，不要手工复制进仓库。

## 配置要点

| 分组 | 作用 |
|------|------|
| plugin.enabled | 总开关，默认关闭 |
| generation | 每日生成时刻、时区、模型、是否读人设/聊天/记忆 |
| schedule.allowed_streams | 启用日程注入与查询/修改的聊天流 |
| share.stream_profiles | 启用分享的聊天流；每项可覆盖条数和额外提示词，留空/0 则用默认值 |
| share.count_min / count_max | 聊天流未单独填写条数时的默认生成条数 |
| share.extra_prompt | 聊天流未单独填写时的默认额外提示词 |
| prompts | 日程生成、分享生成、修改、注入、唤醒意图的提示词 |

白名单写法：

- `all`
- `session:<session_id>` 或裸 `session_id`
- `<platform>:group:<group_id>`，例如 `qq:group:123`
- `<platform>:private:<user_id>`，例如 `qq:private:456`
- `<platform>:<user_id>`，例如 WebUI 私聊 `webui:webui_user_xxx`

空白名单：对应功能不会对任何聊天流生效。

### 提示词占位符

`{date}` `{weekday}` `{now}` `{timezone}` `{bot_nickname}` `{persona}` `{history}` `{knowledge}` `{yesterday_tail}` `{schedule_json}` `{share_json}` `{recent_schedule}` `{user_request}` `{wake_time}` `{sleep_time}` `{activity_count_min}` `{activity_count_max}` `{share_count_min}` `{share_count_max}` `{stream_info}` `{share_item}` `{target}` `{action}` `{extra_prompt}` `{stream_id}`

未知占位符会原样保留。配置项留空则回退到内置默认模板。

工具的 `description` 在代码里固定，改配置不会热更新工具描述。

## 命令

| 命令 | 说明 |
|------|------|
| `/mai_life_help` | 帮助 |
| `/mai_life_status` | 开关、今日条数、未提醒数量 |
| `/mai_life_generate` | 立即生成今日记录 |
| `/mai_life_show` | 查看今日日程和分享任务 |
| `/mai_life_wake` | 对当前聊天流立刻唤醒规划器（测试用） |

## 规划器工具

- `mai_life_query`：查询日程和分享时间。`date` 可选，`target` 为 `schedule` / `share` / `both`
- `mai_life_modify`：修改日程或分享时间。`target` + `action`（add / update / delete / replace_day）+ `request`

## 测试

离线：

```powershell
python -m pytest tests -q
```

联调：

1. 将插件放入 MaiBot 的 `plugins` 目录并启动
2. WebUI 启用插件，填写日程白名单，并在分享提醒的「启用的聊天流」里添加聊天流
3. 发送 `/mai_life_generate`，再用 `/mai_life_show` 查看
4. 正常聊天，检查规划器 prompt 日志是否出现「当前生活日程」
5. 问「你今天干什么」，规划器应调用 `mai_life_query`
6. 说「下午三点改成学习」，规划器应调用 `mai_life_modify`
7. 把某条分享任务的时间改到下一分钟，规划器应被唤醒，且不是固定模板发言
8. 关闭插件后后台循环应停止；白名单为空时不注入、不唤醒

## 依赖

- MaiBot >= 1.0.0（本机验证版本 1.1.4）
- maibot-plugin-sdk >= 2.7.1

## 协议

MIT
