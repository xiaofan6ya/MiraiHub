# 通用 PKCE 设备授权：三个平台一个套路

> 本项目里 Qoder 和 Trae 都是 PKCE 授权，**流程完全同构**，
> 只有端点、参数名、token 格式不同。这份文档给出可复制的骨架，
> 新平台接进来照抄即可。

另见 [`DEVICE-AUTH.md`](DEVICE-AUTH.md)（Qoder 详细版、refresh 串格式、多账户论证）。

---

## 一、七步骨架（所有平台通用）

```
1. verifier  = base64url(random(32))            ← 留着自己用，绝不外传
   challenge = base64url(sha256(verifier))      ← 放进授权链接
   nonce     = random_hex()                     ← 会话标识

2. 起**本机回调服务器**（或轮询端点，二选一，见下）

3. 拼授权链接 → 给用户点

4. 用户登录并点「授权」

5. 回调/轮询拿到 code（或 token）

6. POST 换 token（code + verifier → access/refresh token）

7. 写 accounts 表（auth_json），刷新余额，落号池
```

**关键：第 1 步的 `verifier` 不进 URL，只有 `challenge` 进。**
拿到 code 时连同 `verifier` 一起去换 token —— 这是 PKCE 的全部意义。

---

## 二、两种收码方式（按平台能力选）

| | 本机回调（redirect） | 轮询（device poll） |
|---|---|---|
| 例子 | **Trae** | **Qoder** |
| 做法 | 起 `http.server` 监听 127.0.0.1:随机端口，等浏览器跳回 | 前端每 3 秒 `GET /poll?nonce=` |
| 参数 | `auth_callback_url=http://127.0.0.1:<port>/authorize` | `nonce` + `verifier` |
| 优点 | 用户体验好（浏览器自动跳回） | 不用开端口，穿 NAT 方便 |
| 缺点 | 端口可能被占；本机防火墙 | 要前端轮询 |

**判据**：授权链接里有 `auth_callback_url` / `redirect_uri` 参数 → 用回调；
只有 `nonce` / `code` 轮询入口 → 用轮询。

---

## 三、Trae 实现（本机回调版）

代码：[`admin/platform/trae/core/oauth.py`](../../trae/core/oauth.py)

### 授权 URL（真实抓包）

```
https://www.trae.ai/authorization
    ?login_version=1
    &auth_from=trae
    &login_channel=native_ide
    &plugin_version=2.3.93439
    &auth_type=local
    &client_id=ono9krqynydwx5
    &redirect=0
    &auth_callback_url=http://127.0.0.1:<PORT>/authorize
    &machine_id=<64位hex>
    &device_id=<19位数字>
    &x_device_id=<同上>
    &x_machine_id=<同上>
    &x_device_brand=NH5x_NH7x_HHx_HJx_HKx
    &x_device_type=windows
    &x_os_version=Windows%2011%20Pro
    &code_challenge=<base64url(sha256(verifier))>
    &code_challenge_method=S256
    &channel_name=common
```

### 换 token

```python
POST https://api.trae.com.cn/cloudide/api/v3/trae/oauth/ExchangeToken
{
  "ClientID":     "ono9krqynydwx5",
  "ClientSecret": "-",              # ← 必须带，漏了回 10101「无效参数」
  "Code":         "<授权码>",
  "CodeVerifier": "<verifier>",
  "DeviceID":     "<device_id>",
  "MachineID":    "<machine_id>"
}
```

**字段名坑**：全是 `ID` 结尾（大写 D），写成 `ClientId` 会被拒。

### 响应结构

```json
{
  "ResponseMetadata": {"Error": {"Code": "10101", "Message": "无效参数"}},
  "Result": {
    "Token": "eyJ...", "RefreshToken": "eyJ...",
    "TokenExpireAt": "...", "RefreshExpireAt": "...",
    "UserId": "51035768889193"
  }
}
```

判据：先看 `ResponseMetadata.Error.Code`（非 0 = 失败），
再从 `Result` 取 — **结果不在顶层**。

---

## 四、Qoder 实现（轮询版）

代码：[`admin/platform/qoder/core/oauth.py`](../../qoder/core/oauth.py)

### 两台主机不能混

| 用途 | 主机 | 路径 |
|---|---|---|
| 人点链接 | `https://qoder.com.cn` | `/device/selectAccounts` |
| 程序轮询 | `https://openapi.qoder.com.cn` | `/api/v1/deviceToken/poll` |

把 poll 挂到门户 → 404。

### 202 / 404 都是「还没授权」

```python
if status in (202, 404):
    return None      # 继续等，不是错误
```

