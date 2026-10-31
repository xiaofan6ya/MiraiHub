# 已修 bug 档案（避免重犯）

> 这里记录本项目**真实出现过并已修复**的 bug。
> 每条都写清「现象 / 根因 / 修法 / 怎么验证」——
> 下次改动相关代码前先扫一遍，能省掉大量重复排查。

---

## 一、调度与禁用

### 1.1 平台禁用了还在用 ⚠️ 最严重

**现象**：禁用 WorkBuddy 后，`auto` 路由 6/6 请求照样打到它。

**三个独立根因**（都是坑，要一起修）：

| 根因 | 修法 |
|---|---|
| `PUT /api/gateway/config` 的 `RoutingIn` **没有 `disabled` 字段**，pydantic 静默丢弃 | 补字段 + `save_routing(disabled=...)` 写入 `DISABLED_KEY` |
| 调度缓存签名**只含 strategy**，禁用变更不失效 | 签名加入 `disabled` + `priority` + `spread_unbound` |
| `resolve()` 对显式头 / Key 绑定**不检查禁用** | 统一走 `_disabled_platforms()`，命中抛 `PlatformDisabled` → 403 |

**额外兜底**：
- `_candidates_for_model` 过滤禁用平台
- 默认平台被禁用时退到「第一个未禁用平台」，而不是硬塞回去
- `_is_disabled` 同时认 id 和 storage（禁用 `trae` 覆盖 `trae-cn`/`trae-intl`）

**验证**：禁用后 6/6 全部落到 loomy；显式请求返回 403；恢复后立即可用。

### 1.2 模型禁用可被绕过

**现象**：`X-Platform: loomy` + 已禁用的 `spark-x2.6` 仍返回 200。

**根因**：`_catalog_maps` 只含 enabled 模型，禁用后查不到 → 落入
「谁都查不到 → 原样放行」的兜底分支，直接转发出去。

**修法**：`_disabled_model_id()` 按归一 key + 原生 id 双重匹配，命中返回 403。

### 1.3 缓存不同步导致「改了半天没反应」

**现象**：改配置后要等十几秒甚至重启才生效。

**修法**：`invalidate()` 要同时清三份缓存：
```python
_model_cache        # 模型目录
_disabled_cache     # 禁用集合
_order_cache        # 平台顺序
models_router._cache # /v1/models 聚合结果（两种视图分开存）
```

**教训**：加缓存时**必须同时想清楚「谁负责失效它」**。

---

## 二、模型与路由

### 2.1 同名模型跨平台串号

**现象**：Trae 国内版倍率 `0.12` 变成了 `0.11`（WorkBuddy 的值）。

**根因**：同步模型描述时只按 `model_id` 匹配，13 个同名 ID 会互相覆盖。

**修法**：
```python
# 必须 platform + model_id 双重匹配
owner = str(model.get("platform") or "").lower()
if owner and owner != str(row.platform or "").lower():
    continue
# 倍率只在库里没设过时才写，不覆盖用户手填
if price is not None and row.credit_multiplier in (None, 0):
    row.credit_multiplier = price
```

### 2.2 默认平台不改写模型名 → 聚合名 400

**现象**：把调度策略切到「优先级」（WorkBuddy 第一）后，所有聚合名请求
都报 `400 模型 'deepseek-flash' 不存在`。

**根因**：只有非默认平台做 model_id 映射，默认平台直接把聚合名转给主应用，
而主应用只认白名单里的 id。

**修法**：默认平台分支也要 `_model_id_for()` 改写后再转发。

### 2.3 模型 ID 后缀路由（主动设计，不是 bug）

同 ID 多平台时，`model` 字段可带 `-平台名` 后缀：

```
deepseek-v4.1-flash-workbuddy   → 只发 WorkBuddy
DeepSeek-V4.1-Flash-loomy       → 只发 Loomy
dfmodel-qoder                   → 只发 Qoder 中国版
```

实现 `admin/platform/model_suffix.py`：
- 平台 id **按长度降序**匹配（`trae-cn` 先于 `trae`）
- 只在后缀恰好等于某个平台 id 时剥离，不误伤 `gpt-4.1-2025-04-14`
- 剥完必须还剩内容
- 命中平台同样过禁用检查
- 响应头回显 `X-Gateway-Platform` / `X-Gateway-Model`

---

## 三、协议与端点

### 3.1 4023「模型不存在」其实是**端点错了**

**现象**：`deepseek-v4.1-flash` 在 Trae 一律 4023，改模型名、换设备指纹、
清冷却全无效。

**根因**：Trae 有两套聊天端点，模型按类别分流：

| 模型类别 | 端点 |
|---|---|
| 老模型（deepseek-R1 / V3） | `/api/ide/v1/llm_raw_chat` |
| **SOLO / Agent 类**（v4.1-flash、glm-5.3、kimi-k3…） | **`/api/agent/v3/llm_utils_chat`** `function=solo_work_lite` |

**修法**：`admin/platform/trae/core/solo.py`，六字段 body + 专用头 + 事件流翻译。

**教训**：**4023 是端点级拒绝，不是模型级**。改名字试一万遍也没用。
详见 [`ENDPOINT-FIRST.md`](ENDPOINT-FIRST.md)。

### 3.2 异常类型不匹配导致二次崩溃

**现象**：`AttributeError: 'RuntimeError' object has no attribute 'kind'`，
把真正的 401 完全盖住。

**根因**：`note_failure()` 参数标注 `exc: client.UpstreamError`，
但 SOLO 通道抛的是普通 `RuntimeError`，直接读 `exc.kind` 就崩。

**修法**：放宽成 `exc`，非 UpstreamError 时从消息里还原 HTTP 语义：
```python
if not isinstance(exc, client.UpstreamError):
    status = 0
    for code in ("400","401","403","404","429","500","502","503"):
        if "HTTP %s" % code in message: status = int(code); break
    kind = "auth" if status in (401,403) else "upstream"
    exc = client.UpstreamError(kind, message[:400], status or None, kind=kind)
```

