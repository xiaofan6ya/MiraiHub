# 功能契约：智能体（Agent）与登录页（Login）

**页面**：`/agent`（独立三栏创作台）+ `login.html`（独立登录页）
**设计稿**：`06-pages-d.md` P21（智能体）、P26（登录页）
**旧版**：`#panel-agent`（index.html 1891–1968 行）+ `agent.js`（1347 行）+ `agent.css`；`admin/static/login.html`
**后端**：`admin/routers/agent.py`（`/api/agent/*`）、`admin/agent.py`（工具事件）、`admin/media_service.py`（资产）、`admin/server.py:login`（`POST /api/login`）
**新版文件**：`src/pages/AgentPage.vue`、`src/api/agent.ts`、`src/login/LoginPage.vue`、`src/login/main.ts`（替换）、`tests/page-agent.spec.ts`

---

## 一、区块（按设计稿 P21 顺序）

| # | 区块 | 内容 | 新版位置 |
|---|---|---|---|
| 1 | 页头 | 标题 + 说明气泡 + 模式切换（对话/生图/生视频）+ 上下文占用 + 参数开关 | `PageHeader` `#actions` |
| 2 | 左栏 240px | 「新会话」、搜索、置顶/今天/更早分组、会话项（重命名/置顶/删除） | `aside.agent-conv` |
| 3 | 中栏 | 消息流（用户右对齐浅靛底、助手无底色）、空会话「开始创作」三入口、输入框 + 附件 + 发送/停止 | `section.agent-chat` |
| 4 | 右栏 300px（≤1280 收起为浮层） | 模型与参数（模型/思考/思考档位/生图尺寸/视频时长·分辨率·比例）、积分预估、产物画廊（选择 + 合并视频） | `aside.agent-aside` |

## 二、数据展示

- **消息**：用户消息纯文本（保留换行）；助手消息 = 思考块（可折叠）+ 工具进度 chips + 阶段提示 + Markdown 正文 + 产物卡片 + 本轮积分；流式时末尾显示「生成中…」。
- **Markdown**：`marked`（gfm + breaks）渲染后必须经 `DOMPurify.sanitize`，禁用 `style/iframe/form/input/button` 与事件属性；**点击代码块复制**（`pre.textContent`）。
- **工具摘要**：`tool_start` 建一条 running，`tool_end` 按同名更新 ok/failed + 摘要 + 毫秒。
- **产物**：图片 `<img>`、视频 `<video controls>`，地址 `/api/agent/assets/{asset_id}`；落库消息的产物只存在于当轮事件流（历史消息不含 assets，刷新后由产物画廊补齐）。
- **上下文**：`meta.context` / `done.context` → `{used_tokens,budget_tokens,percent,messages}`，页头显示百分比 + Meter。
- **会话时间**：`fmtRelative(updated_at)`；分组按北京时间自然日：置顶 → 今天 → 更早。

## 三、交互

| 动作 | 行为 | 成功 / 失败 |
|---|---|---|
| 新会话 | 清空当前会话（不删除旧会话），聚焦输入框 | — |
| 打开会话 | `GET /conversations/{id}` → 历史消息 + context；恢复该会话的 mode/model/reasoning | 失败：中栏 `ErrorBlock` + 重试 |
| 重命名 | 左侧内联输入，Enter/失焦 → `PATCH {title}` | Toast 失败提示 |
| 置顶 | `PATCH {pinned:true/false}`，刷新列表 | 失败 Toast |
| 删除会话 | 确认框（问句 + 不可恢复）→ `DELETE` | 成功 Toast「已删除会话」 |
| 发送 | 校验非空；推送用户消息 + 助手占位；`POST /chat` SSE 逐帧合并；Enter 发送、Shift+Enter 换行；按钮变「停止」 | 失败：消息内 error + Toast；中止：静默结束 |
| 停止 | `AbortController.abort()`，保留已生成内容 | — |
| 附件 | 本地选择（≤5 个）显示 chip；**以文本标注附在消息末尾**（后端无附件字段，不做假上传） | — |
| 模式切换 | 对话/生图/生视频：切模型候选与右侧参数；空会话三入口同效 | — |
| 积分预估 | 生图/生视频参数变化 300ms 防抖调用 `POST /estimate`，显示「预计 N 积分（仅提示）」 | 失败静默（—） |
| 产物合并 | 选 ≥2 个视频 → `POST /merge` | 成功 Toast（后端 summary）+ 刷新产物 |
| 右栏开关 | ≤1280px 收成浮层，按钮切换 | — |

## 四、接口

