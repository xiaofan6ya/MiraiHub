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

> **后半段在五·补35**：字段形状对了，但**上游的值没被带出来**（平台只给
> `tags`、或 `fetch_models` 把 `context_window` 过滤掉），表现仍然是空。
> 形状 + 数据源，两件事都要过。

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

> **同源第二次**：`Content-Length` 那个条件也是同一类问题（chunked /
> 大 body 不预读），见**五·补25**。
> 两次都是「预读被某个条件挡住 → 后缀静默失效」。
> 现在的实现已经没有任何条件分支（写请求一律预读），别再往里加。

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

## 五·补21 实时区用轮询会空转，应走 SSE

**现象**

用量日志页的「请求动态」每 2 秒打一次 `/api/sentinel/traffic`。空闲时也在
持续请求，多开标签页更明显；有请求时又要等到下一拍才显示。

**根因**

选了轮询。轮询的两块固有代价：

- **空转**：无事发生也在打接口，一个打开的页面就是一条持续 QPS
- **延迟**：事件落在两次轮询之间时要等到下一拍，最坏 2N

**修法：SSE + diff 推送**

```
后端 GET /api/sentinel/traffic/stream
  · 每 500ms 采样一次 traffic_stats.snapshot()
  · 只推「变了的行」+ 平台计数，空闲时不发数据
  · 每 15 秒一个注释心跳，防中间层掐闲置连接
  · request.is_disconnected() → 生成器立即退出，不留僵尸任务

前端 new EventSource(...)
  · onmessage 按 rid 合并，按 ts 倒序保留最近 12 条
  · EventSource 不可用时退回 3 秒轮询（不是直接空着）
```

关键响应头：`X-Accel-Buffering: no` —— nginx 默认会缓冲 SSE，
不加这个会把整段攒着一次发，实时性完全丧失。

**实测**

```
0.0s  snapshot  recent=3 running=0
2.2s  delta     1 条变 running      ← 请求开始
4.3s  delta     1 条变 completed    ← 请求结束
```

空闲 8 秒只 1 帧（首帧快照），有请求时事件驱动。

**教训**：实时数据用 SSE，不要轮询。轮询只适合「低频、可容忍延迟」的场景。

## 五·补22 备份面板看不到路径、也改不了

**现象**

维护页「备份数据」只有一个下载按钮：用户不知道数据库在哪、不知道备份存哪、
也没法换目录。

**根因（三层）**

1. `/api/maintenance/update` 压根**不返回**备份位置字段
2. 前端 `backupPathBlock(data)` 判断 `!data.ok` 直接返回空串 ——
   而 `/update` 的返回里连 `ok` 都没有
3. 静态资源 `<script src="/admin/assets/review-improvements.js">` **没有版本参数**，
   浏览器一直用缓存里的旧 JS，代码改了也不生效

**修法**

- 抽出 `_backup_location(db)`，`/update` 与 `/backup/info` 共用一份
  （两处各写一份，字段一漂移就会出现「探测说能写、面板说不存在」）
- 新增 `PUT /api/maintenance/backup/dir`，落库到 `system_settings`
- 面板显示两行：数据库文件（MySQL 补成 `host:port/db`，不含账号密码）、
  备份存放位置（可改、可恢复默认、不可写时标红）
- **所有 `/admin/assets/*` 静态资源统一加 `?v=`**，根治缓存问题

**教训**：静态资源的版本参数不是可选项。没有它，前端改动在用户侧
等于没改 —— 而开发者本地因为 cache-busting 不生效往往看不出来。

## 五·补23 Trae 授权只能在桌面版用，服务器部署登录不了

**现象**

部署到服务器后点「授权登录」，浏览器跳 `http://127.0.0.1:8790/authorize`
打不开 —— 这个地址指的是**用户自己电脑**，不是服务器。

**根因**

`callback_url()` 默认拼 `http://127.0.0.1:<port>/authorize`。桌面版浏览器和
网关同机，没问题；服务器部署时浏览器在用户那边，回环地址必然断开。

**关键认知：Trae 不能像 Qoder 那样纯轮询**

翻遍官方客户端 `out/main.js`，Trae 的全部 oauth 端点只有：

```
/trae/api/v3/oauth/ExchangeToken        AuthCode 换 token
/trae/api/v3/oauth/ClearRefreshToken
/api/v3/trae/{CheckLogin,GetUserInfo,LoginLocal,GenerateTempToken,...}
```

**没有** `deviceToken/poll` 这类「服务端主动查授权结果」的接口。
客户端自己也是 `http.createServer()` 起本地服务接 `/authorize` 回调。

对比 Qoder：它有 `deviceToken/poll`，服务端拿 nonce+verifier 直接问，
**不需要任何回调地址**，所以 Qoder 天生支持服务器部署。

**修法：让回调地址可以是公网地址**

`auth_callback_url` 只是拼进授权页的普通 query 参数，上游不校验它指向哪 ——
填公网域名，授权完成后浏览器就跳到公网域名。

```python
def callback_url():
    base = public_base()          # WB_OAUTH_PUBLIC_BASE
    if base:
        return base + CALLBACK_PATH          # https://域名/authorize
    return "http://%s:%s%s" % (main_host(), main_port(), CALLBACK_PATH)
```

为什么要一个**完整基址**而不是 host+port：公网需要 HTTPS，反代可能带路径
前缀（`https://a.com/gw/authorize`），两个零件拼不出来。

**前端配合**

`/device/start` 返回 `callback_reachable` + `callback_hint`：

- 判断依据是**请求来源**，不是「有没有配 WORKBUDDY_DESKTOP」。
  管理员用本机浏览器打开 localhost 管理台时，回环完全可用 —— 之前按
  桌面版标志判断，源码部署被误报成不可达。
- 不可达时弹窗换文案（「用任意设备的浏览器打开」）、显示黄色警告、
  多一个「复制授权链接」按钮（发到手机上打开）。项目里没有 QR 依赖，
  为一个场景引库不划算。

**实测**

```
桌面版            http://127.0.0.1:8790/authorize   reachable=True
ADMIN_PORT=8795   http://127.0.0.1:8795/authorize   reachable=True
公网基址          https://2api.example.com/authorize  reachable=True
带斜杠            https://a.com/authorize            （自动去尾斜杠）
反代路径前缀      https://a.com/gw/authorize
```

**两个平台的分工**

| | 授权方式 | 服务器部署 |
|---|---|---|
| Qoder | 设备码轮询（服务端主动查） | 原生支持，无需配置 |
| Trae | 浏览器回调带 AuthCode | 需要 `WB_OAUTH_PUBLIC_BASE` |

桌面版不需要动任何配置 —— 本地回调就是它的默认路径，也是它的最优路径。

## 五·补24 四个平台的授权形态不同，前端不该硬编码

**背景**

想让「服务器部署也能在线登录」。逐个查过上游能力后，四个平台分三类：

