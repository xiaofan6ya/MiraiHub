# 功能契约：IP 管理页（Ip）

**页面 key**：「ip」　**路由**：「/ip」（hash：「/admin/v2/#/ip」）　**设计稿**：「06-pages-c.md」P13　**模板**：B（列表管理型，三页签）
**旧版位置**：「admin/console/index.html」#panel-ip（1845–1888 行）+「admin/console/ip-management.js」（全文 142 行）+「admin/console/usage-ip.js」（IP 单元格 / 复制 IP 的全局事件）
**后端**：「admin/routers/ip_management.py」全文（ip_activity / list_blocked_ips / block_ip / patch_blocked_ip / delete_blocked_ip / devices / update_device）；拦截实现「admin/ip_access.py」（IPBlockMiddleware / is_blocked_ip）
**新版文件**：「src/pages/IpPage.vue」、「src/api/ip.ts」、「tests/page-ip.spec.ts」

三个页签与路由 query 的对应：「?view=active」（默认）/「?view=blocked」/「?view=devices」，刷新与分享链接不丢。

---

## 一、区块（按旧版出现顺序）

| # | 旧版区块 | 旧版中文标题 / 文案 | 新版位置 |
|---|---|---|---|
| 1 | 页头工具栏 | 副标题「封禁后，该 IP 的全部 /v1 与 /gw 请求直接返回 403「该 IP 已被封禁」，在鉴权和转发上游之前拦截。」+「刷新」 | PageHeader 标题「IP 管理」+ help（HelpTip 收纳副标题原文）+ #actions：「刷新」（quiet）+「封禁 IP」（primary） |
| 2 | 统计卡 #ipActivitySummary | 活跃 IP / 入口请求 / 封禁 IP / 设备标识 | StatStrip（4 格，页签上方，三个页签共享） |
| 3 | 页签（新增） | 活跃 IP / 封禁名单 / 设备关联（P13 要求） | Segmented，绑定 ?view= |
| 4 | 活跃 IP 筛选表单 #ipUsageFilters | 流量筛选（全部 / 高频·低 Token / 高频·低积分）、至少请求、平均上限、IP 地址、「筛选」按钮 | FilterBar（无「查询」按钮，300ms 防抖自动查）：IP 搜索框 + 时间范围 + 状态 + 流量筛选 + 至少请求 + 平均上限 |
| 5 | 页面说明 ul.ip-notes 四条 | 「入口次数包含鉴权失败、限流和封禁请求…」等 | 收进 PageHeader.help（原文保留）+ 各列 HelpTip |
| 6 | 活跃 IP 表 | IP 地址 / 请求次数 / 失败次数 / 封禁拦截 / 积分 / Token / 最近请求 / 状态 / 操作 | Card flush 内 DataTable（data-ip-sort 的 7 个可排序列全部保留） |
| 7 | 分页 #ipActivityPager | 旧版固定每页 100 | DataTable 远程分页 20/50/100（服务端 limit/offset） |
| 8 | 封禁名单 h3 + 说明 | 「封禁名单」+「封禁后该 IP 的全部 /v1 与 /gw 请求直接 403」 | 页签 2 的区块标题 + Card 顶部说明行 |
| 9 | 封禁名单表 | IP 地址 / 备注 / 状态 / 更新时间 / 操作（解除封禁·重新封禁 / 删除记录） | 页签 2 DataTable：IP·网段 / 原因 / 封禁时间 / 到期 / 状态 / 操作 |
| 10 | 设备关联 h3 + 说明 + panel-note | 「设备关联与封禁」+「按标识独立封禁」+「设备 ID、指纹和 MAC 均由客户端上报，未经硬件验证…最多展示最近 2000 条关联」 | 页签 3 区块标题 + 说明行 + 旧版说明原文进 HelpTip |
| 11 | 设备卡片列表 #ipDevices | 每种标识一张卡：类型 / 值 / 状态 /「客户端上报 · 未验证」/ 关联 IP / 封禁按钮 | 页签 3 DataTable：设备标识 / 关联 IP / 关联 Key / 最近活动 / 状态 / 操作 |
| 12 | 顶部封禁表单 #ipBlockForm（IP 地址 + 备注 + 封禁 IP） | 内联表单常驻页面 | FormDrawer（右侧 480px）：IP 或 CIDR / 原因 / 时长 |

