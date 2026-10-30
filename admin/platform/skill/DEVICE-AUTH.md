# 无桌面导入的平台：设备授权（PKCE）

有些平台的登录态**读不出文件**。硬啃文件解析不如换条路：
让用户点一次链接，自动拿可续期的 token。

---

## 先判断：读得出文件吗

| 平台 | 登录态存在哪 | 读得出吗 |
|---|---|---|
| **Loomy** | `<configRoot>/userData/auth-session.json`（**明文**） | ✅ 直接读 |
| **Qoder** | `auth.v1.dat`（原生层，DPAPI 之外的自定义加密） | ❌ 读不出 |
| Trae | `storage.json` + 自定义混淆 | 部分 |

**先花时间确认真实存储形态**，别一上来就写设备流。
Loomy 那种明文 JSON 是最省事的路径（用户零操作）。

判断方法见 `NO-CREDENTIAL.md` 的「追凭证四跳」。

---

## 读不出时的正解：PKCE 设备授权

参考实现（Agent2API）对 Qoder 就是这么做的 —— 它**没有**解析
`auth.v1.dat`，而是走设备流。这是正解，不是妥协。

### 完整流程

```
1. verifier   = base64url(random(32))
   challenge  = base64url(sha256(verifier))
   nonce      = random_hex()
   machine_id = 稳定的设备标识（UUID5(用户名|主机名)）

2. 拼授权链接（挂在**门户**主机，不是 API 主机）
   {web_origin}/device/selectAccounts
       ?challenge=<challenge>
       &challenge_method=S256
       &machine_id=<machine_id>
       &nonce=<nonce>

3. 让用户点开链接，在浏览器里选账号授权

4. 轮询（挂在**OpenAPI** 主机）
   GET {open_api}/api/v1/deviceToken/poll
       ?nonce=<nonce>&verifier=<verifier>&challenge_method=S256
       - 202 / 404 -> 还没授权，继续等
       - 200       -> 拿到 token / refresh_token / user_id / expires_at

5. 拼 refresh：`<refresh>|<user_id>|<machine_id>`
   （参考实现的格式，最后两段固定是「用户 | 设备」）
```

### 两台主机的区别（容易搞错）

| 用途 | 主机 | 路径 |
|---|---|---|
| 人点链接 | `web_origin` = `https://qoder.com.cn` | `/device/selectAccounts` |
| 程序轮询 | `open_api` = `https://openapi.qoder.com.cn` | `/api/v1/deviceToken/poll` |

**不是同一台主机。** 把 poll 挂到门户会 404。

### 前端轮询而不是后端阻塞

后端阻塞一个请求 5 分钟会被网关/浏览器超时切断。
正确做法：

```
前端点按钮 -> POST /device/start 拿到 auth_url + nonce
          -> 展示链接
          -> 每 3 秒 GET /device/poll?nonce=...
          -> 拿到 credentials -> POST /device/claim 落账号
```

实测可用。会话存内存（`nonce -> DeviceLogin`），10 分钟清理。

---

## 实现要点（踩过的）

### 1. `machine_id` 要稳定

参考实现用系统信息生成。用 `UUID5(namespace, 用户名|主机名)` 即可 ——
同机恒定，换机才变。**不要每次随机**：上游会把它绑进 refresh 串。

### 2. refresh 串的格式

```
OAuth 来源：  <refresh_token>|<user_id>|<machine_id>
PAT 来源：    pat|<PAT>|<job_refresh>|<user_id>|<machine_id>
```

解析时按 `|` 切分，**最后两段固定是用户和设备**：

```python
parts = refresh.split("|")
if len(parts) >= 3:
    prefix = "|".join(parts[:-2])
    refresh = f"{prefix}|{user_id}|{machine_id}"
```

`len(parts) < 3` 时整串当前缀（拼出的串上游会拒绝，但不会崩）。
**不要按下标硬取** —— 段数不足会越界。

### 3. 202 和 404 都是「还没授权」

```python
if status in (202, 404):
    return None      # 继续等，不是错误
```

把 404 当错误会导致「用户还在授权，前端就报失败了」。

### 4. expires_at 三种格式

```
毫秒时间戳  1793082630934      -> 原样
秒时间戳    1793082630         -> ×1000
ISO 字符串  2026-10-29T00:00Z  -> 解析
```

判据：`number < 100_000_000_000` 就是秒。

---

## 和 PAT 的关系

两条路并存，不是替代：

| 方式 | 优点 | 缺点 |
|---|---|---|
| PAT | 用户自己生成，永久有效 | 手动操作；泄漏风险；换环境要重生成 |
| 设备授权 | 自动；可续期；**支持多账户** | 需要点一次链接 |

**都要提供。** PAT 作为兜底（用户已有 token 时最快），
设备授权作为主路径（多账户场景必需）。

UI 上体现为两个按钮：

```
[授权登录]   -> 设备流，自动完成
[导入本机]   -> 扫描到就导入（加密时提示用 PAT/设备流）
```

---

## 多账户问题（用户提的重点）

> 「你扫不出来导入跟保活的话后期会很麻烦，不能多账户了」

三个能力缺一不可：

| 能力 | 读文件方案 | 设备授权方案 |
|---|---|---|
| 添账号 | 扫到一个 | **每次点一次链接，可加无限个** |
| 保活 | 靠文件里的 refresh | **refresh_token 自动续期** |
| 多账户 | 本机只有一份登录态 | **每账号一个 refresh** |

**只有设备授权能真正做到多账户** —— 本机登录态只有一份，
扫文件最多扫出一个账号。

这也是为什么读不出文件不一定是坏事：设备流本来就更适合多号。

---

## 检查清单

```
[ ] 先确认登录态在哪（能读明文就别上设备流）
[ ] web_origin 和 open_api 是两台主机，别混
[ ] challenge = base64url(sha256(verifier))，challenge_method=S256
[ ] machine_id 稳定（同机恒定）
[ ] 202/404 都当「等待」，不当错误
[ ] refresh 串按段数处理，不按下标硬取
[ ] expires_at 兼容 秒/毫秒/ISO
[ ] 前端轮询（3 秒），后端不阻塞
[ ] 会话存内存 + TTL 清理
[ ] PAT 路径保留作兜底
```