**教训**：**处理异常的函数要接受任意异常**，否则排障时看到的是处理器的错，
不是真正的错。

### 3.3 `latency_ms = ttfb_ms` —— 总耗时从没记过

**现象**：面板上「P50 总耗时 == TTFT」，TPS 永远算不出来。

**根因**：Qoder 网关把两者写成同一个值，`latency - ttfb = 0`。

**修法**：`_bill(...)` 接受 `started` 时间戳，
`latency_ms = latency_ms or _elapsed_ms(started) or ttfb_ms`。

**TPS 计算**（bench_router）：
```python
gen_ms = latency - ttfb
if gen_ms <= 50:        # 非流式上游，两者相等
    gen_ms = latency    # 退回整段
tps = completion_tokens / (gen_ms / 1000)
```

### 3.4 消息 content 必须是 parts 数组

```python
# 错 —— 直接 400
{"role": "user", "content": "hi"}
# 对
{"role": "user", "content": [{"type": "text", "text": "hi"}]}
```

---

## 四、前端与 UI

### 4.1 Tailwind 的 `md:` 前缀在本项目**没定义**

**现象**：扫描弹窗只剩标题和按钮，表格消失。

**根因**：
```html
<div class="hidden md:block overflow-x-auto">   <!-- md:block 无效 -->
```
本项目只用了 Tailwind 的部分类名，没引断点前缀。
于是 `hidden` 永久生效，数据在 DOM 里但 `display:none`。

**修法**：换自定义类 + 真实媒体查询
```css
.scan-desktop { display: none; }
@media (min-width: 768px) { .scan-desktop { display: block; } }
```

**教训**：用 Tailwind 类名前先确认**这个前缀在本项目有没有定义**。

### 4.2 `copyText(sel)` 传文本而不是选择器 → 静默失败

**现象**：点「复制」弹出「已复制」，但剪贴板是空的。

**根因**：
```javascript
async function copyText(sel){
  const source = $(sel);   // 期望 CSS 选择器
  if(!source) return;      // 传纯文本 → null → 静默 return
  ...
}
```
后面那句 `toast("已复制")` 照常弹，所以看起来成功。

**修法**：新增 `copyRawText(text)` 直接走 `navigator.clipboard.writeText`。

**教训**：**先校验再提示**，不要「先 toast 再干活」。

### 4.3 悬停浮层移进去就关闭

**现象**：模型 chip 的详情浮层，鼠标一移进去就消失，根本没法复制。

**根因**：纯 CSS `:hover` 在移出 chip 瞬间失效。

**修法**：
```css
.model-chip-wrap:hover .model-chip-pop,
.model-chip-wrap:focus-within .model-chip-pop,
.model-chip-wrap:has(.model-chip-pop:hover) .model-chip-pop { display: block; }
/* 用 ::before 补 7px 透明桥，把 chip 和浮层之间的空隙连上 */
.model-chip-pop::before { content:""; position:absolute; left:0; right:0; top:-7px; height:7px; }
```

### 4.4 硬编码浅色在深色主题下变成白块

**现象**：深色下弹窗出现白色描边、文字看不见。

**根因**：代码里写了 `bg-white` / `text-slate-*` / `border-slate-200`，
但项目没有完整的 Tailwind 主题映射。

**修法**：按语义覆盖回主题变量
```css
:root[data-theme="dark"] #modalBox .bg-white { background: var(--surface) !important; }
:root[data-theme="dark"] #modalBox .border,
:root[data-theme="dark"] #modalBox [class*="border-"] { border-color: var(--line) !important; }
```
注意 **`border` 工具类会把 border-color 设成 currentColor**，
必须显式覆盖，只改 `border-slate-200` 不够。

### 4.5 两套扫描实现并存

**现象**：平台管理扫描出「不可导入」空表格，账号池扫描正常。

**根因**：WorkBuddy 有专用弹窗（按 UID 去重、标「当前/号池已有」、逐条导入），
通用渲染拿不到这些字段。

**修法**：平台管理的 WorkBuddy 直接委托 `scanLocal()`：
```javascript
if (id === "workbuddy" || id === "codebuddy") {
  // Android 构建会删掉 scanLocal 实现，必须做存在性判断
  if (typeof scanLocal === "function") return scanLocal();
  toast("移动端不支持扫描本机", "err"); return;
}
```

**教训**：**`typeof` 保护不是可选的** —— Android 构建脚本会删掉整个函数体。

### 4.6 平台切换后数据不刷新（缓存卡闪）

**现象**：切平台后积分页还是旧数据，要手动刷新。

**修法**：前端所有列表请求都带 `platformScopeQuery()`，
并监听统一事件重载：
```javascript
document.addEventListener("wb-platform-change", function () {
  const panel = document.getElementById("panel-credits");
  if (panel && !panel.classList.contains("hidden")) loadCreditStats();
});
```

---

## 五、性能

### 5.1 接口在 GET 里同步拉上游

| 接口 | 原来 | 修法 | 现在 |
|---|---|---|---|
| `/api/models/descriptions` | 1434ms（每次 `_fetch_catalog`） | 扔后台线程刷新，本次返回库里数据 | 50~100ms |
| `/api/models/busy` | 1189ms（每次探上游容量） | 30 秒缓存 | 11ms |

### 5.2 `/v1/models` 只返回默认平台

**现象**：客户端只看到 17 个模型，其它平台的看不到。

**修法**：`admin/platform/models_router.py` 聚合全库，**两种视图**：

| Key 的 aggregate | 返回 |
|---|---|
| `true`（聚合） | 同模型一条，`owned_by=gateway`，附 `platforms` 列表，落点交给调度 |
| `false` | 全量，多平台同名带 `-平台名` 后缀 |

**必须鉴权**：聚合清单等于整张模型目录，`_key_of(request)` 为 None 时回退原路径
（让主应用给 401），不能直接返回。

---

## 五·补、模型 id 必须是规范 slug（不是展示名）

**现象**：客户端报 `模型 'DeepSeek-Flash · Qoder 中国版' 不存在` /
`模型 'DeepSeek V4.1 Flash(x2.0)' 不存在`。