| 平台 | 授权形态 | 服务器部署 |
|---|---|---|
| WorkBuddy | 官方页面接力 + `state` 轮询 | 本来就行 |
| Qoder | 设备码轮询（`deviceToken`） | 本来就行 |
| Trae | PKCE + 浏览器回调带 AuthCode | 需要公网回调基址 |
| Loomy | **上游没有登录接口** | 做不到 |

**WorkBuddy 的实现**（`admin/routers/login.py`）

```python
# 官方登录页没有 X-Frame-Options，可以内嵌 iframe
# /v2/plugin/auth/token?state= 轮询不需要任何 cookie（实测）
# 所以服务端能独立拿到凭证，不必碰用户会话
POST /api/join/official/start   -> {sid, auth_url}
POST /api/join/official/poll    -> {done, ...}  完成后自动换凭证入库
```

**Qoder 的实现**（`admin/platform/qoder/core/oauth.py`）

```python
def poll_once(self):
    # HTTP 202/404 -> None（还没授权）；200 -> 凭证
```

设备码流程：`start` 拿 nonce+verifier 拼授权 URL，服务端带这两个值去
`deviceToken` 轮询。**浏览器在哪台机器上都无所谓。**

**Loomy 为什么不行**

实测它的 API 只有 5 条业务接口，登录相关的全是 404：

```
/models  /chat/completions  /points/balance  /points/records  /points/first-login
/auth/login 404   /session 404   /session/refresh 404   /oauth/token 404
```

凭证只有一个明文 `session`，来自本机
`C:/Users/Public/Loomy/<sha256(用户名)[:12]>/userData/auth-session.json`，
而且**没有 refresh**。所以它只能「先在官方 App 登录 → 扫描本机导入」。

**修法：把授权形态交给平台自己声明**

三个平台的 `/meta` 增加两个字段：

```python
"auth_mode": "callback" | "device-poll" | "local-session-only",
"auth_explain": "给用户看的一句话说明（含需要配什么）"
```

前端不再硬编码 `id === "qoder"`：

```js
const authMeta = await platformAuthMeta(storage);
const supportsAuth = authMeta.auth_mode !== "local-session-only";
const needAuth = !canImport && supportsAuth;
```

- 有授权能力的平台 → 显示「立即授权登录」
- 没有的（Loomy）→ 隐藏按钮，改为展示 `auth_explain` 解释原因

**顺手修的一个 bug**

`showScanResult()` 原本是同步函数，加 `await` 后必须改成 `async`，
调用点（`platformScanLocal` 里）同步补 `await`。
改完跑 `node --check` 验证 —— 这类「await 出现在非 async 函数里」
只有真正解析时才报，肉眼看不出来。

**教训**：多平台架构里，**能力差异要让平台自己声明**。
前端一旦写死「qoder 有授权、其它没有」，加第五个平台就得改前端 ——
正确做法是问平台，平台说有什么就渲染什么。

## 五·补25 ★ 偶发 400「模型 'xxx-raccoon' 不存在或已被禁用」

**现象**（2026-11-04 用户原话）："用一会就出现这种问题，明明可以用没问题"。

```
本轮运行失败400: {"message":"模型 'sn-deepseek-v4-1-flash-1017-raccoon'
不存在或已被禁用；该名字看着像平台模型（raccoon）","type":"model_not_found"}
```

**根因**：`dispatch._should_peek()` 只在「带 `Content-Length` 且 ≤256KB」时
预读 body。于是两种请求**静默不预读**：

| 请求 | 为什么会这样 |
|---|---|
| chunked（无 Content-Length） | 客户端 / 代理默认分块 |
| body > 256KB | 多轮对话、长上下文、带图、工具结果 |

不预读 → `raw_model` 为空 → 后缀解析不出 → 落默认平台（WorkBuddy）→
主应用白名单里没有这个带后缀的名字 → **400**。

「用一会儿才出错」= 对话变长、body 越过 256KB。

**修法**：

```python
_BODY_METHODS  = {"POST", "PUT", "PATCH"}
_BODY_HARD_CAP = 32 * 1024 * 1024

# 写请求一律预读；无 Content-Length 也读；超上限不读但要 warning
```

同时 `_read_body()` **不再「超限返回空串」**——那会把 body 吃掉，
下游拿到空 body 直接 400 bad json（每个大请求都挂）。
改动后完整读完、完整回放，只在超限时记日志。

**验证**：300B / 600KB / chunked 三种 body 都必须能拿到带后缀的模型名。
`scripts/check_hotpath.py` 第 1~2 项已固化为回归。

**教训**：热路径上的「优化条件」如果会让一个**必需**的动作静默跳过，
那就是 bug 温床。预读不是优化，是路由的前提。

---

## 五·补26 ★ 同名模型写法不同，只剥后缀不还原上游 id

**现象**：`glm-5.3-flash-loomy` 在模型列表里能看到，一发就
`模型不存在`；同名模型在 WorkBuddy 上正常。

**根因**：`/v1/models` 对外统一发小写 slug + 平台后缀，但上游只认自己的写法：

| 对外 slug | WorkBuddy | Trae | Loomy |
|---|---|---|---|
| `glm-5.3-flash` | 同名 | 同名 | **`GLM-5.3-Flash`** |
| `deepseek-v4.1-flash` | 同名 | **`DeepSeek-V4.1-Flash`** | 同左 |
| `minimax-m3` | — | `minimax-m3` | **`MiniMax-M3`** |

dispatch 原来只**剥后缀**、不**还原 id**。而且 `upstream_of()` 是全局扫描、
不分平台，同名时可能返回别家拼法 —— "还原"成另一个平台的模型名。

**修法**：新增 `models_router.upstream_of_in_platform(slug, storage, db)`，
平台确定后按 `(platform, enabled=1)` 收窄，两级匹配：

1. 完全同名（大小写不敏感）；
2. `slugify_model_id()` 归一后相同（`GLM-5.3-Flash` ↔ `glm53flash`）。

找不到返回 `""`，调用方**保持原名**——不猜，让上游自己报错（可归因）。

**验证**：`upstream_of_in_platform("glm-5.3-flash","loomy") == "GLM-5.3-Flash"`。
实测带后缀调用 `glm-5.3-flash-loomy`，上游回显 `model=GLM-5.3-Flash`。

---

## 五·补27 ★ 倍率字段被自己的 `fetch_models()` 过滤掉了

**现象**：小浣熊 9 个模型的倍率列全是「—」（`-1.0`），而客户端里明明
显示 ×0.25 / ×0.5。

**根因**：上游 `/model_catalog` 每个模型都带
`billing_multiplier` / `billing_effective_multiplier` / `billing_status`，
但 `fetch_models()` 只挑了 `name/display/description/visible` 四个键 ——
**字段存在，被我们自己丢了**。上层自然解析出「没有倍率」。

**修法**：`flat.append({...})` 补齐计费四件套；解析时
**`billing_effective_multiplier` 必须排在 `billing_multiplier` 前面**
（实扣 vs 标价）。

