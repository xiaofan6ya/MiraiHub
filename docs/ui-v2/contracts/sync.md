# 功能契约：同步设置（S07 `/sync`）

| 项 | 值 |
|---|---|
| 页面 | 同步设置（模板 C） |
| 路由 | `/sync`（hash 路由：`/admin/v2/#/sync`） |
| 设计稿 | `docs/ui-v2/06-pages-d.md` P19（单卡片表单 + 「保存配置」+ 只读状态行） |
| 旧版 | `admin/console/index.html` `#panel-sync`（2426–2446）；`admin/console/console-app.js` 13501–13569；进入页面：1542；切平台：3349 |
| 后端 | `admin/routers/sync.py`：`get_config`(82) / `save_config`(99) / `push`(110) / `receive`(243) |
| 新版交付 | `admin/ui/src/pages/SyncPage.vue`、`admin/ui/src/api/sync.ts`、`admin/ui/tests/page-sync.spec.ts` |
| 页面标题 | 「同步设置」（`router/pages.ts` PAGE_TITLES，不硬编码第二份） |

---

## 一、功能

旧版这一页（`#panel-sync`）的全部能力，按出现顺序：

1. **远程 URL**：线上实例地址（`https://online.example.com`）。
2. **同步密钥（HMAC 签名用）**：与线上实例一致的共享密钥；同一行提供「显示/隐藏」「复制」「重新生成」。
3. **保存配置**：把 URL 与密钥写进本机库（`system_settings`）。
4. **同步到线上**：把本机账号、模型配置、客户端兼容参数推送到线上 `/api/sync/receive`；推送前会先把输入框当前值写库（允许不点「保存配置」直接推送）。
5. **只读状态行**：远程 URL 是否配置、密钥是否设置/打码/来源、最近一次同步的时间与结果。
6. **未保存标记**：表单与已保存值不一致时提示（旧版是 `#syncDirty` 文字，新版是模板 C 的 Badge）。

新版在模板 C 下新增（来自设计稿与 05-patterns，不是旧版能力）：
7. 卡片「取消」（恢复为上次加载的已保存值）。
8. 首次加载骨架 / 刷新中保留旧数据 / 空状态 / 错误块 + 重试 四种状态。
9. 关键字：**保存按卡片**（本页只有一张卡片，因此等价整页保存）。

---

## 二、数据展示

### 2.1 表单字段

| 字段 | 控件 | 默认值 | 必填 | 说明（与后端一致） |
|---|---|---|---|---|
| 远程 URL | `NInput` 单行 | `GET /api/sync/config` 的 `remote_url`（空串=未配置） | 否 | 保存时前端 `trim` 并去掉末尾 `/`（对齐后端 `remote_url.rstrip("/")`）。**留空不会清除**：后端 `save_config` 只在非空时写库。 |
| 同步密钥 | `NInput`（密码/明文切换）+ 显示/隐藏/复制 | `GET` 的 `secret`（后端对已登录管理员返回完整密钥） | 否 | 保存时 `trim`；留空提交=保留库里的密钥（库中也没有时后端自动生成）。后端无长度/字符校验。 |

密钥说明文案（旧版「基于本机特征自动生成（IP/设备唯一）」**已不成立**，必须按现状改写）：
> 密钥用于 HMAC-SHA256 签名。后端首次访问时自动生成 256 bit 随机密钥（`secrets.token_hex(32)`，64 位十六进制），与主机特征无关；「重新生成」使用浏览器 `crypto.getRandomValues` 生成同等强度密钥。

### 2.2 只读状态行

