# 凭证生命周期：扫描 · 授权 · 登录 · 导入 · 导出 · 刷新

**账号进不来**和**账号进来了但用不了**是两类完全不同的故障，用户看到的
却都是「导入失败」。这份文档按**六条路径**拆开，每条给出入口、契约、
以及实测踩过的坑。

> 一句话原则：**每条路径都必须能回答「0 条导入的原因是什么」**。
> 返回 `imported: 0` 而不解释，用户只会反复点按钮。

---

## 0. 六条路径总览

| 路径 | 入口 | 凭证在哪 | 何时用 | 落到 |
|---|---|---|---|---|
| **扫描本机** | `GET /api/<storage>/scan-local` | 客户端本地文件 | 同机装了客户端 | 只列清单，**不落库** |
| **导入本机** | `POST /api/<storage>/import-local {uids}` | 同上 | 扫描后勾选入库 | 号池 |
| **导入文件** | `POST /api/accounts/import-async {storage,text}` | 用户手里的备份 | 换机器 / 别人给的备份 | 号池 |
| **授权登录** | `POST /api/<storage>/device/start` 或扫码 | 上游授权流 | 没有本地客户端 | 号池（成功后自动） |
| **Cookie** | `POST /api/<storage>/web-cookie` | 浏览器 | 只为查用量（Trae） | 仅查询 |
| **导出** | `.../export?mode=backup\|plain` | 号池 | 备份 / 迁移 / 对外清单 | 文件 |

**扫描 ≠ 导入。** 扫描只负责「告诉用户本机有哪些号」，导入才写库。
把两者做成一个按钮，就会出现用户报的那种「导入是导入不是扫描」。

---

## 1. 扫描本机：每个平台必须有自己的接口

### 1.1 全局端点属于默认平台

`/api/accounts/scan-local` 是 **WorkBuddy（默认平台）** 的实现，它读
`%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth`。

在 Loomy 平台点扫描却扫出 CodeBuddy 的目录，就是这么来的。

**规则**：前端按 `storage_platform` 推前缀，**不要**用全局端点。

```javascript
const prefix = storage === "workbuddy" ? "/api/accounts" : `/api/${storage}`;
const r = await api(prefix + "/scan-local" + qs);
```

验证方法：浏览器逐平台点扫描，抓实际请求 URL。

```
[raccoon]   /api/raccoon/scan-local
[loomy]     /api/loomy/scan-local?realm=cn
[workbuddy] /api/accounts/scan-local
```

### 1.2 ★ 扫不出来的，要说明「为什么」，别返回空数组

```python
return {"supported": False, "reason": "electron_leveldb",
        "hint": "客户端登录态在 LevelDB 里，不是明文文件"}
```

空数组 `{"items": []}` 会让用户以为「客户端没登录」，于是去重装客户端。

### 1.3 ★ 反过来：注释说「不支持」，代码其实支持

小浣熊的真实教训：第一版文档、`__init__.py`、路由注释三处都写着
「Electron LevelDB，扫不出来」，于是 `supports_local_scan=False`、
界面隐藏按钮。实际客户端在：

```
~/.office-raccoon/agent/config/auth.json     ← 明文 IAM JWT
```

用户手点扫描才发现「明明能扫」。

**规则**：结论必须**代码验证**过再写进文档。三处（README / `__init__` /
routers 注释）是同一份事实的副本，改一处必须同步另外两处，否则下一个人
会照着旧注释把功能关掉。

### 1.4 ★ 扫描结果里的 uid 是脱敏的 —— 不能直接回传给导入

小浣熊扫描返回：

```json
{"uid": "pu-01a1a******", "uid_full": "pu-01a1a3edxxxxxxxx"}
```

前端第一版把**脱敏值**塞进勾选框的 `data-uid`，后端拿它和完整 uid 全等比较
→ 一条都对不上 → 用户看到：

> 本机凭证不在勾选范围内，未导入

两处修（都要做，缺一不可）：

```javascript
// 前端：优先完整值
const uid = it.uid_full || it.uid || it.account_id || "";
```