> 设计稿 P13 把「封禁表单」放进抽屉、并新增「时长」与「到期」列；旧版为常驻内联表单、只有 IP + 备注两个字段。字段名与文案沿用旧版（IP / 备注→原因），时长按 P13 新增。

---

## 二、数据展示

### 2.1 统计条（StatStrip，三个页签共享）

| 卡 | 中文标签 | 来源字段 | 格式 / 着色 |
|---|---|---|---|
| 1 | 活跃 IP | GET /api/ip/activity → total_ips | fmtInt；脚注「所选时间内有入口请求的 IP 数」 |
| 2 | 入口请求 | 同接口 total_requests | fmtCompact；脚注「含鉴权失败、限流与封禁拦截」 |
| 3 | 封禁 IP | 同接口 blocked_count | fmtInt；> 0 时 tone=danger |
| 4 | 设备标识 | GET /api/ip/devices → items.length | fmtInt；脚注「客户端上报 · 未验证」 |

### 2.2 页签 1「活跃 IP」表格列（DataTable，服务端分页 / 排序）

| 列 | 中文标签 | 来源字段 | 格式 / 着色 | 排序参数 | 手机位 |
|---|---|---|---|---|---|
| 1 | IP 地址 | items[].ip | 等宽；ip === current_ip 时下方 12px --fg-muted 标注「当前访问 IP」；悬停气泡展示客户端识别（见 2.3） | sort=ip | title |
| 2 | 请求次数 | requests | fmtInt 右对齐等宽 | sort=requests | meta |
| 3 | 失败次数 | failed | fmtInt；>0 用 --danger 着色（旧版即红） | sort=failed | meta |
| 4 | 封禁拦截 | blocked_requests | fmtInt；>0 用 --warn | sort=blocked_requests | meta |
| 5 | 积分 | credits / avg_credits | 主值 fmtCredit(credits)；次行「均 <avg_credits.toFixed(4)>」（空值 —） | sort=credits | meta |
| 6 | Token | tokens / avg_tokens / known_usage | 主值 fmtCompact(tokens)；次行「均 <fmtCompact(avg_tokens)>」；次行「N 次有用量」 | sort=tokens | meta |
| 7 | 最近请求 | last_seen | fmtTime，title=fmtFullTime；空 — | sort=last_seen | meta |
| 8 | 状态 | blocked | Badge：true → danger「已封禁」；false → ok「允许」 | — | subtitle |
| 9 | 操作 | — | RowActions：「封禁」/「解除封禁」（inline）、「查看日志」（⋯ 内，跳 /logs?client_ip=） | — | actions |

- 行键：ip（全表唯一）。
- 默认排序：sort=requests&order=desc（旧版默认）。
- 排序三态：升 → 降 → 取消（取消后回默认 requests desc，与旧版「同一表头再点切方向」一致）。
- 分页：服务端 limit/offset，total_ips 作为 itemCount；每页 20/50/100，越界自动回退。

### 2.3 IP 列的客户端识别（旧版 ipTooltip 原样迁移）

来自 items[].client_details（user_agent / device_ids / fingerprints / macs / identity_source）：

```
客户端（按 UA 识别）：<Claude 客户端 | Codex 客户端 | OpenAI SDK | Anthropic SDK | curl | Python HTTPX | Python Requests | 浏览器 | 未知>
UA：<原文，空则「未记录（更新后新请求开始采集）」>
设备 ID：<逗号分隔，空则「未上报」>
指纹：<…>
MAC：<…>
UA 和设备标识由客户端上报，可被修改；服务端无法跨公网获取网卡 MAC。
最近已认证请求 · 每类设备标识最多显示 3 个（后端 _client_details 截断）
```

UA 识别顺序与旧版完全一致（Claude → Codex → OpenAI → Anthropic → curl → Python HTTPX → Python Requests → 浏览器 → 未知）。

### 2.4 页签 2「封禁名单」表格列