| 行 | 来源字段 | 格式 | 着色/规则 |
|---|---|---|---|
| 远程 URL | `remote_url` | 已配置：等宽文本 + 复制按钮；未配置：「未配置」 | 已配置 `--fg`；未配置 `--fg-muted` |
| 密钥 | `secret_masked`（后端 `"*"×8 + 后 4 位`）；为空时回落 `maskSecret(secret)` | 打码等宽文本；未设置：「未设置」 | 打码 `--fg` |
| 密钥来源 | `is_auto_generated` | `true` → Badge brand「本次访问自动生成」；`false` → 不显示 Badge | 只表达"本次 GET 时刚生成"，**无法区分历史上自动/自定义**（后端字段语义如此） |
| 最近同步 · 结果 | 见 2.3 | Badge ok「成功」/ danger「失败」 | 成功 ok，失败 danger |
| 最近同步 · 时间 | 见 2.3 的 `at` | `fmtRelative(at)`（刚刚 / N 分钟前 / 昨天 / 日期），悬停 `fmtFullTime` | `--fg-muted` |
| 最近同步 · 目标 | 记录里的 `remote` | 「推送至 https://…」 | `--fg-muted`，等宽 |
| 最近同步 · 详情 | 见 2.3 | 「账号 +3 / ~9 · 模型 +1 / ~6 · 客户端参数 已同步 2 项」 | 数字 tabular-nums |
| 最近同步 · 失败原因 | 记录里的 `error` | 后端 `detail` 原文 | `--danger` |
| 无记录 | — | 「尚未同步」 | `--fg-muted` |

### 2.3 「最近同步」的数据来源（重要）

**后端 `GET /api/sync/config` 目前不返回最近同步时间/结果**（已逐行核对 `admin/routers/sync.py:get_config`，只有 `remote_url` / `secret` / `secret_masked` / `is_auto_generated` 四个字段）。因此：

1. 首选后端字段（前向兼容）：若 `GET` 响应出现 `last_push_at` 与 `last_push_result`（形状同 `POST /api/sync/push` 的 `remote_result`），直接采用，标记 `source = backend`。`last_push_at` 用 `toDate()` 解析（秒级时间戳与 ISO 都认）。
2. 否则用本机记录：`POST /api/sync/push` 成功后把真实响应的计数写入 `localStorage['mh.sync.lastPush']`；失败时写入 `{ok:false, error:<detail>}`。标记 `source = local`。

`mh.sync.lastPush` 的形状（`src/api/sync.ts` `SyncPushRecord`）：

```json
{
  "at": 1761858456000,
  "ok": true,
  "remote": "https://online.example.com",
  "accounts": { "added": 3, "updated": 9 },
  "models": { "added": 1, "updated": 6 },
  "profile": { "applied": true, "fields": 2, "errors": [] },
  "error": ""
}
```

- `at` 为毫秒时间戳（展示走 `fmtRelative`）。
- `accounts` / `models` / `profile` 允许 `null`（线上未回传对应结构时显示 `—`）。
- 解析失败 / 形状不对 / localStorage 不可用时一律当"无记录"，页面显示「尚未同步」，不报错。
- 记录只在本浏览器有效；换设备/清缓存后消失（契约已知缺口，见 §六）。

### 2.4 数字与文案格式

- 计数用 `fmtInt`（千分位）；空值 `—`（`--fg-faint`）。
- 时间用 `fmtRelative` / `fmtFullTime`（`src/utils/format.ts`）。
- 密钥打码优先用后端 `secret_masked`，不用自造格式。

---

## 三、交互

| 控件/动作 | 文案 | 调用 | 成功 | 失败/确认 |
|---|---|---|---|---|
| 保存配置（卡片主按钮） | 「保存配置」 | `POST /api/sync/config` `{remote_url, secret}`，随后 `GET /api/sync/config` 重载 | Toast「已保存同步配置」 | Toast「保存配置失败：<原因>」；按钮 loading |
| 取消（卡片次按钮） | 「取消」 | 无请求 | Toast「已恢复为上次保存的配置」 | 表单已干净时不提示 |
| 同步到线上（卡片标题右侧） | 「同步到线上」 | 先确认 → `POST /api/sync/push` `{remote_url, secret}`（带输入框当前值） | Toast「已同步到线上」；状态行更新计数与时间；表单回到已保存值 | 确认框标题「同步到线上？」，正文「会用本地账号、模型配置与客户端参数覆盖线上实例的同名数据。」；失败 Toast「同步到线上失败：<原因>」并记录失败状态 |
| 显示/隐藏密钥 | icon（aria-label「显示密钥」/「隐藏密钥」） | 无 | — | — |
| 复制密钥 | icon（aria-label「复制密钥」） | `useCopy()` | Toast「已复制」 | Toast「复制失败：浏览器拒绝了剪贴板访问，请手动选中复制」 |
| 重新生成 | 「重新生成」 | 生成后只填进输入框（不自动保存） | 确认框「重新生成同步密钥？」+ 后果说明；Toast「已生成新密钥，记得点「保存配置」」 | 取消则不改 |
| 刷新 | 「刷新」（页头） | `GET /api/sync/config` | 静默更新状态行 | Toast「刷新同步配置失败：<原因>」，保留旧数据 |
| 远程 URL 失焦 | — | 无 | 末尾 `/` 自动去掉 | — |
| 离开页面 | 模板 C 离开确认 | 无 | — | 有未保存修改时弹「有未保存的修改」，离开会丢失修改 |
| 关闭标签页 | 浏览器原生确认 | — | — | 同上 |