**验证**：落库实测 `1.0 / 0.75 / 0.25 / 0.2 / 0`（两个 sensenova 限免为 0）。

**教训**：`fetch_models()` 返回的键 = **整个系统能看到的上限**。
少一个键不会报错，只会让下游永远拿不到。接平台时先打印上游**完整**条目，
再决定拍平哪些字段。

---

## 五·补28 ★ 积分显示 0：记账时倍率查不到

**现象**：对话成功、token 正常，但用量日志积分是 0。

**根因**（两个叠加）：

1. **不分平台查倍率**：`workbuddy` 与 `trae` 都有 `glm-5.2`，
   `.first()` 可能命中另一家的倍率；
2. **slug 与上游 id 不等**：客户端发 `deepseek-v4.1-flash-workbuddy`
   （带后缀的规范 slug），库里存 `deepseek-v4.1-flash`（上游 id）——
   精确查必然落空 → 倍率 0 → 积分 0。

**修法**：

```python
from admin.platform.shared import _billing_candidates
for _name in _billing_candidates(model or ""):        # 原名/剥后缀/归一/反查
    mc = db.query(ModelConfig).filter(
        ModelConfig.model_id == _name,
        ModelConfig.platform == resolved_platform,     # ← 必须先限定平台
        ModelConfig.enabled == 1).first()
    if mc and mc.credit_multiplier: break
```

**验证**：小浣熊 `sn-glm-5-3-flash` 206 token、倍率 0.2 → `0.0412`。

**教训**：`credit_multiplier` 既是展示倍率也是**每千 token 单价**
（`credits = tokens × mult / 1000`）。查错一次，账和展示同时错。

---

## 五·补29 ★ 积分接口返回空结构 → 真实余额被清零

**现象**：账号明明有钱，界面上可用积分变成 0，状态还是 active。

**根因**：上游 HTTP 200 + 空 body 时 `request_json` 返回 `None`；
代码写成 `data = ... or {}` 再逐字段取 0，`apply_quota()` 就把
真实余额覆盖成 0。

**修法**：**空结构一律抛错**，并且要求至少命中一个余额键：

```python
if not isinstance(data, dict) or not data:
    raise UpstreamError("积分查询返回空结构（HTTP 200）", kind="upstream")
if not any(k in data for k in ("available_points","daily_points",
                               "monthly_points","reward_points")):
    raise UpstreamError("积分查询缺余额字段", kind="upstream")
```

调用方按 `partial` 处理：保留上次快照、不写 0，账号不算失败。

**验证**：mock 空 body → 余额不变、账号仍 active；
真实零余额时上游返回完整字段（`available_points=0`），能正常走通。

---

## 五·补30 ★ 「余额刷新失败：凭证缺少 auth」

**现象**：小浣熊账号点刷新，报

```
上游 HTTP 未记录 / code 未返回
CredentialError: 账号凭证缺少 auth，请重新导入或授权登录
```

**根因**（两个）：

1. **前端写死默认平台接口**：`refreshAcc()` 用
   `/api/accounts/{id}/refresh`（WorkBuddy 号池接口），拿小浣熊的
   account_id 打过去 → 后端按 WorkBuddy 凭证结构解 → 这句报错；
2. **成败判据错**：非默认平台的刷新接口**恒返回 HTTP 200**，
   失败在 body 的 `{"ok": false, "error": {...}}` 里。只判 `r.ok` 会把
   失败提示成「已刷新」。

**修法**：

```javascript
const base = platformApiPrefix(spec, storage);   // workbuddy → /api/accounts
api(`${base}/${id}/refresh`, {method: "POST"});

const failed = !r.ok || (data && data.ok === false);   // 两套形状都认
```

顺带修：刷新成功后**必须清 `last_err_msg`**，否则健康账号行上永久挂着
旧报错。

**验证**：`{"ok":true,"quota":{...,"available":6296}}`，账号 active、
`last_err = None`。

---

## 五·补31 ★★ 扫码授权把「能用的凭证」覆盖成「不能用的凭证」

**现象**：扫码授权显示成功、账号入库，然后这个号所有请求都失败
（"凭证缺少 auth"）。原本扫本机导入的同一个号是好的。

**根因**：同一个账号两张票，能力完全不同：

| 来源 | sid | `/model_catalog` |
|---|---|---|
| 客户端 `auth.json` | `01a1a3ed` | **200** |
| 扫码授权 | `01a1a434` | **400 `200901 iam_confirm_required`** |

上游要求「IAM 会话确认」，而 `/api/iam/v1/auth/*` 下能想到的 confirm 路径
全是 404。第一版 webauth **入库前不做任何验证**，直接 `import_record`
（同 uid 即更新）→ 好票被坏票替换。

**修法**：`poll()` 拿到 token 后**先用业务接口验一次**，不通就**不落库**：

```python
try:
    raccoon_client.fetch_models(token, realm, None)     # 业务接口，不是 /me
except UpstreamError as exc:
    sess["status"] = "failed"
    sess["confirm_required"] = (exc.kind == "confirm")
    sess["error"] = "扫码会话未完成 IAM 确认，已保留原凭证"
    return                                              # ← 关键
raccoon_accounts.import_record(db, record)
```

并把 `200901` 单独归类为 `confirm`，**不能**归 `auth`——归 auth 会把账号
直接 disabled，用户明明还能用。

**验证**：扫码票入库被拒、旧凭证 `sid` 不变、账号仍能对话。

**教训**：**任何"更新凭证"的路径，写库前都要先证明新票能用。**
"授权成功"只说明拿到了 token，不说明 token 有权限。

---

## 五·补32 ★ 导出的账号文件导不回来 + GBK 备份变乱码

**现象**：WorkBuddy 导出正常，原样导入报「缺少 auth / 账号 字段」。

**根因**：导出是**信封**：

```json
{"id":188,"name":"...","format":"client","item":"{\"account\":{...},\"auth\":{...}}"}
```

凭证在 `item`（**字符串**）里。整份贴回去，校验器只看到 `id/name/item`。

**修法**（两侧都做，双保险）：

- 前端 `submitUpload()` 加 `unwrapExport()`：拆 `item` / `accounts`；
- 后端 `credential_codec.normalize_credential()` 拆同样的外壳。

⚠️ 拆壳后必须把 `changed = True`，否则函数末尾认为"没改动"又把原串吐回去。

**附带**：文件读取换 `readCredentialFile()`（严格 UTF-8 → GBK → 宽松），
否则 Windows 的 GBK 备份把中文昵称读成 `Ð¡ä½ÐÜ 1de8`。

**验证**：导出 → 回灌 = `added 0 / skipped 1`，无报错。

---

## 五·补33 ★ 扫描到了却「没有导入任何账号」

**现象**：扫描列出 1 个账号、勾选、确认 → 「本机凭证不在勾选范围内，未导入」。

**根因**：扫描结果是**脱敏 uid**（`pu-01a1a******`），前端把脱敏值塞进
勾选框 `data-uid`，后端和完整 uid 做**全等**比较 → 一条都对不上。

