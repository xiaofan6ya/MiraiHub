# 协议转换 · 平台选路 · 缓存

这三件事是同一条链路上的三个节点，任何一环「静默降级」，用户看到的现象
都是**同一个**：`400 模型 'xxx-平台' 不存在或已被禁用`。

这份文档按**请求实际流经的顺序**写，每一节都给出「错在哪、为什么错、
怎么验」。所有结论来自 2026-11-04 的小浣熊（raccoon）接入实测。

---

## 0. 一次 `/v1` 请求的完整路径

```
客户端
  │  POST /v1/chat/completions  {"model":"sn-glm-5-3-flash-raccoon"}
  ▼
dispatch.py  ← 唯一的平台判断点（中间件，所有 /v1/* 都过）
  ├── ① 预读 body          _should_peek / _read_body / _replay_receive
  ├── ② 取模型名           _model_of_body（前 8KB 正则 → 整段 JSON）
  ├── ③ 解析平台后缀       model_suffix.resolve()
  ├── ④ 归属反查兜底       upstream_of()（没后缀、或后缀拼错时）
  ├── ⑤ 还原上游 model_id  upstream_of_in_platform(slug, storage)  ★
  ├── ⑥ 选平台             cached_pick()（聚合 Key 才走）
  └── ⑦ 改写 body 后回放   _replace_model + _replay_receive
  ▼
平台 gateway.dispatch()  →  上游
  ▼
proxy.py 记账（倍率 × token）→ usage_logs
```

**①⑤ 两处是最容易「静默出错」的地方**：错了不报错，只是模型名没被识别，
请求落到默认平台，然后默认平台白名单里没有这个带后缀的名字 → 400。

---

## 1. 选路：三条判据，顺序不能乱

| 优先级 | 判据 | 实现 | 适用 |
|---|---|---|---|
| 1 | **显式后缀** `xxx-raccoon` | `model_suffix.resolve()` | 客户端点名要某平台 |
| 2 | **模型归属反查** | `upstream_of()` | 没后缀，或后缀拼错 |
| 3 | 默认平台 | `registry.DEFAULT_PLATFORM` | 兜底 |

### 1.1 带后缀的请求**永远**走平台网关

哪怕后缀指向的就是默认平台（`xxx-workbuddy`），也必须走平台网关，而不是
丢给主应用（`proxy.py` 里的 WorkBuddy 适配）。理由：平台网关有账号池、
倍率、风控；主应用那条路没有。

```python
explicit_platform = bool(suffix_platform)
```

### 1.2 后缀解析不出来时，不许猜

`upstream_of_in_platform()` 找不到就返回 `""`，调用方**保持原名**转发。
不要「猜一个最像的」——猜错等于用别的模型跑用户的请求，比报错严重得多。

---

## 2. ★ 预读 body：必须无条件，只读大小会出事

### 现象

> 用着好好的，聊一会儿就 400：
> `模型 'sn-deepseek-v4-1-flash-1017-raccoon' 不存在或已被禁用；
> 该名字看着像平台模型（raccoon）`

用户第一反应是缓存/白名单。**实际是中间件没读到模型名。**

### 根因

旧实现：

```python
# ❌ 只有「带 Content-Length 且 ≤ 256KB」才预读
for name, value in headers:
    if name.lower() == b"content-length":
        return raw.isdigit() and 0 < int(raw) <= _BODY_LIMIT
return False
```

两种情况必然退化：

| 情况 | 为什么会出现 |
|---|---|
| **chunked**（无 `Content-Length`） | 部分客户端/代理默认分块上传 |
| 请求体 **> 256KB** | 多轮对话、长上下文、带图、工具调用结果 |

退化路径：不预读 → `raw_model` 为空 → 后缀解析不出 → 落默认平台 →
主应用白名单没这个名字 → **400**。

> 「用一会儿才出错」正好对应「对话变长、body 越过阈值」。

### 修法（现行实现，别再改回去）

