# 接入新平台时必须一起做的四件事

这份文件是 `ONBOARDING.md` 与 `CASE-QODER.md` 的**补遗**。

接完协议、能对话之后最容易漏的四件事 —— 每一件漏了都会让新平台
「看起来能用，但实际是个半成品」。

---

## 一、平台图标：用 lobe-icons，不要自己画

### 规范来源

`@lobehub/icons` —— 200+ AI/LLM 品牌矢量图标。
规范文档：https://lobehub.com/icons/skill.md

### 但先确认目标平台真的有图标

实测（2026-10-28）探测 `@lobehub/icons-static-svg`：

| 平台 | 有独立图标 | 说明 |
|---|---|---|
| Qwen / DeepSeek / Kimi / Zhipu / MiniMax / ChatGLM | ✅ | 直接用 |
| ByteDance / Tencent / AlibabaCloud | ✅ | 母品牌图标 |
| **Qoder** | ❌ | 和阿里的关系明确（`source: sso.aliyun`），用 `alibabacloud` |
| **WorkBuddy / CodeBuddy** | ❌ | 需内联自备（项目里已有） |
| **Trae** | ❌ | 需内联自备（项目里已有） |

### 探测方式（一次请求，不要循环超时）

```bash
# 列全部 provider key
curl -s https://lobehub.com/only-ai/skills/icons/reference/providers

# 测单个图标是否存在（短超时！26 个候选 × 20s 会让人等到崩）
curl -sI --max-time 6 \
  https://unpkg.com/@lobehub/icons-static-svg@latest/icons/qoder.svg
```

> ⚠️ **不要写循环探测 20+ 个候选、每个 20 秒超时** —— 那是 400 秒的无响应。
> 先拉一次 provider 清单，再只测清单外的少数几个。

### 三种 CDN

```
github   https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-svg/icons/{id}.svg
unpkg    https://unpkg.com/@lobehub/icons-static-svg@latest/icons/{id}.svg
aliyun   https://registry.npmmirror.com/@lobehub/icons-static-svg/latest/files/icons/{id}.svg
```

变体后缀：`-color` / `-text` / `-text-cn` / `-brand` / `-brand-color`，
mono 无后缀。

### 落地方式：内联，不要外链

本项目的图标是**内联 SVG**（`fill="currentColor"`），原因：

* 要跟着主题变色（浅色/深色/选中态绿底白图）
* 外链 CDN 会让首屏多一次请求，且离线环境直接空白

```javascript
var ICONS = {
  qoder:
    '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
    '<path d="..."></path></svg>',
};
```

`viewBox` 统一 `0 0 24 24`，`fill="currentColor"` —— 这样不需要为
主题/选中态写额外样式。

### 兜底规则

**找不到官方图标时用母品牌，不要用几何占位。**

```javascript
var ALIASES = {
  "trae-cn": "trae",       // 同产品两个区，换区不换 logo
  "trae-intl": "trae",
  "qoder-cn": "qoder",
  "qoder-intl": "qoder",
};
```

兜底本身也不能是「加号」—— 会被误认成「新建平台」按钮（历史 bug）。
用中性方块。

---

## 二、定时任务：必须能被选到、能被分派

### 三个地方都要通

```
① 平台声明         Gateway.task_runners() 返回 {task_name: runner}
② 任务清单         前端「新建定时任务」下拉里要出现
③ 实际分派         run_task() 能找到并执行它
```

### 最容易漏的是 ②

`TASK_CHOICES` 写死会让新平台任务永远进不了下拉框：

```python
# ❌ 写死
TASK_CHOICES = ["refresh_balances", ..., "trae_checkin"]

# ✅ 内建 + 平台动态收集
def _all_task_choices():
    platform_labels, _ = _platform_tasks()   # 遍历所有平台 task_runners()
    extra = sorted(t for t in platform_labels if t not in TASK_CHOICES)
    return list(TASK_CHOICES) + extra
```

### ③ 有一个隐藏陷阱：`load_gateway` 不存在

```python
# ❌ 原来的代码（AttributeError 被 except 吞掉）
gateway = spec.load_gateway()

# ✅ 正确
gateway = registry.load_platform_gateway(spec, spec.realm)
```

`PlatformSpec` 上只有字符串字段 `platform_gateway`（`"pkg.mod:Attr"`），
**没有 `load_gateway()` 方法**。

后果：`_platform_runner` 永远返回 None，**所有平台声明的任务一次都不跑**。
而 WorkBuddy 的任务注册在全局 `TASK_RUNNERS` 表里，所以 Admin 自己的
任务照常 —— 只有平台独有的（Trae/Qoder 签到）静默失效，
表现为「今天没签到成功」，极难归因。

