# 功能契约：预警管理页（Alerts）

**页面**: P20 / S08 · 路由 `/alerts`（页面 key `alerts`，分组「接入」）
**设计稿**: `docs/ui-v2/06-pages-d.md` P20、`docs/ui-v2/05-patterns.md` §2 模板 C
**旧版**:
- 结构：`admin/console/index.html` `#panel-alerts`（2704–2772 行）
- 逻辑：`admin/console/console-app.js`「预警管理」段（13632–13848 行）
  `loadAlerts` / `initAlertTypeSelect` / `applyAlertChannelType` / `renderAlertChannels` /
  `alertsAddChannel` / `alertsRemoveChannel` / `alertsSave` / `alertsSendTest` /
  `alertsClearCompact` / `renderSmtpHealth`
**后端**:
- 路由：`admin/platform/alerts_router.py`：`get_config` / `put_config` / `send_test` / `bootstrap` / `_mask` / `_apply_env`
- 发送：`admin/alerting.py`：`CHANNEL_LABELS` / `channels_summary` / `config_summary` /
  `smtp_settings` / `active_channels` / `deliver` / `_send_email` / `_send_webhook` / `parse_smtp_compact`
**新版**: `admin/ui/src/pages/AlertsPage.vue` + `admin/ui/src/api/alerts.ts`

> 旧版说明原文（页头副标题，保留进 ⓘ 说明气泡）：
> 「风控哨兵判定批次封控时，通过下列渠道通知管理员。同一平台的同一业务码 10 分钟内只通知一次，避免连坐时被刷屏。」

---

## 一、区块（按旧版出现顺序，保留原中文标题）

| # | 区块 | 旧版位置 | 新版位置（锚点 id） |
|---|---|---|---|
| 1 | 页头（标题 + 说明气泡 + 发送测试 / 取消修改 / 保存配置） | `page-head` 2711–2717 | `SettingsLayout` 的 `title` / `help` / `#actions` |
| 2 | 告警规则（启用预警通知 + 去重窗口） | 2719–2726 | `SettingsCard` `#alerts-rules` |
| 3 | 通知渠道（列表 + 添加渠道） | 2728–2741 | `SettingsCard` `#alerts-channels` |
| 4 | SMTP（邮件渠道）（一条式 + 其它邮箱 / 自建 SMTP） | 2743–2772 | `SettingsCard` `#alerts-smtp` |

左侧 200px 锚点目录条目：**告警规则 / 通知渠道 / SMTP（邮件渠道）**（与卡片标题一致，滚动高亮由 `SettingsLayout` 负责）。

---

## 二、数据展示

| 位置 | 中文标签 | 来源字段 | 格式 / 规则 |
|---|---|---|---|
| 告警规则 | 启用预警通知 | `config.enabled`（缺失时回落顶层 `enabled`，再回落 `true`） | 开关；关闭后不发送任何渠道 |
| 告警规则 | 去重窗口 | `config.cooldown`（秒） | 数字输入，范围 30–86400，默认 600；提示里显示「当前 10 分钟」等可读文案 |
| 通知渠道 | 渠道数量 / 说明 | `channels.length` | 「支持多个渠道同时发送。测试会向全部渠道发送一条通知。」 |
| 通知渠道·行 | 类型图标 | `channel.type` | email → Mail；feishu → Webhook；qqbot → MessageCircle；其它 → Link（lucide 16px） |
| 通知渠道·行 | 名称 | `labels[type]` → 行内 `label` → 类型名 | 如「飞书群机器人」「QQ 机器人」「邮件（SMTP）」 |
| 通知渠道·行 | 目标 | `channel.target` | 等宽字体、单行省略、`title` 显示全文 |
| 通知渠道·行 | 启用开关 | 前端编辑态（后端无 per-channel 位，见 §八） | 默认开；关闭后显示 warn 徽章「已停用」+ 说明「保存后该渠道会被移除」 |
| 通知渠道·行 | 默认收件邮箱标记 | `config.mail_to` 与 `config.channels` 推导 | neutral 徽章「默认收件邮箱」，开关与删除均只读（§六.5） |
| 通知渠道 | 最近一次测试结果 | `POST /api/alerts/test` 的 `results` | 每行：成功/失败徽章 + 渠道名 + 目标 + 失败原因；另显示测试时间（`fmtTime`） |
| SMTP | 邮件链路体检 | `smtp.ready` / `smtp.missing` / `smtp.from` / `smtp.mail_to` / `smtp.host` / `smtp.port` / `smtp.ssl` | 就绪：ok 色「邮件链路就绪：from → mail_to（host:port SSL）」；未就绪：warn 色「邮件链路还差：<missing 列表>」 |
| SMTP | QQ 授权码（一条式） | `config.has_smtp` | **永不回显**（后端只回 `********`）；输入框 placeholder 按状态写「已设置（留空不修改，可粘贴覆盖）」/「qqSMTP:授权码 或 qqSMTP:你的邮箱@qq.com:授权码」 |
| SMTP | 收件邮箱（默认收件人） | `config.mail_to` | 原值显示 |
| SMTP | 服务器 / 端口 / 账号 / 发件人 | `config.smtp_host` / `smtp_port` / `smtp_user` / `smtp_from` | `—` 不冒充 0；端口默认 465 |
| SMTP | 密码 | `config.has_password` | **永不回显**；placeholder「已设置（留空不修改）」/「未设置（留空不修改）」 |
| SMTP | 加密（STARTTLS） | `config.smtp_starttls` | 开关；说明「465 端口自动走 SSL；其它端口按此开关走 STARTTLS（587 通常需要）」 |
| 全页 | 未保存标记 | 表单 vs 上次保存快照 | `SettingsLayout` 的 warn 徽章「有未保存的修改」+ 路由离开/关页确认 |