说明行（Card 顶部）：「封禁后该 IP 的全部 /v1 与 /gw 请求直接 403」+ HelpTip「到期时间由 blocked_ips.expires_at 决定，为空表示永久；已过期的记录后端视为未封禁」。

| 列 | 中文标签 | 来源字段 | 格式 / 着色 | 手机位 |
|---|---|---|---|---|
| 1 | IP / 网段 | items[].ip | 等宽 | title |
| 2 | 原因 | note | 单行省略 + title；空 — | subtitle |
| 3 | 封禁时间 | created_at | fmtTime + title=fmtFullTime | meta |
| 4 | 到期 | expires_at | 空 →「永久」（--fg-2）；有值 → fmtExpiry + expiryTone 着色 | meta |
| 5 | 状态 | enabled | Badge：true → danger「已封禁」；false → neutral「已解除」 | meta |
| 6 | 操作 | — | RowActions：「解除封禁」/「重新封禁」（inline）、「删除记录」（⋯，danger） | actions |

### 2.5 页签 3「设备关联」表格列

说明行：「按标识独立封禁」+ 旧版 panel-note 原文进 HelpTip（「设备 ID、指纹和 MAC 均由客户端上报，未经硬件验证。相同标识换 IP 后仍会拦截；未上报或重置标识的设备无法可靠关联。普通 UA 不作为封禁依据。最多展示最近 2000 条关联。」）。

| 列 | 中文标签 | 来源字段 | 格式 / 着色 | 手机位 |
|---|---|---|---|---|
| 1 | 设备标识 | kind + value | 上行 12px --fg-muted：设备 ID / 客户端指纹 / MAC / 浏览器指纹；下行等宽显示 value | title |
| 2 | 关联 IP | ips[] | Chip 列表（最多 3 个，多出的显示 +N）；空 — | meta |
| 3 | 关联 Key | 无字段 | — + HelpTip「/api/ip/devices 不返回关联 Key；按 Key 追查请在用量日志筛该 IP」（见 §七） | meta |
| 4 | 最近活动 | last_seen | fmtRelative + title=fmtFullTime | meta |
| 5 | 状态 | blocked | Badge：true → danger「已封禁」；false → ok「允许」；另加 neutral「客户端上报」 | subtitle |
| 6 | 操作 | — | RowActions：「封禁设备标识」/「解除设备封禁」 | actions |

- 行键：kind + ':' + digest（后端按 (kind, digest) 唯一）。
- 默认顺序 = 后端顺序（ClientIdentitySeen.last_seen DESC）；支持按 最近活动 / 状态 / 设备标识 客户端排序。

### 2.6 筛选栏与路由 query

页签 1（useTableQuery('ip')）：

| 控件 | 中文标签 | query | 取值 | 语义 / 后端参数 |
|---|---|---|---|---|
| 时间范围 | 时间范围 | days | 1/7/30/90 | days=1…90（默认 1 = 近 24 小时，含起始小时） |
| 搜索 | 搜索 IP | q | 文本（300ms 防抖） | ip=<值>（精确 IP → 归一后等值；否则 contains 模糊） |
| 状态 | 状态 | status | ''/blocked/allowed | 客户端过滤当前页（后端无该参数，见 §七） |
| 流量筛选 | 流量筛选 | usage | ''/tokens/credits | 选 tokens → max_avg_tokens；credits → max_avg_credits |
| 至少请求 | 至少请求 | min | 整数 ≥ 0（默认 0） | min_requests |
| 平均上限 | 平均 Token ≤ / 平均积分 ≤ | max | 数字（默认 100 / 0.01） | 配合 usage 生效 |

页签 2：bq（搜索 IP / 原因）、bs（状态 ''/enabled/disabled）。
页签 3：dq（搜索标识 / IP）、ds（状态 ''/blocked/allowed）。
三个页签共用 page/size/sort/order，并各自把生效条件写成可关闭的 chips；「重置」只清当前页签的条件并回第 1 页。

### 2.7 四种状态