### 验证方式

```python
# 不能只看清单，要实调
out = scheduler.run_task("qoder_checkin", db, fake_schedule)
assert out["task"] == "qoder_checkin"
assert "claimed" in out or "already" in out
```

---

## 三、风控哨兵：新平台必须上报

### 哪些错误进哨兵

判据：**是不是「这个账号/这个平台不能用了」**。

| 场景 | kind | 进哨兵 |
|---|---|---|
| 401 / TOKEN_INVALID | `auth` | ✅ |
| 签名错（`101` / Signature invalid） | `signature_invalid` | ✅ |
| 额度耗尽 | `quota_exceeded` | ✅ |
| SSE 信封内业务错误 | `upstream_business` | ✅ |
| 网络连续失败 | `network` | ✅ |
| 404 模型不存在 | `model_unknown` | ❌ 用户输错名字 |
| 429 限流 | `rate_limit` | ❌ 不是故障 |
| 排队（`10605`） | `queued` | ❌ 正常重试 |

### `signature_invalid` 为什么要单独一类

它不是账号问题，是**网关实现**问题 —— 一出现就是全平台不可用。
归到 `auth` 会误导运维「去重新登录」，白费功夫。

### 平台维度用落库值

```python
sentinel.note_failure("qoder", account_id, kind, ...)   # 不是 'qoder-cn'
```

熔断的对象是「这一个上游」，不是「某一个区」——
国内/国际共用一套风控策略，分开会让熔断互相看不见。

### 上报函数绝不抛异常

```python
def note_sentinel(realm, account_id, kind, **kw):
    if kind not in SENTINEL_KINDS:
        return                      # 白名单过滤
    try:
        sentinel.note_failure(...)
    except Exception:               # noqa: BLE001
        logger.debug(...)           # 哨兵坏了不能阻断转发
```

### 验证

```python
sentinel.reset()
note_sentinel("cn", 1, "signature_invalid", code="101", status=403)
note_sentinel("cn", 1, "model_unknown", code="404")     # 应被过滤
assert sentinel.status("qoder")["total_events"] == 1    # 只有 1 条
```

---

## 四、余额要"新"，但**列表接口绝不许同步打上游**

### 先看症状与代价（2026-11-04 实测）

用户消耗了 1 积分，账号池显示的是旧值 —— 这确实要修。
但第一版修法是**在 `list()` 里对每个过期账号同步拉一次上游**：

```python
for account in rows:
    if quota_is_fresh(account): continue
    usage = fetch_usage(...)      # ← 一次上游 HTTP，串行
```

50 个号的池子实测 **2.82 秒**，几千个号就是几十秒。
用户的原话是「这几个接口太慢了」——**慢的就是它**。

### 铁律

> **展示数据不许挡住页面渲染。** 列表接口只读 DB 快照，
> 刷新交给后台线程。

```python
def list(self, db):
    views, stale = [], []
    for account in rows:
        if not quota_is_fresh(account):
            stale.append(account.id)
        views.append(public_view(account, quota=snapshot_of(account)))
    if stale:
        from admin.platform import shared
        shared.kick_background_refresh(
            "<platform>", stale,
            lambda session, account: sync_quota(session, account),
            max_items=8, min_gap=10.0)
    return {"items": views, ...}
```

`shared.kick_background_refresh()` 已经封好四件事，**不要自己起线程**：

| 约束 | 值 | 为什么 |
|---|---|---|
| 单飞 | 同平台同时只有 1 个刷新线程 | 防止翻页把线程开爆 |
| 限频 | 10 秒/次 | 列表每 30 秒刷一次，够用 |
| 限量 | 一次 8 个号 | 一轮最多 8 次 RTT，藏在后台 |
| 不冒泡 | 异常只回滚自己的 Session | 后台任务不能掀翻请求 |

### 两条读路径仍要一致

```
/api/<平台>/accounts   → 平台 list()，只读快照
/api/accounts          → 直读 Account.balance_remain 列
/api/overview          → 概览卡片读 credits_snapshot
```

后台刷新会 `apply_quota()` 写回这三列/快照，所以**三条路径看到的是同一份数字**，
只是它可能比上游晚几秒。这是刻意的取舍：**显示晚 5 秒 > 页面卡 3 秒**。
用户想立刻要最新值，界面上有「刷新额度」按钮（走 `refresh()`，同步打上游）。

### 多桶额度要合并