### 3.1 未保存判定（`isSyncFormDirty`）

- URL：`normalizeRemoteUrl(输入) !== normalizeRemoteUrl(已保存)`。
- 密钥：输入值（`trim` 后）与已保存值不同即算修改；**清空输入框也算修改**（保存后后端会保留原密钥，页面重载后恢复显示原值，Toast 仍为「已保存同步配置」）。
- 未保存时：`SettingsCard` 标题旁 Badge warn「未保存」；`SettingsLayout` 页头 Badge「有未保存的修改」并接管路由离开确认。
- 「保存配置」在干净时禁用（`saveDisabled`）。

### 3.2 刷新与平台切换时的表单处理

- 首次加载、保存成功、推送成功：用响应覆盖表单。
- 手动刷新 / 平台切换：若表单有未保存修改，**不覆盖**用户输入，只更新状态行与脏判定基线；否则同步为最新值。
- 平台切换：订阅 `usePlatformStore().onChange`，重取 `GET /api/sync/config`；卸载时取消订阅。

### 3.3 推送前置校验（前端，不替代后端）

- 远程 URL 为空：不发请求，Toast warning「请先填写远程 URL」。
- URL 非 `http(s)`：仅把字段下方提示改成「不是 http(s) 地址；后端不校验格式，但推送一定失败」，**不拦截**保存（后端 `save_config` 确实不校验）。
- 密钥为空：允许（后端会沿用库里的值；推送时若库里也没有会返回 400 原文）。

---

## 四、接口

> 所有请求走 `@/api/http` 的 `http<T>(method, path, { query, body })`：自动带 `credentials: 'same-origin'`、非 GET 自动补 `X-CSRF-Token`（Cookie `wb_csrf`）、每个请求带 `X-Platform`。页面不直接 `fetch`。

### 4.1 `GET /api/sync/config` — 读取配置

- 后端：`admin/routers/sync.py:get_config`（`require_admin`）。
- 调用时机：进入页面、点「刷新」、平台切换、保存/推送成功后。
- 响应（真实形状）：

```json
{
  "remote_url": "https://online.example.com",
  "secret": "3f9c…（64 位十六进制，完整）",
  "secret_masked": "********9f2e",
  "is_auto_generated": false
}
```

| 字段 | 类型 | 含义 |
|---|---|---|
| `remote_url` | string | 已保存的线上地址；空串=未配置 |
| `secret` | string | 完整密钥（仅已登录管理员可见） |
| `secret_masked` | string | `"*"×8 + 后 4 位`（长度 ≤4 时为 `****`，空密钥为空串） |
| `is_auto_generated` | boolean | **本次请求**是否刚生成密钥（库中无密钥时为 true，并已落库） |

- 副作用：库中无密钥时该 GET 会生成并写入 `sync_secret`（首次访问即"已设置"）。
- 前端类型：`SyncConfigPayload`；前向兼容可选字段 `last_push_at` / `last_push_result` / `last_push_error`（当前后端不返回）。

### 4.2 `POST /api/sync/config` — 保存配置