| 状态 | 表现 |
|---|---|
| 首次加载 | 表格区 6 行 NSkeleton；StatStrip 数字位置显示 —；aria-busy=true |
| 刷新中 | 保留旧数据；DataTable 顶部 2px 进度条；刷新 按钮转圈 |
| 空 | 活跃：Empty「所选时间内暂无请求」+（有筛选时）「清除筛选」；封禁：Empty「暂无封禁记录」+「封禁 IP」；设备：Empty「暂无客户端上报的设备标识」 |
| 错误 | 首次失败：ErrorBlock（「加载 IP 活动失败：<原因>」/「加载封禁名单失败：…」/「加载设备关联失败：…」，detail 可展开 + 重试）；已有数据时刷新失败：保留旧数据 + 错误 Toast |

三个页签的取数互不影响（局部失败不阻塞其他区块）。

---

## 三、交互

### 3.1 页头

| 动作 | 文案 | 行为 | 成功 / 失败 |
|---|---|---|---|
| 刷新 | 刷新 | 重新请求当前页签数据 + 统计（活跃页签同时刷新封禁/设备统计） | 失败：错误 Toast + 旧数据保留 |
| 封禁 IP | 封禁 IP | 打开空白 FormDrawer | — |

### 3.2 活跃 IP 行操作

| 动作 | 文案 | 行为 | 成功 / 失败 |
|---|---|---|---|
| 封禁 | 封禁 | 打开抽屉并预填该行 IP（可补原因 / 时长），提交走 POST /api/ip/blocked | Toast「已封禁 IP <ip>」；失败「封禁 IP 失败：<原因>」 |
| 解除封禁 | 解除封禁 | PATCH /api/ip/blocked/{blocked_id} body {enabled:false}（无二次确认，与旧版一致） | Toast「已解除封禁」；失败「解除封禁失败：<原因>」 |
| 查看日志 | 查看日志（⋯） | router.push({path:'/logs', query:{client_ip: ip}})（旧版 data-usage-ip 行为：切到日志页并按该 IP 过滤） | — |

### 3.3 封禁名单行操作

| 动作 | 文案 | 行为 | 成功 / 失败 |
|---|---|---|---|
| 解除封禁 | 解除封禁 | PATCH {enabled:false} | Toast「已解除封禁」 |
| 重新封禁 | 重新封禁 | useConfirm（标题「重新封禁 <ip>？」正文「该 IP 的全部 /v1 请求将立即返回 403。」按钮 取消/重新封禁 danger）→ PATCH {enabled:true} | Toast「已重新封禁」 |
| 删除记录 | 删除记录 | useConfirm（标题「删除 <ip> 的封禁记录？」正文「删除后该 IP 将允许访问网关。」按钮 删除 danger）→ DELETE | Toast「已删除封禁记录」 |

### 3.4 设备关联行操作

| 动作 | 文案 | 行为 | 成功 / 失败 |
|---|---|---|---|
| 封禁设备标识 | 封禁设备标识 | useConfirm（标题「封禁设备标识？」正文「确认封禁 <value>？携带这个上报标识的请求换 IP 后仍返回 403。该标识未经硬件验证。」）→ POST /api/ip/devices {kind, value, blocked:true, note} | Toast「已封禁设备标识」 |
| 解除设备封禁 | 解除设备封禁 | 同上 blocked:false（无二次确认） | Toast「已解除设备封禁」 |

### 3.5 封禁抽屉（FormDrawer 480px，FormField）

| 字段 | 控件 | 必填 | 校验 / 说明 |
|---|---|---|---|
| IP 或 CIDR | NInput（placeholder「IPv4 或 IPv6」，maxlength 64） | 是 | 空 →「请填写要封禁的 IP 地址」；非法 →「请输入有效的 IPv4 或 IPv6 地址」（与后端 400 同文案）；CIDR →「CIDR 网段封禁需要后端支持，当前接口只接受单个 IP 地址」（后端 _normalize_ip 用 ipaddress.ip_address，网段会 400，见 §七） |
| 原因 | NInput（placeholder「例如：异常刷请求」，maxlength 255） | 否 | 去空白后提交为 note |
| 时长 | NSelect：永久 / 1 小时 / 1 天 / 7 天 / 自定义 | 否（默认永久） | 选「自定义」时出现 NDatePicker type=datetime；未选 →「请选择到期时间」；早于当前 →「到期时间必须晚于当前时间」 |
| 到期时间 | NDatePicker type=datetime（仅自定义） | 条件必填 | 转 ISO 后作为 expires_at 提交 |

