# 小浣熊（Raccoon）平台接入

MiraiHub 的小浣熊平台模块。上游是 [xiaohuanxiong.com](https://xiaohuanxiong.com)
的 LLM 网关，走标准 OpenAI 协议，凭证是一张 HS256 JWT。

## 快速开始

```powershell
# 1) 装依赖
pip install -r requirements.txt

# 2) 配 .env（后台登录口令必填，源码版不再有默认 admin/admin123）
#    ADMIN_USERNAME=...
#    ADMIN_PASSWORD=...
#    ADMIN_JWT_SECRET=<随机串>

# 3) 启动
python main.py
```

打开 `http://127.0.0.1:8790/admin` → 平台切换条里会多出「小浣熊」。

## 导入账号

小浣熊客户端用 **Electron LevelDB** 存登录态（不是明文 JSON），而且不是
每台机器都装。所以本平台**没有「扫描本机」**——不是没实现，是扫不出来。
`scan()` 会明确返回 `supported: false` 并说明原因，而不是返回空列表让你
以为客户端没登录。

三种凭证都接受：

| 来源 | 形态 | 用法 |
|---|---|---|
| agent2api 导出 | `agent2api-account` JSON（单条或打包） | 控制台「导入凭证」粘贴 |
| 裸 JWT | `eyJhbGciOiJIUzI1NiIs...` | 直接粘贴，一行一个 |
| 原生字段 | `{"token": "...", "uid": "..."}` | 直接粘贴 |

接口：

```http
POST /api/raccoon/import
{"records": [ {agent2api 导出的对象}, ... ]}   # 批量
{"credential": {单条}}                          # 单条
{"text": "<整段粘贴，JSON / JSONL / 裸 JWT 都行>"}  # 省事
```

导入后**自动做两件事**：探一次积分余额（避免「导入成功但根本不能用」），
同步模型目录进 `model_configs`（否则 `/v1/models` 是空的，而调用却是通的）。

## 上游契约

| 用途 | 端点 | 鉴权 |
|---|---|---|
| 对话 | `POST {llm}/chat/completions` | `Authorization: Bearer <JWT>` |
| 模型目录 | `GET {llm}/model_catalog` | 同上 |
| 积分 | `GET {origin}/api/web/points/v1/balance` | 同上 |
| 权益 | `GET {origin}/api/web/auth/v1/entitlement_info` | 同上 |
| 续期 | `POST {origin}/api/web/auth/v1/refresh` | 同上 |

- `{llm}` = `https://xiaohuanxiong.com/api/web/llm/v2`
- `{origin}` = `https://xiaohuanxiong.com`
- JWT：`iss=xiaohuanxiong.com/iam`，`sub=pu-...`（就是账号 id）

环境变量可覆盖（联调指向本地 mock 用）：
`ADMIN_RACCOON_LLM_BASE_URL`、`ADMIN_RACCOON_ORIGIN`。

## 踩过的坑（都是实测）

**① `/model_catalog` 的 `visible=false` 不代表不能用。**
第一版按 `visible` 过滤 `/v1/models`，结果返回空列表 —— 而
`raccoon-090dd3` 这些 `visible=false` 的模型**对话是通的**。
`visible` 只是客户端要不要放进下拉框。现在全部返回，用字段标注可见性。

**② `/v1/models` 走的是聚合目录，不是平台 handler。**
`dispatch.py` 里 `/v1/models` 在**没有显式选择器**时会跨平台聚合，
数据源是 `model_configs` 表。所以平台必须把目录同步进库，否则客户端看到空清单。
`dispatch` 层一行没改，靠导入时调用框架已有的 `_do_sync_models` 解决。

**③ 业务错误藏在 HTTP 200 里。**
上游信封是 `{code, message, data}`，`code != 0` 时 HTTP 可能仍是 200。
`client.unwrap()` 统一拆信封并按 `code` 分类成 auth / quota / rate / model，
这样 admin 的冷却与哨兵逻辑不用为小浣熊写特例。

**④ refreshToken 与 accessToken 同值。**
50 条样本 100% 相同，说明是「同票自续」。续期只能推迟 `exp`，
被吊销的会话救不回来 —— 必须回官方客户端重新登录。返回里已写清楚。

## 已验证

12 项端到端全部通过（源码版，干净库）：

```
raccoon 已注册 / 导入 50 条 / 模型目录同步 9 个 / 账号池 50 条 / 额度写回
创建 Key / /v1/models 9 个 / 对话真实打通 / 流式 SSE / Anthropic /v1/messages
分段健康检查（token/jwt/models/points）
```

## 目录

```
admin/platform/raccoon/
  __init__.py            平台声明 + install()
  gateway.py             /v1/* 转发（chat / responses / messages / models / balance）
  platform_gateway.py    Gateway 契约（账号池 11 项 / 任务 / 探测 / 概览卡片）
  core/endpoints.py      端点表 + env 覆盖
  core/client.py         HTTP 客户端 + 信封拆解 + 错误分类 + 流式句柄
  core/paths.py          客户端路径表（仅用于「装没装」探测）
  services/accounts.py   凭证归一 / 账号池 / 额度 / 目录同步
  services/tasks.py      保活 / 过期清扫
  routers/accounts.py    /api/raccoon/*
```

## 明确的「不支持」

按契约要求，做不到的事返回 `supported: false` 而不是伪造成功：

| 能力 | 状态 | 原因 |
|---|---|---|
| 本机扫描 | ❌ | Electron LevelDB，非明文 |
| 签到 | ❌ | 上游没有签到接口 |
| 写回客户端 | ❌ | 要改 LevelDB，风险高且无必要 |
