# 计费与倍率：五套口径的识别与落地

用户的核心规则：**只有 0 倍率才算免费。**

这条规则听起来简单，但接平台时会遇到六种完全不同的上游形态。
判错的后果是「把收费模型标成免费」——用户拿到账单才发现。

---

## 六种上游形态（全部实测）

| 平台 | 倍率来源 | 形态 | 样例 |
|---|---|---|---|
| WorkBuddy | `credits` | 字符串 | `"x0.50"` |
| Trae | `credits` | 字符串 | `"x0.79"` |
| Qoder | `price_factor` | 数字 | `0.5` |
| **Loomy** | **模型显示名后缀** | **字符串内嵌** | `"DeepSeek V4.1 Flash(x2.0)"` |
| **Raccoon** | **`billing_effective_multiplier` / `billing_multiplier`** | **两个数字字段** | `0.25` / `0.5` |
| （兜底） | 无 | — | 返回 `-1` |

> 接第 7 个平台时先问一句：**上游的模型目录接口里，有没有我还没看的字段？**
> 小浣熊的倍率一直在 `billing_*` 里，只是第一版 `fetch_models()` 只挑了
> name/display/description/visible 四个键 —— 字段存在，被我们自己过滤掉了。

---

## ★ 最容易踩的坑：倍率藏在显示名里

Loomy 的 `/models` **没有任何价格字段**：

```
id / name / context_length / max_output_tokens / capabilities /
reasoning_efforts / protocol / is_default_model / type
```

我第一轮 grep 了 `price_factor` / `multiplier` / `pointsPerToken` /
`modelPrice` / `creditRate` / `unitPrice` —— **全部未命中**，
于是错误结论：「Loomy 没有倍率概念」。

### 实际数据长这样

```
Spark-X2.6 正式版(限时免费)(x0.0)   -> 0.0   免费
Spark-X2.5（限时免费）(x0.0)        -> 0.0   免费
DeepSeek V4.1 Flash(x2.0)          -> 2.0
MiniMax M3 （x4.0）                 -> 4.0
Kimi k2.6 （x6.5）                  -> 6.5
Qwen 3.8 Max (x12.0)               -> 12.0
GLM 5.3 Flash(x0.8)                -> 0.8
qwen 3.8 flash（x0.8）              -> 0.8
MiMo V2.5（x3.3）                   -> 3.3
```

### 怎么发现的

在 `app.asar`（220 MB 主体，不是 unpacked 目录）里搜**中文关键词**：

```python
data = pathlib.Path("app.asar").read_bytes()
data.find("倍率".encode())
# -> "* 倍率代表积分消耗倍数.标准为 1.0x."
# -> "模型显示名末尾的括号内容作为展示标签.末尾的 x数字作为倍率."
```

> **教训**：只搜英文字段名会漏。中文 UI 文案里常写着数据从哪来。

### 客户端的解析逻辑（必须照抄）

`app.asar` 里的 `splitModelDisplayName(value)`：

```javascript
export function splitModelDisplayName(value) {
  const original = String(value || '').trim()
  if (!original) return { name: '', badge: '', multiplier: '' }
  let name = original, badge = '', multiplier = ''

  for (let i = 0; i < 2; i += 1) {           // ← 最多两轮
    const m = name.match(/\s+[xX](\d+(?:\.\d+)?)\s*$/)   // 末尾 ` x0.5`
    if (m) { multiplier = `x${m[1]}`; name = name.slice(0, m.index).trim(); continue }

    const b = name.match(/^(.*?)\s*(?:[（(]([^（）()]*)[）)])\s*$/)  // 末尾括号
    if (!b) break
    const nextName = String(b[1] || '').trim()
    const nextBadge = String(b[2] ?? b[3] ?? '').trim()
    if (!nextName || !nextBadge) break
    name = nextName
    const pm = nextBadge.match(/^[xX](\d+(?:\.\d+)?)$/)
    if (pm) multiplier = `x${pm[1]}`        // 括号里是 x数字 -> 倍率
    else badge = nextBadge                  // 否则是徽标（如「限时免费」）
  }
  return { name: name || original, badge, multiplier }
}
```

**为什么不能自己写正则：**

1. 全角 `（）` 与半角 `()` 混用 —— 实际数据里两种都有
2. 两层后缀：`(限时免费)(x0.0)` —— 先剥掉徽标才看得到倍率
3. `x` 前后空格不一致：`x0.8` / ` x6.5`

---

## ★ 第六种：标价 ≠ 实扣（Raccoon）