- 底部操作栏：取消 + 封禁 IP；提交中 loading 且禁用。
- 提交前 useConfirm：标题「封禁 <ip>？」正文「该 IP 的全部 /v1 与 /gw 请求将立即返回 403。」；ip === current_ip 时追加「这是当前访问 IP，封禁后也会影响你从此 IP 发起的 API 请求。」（旧版 confirmBlock 原文）。
- 成功：Toast「已封禁 IP <ip>」、关闭抽屉、刷新活跃与封禁两个页签。
- 失败：Toast「封禁 IP 失败：<原因>」（后端 400「请输入有效的 IPv4 或 IPv6 地址」/ 409「该 IP 状态发生冲突，请重试」），抽屉保持打开、内容不丢。
- 提交体：{ ip, note, expires_at }。后端 BlockedIPIn 目前只有 ip/note，多余字段被 Pydantic 忽略（见 §七）。

### 3.6 页签切换

Segmented 切换 → router.replace({query:{...route.query, view: next}})，同时清掉 page（回到第 1 页）；刷新页面 / 分享链接恢复页签。

---

## 四、接口

### 4.1 GET /api/ip/activity — 活跃 IP

- 后端：admin/routers/ip_management.py:ip_activity（15s 结果缓存 _activity_cache，flush() 后聚合）。
- 参数：days(1–90) / sort(requests|failed|blocked_requests|credits|tokens|last_seen|ip) / direction(asc|desc) / limit(1–500) / offset(≥0) / min_requests(≥0) / max_avg_tokens / max_avg_credits / ip。
- 调用时机：进入页面、刷新、封禁/解封后、筛选/排序/分页变化；每次变化即查（请求带 AbortController，只保留最后一次）。
- 响应：

```json
{
  "days": 1,
  "sort": "requests",
  "direction": "desc",
  "items": [
    {
      "ip": "203.0.113.7",
      "requests": 1842,
      "failed": 37,
      "blocked_requests": 0,
      "credits": 128.4567,
      "tokens": 482013,
      "avg_tokens": 261.7,
      "avg_credits": 0.0697,
      "known_usage": 1805,
      "last_seen": "2026-11-23T03:12:44",
      "blocked": false,
      "blocked_id": null,
      "client_details": {
        "user_agent": "claude-cli/1.0.60 (external, cli)",
        "device_ids": ["dev-9f2a"],
        "fingerprints": ["fp-77c1"],
        "macs": [],
        "identity_source": "client_reported"
      }
    }
  ],
  "total_ips": 128,
  "total_requests": 20481,
  "current_ip": "203.0.113.7",
  "offset": 0,
  "limit": 20,
  "blocked_count": 3
}
```

| 字段 | 类型 | 含义 | 新版用途 |
|---|---|---|---|
| items[].ip | string | 归一后的客户端地址 | 列 1、行键 |
| requests | int | 入口请求数（含鉴权失败 / 限流 / 封禁拦截） | 列 2、排序 |
| failed | int | 失败数（按 HTTP 状态 / error_kind 非成功） | 列 3 |
| blocked_requests | int | 被封禁拦截的请求数（IPRequestBucket.blocked） | 列 4 |
| credits | float | 成功请求积分合计 | 列 5 主值 |
| avg_credits | float \| null | 成功请求平均积分 | 列 5 次行 |
| tokens | int | 成功请求 Token 合计 | 列 6 主值 |
| avg_tokens | float \| null | 平均 Token | 列 6 次行 |
| known_usage | int | 有 Token 记录的请求数 | 列 6「N 次有用量」 |
| last_seen | string \| null | 最近请求（UTC ISO） | 列 7 |
| blocked | bool | 当前是否在启用中的封禁名单里 | 列 8 |
| blocked_id | int \| null | 封禁记录主键（用于解封） | 行操作 |
| client_details.* | object | UA + 每类最多 3 个设备标识 | 列 1 气泡 |
| total_ips | int | 满足筛选的 IP 总数（忽略分页窗口） | 分页 itemCount、统计卡 1 |
| total_requests | int | 满足筛选的请求总数 | 统计卡 2 |
| current_ip | string | 当前管理员访问 IP | 列 1 标注、封禁确认追加文案 |
| blocked_count | int | 启用中的封禁条数 | 统计卡 3 |