| 方法 | 路径 | 请求 | 响应/事件 | 调用时机 |
|---|---|---|---|---|
| GET | `/api/agent/bootstrap` | — | `{key, models:{chat,image,video}, video, estimates, merge, presets, limits}`（`agent.py:139`） | 进入页面 |
| POST | `/api/agent/chat` | `{message, conversation_id?, model?, mode?, reasoning?, thinking?, image?, video?}` | SSE `data:` JSON：`meta|delta|reasoning|tool_start|tool_end|notice|media|done|error`（`agent.py:575` / `_run_turn` 720–864） | 每次发送 |
| GET | `/api/agent/conversations?limit=` | — | `{items:[{conversation_id,title,model,mode,reasoning,thinking,pinned,updated_at,created_at}]}`（907） | 进入页面 / 发送完成 / 重命名/置顶/删除后 |
| GET | `/api/agent/conversations/{id}` | — | 会话字段 + `messages[]` + `context`（923） | 打开会话 |
| PATCH | `/api/agent/conversations/{id}` | `{title?|pinned?|model?|mode?|reasoning?|thinking?}` | `{ok,updated,...会话}`（936） | 重命名 / 置顶 |
| DELETE | `/api/agent/conversations/{id}` | — | `{ok}`（972） | 删除会话 |
| GET | `/api/agent/assets?kind=&limit=` | — | `{items:[{asset_id,kind,model,bytes,duration_s,credits,prompt,created_at}]}`（985 / media_service.list_assets） | 进入页面 / 生成完成 / 合并后 |
| GET | `/api/agent/assets/{id}` | — | 文件流（FileResponse） | 渲染图片/视频 |
| POST | `/api/agent/estimate` | `{kind,count?,size?,seconds?,resolution?,model?}` | `{credits|total_credits,…}`（1015） | 参数变化（防抖） |
| POST | `/api/agent/merge` | `{asset_ids:[…]}` | `{ok,asset_id,summary}`（1162） | 合并所选视频 |

**SSE 事件字段**：
- `meta`：`{conversation_id, model, mode, reasoning, thinking, context, tools[]}`
- `delta` / `reasoning`：`{text}`（增量，直接拼接）
- `tool_start`：`{tool,label,args}`；`tool_end`：`{tool,label,ok,ms,summary}`
- `notice`：`{text}`；`media`：`{asset:{asset_id,kind,model,credits,duration_s,prompt}}`
- `done`：`{credits,usage,steps,conversation_id,messages,context}`；`error`：`{message}`

## 五、跨页联动

- **平台上下文**：`/api/agent/*` 是后台路由，按登录态鉴权，不读 `X-Platform`；本页不订阅平台变化（与旧版一致）。
- 智能体使用「池子里第一个启用 Key」的权益（`_default_key`）；没有 Key 时 `bootstrap.key=null`，页头横幅提示去 `/keys` 创建。
- 生成产物落 `AgentAsset`，与账号/Key 绑定；不在其它页面展示。

## 六、边界情况

1. **无 Key / 无对话模型**：`bootstrap.key=null` → 横幅；`chat` 503 时消息内显示错误。
2. **流中断**：abort 后保留已生成内容，`streaming=false`；网络错误写入消息 `error`。
3. **刷新恢复**：只恢复 `role=user|assistant` 的消息与思考；工具过程与产物卡片不落库（产物由右栏画廊补）。
4. **空会话**：三入口卡片；输入框仍可直接发送。
5. **消息为空**：发送前校验；后端也回 400「消息不能为空」。
6. **模型列表为空**：模型下拉为空，发送会回 503「没有已启用的对话模型」。
7. **附件**：仅记录文件名与大小（不读内容、不上传），随消息正文标注。
8. **长会话**：后端裁剪到 400 条 / 上下文预算，页头显示 `context.percent`。
9. **手机 375px**：三栏变单列（左栏限高 220px，右栏为浮层），消息气泡最大宽度收窄。
10. **深浅色**：只用 tokens 变量，无裸写颜色。

## 七、登录页（P26）

- **入口**：`admin/ui/login.html`（Vite 多入口，构建为 `dist/login.html`），不依赖路由与外壳；`src/login/main.ts` 只做字体/令牌/主题初始化 + 挂载 `LoginPage.vue`。
- **布局**：页面居中、360px 卡片、1px 边框无阴影；48px 图标（`/api/app/icon`，失败回退字母 M）+ 标题「MiraiHub 管理后台」+ 副标题；用户名、密码、登录按钮、错误文案在按钮上方（danger）、底部 12px `--fg-faint` 版本号。
- **请求**：`POST /api/login`，`application/x-www-form-urlencoded`（`username` + `password`），带 `X-Browser-Fingerprint`（UA/语言/分辨率/时区哈希）；成功 200 → `location.replace('/admin/v2/')`；401 → 「用户名或密码错误」；429 → 后端 detail（含等待秒数）；其它 → detail。
- **不循环**：登录页先 `setUnauthorizedHandler(() => undefined)`，避免 `http` 层 401 时跳 `/admin` 造成刷新死循环。
- **主题**：`initTheme()` 跟随 `wb_console_theme` / 系统深浅色；按钮与焦点环使用 tokens。
- **版本号**：`GET /api/app/version`（公开接口），失败则显示 MiraiHub。

