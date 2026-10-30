# 能力矩阵：思考档位 / 上下文参数

用户会问：「思考开启有没有问题？切换推理强度有没有问题？设置上下文呢？」

**每个平台答案都不一样，必须实测。** 这份记录已验证的结论。

---

## 三层要分清

| 层 | 问题 | 怎么验 |
|---|---|---|
| ① 协议支持 | 下游能不能传这个参数 | 读 OpenAI / Anthropic 规范 |
| ② 上游支持 | 上游认不认、**有没有效果** | 发不同档位，比 `reasoning_content` 长度 |
| ③ 要不要暴露 | 不支持时**不能假装支持** | 见下 |

> 第 ③ 层最容易错：声明支持但没效果，用户以为调了其实没调，
> 出问题时无法归因。

---

## 实测结论

### Loomy（2026-10-28，DeepSeek-V4.1-Flash）

| 参数 | 传了有变化吗 | 结论 |
|---|---|---|
| `reasoning_effort` = none/low/medium/high/xhigh | ❌ **完全无变化** | 上游忽略 |
| `enable_thinking` = true/false | ❌ **完全无变化** | 上游忽略 |
| `chat_template_kwargs.enable_thinking` | ❌ 无变化 | 上游忽略 |
| `/no_think` 前缀 | ❌ 无效 | 上游忽略 |
| `max_tokens` / `max_completion_tokens` / `max_output_tokens` | ✅ 都认 | 三个别名等价 |
| 未知参数（`top_k` / `bogus_param`） | ✅ 被忽略，不报错 | 可安全透传 |

实测数据（同一个问题，只改 effort）：

```
effort=none    content='4'  reasoning_len=59  completion=21
effort=low     content='4'  reasoning_len=64  completion=21
effort=medium  content='4'  reasoning_len=59  completion=20
effort=high    content='4'  reasoning_len=53  completion=20
effort=xhigh   content='4'  reasoning_len=52  completion=19
effort=(默认)  content='4'  reasoning_len=55  completion=19
```

`reasoning_len` 的波动是**采样随机性**，不是档位生效
（如果生效，none vs xhigh 应该差一个数量级）。

### 模型目录里声明了 ≠ 真的支持

Loomy 的 `/models` 给每个模型都写了：

```json
"reasoning_efforts": ["none", "low", "medium", "high", "xhigh"]
```

**但实测传了没效果。** 所以不能只看目录声明，
必须**自己发两档对比** `reasoning_content` 长度。

### 但 spark 系列有真实差异

```
spark-x2.6   finish=length  content=''        ← 思考链吃满 max_tokens
DeepSeek-V4.1-Flash  finish=stop  content='4' ← 正常
```

这说明 spark 系列的思考链**更长**（会吃掉 64 token 全部预算），
不是「档位生效」，而是「基础思考量就很大」。

**处置**：不把 spark 系列作为默认；在 `THINKING_HUNGRY` 里标记。

---

## 接新平台时必须测的六项

```python
# 伪代码：拿到 token 后逐项跑
for effort in ("none", "low", "medium", "high", "xhigh", None):
    r = chat(model, msg, reasoning_effort=effort, max_tokens=64)
    print(effort, r.finish, len(r.reasoning_content), r.content)

for flag in (True, False):
    r = chat(model, msg, enable_thinking=flag, max_tokens=64)
    print(flag, len(r.reasoning_content), r.content)
```

| 检查 | 判据 |
|---|---|
| 1. 档位有效果吗 | `none` 与 `xhigh` 的 reasoning 长度差 ≥ 2 倍 |
| 2. 能关思考吗 | `enable_thinking=false` 时 reasoning 长度接近 0 |
| 3. 关思考后正文正常吗 | `content` 非空 |
| 4. 上下文参数名 | 逐个试 `max_tokens` / `max_completion_tokens` / `max_output_tokens` |
| 5. 未知参数报错吗 | 传 `bogus=1`，看是否 400 |
| 6. 长上下文支持 | 传大 max_tokens，看是否被截断 |

---

## 落地规则（写进平台实现）

### 上游不支持档位时

```python
def _thinking_choice(body: dict) -> bool | None:
    """读下游的思考意图。

    上游不支持档位时，**仍然接受**这个参数（不报错），
    但不做任何转换 —— 让客户端不因为多传一个字段而失败。
    """
    ...
```

**不要**：
- ❌ 假装支持（改了参数但不生效，用户以为生效）
- ❌ 直接报 400（客户端只是多传一个字段）
- ❌ 静默丢弃且不记录（排障时看不出）

**要**：
- ✅ 接受参数、透传上游（若上游忽略也无害）
- ✅ 在 `/v1/models` 的 `x_<platform>` 里**如实标注** `reasoning_efforts`（上游给的）
- ✅ 文档写清「实测无效果」，避免下次重复踩

### 上游支持档位时

需要在请求体里正确映射三处（各协议不同）：

| 下游 | 字段 |
|---|---|
| OpenAI | `reasoning_effort: "low"\|"medium"\|"high"` |
| Anthropic | `thinking: {type: "enabled", budget_tokens: N}` |
| Responses | `reasoning: {effort: "low"}` |

Anthropic 的 `budget_tokens` 要换算成档位：

```python
budget = thinking.get("budget_tokens")
effort = "low" if budget <= 2048 else "medium" if budget <= 8192 else "high"
```

---

## 已接入平台的现状

| 平台 | 档位 | 上下文参数 | 备注 |
|---|---|---|---|
| WorkBuddy | 上游支持 | `max_tokens` | 有倍率概念 |
| Trae CN | 不支持 | `max_tokens` | — |
| Trae Intl | 不支持 | `max_tokens` | — |
| Qoder | ✅ 支持 `thinking_config` | `max_tokens` | 但**必须剥掉** `thinking_config` 否则覆盖 `enable_thinking` |
| Loomy | ❌ 传了无效 | 三个别名都认 | 未知参数被忽略 |