**修法**：

- 前端优先 `uid_full`；
- 后端 `_uid_selected()` 兼容带 `*` 的值（按前缀匹配），老页面 / 脚本直调
  也能导。

**验证**：勾选框是完整 uid；把脱敏值直接 POST 给 `import-local` 同样 200。

---

## 五·补34 ★ 同步提示「新增 N 个」但列表里一条都没有

**现象**：小浣熊同步返回 `fetched 9 / updated 9`，模型配置页空白。

**根因**：聚合视图只渲染 `platform_count > 1` 的组，而小浣熊 9 个模型都是
单平台 → 全部被过滤。同步确实成功了，只是**列表看不见**。

**修法**：聚合视图渲染**全部组**（单平台组也显示 `平台数=1`），
"跨平台调度"用徽标区分，而不是用过滤隐藏。

**顺带**（同一处，三个 bug）：

1. `loadModels()` 在**分流到聚合之后**才算平台过滤值 → 聚合视图一直沿用
   上一次平铺视图的筛选，切平台等于没切；
2. 聚合视图从不重画平台芯片 → 点了没有视觉反馈；
3. 关掉聚合后表头不还原（聚合 5 列 / 平铺 8 列）→ 状态、操作列"消失"。

修法：过滤值在分流**之前**算；聚合视图也渲染芯片；覆盖表头前先留档
`_modelHeadDefault`，平铺时 `restoreModelTableHead()`。

**验证**：聚合 5 列 62 组 ↔ 平铺 8 列 9 行，来回切 3 次正确、无 JS 报错。

---

## 五·补35 ★ 模型目录丢了"这是什么模型"

**现象**：模型配置页一列代号（`raccoon-090dd3` / `sn-glm-5-3-flash`），
用户不知道哪个该开；上下文窗口、最大输出、图片输入全是空。

**根因**（两处）：

1. `fetch_models()` 把 `display_description` 丢了（见五·补27 同源）；
2. `/v1/models` 只给 `id` / `name`。DSH 的 `discoverModels` zod schema
   （反编译 `app.asar` 确认）要的是
   `{id,name,contextWindow,maxTokens,inputModalities}` —— 缺字段就显示默认空值。

**修法**：

- 描述一路带到 `model_configs.note` / 前端 tooltip；
- `_caps_flat()` 输出 `contextWindow` / `maxTokens` / `inputModalities`，
  并附 OpenRouter 别名（`context_length` / `max_output_tokens` /
  `architecture.input_modalities`）；
- 能力从 `tags` 兜底（小浣熊只给 `vision` / `reasoning` 标签，没有布尔位）。

**验证**：`sn-deepseek-v4-1-flash-1017-raccoon` →
`contextWindow 1000000 / maxTokens 100000 / ["text","image"]`。

**已知限制**：DSH 的 schema 里**没有推理档位字段**，那一栏只能手工选。
不要为此伪造其它键；但 `reasoning_effort` 实测能透传上游（low → 200）。

---

## 五·补36 文档说"不支持扫描"，代码其实支持

**现象**：小浣熊平台看得到「扫描本机」按钮，点了没反应 / 返回
`reason: electron_leveldb`；而本机 `~/.office-raccoon/agent/config/auth.json`
明明是明文 JWT。

**根因**：第一版结论（"Electron LevelDB，扫不出来"）写进了三处：
`README.md` / `__init__.py` 的 `supports_local_scan=False` / `routers/accounts.py`
的模块注释。**三处都是同一份事实的副本，改一处没同步另外两处**，
于是功能和文档互相矛盾。

**修法**：改结论时三处同步 + 搜索仓库里所有旧文案（含用户可见的
`auth_explain`）；`scan()` 返回 `supported: true` 并实际读文件。

**验证**：`/api/raccoon/scan-local` 返回真实 uid；
`import-local` 返回 `{"supported":true,"imported":1}`。

**教训**：**结论必须是代码验证过的，不是推理出来的。**
"Electron 应用 → 用 LevelDB → 扫不出来"是很顺的推理，也是错的。
写进文档前先 `ls` 一下那个目录。

---

## 五·补37 ★★ 一条请求建了两条「进行中」记录，面板永远显示"已接收"

**现象**：请求动态面板上堆着十几条「已接收 / 选路中」，耗时为 `—`；有的挂了
一小时还在；**重启进程也不消失**（因为记录在 Redis）。用户判断是"排队卡死、
并发被打满"。

**根因**：`add_middleware` 是**前插**，实际层次是

```
TimingMiddleware(最外) → GatewayAdmission → PlatformDispatch(最内)
```

`TimingMiddleware` 与 `PlatformDispatchMiddleware` **都调了**
`traffic_stats.start()`：外层先建 `rid=B` 并写进 `scope["_wb_traffic_rid"]`，
dispatch 后建 `rid=A` **覆盖**了它。收尾时只删 scope 里那个（A），
`B` 永远没人删 —— 一条请求漏一条记录，`running` 计数只增不减。

实测一次排障里堆了 **59 条**，最早的是 1 小时前。

**修法**：
1. `traffic_stats.annotate(rid, **fields)`：往已有记录里补字段，不新建；
2. dispatch 只 `annotate`（平台猜测 / Key / IP），**不再 start**；
3. 平台确定后 `_set_state()` 再 annotate 一次真实平台，running 行不再显示"选路中"；
4. `snapshot()` 里按 `GATEWAY_REQUEST_TIMEOUT + 120s` 清僵尸记录（复用已读出的
   hvals，`HDEL` 掉），防止历史残留永远挂着。

**验证**：发一条 `/v1/chat/completions`，`HGETALL wb:traffic:running` 的条数
**从 0 → 1 → 0**；面板 `running` 归零。`tests/test_gateway_reclaim.py` 覆盖。

**教训**：统计埋点**只能有一个创建点**。多层中间件需要的是「补充」而不是
「各建一份」——否则谁来收尾那一条永远说不清。

---

## 五·补38 ★★ 402 被当成不可重试 + 零余额号永远排第一 → "一直 402"

**现象**：Loomy 平台所有对话返回
`402 {"type":"http_error","code":"402","message":"请求失败（HTTP 402）"}`，
换模型、重启都没用。

**根因**（两段叠加）：
1. `client._request()` 把 402 归到 `kind="http_error"`，而 `retryable()` 只认
   408/429/5xx → 网关**不换号直接返回**，号也不进冷却；
2. 选号按 `last_used_at` 升序取"最闲"，而**从没成功过的空号 `last_used_at`
   恒为 NULL**，永远排最前 → 每次请求都选中同一个 0 积分号。

上游原文其实是 `{"error":{"type":"insufficient_quota",
"message":"积分余额不足，请获取积分后再试"}}`，被自己的分类吞掉了。

**修法**：
1. 402 → `kind="quota_exceeded"` + `retryable()=True` + 透出上游原文
   （`_upstream_message()`）；网关已有的 `cool_seconds=600` 随即生效；