```python
# 后端：兼容脱敏值（老页面 / 脚本直接调接口）
def _uid_selected(uid: str, wanted: list[str]) -> bool:
    for item in wanted:
        if item == uid:
            return True
        if "*" in item:                       # 脱敏值按前缀匹配
            prefix = item.split("*", 1)[0]
            if prefix and uid.startswith(prefix):
                return True
    return False
```

> 前缀匹配是安全的：uid 前缀足够长（`pu-01a1a` 之后还有十几位），
> 不会误伤同机其它账号。

### 1.5 各平台扫描字段不同，前端必须兼容

| 平台 | 定位字段 | 身份字段 |
|---|---|---|
| Loomy | `source` | `session`（掩码）/ `phone` |
| Qoder | `source` / `data_dir` | `account_id` / `uid_hint` |
| Raccoon | `file` / `source` | `uid` / `uid_full` / `phone` |
| WorkBuddy | `data_dir` | `account_id` |

```javascript
const pick = (it, keys) => {
  for (const k of keys) if (it?.[k]) return it[k];
  return "";
};
const where = pick(it, ["source", "file", "data_dir", "dir", "path"]);
const who   = pick(it, ["nickname", "session", "account_id", "uid_full",
                        "uid_hint", "uid", "phone"]);
```

只读一种字段的后果：Qoder 的扫描弹窗**全空白**。

---

## 2. 授权 / 登录

### 2.1 三条授权形态，交给平台自己声明

| `auth_mode` | 含义 | 例子 |
|---|---|---|
| `device-poll` | 设备码 + 轮询 | Qoder |
| `callback` | 本机回调端口接 code | Trae |
| `qr` / 扫码 | 二维码 + 手机确认 | Raccoon |
| `local-session-only` | 没有授权流，只能扫本机 | Loomy |

`/meta` 下发 `auth_mode` + `auth_explain`，前端不再写 `if (id === "qoder")`。

### 2.2 ★ 扫码拿到的票，可能**调不了业务接口**

小浣熊实测（2026-11-04）：同一账号两张票，行为完全不同。

| 来源 | sid | `/model_catalog` | `/points/balance` |
|---|---|---|---|
| 客户端 `auth.json` | `01a1a3ed` | **200** | 200 |
| 扫码授权 | `01a1a434` | **400 `200901 iam_confirm_required`** | 400 |

上游要求「IAM 会话确认」，而 `/api/iam/v1/auth/*` 下的 confirm 路径全是 404。

**最坏的不是拿不到票，而是把能用的票覆盖掉。** 第一版直接把扫码票写库
（同 uid 更新），于是原本正常的账号突然变成「凭证缺少 auth」、对话全挂。

### 2.3 修法：入库前先验一次，不通就不落库

```python
# 1) 用新票打一次**业务**接口（不要只打 /me、/userinfo 这种）
try:
    raccoon_client.fetch_models(token, realm, None)
except UpstreamError as exc:
    sess["status"] = "failed"
    sess["confirm_required"] = (exc.kind == "confirm")
    sess["error"] = "扫码会话未完成 IAM 确认，已保留原凭证"
    return                      # ← 关键：不写库
# 2) 验证通过才 import_record
```

**三条规则**：

1. 验证用**业务接口**（模型目录 / 积分），别用「能返回 200 但什么都干不了」
   的接口；
2. 验证失败**保留旧凭证**，绝不覆盖；
3. `200901` 归到 `confirm`，**不能**归 `auth` —— 归 `auth` 会把账号直接
   `disabled`，用户明明还能用。

### 2.4 前端：成功必须关弹窗

```javascript
if (status === "done") {
  el.textContent = "✅ 授权成功，账号已入库";
  toast("扫码授权成功，账号已入库");
  loadAccounts();
  setTimeout(closeModal, 900);          // ← 少了这句，弹窗永远停在「等待确认」
  return;
}
```

同时要区分三种终态，文案不能共用：

| 终态 | 文案 |
|---|---|
| `done` | 授权成功，账号已入库 |
| `failed` + `confirm_required` | 会话未完成确认，**已保留原凭证** |
| `failed` | 授权失败：<原因> |

---

## 3. 导入：三条入口，一个契约

### 3.1 入口