**根因**：`/v1/models` 的 `id` 用了 `display_name`（给人看的名字，带空格、
括号、中文、倍率标记），而客户端会**原样拿它当调用名**回传。

**规范**：`id` 是**调用标识**，必须满足

```
^[a-z0-9.:\-]+$        全小写、只留 [a-z0-9.-]
```

转换规则（`models_router.slugify_model_id`）：

```
DeepSeek V4.1 Flash(x2.0)   -> deepseek-v4.1-flash      去掉 (x2.0) 倍率
Kimi k2.6 （x6.5）          -> kimi-k2.6                 全角括号 + 倍率
Doubao_1_5_thinking_pro     -> doubao-1-5-thinking-pro   下划线转连字符
Seed-2.1-Turbo (Doubao-…)   -> seed-2.1-turbo            去掉括号别名
deepseek-v4.1-flash:intl    -> deepseek-v4.1-flash:intl  保留 :intl 区后缀
```

**关键配套**：改名后必须能**还原**，否则上游 400。

```python
# 转发前反查真实 id
real = models_router.upstream_of("deepseek-flash", db)   # -> "dfmodel"
```

`upstream_of()` 遍历已启用配置，对每条算 slug 命中即返回。三条路径都要覆盖：
1. 原始 id 直接相等
2. slug 相等
3. **缩写映射后的可读名** slug 相等（`deepseek-flash` -> `dfmodel`）

**两个接口必须用同一套命名**：

| 接口 | 用途 |
|---|---|
| `/v1/models` | 客户端拉清单、决定调用名 |
| `/api/models/configs` | 控制台模型表格、分组编辑器 |

只改一个 → 控制台显示 `dfmodel-qoder`、客户端显示 `deepseek-flash-qoder`，
用户不知道该复制哪个。改完用集合比对验证：

```python
assert {m["id"] for m in api_items} == {m["model_id"] for m in cfg_items}
```

---

## 五·补2、能力字段必须用 DSH 认的 camelCase 顶层键

**现象**：客户端模型行显示「256K / 32K / 纯文本 / 推理开关是灰的」，
即使上游 metadata 里 `maxInputTokens=1000000`、`supportsImages=true`。

**根因**：能力信息只放在自定义的 `caps` 对象里，而 **DSH 读的是顶层
camelCase 键**。

从 `app.asar` 反编译到的 `discoverModels` zod schema：

```typescript
z.array(z.object({
  id: z.string(),
  name: z.string().optional(),
  contextWindow: z.number().optional(),          // 正整数
  maxTokens: z.number().optional(),              // 正整数
  inputModalities: z.array(z.enum(["text","image"])).optional(),
}))
```

编辑器校验代码（`const contextWindow = model["contextWindow"]`）也确认了
同名字段，所以**必须同时**输出：

```json
{
  "id": "deepseek-flash-qoder",
  "object": "model",
  "created": 0,
  "name": "DeepSeek-Flash · Qoder 中国版",
  "contextWindow": 1000000,
  "maxTokens": 128000,
  "inputModalities": ["text", "image"]
}
```

**三个易错点**：

1. `inputModalities` 是**字符串数组**，不是布尔（`supportsImages: true` 没用）
2. `contextWindow` / `maxTokens` 必须是**正整数**，`<= 0` 会被判非法丢弃
3. `name` 只在有值时给，空串会让校验器报 `modelNameInvalid`

**为什么要保留 `object` / `created`**：OpenAI 兼容客户端（含严格解析的）
要求这两个字段；DSH 的 schema 是 `additionalProperties: true`，
多余键被忽略，两者不冲突。

---

## 五·补3、DSH 的「已自定义模型目录」会覆盖手改的 yaml

**现象**：手改 `~/.dsh/profiles/desktop/cordis.patch.yml` 补了
`contextWindow` / `input`，DSH 重启后又被改回空值。

**根因**：DSH 在 UI 里点过「获取可用模型」后，用户层会**自己存一份**
模型行；之后以用户层为准，yaml 里的默认值被覆盖。

**正解**：**先修网关的 `/v1/models` 输出**（让它带 camelCase 能力字段），
再在 DSH 里点一次「获取可用模型」让用户层重新拉取。只改 yaml 是白费。

**判据**：看 yaml 里 `id` 与你写的是否一致 —— 如果 `id` 都被 DSH 改写
（`deepseek-v4.1-flash:intl-trae-intl` -> `deepseek-v4.1-flash-intl-trae-intl`），
说明它用的是自己那份，yaml 只是初值。

---

## 五·补4、非聚合 Key 不预读 body → 后缀模型 400

**现象**：用 `aggregate=False` 的 Key 调 `deepseek-v4.1-flash-loomy`，
稳定返回 400「模型不存在」；用 `aggregate=True` 的 Key 同一个模型 200。

**根因**（`dispatch._should_peek`）：

```python
elif not _aggregate_enabled(key):
    return False        # 非聚合 Key 直接不预读请求体
```

预读**不只**用于「跨平台自动选路」，它还承担**读模型名做后缀路由**。
不预读 → `raw_model` 为空 → `-loomy` 后缀解析不出来 → 落到默认平台
（workbuddy）→ 主应用白名单里没有 → 400。

**日志判据**：

```
dispatch 分叉 | raw_model='' -> platform='workbuddy' explicit=False
                    ^^^^^^^^ 空的就是没预读到 body
```

**修法**：任何 Key 都预读，不做聚合与否的分支。

**教训**：一个「顺带做两件事」的分支，条件收紧时会漏掉其中一件。

---

## 五·补5、DSH 模型目录会在 UI 操作时重写整个 models 数组

**现象**：手改 `cordis.patch.yml` 补了 7 个模型（带 `input: [text, image]`），
在 DSH 界面删过/加过一行之后，文件里只剩 1 个模型、`input` 也丢了。

**根因**：DSH 的「模型目录」编辑器**以界面状态为准**整体重写
`llm-pi-ai.config.providers.<name>.models`。手改的默认值只能撑到
下一次 UI 编辑。

**正确做法**：

