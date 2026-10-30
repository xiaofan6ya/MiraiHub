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

## 四、余额/额度必须实时，不能只读 DB 缓存

### 症状

用户消耗了 1 积分，账号池仍显示旧值（`400` 而不是 `399`）。

### 根因：两条读路径

```
/api/qoder/accounts      → 平台 account_ops().list()  → 实时拉上游 ✅
/api/accounts            → 直读 Account.balance_remain 这一列  ❌ 旧值
/api/overview            → 概览卡片读 credits_snapshot        ❌ 旧值
```

`admin/routers/accounts.py` 是**平台无关**的聚合接口，
它不调平台的 `account_ops().list()`。

### 修法：在 `list()` 里写回 DB

```python
def list(self, db):
    for account in rows:
        if quota_is_fresh(account):        # TTL 内用缓存，不打爆上游
            continue
        usage = fetch_usage(...)
        _apply_usage(account, usage)       # ← 写回 DB 列
    db.commit()                            # 两条读路径都看到新值
```

TTL 取 60 秒：用户感知得到变化，又不会每次刷页面都打上游。

### 多桶额度要合并

```python
remain = userQuota.remaining + addOnQuota.remaining
total  = userQuota.total     + addOnQuota.total
```

`balance_remain/total` 存**合计**（用户关心「总共还能用多少」），
明细留在 `credits_snapshot` 里供界面区分套餐与资源包。

> 只看 `userQuota` 会以为「签到没生效」——
> 签到额度落在 `addOnQuota`，套餐那个桶不变。

### 验证

```python
account.credits_synced_at = None      # 强制过期
ops.list(db)
db.refresh(account)
assert account.balance_remain == 399  # 299 套餐 + 100 签到
```

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
```

---

## 七、四条实测证据（避免重复踩）

| 事实 | 来源 |
|---|---|
| lobe-icons **没有** qoder/workbuddy/trae | CDN 逐个探测 404 |
| `PlatformSpec.load_gateway()` **不存在** | `AttributeError`（被 except 吞掉） |
| Qoder 余额 = `userQuota` + `addOnQuota` | 实时值 `299 + 100 = 399` |
| 循环探测 26 个图标 × 20s 超时 = 用户等待 400 秒 | 实际发生，被要求「有这么慢吗」 |