- 后端：`admin/routers/sync.py:save_config`。
- 请求体：`{ "remote_url": string, "secret": string }`。
- 响应：`{ "ok": true, "secret_set": true }`。
- 实际语义（前端必须照抄，不能自行加限制）：
  - `remote_url` **非空才写库**，写库前 `rstrip("/")`；空串=保持原值（无法通过本接口清除 URL）。
  - `secret` `strip()` 后为空 → 沿用库里的值；库里也没有 → 自动生成；非空 → 原样写入（不去重、不校验强度）。
- 保存后必须重新 GET：以库里实际值为准（末斜杠、留空未改动、自动生成的密钥都只有重取才准确）。

### 4.3 `POST /api/sync/push` — 同步到线上

- 后端：`admin/routers/sync.py:push`。
- 请求体（可选）：`{ "remote_url": string, "secret": string }`。带上时后端先写库再推送（对应旧版"输入框的值直接推，不必先保存"）。
- 推送内容（后端组装，前端不参与）：
  - `accounts[]`：`platform, uid, name, enterprise_id, domain, status, auth_json, balance_total, balance_remain`（本机全部账号，跨平台）。
  - `models[]`：`platform, level, model_id, enabled, credit_multiplier, credits_raw, note, description`。
  - `client_profile`：`cprofile.effective()` 里的可同步字段（`desktop_version, cli_version, user_agent, ide_name, ide_type, product, fp_product, ext_name, os, arch, os_version, cpu_cores, memory_size, timezone, report_delay, commit, release_date_ms, turing_channel_id, turing_product_name, node_version, stainless_package_version, headers`），空值被去掉。
  - 签名：`X-Sync-Signature = HMAC-SHA256(secret, raw_body) hex`，目标 `{remote_url}/api/sync/receive`。
- 响应（真实形状）：

```json
{
  "ok": true,
  "remote": "https://online.example.com",
  "accounts": 12,
  "models": 7,
  "client_profile": true,
  "profile_fields": ["cli_version", "desktop_version", "headers"],
  "remote_result": {
    "ok": true,
    "accounts": { "added": 3, "updated": 9 },
    "models": { "added": 1, "updated": 6 },
    "client_profile": {
      "applied": true,
      "source": "saved",
      "fields": ["desktop_version", "user_agent"],
      "errors": []
    }
  }
}
```

| 字段 | 类型 | 含义 / 展示 |
|---|---|---|
| `remote` | string | 推送目标 |
| `accounts` / `models` | number | 本次推送的**本地条数**（不是线上结果） |
| `client_profile` | boolean | 本次是否带了客户端参数（`false`=本地为空） |
| `profile_fields` | string[] | 实际推送的客户端参数字段名 |
| `remote_result.accounts` | `{added, updated}` | 线上 upsert 结果 → 「账号 +a / ~u」 |
| `remote_result.models` | `{added, updated}` | → 「模型 +a / ~u」 |
| `remote_result.client_profile.applied` | boolean | 线上是否成功钉住客户端参数 |
| `remote_result.client_profile.fields` | string[] | 线上应用的字段 → 「客户端参数 已同步 N 项」 |
| `remote_result.client_profile.errors` | string[] | 失败原因 → 「未同步：…」 |

- 错误：`400`「请先在「同步设置」中填写并保存 远程 URL 与 密钥」；`502`「线上返回错误 <status>: <text>」或「推送失败: <exception>」；`401` 由 `http` 层跳登录。
- `remote_result` 缺失/形状异常时：计数与客户端参数显示 `—`，推送本身仍算成功。

### 4.4 `POST /api/sync/receive` — 接收端（本页不调用）

- 后端：`admin/routers/sync.py:receive`。线上实例用本地库里的 `sync_secret` 验签（`hmac.compare_digest`），失败 `401`「签名校验失败」；未配置密钥 `400`。
- 按 `(platform, uid)` / `(platform, model_id)` upsert；`client_profile` 以 `source=saved` 合并（`merge=false`）。
- 页面只在说明文案里提到它，不发起请求。

### 4.5 前端本地记录（补齐后端缺口）