| 场景 | 接口 | 载荷 |
|---|---|---|
| 控制台「导入账号」（多文件 / 粘贴） | `POST /api/accounts/import-async` | `{storage, text}` 或 `{storage, items:[{name,auth_json}]}` |
| 平台自己的导入 | `POST /api/<storage>/import` | `{records}` / `{credential}` / `{text}` |
| 扫描后勾选 | `POST /api/accounts/import-local` | `{storage, uids}` |

异步导入要**轮询任务**：`GET /api/accounts/job/{job_id}` → `status` 为
`running` / `done` / `failed`，结果在 `result` 里。同步接口导 100 个号会超时。

### 3.2 ★ 平台必须注册进 `_platform_import_module`

```python
mapping = {
    "trae":    "admin.platform.trae.services.accounts",
    "qoder":   "admin.platform.qoder.services.accounts",
    "loomy":   "admin.platform.loomy.services.accounts",
    "raccoon": "admin.platform.raccoon.services.accounts",   # ← 漏了就是 400
}
```

漏注册的表现：`不支持的导入平台：raccoon`。

### 3.3 `import_batch(db, text)` 契约

```python
def import_batch(db, text: str) -> dict:
    """text = 文件原文（不预先解析），返回统一统计"""
    return {"ok": bool, "added": int, "updated": int,
            "failed": int, "total": int, "errors": [str]}
```

要点：

- **入参是原文**，不是解析好的 list —— 解析逻辑属于平台（各平台备份格式不同）；
- 认的形状要宽：JSON 数组 / `{"accounts":[...]}` / JSONL / 裸 JWT 单行；
- `updated` 对应内部的 `skipped`（已存在的按 uid 更新），**别报成 0 导入**；
- `errors` 截断（`[:50]`），否则 100 个坏号会把响应撑爆。

### 3.4 ★ 导出的文件必须能导回来

WorkBuddy 的导出是**信封**：

```json
{"id":188,"name":"...","uid":"...","format":"client",
 "item":"{\"account\":{...},\"auth\":{...}}"}
```

真正的凭证在 `item`（**字符串**）里。整份文件原样贴回导入，校验器只看到
`id/name/item`，于是报：

> 缺少 auth / 账号 字段

两处都要拆（双保险：老页面 / 脚本直调也能过）：

```javascript
// 前端 submitUpload() 里
const unwrapExport = obj => {
  if (obj && typeof obj === "object" && typeof obj.item === "string") {
    try { const inner = JSON.parse(obj.item); if (inner) return inner; } catch (_) {}
  }
  if (obj && Array.isArray(obj.accounts) && obj.format) return obj.accounts;
  return obj;
};
```

```python
# 后端 normalize_credential() 里
if isinstance(item, str) and item.strip().startswith("{"):
    inner = json.loads(item)
    if isinstance(inner, dict) and ("auth" in inner or "account" in inner):
        return inner
```

> ⚠️ 拆壳后记得把 `changed = True`。否则函数末尾认为「没改动」，
> 又把原串吐回去 —— 拆了等于没拆。

### 3.5 ★ 编码：UTF-8 → GBK → BOM

Windows 下导出的备份常是 **GBK**。直接 `file.text()`（只认 UTF-8）会把中文
昵称读成 `Ð¡ä½ÐÜ 1de8`，而且 JSON 里出现替换字符后**解析不一定报错**，
于是库里的账号名是乱码。

```javascript
async function readCredentialFile(file) {
  const buf = await file.arrayBuffer();
  // 1) 剥 BOM：UTF-8 BOM / UTF-16LE BOM（不剥 JSON.parse 直接失败）
  // 2) 严格 UTF-8（fatal: true）
  // 3) 失败退 GBK
  // 4) 再失败宽松 UTF-8（至少能导）
}
```

`TextDecoder("gbk")` 浏览器原生支持，不需要额外库。

### 3.6 幂等

同 `(platform, uid)` 必须**更新**而不是新增。否则重复导入 = 号池里一堆重复，
调度时同一个号被并发打爆。

---

## 4. 导出

### 4.1 两种模式，一个都不能少

| 模式 | 内容 | 用途 |
|---|---|---|
| `backup` | 完整凭证（含明文 token） | 备份 / 迁移，**可回灌** |
| `plain` | 去掉 token，只留清单 | 对外核对 / 工单 |

只给 backup，用户想「导一份给别人看」就会把自己的 JWT 全发出去。

