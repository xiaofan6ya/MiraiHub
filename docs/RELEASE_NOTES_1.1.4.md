# MiraiHub v1.1.4

这次主要是把腾讯 Marvis 这条路真正跑通 —— 续签打通、首次录入不再误报、重新授权能救活号，公开注册入口也接上了。另外修了 Android 端应用名还叫 wb2api 的问题。

## 新增：Marvis 续签打通（感谢 @hxzzlg 的 PR #43）

在此之前 Marvis 的 token 过期只能重新扫码。现在能自动续期了。

**关键在请求体形状**，不是方法用错：

```json
{"userInfo":{"openId":"…","refreshToken":"…","accessToken":"…","loginType":"Marvis"}}
```

上游读的是 `body.userInfo.{openId, refreshToken, accessToken}`，`loginType` 要字面量 `"Marvis"`（不是登录方式码 `"1"`/`"2"`）。少一层嵌套、字段写小写 `openid`、或 loginType 填成码，回的都是同一句 `-109 missing required param` —— 所以从错误文案反推参数位置永远推不出来。

真机回执（两种登录方式都验过）：

| 账号 | 签发 | 续签结果 |
|---|---|---|
| QQ 号 | 12.2 小时 | `code:0`，`expires_in:"2592000"`（30 天） |
| 微信号 | 12.5 小时（此前已 401） | `code:0`，`expires_in:"7200"`（2 小时），**续签后被救活** |

配套：
- 临期（<15 分钟）自动续签；转发链路里失败只记日志**继续用旧票**，`force=True` 才把上游原文抛给用户；
- **每账号串行锁** —— refresh_token 是一次性轮换票，同号并发续签会互踢；
- 失败分档：`-107/-101/-114` 判登录态真死（标 `refresh_dead`，不再空打），`-109/-7/-5` 判本端问题（避免把好号误标「需要重登」）；
- 新增 `marvis_keepalive` 定时任务，30 分钟一轮（不能用 360 分钟兜底 —— 微信票只活 2 小时）。

## 修复：首次录入账号后误报「API 密钥无效」

现象：新建 Marvis 号录入后第一次调用，客户端提示 API 密钥无效。

根因：上游 401 的含义是「**这个平台账号**的登录态没了」，但 401 在 HTTP 语义里是「调用方没通过身份验证」—— 所有 OpenAI 兼容客户端看到 401 都渲染成「API 密钥无效」，于是用户去翻自己的 Key，而 Key 根本没坏。首次录入后的第一次调用正好撞在这个窗口里。

现在账号侧的 auth 失败映射成 **503**，并在文案里明说「这是 Marvis 平台账号的问题，你的 API Key 是有效的」。真正的 Key 问题在鉴权层就 401 掉了，不会被掩盖。

## 修复：重新授权救不活账号

现象：重新扫码授权成功后，调用仍然 503「账号都在冷却或排队中」。

根因：`import_record` 只清了 `cool_until`，但额度类失败写的是 `degrade_until`（30 分钟）、风控类写 `breaker_until`、判死会打 `refresh_dead`。这三样只要还挂着，选号就继续跳过它 —— 用户刚授权完，一调用还是 503。

这些都是「针对旧凭证」的判断，新凭证到手就该全部作废。现在 `status / cool / degrade / breaker / err_count / refresh_dead` 一起复位，`access_token / refresh_token / guid` 也确认换新。

## 修复：授权码被重复提交报 -107

现象：账号明明导进去了，弹窗却报「授权码已过期或已被使用（-107）」。

根因：授权码一次性，而前端 `focus`（自动读剪贴板）/ `paste` / 点「粘贴并入库」三个入口会各自独立触发提交，同一串 code 能漏过防重进两次 —— 第二次必然 -107。

现前端把 `busy` 与 `lastTried` 一起前置；服务端对同一会话里「同一 code 已换过」直接返回上次结果，并对 -107 增加「90 秒内刚入库过」的抢救分支（判据收紧到 session 开立之后，不拿无关旧号冒充成功）。

顺带修 `[object Object]`：FastAPI 的 `detail` 是对象 `{message, step}`，前端直接拼进字符串，用户只看到一串无意义字符。现在统一抽成可读文本。