小浣熊的模型目录**同时给两个数字**：

```json
{
  "name": "sensenova-6-8-flash",
  "billing_multiplier": 0.5,             // 标价
  "billing_effective_multiplier": 0.0,   // 实扣（限免 / 折扣后）
  "billing_status": "limited_free",
  "billing_status_note": "限时免费"
}
```

### 取哪个：**实扣优先，标价解释**

```python
for key in ("price_factor", "priceFactor", "original_price_factor",
            "billing_effective_multiplier",   # ← 实扣必须排在前面
            "billing_multiplier"):            # ← 标价兜底
    ...
```

取错一个的后果是**两个方向都会错**：

| 取错 | 表现 |
|---|---|
| 只取标价 | 限免模型被标成「×0.50 收费」，用户不敢用 |
| 只取实扣 | 折扣结束后仍是旧值，账单对不上 |

### 为什么不能只留一个

`billing_effective_multiplier` 是「**此刻**要花多少」，`billing_multiplier`
是「原价多少」。前端提示 `×0.25（原价 ×0.50）` 比只给一个数字有用得多 ——
用户能判断「是限免还是真便宜」。

### 同一条链路的三个字段，一个都不能丢

```python
flat.append({
    "billing_multiplier":           multipliers[0],
    "billing_effective_multiplier": multipliers[1],
    "billing_status":               item.get("billing_status"),      # limited_free
    "billing_status_note":          item.get("billing_status_note"), # 限时免费
    ...
})
```

丢了 `billing_status`，前端就只能显示数字，说不出「为什么这么便宜」。

---

## ★ 记账：倍率同时是「每千 token 积分」

`model_configs.credit_multiplier` **一个字段担两个职责**：

| 用途 | 语义 |
|---|---|
| 展示 | 相对倍率（`×0.25`） |
| **记账** | **每 1000 token 扣多少积分** |

```python
# proxy.py 记账（上游没给 credits，或给了 0 时）
credits = float(total_tokens) * credit_multiplier / 1000.0
```

实测：小浣熊 `sn-glm-5-3-flash`，206 token、倍率 0.2 → `0.0412`。

### 三个坑

**① 上游给 `credits = 0` 不代表免费**

WorkBuddy 上游就恒回 `usage.credit = 0`（它根本没算）。判据必须是
「库里配了正倍率 → 按倍率估算」，而不是「上游说 0 就写 0」。

```python
if (credits is None or float(credits or 0) <= 0) and (total_tokens or completion_tokens):
    ...
```

**② 查倍率必须限定平台**

```python
# ❌ 只按 model_id：workbuddy 与 trae 都有 `glm-5.2`，.first() 可能命中另一家
db.query(ModelConfig).filter(ModelConfig.model_id == name).first()

# ✅ 先按平台查，查不到才放宽
db.query(ModelConfig).filter(ModelConfig.model_id == name,
                             ModelConfig.platform == resolved_platform,
                             ModelConfig.enabled == 1).first()
```

**③ 客户端发的是 slug，库里存的是上游 id**

`deepseek-v4.1-flash-workbuddy`（对外 slug）、`deepseek-v4.1-flash`
（WorkBuddy 库里）、`DeepSeek-V4.1-Flash`（Loomy 库里）是同一个模型。

只用字面量精确查 → 查不到 → 倍率 0 → **积分显示 0**。必须走候选列表：

```python
from admin.platform.shared import _billing_candidates
for name in _billing_candidates(model or ""):
    ...   # 依次尝试：原名 / 剥后缀 / slug 归一 / 上游 id 反查
```

---

## 积分获取与显示（读不到 ≠ 0）

### 各平台的余额接口不一样

| 平台 | 接口 | 字段 | 口径 |
|---|---|---|---|
| WorkBuddy | `/api/accounts/{id}/refresh` | `balance_remain` | 积分 |
| Raccoon | `/api/web/points/v1/balance` | `available_points` / `daily_points` | 可用总额 / 当日额度 |
| Trae | quota detail | `basic_usage_amount`(milli$) / `basic_usage_limit`($) | **单位不同** |
| Qoder | `/quota` | `price_factor` 计费 | — |

映射到统一三件套：

```python
account.balance_remain          = available        # 可用
account.credits_expiring_soon   = daily            # 会过期的部分
account.credits_snapshot        = json({...})      # 原始结构，供明细页
```

### ★ 空结构必须抛错，不能取 0