```python
_BODY_METHODS   = frozenset({"POST", "PUT", "PATCH"})
_BODY_HARD_CAP  = 32 * 1024 * 1024      # 32MB

def _should_peek(request) -> bool:
    if method not in _BODY_METHODS: return False
    for name, value in headers:
        if name.lower() == b"content-length":
            if not raw.isdigit(): return True      # 长度不可信：照样读
            size = int(raw)
            if size > _BODY_HARD_CAP:
                logger.warning("...超过预读上限，本次不做后缀路由...")  # ← 必须有
                return False
            return size > 0
    return True                                     # chunked：照样读
```

四条铁律：

1. **写请求一律预读**，不看 `aggregate` 标志、不看 Key 类型；
2. **没有 `Content-Length` 也要读**（chunked）；
3. **超上限不预读时必须 `logger.warning`** —— 否则又是一次「无缘无故 400」；
4. **读完必须回放**，`_read_body` 绝不允许「超限返回空串」。

### 第 4 条为什么是硬约束

`_read_body` **消费**了 `receive`。返回空串 = 下游拿到空 body =
`400 bad json`，而且是**每一个**大请求都挂。

```python
# ❌ 旧实现：超限就把 body 吃掉
return b"".join(chunks) if 0 < total <= limit else b""

# ✅ 现行：完整读完、完整回放，只在超限时记日志
if total > limit:
    logger.warning("请求体 %d 字节（上限 %d）仍完整回放给下游", total, limit)
return b"".join(chunks)
```

### `_replay_receive` 的两个禁忌

```python
async def receive():
    if not state["sent"]:
        state["sent"] = True
        return {"type": "http.request", "body": body, "more_body": False}
    await asyncio.sleep(3600)          # ← 挂住，不要立刻返回
    return {"type": "http.disconnect"}
```

| 错误写法 | 后果 |
|---|---|
| 第二次直接返回 `http.disconnect` | 下游认为客户端断开，**不产出响应**（`RuntimeError: No response returned.`） |
| 第二次立即返回空 `http.request` | `StreamingResponse` 的 `listen_for_disconnect` **忙循环**占满事件循环 → 流式请求 60 秒 0 字节 |

### 取模型名：前 8KB 正则优先

```python
head = body[:8192]
match = re.search(rb'"model"\s*:\s*"([^"\\]{1,200})"', head)
if match: return match.group(1).decode("utf-8", "replace").strip()
# 取不到才整段 json.loads（一轮长对话几 MB，每跳都 parse 会拖慢首字）
```

`model` 字段几乎总在最前面，正则先命中；但**不能只靠正则** ——
有的客户端会把它塞在 `metadata` 后面，所以必须有 JSON 兜底。

### 验证

```python
# 300B / 600KB / chunked 三种都必须拿到带后缀的模型名
assert d._should_peek(req_of(body, with_length=False)) is True   # chunked
assert d._model_of_body(body_600kb) == "sn-deepseek-v4-1-flash-1017-raccoon"
```

跑 `scripts/check_hotpath.py` 的第 1、2 项即可。

---

## 3. ★ 模型名还原：同名不同写法

### 现象

`glm-5.3-flash-loomy` 在网关里「能用」，但一发就 `模型不存在`；
同一个模型在 WorkBuddy 上正常。

### 根因

`/v1/models` 对外统一发**小写 slug + 平台后缀**，但上游只认自己的拼法：

| 对外 slug | WorkBuddy | Trae | Loomy |
|---|---|---|---|
| `glm-5.3-flash` | `glm-5.3-flash` | `glm-5.3-flash` | **`GLM-5.3-Flash`** |
| `deepseek-v4.1-flash` | `deepseek-v4.1-flash` | **`DeepSeek-V4.1-Flash`** | `DeepSeek-V4.1-Flash` |
| `minimax-m3` | — | `minimax-m3` | **`MiniMax-M3`** |

原来 dispatch 只**剥后缀**、不**还原 id**：`glm-5.3-flash-loomy` → 剥成
`glm-5.3-flash` → 原样发给 Loomy → Loomy 没这个 id → 400。

更隐蔽的是 `upstream_of()` 是**全局**扫描、不分平台：同名时可能返回别家的
拼法，于是「还原」成了另一个平台的名字。

### 修法

平台**确定之后**，按 `(platform, model)` 收窄再还原一次：

