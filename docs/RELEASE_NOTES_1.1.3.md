# MiraiHub v1.1.3

这次主要修三件事：**腾讯 Marvis 接入**、**给 API Key 用的模型广场接口**、**Token 制平台的额度显示**；
顺带把豆包网关整块下线（它一直不稳定，已删干净）。

## 新增：腾讯 Marvis 平台

接入腾讯应用宝 AI 助手（Marvis）背后的模型网关，结构与 WPS 灵犀一致（`core/` + `gateway.py` + `platform_gateway.py` + `services/` + `routers/`）。

- 模型：DeepSeek V4.1 Flash / V4 Pro / V4 Flash、hy3、混元视觉，共 5 个槽位
- 额度：**令牌钱包制**（每号每日 1000 万计费 token，缓存命中免费、输入输出五折）
- 登录：QQ / 微信扫码 → 把回调地址粘回控制台即入库（支持剪贴板自动填充、粘贴即导入）
- 协议：`/v1/chat/completions` + `/v1/messages` + `/v1/responses` 三种协议、流式与非流式、function calling 全部打通
- 模型名对外用可读名（`deepseek-v4.1-flash`），槽位名只留在内部给上游用

## 新增：API Key 视角的模型广场

```
GET /v1/model-market?hours=24
GET /v1/models/market?hours=24
Authorization: Bearer sk-xxx
```

只返回**这个 Key 能用**的模型（分组 ∩ 平台 ∩ 绑定账号权益 ∩ 启用），用不了的不出现。字段与控制台模型广场对齐，另补 Key 视角的数字：

- 用量：`requests` / `success_rate` / `tokens` / `credits` / `avg_latency_ms` / `avg_ttfb_ms`
- 容量：`active_accounts` / `exhausted_accounts` / `available_accounts` / `available` / `status` / `health`
- 剩余：`key_used_credits` / `key_quota_remaining`，顶层 `key.quota{limit,used,remaining,unlimited}`

## 修复

### Marvis 登录态（重要）

腾讯 Marvis 的 QQ / 微信登录**不下发 `expires_in`**，`access_token` 实测**只活约 2 小时**
（真机时间线：13:34 授权 → 15:44 上游回 `4100403 登录态过期/未登录`）。所以「授权完能用、
过一阵突然退登」不是偶发故障，是固定到期。

这一版把这件事变成可预判、可解释的：

- 入库时记录凭证签发时间，按 2 小时推 TTL，账号卡上给出 `fresh / soon（剩 10 分钟内）/ expired` 三档提示，
  可以提前重登，不必等一次失败；
- 钱包接口在上游 token 失效时仍返回 `{"code":0,"msg":"success","data":null}` —— 以前被当成
  「业务失败」并显示「钱包接口没有返回 data 字段」，现在正确归为**登录态过期**；
- 401 的错误信息会带上凭证年龄（「该凭证已签发 130 分钟，超过约 2 小时的有效期」）并直接引导重新扫码；
- 选不到号时不再笼统回「请先在控制台导入凭证」，而是区分三种情况：
  **全池过期**（请重新扫码）/ **都在冷却**（稍后重试）/ **真的没号**（请扫码）。

> 过期后重新扫码即可，续签接口需要完整凭证、且实测参数格式未公开，本版不做自动续期。

### Token 制平台的额度显示


Marvis 这类平台一个号池上亿 token，千分位全展开会把等宽列撑开。现在按平台声明的 `quota_unit` 自动切换格式：token 制用 `K / M / B` 缩写（`12.35M`），积分制仍是千分位（`69,475`），精确值放在悬停提示里。列名也跟着变成「总 Token / Token 到期」。

### 模型命名

- 上游槽位名（`main-auto`）不再出现在对外模型 id 里，统一换成真实模型名（`deepseek-v4.1-flash`）
- 可读名 / 上游真实名 / 槽位名 / 旧数字下标**四种写法都能调**
- 聚合视图按可读名收敛，`deepseek-v4.1-flash` 各平台正确归为一组并带平台后缀

### 公开注册入口

- 链接时效新增**永久**选项（关闸即作废，其余语义不变）
- 粘贴回调即自动导入，不再需要手点提交
- 文案区四个字段改成多行文本组件，带字数/行数计数与超限拦截
- 平台胶囊改用控制台统一配色与系统 tooltip

### 豆包网关下线

`admin/platform/doubao/` 整体删除（协议实现不稳定）。`loader.py` / `server.py` / `public_auth.py` 相关装配全部移除；`dispatch` 与 `models_router` 新增「已下线平台」拦截——残留的 `doubao-*` 模型名直接 404 讲清楚，**不会落到默认平台去打真上游**。数据库里 `platform=doubao` 的模型行已清理（有备份）。

## 已知限制

- Marvis 的 `client_probe`（本机扫描）返回空：它是应用宝里的助手，PC 上没有可扫描的凭证目录。
- Marvis 的 `refresh_token` 续签需要完整凭证，过期后请重新扫码或导入刷新后的凭证。
- 部分槽位（Claude Sonnet 4.6 / Opus 4.6 / GPT-5.4）在上游按账号等级下发，免费号调用会回 `4130001`，已在代码里留档，换高等级账号可搬回。