当错误处理会出现「用户还在授权，前端就报失败」。

### refresh 串格式

```
OAuth 来源：  <refresh_token>|<user_id>|<machine_id>
PAT  来源：   pat|<PAT>|<job_refresh>|<user_id>|<machine_id>
```

按 `|` 切分，**最后两段固定是用户和设备**；按段数处理，不按下标硬取。

---

## 四·补、Trae 授权的五个硬参数（照抄客户端，别自己编）

**全部来自客户端日志里的真实 URL**（`%APPDATA%/Trae CN/logs/*/main.log`
搜 `getLoginUrl`），少一个都可能 `Login Failed`：

```
auth_host            国内 https://www.trae.cn    国际 https://www.trae.ai
auth_callback_url    http://127.0.0.1:8790/authorize   ← 路径必须 /authorize
plugin_version       2.3.98528
x_app_version        3.4.11
device_id            账号自己的（库里 auth_json.device_id）
code_challenge       base64url(sha256(verifier))
code_challenge_method S256
login_trace_id       uuid4（上游会原样带回回调）
```

### 三个曾经踩过的错

| 错 | 后果 | 正解 |
|---|---|---|
| host 写死 `www.trae.ai` | 国内版账号被送去国际站，`Login Failed` | 按 realm 分流 |
| 回调路径写成 `/api/trae/device/callback` | 上游路径白名单不认，`Something went wrong` | 必须是 `/authorize` |
| 回调地址后面加 `?nonce=xxx` | 前缀比对失败 | 不要加 query，用 `login_trace_id` 关联会话 |

### 回调路由要挂在根路径

```python
# admin/server.py
app.add_api_route("/authorize", trae_device_callback, methods=["GET"],
                  include_in_schema=False)
```

这个路由**不能要求管理员身份**（浏览器重定向没有 token），
只做会话匹配 → 把 code 交给会话 → 返回美化过的结果页。

---

## 五、后端接口形状（统一）

```python
POST /api/<platform>/device/start?realm=cn
  -> {"nonce": "...", "auth_url": "...", "port": 51291, "expires_in": 600,
      "summary": "打开授权链接 → 选择账号 → 自动回调"}

GET  /api/<platform>/device/poll?nonce=...
  -> {"pending": true}                          # 还没授权
  -> {"ok": true, "account_id": 162, "uid": "..."}   # 成功，已落号池
  -> {"ok": false, "error": "用户拒绝"}          # 失败
```

**前端轮询，后端不阻塞**：后端阻塞 5 分钟会被网关/浏览器超时切断。
实测 2~3 秒一次、最多 150 次（5 分钟）足够。

---

## 六、写进 accounts 表的最小字段

```python
auth_json = json.dumps({
    "token": access,
    "refresh_token": refresh,
    "uid": uid,
    "realm": "cn",
    "device_id": device_id,     # 续期时要用同一个
    "machine_id": machine_id,   # 同上
    "source": "oauth",
    "imported_at": int(time.time()),
    "expiredAt": ..., "refreshExpiredAt": ...,
})
```

`device_id` / `machine_id` **必须存**：上游把它和 refresh token 绑定，
下次续期不一致会被判「异常设备」。

---

## 七、踩过的坑（清单）

```
[ ] verifier 绝不进 URL —— 只有 challenge 进
[ ] challenge = base64url(sha256(verifier))，method=S256
[ ] 端口用 bind(0) 让系统分配，别写死（写死会撞车）
[ ] 回调服务器只处理一次就退出，别长驻
[ ] 会话存内存 + 10 分钟 TTL 清理
[ ] device_id / machine_id 存进 auth_json，续期要用
[ ] 换 token 的字段名以客户端为准（Trae 是 ClientID 不是 ClientId）
[ ] 结果可能包在 Result 里，错误在 ResponseMetadata.Error
[ ] 前端轮询，后端不阻塞
[ ] 拿到 token 后立刻 probe 一次余额/模型，确认真的能用
```

---

## 八、与「扫码导入」的关系

两条路并存，不是替代：

| 方式 | 优点 | 缺点 |
|---|---|---|
| 读本机文件 | 用户零操作 | 只能读到本机那一份；加密平台读不出 |
| **PKCE 授权** | 自动、可续期、**支持无限多账号** | 需要点一次链接 |

本机登录态只有一份，扫文件最多一个账号；
**只有 PKCE 能真正做多账户** —— 每点一次链接就多一个独立 refresh。

判据见 [`NO-CREDENTIAL.md`](NO-CREDENTIAL.md) 的「追凭证四跳」。