```python
_real = models_router.upstream_of_in_platform(cur_model, storage_platform, db)
if _real and _real != cur_model:
    body = _replace_model(body, _real)
    receive = _replay_receive(body, tail)
```

匹配顺序（两级）：

1. 完全同名（大小写不敏感）—— 覆盖 99%；
2. `slugify_model_id()` 归一后相同 —— `GLM-5.3-Flash` ↔ `glm53flash`。

### 两条不可动摇的规则

- **必须带 `enabled == 1` 过滤**：把已禁用的模型还原出来，等于绕过禁用；
- **找不到就返回空串**，调用方保持原名，让上游自己报错（可归因）。

### 验证

```python
from admin.platform import models_router as mr
assert mr.upstream_of_in_platform("glm-5.3-flash", "loomy") == "GLM-5.3-Flash"
assert mr.upstream_of_in_platform("deepseek-v4.1-flash", "trae") == "DeepSeek-V4.1-Flash"
assert mr.upstream_of_in_platform("不存在的模型", "loomy") == ""
```

> `scripts/check_model_conflicts.py`（仓库根，不在 skill 里）负责扫
> **同名不同物**的真实冲突：退出码 2 = 存在未映射，可进流水线。
> 当前 87 行 / 76 个唯一 id / 9 组同名，全部是同一模型的写法差异。

---

## 4. 协议转换

### 4.1 四个入口

| 入口 | 协议 | 实现 |
|---|---|---|
| `POST /v1/chat/completions` | OpenAI Chat | `proxy.py` / 平台 `gateway.dispatch` |
| `POST /v1/responses` | OpenAI Responses | 同上（字段映射不同） |
| `POST /v1/messages` | Anthropic Messages | 同上（`system` / `content[]` / `thinking`） |
| `POST /v1/messages/count_tokens` | Anthropic | 只计数，**别真调上游生成** |

### 4.1.1 Anthropic → Chat 的字段映射（`anthropic_adapter.py`）

| 下游（Anthropic） | 上游（Chat） | 注意 |
|---|---|---|
| `system`（字符串或 block 数组） | `messages[0].role=system` | 数组要抽文本，别整个塞进去 |
| `messages[].content[]` 文本块 | `content` 字符串 | 多块要拼接 |
| `tool_use` 块 | `tool_calls[]` | `id` / `name` / `input` 一一对应 |
| `tool_result` 块 | `role=tool` 消息 | 必须紧跟在对应 `tool_calls` 后 |
| `tools[].input_schema` | `tools[].function.parameters` | 结构完全相同，只改名 |
| `tool_choice` | `tool_choice` | Anthropic 的 `{type:"tool",name}` → `{type:"function",function:{name}}` |
| `thinking.budget_tokens` | `reasoning_effort` | 按预算换算档位（见 CAPABILITY-MATRIX） |
| `stop_sequences` | `stop` | 单数/复数不同名 |
| `metadata` | — | 丢弃，上游不认 |

**`stream` 的处理**：适配器内部统一把 `stream` 置 `True`（网关自己控流），
返回时再按客户端原来的请求决定是否转回非流式。所以：

> 上游非流式 + 客户端要流式 → 网关自己切 SSE；
> 上游流式 + 客户端要非流式 → 网关**攒完再吐**，但 TPS/TTFT 要按真实口径记
> （`FIXED-BUGS.md` 五·补8、12、13）。

### 4.1.2 Responses 与 Chat 的差异（只列会踩的）

| 概念 | Chat | Responses |
|---|---|---|
| 输入 | `messages[]` | `input`（字符串或 item 数组） |
| 系统提示 | `role=system` | `instructions` |
| 工具 | `tools[].function` | `tools[]`（扁平，无 `function` 层） |
| 工具调用回填 | `role=tool` | `function_call_output` item |
| 推理档位 | `reasoning_effort` | `reasoning.effort` |
| 输出 | `choices[].message` | `output[]`（typed items） |

> 转换时**不要复用 Chat 的结构体**：`output[]` 是带 `type` 的联合类型，
> 硬套 `choices` 会让客户端解析失败，而且错得很隐蔽（HTTP 200）。

### 4.1.3 ⚠️ Anthropic 协议下，`-平台` 后缀会被"档次映射"吃掉