上游 HTTP 200 + 空 body 时，`request_json` 返回 `None`。如果这里变成 `{}`
再逐字段取 0，`apply_quota` 会把**真实余额覆盖成 0**，账号还是 active ——
界面上看起来是「这个号没钱了」，实际只是没读到。

```python
if not isinstance(data, dict) or not data:
    raise UpstreamError("积分查询返回空结构（HTTP 200）", kind="upstream")
if not any(k in data for k in _POINTS_KEYS):
    raise UpstreamError("积分查询缺余额字段", kind="upstream")
```

调用方按 `partial` 处理：**保留上次快照，不写 0**。

### 缓存命中：`cached_tokens` 要一路带到 UI

```
上游 usage.prompt_tokens_details.cached_tokens
  → proxy 归一
  → usage_logs.cached_tokens
  → 前端「缓存命中」列
```

少了任何一环，界面永远是 0，用户会以为网关没做缓存（实测第二次请求
`cached_tokens=256`）。**注意**：缓存命中**不减倍率**，它只是上游的计费口径，
别自作聪明打折。

---

## 三值语义（关键设计）

用 `credit_multiplier` 一个字段表达三种含义：

| 值 | 含义 | 前端显示 | 是否免费 |
|---|---|---|---|
| `> 0` | 收费，按倍率 | `×2.00` | ❌ |
| `= 0` | **真免费** | `FREE` | ✅ |
| `< 0` | **无量纲**（按 token 计量） | `按用量` | ❌ |
| `null` | 尚未同步 | `未知` | ❌ |

**为什么用 -1 而不是 0 表示「无量纲」：**

```python
# 错：-1 会被 `or 0` 抹成 0，然后免费判定就命中了
"credit_multiplier": m.credit_multiplier or 0

# 对：原样输出
"credit_multiplier": m.credit_multiplier
```

---

## 落地清单（接新平台必做）

### 1. 找倍率字段：先搜中文，再搜英文

```bash
# ① 在 asar 主体里搜中文（最有效）
python -c "
import pathlib
d = pathlib.Path('app.asar').read_bytes()
for kw in ['倍率','积分','计费','倍']:
    i = d.find(kw.encode())
    if i > 0: print(kw, hex(i))
"

# ② 再搜英文字段名
# ③ 最后看 /models 的完整字段清单
```

字段可能出现在**五个地方**：

```
顶层             {"price_factor": 0.5}
命名空间子对象    {"x_qoder": {"price_factor": 0.5}}
字符串字段        {"credits": "x0.50"}
★ 显示名内嵌      {"name": "DeepSeek V4.1 Flash(x2.0)"}
★ 双数字字段      {"billing_multiplier": 0.5,
                  "billing_effective_multiplier": 0.0}   ← 实扣优先
```

解析函数必须覆盖全部五处。**「搜不到」优先怀疑「没把字段从上游带出来」**，
而不是「平台没有这个概念」——小浣熊就是这么被误判成「无倍率」的：
字段在上游响应里，被自己的 `fetch_models()` 过滤掉了。

> 判据：`fetch_models()` 里 `flat.append({...})` 的键，等于**上游能力的上限**。
> 少写一个键，上面所有层都拿不到这个信息，而且不会报错。

### 2. 别覆盖上游的 `name`

```python
# 错：覆盖成 model_id，倍率信息直接丢失
"name": model_id

# 对：保留原名，另给结构化的
"name": display_name,
"x_<platform>": {"display_name": ..., "clean_name": ..., "multiplier": ...}
```

### 3. 免费判定必须按平台 + 按值

```python
# 错：只按 model_id（多平台后同名模型会串）
return {m.model_id for m in rows if m.credit_multiplier == 0}

# 对：按（平台, model_id）
return {(m.platform, m.model_id) for m in rows if m.credit_multiplier == 0}
```

**实测踩过**：`qwen3.8-flash` 在 Trae 免费（0），在 Loomy 是 0.8（收费）。
只按 id 判会把两边搞混。

### 4. 计费口径按值判，不按平台名

```python
# 错：写死平台名，每加一个平台都要改
"billing_mode": "upstream_token" if platform == "trae" else "token_multiplier"

# 对：按倍率值
def _billing_mode(model):
    if is_media_model(model.model_id):
        return "upstream_usage"       # 图片/视频按 upstream usage.credit
    if model.credit_multiplier is None or model.credit_multiplier < 0:
        return "upstream_token"       # 该平台按实际 token 计量
    return "token_multiplier"         # 有倍率（含 0 = 免费）
```

### 5. 前端三态渲染