### 4.2 GET /api/ip/blocked — 封禁名单

- 后端：ip_management.py:list_blocked_ips（enabled DESC, created_at DESC，不分页）。
- 响应：{"items": [{"id": 3, "ip": "198.51.100.9", "enabled": true, "note": "异常刷请求", "created_at": "2026-11-22T10:00:00", "updated_at": "2026-11-22T10:00:00"}]}
  后端 _blocked_item **未返回** expires_at（模型里有该列，见 §七）；前端按可选字段读取。
- 调用时机：进入页面、刷新、封禁/解封/删除后。

### 4.3 POST /api/ip/blocked — 封禁

- 后端：ip_management.py:block_ip；请求体 BlockedIPIn {ip, note}（note ≤255）；已存在则复用该行并置 enabled=1。
- 响应：{"item": {…同上}}；错误：400「请输入有效的 IPv4 或 IPv6 地址」/ 409「该 IP 状态发生冲突，请重试」。

### 4.4 PATCH /api/ip/blocked/{id} — 解封 / 重新封禁

- 后端：ip_management.py:patch_blocked_ip；请求体 BlockedIPPatch {enabled?, note?}。
- 响应：{"item": {…}}；错误：404「封禁记录不存在」。

### 4.5 DELETE /api/ip/blocked/{id} — 删除记录

- 后端：ip_management.py:delete_blocked_ip；响应 {"id": <id>, "ok": true}；错误：404「封禁记录不存在」。

### 4.6 GET /api/ip/devices — 设备关联

- 后端：ip_management.py:devices → identity_items()（ClientIdentity ⋈ ClientIdentitySeen，按 last_seen DESC 取 2000 行后按 (kind, digest) 归并）。
- 响应：

```json
{
  "items": [
    {
      "kind": "device_id",
      "digest": "9f2a1c3b5d",
      "value": "dev-9f2a",
      "blocked": false,
      "note": "",
      "source": "client_reported",
      "ips": ["203.0.113.7", "198.51.100.4"],
      "last_seen": "2026-11-23T03:11:02"
    }
  ],
  "limit": 2000,
  "source": "client_reported"
}
```

| 字段 | 类型 | 含义 | 新版用途 |
|---|---|---|---|
| kind | string | device_id / fingerprint / mac / browser | 列 1 类型标签 |
| digest | string | 归一后的摘要（唯一键的一部分） | 行键 |
| value | string | 上报的原始值 | 列 1 |
| blocked | bool | 是否已封禁 | 列 5、行操作文案 |
| note | string | 备注 | 回传 POST 时原样带上 |
| source | string | 恒为 client_reported | 列 5「客户端上报」 |
| ips | string[] | 最近出现过的 IP | 列 2 chips |
| last_seen | string \| null | 最近一次出现 | 列 4 |
| limit / source | int / string | 2000 / client_reported | 说明行 |

### 4.7 POST /api/ip/devices — 封禁 / 解封设备标识

- 后端：ip_management.py:update_device；请求体 IdentityIn {kind, value, blocked, note}（kind 限 4 种，value 1–128，note ≤255）。
- 响应：{"ok": true, "kind": "device_id", "digest": "9f2a…", "blocked": true}；错误：400（normalize_identity 校验失败）/ 409「设备状态发生冲突，请刷新后重试」。

---

## 五、跨页联动