1. **网关的 `/v1/models` 必须带全能力**（`contextWindow` / `maxTokens` /
   `inputModalities` / `reasoningEfforts`，见「五·补2」）
2. 在 DSH 里点 **「获取可用模型」** → 勾选 → 「添加所选」
   —— 这样灌进去的是网关的真实数据
3. 只在**不改 UI**的前提下，手改 yaml 才有意义

**判据**：改完 yaml 重启 DSH，若界面上没变化，说明它读的是更上层的用户
状态；看 `profiles/desktop/cordis.patch.yml.bak-*` 能确认被覆盖过几次。

---

## 五·补6、哨兵把 200 画成红色 / 噪音请求刷屏

### 6.1 200 显示红色

**现象**：最近请求列表里状态码 200 的行被染成红色。

**根因**：颜色只看 `outcome` 白名单：

```javascript
if (failed) ...   // failed = outcome not in ("completed", "ok")
```

而**流式请求正常结束**时 `outcome` 记的是 `interrupted`（客户端断开 /
流式收尾），不在白名单里 → 被判失败 → 红色。

**修法：状态码优先于 outcome。**

```javascript
if (status >= 500) return "danger";
if (status >= 400) return "warn";
if (status >= 200 && status < 400) return "ok";   // 2xx/3xx 就是成功
// 没有状态码（连接层失败）才轮到 outcome / error 说话
```

**判据**：`status=200 + outcome=interrupted` → `ok`。

### 6.2 `/v1/models` 刷屏

**现象**：最近请求列表被 `/v1/models`、`/v1/balance` 占满。

**根因**：`traffic_stats.record()` 对所有 `/v1` 请求都记。

**修法**：用白名单只记**对话端点**（口径定义在 `TRACKED_PATHS`）：

```python
TRACKED_PATHS = {"/v1/chat/completions", "/v1/responses",
                 "/v1/messages", "/v1/messages/count_tokens",
                 "/gw/v1/chat/completions", "/gw/v1/messages"}
SKIP_PATHS = {"/v1/models", "/v1/balance"}
```

哨兵看的是「谁在烧额度 / 谁在报错」，目录查询既不计费也不代表模型
健康度，记进去纯属噪音。

---

## 五·补7、11102「service info not found」= 模型名大小写不对

**现象**：`deepseek-v4.1-flash` 请求永久 90 秒超时/卡死，但直连上游 1.5 秒就回。
清掉负缓存后立刻恢复。

**根因链条**（三层，缺一不可）：

1. **上游对模型名大小写敏感**
   ```
   deepseek-v4.1-flash  -> 200 OK
   DeepSeek-V4.1-Flash  -> 400 {"code":11102,
                                 "msg":"model [...] service info not found"}
   ```

2. **`_model_id_for` 只做精确匹配**（`row["model_id"] == model`），
   大小写不同就匹配不上 → 原样返回客户端发来的大写形态 → 踩 11102。

3. **11102 被当成 `model_block` 负缓存 6 小时**（`MODEL_BLOCK_SECONDS`），
   于是后续所有请求都在选号阶段被挡，表现成「卡住不动」。

**修法（双层）**：

```python
# 1) 大小写不敏感兜底（_model_id_for）
for row in rows:
    if str(row["model_id"] or "").strip().lower() == lowered:
        exact = row["model_id"]
        break

# 2) 默认平台转发前先按 slug 反查上游真实 id
real0 = models_router.upstream_of(model0, db)
if real0 and real0 != model0:
    body = _replace_model(body, real0)
```

**排查手册**：遇到「某一平台发请求就卡住 / 超时」先查负缓存：

```sql
SELECT account_id, model, kind, until, reason FROM account_model_cooldowns;
```

`reason` 里出现 `11102` / `service info not found` 就是模型名对不上。
清表即可恢复：`DELETE FROM account_model_cooldowns;`

**注意区分**：
| 错误码 | 含义 | 处置 |
|---|---|---|
| **11102** | 该后端没有这个模型（常因大小写/拼写） | 负缓存 6h；纠正模型名 |
| **11128** | 渠道校验未通过（harness 提示词指纹） | 不改账号，靠 prompt 改写盾 |
| **11101** | 不支持非流式 | 上游只吃 `stream:true` |

---

## 五·补8、TTFT / TPS 口径必须对齐 DSH（否则差一个量级）

**现象**：DSH 显示「首 token 平均 15.6 秒，输出速度 214 tok/s」，
而我们的面板显示同一模型 40 多 tok/s。

**DSH 的真实算法**（`app.asar` turn-metrics，反编译确认）：

```javascript
ttftMs   = firstTokenTime - stepStartTime      // 首 token 前的时间
decodeMs = completedTime  - firstTokenTime     // **纯解码时间**
tps      = decodeTokens / (decodeMs / 1000)    // 只用 decodeMs！
```

关键：**TPS 的分母不含首包等待**。1.5 秒的首包里 1.4 秒是在排队/预填，
它不该稀释输出速度。

**我们原来的错误**（`bench_router`）：

```python
gen_ms = latency - ttfb
if gen_ms <= 50:
    gen_ms = latency      # ← 元凶：退回整段
```

非流式上游（Loomy / Trae 的 llm_raw_chat）把 ttfb 和 latency 记成同一个值，
相减为 0，于是退回整段 latency —— 首包等待被算进解码时间，TPS 被严重低估。

**修法三层**：

1. **记账时分开采集**（`loomy/core/client.py`）
   ```python
   with urlopen(req) as resp:
       first_byte_at = time.perf_counter()   # 响应头到了 = 首字节
       text = resp.read()                    # 之后才是传输/生成
   LAST_TTFB_MS  = first_byte_at - start_at
   LAST_TOTAL_MS = now - start_at
   ```

2. **Trae 流式用首个事件时间**
   ```python
   for event in iter_events(resp):
       if first_event_at is None:
           first_event_at = time.perf_counter()
   ```

3. **统计层加物理边界**（`bench_router`）
   ```python
   MIN_DECODE_MS = 100    # 低于 100ms 视为没有分段数据
   MAX_TPS = 1000.0       # 物理上不可能更快
   ```
   没有分段数据的记录**不参与 TPS 统计** —— 宁可少一个样本，
   也不要给一个错一个数量级的数字。