数字/时间格式统一走 `@/utils/format`（本页只有测试时间 `fmtTime`，无统计数字）。

---

## 三、交互

| 控件 | 旧版行为 | 新版行为（文案 / 接口） |
|---|---|---|
| 启用预警通知（开关） | 写进 PUT 的 `enabled` | 同；关闭后卡片无额外提示条，说明常驻 |
| 去重窗口（数字） | PUT 的 `cooldown`（后端 `max(30, n)`） | 前端先校验 30–86400，越界提示「保存失败：去重窗口需在 30–86400 秒之间」 |
| 保存配置（主按钮） | 一次 PUT 全部字段，成功后 `toast("预警配置已保存")` 并重载 | 同；按钮 loading；成功后 Toast「预警配置已保存」，再静默重载拿到掩码/生效渠道；失败 Toast「保存预警配置失败：<后端 detail>」 |
| 取消修改（新增） | — | 放弃未保存修改并重载；有未保存时先确认「放弃未保存的修改？」（okText「放弃修改」） |
| 重新加载（图标按钮） | 无（旧版保存后自动重载） | IconBtn「重新加载配置」，加载中显示转圈；失败 Toast「加载预警配置失败：<原因>」 |
| 发送测试（页头） | POST `/api/alerts/test`；结果用弹窗逐路列出，无渠道时提示「没有配置任何渠道，先添加一个」 | POST 同接口；结果用 Toast 汇总（见下）+ 卡片内「最近一次测试结果」逐路列出；无渠道时 error Toast「发送测试通知失败：没有配置任何渠道，先添加渠道并保存」 |
| 添加渠道 | 下拉 + 目标输入 + 「添加渠道」，类型切换只改 placeholder/说明，邮件时滚动高亮 SMTP 卡；重复渠道 Toast「该渠道已存在」 | 改 `FormDrawer`（右侧 480px，标题「添加通知渠道」，主按钮「添加渠道」）：类型下拉 + 目标输入 + 该类型的 placeholder/hint；切换类型清空目标；重复渠道在字段下方报「该渠道已存在」；成功后 Toast「已添加渠道，保存配置后生效」并关闭抽屉 |
| 目标输入校验 | 非邮件必须 http(s):// | email：每个逗号分隔项都必须是邮箱；feishu：必须 http(s)://；qqbot：必须 http(s):// 且带 `?group=群号`；其它类型：非空即可 |
| 渠道启用开关 | 无 | 关闭显示「已停用」，保存时该渠道从 PUT 的 `channels` 里剔除（§八.2） |
| 删除渠道（行内） | 「移除」只改本地数组，保存后生效，无确认 | 「删除」危险按钮：确认「删除「<渠道名>」渠道？」正文「删除后风控告警不再发送到该渠道；点「保存配置」后写入后端。」okText「删除」；确认后仅从本地列表移除，仍需保存 |
| 发送测试（行内） | 无（只有页头） | 与页头同一个 POST（后端一次发全部渠道）；按钮 title/说明「测试会向全部渠道发送一条通知」 |
| QQ 授权码「清除」 | 只打标记，保存时发 `smtp: "__clear__"`，输入框 placeholder 变「保存后即清空已存授权码」 | 危险按钮「清除授权码」，确认「清除已保存的 QQ 授权码？」正文「保存配置后生效。清除后邮件渠道需要改用下面的服务器 / 账号 / 密码发信。」；确认后显示 warn 徽章「待清除」+「撤销」按钮；PUT `smtp = "__clear__"` |
| SMTP 密码留空 | 空串不覆盖（后端只在非空时写 env） | 同：输入框留空则 PUT `smtp_password = ""`，后端保留原密码；placeholder 写明「留空不修改」；掩码串 `********` 永不回写 |
| 表单同步 | 直接改 DOM | 单一表单对象 + 上次保存快照比对（`isConfigDirty`）判定未保存 |