2. `shared.select_account(..., min_balance=0)`：余额 ≤0 的号**不进候选**
   （NULL 视为未知仍放行）；
3. `sync_quota()` 探到 0 余额主动冷却 600s，不等撞 402。

**验证**：`chat_stream()` 打一个 0 积分号 →
`kind=quota_exceeded status=402 retryable=True message=积分余额不足，请获取积分后再试`；
过滤前后 `select_account` 首选 id 从 `199(balance 0)` 变成 `204(20000)`。

---

## 五·补39 ★★ 列表接口在请求线程里同步探活 → 2.8 秒，几千个号几十秒

**现象**：`/api/loomy/accounts?page=1&page_size=50` 稳定 2.4~3.0 秒；
Qoder 同病。用户："这几个接口太慢了"。

**根因**：`account_ops().list()` 对每个「快照过期」的号同步调一次上游
（Loomy `sync_quota` / Qoder `fetch_usage`），**串行**。
把同步调用临时置零后同一接口 **0.05 秒** —— 2.77 秒全在这里。

**修法**：`shared.kick_background_refresh(name, ids, worker, max_items=8, min_gap=10)`，
单飞 + 限频 + 限量 + 不冒泡；列表只读 `credits_snapshot` 立刻返回。
Loomy/Qoder 都已改，`scaffold.py` 的模板也改成这个写法（新平台不再复制这个坑）。

**验证**：`list()` 实测 **0.057s**（原 2.82s）；等 3 秒后余额确实被后台写回。

> 详见 [INTEGRATION-CHECKLIST.md](INTEGRATION-CHECKLIST.md) §四 —— 那里已把
> "在 list() 里实时拉上游" 这条旧约定**改写掉**，因为它就是性能事故本身。

---

## 五·补40 ★★ 注入客户端凭证"成功"了但没生效

**现象**：点「注入本机客户端」提示成功，客户端里还是原来的账号。

**根因**：接口一直 200（文件确实写了），但客户端把登录态放在**内存**里，
刷新一次令牌就把 `workbuddy-desktop.info` 整份重写回去。
实测 11:26:02 注入、**11:28:40 被覆盖**。同目录还有
`<info>.<16hex>.observation.json` 记录 `identity.userId`，只改 `.info`
等于给客户端两套互相矛盾的"我是谁"。

**修法**：`core/client_session.py`
· 原子写（临时文件 + `os.replace`，客户端读不到半份 JSON）；
· `.info` 与 observation 一起改（后缀不猜，复用现场那个文件名）；
· 写完 1.2 秒读回来核对 uid，被覆盖就回报 `overwritten`；
· 检测客户端进程；`needs_restart` 时前端二次确认"是否重启客户端"。

**验证**：注入后 `normalize_credential(info).account.uid == 目标 uid`；
客户端在跑时接口返回 `verified=false / needs_restart=true`，而不是假装成功。

---

## 五·补41 ★ 凭证 `.info` 被当成"批量导出"拆开，auth 连同 refreshToken 一起丢

**现象**：账号登录状态正常、额度查询与用量日志都能用，但列表提示
「读不出 accessToken 的 exp，也没有 refreshToken」。

**根因**：客户端完整 `.info` 同时含 `account / auth / accounts / allAccounts`。
`_unwrap_export_wrapper()` 见 `accounts` 就判定为批量导出，取走 `accounts[0]`
（纯账号档案，没有 `auth`）——令牌全丢。**凭证本身是好的**。

**修法**：先判 `_looks_like_credential()`（有 `auth` 子对象或自带 token 字段
就原样返回），再考虑拆信封。

**验证**：`summary_line()` 对 #188 从「两条时间线都无法解析」变成
`access 剩 7 天 / refresh 剩 37 天 / 可自动续期`。

---

## 五·补42 ★ 前端资源初始化轮询无上限，把整个控制台拖进请求风暴

**现象**：DevTools 里 `model-health` / `models/display` / `models/configs`
每几毫秒刷一次，页面一直在重绘。日志统计：几分钟内 `model-health` 被打了 **106 次**。

**根因**：`loadAccounts()` 末尾只要有一个账号处于 `queued/running`，就
`setTimeout(2000)` 再来一次，而且**顺带调 `loadModels()`**（一次 5 个接口）。
没有次数上限、没有退避、没有"卡死"判定 —— 一个后台任务没结束就能让整页无限刷。

**修法**：
1. 轮询**只刷账号面板**；模型页只在收尾时补一次；
2. 指数退避 2s→4s→…→10s；
3. 最多 40 次（约 5 分钟）自动停；
4. 状态 `updated_at` 超过 10 分钟没动 → 判为卡死，停止等待；
5. `document.hidden` 或已切走 tab 时不再轮询。

**验证**：账号池有 queued 号时，Network 面板 60 秒内只应看到个位数请求；
`model-health` 不再周期性出现。

---

---

## 五·补43 ★★ `record()` 提前 return，`/v1/models` 每次都漏一条「进行中」

**现象**：面板动态区里 `/v1/models`、`/v1/balance` 反复出现「streaming」
且永不消失；点几次模型页就多几条，越积越多（实测 25 分钟前的记录还挂着）。

**根因**：`traffic_stats.record()` 开头有两处 `return`：

```python
if _path in SKIP_PATHS: return
if _path not in TRACKED_PATHS: return
```

而清理「进行中」占位的 `finish_running(rid)` 写在**函数末尾**。
`/v1/models` 这类「要显示在面板、但不进对话统计白名单」的路径正好走第二个
`return` —— 于是每条这样的请求都留下一具尸体。

**修法**：把 `finish_running(rid)` 移到 `try` 块的**第一行**，
在任何过滤之前执行（HDEL 幂等，重复调用无副作用）。

**验证**：连打 5 次 `/v1/models` + 5 次 `/v1/balance`，
`HLEN wb:traffic:running` 必须回到 0；面板 `running` 归零。

**教训**：任何「先 return 再清理」的写法都要检查一遍 ——
清理逻辑必须放在**所有出口之前**，否则新增一个白名单路径就多一处泄漏。

---

## 五·补44 ★ 用 `datetime.utcnow().timestamp()` 判过期，东八区凭空多 8 小时

**现象**：临时凭证明明还有 1 小时，网关每次请求都去续期（慢、且刷
refresh_token）；反过来在某些时区又会「过期了还以为没过期」。

**根因**：`datetime.utcnow()` 返回**naive** datetime，
`.timestamp()` 会把它当**本地时间**解释 —— 在 UTC+8 上算出来的 epoch
比真实值大 28800 秒。

**修法**：判过期一律用 `time.time()`（`admin/platform/codearts/.../accounts.py`
的 `needs_refresh`）。**不要**用 `utcnow().timestamp()`。

**验证**：`expires_at = now + 60s` → 需要续期；`now + 3600s` → 不需要。
`tests/test_codearts_platform.py::test_needs_refresh_window` 固化这两条。

