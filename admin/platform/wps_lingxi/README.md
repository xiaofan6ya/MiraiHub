# WPS 灵犀（Lingxi）平台接入

把 **WPS 灵犀桌面客户端的 agent-runtime 通路**接成 OpenAI / Anthropic / Responses 三种协议。

参考实现：[`@kevinfx/magpie-wps-lingxi@0.3.3`](https://www.npmjs.com/package/@kevinfx/magpie-wps-lingxi)
（Magpie / OpenCode 插件）。本目录是**在 MiraiHub 里按同一协议重写**的 Python 版，
不是直接加载那个 npm 包 —— MiraiHub 的平台必须实现 `Gateway` / `AccountOps` /
`ClientProbe` 三个契约，npm 插件的宿主接口与它不兼容。

---

## 一、上游事实（不是猜的）

| 用途 | 请求 |
|---|---|
| 建会话 | `POST {base}/api/aioffice/v1/cowork/sessions` → `data.session_id` |
| 领 run | `POST {base}/api/public/v1/credits/runs` → `data.run_id` / `data.group_id` |
| 对话 | `POST {base}/api/public/v1/agent-runtime/chat/completions`（SSE，自定义帧） |
| 资料 | `GET {base}/api/public/v1/account/profile` → `data.id` / `data.name` |
| 模型目录 | `GET {base}/api/aioffice/v1/sessions/plans` → `data.plans` / `data.models` |
| 智点余额 | `GET {base}/api/public/v1/credits/balance` |
| 任务中心 | `GET {base}/api/public/v1/tasks`、`POST …/tasks/{key}/claim` |

* `base`：账号面 `https://lingxi.wps.cn`（回落 `https://lingxi.kdocs.cn`）；
  **对话面**按 `[wps.cn, kdocs.cn]` 顺序回落，成功后把实际域缓存进 run。
* 鉴权：`Cookie: wps_sid=<sid>[; uid=…][; copilot_server_branch=…]`，
  再追加三段桌面客户端标记 `x-kso-app-name` / `x-kso-app-version` /
  `x-kso-device-id`（device-id 是 `sha256("magpie-lingxi-device")`，可复现）。
* 计费按 **run**：一个 run 内多次调用只扣一次智点，所以「会话 + run」按
  「Cookie × 模型」缓存 1 小时（**不滑动续期**）。

### 三个必须照抄、改了就坏的地方

1. **`usage.total_tokens` 必须自算**（`prompt + completion`）。上游给的是
   「当前上下文大小」，直接透传会让总量虚高几十倍。
2. **`cached_tokens` / `reasoning_tokens` 是扁平字段**，要写进
   `prompt_tokens_details` / `completion_tokens_details`（方向与 OpenAI 相反）。
3. **`max_tokens` 要塞进 `base_llm_arguments`**，`reasoning_effort` 顶层与
   `llm_arguments` 各写一份；工具 schema 必须先压平（`anyOf` / `$ref` /
   数组型 `type` 会让上游整轮失败，只回一句「AI服务异常」）。

详见 `core/protocol.py` 的模块注释。

---

## 二、本平台支持什么

| 能力 | 状态 |
|---|---|
| `/v1/chat/completions`（真流式 SSE） | ✅ 逐帧翻译，上游没有 `[DONE]`，本地补 |
| `/v1/messages`（Anthropic） | ✅ 本地翻译；流式用完整结果合成帧 |
| `/v1/responses` | ✅ 本地翻译 |
| `/v1/messages/count_tokens` | ✅ 粗估（中文约 1.5 字/token） |
| `/v1/models` | ✅ 先拉上游 plans，失败回落内置 14 个模型快照 |
| `/v1/balance` | ✅ 智点余额 + 15 天内到期的「临时积分」 |
| 账号池 11 项 | ✅ 导入 / 导出 / 探活 / 健康检查 / 用量 |
| 本机客户端扫描 | ✅ Windows（DPAPI + AES-256-GCM）/ macOS（明文 `value` 列） |
| 每日签到 | ✅ 任务中心 `daily_check_in`，奖励按**领前/领后余额差**计算 |
| 每日活跃对话 | ✅ `wps_lingxi_activity`：每天真发一条办公场景对话（串行 + 抖动 + 85% 概率 + 偶尔追问），记账进 `usage_logs` |
| 会话保活 | ✅ `wps_lingxi_keepalive`：校验 Cookie + 同步智点（上游没有 refresh 接口，保活只能到这一步） |
| 失效复检 | ✅ `wps_lingxi_ban_recheck`：disabled 的号探活通过即恢复 |
| 用量详情 | ✅ 上游 `credits/balance` + `credits/transaction_summary` + 本地按模型聚合 |
| 使用日志 | ✅ 上游 `agents/run_records` + 本地 `usage_logs`（两份可对照） |
| 一次性任务 | ⚠️ 只领 `status=claimable` 的；需要「做动作」的（专家模式 / 建群 / 下载客户端）如实跳过 |
| Cookie 刷新 | ❌ 上游没有 refresh 接口 —— 过期只能重新粘贴 |

> **为什么保活必须带「活跃对话」**：签到只证明点过按钮，刷智点只证明有人查了余额。
> 上游看的是账号自己的对话行为 —— 只有管理接口、没有一条对话的号，正是最典型的
> 「机器在养号」形状。所以 `wps_lingxi_activity` 不是可选项。

---

## 三、怎么用

1. 控制台 → 平台切换 → **WPS 灵犀**
2. 账号池 → 「导入」：粘贴 `wps_sid` 或整段 Cookie（从浏览器 DevTools →
   Application → Cookies 里复制），或点「导入本机」直接读灵犀客户端的登录态
3. 调用：`curl -H "Authorization: Bearer $KEY" -H "X-Platform: wps-lingxi" ...`
   或直接用带平台后缀的模型名 `deepseek-v4.1-flash-wps-lingxi`

管理接口：

```
GET  /api/wps-lingxi/meta
GET  /api/wps-lingxi/scan                 扫描本机（不含 Cookie）
POST /api/wps-lingxi/import               导入单条
POST /api/wps-lingxi/import-batch         批量导入（数组 / JSON / JSONL）
POST /api/wps-lingxi/import-local         导入本机账号
POST /api/wps-lingxi/keepalive            全量探活 + 同步智点
POST /api/wps-lingxi/checkin              签到
POST /api/wps-lingxi/activity             补跑一次「每日活跃对话」
POST /api/wps-lingxi/ban-recheck          失效账号复检
GET  /api/wps-lingxi?page_size=200        账号列表（控制台「用量明细」入口）
GET  /api/wps-lingxi/usage/detail?days=30 用量详情（上游流水 + 本地聚合）
GET  /api/wps-lingxi/usage/logs?days=7    使用日志（本地账本；source=both 对照上游）
GET  /api/wps-lingxi/{id}/request-usage   单个账号的上游调用明细
GET  /api/wps-lingxi/export?reveal=0      导出（默认脱敏）
GET  /api/wps-lingxi/usage?days=30        用量
```

---

## 四、环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `ADMIN_WPS_LINGXI_BASE` | `https://lingxi.wps.cn` | 账号面 base（联调/回归用） |
| `ADMIN_WPS_LINGXI_CHAT_BASES` | `wps.cn,kdocs.cn` | 对话面回落顺序（逗号分隔） |
| `ADMIN_WPS_LINGXI_MAX_PROMPT_KB` | `320` | 单请求提示词预算 |
| `ADMIN_WPS_LINGXI_MAX_MESSAGE_KB` | `48` | 单条消息上限（超出掐头去尾） |
| `ADMIN_WPS_LINGXI_TEMP_DAYS` | `15` | 「临时积分」窗口天数 |
| `ADMIN_WPS_LINGXI_NO_KSO` | 空 | 置 `1` 时不追加 `x-kso-*`（排障用） |
| `ADMIN_WPS_LINGXI_HOME` | 空 | 客户端数据目录（默认自动探测） |

---

## 五、接入时的验证记录（2026-11-07）

**上游连通性**（假 Cookie 打真实上游，`401` = 端点存在、只缺凭证）：

```
[401] GET  /api/public/v1/account/profile                     lingxi.wps.cn
[401] GET  /api/public/v1/account/profile                     lingxi.kdocs.cn
[401] GET  /api/public/v1/credits/balance
[401] GET  /api/aioffice/v1/sessions/plans
[401] GET  /api/public/v1/tasks
[401] POST /api/aioffice/v1/cowork/sessions
[401] POST /api/public/v1/agent-runtime/chat/completions
```

**结构验收**（`admin/platform/skill/scripts/verify_platform.py --platform wps-lingxi`）：
11 项通过；`overview_cards` 在 SQLite 后端下另有冒烟覆盖；
`tests/gateway_contract_baseline.txt` 在本分支不存在（develop 尚未有 `tests/` 目录）。

**协议层单测**：49 项断言通过（Cookie 归一 / usage 归一 / schema 压平 /
提示词裁剪四步 / SSE 帧翻译 / 非流式折叠 / 错误分档）。

**端到端冒烟**（SQLite + TestClient）：17 项通过，含
`/v1/models` 回落 14 个内置模型、无效 Cookie → `401 auth`、无账号 → `503`、
导出默认不含 Cookie。见 PR 说明。

---

## 六、已知限制

* **没有 Cookie 刷新**：`wps_sid` 过期只能重新导入；`refresh()` 做的是
  「重新校验 + 刷余额」。
* **一次性任务不自动做动作**：专家模式 / 建群那种要靠真实对话翻转状态，
  本轮只领已经 `claimable` 的，其余在返回值里如实列出 `one_time_skipped`。
* **单号并发**：上游 `magpie.maxConcurrency=4` 只是宿主声明，
  插件内部没有并发控制；本实现在进程内按 run 复用，但没有做 single-flight，
  同一账号高并发时仍可能各建一个 run（多扣智点）。
* **macOS 客户端扫描**依赖 `value` 列明文；若该客户端版本启用了 OSCrypt，
  会走 Windows 同一套解密路径（需要 `cryptography`）。