**Toast 文案（`summarizeTestResult`）**
- 全部成功：`测试通知已发送：N 个渠道全部成功`（success）
- 部分失败：`测试通知部分失败：M/N 个渠道（<渠道名>：<error>；…）`（warning）
- 全部失败：`发送测试通知失败：M/N 个渠道（…）`（error）
- 没有渠道：`发送测试通知失败：没有配置任何渠道，先添加渠道并保存`（error）

---

## 四、接口

> 所有请求都经 `@/api/http` 的 `http<T>()`（自动带 `X-CSRF-Token` 与 `X-Platform`），页面不直接 fetch。
> 后端位置均以 `文件:函数` 标注。

### 4.1 GET `/api/alerts/config`

- **调用时机**：进入页面（首次加载）、保存成功后静默重载、点「重新加载」、点「取消修改」、错误重试。
- **参数**：无。
- **后端**：`admin/platform/alerts_router.py:get_config`（掩码 `_mask`、渠道摘要 `alerting.channels_summary`、邮件体检 `alerting.config_summary`）。
- **响应**：

```jsonc
{
  "config": {                       // system_settings["alerts:config"] 的落库值，可能为 {}（从未保存）
    "enabled": true,
    "cooldown": 600,
    "channels": [{ "type": "feishu", "target": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx" }],
    "smtp_host": "smtp.qq.com",
    "smtp_port": 465,
    "smtp_user": "me@qq.com",
    "smtp_password": "********",    // 只在有密码时出现，恒为掩码
    "has_password": true,
    "smtp_from": "me@qq.com",
    "smtp_starttls": true,
    "smtp": "********",             // 只在有 qqSMTP:授权码 时出现
    "has_smtp": true,
    "mail_to": "me@qq.com"
  },
  "channels": [                     // 真正会发的渠道（active_channels：可能含由 mail_to 自动补的邮件渠道）
    { "type": "feishu", "label": "飞书群机器人", "target": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx", "enabled": true }
  ],
  "labels": { "email": "邮件（SMTP）", "feishu": "飞书群机器人", "qqbot": "QQ 机器人" },
  "enabled": true,                  // 顶层回声，与 config.enabled 同源
  "smtp": {                         // alerting.config_summary()
    "host": "smtp.qq.com", "port": 465, "user": "me@qq.com", "from": "me@qq.com",
    "mail_to": "me@qq.com", "ssl": true, "compact": false,
    "has_password": true, "ready": true, "missing": []
  }
}
```

- **本页用到的字段**：`config.enabled/cooldown/channels/smtp_host/smtp_port/smtp_user/smtp_from/smtp_starttls/mail_to/has_password/has_smtp`、`channels[]`、`labels`、`smtp.ready/missing/from/mail_to/host/port/ssl`。
- **不消费但可用于新版**：`smtp.compact`（是否走了 QQ 一条式，可作为说明文案）、`smtp.has_password`、`enabled`（顶层回声）。

### 4.2 PUT `/api/alerts/config`

- **调用时机**：「保存配置」。
- **后端**：`admin/platform/alerts_router.py:put_config`（写 `system_settings` → `_apply_env` 立刻覆盖 `os.environ` → `alerting.reset_cooldown()`）。
- **请求体（全量，缺省字段会用后端默认值覆盖，所以每次都必须发全部字段）**：