---

## 五·补9、Loomy 的 V4.1 只能推理，max_tokens 太小会「空回复」

**现象**：Loomy 返回 200，但 `content: ""`、`finish_reason: length`。

**根因**：Loomy 的 `DeepSeek-V4.1-Flash` **只能推理**，实测关不掉：

| 手段 | 结果 |
|---|---|
| `enable_thinking: false` | 无效 |
| `chat_template_kwargs.enable_thinking` | 无效 |
| `/no_think` 前缀 | **无效**，还会被当正文念出来 |

客户端给 `max_tokens: 32` 时，32 个 token 全被 reasoning 吃掉：
```json
{"finish_reason":"length",
 "usage":{"completion_tokens":32,
          "completion_tokens_details":{"reasoning_tokens":32}}}
```

**修法**：在 `_apply_no_think` 里给 `max_tokens` 设**思考预算下限**。
同时**删掉 `/no_think` 拼接** —— 它对 Loomy 无效还污染用户输入。

```python
# 思考预算下限 + 客户端要得更多时放宽
_floor = 16384 + min(8192, _chars * 2)
if want < _floor:  out["max_tokens"] = _floor
elif want > 32768: out["max_tokens"] = 32768
```

### ⚠️ 这个下限曾经定小了（两次）

| 版本 | 下限 | 实测结果 |
|---|---|---|
| 第一版 | 512 | 短提示能出正文，**长提示正文 0 字** |
| 第二版 | 4096 | 复杂问题仍然 `finish=length`、正文 0 字 |
| **现行** | **16384** | `finish=stop`，正文 9069 字 ✅ |

同一段提示词的实测对照：

```
max_tokens=4000   -> finish=length，正文 0 字      （全被思考吃光）
max_tokens=8000   -> finish=length，正文 6402 字   （还是被截）
max_tokens=16384  -> finish=stop，  正文 9069 字   ✅
```

**教训**：思考链长度**随提示复杂度增长**，不是固定值。用短提示测出来的
「512 够用」放到真实任务上必然翻车 —— 定这类下限要用**最复杂的真实提示词**
测，而不是 `hi`。

---

## 五·补10、多平台同步把 `platform` 丢了 → 线上号池废掉

**现象**：从本地「同步到线上」后，线上实例的账号和模型全都不知道属于哪个平台。

**根因**：`/api/sync/push` 导出账号/模型时**只带了业务字段，没带 `platform`**；
`/api/sync/receive` 接收时也没处理它，查询条件只有 `uid` / `model_id`：

```python
# push：漏了 platform
accounts.append({"uid": a.uid, "name": a.name, ...})
# receive：按 uid 单键定位
existing = db.query(Account).filter(Account.uid == uid).first()
```

单平台时代这没问题（只有一个平台）。多平台下后果是**破坏性**的：

* 所有号都落到空 `platform` → 线上选号全乱；
* `glm-5.2` 在 workbuddy 和 trae **都存在**，按 `model_id` 单键匹配会
  互相覆盖，改一个等于改两个。

**修法**：导入导出都带 `platform`，接收端改成**双键定位**：

```python
q = db.query(Account).filter(Account.uid == uid)
if plat:
    q = q.filter(Account.platform == plat)      # platform + uid
```

`ModelConfig` 同理，用 `(platform, model_id)`。

**验证**：推送后用接收端日志确认 `acc_added/acc_updated`，再抽查线上
`SELECT platform, COUNT(*) FROM accounts GROUP BY platform` 应能看到多个平台。

---

## 五·补11、Key 绑 `trae-cn` → 503「平台 trae 未装配网关实现」

**现象**：Key 绑定平台选「Trae 国内版」，不带模型后缀调用直接失败：

```json
{"error":{"message":"平台 trae 未装配网关实现","code":"platform_unavailable"}}
```
换个写法 `deepseek-v4.1-flash-trae-cn`（带后缀）却正常。

**根因**：Trae 有两个**并存的概念**，混淆了就会踩这个坑：

| 概念 | 值 | 说明 |
|---|---|---|
| **存储平台**（`accounts.platform`） | `trae` | 两区共用一套号池 |
| **对外平台**（PlatformSpec.id） | `trae-cn` / `trae-intl` | 注册表里**只有这两个** |

`registry.resolve("trae:cn")` 按契约返回 `("trae", "cn")`（这是对的，
测试也这么断言），但 `registry.get("trae")` **返回 None** —— 注册表里
根本没有叫 `trae` 的 spec。于是 dispatcher 找不到网关模块 → 503。

**修法**：**不要动 `resolve`**（它符合契约，改了会挂 6 个测试）。
在 dispatch 侧加一层兜底：

```python
def _spec_with_gateway(platform_id, realm):
    spec = registry.get(platform_id)
    if spec is not None and registry.load_gateway(spec) is not None:
        return spec
    if realm:
        alt = registry.get("%s-%s" % (platform_id, realm))   # trae + cn -> trae-cn
        if alt is not None and registry.load_gateway(alt) is not None:
            return alt
    # 再兜底：同一 storage_platform 下任意一个有网关的区域
    ...
```

**必须改两处**，少一处就还会坏：

| 位置 | 不改会怎样 |
|---|---|
| 网关模块解析（503 判定） | 报「未装配网关实现」 |
| 候选平台循环 `registry.get(target)` | 被 `continue` 跳过 → 最后报 `no_platform` |

**教训**：这类「存储 id ≠ 对外 id」的平台，**每一次 `registry.get(id)` 都
要考虑兜底**。以后新增同类平台（一个模块多个区）照此办理。

---

## 五·补12、前端 TPS 用 `prompt_tokens + 整段 latency` → 算出 2208

**现象**：日志面板 TPS 显示 2208、3854，而客户端实测只有 273。

**根因**：`logRow()` 里的算法**三处全错**：