`/v1/messages` 有两段模型名处理，**顺序**决定了行为：

```
dispatch（剥后缀、按平台还原）  →  proxy._map_anthropic_model（claude-* → 档次模型）
```

于是 `claude-sonnet-4-5-raccoon`：

1. dispatch 剥掉 `-raccoon`，把 body 改成 `claude-sonnet-4-5`；
2. `_map_anthropic_model` 看到 `claude-` 前缀且库里没有这个 id →
   按 `_ANTHROPIC_MODEL_TIERS` 映射到配置的档次模型（可能在**另一个平台**）。

结果：用户以为"用 Anthropic 协议指定小浣熊"，实际走了默认的档次映射。

**结论**：Anthropic 协议下要指定平台，请直接用**该平台的真实模型名**
（`sn-glm-5-3-flash-raccoon` 这种不带 `claude-` 前缀的），或者接受档次映射。

**另一条硬约束**：`_map_anthropic_model` 不会把"被禁用/移出分组"的模型退回
`auto` —— 否则一次本该被拒绝的调用会静默换成别的模型并成功（越权 + 计费错）。
改这个函数时别"顺手优化"成 fallback。

### 4.2 usage 归一（缓存命中显示就靠这）

不同上游给的形状不同，**统一成**：

```python
usage = {
  "prompt_tokens": int,
  "completion_tokens": int,
  "total_tokens": int,
  "prompt_tokens_details": {"cached_tokens": int},   # ★ 缓存命中
  "completion_tokens_details": {"reasoning_tokens": int},
}
```

- **`cached_tokens` 必须透传**（小浣熊实测第二次请求回 `cached_tokens=256`）。
  丢了这一项，界面上「缓存命中」永远是 0，用户会以为网关没做缓存。
- 上游用别的键名（`cache_read_input_tokens` / `prompt_cache_hit_tokens`）时，
  在平台层归一，**不要在 proxy 里按平台名分支**。

### 4.3 流式：宁可不流，不要假流

上游只有非流式时，网关自己切 SSE 是可以的，但：

- **TTFT / TPS 口径必须按 DSH**（见 `FIXED-BUGS.md` 五·补8、五·补12）；
- 不能「读完整个响应再一次性吐出」还标成流式 —— 客户端会以为网络卡死
  （见五·补13）。

### 4.4 `/v1/models` 是协议的一部分，不是给人看的

客户端（尤其 DSH）靠它**填模型表单**。反编译 DSH 的 `discoverModels`，
zod schema 只认这五个键：

```ts
{ id: string, name?: string, contextWindow?: number,
  maxTokens?: number, inputModalities?: ("text"|"image")[] }
```

我们原来只给 `id` / `name`，于是模型配置页里**上下文窗口、最大输出、
图片输入全是空**。现在 `_caps_flat()` 输出：

| 键 | 来源 | 备注 |
|---|---|---|
| `contextWindow` / `context_length` / `max_input_tokens` | `caps.context_window` | DSH 读第 1 个，Cherry/Lobe 读后两个 |
| `maxTokens` / `max_output_tokens` | `caps.max_output_tokens` | 同上 |
| `inputModalities` / `architecture.input_modalities` | `caps.supports_images` | **数组，不是布尔** |
| `reasoningEfforts` | `caps.reasoning_efforts` | 选填 |

> ⚠️ **`/v1/models` 还有最后一道白名单**：`to_openai_strict()` 只放行
> `id/object/created` + `name/contextWindow/maxTokens/inputModalities/
> reasoningEfforts/defaultReasoningEffort/canDisableThinking`。
> `_caps_flat()` 产出的 OpenRouter 别名（`context_length`、
> `max_output_tokens`、`architecture`）**在 `/v1/models` 里不会出现**，
> 只存在于内部 payload（管理端聚合视图）。
>
> 想在 `/v1/models` 里也下发别名，改 `to_openai_strict()` 的白名单 ——
> 但先确认目标客户端不会因为未知键报错（严格的 serde/Go struct 会）。
> 这是**已知的、有意为之**的裁剪，别当成 bug 反复"修"。

**标签兜底**：有的平台只给 `tags`（`vision` / `reasoning`），不给布尔位。
`_capabilities_of()` 里必须能从 tags 推出来，否则「能看图的模型」被标成不能。