- 键：`mh.sync.lastPush`（见 §2.3）。
- 写入时机：`POST /api/sync/push` 成功（`pushRecordFromResult`）或失败（`pushRecordFromError`）。
- 读取时机：页面初始化（`readSyncRecord`）；损坏/不可用时忽略。
- 不写入服务器；不参与推送。

---

## 五、跨页联动

### 5.1 平台上下文

- 页面在 `router/pages.ts` 的 `PLATFORM_SCOPED_KEYS` 中（顶栏显示平台选择器），旧版切平台会重跑 `loadSyncConfig()`（`console-app.js:3349`，见 `contracts/platform-context.md` §6.1/§8）。
- 订阅 `usePlatformStore().onChange(cb)` → 重取 `GET /api/sync/config`；`onBeforeUnmount` 取消。
- **接口数据本身与平台无关**：`sync_remote_url` / `sync_secret` 是全局 `system_settings` 键，`push` 推送的是全部平台的账号与模型（`db.query(Account).all()`）。切平台重取不会改变数据，只是保持旧版行为与页面状态一致。契约明确记录这一点，避免后续误以为按平台隔离。
- `X-Platform` 头照常带上（`http` 层统一行为），后端 `/api/sync/*` 不读它。

### 5.2 与其他页面的数据关系

- 推送内容来自账号池（`/accounts`）、模型配置（`/models`）、客户端参数（`/client`）。本页只读地触发一次全量推送，不修改这些页面的数据；推送后这些页面无需刷新（数据是推出去，不是拉回来）。
- 线上实例的客户端参数会被钉成 `source=saved`，等价于在 `/client` 里保存了一份参数（对线上实例而言）。

### 5.3 导航与全局事件

- 本页无跳转入口；不派发全局事件。
- `401` → `http` 层 `onUnauthorized()` 跳登录页。

---

## 六、边界情况

| 场景 | 表现 |
|---|---|
| 首次加载中 | 卡片内骨架（`NSkeleton`，统一一种样式），表单与状态行都不渲染 |
| 首次加载失败 | 卡片内 `ErrorBlock`「同步配置加载失败」+ 详细 `detail`（可展开）+「重试」；不弹 Toast |
| 刷新失败（已有数据） | 保留旧数据；卡片内 compact `ErrorBlock`「刷新同步配置失败，当前显示上次加载的值」+「重试」；同时 Toast 错误 |
| 空配置（理论少见） | `remote_url` 与密钥都为空 → 状态区 `Empty`「还没有配置线上实例。填写远程 URL 并保存后即可同步。」（后端 GET 会自动生成密钥，所以此分支主要用于异常/代理改写响应） |
| 从未推送 | 最近同步区显示「尚未同步」，不显示时间与计数 |
| URL 留空保存 | 后端不写库；重载后显示原地址（不是清空） |
| URL 带尾斜杠 | 前端去尾斜杠后再提交；后端也 `rstrip`，两边一致 |
| URL 非 http(s) | 字段提示变红字说明；允许保存（后端不校验）；推送会失败并给出后端 `detail` |
| 密钥留空保存 | 后端保留原密钥；重载恢复显示；若库里也没有则自动生成（`is_auto_generated=true`） |
| 密钥含首尾空格 | 前端 `trim` 后提交，与后端 `.strip()` 一致 |
| 推送时未填 URL | 不发请求，Toast warning「请先填写远程 URL」 |
| 推送 400（未配置） | Toast「同步到线上失败：请先在「同步设置」中填写并保存 远程 URL 与 密钥」；状态行记录失败与时间 |
| 推送 502（线上 4xx/5xx / 连接失败） | 同上，错误文案取后端 `detail`；状态行记录失败 |
| `remote_result` 缺字段 | 对应计数/客户端参数显示 `—`，其余正常 |
| `client_profile: false` | 客户端参数「为空，未同步」 |
| `applied: false` | 「未同步：<errors 用；拼接>」，无 errors 时「未同步：线上未接受」 |
| localStorage 不可用 | `readSyncRecord` 返回 null，`writeSyncRecord` 静默失败；本次会话仍显示状态（内存 `record`） |
| 保存/推送并发点击 | `saving`/`pushing` 互斥，按钮 loading 期间禁用 |
| 平台切换时有未保存修改 | 不覆盖表单，只更新状态行（见 §3.2） |
| 401 | `http` 层跳登录，页面不再处理 |
| 移动端 ≤1023px | 锚点目录变横向滚动条，卡片单列；≤640px 表单控件占满宽、密钥按钮与输入框换行 |