```javascript
// ❌ 错
const totalTokens = prompt_tokens + completion_tokens;   // prompt 常上千，不是生成量
const tps = totalTokens / (latency_ms / 1000);           // 分母含首包等待
// 没有任何边界保护
```

分母含首包等待，方向还反了：等待越久 TPS 越"低"，但其实生成很快。

**修法**：与 DSH / 后端 `bench_router` **完全同口径**：

```javascript
const outTok = completion_tokens;
const decodeMs = latency_ms - ttfb_ms;        // 纯解码段
if (outTok > 0 && decodeMs >= 100) {
  const raw = outTok / (decodeMs / 1000);
  if (raw > 0 && raw <= 1000) tpsValue = raw.toFixed(1);   // 上限保护
}
```

| 参数 | 值 | 理由 |
|---|---|---|
| 分子 | `completion_tokens` | 只有它才是"生成量" |
| 分母 | `latency_ms - ttfb_ms` | 不含首包等待 |
| 有效下限 | `decodeMs >= 100ms` | 更小的差值是非流式上游的爆发，算不出速度 |
| 上限 | `1000 tok/s` | 快过这个说明采样有问题，显示「—」比假数字诚实 |

**验证**：同一请求下，客户端实测 TPS 与服务端记录应**完全相等**。

```
workbuddy  客户端 113 / 服务端 113  ✅
trae       客户端 273 / 服务端 273  ✅
```

**顺带**：Trae 的 `ttfb` 原记的是**首个上游事件**（那是思考链），
实测"首个思考 1.3s、首个正文 22s"，导致 TPS 算成 73。改成只认
`response` / `tool_calls` 后才对上。

---

## 五·补13、Loomy 是「伪流式」：读完整个响应才吐给客户端

**现象**：Loomy 的对话总是「先干等几十秒，然后一大坨文字瞬间出现」，
不像流式；`ttfb == latency`，TPS 永远算不出来。

**根因**：`chat_completions` 里写着：

```python
# 注释说「上游只有非流式」——这是错的
plain = {**body, "stream": False}
response = await _forward(plain, request)     # 等全部生成完
return StreamingResponse(_sse_from_chat(payload))   # 才切块吐出去
```

实测上游**支持 SSE**：`stream: true` 返回 `text/event-stream`，首帧 1.6 秒就到。

**代价**（不只是观感）：

* TTFT 被记成"整个生成时间"；
* `ttfb` 与 `latency` 天然相等 → `latency - ttfb = 0` → TPS 恒为空；
* 长任务会撞网关总时限。

**修法**：新增 `client.chat_stream()`（逐行读上游、逐帧 yield），
`gateway` 侧三条链路全部改真流式：

| 端点 | 改前 | 改后 |
|---|---|---|
| `/v1/chat/completions` | 伪流式 | ✅ 首帧 ~1.0s，1.7s 内 47 行 |
| `/v1/messages` | 强制 `stream=False` | ✅ 真流式 |
| `/v1/responses` | 强制 `stream=False` | ✅ 真流式 |

**踩过的坑**：分段耗时原先用**模块级全局变量**传递，`_bill` 在另一个协程里
读不到，`ttfb` 恒等于 `latency`。改成**显式传 `stats` 字典**才修好
（`contextvars` 也会因协程边界失效）。

**教训**：**不要相信"上游只支持非流式"这类注释** —— 自己去发一个
`stream:true` 看 `content-type`。伪流式是最容易自我说服的偷懒。

---

## 五·补14、实时面板「进行中」永久残留 + 旧快照

**现象**：
1. 请求早就结束了，风控哨兵/用量日志还显示「进行中」，刷新才消失；
2. 「正在处理 N 路」在请求进行中反复闪成 0；
3. 跑完任务后用量日志不更新，要大刷。

**根因（三个叠加）**：

**a) running 记录写进了有序集合，删不掉**

```python
pipe.zadd(RECENT_KEY, {json.dumps(item): now})   # 最近请求 zset
...
client.hdel(PREFIX + "running", rid)             # 结尾只删了 hash
```

zset 的成员是 JSON 串，**没法按 rid 精确删除**，那条 running 永久残留。
实测堆了 **60 条**（清理时 zset 里 124 条）。

**b) 采样合并返回了旧快照**

```python
if not leader:
    entry['event'].wait(...)
    return copy.deepcopy(shared)     # shared 是上一次采样的结果
```

SSE 每 0.5 秒踢一次、前端同时在轮询，非 leader 每次拿到几秒前的状态 →
「N 路」闪成 0。

**c) 前端「动态请求」没有定时器**

`loadRequestActivity()` 只在切到日志页时被调用一次，之后再无下文。

**修法**：

| 问题 | 修法 |
|---|---|
| a | running 改存**独立 hash**，`hdel` 即干净；`snapshot()` 读列表时临时合并 |
| b | 非 leader 等 leader 完成后**自己再采一次**，不返回旧 payload |
| c | 「动态请求」整块**下线**（哨兵已有更完整的实时视图）；用量日志保留 5 秒一次的**轻量探针** `?include_history=false`（只查一个主键） |

**验证**：

```
32.5 秒的长请求：+2s/+8s/+18s/+20s 全部 running=1，结束后立刻 0  ✅
日志表：发请求 → 12 秒后自动跳到新记录  ✅
```

**教训**：往有序集合里塞「需要单独撤回」的状态，是设计错误。
**要么存 hash（可按 key 删），要么存带索引的列表** —— 别指望事后能捞出那一条。

---


## 五·补15 多平台网关"只剩一个平台"（展示层过滤过头）

**现象**

打包出来的桌面版里，「平台网关」的调度可视化只画一个平台，
「当前调度顺序」只有一条；「风控哨兵」的拓扑也只有一个平台节点。
但「平台管理」列表是完整的 6 个。

**根因**

`gateway_router.order()` 原本把「没有可用账号」的平台直接 `continue` 跳过：

```python
for pid in targets:
    item = health(db, pid)
    if not item.get("ok"):
        continue        # 没有可用账号的平台直接出局
    pool.append(item)
```

这个语义对**调度**是对的 —— 排上去只会白跑一轮。
但同一个函数还被三处**展示**用途复用：