---

## 五·补45 ★★ 各平台扫描目录写死 `D:\` / `C:\Program Files` / `C:/Users/Public`

**现象**：用户把客户端装在 E 盘、改名安装文件夹、或系统是别的盘时，
「扫描本机登录态」一律返回空 / 「未检测到客户端」；Trae 读不到
`product.json` 的 `appVersion`，退化成兜底版本号后被上游 `10101` 拒绝；
Loomy 永远只找 `C:/Users/Public/Loomy/<hash>`；Qoder 只认
`Qoder CN.exe` 这一个进程名。写死清单（本次全部清掉）：

| 位置 | 原来写死的 |
|---|---|
| `qoder/core/paths.py`、`loomy/core/paths.py` | `D:\Qoder\...`、`D:\Loomy\...` |
| `codearts/core/paths.py` | `D:/CodeArts/CodeArts.exe` |
| `trae/core/oauth.py` | `D:\Trae CN`、`C:\Program Files\Trae CN` |
| `loomy/services/scan.py` | `C:/Users/Public/Loomy` |
| `wb_install.py` | 扫描盘符集合 `C,D,E,F,G` |

**根因**：把「上游目录名固定」误当成「整条绝对路径固定」。实际上固定的
只有目录名（`WorkBuddy` / `CodeArts` / `com.qodercn.app.stable` …），
**动态的是盘符和用户新建的父文件夹**（`E:\AI\工具\WorkBuddy`）。

**修法**：新增 `admin/platform/install_paths.py`（与平台无关）——
按固定名，在「所有实际存在的盘符」+「盘下 1~3 层动态父文件夹」里
逐层直接拼路径、**命中即停**（冷启动 <200ms，缓存后 ~0.1ms）；
数据目录取 `%APPDATA%` / `%PUBLIC%` 等环境变量，不拼 `C:`。
优先级：`<PID>_INSTALL_DIR` 等环境变量 > 后台「客户端路径」设置
（`system_settings.client_paths`，`/api/client-paths`）> 自动定位 > 不含盘符的兜底模板。
库查询走启动时的后台预热，绝不压在展示接口的请求线程里。

各平台 `core/paths.py` 只留平台知识（固定名 / 特征文件 / exe 名），
对外提供 `app_exe_candidates()` / `data_dir_candidates()`；
`platform_gateway.probe()` 改调函数而不是迭代写死的常量。
Loomy 的 `config_root()` 增加「按 `userData/auth-session.json` 反查」，
域用户 / 多账号 / hash 算法变化时也能找到。

**验证**：`tests/test_install_paths.py`（动态盘符、动态父文件夹、覆盖优先级、
不写死盘符）；`python -c "from ... import paths; print(paths.data_dir_candidates())"`
在真机上应命中 `D:\WorkBuddy`、`D:\Trae`、`D:\CodeArts`、
`%APPDATA%\codearts`、`%PUBLIC%\Loomy\<hash>`；Trae `app_version()` 读到 3.6.4。

**脚手架同步**：`scripts/scaffold.py` 新增 `PATHS_PY` 模板，新平台生成即接入
`install_paths`；`SCAN_PY` 模板删掉 `_appdata()` 写死与 `C:/Users/Public` 示例。

---

## 五·补46 ★★★ 上游会话窗口满，被报成「没有可用账号」

**现象**：单账号 CodeArts 池，5 路并发流式 → 3 成功、2 条 `503
{"message":"CodeArts 没有可用账号（请先在控制台导入并确认状态为启用）"}`。
号明明在，状态 active，额度 4600。

**根因**：`_forward_stream` 把三种完全不同的"拿不到号"混在一个
`for attempt in range(MAX_ATTEMPTS)` 里：

| 真实原因 | 旧代码的处理 | 后果 |
|---|---|---|
| 在途租约满（单号并发上限 3） | `exclude.add(id)` + 消耗一次 attempt | 号被永久排除，第 4/5 路直接 503 |
| 会话窗口满（上游 3 个/55s） | 只在 `attempt == 0` 等一次 | 等完 attempt 也耗尽了，还是 503 |
| 上游真的失败 | 换号 | 正常 |

三种都汇成同一句 503「没有可用账号」，排障方向完全错。

**修法**：

1. 抽出 `reserve_slot(uid, deadline)`（async）——窗口满就**排队**，
   返回 `(slot, waited, timed_out)`；
2. 排队与等租约**不消耗** `MAX_ATTEMPTS`（那个预算是给"上游真失败"的），
   也不把号加进 `exclude`（加进去就再也选不到它了）；
3. 结尾按原因分派：`last_error` → 原样返回；等到超时 → **429**
   `session_window_full` + `Retry-After: 30`；真的没号 → 503。

**验证**：`tests/test_codearts_session_window.py::TestReserveSlotWaits`
（等得到 / 超时报忙）；线上 5 路并发：2 路立即成功、2 路排队 ~55s 后成功、
1 路 429（而非误导性的 503）。

**附带结论（容量口径）**：单账号流式吞吐上限 ≈ **`limit / ttl` = 2 / 55s
≈ 2.2 请求/分钟**。要更高并发只能加号 —— 上游按**账号**计会话，
换号是唯一有效的扩容手段。

---

## 五·补47 ★★★ 会话 id 每请求随机：缓存打不中 + 像脚本

**现象**：用户反馈"同账号尽量用同一个会话 id，别乱串，不然缓存命中不了还容易被风控"。

**根因**（三处，各自随机）：

| 位置 | 原来 | 上游看到 |
|---|---|---|
| `loomy/core/endpoints.py::chat_headers` | `ChatId = "gwchat-" + uuid4()[:16]` | 每请求新建一个会话 |
| `qoder/core/protocol.py::build_upstream_body` | `session_id = "sess-" + uuid4()[:16]`（除非客户端给 `user`/`session_id`） | 同上 |
| `proxy.py::_turn_headers` | 客户端没给会话标识时 `conversation = ""` | 空值 → 上游自造，等于每请求新会话 |

后果是双份：上游前缀缓存永远打不中（费用与时延都上去）；
一个号几分钟内"新建"几十个会话，是风控最敏感的形状。

**修法**：新增 `pool.stable_session_id(platform, account_id, body, prefix, size)`，
用 `sha256(平台 | 账号 | 会话键)` 派生：

* 会话键 = 客户端显式会话标识，或**首条 user 文本**的哈希（`session_key_for`）；
* **同账号 + 同对话 → 恒定**（缓存能命中）；
* **换号必换 id**（一个会话不会跨账号乱串）；
* 客户端什么都没给 → 用首条 user 文本派生（同一段对话恒定），连消息都没有
  才退化成"每账号一个固定会话"；**任何一层都不是随机值**。

`MsgId` / `TurnId` / `X-Conversation-Request-ID` 仍**每请求唯一**
（它们是去重键，复用会导致上游少记账）。

**验证**：`tests/test_session_effort.py`（同对话稳定 / 跨账号不同 / 显式 id 优先 /
无信号时每账号一个常数）。

