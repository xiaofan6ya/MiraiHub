# 平台请求体契约（不可通用共享）

> 2026-11-04 两次真实故障的共同结论：**客户端发什么、上游认什么、错了怎么回，
> 全是平台私有知识**。框架层只能提供机制，不能提供「一套通吃的净化规则」。
> 每个平台的网关必须自己维护、自己随上游升级而回归。
>
> 相关已修 bug：[FIXED-BUGS 五·补51 / 五·补52](FIXED-BUGS.md)；
> CodeArts 接入细节见 [CASE-CODEARTS.md](CASE-CODEARTS.md)。

---

## 0. 一句话结论

```
请求体规则 = 平台私有契约
  · role 白名单、tools schema、指纹词表、错误帧格式、终止帧要求 —— 逐平台实测
  · 框架层（dispatch / 预读 body / 协议桥）只负责把请求送到对的网关，不判断内容
  · 同一平台可能有多条入口链路，规则必须每条链路都落实，且列出全部调用点
```

`upstream_compat.py` / `desensitize.py` 名字看着通用，实际是
**WorkBuddy 上游（`copilot.tencent.com`）的实测规则集**：`11128`、
`x-anthropic-billing-header`、`tool_choice` 字符串化、reasoning 档位……
这些码与词表只对那一个上游成立。把它们当「全局净化」套给别的平台，
或反过来把别家规则并进这里，都会制造静默故障。

---

## 1. 两次实测：为什么共享会出事

| | CodeArts（2026-11-04） | WorkBuddy（11-02 起） |
|---|---|---|
| 客户端字段 | `messages[0].role = "developer"`（Codex / Mirai 发系统指令） | `claude-cli` 在 `system` 首个 text block 塞 `x-anthropic-billing-header: cc_version=…; cc_entrypoint=sdk-cli;` |
| 上游反应 | **HTTP 200 的 SSE 错误帧**：`{"text":"[DONE]","error_code":"InferHub.001001044.400","error_msg":"Message role cannot empty"}` —— 没有 `choices`、没有 `finish_reason` | **HTTP 400 code=11128** `Illegal API invocation from an unapproved channel` |
| 网关表现 | 旧代码只认 `{"error":{...}}` 形式，把错误帧当正常 chunk 透传，末尾只补 `[DONE]` → 客户端报 `Stream ended without finish_reason`，按 ~937ms 退避反复重试；网关还 `note_success()` 记成绿勾、0 token、ttfb=latency | `/gw` 链路走 `prepare_upstream_body(sanitize=True)` → `sanitize_messages()` → `_HDR_RE` **整行删除** ✅；`/v1/*` 三条链路只做词表零宽脱敏，**没删这个头** |
| 数据特征 | 同一账号 632/759/870ms 三条空流，余额 4600、status=active —— 不是坏号 | AUX 带 billHdr：785 请求 / 694 个 400（**88.4%**）；不带：647 请求 / 28 个 400（4.3%）；MAIN 1.4%。同 key 同 IP 同秒的其它请求 200 |
| 根因归类 | 请求体角色不在上游白名单 + 错误帧识别不全 | 两条链路净化强度不一致；零宽字符会被上游归一化，等于没改 |

**共同点**：账号、凭证、额度全正常；失败完全由请求体决定；网关把失败
伪装成了「成功」或「上游抽风」，于是客户端一直重试、排障一直往号池查。

---

## 2. 谁维护哪条规则（归属表）

| 平台 | 规则落在哪 | 入口 | 备注 |
|---|---|---|---|
| **CodeArts** | `admin/platform/codearts/core/client.py` | `chat_completions()` / `chat_stream()` → `normalize_body()`；`_inband_error()` | 上游自称 OpenAI 同构，但 role 白名单和错误帧格式仍是私有；`gateway.py` 负责流收尾与记账 |
| **WorkBuddy**（默认平台） | `admin/routers/proxy.py` + `converter.py` | `/v1/chat/completions`、`/v1/responses`、`/v1/messages` 三条链路 + converter 的 `/gw` | **没有自己的 `gateway.py`**，上游适配长在主网关里（见 [SKILL §2.5](SKILL.md)）。这是历史结构，不是"可以通用"的许可证 |
| Trae / Qoder / Loomy / Raccoon | 各自 `admin/platform/<name>/gateway.py` | 平台自己的 dispatch | 别复用 WorkBuddy 的词表；各自按上游实测维护 |

> 判断标准沿用 [SKILL §1](SKILL.md)：改这个规则时需要改框架层吗？
> 需要 → 说明平台知识漏到了框架层，先搬回去。

---

## 3. 铁律（改请求体代码前逐条对）

1. **规则放平台模块**，框架层不允许出现平台专有名词（`test_gateway_contract.py` 会拦）。
2. **HTTP 200 ≠ 成功**。必须按「有没有 `finish_reason` / 有没有 `error_code` 信封」再判一次。
3. **错误帧要覆盖所有形式**：OpenAI 风格 `{"error":{...}}`、华为云风格
   `{"error_code":…,"error_msg":…,"text":"[DONE]"}`、以及 `event: error` 行。
4. **流内错误必须补终止帧**（`finish_reason` + `[DONE]`），并写 `error_kind`；
   不能 `note_success()`，否则坏号/坏请求永不受罚，客户端每次重试都再命中它。