## 实现对照

| 功能 | 新版位置 | 状态 |
|---|---|---|
| 三栏布局 240/对话/300，≤1280 右栏收起为浮层 | `AgentPage.vue` `.agent-layout` + matchMedia | 已实现 |
| 新会话 / 搜索 / 置顶 / 今天与更早 | `newConversation` + `filterConversations` + `groupConversations` | 已实现（置顶分组在旧版基础上保留） |
| 会话重命名 / 置顶 / 删除 | 左侧内联编辑 + `patchConversation` / `deleteConversation` + 确认框 | 已实现 |
| 消息流式（SSE，发送→停止） | `chatStream` + `applyChatEvent` 逐帧合并 + `AbortController` | 已实现 |
| Enter 发送 / Shift+Enter 换行 / 自动增高最多 8 行 | `n-input type=textarea :autosize {minRows:1,maxRows:8}` + `onKeydown` | 已实现 |
| 附件 | 📎 选择本地文件（≤5）+ 文件名 chip | **有差异**：后端 `/chat` 无附件字段，附件只作为正文标注发送，不上传文件内容 |
| 模型选择 / 思考档位 / 思考开关 | 右栏 `NSelect`（bootstrap.models 按模式切换） | 已实现 |
| Markdown（marked + DOMPurify）/ 代码块复制 | `renderMarkdown` + 消息区点击 `pre` 复制 | 已实现 |
| 生成图片/视频产物 | `media` 事件 → 消息内 `<img>/<video>`；右栏画廊 `listAssets` | 已实现 |
| 产物合并（视频） | 右栏选择 ≥2 → `POST /merge` | 已实现（旧版同功能） |
| 空会话「开始创作」三入口 | `.agent-start` 卡片（对话/生图/生视频） | 已实现 |
| 上下文占用 | `meta/done.context` → 页头百分比 + Meter | 已实现 |
| 积分预估 | `POST /estimate`（300ms 防抖） | 已实现 |
| 四态：首屏骨架 / 流式与刷新 / 空 / 错误 | 骨架、`streaming`、`Empty` 多态、`ErrorBlock` 三处（bootstrap/会话列表/消息） | 已实现 |
| 预设（风格/镜头）与提示词增强、长视频规划按钮 | — | 未做（P21 未列；后端 `/enhance`、`/plan`、`presets` 已保留，后续可加） |
| 动态工具选择器 / 生成参数表单的完整旧版控件 | 右栏「模型与参数」简化版（模型/思考/尺寸或时长·分辨率·比例） | **有差异**：旧版还有 mode 预设、aspect 细分等；新版按 P21 收成 4 项 |
| 登录页：独立 360px 卡片、图标、用户名密码、登录、错误、版本号、跟随深浅色 | `src/login/LoginPage.vue` + `src/login/main.ts` + `login.html` | 已实现（图标用 `/api/app/icon`，失败回退字母 M） |
| 登录请求与旧版一致（form-urlencoded + 指纹头），成功后跳 `/admin/v2/` | `LoginPage.vue:submit` | 已实现（旧版跳 `/admin`，按 P26 改成 v2） |
| 登录失败不触发 http 的 401 跳转 | `setUnauthorizedHandler(() => undefined)` | 已实现 |
| 单元测试（分组/搜索/Markdown 净化/流事件归并/历史消息映射） | `tests/page-agent.spec.ts` | 已实现 |

### 口径核对（以 agent.py / media_service.py / server.py 实际字段为准）

- SSE 帧只有 `data:` 行，没有 `event:` 名；`http.ts:stream` 按空行分帧并 JSON.parse。
- `done.credits` 是本轮积分（上游 usage 累加）；历史消息的 `usage.credits` 是同一口径。
- 历史消息只保存 `role=user|assistant`（以及 `role=tool` 的工具结果，页面不渲染）；`reasoning_content` 单独落库。
- `conversations` 列表按「置顶优先 → updated_at 倒序」，limit 1–100（默认 40）。
- 资产地址 `/api/agent/assets/{id}` 需要登录态；文件 404 表示已过期或被清理。
- `POST /api/login` 字段为 `username` / `password`（Form），成功回 `{ok,expires_in,sid,multi_device}`，凭据在 HttpOnly Cookie。