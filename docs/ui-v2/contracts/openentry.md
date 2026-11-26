# 功能契约：公开注册入口页（Open Entry）

**页面**: P22 / S28 · 路由 `/openentry`（页面 key `openentry`，分组「公开入口」）
**设计稿**: 06-pages-d.md P22、05-patterns.md 模板 C
**旧版**: admin/console/index.html `#panel-openentry`（2580–2695）+ console-app.js（loadJoinGate 2933 / loadOpenEntryConfig 2965 / loadOpenEntryTickets 3048 / openEntrySaveCopy 3100 / openEntryResetCopy 3129 / openEntrySaveConfig 3153 / joinGateToggle 3164 / joinGateMakeLink 3193）
**后端**:
- 总闸与票据：admin/routers/join_gate.py（GET /api/join/gate/status、POST /api/join/gate/toggle、POST /api/join/gate/tickets）+ admin/join_gate.py
- 入口配置与链接列表：admin/public_auth.py（GET/POST /api/open/admin/config、GET /api/open/admin/tickets、CONFIG_KEY、COPY_DEFAULTS、_sanitize_copy）
- 公开页：admin/static/join.html（**不在本次重写范围**，逻辑不动）
**新版**: admin/ui/src/pages/OpenEntryPage.vue + admin/ui/src/api/openentry.ts