```jsonc
{
  "enabled": true,
  "cooldown": 600,                 // 后端 max(30, n)
  "channels": [{ "type": "feishu", "target": "https://…" }],
  "smtp_host": "smtp.qq.com",
  "smtp_port": 465,
  "smtp_user": "me@qq.com",
  "smtp_password": "",             // 空串 = 不修改；"********" 不会被后端当真值，但前端禁止回写掩码
  "smtp_from": "me@qq.com",
  "smtp_starttls": true,
  "smtp": "",                      // 空串 = 不修改；"__clear__" = 清空已存授权码；"********" 视同未改
  "mail_to": "me@qq.com"
}
```

- **响应**：`{ "ok": true, "channels": [ …channels_summary… ] }`（新版忽略 `channels`，直接重载 GET）。
- **后端行为要点**：`channels` 只保留 `type` 与 `target` 都非空的项；`smtp_password` 空串不写 env；`smtp` 仅在非空且非掩码时覆盖，`__clear__` 才清空；`cooldown` 下限 30。

### 4.3 POST `/api/alerts/test`

- **调用时机**：页头「发送测试」、渠道行「发送测试」。**使用已保存的配置**（后端 `_load(db)` + `_apply_env`），未保存的修改不参与。
- **后端**：`admin/platform/alerts_router.py:send_test` → `alerting.deliver(event)`。
- **响应**：

```jsonc
{
  "ok": false,                     // 全部渠道成功且至少一个渠道才为 true
  "title": "[风控提醒] 测试 出现封控信号",
  "text": "这是一条来自 MiraiHub 的测试通知。……",
  "level": "info",
  "results": [                     // 每个生效渠道一条
    { "type": "feishu", "target": "https://…", "ok": true, "error": "" },
    { "type": "email", "target": "me@qq.com", "ok": false, "error": "SMTPAuthenticationError: (535, b'Error: authentication failed')" }
  ],
  "smtp": { …同 4.1 的 smtp 体检… }
}
```

- **本页用到的字段**：`results[].type/target/ok/error`、`smtp`（顺带刷新体检行）。
- **说明**：后端一次向所有生效渠道发送，没有单渠道测试接口；行内按钮与页头按钮行为一致。

---

## 五、跨页联动

- **不依赖平台上下文**：`router/pages.ts` 的 `PLATFORM_SCOPED_KEYS` 不含 `alerts`，旧版 `loadAlerts` 也不在 `wb-platform-change` 重载清单内。因此本页不订阅 `usePlatformStore().onChange`，顶栏不显示平台选择器（与 03-shell 一致）。
- **无筛选/分页**：本页没有列表筛选与分页，不使用 `useTableQuery`，不改路由 query。
- **告警消费方**：概览（P1）与风控哨兵（P2）的告警横幅/止损按钮属于各自页面；本页只负责「谁来收」。
- **全局事件**：无（旧版也没有自定义事件）。
- **跳转**：无跨页链接（P20 设计稿未要求）。SMTP 说明里出现的 `ADMIN_ALERT_SMTP` 等变量名以等宽文本呈现，不做跳转。

---

## 六、边界情况

1. **首次加载**：三张卡片位置显示骨架块（`--surface-2` 灰块，1 种样式、不闪烁），页头操作除「重新加载」外禁用。
2. **刷新中**：保留旧数据，只有「重新加载」图标转圈；保存/测试按钮按状态禁用，不闪屏。
3. **空**：
   - 渠道为空 → `Empty`「还没有配置通知渠道。风控告警只会记录在面板里。」+「添加渠道」按钮（与旧版 `alerts-empty` 文案一致）。
   - `config = {}`（从未保存过）→ 表单为默认值：开关开、去重 600 秒、渠道空、端口 465、STARTTLS 开；`has_password/has_smtp` 为假 → placeholder 显示「未设置（留空不修改）」。