| 调用点 | 用途 |
|---|---|
| `/api/gateway/order` | 前端「当前调度顺序」+ 调度可视化 |
| `/api/gateway/health` | 平台健康列表 |
| 哨兵拓扑 `gwTrafficPlatforms()` | 拓扑右侧的平台节点 |

桌面版用的是**长期存在的 SQLite 库**，里面只有 workbuddy 的账号，
于是这三处全都只剩一个平台 —— 看起来像"多平台没接进来"。

**修法**

给 `order()` 加**只影响展示**的开关，调度路径的语义不变：

```python
def order(db, platform_ids=None, *, include_unavailable: bool = False):
    ...
    item["available"] = bool(item.get("ok"))
    if not item.get("ok"):
        if include_unavailable:
            item["reason"] = "无可用账号"
            pool.append(item)
        continue
```

- 展示型接口（`/order`、`/health`）传 `include_unavailable=True`
- **调度路径（`dispatch.py`）保持默认 False**，行为一字未变

**验证**

```
GET /api/gateway/order
→ 6 项，每项带 available / reason
   workbuddy  available=true   reason=优先级 100
   trae-cn    available=false  reason=无可用账号
   ...
```

> **教训**：一个函数同时服务「决策」和「展示」时，过滤逻辑必须能分别控制。
> 决策要严格（宁可少选），展示要完整（否则用户以为功能坏了）。
> 用同一个返回值硬扛两种语义，迟早出现这种"看着像 bug、其实是设计遗漏"的问题。

## 五·补16 桌面版 EXE 启动即崩（`/gw` 下线后的遗留校验）

**现象**

双击 EXE 后进程还在，但 `8790` 端口没有任何监听；
`%LOCALAPPDATA%\<app>\logs\backend.log` 里是：

```
RuntimeError: 内嵌网关未能加载，请检查打包依赖
  File "desktop\app.py", line 116, in backend
```

**根因**

`/gw`（读桌面端登录文件的第二入口，绕过账号池与计费）下线后，
`admin/server.py` 里的 `_CONVERTER_EMBEDDED` 变成恒 `False`。
而桌面版启动时**强制校验**这个标志：

```python
from admin.server import app, _CONVERTER_EMBEDDED
if not _CONVERTER_EMBEDDED:
    raise RuntimeError("内嵌网关未能加载，请检查打包依赖")
```

于是桌面版**必然启动失败** —— 源码服务不受影响（它不走这个分支）。

**修法**

删掉这个校验。真正需要的只是 `admin.server.app` 本身，
上面那行 import 失败会自然抛异常，不需要额外的标志位把关。

**教训**：下线一个子系统时，要搜**它的标志位**还被谁读 ——
尤其是"启动自检"这类代码，它把一个可选依赖当成了必需项。

## 五·补17 桌面版旧库缺列导致 `/api/keys` 直接 500

**现象**

EXE 里「API Keys」页空白，`GET /api/keys` 返回 `500 Internal Server Error`，
但日志里**没有** Traceback。

**根因**

桌面版用 SQLite，库是**长期存在**的（升级不会重建）。
表里缺两列：

```
api_keys.aggregate    (BOOLEAN)
api_keys.account_ids  (TEXT)
```

SQLAlchemy 按模型 SELECT **全部**列，表里少一列就整条查询失败。
`init_db()` 里有 `_ensure_column()` 迁移机制，但清单里漏了这两列。

**修法**

```python
_ensure_column("api_keys", "aggregate", "BOOLEAN", "DEFAULT 1")
_ensure_column("api_keys", "account_ids", "TEXT", "")
```

**怎么快速定位这类问题**

拿模型定义和实际库表 diff 一遍，比猜快得多：

```python
from admin import models
from admin.db import Base
for tname, table in Base.metadata.tables.items():
    have = {r[1] for r in cur.execute(f'PRAGMA table_info("{tname}")')}
    for col in table.columns:
        if col.name not in have:
            print(tname, col.name, col.type)
```

**教训**：给模型加字段时，**同一提交里必须加迁移** ——
否则源码运行（新库）永远正常，只有老用户升级后才会炸。

## 五·补18 桌面版端口漂移到随机高位端口

**现象**

`http://127.0.0.1:8790/v1` 明明没被占用，桌面版却跑在 `54293`（或 `50069`）
这种随机端口，而且重启也一直用它。用户收藏的 API 地址、客户端里填的
base_url 全都失效。

**根因（两层）**

第一层，端口选择只会「首选或随机」：

```python
try:
    sock.bind(("0.0.0.0", preferred))
except OSError:
    sock.bind(("0.0.0.0", 0))   # 系统随便给一个
return sock.getsockname()[1]
```

`0.0.0.0:8790` 绑不上（TIME_WAIT 堆积、端口在保留段、需要特权的场景都会
`WinError 10013`）时，直接落到 `bind(0)`，拿到 49152–65535 段的随机端口。

第二层更隐蔽：那个随机端口被 `save_port()` **写回 config.json**，
下次启动 `preferred` 就读到 54293 —— 于是随机值变成了「用户指定的端口」，
永远优先。**8790 从此再也回不去。**

**修法**

1. 端口顺序改成：首选 → 固定备选段（8790, 8791, … 8080, 8000）→ 最后才随机
2. 首选值如果落在 **49152–65535 动态段**，视为历史兜底产物，**降到最低优先级**
3. 随机端口**不落盘**：`save_port()` 只接受固定段内的值

```python
if 49152 <= preferred <= 65535:      # 历史随机端口
    order = [*FALLBACK_PORTS, preferred]
else:
    order = [preferred, *FALLBACK_PORTS]
```

**验证**

```
8790 空闲，config=54293  -> 8790   （回到默认）
8790 空闲，config=8790   -> 8790
8790 被占，config=8790   -> 8791   （固定备选，不再随机）
```

**教训**：兜底策略的产物**不能反过来成为默认值**。任何「失败了就选个随机的
再存起来」的写法，本质是在把临时状态固化成配置。

## 五·补19 授权回调地址写死端口