> 旧版说明：多平台聚合入口；总闸关闭时 /join 与 /api/open/* 一律 404；每次开关推进代次，旧链接与邀请码同时作废。

---

## 一、区块（按 P22 顺序）

| # | 区块 | 锚点 id | 内容 |
|---|---|---|---|
| 1 | 入口状态 + 限制参数 | openentry-gate | 大号 Switch + Badge「已开放/已关闭」+ 代次/可用票据/有效邀请码 + 链接时效 |
| 2 | 已生成的访问链接 | openentry-links | DataTable（链接 Copyable · 剩余有效期 · 状态）+「生成访问链接」FormDrawer（数量 1–50） |
| 3 | 公开页可见的平台 | openentry-platforms | 平台多选（logo + 名称 + 开关），点击即存 |
| 4 | 公开页文案 + 实时预览 | openentry-copy | 标题/副标题/说明（多行）/页脚 + 右侧预览 |

---

## 二、数据展示

### 2.1 GET /api/join/gate/status（join_gate.py:gate_status）

``` "open": true, "epoch": 7, "active_tickets": 3, "active_invites": 1, "ticket_ttl": 86400, "access_ttl": 3600 ```

- 状态 Badge：open → ok「已开放」/ neutral「已关闭」。
- 元信息：`代次 {epoch} · 可用票据 {active_tickets} · 有效邀请码 {active_invites}`。
- `ticket_ttl`：0 = 永久；其它走 `ttlLabel()`（分钟 / 小时 / 天）。

### 2.2 GET /api/open/admin/config（public_auth.py:open_admin_config）

``` "ok": true,
  "platforms": [{ "id": "workbuddy", "label": "WorkBuddy", "modes": ["sms"], "order": 10, "enabled": true }],
  "ticket_ttl": 86400, "ttl_range": [600, 604800],
  "copy": { "title": "MiraiHub 注册", "sub": "", "intro": [], "foot": "" } ```

- 平台开关 `platforms[].enabled`，清单由后端适配器决定（不硬编码平台）。
- 文案上限（后端 _sanitize_copy）：title ≤40、sub ≤120、intro ≤6 行且每行 ≤200、foot ≤160。
- 时效范围 `ttl_range=[600,604800]`，另允许 0 = 永久。

### 2.3 GET /api/open/admin/tickets（public_auth.py:open_admin_tickets）

``` "ok": true, "open": true, "epoch": 7, "count": 2,
  "tickets": [{ "code": "Ab3dE9xQ2k", "permanent": false, "expires_in": 82800 }] ```

- 展示链接 = `location.origin + '/j#k=' + code`（后端只回 jti）。
- 状态：permanent → 「永久有效」；否则「剩余 `ttlLabel(expires_in)`」。
- 只列当前代次仍有效的票据（最多 50 条）。

---

## 三、交互

| 控件 | 旧版行为 | 新版行为 |
|---|---|---|
| 总开关 | POST /api/join/gate/toggle{enable}，确认文案写明旧链接与邀请码全部失效 | 大号 NSwitch + 危险确认；成功后 Toast「入口已开启/已关闭，旧链接全部失效」并重载 |
| 链接时效 | POST /api/open/admin/config{ticket_ttl} | NSelect（永久 / 1h / 6h / 24h / 3d / 7d）→ 保存 Toast「新签发的链接按新时效生效」 |
| 生成访问链接 | POST /api/join/gate/tickets{count} → urls | FormDrawer（1–50）→ 列出全部 urls（Copyable），首条复制 + Toast |
| 链接列表 | 链接 + 剩余有效期 + 复制 | DataTable（链接 Copyable / 剩余有效期 / 永久或临时 / 复制） |
| 撤销链接 | 旧版无单条撤销 | 卡内「关闭入口使全部失效」（总开关确认路径）；后端无单条撤销接口（§七） |
| 平台开关 | POST /api/open/admin/config{platforms:{id:bool}} 即时生效 | 平台行（PlatformLogo + 名称 + NSwitch）切换即保存 + Toast |
| 保存文案 | POST /api/open/admin/config{page_copy}；恢复默认 page_copy:{} | 保存 / 恢复默认（确认）；前端按后端上限校验并给出行号 |
| 实时预览 | 右侧小框 | 预览框按公开页排版渲染纯文本（不解析 HTML） |

---

## 四、接口

| 方法 | 路径 | 请求 | 响应（用到的字段） | 后端 |
|---|---|---|---|---|
| GET | /api/join/gate/status | — | open/epoch/active_tickets/active_invites/ticket_ttl/access_ttl | join_gate.py:gate_status |
| POST | /api/join/gate/toggle | {enable} | {ok, open, epoch, revoked_tickets, revoked_invites} | join_gate.py:gate_toggle |
| POST | /api/join/gate/tickets | {count:1..50} | {ok, expires_in, urls[], warning} | join_gate.py:gate_tickets |
| GET | /api/open/admin/config | — | platforms/ticket_ttl/ttl_range/copy | public_auth.py:open_admin_config |
| POST | /api/open/admin/config | {platforms?, ticket_ttl?, page_copy?} | 同上 | public_auth.py:open_admin_config_save |
| GET | /api/open/admin/tickets | — | open/epoch/count/tickets[{code,permanent,expires_in}] | public_auth.py:open_admin_tickets |

调用时机：进入页面三请求并发；开关/时效/平台/文案保存后重载对应区块；生成链接后重载 gate + tickets。

---

## 五、跨页联动

- 与 /join 公开页同源（本页改的开关/平台/文案立即对公开页生效）；join.html 逻辑不动。
- 邀请码数字（active_invites）来自 join_gate；邀请码管理不在本页。
- 不依赖平台上下文（入口模块本身），不订阅 usePlatformStore。

---

## 六、边界情况

1. 首次加载：四张卡骨架；总开关在数据回来前禁用。
2. 刷新中：保留旧数据，卡片 loading。
3. 空：无链接 → Empty「还没有链接，点「生成访问链接」签发」；无平台适配器 → Empty。
4. 错误：任一接口失败 → ErrorBlock（带 detail）+ 重试；文案保存失败保留输入。
5. 入口未开启：生成链接按钮禁用（后端 409）。
6. 开关推进代次：确认框必须写明，成功后重载列表。
7. 单条撤销：后端只有整代失效（§七）。
8. 文案上限：前端同后端校验（标题 40 / 副标 120 / 说明 6×200 / 页脚 160）。
9. 手机 375px：卡片单列，预览折到表单下方，DataTable 走组件卡片模式。

---

## 七、新版取舍

1. **单条链接撤销**：后端只提供「列当前代次票据」，撤销粒度是代次（关闸/开闸）。页面提供「关闭入口使全部失效」，不伪造单条撤销。
2. **票据明文只返回一次**：POST tickets 的 urls 是明文；列表接口只回 jti，用 origin + '/j#k=' + code 重新拼（与旧版一致）。
3. **平台清单来自后端适配器**，页面不写平台 id。
4. **join.html 不改**：本页只改配置。

---

## 实现对照

| 契约功能 | 新版位置 | 状态 |
|---|---|---|
| 总开关 + 状态 Badge + 代次/票据/邀请码 | 入口状态卡 + GET /api/join/gate/status | 已实现 |
| 限制参数（链接时效） | ttlOptions + POST /api/open/admin/config{ticket_ttl} | 已实现 |
| 访问链接 DataTable（Copyable/有效期/状态） | GET /api/open/admin/tickets + buildTicketUrl | 已实现 |
| 生成链接 FormDrawer（1–50） | POST /api/join/gate/tickets → urls | 已实现 |
| 单条撤销 | 无单条接口 | 有差异（§七.1） |
| 公开页平台多选（logo + 开关，点击即存） | PlatformLogo + NSwitch + POST config{platforms} | 已实现 |
| 公开页文案 + 实时预览 | 表单 + 预览 + copyValidation（与后端同上限） | 已实现 |
| 恢复默认文案（确认） | page_copy:{} | 已实现 |
| 危险操作确认 | useConfirm | 已实现 |
| 四态 + 375px | 骨架 / 保留旧数据 / Empty / ErrorBlock；单列 | 已实现 |
| 单元测试（TTL 文案/链接拼接/文案校验/平台摘要） | admin/ui/tests/page-openentry.spec.ts | 已实现 |