---

## 七、旧版有但设计稿没写到的功能，与新版落点

| 旧版功能 | 新版落点 |
|---|---|
| `#syncDirty` 文字「有未保存改动」 | 模板 C：`SettingsCard` 标题旁 Badge warn「未保存」+ `SettingsLayout` 页头 Badge / 离开确认 |
| 密钥旁的「显示/隐藏」「复制」按钮 | 密钥输入框 `#suffix` 里的两个 `IconBtn`（24px），带 aria-label 与 tooltip |
| 「重新生成」按钮（旧版生成 16 字节=32 hex） | 保留按钮，改为 32 字节（64 hex，与后端 `token_hex(32)` 同为 256 bit）；确认框与提示文案沿用旧版语义 |
| 推送成功后把 `accounts/models/client_profile` 结果拼进状态行 | 保留：结果写成 `mh.sync.lastPush` 并渲染「账号/模型/客户端参数」三组计数；失败也记录 |
| 「正在推送...」即时 Toast | 去掉（改为按钮 loading，避免重复 Toast） |
| 旧版编辑器没有「取消」 | 模板 C 的「取消」= 恢复上次加载值 |
| 旧版无刷新按钮（靠切页/切平台重取） | 页头「刷新」按钮 + 平台切换重取 |

---

## 实现对照

| 功能 | 新版位置 | 状态 |
|---|---|---|
| 远程 URL 输入 + 尾斜杠归一 | `SyncPage.vue` 连接区 + `api/sync.ts:normalizeRemoteUrl` | 已实现 |
| 同步密钥输入（密码/明文切换） | `SyncPage.vue` 密钥 `NInput` + `secretVisible` | 已实现 |
| 显示/隐藏密钥 | 密钥输入框 suffix `IconBtn` | 已实现 |
| 复制密钥 | `useCopy()` + Toast | 已实现 |
| 重新生成密钥（确认 + 32 字节随机） | `regenerate()` + `api/sync.ts:generateSyncSecret` | 已实现 |
| 保存配置（按卡片、未保存 Badge、成功/失败 Toast、保存后重取） | `SettingsCard @save` + `saveSyncConfig` | 已实现 |
| 取消（恢复已保存值） | `SettingsCard @reset` → `reset()` | 已实现 |
| 同步到线上（确认、带当前输入值、成功/失败 Toast） | `push()` + `pushToOnline` | 已实现 |
| 推送结果计数（账号/模型/客户端参数） | `api/sync.ts:summarizePushResult/formatPushSummary` | 已实现 |
| 只读状态行：URL / 密钥打码 / 密钥来源 | `describeSyncConfig` + `KV` | 已实现 |
| 最近同步时间与结果（后端无字段 → 本机记录 + 后端字段前向兼容） | `api/sync.ts:readSyncRecord/describeSyncConfig` | 已实现（来源注明，见 §2.3） |
| 未保存判定与离开确认 | `isSyncFormDirty` + `SettingsLayout`/`SettingsCard` | 已实现 |
| 首次加载骨架 / 刷新中 / 空 / 错误 + 重试 | `SyncPage.vue` 四种状态分支 | 已实现 |
| 平台切换重取（订阅 + 卸载取消） | `usePlatformStore().onChange` | 已实现 |
| 500/400/401 错误文案 | `failureText`（`utils/error`） | 已实现 |
| 路由 query 同步（筛选/分页） | — | 不适用：本页无列表/筛选/分页 |
| 深色/浅色、≤1023/≤640px | 全部用令牌与模板 C 断点 | 已实现（未截图验证，见交付说明） |
