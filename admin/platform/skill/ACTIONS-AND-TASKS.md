# 平台动作与任务：五个硬编码陷阱

新增平台时最容易漏的不是协议，而是「按钮点了没反应」和
「任务跑了但用错平台协议」。这五个坑全部实测踩过。

---

## 坑 1：动作按钮只对某个平台注册

```javascript
// platform-switch.js
function actionsFor(spec) {
  if ((spec.storage_platform || spec.id).toLowerCase() !== "trae") return [];
  ...
}
```

后果：新增平台**完全没有扫描/导入按钮**，用户没有入口导账号。

**修法**：能力由后端声明

```python
PlatformSpec.extra["actions"] = ["scan", "import", "refresh-quota", "checkin"]
# -> /api/platforms 下发 actions 字段
# -> 前端 platform-actions.js 照单渲染
```

## 坑 2：动作分发器写死单平台

```javascript
document.addEventListener("wb-platform-action", async event => {
  const storage = ...;
  if (storage !== "trae") return;               // ← 别的平台直接 return
  if (action === "scan") return traeScanLocal();
});
```

后果：点按钮**一点反应都没有** —— 事件发了、处理器直接 return，
连报错都没有。这是最难查的一类（用户以为按钮坏了）。

**修法**：按 `storage_platform` 分派到各平台自己的接口。

```javascript
if (action === "scan") {
  if (storage === "trae") return traeScanLocal();
  return platformScanLocal(spec, storage);   // 通用：/api/<storage>/scan-local
}
```

## 坑 3 ★ 全局扫描读的是**默认平台**的目录

```javascript
async function scanLocal(){
  const r = await api("/api/accounts/scan-local");   // ← WorkBuddy 的实现
}
```

`/api/accounts/scan-local` 属于**默认平台**（WorkBuddy），它会去读

```
%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth
```

实测：**在 Loomy 平台点扫描，扫出来的却是 CodeBuddy 的目录**。

**修法**：按 `storage_platform` 推接口前缀

```javascript
const prefix = storage === "workbuddy" ? "/api/accounts" : `/api/${storage}`;
const r = await api(prefix + "/scan-local" + qs);
```

验证方法（必须做）：在浏览器里逐平台点扫描，抓实际请求 URL：

```
[loomy]     /api/loomy/scan-local?realm=cn
[qoder]     /api/qoder/scan-local?realm=cn
[workbuddy] /api/accounts/scan-local
```

## 坑 4：任务注册在 `__init__` 但 Gateway 返回 `{}`

```python
# __init__.py 里写了
def _task_runners() -> dict:
    return {"loomy_checkin": checkin.run_daily_checkin}

# 但 Gateway 里是
def task_runners(self) -> dict:
    return {}          # ← 空 dict，任务永远跑不到
```

调度器读的是 **Gateway 的 `task_runners()`**，不是 `__init__` 的那个。
两处必须连通。

```python
def task_runners(self) -> dict:
    from admin.platform.loomy import _task_runners as declared
    return dict(declared())
```

**验证**：

```python
gw = registry.load_platform_gateway(registry.get("loomy"), "cn")
print(gw.task_runners())     # 必须非空
```

## 坑 4.5 ★ 声明了 `begin-auth` 但没有授权实现

**现象**：平台动作里有「授权登录」按钮，点了只打开一个网页，
用户登录完账号还是没进号池。

**根因**：早期实现只是 `window.open(首页)` —— 那只是**看网页**，
根本没有走授权链，拿不到 token。

**正解**：`begin-auth` 必须对接 `POST /api/<platform>/device/start`，
由后端生成 PKCE challenge、起本机回调端口，用户登录后自动换 token。
完整骨架见 [`PKCE-AUTH.md`](PKCE-AUTH.md)。

**判据**：授权成功后应该**自动落号池**并提示「授权成功」，
如果还要用户手动「扫描本机 / 导入」，说明授权没真正走通。

**另外**：`supports_local_scan=false` 的平台，前端会隐藏「扫描/导入」，
此时 `begin-auth` 是**唯一**的加号入口 —— 绝不能只声明不实现，
否则这个平台的账号永远加不进来。

---

## 坑 5 ★ 保活任务用错平台协议

```python
def run_keepalive_tokens(db, schedule=None) -> dict:
    from admin.backend import AccountSession       # ← WorkBuddy 的实现
```

`_active_accounts(db, schedule)` 已经按平台过滤了账号，所以对 qoder / loomy
的账号会拿 **WorkBuddy 的协议**去探活 → 必然失败。

**修法**：函数开头按平台分派

```python
target_platform = _platform_of(schedule)
if target_platform and target_platform != "workbuddy":
    spec = <按 storage 找 spec>
    gateway = registry.load_platform_gateway(spec, spec.realm)
    return gateway.account_ops().keepalive(db)
```

同时签名要接受 `schedule`（否则拿不到平台）：

```python
def run_keepalive_tokens(db, schedule=None) -> dict:   # 原来只有 db
```

---

## 附：声明了但没实现的按钮 = 404

`actions` 里声明 `checkin`，但该平台没有 `/api/<storage>/checkin` 路由：

| 平台 | 有手动签到接口吗 | 签到在哪 |
|---|---|---|
| workbuddy | ❌ | 定时任务 `daily_checkin` |
| trae | ❌ | 定时任务 `trae_checkin` |
| qoder | ✅ `/api/qoder/checkin` | 手动 + 定时任务 |
| loomy | ✅ `/api/loomy/checkin` | 手动 + 定时任务 |

**两条都要做**：

1. 没有接口的平台**不要声明** `checkin`
2. 前端对 404 给可读提示（"签到由定时任务执行"），不要露出裸 404

---

## 检查清单（新增平台后逐项过）

```
[ ] spec.extra["actions"] 声明了真实实现的动作
[ ] 动作分发器能路由到该平台（不是 if (storage !== "trae") return）
[ ] scan 请求打到 /api/<storage>/scan-local（浏览器抓包确认）
[ ] scan 返回的 items 字段名被前端兼容（各平台不同）
[ ] task_runners 在 Gateway 里非空（不是 __init__ 里有、Gateway 返回 {}）
[ ] 任务在 schedules.py 的 _TASK_FALLBACK_LABEL 里有中文名
[ ] keepalive_tokens 能按平台分派（不是只走 WorkBuddy AccountSession）
[ ] 声明的动作都有对应路由（否则 404）
```

---

## 各平台 scan 返回的 items 字段差异

前端渲染要兼容，不能只读一种：

| 平台 | 定位字段 | 身份字段 |
|---|---|---|
| Loomy | `source` | `session`（掩码）/ `phone` |
| Qoder | `source` / `data_dir` | `account_id` / `uid_hint` |
| WorkBuddy | `data_dir` | `account_id` |

```javascript
const pick = (it, keys) => {
  for (const k of keys) {
    if (it && it[k] !== undefined && it[k] !== null && it[k] !== "") return it[k];
  }
  return "";
};
const where = pick(it, ["source", "data_dir", "dir", "path"]);
const who   = pick(it, ["session", "account_id", "uid_hint", "uid", "phone"]);
```

**实测**：只读 `source`/`session`/`phone` 时，Qoder 的弹窗是**空白的**
（它的字段叫 `account_id`/`uid_hint`）。