4. **错误**：GET 失败 → 卡片上方 `ErrorBlock`（标题「加载预警配置失败：<原因>」，可展开后端 detail，带「重试」）；已加载过再失败 → 保留数据 + error Toast。PUT/POST 失败 → error Toast，表单内容不丢。
5. **后端没有 per-channel 启用位**：`config.channels` 只有 `{type,target}`；`channels_summary().enabled` 只是「target 非空」。因此「启用开关」是**编辑态**：关闭后保存 = 从 `channels` 中移除（UI 明示「保存后该渠道会被移除」），不是持久化的停用位。
6. **默认收件邮箱自动补渠道**：`alerting.active_channels()` 在 `mail_to` 非空且没有 email 渠道时补一条 `{"type":"email","target":mail_to}`。该行显示 neutral 徽章「默认收件邮箱」，开关与删除禁用，提示「清空收件邮箱后才会消失」；它不写回 PUT 的 `channels`（避免把派生行固化成显式渠道）。
7. **一条式 vs 五元组**：`ADMIN_ALERT_SMTP=qqSMTP:授权码` 与显式 `host+password` 同时存在时，后端优先显式值（`smtp_settings()`）；页面说明文案照此写。
8. **密码无法单独清空**：后端只支持 `smtp = "__clear__"`，没有「清空 smtp_password」的入口（空串 = 不修改）。本页只提供「清除授权码」，密码清空留待后端支持，契约不假装实现。
9. **cooldown** 后端下限 30 秒、无上限；前端限制 30–86400（与旧版 `min=30 max=86400` 一致）。
10. **未知渠道类型**：`labels` 里没有的 type 用类型名兜底显示，行仍可删除；类型下拉只提供后端 `labels` 已知的三种（`feishu/qqbot/email`，顺序同旧版）。
11. **掩码**：`smtp_password` / `smtp` 的 `********` 永不回填输入框（只作为 placeholder 依据），`buildAlertConfigBody` 也会把 `********` 清成空串兜底。
12. **未保存离开**：`SettingsLayout` 的 `dirty` 由 `isConfigDirty(form, saved)` 汇总；路由离开与关标签页由外壳确认。
13. **手机端（≤640px）**：锚点目录变横向滚动条；渠道行由「图标 + 名称/目标 + 操作」纵向堆叠，操作换行到下一行；SMTP 两组字段变单列。
14. **深色**：只使用 `tokens.css` 变量，无硬编码十六进制颜色。

---

## 七、旧版有但设计稿没写到的功能（已保留）

| 功能 | 旧版位置 | 新版位置 |
|---|---|---|
| QQ 邮箱「一条式」授权码（`qqSMTP:授权码`） | `#smtpCompact`（2750–2754）、`alertsClearCompact` | SMTP 卡「QQ 邮箱一条式（推荐）」 |
| 一条式授权码「清除」（`__clear__` 标记，保存时生效） | `alertsClearCompact` 13645–13652 | SMTP 卡危险按钮「清除授权码」（带确认与撤销） |
| 收件邮箱（默认收件人） | `#smtpMailTo` 2756 | SMTP 卡「收件邮箱（默认收件人）」 |
| 邮件链路体检（还差哪一步 / QQ 授权码反查不到账号的说明） | `renderSmtpHealth` 13731–13748 | SMTP 卡顶部状态行（`describeSmtpHealth`） |
| STARTTLS 开关 | `#smtpStarttls` 2769 | SMTP 卡「加密」开关 |
| 渠道目标输入随类型切换 placeholder / 说明 | `applyAlertChannelType` 13674–13694 | 抽屉内 `alertChannelMeta(type)` 动态 placeholder + hint |
| 邮件类型时高亮 SMTP 卡并滚动定位 | `applyAlertChannelType`（`is-highlight` + scrollIntoView） | 抽屉 hint 直接写「下一步去 SMTP 区填服务器」；不做滚动高亮（模板 C 已有锚点目录） |
| 重复渠道拦截 | `alertsAddChannel` 13764–13780 | 抽屉字段下方报「该渠道已存在」 |
| 测试结果逐路展示 | `alertsSendTest` 的弹窗 `<pre>` | Toast 汇总 + 卡片内「最近一次测试结果」列表 |
| 多个渠道同时发送 | `deliver()` 循环 `active_channels` | 保留（后端行为，页面说明中写明） |

---

## 八、新版已知取舍（必读）

1. **单一「保存配置」**，不做每卡独立保存。原因：`PUT /api/alerts/config` 是**全量覆盖**且未给字段会用默认值（`enabled=true` / `cooldown=600` / `channels=[]` / `smtp_host=""`）。按卡片部分提交会把其他区块清空，因此三张卡片共用一个提交按钮（与旧版「保存配置」一致），`SettingsCard` 用 `hide-save`/`hide-reset`。
2. **渠道启用开关 = 保存时是否保留**（§六.5 的后果）：后端没有停用位，关闭并保存等价于删除；UI 在行内明确写出这一点，避免误解。
3. **派生渠道只读**：由 `mail_to` 自动补的邮件渠道不可删除、不可停用，只能去 SMTP 卡清空收件邮箱（§六.6）。
4. **测试用已保存配置**：`POST /api/alerts/test` 不接收请求体，未保存的修改不参与；页面在渠道卡说明里写明。
5. **渠道删除 / 清除授权码**都只改本地编辑态，需点「保存配置」落库（与旧版一致）。
6. **没有单渠道测试**：行内「发送测试」与页头是同一个接口，Toast 会列出每一路的结果。