**这已经是铁律，不是个案**：
[PROTOCOL §4.7](PROTOCOL-ROUTING-CACHE.md)（派生规则 + 哪些 id 必须每次唯一）、
[INTEGRATION §八](INTEGRATION-CHECKLIST.md)（新平台必做清单）、
`SKILL.md` 热路径第 10 条、`scripts/scaffold.py` 的 `CLIENT_PY` 模板已内置
`session_id_for()`。新平台照抄模板即可，不要自己再写一个 id 生成器。

---

## 五·补48 ★★ 思考档位：不记账、被网关抹平、被静默丢弃

**现象**：除 WorkBuddy 外，用量日志的「思考程度」永远是"默认 / 未记录"，
用户以为是客户端没传。

**实测（2026-11-04，直打 CodeArts 上游，每档 3 次）**：

| 请求 | 结果 |
|---|---|
| `reasoning_effort: insane` | **400** `parameter check failed, reasoning_effort must be none or low or medium or high or xhigh or max` |
| `max` ×3 | reasoning_tokens 中位数 **691**（区间 668~1200，两次顶到 max_tokens） |
| `high` ×3 | 272 |
| `xhigh` ×3 | 189（**不比 high 强**，属采样噪声，别把它当"更高档"） |
| `none` ×3 | 346 —— **`none` 不等于关思考** |
| `thinking:{type:"disabled"}` | reasoning_tokens **None**，正文正常 → 这才是真正的关闭方式 |

结论：上游**确实解析**这个枚举（非法值直接 400），`xhigh`/`max` 是**合法值**；
但只有 `max` 有肉眼可见的效果差异。要"完全不思考"必须发
`thinking:{"type":"disabled"}`，发 `reasoning_effort:none` 没用。

**三个网关侧的真问题**：

1. **不记账**：CodeArts / Loomy / Qoder / Trae / Raccoon 的 `_bill()` 都没传
   `reasoning_effort`，`shared.record_usage` 的这个参数一直是默认空串。
   修：`shared.reasoning_effort_of()`（统一口径，认 OpenAI / Responses /
   Anthropic 三种写法）+ `reasoning_effort_of_request()`（从 `scope["_wb_body"]`
   取，不必让每个调用点都往下传 body），五个平台的 `_bill` 全部接上。
2. **被抹平**（Loomy）：`_apply_no_think()` 无条件
   `enable_thinking=False`，用户把档位调到 max 也照样不思考。
   修：下游明确要思考时（`reasoning_effort` 不在 off/none/minimal）
   打开 `enable_thinking` 并把档位带给上游，同时不再往 prompt 里塞
   `/no_think `（那句上游本来就不认，还会当正文念）。
3. **被丢弃**（Qoder）：`_effort_of()` 只认 low/medium/high，`xhigh`/`max`
   直接丢。修：等价映射（xhigh/max/ultra→high，minimal→low）；
   日志仍记**原始值**（读的是下游 body），所以不会失真。

## 五·补49 ★★ TTFT / TPS 记的不是上游真实值（三种错法）

**现象**：面板上多数平台「TPS 暂无样本」；Loomy 永远是 `ttfb 9.33s /
latency 9.49s`（解码段 160ms）；Qoder 干脆没有分段；Raccoon 记过 89ms
的 TTFT 而客户端 2.5 秒才看到字。

**三种错法（都是"看起来有值，实际不可用"）**：

| 平台 | 原来的口径 | 后果 |
|---|---|---|
| Loomy / Trae | TTFT 取**首个正文**，思考链不算 | 思考模型先想 9s、正文 150ms 吐完 → 解码段 160ms → `bench_router` 判"量不出来"，TPS 永久缺失 |
| Qoder | 只写 usage，不写 ttfb / latency | 记账退回「整段耗时」，`ttfb == latency`，TPS 恒不可算 |
| Raccoon | 从 `iter_lines()` 开始计时 | httpx 在 `send(stream=True)` 返回时已缓冲首批字节 → 记出 89ms 的假 TTFT |

**修法（统一口径：首个 token → 末个 token）**：

1. **TTFT = 首个 token（content / reasoning / tool_calls 任一）**。
   reasoning token 也是用户看得见的输出，而且 `completion_tokens` 里包含
   `reasoning_tokens` —— 分子含它，分母就必须从它起算。
2. Qoder `stream_openai` 记录首/末 token 时刻写回 `holder`，网关 `_bill`
   显式传入（4 处调用点）。
3. Raccoon 计时起点移到 `open_stream()` 发请求之前，并只在**带内容的
   delta** 上记 TTFT（首行常是 role/metadata 帧）。
4. CodeArts 的 `total_ms` 改成「最后一个 token」而不是生成器收尾
   （收尾还含 `[DONE]` / usage 帧的网络时间）。

**验证**（同一请求，客户端观测 vs 库里的值）：

```
loomy     客户端 ttfb=1679ms  total=4530ms  DB 1604/4484  tok=441  tps=154.7
qoder     客户端 ttfb=1471ms  total=4931ms  DB  554/4013  tok=732  tps=211.6
trae      客户端 ttfb=25363ms total=34332ms DB 25238/34207 tok=793  tps=88.4
raccoon   客户端 ttfb=2513ms  total=3335ms  DB 2219/3324  tok=160  tps=194.6
workbuddy 客户端 ttfb=1903ms  total=2844ms  DB 1832/2774  tok=66   tps=70.1
```

> Qoder 的库内 TTFT 比客户端小 ~0.9s：它记的是**网关发出上游请求之后**的首字，
> 取 token / 选模型属于本地准备时间，不计入上游延迟。这一点在
> [CAPACITY-PLANNING.md §5](CAPACITY-PLANNING.md) 里写明。

---

## 五·补50 ★★ 默认「60 请求/分钟 + 4 并发」把并发锁死，还伪装成上游慢

**现象**：16 路并发时 Loomy 的 TTFT 从 1.6s 涨到 21.9s、TPS 完全正常。
看起来像"上游扛不住"，实际是准入层按 60/分钟**匀速放行**（约 1 秒 1 个）。

**实测（放开 Key 限后重测，同一台机器同一批号）**：

| 平台 | 1 并发 | 16 并发 | 32 并发 |
|---|---|---|---|
| WorkBuddy（24 号） | 4.6s / 46.9 tps | 6.8s / 50.3 | 7.0s / 49.7 |
| Loomy（101 号） | 1.6s / 162.8 tps | 16.2s / 174.2 | 21.7s / 46.8 |
| Qoder（2 号） | 1.05s / 174.8 tps | — | — |

WorkBuddy 32 并发几乎无衰减 → 池子够大时并发可以拉满；
Loomy 即使把单号在途压到 1（101 个号各跑一路）16 并发仍 16.2s →
**是上游按 IP/用户限速，不是网关挤在几个号上**。

**修法**：