5. **同一规则列出全部调用点**：`/gw` 与 `/v1/*` 是两套入口；messages 与 tools 是两块数据。
   漏一条链路的表现是「稳定 4~6% 失败」，不是全挂，最难查。
6. **净化顺序即语义**：先整行删指纹 → 再做零宽脱敏。反了会让正则再也匹配不到，
   而上游会归一化零宽字符 —— 看起来改过了，字面仍然成立。
7. **零宽字符不是删除**。它只对「词表误判」有效，对渠道指纹、role 白名单、
   错误帧一律无效。
8. **`developer → system` 不是万能钥匙**。它只是 WorkBuddy/CodeArts 两条白名单的
   交集；别家可能还限制 `tools` 结构、`tool_choice` 类型、`reasoning_effort` 枚举。
9. **规则基于实测，上游是移动靶**。每次上游发版，重测 §6 三项；改完跑回归，
   不要只跑契约测试（契约全绿但路由/净化错是常见形态）。

---

## 4. 排障清单（按现象反查）

| 现象 | 先看哪里 | 动作 |
|---|---|---|
| 稳定 400 / `11128` | 按「system 里是否带 `x-anthropic-billing-header`」分组统计 400 率 | 带头的组 88% vs 不带 4% → 指纹没删干净，查净化调用点，别查账号 |
| `Stream ended without finish_reason` + 反复重试 | 抓原始 SSE 第一帧 | 若是 `error_code/error_msg` 信封 → 补错误帧识别 + 终止帧 |
| 日志绿勾但 TOKEN 0、`ttfb == latency`、耗时只有几百 ms | `usage_logs.error_kind` 是否为空 | 网关把错误帧记成了成功，补 `error_kind` / 去掉 `note_success()` |
| 简单 `hi` 能过、带 tools / 开思考就 400 | 请求体差异（role / tools / max_tokens） | 用同一账号做 A/B 对照，只改一个字段 |
| 账号 active、余额充足却一直失败 | 不是号池问题 | 停止换号，回到请求体与链路一致性 |

---

## 5. 验证（改完必跑）

```bash
# 语法
py -3 -m py_compile admin/platform/codearts/core/client.py \
  admin/platform/codearts/gateway.py admin/routers/proxy.py

# 纯函数断言（不需要服务）
py -3 - <<'PY'
from admin.platform.codearts.core import client as ca
from admin.platform.codearts import gateway as gw
from admin.routers import proxy
from upstream_compat import has_fingerprint

# CodeArts：角色归一 + 错误帧识别
b = {"messages": [{"role": "developer", "content": "sys"}, {"role": "user", "content": "hi"}]}
assert [m["role"] for m in ca.normalize_body(b)["messages"]] == ["system", "user"]
e = ca._inband_error({"text": "[DONE]", "error_code": "InferHub.001001044.400",
                      "error_msg": "Message role cannot empty"})
assert e is not None and e.kind == "upstream_business"
assert ca._inband_error({"choices": [], "usage": {"total_tokens": 5}}) is None
assert b"finish_reason" in gw._terminal_frame("m")

# WorkBuddy：两条链路的净化强度一致
fp = {"messages": [{"role": "system", "content":
        "x-anthropic-billing-header: cc_version=2.1.310.8ea; cc_entrypoint=sdk-cli;"}],
      "tools": [{"type": "function", "function": {
        "name": "shell", "description": "x-anthropic-billing-header: cc_version=1;",
        "parameters": {"type": "object"}}}]}
out = proxy._desensitize_chat_body(fp)
assert not has_fingerprint(out["messages"][0]["content"])
assert not has_fingerprint(out["tools"][0]["function"]["description"])
clean = {"messages": [{"role": "user", "content": "hello"}]}
assert proxy._desensitize_chat_body(clean)["messages"][0]["content"] == "hello"
print("request-body contract: OK")
PY

# 端到端（服务在 8790）：带 developer 的流式请求必须有终止帧与 usage
curl -sN -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4.1-flash-codearts","stream":true,
       "messages":[{"role":"developer","content":"You are a coding agent."},
                   {"role":"user","content":"hi"}]}' \
  http://127.0.0.1:8790/v1/chat/completions | tail -c 500
# 期望：finish_reason=stop + usage + [DONE]，没有 error_code

python admin/platform/skill/scripts/check_hotpath.py   # 热路径不变量
python tests/test_gateway_contract.py                 # 框架层无平台名词
```

> 改的是热路径时记得重启：杀掉 uvicorn 子进程即可，`main.py` 会自动重生
> （`python -m uvicorn admin.server:app --host 0.0.0.0 --port 8790`）。

---

## 6. 上游每次升级后重测三件事

1. **角色白名单**：`developer` / `system` / `tool` 各发一次，看是 400 还是 200 错误信封。
2. **tools 契约**：`tool_choice` 是字符串还是对象、并行工具调用、参数 schema 是否被拒。
3. **错误与收尾**：错误是 HTTP 4xx 还是 200 SSE 信封？正常流是否给 `finish_reason`
   与 `[DONE]`？——这两项决定了客户端会不会无限重试、日志会不会误记成功。

三项都用**同一账号、只改一个字段**的 A/B 请求做，别拿压测数据猜。