| 联动 | 说明 |
|---|---|
| 用量日志（/logs，S15） | 行操作「查看日志」跳 /logs?client_ip=<ip>（后端日志接口的筛选参数名就是 client_ip，见 contracts/logs.md）；旧版是切页签并填 #logIp 后 loadLogs(1) |
| 平台上下文 | 本页**不随平台切换重载**：router/pages.ts:PLATFORM_SCOPED_KEYS 不含 ip，/api/ip/* 也不读 X-Platform（ip_management.py 无 platform 依赖）。因此**不订阅** usePlatformStore().onChange |
| 平台网关封禁拦截 | 生效点在 admin/ip_access.py:IPBlockMiddleware（/v1/* 在鉴权与转发前返回 403）；本页只写 blocked_ips / client_identities，不改网关配置 |
| 命令面板 | 不在 08-prompts-pages 的命令注册清单内；只提供页内「封禁 IP」 |
| 全局事件 | 无（仅表格密度 / 列显示 / 每页条数偏好写 localStorage） |

---

## 六、边界情况

1. **空数据**：items: [] → Empty；活跃页签有筛选时给「清除筛选」，无筛选时提示调整时间范围。
2. **加载 / 错误**：见 §2.7；401 由 http 层统一跳登录（api/auth.ts），页面不重复处理。
3. **当前访问 IP**：current_ip 可能出现在列表里（管理员自己也走 /v1）。封禁确认文案额外提示会自伤；行内标注「当前访问 IP」。
4. **脏数据**：client_details 缺失（老数据 / 后端字段变更）时按空对象处理，气泡全部显示「未上报」；ips 为空时列 2 显示 —。
5. **分页越界**：筛选后当前页超出 → 回退到最后一页（前端按返回的 total_ips 纠正）。
6. **排序取消**：点第三次表头回到 requests desc 默认，与旧版 WBIP.sort 的「同列再点切方向」一致。
7. **到期**：expires_at 为空 = 永久（后端 is_blocked_ip 语义）；有值但已过期时后端视作未封禁，本页按 fmtExpiry 显示「已过期 N 天」+ danger 着色。
8. **CIDR**：后端 _normalize_ip 只接受单地址，网段会 400；表单层拦截并说明原因（§3.5）。
9. **时长**：见 §七（后端未持久化 expires_at）。
10. **设备标识未验证**：source=client_reported，页面固定展示「客户端上报」徽章，不做「已可信」暗示。
11. **手机 / 375px**：DataTable 自动卡片模式（列 mobile 标注见 §2.2/2.4/2.5）；FilterBar 换行、控件 100% 宽；抽屉在窄屏退化为全宽。
12. **深色 / 浅色**：只用 tokens.css 变量，无裸写十六进制色；状态色只用于状态（Badge / 失败数 / 到期）。
13. **减少动效**：沿用 DataTable 进度条与 NDrawer 动效，prefers-reduced-motion 由 tokens.css 全局关闭。

---

## 七、旧版有但设计稿没写到的功能，以及新版放置

| 旧版功能 | 新版处理 |
|---|---|
| 流量筛选（高频·低 Token / 高频·低积分）+ 至少请求 + 平均上限 | 保留在活跃页签 FilterBar：usage 下拉 + min + max，映射后端 min_requests / max_avg_tokens / max_avg_credits |
| 页面四条说明（入口次数口径 / 按小时汇总 / 点表头排序 / 低消耗筛选口径） | 收进 PageHeader.help，原文保留 |
| IP 列客户端识别气泡（UA / 设备 ID / 指纹 / MAC） | IpPage 列 1 的 HelpTip（buildClientSummary 逐字迁移旧版文案） |
| 复制 IP 按钮（data-copy-usage-ip） | 行操作菜单保留「查看日志」；IP 列的悬停气泡内提供 Copyable 复制，避免行内再塞图标 |
| 封禁名单「更新时间」 | 列表改为「封禁时间」(created_at) 与「到期」(expires_at)（P13 要求）；updated_at 不展示 |
| 封禁名单「删除记录」 | RowActions 的 ⋯ 菜单（danger，二次确认） |
| 解封后「重新封禁」 | 同菜单的「重新封禁」 |
| 设备卡片列表 | 改为 DataTable（P13 指定列），保留「客户端上报 · 未验证」语义与封禁/解除按钮 |
| 底部 panel-note 长说明 | 页签 3 说明行 + HelpTip 原文 |
| 刷新按钮 | 页头「刷新」（当前页签 + 统计） |
| 每页固定 100 条 | 改为 20/50/100 可选（后端 limit 上限 500） |

### 无法在本页闭环的两处（需后端跟进，已在 UI 显式说明）

1. **CIDR 网段封禁**：ip_management.py:_normalize_ip 用 ipaddress.ip_address，只接受单地址；blocked_ips.ip 也无网段语义。表单层拦截并给出原因。
2. **封禁时长 / 到期列表**：BlockedIP.expires_at 已存在（ip_access.is_blocked_ip 会判过期，abuse_guard 的自动封禁在用），但 BlockedIPIn 不接收该字段、_blocked_item 不返回它。新版表单仍提交 expires_at（后端补字段后立即生效），列表按响应字段渲染，缺失时显示「永久」并在列头 HelpTip 说明。

---

## 实现对照

| 功能 | 新版位置 | 状态 |
|---|---|---|
| 三页签（活跃 IP / 封禁名单 / 设备关联）绑定 ?view= | IpPage.vue 的 view / setView + Segmented | 已实现 |
| 统计条（活跃 IP / 入口请求 / 封禁 IP / 设备标识） | StatStrip + activity / devices 数据 | 已实现 |
| 活跃 IP 表 9 列（含当前 IP 标注） | columns + toActivityRows | 已实现 |
| 7 个旧版表头排序（data-ip-sort） | 列 sortable + 受控 sortOrder + activityQueryFromState 映射后端 sort/direction | 已实现（服务端排序） |
| 活跃 IP 服务端分页（20/50/100） | DataTable remote + limit/offset + total_ips | 已实现（旧版固定 100） |
| 时间范围 / 搜索 IP / 状态 / 流量筛选 / 至少请求 / 平均上限 | FilterBar + activityQueryFromState + filterActivityRows | 已实现 |
| 状态筛选（已封禁 / 允许） | filterActivityRows 对当前页过滤；chips 与 summary 标注「仅当前页」 | 已实现（有差异：后端无该参数） |
| 筛选/排序/分页同步路由 query | useTableQuery('ip' / 'ip-blocked' / 'ip-devices') | 已实现 |
| IP 列客户端识别气泡（旧版 ipTooltip 原文） | buildClientSummary + 列 1 HelpTip | 已实现 |
| 行操作：封禁 / 解除封禁 | openBlock(row) / unblockActivity(row) + RowActions | 已实现（有差异：旧版行内直接 POST，新版打开预填抽屉以补原因/时长） |
| 行操作：查看日志 → /logs?client_ip= | viewLogs(row) | 已实现 |
| 封禁名单说明行 | 页签 2 Card 顶部说明 + HelpTip | 已实现 |
| 封禁名单列（IP/网段、原因、封禁时间、到期、状态、操作） | columnsBlocked + toBlockedRows | 已实现（到期在响应缺字段时为「永久」，见 §七） |
| 封禁名单：解除 / 重新封禁 / 删除记录（确认） | toggleBlocked / removeBlocked + useConfirm | 已实现 |
| 设备关联说明 + 旧版 panel-note 原文 | 页签 3 说明行 + HelpTip | 已实现 |
| 设备关联列（标识 / 关联 IP / 关联 Key / 最近活动 / 状态 / 操作） | columnsDevices + toDeviceRows | 已实现（关联 Key 列为 — + 说明，后端不返回） |
| 设备关联：封禁 / 解除（确认 + POST /api/ip/devices） | toggleDevice + useConfirm | 已实现 |
| 封禁抽屉（IP 或 CIDR、原因、时长：永久/1h/1d/7d/自定义） | FormDrawer + parseBlockTarget + expiryFromDuration | 已实现（CIDR 拦截并说明；时长随 expires_at 提交，见 §七） |
| 封禁确认（含「当前访问 IP」自伤提示） | submitBlock 的 useConfirm | 已实现 |
| 四态（首次加载 / 刷新 / 空 / 错误 + 重试） | 每个页签独立的 loading/error ref + NSkeleton / Empty / ErrorBlock | 已实现 |
| 平台切换重载 | 不适用（ip 不在 PLATFORM_SCOPED_KEYS） | 已实现（不订阅 usePlatformStore） |
| 手机 375px 卡片模式 | 列 mobile 标注 | 已实现 |
| 单元测试（行转换 / 查询映射 / 过滤 / 排序 / 分页 / IP 校验 / 到期计算） | tests/page-ip.spec.ts | 已实现 |