### 4.2 脱敏不要取 token 尾部

```python
# ❌ 尾部 6 位是 JWT 的**签名**片段，属于凭证的一部分
item["token_tail"] = token[-6:]

# ✅ 头部恒为 `eyJhbG`，零信息量，只用于核对「这条有没有 token」
item["token_prefix"] = token[:6]
item["has_token"] = bool(token)
```

### 4.3 回灌必须实测（往返一致性）

```python
export  →  import  →  期望 added=0 / updated=N（不是新增 N 条）
```

只验证「导出成功」是不够的 —— 那份文件名对、大小对、内容却导不回来。

---

## 5. 刷新额度 ≠ 刷新凭证

### 5.1 ★ 前端必须按平台拼接口

```javascript
// ❌ 写死默认平台：拿小浣熊的 id 打 WorkBuddy 的接口
api(`/api/accounts/${id}/refresh`)

// ✅ 按账号自己的平台
const base = platformApiPrefix(spec, storage);   // workbuddy → /api/accounts
api(`${base}/${id}/refresh`, {method: "POST"})
```

写错的报错长这样（后端的 WorkBuddy 适配器去解小浣熊的凭证）：

> `CredentialError: 账号凭证缺少 auth，请重新导入或授权登录`

### 5.2 ★ 非默认平台的刷新接口**恒返回 200**

各平台的 `/api/<storage>/accounts/{id}/refresh` 直接返回业务结果，
HTTP 状态往往是 200，成败在 body 里：

```json
{"ok": false, "error": {"message": "...", "upstream": {...}}}
```

只判 `r.ok` 会把失败提示成「已刷新」。两套形状都要认：

```javascript
const { ok, data } = await safeJson(r);
const failed = !r.ok || (data && data.ok === false);
if (failed) {
  const detail = data?.error || data?.detail;
  toast("刷新失败：" + (detail?.message || "未知原因"), "err");
  if (detail?.upstream) openModal(upstreamEvidenceHtml(detail.upstream));
}
```

### 5.3 成功要清 `last_err_msg`

刷新成功后不清历史错误，健康账号行上会**永久挂着一句旧报错**，
用户以为还没修好。

```python
apply_quota(account, quota)
account.last_err_msg = None          # ← 关键
db.commit()
```

### 5.4 ★ 积分接口返回空结构 ≠ 余额 0

```python
# ❌ 把 None / {} 逐字段取 0 → apply_quota 把真实余额覆盖成 0
data = request_json(...) or {}
available = data.get("available_points", 0)

# ✅ 拿到的不是 dict、或一个余额字段都没有 → 抛错，保留上次快照
if not isinstance(data, dict) or not data:
    raise UpstreamError("积分查询返回空结构（HTTP 200）", kind="upstream")
```

「余额真的是 0」时上游会返回完整字段（`available_points=0`），
所以抛错不会误伤真实零余额。

### 5.5 partial：凭证有效、只是积分读不到

积分接口 403 / 空结构，但**对话接口正常** —— 这不是账号失效。

```python
if result.get("ok"):        alive += 1
elif result.get("partial"): partial += 1; alive += 1     # 别算失败
else:                       failed.append(...)
```

算成 failed 的后果：保活任务「上线即全红」，运维以为整个号池挂了。

---

## 6. 回归清单（每次动凭证链路都跑）

```
[ ] 逐平台点「扫描本机」，抓包确认打到 /api/<storage>/scan-local
[ ] 扫描结果勾选 → 导入成功（且用的是 uid_full，不是脱敏值）
[ ] 手填脱敏 uid 调 import-local → 仍能命中（兼容旧页面）
[ ] 导入文件：JSON 数组 / JSONL / 单对象 / GBK 编码 各一份
[ ] 导出 backup → 原样回灌 → added=0 / updated=N
[ ] 导出 plain → 文件里搜不到 eyJ（没有 JWT）
[ ] 刷新额度（每个平台各一次）→ 成功清 last_err_msg；失败弹上游原文
[ ] 积分接口返回空 body（可 mock）→ 余额**不被清零**，账号仍 active
[ ] 扫码/授权成功后弹窗自动关闭
[ ] 授权拿到的票先验证再入库，失败时旧凭证不变
```
