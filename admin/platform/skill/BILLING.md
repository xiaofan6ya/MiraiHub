# 计费与倍率：五套口径的识别与落地

用户的核心规则：**只有 0 倍率才算免费。**

这条规则听起来简单，但接平台时会遇到五种完全不同的上游形态。
判错的后果是「把收费模型标成免费」——用户拿到账单才发现。

---

## 五种上游形态（全部实测）

| 平台 | 倍率来源 | 形态 | 样例 |
|---|---|---|---|
| WorkBuddy | `credits` | 字符串 | `"x0.50"` |
| Trae | `credits` | 字符串 | `"x0.79"` |
| Qoder | `price_factor` | 数字 | `0.5` |
| **Loomy** | **模型显示名后缀** | **字符串内嵌** | `"DeepSeek V4.1 Flash(x2.0)"` |
| （兜底） | 无 | — | 返回 `-1` |

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

字段可能出现在**四个地方**：

```
顶层             {"price_factor": 0.5}
命名空间子对象    {"x_qoder": {"price_factor": 0.5}}
字符串字段        {"credits": "x0.50"}
★ 显示名内嵌      {"name": "DeepSeek V4.1 Flash(x2.0)"}
```

解析函数必须覆盖全部四处。

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

## 各平台现状（2026-10-29 实测）

| 平台 | 倍率来源 | 免费 | 收费 | 无量纲 |
|---|---|---|---|---|
| workbuddy | `credits` 字符串 | 2 | 34 | 0 |
| trae | `credits` 字符串 | 59 | 11 | 0 |
| qoder | `price_factor` 数字 | 2 | 7 | 0 |
| loomy | **显示名后缀** | 2 | 7 | 0 |

WorkBuddy 有 6 个 0 倍率模型，但 4 个是图像/视频
（`hunyuan-image-*` / `kling-video-*` / `minimax-video-*`）——
倍率对媒体模型不适用，它们走 `upstream_usage`。

---

## 一句话总结

```
① 找倍率：先搜中文（「倍率」「计费」），再搜字段名，最后看显示名
② 显示名里带倍率时，照抄客户端的 splitModelDisplayName（两层后缀、全角括号）
③ 有倍率 → 原样存（含 0）；真没有 → 存 -1（不是 0）
④ 判免费 → 倍率 == 0 才算，且按（平台, 模型）双键判
⑤ 各平台倍率基准不同，展示要带平台上下文
```