```javascript
const rawMult = ...;  // 从后端 credit_multiplier 读，不从前端 metadata 推测

if (rawMult == null)      → 未知
else if (rawMult < 0)     → 按用量
else if (rawMult === 0)   → FREE
else                      → ×N
```

**不要从前端 metadata 推测价格**：`upstream_metadata` 只保留
name/vendor，各平台的倍率字段都丢了。

---

## ★ 各平台倍率基准不同，不能横向比较

同一类模型在不同平台的倍率差很多：

| 模型 | Loomy | Qoder |
|---|---|---|
| Qwen3.8-Max | **×12.0** | ×0.2 |
| Kimi | ×6.5 | ×1.0 |
| DeepSeek | ×2.0 | ×0.5 |
| GLM-Flash | ×0.8 | ×0.1 |

**这是正常的**：倍率的基准（1.0 代表多少积分/token）由各平台自己定。
展示时必须带平台上下文，不要拿两边的数字比大小。

---

## 验证方法

### 后端解析

```python
from admin.routers.models import _parse_multiplier, split_display_multiplier

# 五种形态
cases = [
    ("qoder 顶层", {"price_factor": 0.2}),                # 0.20
    ("qoder 嵌套", {"x_qoder": {"price_factor": 0.5}}),   # 0.50
    ("qoder 免费", {"x_qoder": {"price_factor": 0.0}}),   # 0.00
    ("wb",         {"credits": "x0.50"}),                 # 0.50
    ("loomy",      {"name": "DeepSeek V4.1 Flash(x2.0)"}),# 2.00  ★
    ("真没有",      {}),                                   # -1.00
]

# 显示名解析（照抄客户端逻辑的验证）
for text in [
    "Spark-X2.6 正式版(限时免费)(x0.0)",   # ("Spark-X2.6 正式版", "0.0")
    "MiniMax M3 （x4.0）",                 # ("MiniMax M3", "4.0")
    "Kimi k2.6 （x6.5）",                  # ("Kimi k2.6", "6.5")
    "Hy image 3.5 preview",               # ("Hy image 3.5 preview", "")
]:
    print(split_display_multiplier(text))
```

### 前端（浏览器实测，期望输出）

```
[Qoder]  qfmodel      FREE      ← 0 倍率
         dfmodel      FREE
         qmodel_38max ×0.20
         dmodel       ×0.50
         gmodel       ×0.80
[Loomy]  spark-x2.6   FREE      ← 0 倍率（限时免费）
         spark-x      FREE
         GLM-5.3-Flash ×0.80
         DeepSeek-V4.1-Flash ×2.00
         MiniMax-M3   ×4.00
         Kimi-k2.6    ×6.50
         qwen-3.8-max ×12.00
[WorkBuddy] hy3       FREE
            hy4-preview ×0.29
            default    ×2.20
```

---

## 各平台现状（2026-11-04 实测）

| 平台 | 倍率来源 | 免费 | 收费 | 无量纲 |
|---|---|---|---|---|
| workbuddy | `credits` 字符串 | 2 | 34 | 0 |
| trae | `credits` 字符串 | 59 | 11 | 0 |
| qoder | `price_factor` 数字 | 2 | 7 | 0 |
| loomy | **显示名后缀** | 2 | 7 | 0 |
| raccoon | **`billing_effective_multiplier` 双字段** | 2 | 7 | 0 |

小浣熊实测落库值：`1.0 / 0.75 / 0.25 / 0.2 / 0`（两个 sensenova 限免为 0）。

WorkBuddy 有 6 个 0 倍率模型，但 4 个是图像/视频
（`hunyuan-image-*` / `kling-video-*` / `minimax-video-*`）——
倍率对媒体模型不适用，它们走 `upstream_usage`。

---

## 一句话总结

```
① 找倍率：先搜中文（「倍率」「计费」），再搜字段名，最后看显示名
② 显示名里带倍率时，照抄客户端的 splitModelDisplayName（两层后缀、全角括号）
③ 双数字字段（标价/实扣）→ 实扣优先，标价留作解释
④ 有倍率 → 原样存（含 0）；真没有 → 存 -1（不是 0）
⑤ 判免费 → 倍率 == 0 才算，且按（平台, 模型）双键判
⑥ 记账用同一个字段：total_tokens × 倍率 / 1000；上游给 0 也要按倍率估算
⑦ 余额读不到 ≠ 余额 0：空结构必须抛错，保留上次快照
⑧ 各平台倍率基准不同，展示要带平台上下文
```