```python
remain = userQuota.remaining + addOnQuota.remaining
total  = userQuota.total     + addOnQuota.total
```

`balance_remain/total` 存**合计**（用户关心「总共还能用多少」），
明细留在 `credits_snapshot` 里供界面区分套餐与资源包。

> 只看 `userQuota` 会以为「签到没生效」——
> 签到额度落在 `addOnQuota`，套餐那个桶不变。

### 验证（两条都要过）

```python
import time
account.credits_synced_at = None      # 强制过期
t0 = time.perf_counter()
ops.list(db)                          # ← 只读快照 + 丢后台
elapsed = time.perf_counter() - t0
assert elapsed < 0.2, "列表仍在同步打上游（实测应 <200ms）"

time.sleep(3)                         # 等后台把余额写回
db.refresh(account)
assert account.balance_remain == 399  # 299 套餐 + 100 签到
```

> 只断言第二条会漏掉「接口慢」；只断言第一条会漏掉「后台没写回」。

---

## 五、平台切换后数据不刷新

前端 `loadXxxAccounts()` 的各平台分支必须**维护同一组状态**：

```javascript
// WorkBuddy 路径
_accData = d.items;
renderAccPage();

// Trae / Qoder 路径
_traeState.items = d.items;
_accData = d.items;                    // ← 漏了这行，筛选器计数是 0
$("#filterAllCount").textContent = _accData.length;
```

历史 bug：Trae 分支只设 `_traeState.items`，不设 `_accData`，
于是切到 Trae 后筛选条显示「全部 0 / 已启用 0 / 已禁用 0」，
而表格里明明有账号 —— 用户感知是「数据丢了，刷新一下才回来」。

---

## 六、检查清单（接完新平台逐项打勾）

```
[ ] 图标：lobe-icons 有官方就用，没有就用母品牌内联
[ ] 图标：ALIASES 把多区平台映射到同一 logo
[ ] 任务：task_runners() 声明了本平台的任务
[ ] 任务：下拉清单能看到（走 _all_task_choices，不写死）
[ ] 任务：run_task() 实调能跑通
[ ] 哨兵：note_sentinel 接在错误路径上（不是只定义不调用）
[ ] 哨兵：kind 区分账号问题与实现问题
[ ] 哨兵：平台维度用落库值（qoder 而不是 qoder-cn）
[ ] 余额：list() 写回 DB 列（带 TTL）
[ ] 余额：多桶合并（套餐 + 资源包）
[ ] 前端：两条账号路径维护同一组状态变量
[ ] 验证：切换平台后余额、筛选器、卡片都变了
[ ] 路由：>256KB / chunked 请求仍能走对平台（跑 check_hotpath.py）
[ ] 路由：同名不同写法的模型，上游回显的是该平台真实 id
[ ] 计费：落库倍率非 -1；短请求积分 = tokens × mult / 1000
[ ] 积分：空结构不覆盖余额；刷新失败会弹上游原文
[ ] 凭证：扫描→导入→导出→回灌 全链路一次通过
[ ] 路径：`core/paths.py` 里没有任何盘符 / `C:\Users` / 绝对路径常量
[ ] 路径：只提供固定目录名，位置走 `admin.platform.install_paths.resolve()`
[ ] 路径：`platform_gateway.probe()` 调 `app_exe_candidates()` / `data_dir_candidates()` 函数
[ ] 路径：换盘 / 加一层父文件夹（`E:\AI\<Product>`）仍能扫到；`<PID>_INSTALL_DIR` 覆盖生效
[ ] 路径：`discover()` 冷启动 <200ms，连续两次调用第二次走缓存
[ ] 路径：脚手架 `scaffold.py` 生成的 `core/paths.py` 已接入 `install_paths`
[ ] 会话 id：同一段对话连发两次，上游收到的会话 id **完全一致**
[ ] 会话 id：换账号发同一段对话，会话 id **必须不同**（不跨号乱串）
[ ] 会话 id：`MsgId` / `TurnId` / `requestId` **每次不同**（去重键，复用会少记账）
[ ] 会话 id：全仓 `grep -rn "uuid4\|token_hex" <平台目录>` 没有任何一处用于会话 id
```

---

## 七、四条实测证据（避免重复踩）

| 事实 | 来源 |
|---|---|
| lobe-icons **没有** qoder/workbuddy/trae | CDN 逐个探测 404 |
| `PlatformSpec.load_gateway()` **不存在** | `AttributeError`（被 except 吞掉） |
| Qoder 余额 = `userQuota` + `addOnQuota` | 实时值 `299 + 100 = 399` |
| 循环探测 26 个图标 × 20s 超时 = 用户等待 400 秒 | 实际发生，被要求「有这么慢吗」 |