```python
tags = {t.lower() for t in (model.get("tags") or [])}
if "vision" in tags:    caps["supports_images"] = True
if "reasoning" in tags: caps["supports_reasoning"] = True
```

### 4.5 一个诚实的限制：推理档位 DSH 不会自动填

DSH 的 schema 里**没有**档位字段，所以「思考推理程度」那一栏只能手工选。
不要为此伪造 `reasoningEfforts` 之外的键。

但 `reasoning_effort` **能透传到上游**（小浣熊实测 `low` → 200），
所以用户手选了就有效。文档里要写清这个区别，别让人以为「选了没用」。

### 4.6 `description` 不能丢

小浣熊的模型名是代号（`sn-glm-5-3-flash` / `raccoon-090dd3`），
`display_description` 是**唯一**能解释「这是什么模型」的文案。
`fetch_models` 第一版只留了 name/display/visible，把它丢了 ——
用户在模型配置里看到一列代号，根本不知道哪个该开。

规则：**平台返回的描述性字段，一路带到 `model_configs.note` / 前端提示**，
中间任何一层都不许「只留 id 和 name」。

---

### 4.7 ★ 会话 id：只能从客户端真实 id 稳定派生，**不许伪造随机值**

很多上游的信封或请求头里有一个「会话 / 聊天 id」：

| 平台 | 字段 |
|---|---|
| Loomy | `ChatId`（还有 `MsgId` / `TurnId`） |
| Qoder | 信封里的 `session_id` |
| WorkBuddy | `X-Conversation-ID` |

**这个值必须从下游（客户端）真实会话标识稳定派生出来，同一账号同一对话
永远是同一个值。绝不允许每请求 `uuid4()`，也不允许凭空调一个随机串糊上去。**

#### 为什么（两条代价都很实在）

1. **上游前缀缓存打不中**。缓存是按会话/前缀算的，每次都换 id，上游眼里
   每次都是全新对话 —— 系统提示、工具定义、历史消息全部重算，
   首字延迟和费用一起上去，而且**面板上的缓存命中率永远是 0**。
2. **风控特征**。一个号在几分钟内「新建」几十个会话，是脚本行为最典型的形状。

#### 正确写法（唯一的入口）

```python
from admin import pool

sid = pool.stable_session_id("loomy", account_id, body,
                             prefix="gwchat-", size=16)
```

派生规则（`admin/pool.py::stable_session_id`）：

```
会话键（每一层都是确定性的，没有随机分支）：
  1. 客户端显式会话标识：metadata.conversation_id / conversation_id /
     prompt_cache_key
  2. 没有显式标识 → 首条 user 文本的 sha256（同一段对话恒定）
  3. 连消息都没有（探测 / 空请求）→ 退化为「每账号一个固定会话」

会话 id = sha256("平台 | 账号 id | 会话键")[:size]
```

因此天然满足三条：

* **同账号 + 同对话 → 恒定**（缓存能命中）；
* **换号 → 换 id**（一个会话不会跨账号乱串，缓存与风控都按账号算）；
* **客户端什么都没给 → 仍然恒定**（首条消息派生，或每账号一个），
  任何一种情况都不会退化成"每请求一个随机值"。

#### 哪些 id 必须每请求唯一（别搞反）

| id | 生命周期 | 复用后果 |
|---|---|---|
| `ChatId` / `session_id` / `X-Conversation-ID` | 会话级，**稳定** | 缓存失效 / 风控 |
| `MsgId` / `TurnId` | **每请求唯一** | 上游把两条消息当同一条，去重覆盖 |
| `X-Conversation-Request-ID` | **每次上游调用唯一** | 上游少记账（去重键） |

#### 验收（不看代码，看上游）

- [ ] 同一段对话连发两次，抓包/日志里的会话 id **完全一致**；
- [ ] 换一个账号发同一段对话，会话 id **必须不同**；
- [ ] 一次流式请求的 `MsgId` 与上一次不同（否则是去重键撞了）；
- [ ] 上游用量接口里同一账号的会话数增长 ≈ 真实对话数，不是请求数。

---

## 5. 缓存：四处，每处的键都必须带维度

