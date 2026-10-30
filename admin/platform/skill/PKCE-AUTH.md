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

### 通用手动回调：反向代理 / 远程访问

授权弹窗支持粘贴回调链接。若授权后浏览器跳到本机 `/authorize`，但页面无法打开，
复制该页面地址栏里的**完整地址**，回到弹窗点击「提交回调链接」。不要复制授权页链接。

```python
POST /api/platforms/<platform_id>/auth/callback      # 需要管理员身份
  {"nonce": "<本次 start 返回的 nonce>", "realm": "<授权区域>",
   "callback_url": "http://127.0.0.1:8790/authorize?..."}
  -> {"ok": true, "summary": "回调已接收，正在完成授权…"}
```

公共模块 `admin/platform/auth.py` 校验链接、平台、区域和会话，
通过平台适配器解析授权参数，将结果交给当前会话，
由 poll 接口触发后台兑换、写入号池，不请求粘贴的 URL。
兑换期间 poll 立即返回 `{"pending": true, "stage": "exchanging"}`，
成功或失败结果在会话有效期内缓存；重复查询不会再次消费授权码。
Trae 兑换建连超时最多重试一次，读取超时或上游 HTTP 错误不重放授权码。
前端回调模式单次请求最多等待 10 秒，短暂网络错误 / 网关 5xx 最多连续重试 4 次，
不会把反向代理的 HTML 错误页直接展示为授权错误。
回调中的会话标识若与本次授权不符会被拒绝；无标识时按提交的 nonce 关联。
错误链接可修正后重试；过期会话需要重新发起授权。自动回调仍然有效。

平台通过 `PlatformSpec.auth_callback="admin.platform.<name>.auth"` 声明适配器模块。
`GET /api/platforms` 公开 `auth.manual_callback` 和 `auth.lifecycle` 能力标记，前端按能力渲染，
不识别平台名称。适配器模块提供：

```python
session_keys = ("state", "nonce")  # 可声明平台实际回传的会话参数名

def get_session(nonce: str) -> CallbackSession | None:
    ...  # 查找本平台已有会话

def parse_callback(params: dict[str, str]) -> dict:
    ...  # 校验协议参数，返回会话 payload；无效参数抛 ValueError
```

回调会话继承公共 `CallbackSession(platform=<存储平台 id>, realm=<区域>)`，
复用 `deliver`、`wait`、`exchange_once`。PKCE 和凭证兑换仍由平台自己实现；
公开轮询结果不能包含 verifier 或授权码。不同平台、区域的会话不能互相提交。
未配置适配器的平台不显示输入框，公共接口明确拒绝手动回调。

当前六个渠道均接入：Trae 国内版 / 国际版、Qoder 国内版 / 国际版、WorkBuddy、Loomy。

公共生命周期接口（均需管理员身份）：

```text
POST /api/platforms/<platform_id>/auth/start?realm=...
GET  /api/platforms/<platform_id>/auth/poll?nonce=...&realm=...
POST /api/platforms/<platform_id>/auth/callback
POST /api/platforms/<platform_id>/auth/bind/send
POST /api/platforms/<platform_id>/auth/bind/verify
```

适配器还需提供 `start(realm)`、`poll(session)`；需要检查完整 URL 时，
可提供 `parse_callback_url(value, session)` 替代 `parse_callback(params)`。
手机号绑定由可选的 `bind(session, action, body)` 实现，其他平台明确拒绝该操作。

- Trae 解析 `authCodeInfo`、`code`、`userInfo`，保留自动回调和旧提交接口。
- Qoder 和 WorkBuddy 接受与当前 state/nonce 匹配的官方完成链接或客户端协议链接，
  凭证仍通过官方轮询核验。粘贴链接不会直接视为登录成功，自动轮询仍然可用。
  Qoder 公共流程在授权成功后直接保存账号；旧设备接口保留兼容。
- Loomy 使用微信回调中的 code/state 完成兑换；需要绑定手机号时，
  poll 返回 `pending: true, stage: "bind_phone"`，控制台显示短信验证表单。
  已绑定账号走已有绑定流程，授权码不会被重复兑换。

Loomy 需要部署者提供应用签名配置文件，默认 `/data/loomy-oauth.json`，
可用 `LOOMY_AUTH_CONFIG_PATH` 指定位置。必填字段为 `base_url`、`access_key_id`、
`access_key_secret`、`app_id`、`wechat_app_id`；可选 `wechat_authconf`、`redirect_uri`。
真实配置不得提交到仓库，建议文件权限 0600；缺少配置时发起授权会明确报错。

会话保存在单进程内存中，重启后需重新授权；轮询任务在后台执行，
终态结果缓存以避免重复保存账号。过期、跨平台或跨区域的会话会被拒绝。

验证命令（Python 依赖见 requirements.txt；浏览器测试需安装 Playwright 和 Chromium）：

```sh
python -m unittest discover -s tests -p 'test_*callback.py'
python -m unittest discover -s tests -p 'test_all_platform_auth.py'
node tests/test_trae_callback_ui.cjs
```

**前端轮询，后端不阻塞**：后端阻塞 5 分钟会被网关/浏览器超时切断。
每 2 秒查询一次，总时限使用 start 返回的 `expires_in`（默认 10 分钟）。

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