**现象**

桌面版点「授权登录」，浏览器跳转到 `http://127.0.0.1:8790/authorize` 打不开
（页面转圈或连接被拒）。服务实际跑在别的端口。

**根因**

```python
MAIN_PORT = os.environ.get("WB_OAUTH_CALLBACK_PORT", "8790")
def callback_url():
    return "http://%s:%s%s" % (MAIN_HOST, MAIN_PORT, CALLBACK_PATH)
```

模块级常量 + 硬编码默认值。桌面版每个用户的端口都可能不同
（见五·补18），写死 8790 必然对不上。

**修法**

`callback_url()` **每次调用都重新读环境变量**，并按真实优先级取值：

```
1. ADMIN_PORT            —— 运行期真实监听端口（桌面版 --backend <port> 注入）
2. WB_OAUTH_CALLBACK_PORT —— 显式覆盖（反代/公网部署）
3. 8790                   —— 源码直接跑时的兜底
```

关键点：**不做模块级缓存**。端口是启动后才知道的，缓存会让服务换端口后
仍然返回旧值。

## 五·补20 桌面版硬杀后 C 盘残留 `_MEI` 目录

**现象**

`%TEMP%` 下堆了十几个 `_MEIxxxxxx` 目录，单份 152 MB，实测累计
**1933 MB**。用户描述「关机或重启后一直拉屎在 C 盘」。

**根因**

PyInstaller onefile 每次启动把运行时解压到 `%TEMP%/_MEIxxxxxx`，正常退出
会自删。但**硬杀**（关机、重启、任务管理器结束进程、蓝屏）没有机会跑清理
代码，目录就永久留下。

**修法**

在**下一次启动时**清理 —— 这是唯一可靠的时机：

```python
def cleanup_stale_mei_dirs() -> int:
    current = os.path.normcase(os.path.abspath(sys._MEIPASS))
    for name in os.listdir(tempfile.gettempdir()):
        if not name.startswith("_MEI"): continue
        path = ...
        if os.path.abspath(path) == current: continue   # 排除自己
        if _mei_in_use(path): continue                  # 排除活着的实例
        shutil.rmtree(path, ignore_errors=True)
```

`_mei_in_use()` 用**独占重命名探针**判断：Windows 上正在运行的 exe/dll
有文件句柄，改名会失败 —— 失败即认为活着，跳过。

只在 `sys.frozen` 为真时执行（源码运行不该动系统 TEMP）。

**验证**：实测清理 12 个目录，释放 1879.8 MB，正在用的 2 个正确保留。

## 六、运维

### 6.1 改完代码没真正重启

**现象**：改了半天没效果，以为代码写错。

**根因**：`main.py` 会拉 `uvicorn` **子进程**，只 kill 父进程时子进程还在跑旧代码。

**正确重启**：
```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep -Seconds 10
# 确认 8790 端口已释放，再启动
Start-Process -FilePath "python" -ArgumentList "main.py" -WorkingDirectory "E:\反代理\wb2api"
```

**判据**：比较进程 `CreationDate` 和文件 `LastWriteTime` —— 进程早于文件就是旧代码。

### 6.2 磁盘满导致写文件失败

**现象**：`ENOSPC: no space left on device`。

**清理**：`build/` `dist/` `.desktop-build/` `tmp/` 都是可重建产物。

---

## 七、验证清单（每次改这些地方必跑）

```
[ ] 平台禁用 → auto 6/6 不落到它；显式请求 403；恢复后可用
[ ] 账号禁用 → 不再被选中
[ ] 模型禁用 → 请求被拒；恢复后 200
[ ] 带后缀调用 → 200 且 x-gateway-platform 正确
[ ] 聚合 Key /v1/models → 同模型一条；非聚合 → 全量带后缀；无效 Key 401
[ ] 浅色 + 深色两个主题下 UI 无异常
[ ] console 零报错
[ ] 重启后确认进程时间 > 文件时间
```

### 7.1 改了 Key / 分组 / 平台绑定（配置对 ≠ 生效）

配置能存进去不代表生效，**每一条都要真调一次验证**：

```
[ ] 绑分组：组内模型 200；组外模型 4xx 且 code=model_not_in_group
[ ] 绑平台：不带后缀调用 200，x-gateway-platform 落在该平台（不外溢）
[ ] 绑多账号：连调 3 次，usage_logs.account_id ⊆ 绑定的 ids
[ ] aggregate=True：能自动选平台；aggregate=False：只落默认平台
[ ] 绑了 trae-cn 的 Key：不带后缀也要 200（见五·补11，曾经 503）
```

### 7.2 改了流式 / TPS / TTFT（最容易"看起来对"）

```
[ ] 首帧 ≤ 3s（Loomy/Trae 除外，它们有长思考）—— 拿不到首帧就是伪流式
[ ] 同一请求：客户端实测 TPS == 服务端记录 TPS（差 >5% 就是口径不一致）
[ ] 非流式上游：TPS 应显示「—」，绝不能出现 2000+ 这种值
[ ] Trae 的 TTFT 记的是**首个正文**，不是首个思考事件
[ ] 三协议（chat / responses / messages）都要各测一次首帧
```

### 7.3 改了实时面板 / 用量日志

```
[ ] 发一个慢请求（>20s），全程「进行中」=1
[ ] 请求结束后 3 秒内「进行中」归 0（不归 0 = running 残留，见五·补14）
[ ] 请求结束后 15 秒内用量日志表格自动更新（不用手刷）
[ ] 长时间挂着不操作，页面不出现成百上千条 running
```

### 7.4 改了同步 / 备份 / 导出（多平台必查）

```
[ ] /api/sync/push 的 payload 里 accounts/models 都带 platform
[ ] 接收端按 (platform, uid) / (platform, model_id) 双键定位
[ ] 线上 SELECT platform,COUNT(*) FROM accounts GROUP BY platform 有多个平台
[ ] 备份是整库复制 → 天然全平台，无需按平台过滤
[ ] 账号导出默认按平台收窄（安全）；全量迁移要显式传 all_platforms=true
```