| 缓存 | 位置 | 键 | 失效条件 |
|---|---|---|---|
| `/v1/models` 清单 | `models_router._cache` | `(agg\|full, group_id)` + `signature` | TTL 10s 或 signature 变 |
| 调度选平台 | `gateway_router.cached_pick` | 平台 + realm | TTL 5s 或 `invalidate()` |
| 统计口径 | `scope` 相关缓存 | **platform** | 配置变更 |
| 分组限制 | 同上（`slot_key` 里） | `group_id` | 分组内容变更 |

### 5.1 键漏维度 = 「切了没效果」的头号成因

- 漏 `platform` → 切到 A 平台还显示 B 平台的总数；
- 漏 `group_id` → 第一个分组 Key 的结果被所有分组复用（**越权**）。
  实测：某分组只允许 4 个模型，`/v1/models` 返回 54 个。

### 5.2 `signature` 要覆盖「影响结果的每一张表」

```python
def _signature(db):
    # 模型：启用数 + 最大 id + 启用数之和
    # 分组：条数 + 最大 id     ← 少了这半截，改分组不失效
    return "%s|%s|%s%s" % (count, max_id, enabled_sum, group_sig)
```

> ⚠️ 已知边界：只改**某一行的倍率**、总数不变时 signature 不变。
> 这不影响正确性（记账是实时查库），但**不要**指望 `/v1/models` 里的
> 旧倍率字段立刻刷新。

### 5.3 变更必须主动失效

```python
def invalidate_platform_cache(): ...   # 平台增删/禁用
def invalidate_model_cache(): ...      # 模型配置/同步
```

`save_routing()` / `set_enabled()` / 模型同步 / 分组保存，结尾都要调。
忘了这一步，用户点保存后要等 TTL 才看到变化 —— 现象是「保存了没生效」。

### 5.4 缓存不要缓存「错误」

上游 5xx / 超时 / 空结构，**不写缓存**。否则一次抖动会让整个 TTL 周期都错。

---

## 6. 排查速查表

| 现象 | 先查 | 判据 |
|---|---|---|
| 偶发 400 带后缀模型不存在 | `_should_peek` 是否对 chunked / 大 body 返回 True | 日志里有没有「超过预读上限」 |
| 某平台模型全部 400 | `upstream_of_in_platform` 是否还原成功 | 日志 `模型名按平台还原 | A -> B` |
| 切平台统计不变 | 缓存键是否带 platform | 直接 `curl` 同一接口看是否一致 |
| 模型配置页能力全空 | `/v1/models` 是否有 `contextWindow` | `curl /v1/models` 看条目 |
| 缓存命中永远 0 | usage 里有没有 `prompt_tokens_details.cached_tokens` | usage_logs 的 `cached_tokens` |
| 一个号几分钟冒出几十个会话 / 缓存命中 0 | 上游会话 id 是不是每请求随机 | 同对话两次请求的 id 是否一致（§4.7） |
| 流式 60 秒 0 字节 | `_replay_receive` 第二次返回了什么 | 是否 `asyncio.sleep` 挂住 |

---

## 7. 改这块的回归清单

```
[ ] POST / 300B 带 Content-Length      → 预读 + 取到 model
[ ] POST / 600KB                        → 预读 + 取到 model
[ ] POST / chunked（无 Content-Length） → 预读 + 取到 model
[ ] GET  /v1/models                     → 不预读（不卡死）
[ ] body > 32MB                         → 不预读但**有 warning 日志**
[ ] 带后缀模型调用                      → 上游回显的 model 是该平台真实 id
[ ] 改模型配置                          → 10s 内 /v1/models 反映
[ ] 改分组                              → 分组 Key 立刻只能看到新范围
[ ] 缓存命中第二次请求                  → usage.cached_tokens > 0 且落库
[ ] 同一对话连发两次                    → 上游会话 id 完全一致（§4.7，不许随机）
[ ] 换账号发同一段对话                  → 上游会话 id 不同（不跨号乱串）
[ ] 每次上游调用的 requestId            → 与上一次不同（去重键，复理会少记账）
```

前四项 `scripts/check_hotpath.py` 已覆盖，可直接跑。