## 新增：腾讯 Marvis 接入公开注册入口

之前 `/api/open/platforms` 里没有 Marvis —— 不是「网关没配置」，是 `admin/platform/marvis/` 下根本没有 `public_auth.py` 适配器。

现补上（扫码 → 粘贴回调，与控制台授权登录同源），并新增 `/api/open/{platform}/submit` 路由与 `SubmitIn` 模型。入口仍然走 join gate 总闸：无票访问 `/api/open/*` 返回 404 是设计如此（不对外裸露「入口存在」）。

## 修复：账号表不再恒显示「长期有效」

后端早就返回 `token_expiry`（fresh/soon/expired/unknown），但前端一处都没读 —— 账号表读的是 `credits_soonest_expire_at`（积分包到期），Marvis 没有积分包所以恒为空。

现在按登录方式分档显示：微信号过期标红「已过期 · 需重登」、临期橙色「约 N 分钟后过期」、正常灰色「约 N 天后过期」。文案不写死 2 小时（WX 2 小时 / QC 30 天）。

## 修复：Android 应用名还是 wb2api

桌面端（EXE 标题、托盘、诊断）早就叫 MiraiHub，只有 Android 侧漏了。改了 5 处用户可见位置：桌面图标与应用名、前台服务通知标题、启动诊断文案与导出文件名、splash 品牌大字、账号导出兜底文件名。

## 修复：SSE 保活改用合法 data 帧（感谢 @paul2333 的 PR #40）

原来每 8 秒发 `: ping` 注释帧。SSE 规范里注释行会被解析器丢弃、不产生任何事件，客户端 idle 计时器不会重置 —— 长思考请求仍会被判超时断开。现改为合法 data 帧（空增量 chunk），并保留 `MIRAIHUB_KEEPALIVE_DATA_FRAME=0` 回退开关。

合并后补了 `created` / `model` 两个字段：`ChatCompletionChunk` 里它们是必填，SDK 默认宽松解析能过，但开 `strict_response_validation=True` 会抛 ValidationError，保活帧反而把流打断。

## 修复：Marvis「今日消耗」恒为 0

`usage_logs.model` 记的是上游槽位名（`main-auto`），`model_configs.model_id` 存的是可读名（`deepseek-v4.1-flash`），倍率查不到 → 消耗恒 0。现接入 `MODEL_NAME_MAP` 双向补候选。

同时修了负数 credits 反向充值：倍率 `-1` 表示按 token 计量，`credits_for` 算出负值，而扣余额写的是 `balance_remain - int(credits)` —— 负负得正，每跑一次请求余额反而变多。现按 `abs(credits)` 扣减。

也修了 `credits_for` 的全局兜底会跨平台借用倍率（marvis 借到 workbuddy 的 `0.11`，正负都错），现在兜底只认 `platform` 为空的历史行。

## 下载

| 平台 | 文件 | SHA256 |
|---|---|---|
| Windows x64 | `miraihub-1.1.4-windows-x64.exe` | `2d89c16eb948ccd295c4907b8de09a8626122c8d2f2f9c5a9eca1935dec4fe9b` |
| Android arm64 | `miraihub-1.1.4-android-arm64.apk` | `7ed3bc7ba888e017a6a5bd275da1e51ace0ed15a32464289891fda42b4e215dc` |

APK 签名证书 SHA-256：`0F850D0E5B5DC35A84F1CACDCA284673BB11F94CC0BB646B2435D2375B68C3B9`（与既有版本一致，可直接覆盖升级）

## 已知限制

- Marvis 续签只重置有效期、**不换 access_token**（上游行为），所以别指望它能洗白被风控的号；
- 微信账号的真机续签我没跑到（手上只有 QQ 号）—— 微信 `expires_in` 是字符串 `"7200"` 这点来自客户端实现与第三方记录，代码已按字符串兼容，但**没有我自己的真机回执**。不过 1.1.4 发布前我在微信号上验到了 `code:0` 与 `expires_in:"7200"`，这条限制可以划掉；
- 部分槽位（Claude Sonnet 4.6 / Opus 4.6 / GPT-5.4）在上游按账号等级下发，免费号调用会回 `4130001`。