* `ADMIN_GATEWAY_RATE_PER_MINUTE` 60 → **300**，`ADMIN_GATEWAY_MAX_CONCURRENT`
  4 → **24**（Key 上填了值仍以 Key 为准）；
* 账号级按平台覆盖：Loomy `max_in_flight=1`、CodeArts `2`（与 3 会话窗口对齐）、
  Raccoon `2`；WorkBuddy/Qoder/Trae 保持全局 3。

**怎么判断"是限流还是上游慢"**：把 Key 的 `rate_per_minute` 临时调大再测一次
（`_scratch/platform_bench.py --raise-key-limit 64 --rate-per-minute 6000`），
TTFT 掉下来就是自己被限了。完整口径见 [CAPACITY-PLANNING.md](CAPACITY-PLANNING.md)。

---

## 五·补51 ★★★ CodeArts：`developer` 角色 → 200 错误帧 → 客户端无限重试

**现象**：Mirai / deepseek-harness 调 `deepseek-v4.1-flash-codearts`，客户端报
`Stream ended without finish_reason` 并按 ~937ms 退避反复重试；用量日志三条全绿勾、
TOKEN 0、`ttfb == latency`（632/759/870ms）；账号 status=active、余额 4600，换号无效。

**根因**：上游只认 `system/user/assistant/tool`，`developer` 被解析成空 role，
回的是 **HTTP 200 的 SSE 错误帧**：
`{"text":"[DONE]","error_code":"InferHub.001001044.400","error_msg":"Message role cannot empty"}`
—— 没有 `choices`、没有 `finish_reason`。`client.chat_stream()` 旧判断只看
`chunk.get("error")`，漏掉 `error_code/error_msg` 形式，把它当正常 chunk 透传；
网关末尾只补 `[DONE]`，`finally` 里还 `note_success()` 记成成功。
A/B 实测：`developer` 必失败，改 `system`（保留 tools）立即正常。

**修法**（`admin/platform/codearts/core/client.py` + `gateway.py`）：

* `normalize_body()`：`developer → system`、空 role → `user`，流式/非流式统一调用；
* `_inband_error()`：识别 `error_code/error_msg/error` 三种信封（正常 chunk 有 `choices` 则跳过）；
* 流内错误补 `_terminal_frame()`（`finish_reason=stop` + `[DONE]`），
  `holder["error"]` 写进 `error_kind`，不再 `note_success()`。

**验证**：重启 8790 后同一请求 3167 字节完整流（`finish_reason=stop` +
usage 37/30 tok + `[DONE]`）；故意用非法 role → 明确 error 帧 + 终止帧 +
`usage_logs.error_kind='upstream_business'`。详见
[REQUEST-BODY-CONTRACT.md](REQUEST-BODY-CONTRACT.md) §1 / §5。

---

## 五·补52 ★★★ WorkBuddy：11128 指纹在 `/v1/*` 链路没被真正删掉

**现象**：11-02 之后 400 稳定在 4~6%。按「system 里是否带
`x-anthropic-billing-header`」分组：AUX 带 billHdr 785 请求 / 694 个 400
（**88.4%**），不带 647 / 28（4.3%）；MAIN 4425 / 63（1.4%）。
同 key、同 IP、同秒的其它请求 200 —— 不是账号、不是限流。

**根因**：`claude-cli/2.1.310` 的辅助子请求（security monitor / sentinel 哨兵）
在 system 数组首个 text block 里塞 billing header。两条入口链路净化强度不一致：

* `converter.py` 的 `/gw`：`prepare_upstream_body(sanitize=True)` →
  `sanitize_messages()` → `_HDR_RE` **整行删除** ✅
* `admin/routers/proxy.py` 的 `/v1/messages` / `/v1/chat/completions` / `/v1/responses`：
  `_normalize_harness_body()` + `_desensitize_chat_body()`，前者不碰指纹，
  后者只对词表插 U+200B。上游会归一化零宽字符，
  `x-a\u200b…\u200bnthropic-billing-header` 字面仍然成立 → 等于没删。
  `upstream_compat.py:773` 的注释早已写明「零宽字符无效（上游会归一化）」。

**修法**：`proxy.py` 新增 `_strip_channel_fingerprints()`，在
`_desensitize_chat_body()` 开头**无条件**执行（不受 `ANTHROPIC_DESENSITIZE`
开关约束），且**早于**零宽脱敏：调用 `sanitize_messages()` 整行删指纹，
并净化 `tools[].function.description`。顺序反了正则就再也匹配不到。
三条 `/v1/*` 链路共用这一入口，不再各自实现。

**验证**：构造带 billing header 的 system + tools 请求体，
`_desensitize_chat_body()` 输出两处 `has_fingerprint()` 均为 False；
不含指纹的正常请求零改写。断言脚本见
[REQUEST-BODY-CONTRACT.md](REQUEST-BODY-CONTRACT.md) §5。

---

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

### 7.5 改了 dispatch / 模型目录 / 计费 / 凭证（2026-11-04 新增）

```
[ ] python admin/platform/skill/scripts/check_hotpath.py → 0 失败
[ ] 300B / 600KB / chunked 三种请求都走对平台（抓 x-gateway-platform）
[ ] 带后缀模型调用：上游回显的 model 是该平台真实 id（不是 slug）
[ ] 模型配置页每行有「上下文 · 输出 · 输入模态」摘要（不是空）
[ ] 一条短请求：credits ≈ total_tokens × 倍率 / 1000（不是 0）
[ ] 刷新额度：失败能弹出上游原文，成功清掉 last_error
[ ] 导出 backup → 原样回灌 = added 0 / updated N（不是报错）
[ ] 扫描 → 勾选 → 导入，三步都返回 200 且号池数量 +1
```

> 这八项覆盖了「HTTP 200 但结果错」的全部已知形态。
> 只跑契约测试（能加载、方法齐全）**抓不到**其中任何一条。

### 7.6 改了账号列表 / 调度 / 实时面板 / 批量导入（2026-11-04 二次新增）

```
[ ] 账号列表接口 <200ms（curl -w '%{time_total}' 量，不是肉眼看）
[ ] 列表打完后 running 记录不增长：HGETALL wb:traffic:running 前后条数一致
[ ] 选号不会选到 0 余额号：select_account(min_balance=0) 首选 balance>0
[ ] 402 的错误体是上游原文（积分余额不足），不是「请求失败（HTTP 402）」
[ ] 面板「进行中」计数只反映真实在跑：发 1 条 → running=1，完成后 → 0
[ ] 页面上没有周期性出现的 model-health / models/display（Network 面板静置 60 秒）
[ ] 批量导入 100 条 <10 秒（并发验证生效）；进度条一路在动
[ ] 提交导入/导出后弹窗能立刻关掉，任务继续跑（后台队列）
[ ] python admin/platform/skill/scripts/scaffold.py 生成的 list() 不含同步探活
```

> 最后一项是**防复发**：脚手架模板就是"下一个平台的起点"，
> 模板里留着同步探活，这个坑就会一直生新副本。
