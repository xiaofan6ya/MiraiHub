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
| Raccoon | ✅ `reasoning_effort` 透传（low → 200） | `max_tokens`；目录给 `context_window` / `max_tokens` | 档位未在目录声明；能力靠 `tags`（`vision` / `reasoning`） |

---

## ★ 上下文窗口 / 最大输出：客户端从哪读？

这是「模型配置里上下文全是空」的根因。**客户端不问上游，只问我们的
`/v1/models`。**

DSH 的 `discoverModels`（反编译 `app.asar` 确认）zod schema：

```ts
{ id: string, name?: string,
  contextWindow?: number,          // 正整数，缺省 = 未知
  maxTokens?: number,              // 正整数
  inputModalities?: ("text"|"image")[] }
```

所以平台接入时必须走完这条链路：

```
上游目录（context_window / max_tokens / tags）
  → fetch_models() 带出来          ← 漏了这里，后面全空（五·补27）
  → model_configs.upstream_metadata
  → _capabilities_of()             ← tags 兜底推 supports_images / supports_reasoning
  → _caps_flat()                   ← 输出 contextWindow / maxTokens / inputModalities
  → /v1/models
  → 客户端表单
```

### 三层校验（缺一层就可能是空值）

```bash
# 1) 上游原始字段
python -c "from admin.platform.raccoon.core import client; ..."   # 看 context_window

# 2) 我们的接口
curl -s $BASE/v1/models -H "Authorization: Bearer $KEY" | jq '.data[] | select(.owned_by=="raccoon")'

# 3) 客户端实际渲染
#    模型配置页每行应有「1M 上下文 · 100K 输出 · 图片输入 · 推理」摘要
```

### 两个「看起来该有但没有」的字段

| 字段 | 为什么没有 | 怎么办 |
|---|---|---|
| 推理档位（`reasoningEfforts`） | DSH schema 里**没有这个键**，它不读 | 只能手工选；`reasoning_effort` 透传有效 |
| 上下文窗口 | 上游没给 `context_window` | 走品牌兜底 → 再不行落保守值（128K / 16K） |

### 三级兜底链（现行实现，改之前先读）

```
上游 metadata（contextWindow / maxInputTokens / tags）
  → ① 品牌兜底 _CAP_FALLBACK（按模型名里的 glm / deepseek / qwen …）
  → ② 保守值 128K / 16K / 纯文本 / 不支持推理
```

第 ③ 级是**取舍**，不是理想状态：给一个保守数字，好过让客户端显示灰块
（灰块会让用户以为模型坏了）。但它也意味着**界面上显示 128K 不代表上游
真的支持 128K**。

> 排查「上下文显示不对」时，先看这一行是第几级兜底给的。
> 想让它准，就往上游 `upstream_metadata` 补真实字段，而不是在兜底表里
> 调数字 —— 那只是把错误藏得更深。