---

## 八、上游会话 id：只许**稳定派生**，不许随机 / 伪造

新平台最常见的两个"看不见的错"之一（另一个是路径写死盘符）。
很多上游要求客户端自带一个会话/聊天 id：Loomy 的 `ChatId`、Qoder 的
`session_id`、WorkBuddy 的 `X-Conversation-ID`。

**规则：必须从下游（客户端）真实会话标识稳定派生；同账号 + 同对话恒定。
禁止 `uuid4()`，禁止凭空造一个随机串，禁止"为了看起来真实"而随机化。**

### 为什么

1. 上游前缀缓存按会话算 —— id 每次都换 = 每次全新对话，系统提示、工具定义、
   历史消息全部重算；表现是**首字延迟高、费用高、缓存命中率永远 0**。
2. 一个号几分钟内"新建"几十个会话，是风控眼里最典型的脚本形状。

### 唯一入口

```python
from admin import pool

sid = pool.stable_session_id(
    "<platform>",                       # 平台存储名
    int(account.id or 0),               # 账号 id（换号必须换 id）
    body,                               # 下游请求体（从中取真实会话标识）
    prefix="gwchat-", size=16,          # 按上游 id 的形状给
)
```

内部规则：

```
会话键（确定性，无随机分支）：
  1. 客户端显式标识：metadata.conversation_id / conversation_id / prompt_cache_key
  2. 没有 → 首条 user 文本的 sha256（同一段对话恒定）
  3. 连消息都没有（探测/空请求）→ 每账号一个固定会话
会话 id = sha256("平台 | 账号 id | 会话键")[:size]
```

三条自检：

| 检查 | 期望 |
|---|---|
| 同账号同一段对话，连发两次 | 会话 id 相同 |
| 换账号发同一段对话 | 会话 id 不同 |
| 客户端完全没给会话标识 | 仍恒定（首条消息派生 / 每账号一个），**不是随机** |

### 哪些 id 必须每次唯一（别搞反）

| id | 生命周期 |
|---|---|
| `ChatId` / `session_id` / `X-Conversation-ID` | 会话级，**稳定** |
| `MsgId` / `TurnId` | **每请求唯一**（去重，复用会覆盖消息） |
| `X-Conversation-Request-ID` | **每次上游调用唯一**（上游用它当去重键，复用会少记账） |

### 验证（不看代码，看行为）

```
[ ] 同一对话两次请求 → 抓包/日志里的会话 id 一致
[ ] 换号同一对话     → 会话 id 不同
[ ] 一次流式请求     → MsgId 与上次不同
[ ] 上游用量接口     → 该账号会话数增长 ≈ 真实对话数（不是请求数）
```

> 反例（2026-11-04 实际代码）：Loomy `chat_headers()` 里
> `chat_id = "gwchat-" + uuid4().hex[:16]`、Qoder `build_upstream_body()` 里
> `session_id = "sess-" + uuid4().hex[:16]`、`proxy._turn_headers()` 在客户端
> 没给会话标识时留空 —— 三处都等于"每请求新会话"。详见 FIXED-BUGS 五·补47。

---

## 对话链路验收：harness_chat.py（必须跑，不是可选）

「能回一句话」不算通过。新平台接入后，必须用真实模型跑一遍**工具闭环 + 用量核对**：

`ash
python admin/platform/skill/scripts/harness_chat.py \
  --platform wps-lingxi \
  --model deepseek-v4.1-flash-wps-lingxi \
  --user ADMIN_USER --password ADMIN_PASS
`

它会依次验证三条协议，每条都做**两轮**（发起工具调用 → 把工具结果喂回去）：

| 断言 | 为什么必须有 |
|---|---|
| 返回 	ool_calls / 	ool_use / unction_call | 只吐字说明工具没透传，用户拿到的就是「我没有工具」 |
| 工具参数是合法 JSON | 结构压平没做对时上游只回「AI服务异常」 |
| 工具结果回灌后给出最终答复 | 证明 	ool / 	ool_result / unction_call_output 能正确回传 |
| prompt/completion/total tokens > 0 | 用量真的从上游回来了 |
| 缓存命中字段存在（0 也算） | 0 是「没命中」的真实值；**缺字段**说明没透传 |

退出码 0 = 全绿。建议把这一步写进平台目录的 README（记录当时的模型与结果），
下次改协议层时可以对照回归。