---

## 实现对照

| 契约功能 | 新版位置 | 状态 |
|---|---|---|
| 页头标题 + 旧版副标题（进 ⓘ 气泡） | `AlertsPage.vue` → `SettingsLayout :title/:help` | 已实现 |
| 发送测试（页头） | `AlertsPage.vue` → `testSend()` + `summarizeTestResult` | 已实现 |
| 取消修改（放弃未保存） | `AlertsPage.vue` → `discard()` + `useConfirm` | 已实现 |
| 保存配置（全量 PUT） | `AlertsPage.vue` → `save()` + `buildAlertConfigBody` | 已实现 |
| 重新加载 | `AlertsPage.vue` → `reload()`（IconBtn） | 已实现 |
| 未保存标记 + 离开确认 | `isConfigDirty` + `SettingsLayout :dirty` | 已实现 |
| 启用预警通知开关 | `AlertsPage.vue` 告警规则卡 → `form.enabled` | 已实现 |
| 去重窗口（30–86400 秒，默认 600） | `AlertsPage.vue` 告警规则卡 → `cooldownValue` + `clampCooldown` | 已实现 |
| 通知渠道列表（图标 / 名称 / 目标 / 开关 / 发送测试 / 删除） | `AlertsPage.vue` 渠道卡 → `.channel-row` | 已实现 |
| 空渠道状态 | `AlertsPage.vue` → `Empty` + 「添加渠道」 | 已实现 |
| 添加渠道抽屉（类型切换动态字段） | `AlertsPage.vue` → `FormDrawer` + `alertChannelMeta` | 已实现 |
| 目标校验（邮箱 / http(s) / ?group=） | `validateChannelTarget` | 已实现 |
| 重复渠道拦截 | `submitAdd()` → `channelKey` 去重 | 已实现 |
| 行内发送测试 | `AlertsPage.vue` → `testSend()`（同一 POST，说明写明发全部渠道） | 已实现 |
| 最近一次测试结果逐路展示 | `AlertsPage.vue` → `lastTest.results` + `Badge` | 已实现 |
| 渠道启用开关（保存时剔除） | `buildAlertConfigBody` 过滤 `enabled===false` | 已实现（语义见 §八.2） |
| 默认收件邮箱派生渠道（只读标记） | `toChannelViews` → `auto` + 徽章/禁用 | 已实现 |
| 删除渠道确认 | `removeChannel()` + `useConfirm` | 已实现 |
| QQ 授权码一条式（不回显） | SMTP 卡「QQ 邮箱一条式」+ `alertFormFromPayload`（`smtpCompact: ''`） | 已实现 |
| 清除授权码（`__clear__` + 撤销） | SMTP 卡 `clearCompactCode()` / `undoClear()` + `buildAlertConfigBody` | 已实现 |
| 收件邮箱（默认收件人） | SMTP 卡 → `form.mailTo` | 已实现 |
| 邮件链路体检状态行 | `describeSmtpHealth` + SMTP 卡顶部 | 已实现 |
| 服务器 / 端口 / 账号 / 发件人 | SMTP 卡「其它邮箱 / 自建 SMTP」 | 已实现 |
| 密码留空不修改（placeholder 写明、不回显） | SMTP 卡密码字段 + `sanitizeSecret` | 已实现 |
| 加密（STARTTLS）开关 | SMTP 卡「加密」 | 已实现 |
| 四种状态（首次加载 / 刷新 / 空 / 错误） | `AlertsPage.vue` 骨架 / `loading` / `Empty` / `ErrorBlock` | 已实现 |
| 不依赖平台上下文、不同步路由 query | 未订阅 `usePlatformStore`、未用 `useTableQuery` | 已实现（§五说明理由） |
| 手机端与深色 | `AlertsPage.vue` 媒体查询 + 仅用 tokens 变量 | 已实现 |
| 单元测试（响应形状 → 展示数据） | `admin/ui/tests/page-alerts.spec.ts` | 已实现 |
