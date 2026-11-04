---
name: gateway-platform
description: 多平台网关的架构规范与接入指南。当需要新增一个上游平台、修改现有平台（workbuddy / trae / qoder / loomy / raccoon 小浣熊 / codearts 华为云 CodeArts）、给平台加授权登录（PKCE/扫码）、调整跨平台调度与缓存策略、排查「模型 400 / 积分 0 / 余额刷新失败 / 导入不进来」、或验证网关是否正确跑通时使用。涵盖：职责边界、目录结构、Gateway 契约、协议转换与平台选路、缓存、计费倍率与积分、凭证生命周期（扫描/授权/登录/导入/导出）、同名模型后缀路由、风控哨兵、已修 bug 档案、测试与验收。
---

# 多平台网关规范

## 这个 skill 解决什么问题

`MiraiHub` 是一个**多平台聚合网关**：一个 API Key 可以调用多个上游平台
（WorkBuddy、Trae 国内版、Trae 国际版 …）的模型。每次新增平台，最容易
犯的错误是把平台特有的东西写进 `admin/`，导致：

* 加平台要改 admin → 改 admin 就可能碰坏已有平台；
* 一个平台出事（被封、故障），其它平台跟着不可用；
* 删掉一个平台时，发现它的逻辑散落在十几个文件里。

本 skill 定义**必须遵守的边界**，以及**怎么验证**你守住它。

---

## 文档索引（按「遇到问题」查，不按顺序读）

| 你要做什么 | 读哪份 |
|---|---|
| **搞清楚「主网关是谁」、要不要改 workbuddy 命名** | 本文 §2.5 — admin 就是网关，别为命名动热路径 |
| **偶发 400「模型 xxx-平台 不存在」/ 协议转换 / `/v1/models` 字段 / 缓存** | [PROTOCOL-ROUTING-CACHE.md](PROTOCOL-ROUTING-CACHE.md) — 预读 body、后缀还原、能力字段、四处缓存 |
| **账号进不来 / 导出的导不回来 / 余额刷新失败 / 扫码后不能调** | [CREDENTIAL-LIFECYCLE.md](CREDENTIAL-LIFECYCLE.md) — 扫描·授权·登录·导入·导出·刷新 |
| **客户端能调、网关调不通** | [ENDPOINT-FIRST.md](ENDPOINT-FIRST.md) — 4023 是端点错，不是模型错 |
| **平台请求体不兼容 / 400 11128 / 200 流无 finish_reason** | [REQUEST-BODY-CONTRACT.md](REQUEST-BODY-CONTRACT.md) — 平台私有契约 · 两条入口链路同步 · 两个实测案例 |
| **给平台加授权登录** | [PKCE-AUTH.md](PKCE-AUTH.md) — 通用 PKCE 骨架 + Trae/Qoder 实现 |
| **改过相关代码要验证** | [FIXED-BUGS.md](FIXED-BUGS.md) — 已修 bug 档案 + 验证清单 |
| 读不出本机凭证 | [DEVICE-AUTH.md](DEVICE-AUTH.md) — Qoder 详细版、refresh 串格式 |
| **接华为云系产品**（AK/SK 签名 / OSCrypt 凭证 / DPoP 续期） | [CASE-CODEARTS.md](CASE-CODEARTS.md) |
| 追凭证存哪 | [NO-CREDENTIAL.md](NO-CREDENTIAL.md) — 追凭证四跳 |
| 新平台接入 | [ONBOARDING.md](ONBOARDING.md) |
| 任务/签到/保活 | [ACTIONS-AND-TASKS.md](ACTIONS-AND-TASKS.md) |
| 计费/倍率/积分 | [BILLING.md](BILLING.md) — 六种上游形态 + 记账公式 + 余额读不到≠0 |
| 能力矩阵（档位/上下文） | [CAPABILITY-MATRIX.md](CAPABILITY-MATRIX.md) |
| **并发上不去 / TTFT 越压越高 / TPS 缺失** | [CAPACITY-PLANNING.md](CAPACITY-PLANNING.md) — 三层闸门的实测默认值 |

> **⚠️ 改动任何代码前，先扫一遍 [FIXED-BUGS.md](FIXED-BUGS.md)。**
> 那份文档记录了 **70 条真实 bug**（其中 52 条是「五·补」系列：
> 五·补25~36 是接入小浣熊时新增，五·补37~42 是同日「卡死 / 402 / 列表慢 /
> 注入不生效」五连修，五·补43~44 是接入 CodeArts 时补的
> 「`record()` 提前 return 漏 running」「`utcnow().timestamp()` 时区错」，
> 五·补45 是「各平台扫描目录写死盘符 / 目录名」，五·补46~48 是
> 「会话窗口满被报成没有号 / 会话 id 每请求随机 / 思考档位不记账还被抹平」，
> 五·补49~50 是「TTFT/TPS 记的不是上游真实值」「默认 60 转/分把并发锁死」，
> 五·补51~52 是「CodeArts developer 角色 / WorkBuddy 11128 指纹两条链路不一致」）。
> 每条都写清「现象 / 根因 / 修法 / 怎么验证」。
> 高频踩坑的几个：
>
> | 改动区 | 先看 |
> |---|---|
> | 调度 / 禁用 / 模型配置 / 前端扫描 | 一~四章 |
> | **预读 body / 平台后缀 / 同名模型** | 五·补25、五·补26 |
> | **倍率 / 记账 / 积分显示** | 五·补27~29 |
> | **余额刷新 / 扫码授权 / 导入导出** | 五·补30~33 |
> | **聚合视图 / 模型能力字段** | 五·补34、五·补35 |
> | **Key 绑定平台 / 分组 / 多账号** | 五·补11（`trae` 存储 id ≠ 对外 id → 503）|
> | **流式 / TPS / TTFT** | 五·补8、五·补12、五·补13（伪流式、口径错、`ttfb==latency`）|
> | **实时面板 / 用量日志** | 五·补14（running 残留、旧快照）、五·补37（一请求两条记录）|
> | **一直 402 / 选到空号** | 五·补38（402 不可重试 + 零余额号排第一）|
> | **列表接口慢（秒级）** | 五·补39（请求线程里同步探活）+ [INTEGRATION §四](INTEGRATION-CHECKLIST.md) |
> | **注入客户端不生效** | 五·补40（客户端内存态覆盖 `.info`）|
> | **前端无限刷接口** | 五·补42（轮询无上限 + 顺带拉模型页）|
> | **面板总有 `/v1/models` 在跑** | 五·补43（`record()` 提前 return 漏清理）|
> | **临时凭证反复续期 / 不续期** | 五·补44（`utcnow().timestamp()` 时区错）|
> | **换盘/改文件夹就扫不到客户端** | 五·补45（路径写死盘符/目录名，见 `install_paths`）|
> | **并发时莫名「没有可用账号」** | 五·补46（在途/会话窗口满 ≠ 没号，排队而非 503）|
> | **上游缓存不命中 / 一个号一堆会话** | 五·补47（会话 id 每请求随机，见 `pool.stable_session_id`）|
> | **档位设了没反应 / 日志显示未记录** | 五·补48（不记账 + Loomy 抹平 + Qoder 丢弃）|
> | **TPS 一直是「暂无样本」/ 解码段只有几百毫秒** | 五·补49（TTFT 只认正文，思考链不算）|
> | **并发上不去但 TPS 正常** | 五·补50（60 转/分匀速放行，见 [CAPACITY](CAPACITY-PLANNING.md)）|
> | **400/11128、200 流无 finish_reason、绿勾 0 token** | 五·补51、五·补52 + [REQUEST-BODY-CONTRACT.md](REQUEST-BODY-CONTRACT.md) |
> | **同步 / 备份 / 导出** | 五·补10（漏 `platform` → 线上号池废掉）|
>
> 每改完一处，照 §七 的**验证清单**跑一遍（7.1~7.4 按改动类型分好了）。
> 热路径相关改动再加跑 `scripts/check_hotpath.py`（见 §12）。

> **⚠️ 别把 `copilot.tencent.com` 当成「主网关」。**
> `admin/` 才是网关本体（`/v1/*` 由 `routers/proxy.py` 实现），
> `workbuddy` 只是内置默认平台，它的上游恰好是那个域名。
> 详见 [§2.5 架构真相](#25-架构真相谁是主网关) —— 那一节还写了
> **为什么不该为「命名统一」去动 `proxy.py`**。

> **⚠️ 排查「客户端能调、网关调不通」先读 [ENDPOINT-FIRST.md](ENDPOINT-FIRST.md)。**
> 那份文档记录了一次真实的踩坑：某个模型在官方客户端正常，网关却一直报
> 「模型不存在」，我抱着错误的端点试了 20 多种参数组合全失败 ——
> **根因是客户端根本不用那个端点**。先定端点，再谈参数。

---

## 1. 一句话原则

> **admin 只管渠道；平台自己的账号、权益、任务、转发全部自己管。**

判断一个文件该放哪，问三个问题：

| 提问 | 是 → | 否 → |
|---|---|---|
| 它认识具体平台吗？（代码里出现 `trae` / `workbuddy`） | 平台模块 | 框架层 |
| 新增平台时需要改它吗？ | 平台模块 | 框架层 |
| 删掉某个平台后它还成立吗？ | 框架层 | 平台模块 |

> 请求体规则同理：**role 白名单 / tools schema / 指纹词表 / 错误帧格式都是平台私有契约**，
> 见 [REQUEST-BODY-CONTRACT.md](REQUEST-BODY-CONTRACT.md)。别把某个上游的净化规则
> 升级成框架层的「通用能力」。

---

## 2. 目录结构（唯一权威）

```
admin/platform/
  # ---- 框架层（所有平台共用，不含任何平台名）----
  __init__.py
  gateway.py            契约定义（Protocol）
  registry.py           PlatformSpec 注册表 + 选择器解析 + 懒加载
  loader.py             装配列表 BUILTIN
  dispatch.py           /v1/* 分叉中间件（唯一的平台判断点）
  scope.py              平台作用域（统计口径收窄）
  gateway_router.py     跨平台调度策略 + 健康度 + 缓存
  router.py             /api/platforms
  gateway_api.py        /api/gateway/*
  sentinel_router.py    /api/sentinel/*
  overview_router.py    /api/overview
  alerts_router.py      /api/alerts/*

  # ---- 平台模块（各自独立，可整目录删除）----
  trae/
    __init__.py           登记 trae-cn / trae-intl 两个 PlatformSpec
    gateway.py            转发实现（dispatch 调用它）
    platform_gateway.py   Gateway 契约实现
    core/                 纯协议层（client / fingerprint / catalog / protocol）
    services/             状态层（accounts / pool / desktop_catalog）
    routers/              /api/trae/*
    data/                 目录快照（仅兜底）
  workbuddy/
    __init__.py           PlatformSpec + Gateway + AccountOps + ClientProbe
    core/                 实现层（backend / wb_login / credential_codec …）
  raccoon/                小浣熊（扫码 / 明文本机凭证 / 双字段倍率）
  codearts/               华为云 CodeArts（AK/SK 签名 / STS 临时凭证 / DPoP 续期）
    __init__.py           PlatformSpec
    core/signer.py        华为云签名（唯一实现，别在业务里手写）
    core/client.py        对话 / 模型 / 额度 / 身份
    core/session.py       OSCrypt 解密本机登录态 + DPoP 续期
    services/             accounts / scan
    gateway.py            /v1/*（上游本身就是 OpenAI 协议，不做字段翻译）
    __init__.py           PlatformSpec（actions 声明见 ACTIONS-AND-TASKS.md）
    gateway.py            转发 + /v1/models（能力字段在这里）
    platform_gateway.py   AccountOps（scan / import / export / refresh）
    core/                 client（request_json / fetch_models / fetch_points）
    services/             accounts / scan / webauth（扫码，入库前必须验票）
    routers/              /api/raccoon/*
```

> 平台数量：`workbuddy` · `trae-cn` · `trae-intl` · `qoder` · `loomy` · `raccoon` ·
> `codearts`（华为云，AK/SK 签名 —— 见 [CASE-CODEARTS.md](CASE-CODEARTS.md)）。
> 新增平台只改两处（新建目录 + `loader.BUILTIN` 一行），其余自动生效 ——
> 如果不能做到，说明有平台相关的判断漏在了框架层。

### ⚠️ 最容易犯的错

`registry.py` / `dispatch.py` / `scope.py` / `router.py` 这些名字看起来
「通用」，**必须留在 `admin/platform/` 根目录**，不能放进平台子目录。
它们是所有平台共用的机制 —— 放进子目录会让「删掉该平台」变成
「删掉整个注册机制」。

---

## 2.5 架构真相：谁是「主网关」？

这一节专门用来消除一个**反复出现的误解**：「我们是不是还挂在
`copilot.tencent.com` 上？主网关是不是 WorkBuddy？」

### 结论先说

> **`admin/` 就是网关本体。`workbuddy` 只是 6 个平台之一，
> 只不过它是 `DEFAULT_PLATFORM`（内置默认平台），
> 所以它的上游适配直接长在主网关里，没有自己的 `gateway.py`。**

真实的三层关系：

```
admin/                              ← 网关本体（我们自己的 FastAPI 服务）
├── routers/proxy.py               ← /v1/{chat,responses,messages} 的真正实现
│      负责：Key 鉴权 · 配额 · 选号 · 重试 · 限流 · 记账 · 三协议转换
│      └── 3 处 settings.BACKEND + "/v2/chat/completions"
│            = copilot.tencent.com  ← 这是 **workbuddy 平台的上游**，
│                                      不是「主网关的归属」
├── anthropic_adapter.py           ← Anthropic ↔ Chat 的字段转换（见 PROTOCOL §4.1.1）
└── platform/
    ├── workbuddy/      只有 core/    ← **没有 gateway.py**，上游适配在 proxy.py 里
    ├── trae/           gateway.py + platform_gateway.py
    ├── qoder/          gateway.py + platform_gateway.py
    ├── loomy/          gateway.py + platform_gateway.py
    └── raccoon/        gateway.py + platform_gateway.py（扫码 / 明文本机凭证）
```

| 疑问 | 事实 |
|---|---|
| 主网关挂在 WorkBuddy 上？ | **不是。** `admin/` 就是网关，`/v1/*` 由 `proxy.py` 处理 |
| 我们自己有没有内置代理？ | **有**，就是 `admin` 本身，不需要外部中间件 |
| `copilot.tencent.com` 是什么？ | **workbuddy 这个平台的上游地址**（`ADMIN_BACKEND` 默认值） |
| 为什么只有 workbuddy 没 `gateway.py`？ | 它是第一个平台，是 `DEFAULT_PLATFORM`，上游调用直接写在 `proxy.py` |
| 平台判断到底在哪一行？ | `dispatch.py` 的中间件 —— 全站唯一的平台分叉点 |

### ⚠️ 不要为了「看着统一」去改名或拆分

曾经评估过把 `workbuddy` 改成中性名（`builtin` / `gateway`），或者把它的
上游适配抽成 `admin/platform/workbuddy/gateway.py`。**结论是不做**，理由：

| 改动项 | 代价 |
|---|---|
| `DEFAULT_PLATFORM` 代码引用 | **65 处**，分布在 14 个文件 |
| `accounts.platform='workbuddy'` | 3 行（要写数据迁移） |
| `model_configs.platform='workbuddy'` | **41 行**（要写数据迁移） |
| 模型名后缀 `-workbuddy` | 全部失效，客户端要重配 |
| **收益** | **零** —— 功能、性能、稳定性都不会变，只是命名好看 |

更关键的是风险：`proxy.py` 是**全站每一条请求都要走的**热路径，4527 行。
为了纯命名动它，任何一处疏漏都是线上故障。**风险高、收益零，不做。**

### ✅ 什么时候才该动

不是「现在想改」，而是等下面**任一条件**成立，顺路做：

1. **要接第 7 个平台**，且它也需要写「默认平台」那套逻辑时
   → 顺手把 workbuddy 抽成 `gateway.py`，目录自然对齐；
2. **`proxy.py` 本来就要大改**（例如新增一种协议）时
   → 拆分被分摊进已有改动里，风险不额外增加。

单独为「命名统一」动一次，是纯亏。**这个判断要写进 issue 而不是待办。**

### 排查相关问题的正确姿势

| 现象 | 别想错 | 去看 |
|---|---|---|
| 调用报 `copilot.tencent.com` 相关错误 | 不是「主网关坏了」，是 **workbuddy 上游**的问题 | workbuddy 账号状态、`ADMIN_BACKEND` 配置 |
| 想确认这次落在哪个平台 | 看响应头 `x-gateway-platform` | `dispatch.py` 的 `_set_state` |
| 想确认用的是哪个上游域名 | 查 `settings.BACKEND` | `.env` 的 `ADMIN_BACKEND` |

---

## 3. Gateway 契约（必须实现）

位置：`admin/platform/gateway.py`。用 `typing.Protocol`（结构化类型），
平台模块**不需要 import 它就能满足契约**，只在测试里做一次 `isinstance`。

### 3.1 Gateway —— 网关主体

```python
def chat_completions(request, body)   # /v1/chat/completions
def responses(request, body)          # /v1/responses
def messages(request, body)           # /v1/messages (Anthropic)
def count_tokens(request, body)       # /v1/messages/count_tokens
def list_models(request)              # /v1/models（**只返回能调的模型**）
def balance(request)                  # /v1/balance
def account_ops() -> AccountOps
def task_runners() -> dict[str, Callable]
def client_probe() -> ClientProbe
def overview_cards(db) -> list[dict]
```

### 3.2 AccountOps —— 账号池 11 项操作

```python
def list(db)                                  # 列表（只读）
def scan()                                    # 扫描本机（不含 token）
def import_local(db)                          # 导入本机
def import_record(db, record)                 # 导入单条
def refresh(db, account_id, *, force=False)   # 刷新凭证与额度
def keepalive(db)                             # 批量保活
def checkin(db)                               # 签到（不支持时返回 supported=False）
def credits(db)                               # 额度/积分总览
def usage(db, **filters)                      # 用量
def health(db, account_id)                    # 连通性检查（分段返回）
def inject(db, account_id)                    # 注入客户端
def export(db, ids=None)                      # 导出
```

**「不支持」的正确表达**：返回 `{"supported": False, "message": "..."}`，
而不是缺方法或抛异常。缺方法无法和「忘了实现」区分开。

例：Trae 国际版没有签到 →

```python
def checkin(self, db):
    if self.realm == "intl":
        return {"supported": False, "summary": "Trae 国际版没有签到接口"}
    return run_trae_checkin(db, None)
```

### 3.3 ClientProbe —— 客户端探测

```python
def dirs() -> list[str]            # 客户端目录（存在的才返回）
def probe() -> dict                # 版本 / 安装路径 / 是否运行
def schema() -> list[dict]         # 可修改项声明（key/label/type/default）
def read(key) -> Any               # 读当前值
def write(key, value) -> dict      # 写（必须先备份，返回备份标识）
```

admin 提供**通用界面**，平台只回答「我的客户端在哪、有什么可改」。
这样「客户端参数」页面切换平台时字段自动变化，不必为新平台写前端。

**没有可改项时返回空列表**，让界面显示「该平台没有可修改项」，
不要给一堆假开关。

### 3.4 overview_cards —— 概览卡片

返回一个**卡片规格列表**，前端只负责渲染：

```python
[
  {"key": "accounts", "label": "账号总数", "value": 2,
   "sub": "启用 2 · 已禁用 0", "icon": "fa-users", "tone": ""},
  {"key": "credits", "label": "可用积分", "value": "9,572.00",
   "sub": "总额度 11,540.00", "icon": "fa-wallet", "tone": "brand"},
]
```

`tone` 取值：`""` / `brand` / `warn` / `danger`（映射到 `.stat.is-*`）。

**平台自己决定显示什么**。改造前前端有 `if (isTrae) {...} else {...}`
两套分支，新增平台就得再加一个分支 —— 现在前端零改动。

---

## 4. 新增平台：三步

### 步骤 1：建目录并实现 Gateway

```
admin/platform/<name>/
  __init__.py           # PlatformSpec + install()
  gateway.py            # 转发实现（如需处理 /v1/*）
  platform_gateway.py   # Gateway 契约实现（可选，可并入 __init__）
  core/                 # 上游协议
  services/             # 状态
```

`__init__.py` 必须有 `install()`，且**幂等**：

```python
def install() -> None:
    from admin.platform import registry as _registry
    if _registry.get(PLATFORM) is not None:
        return                     # 幂等：重复调用要安静返回
    _registry.register(PlatformSpec(
        id=PLATFORM,
        label="...",
        gateway="admin.platform.<name>.gateway",              # 转发模块（可选）
        platform_gateway="admin.platform.<name>:GatewayImpl", # 管理契约
        ...
    ))

install()   # 模块 import 时自动登记
```

### 步骤 2：加进装配列表

`admin/platform/loader.py`：

```python
BUILTIN = (
    "admin.platform.workbuddy",
    "admin.platform.trae",
    "admin.platform.<name>",     # ← 加这一行
)
```

### 步骤 3：加图标（可选）

`admin/console/platform-icons.js` 的 `ICONS` 加一项。没有就用中性占位。

**其余代码一行不用改** —— 卡片、切换条、账号池、任务、预警全部自动生效。

---

## 5. PlatformSpec 关键字段

| 字段 | 说明 |
|---|---|
| `id` | 对外平台 id（`trae-cn`） |
| `label` | 展示名 |
| `gateway` | **转发**入口（module，提供 `dispatch(request, path)`）。默认平台留空 |
| `platform_gateway` | **管理**契约入口（class 或 instance） |
| `storage_platform` | 落库到 `accounts.platform` 的值 |
| `realm` | 固定区域（单区平台填，多区平台留空） |
| `realms` | 可选区域列表（多区平台填） |
| `icon` | 图标 key |

### 为什么 `gateway` 和 `platform_gateway` 分开

* `gateway` 是**热路径**：`dispatch.py` 要求它是 module（取 `dispatch` / `PATHS`）；
* `platform_gateway` 是**冷路径**：管理能力（账号池 / 任务 / 探测 / 卡片）。

混成一个字段会让热路径 import 一堆管理模块。

### 一个模块多个平台

Trae 国内版/国际版是**两个平台**但共用实现：

```python
register(PlatformSpec(id="trae-cn",   storage_platform="trae", realm="cn",   ...))
register(PlatformSpec(id="trae-intl", storage_platform="trae", realm="intl", ...))
```

存储上共用 `platform='trae'` + `domain='cn'|'intl'`，避免为「换区」复制
一份号池代码；对外是两个独立平台，界面上彻底分开。

---

## 6. 跨平台调度（gateway_router）

位置：`admin/platform/gateway_router.py`。

### 六种策略

| id | 标签 | 排序依据 |
|---|---|---|
| `priority` | 优先级 | 手动数字，小的先 |
| `balance` | 额度优先 | 剩余**比例**降序（不是绝对额度） |
| `expiring` | 到期优先 | 最近到期的时间升序 |
| `throughput` | 吞吐优先 | 可用账号数降序 |
| `round_robin` | 轮转 | 按分钟取模，确定性轮转 |
| `random` | 随机 | shuffle |

### 关键设计

**balance 用比例不用绝对额度**：否则额度大的平台永远压着小的，
「额度优先」会退化成「永远用同一个平台」。

**熔断/禁用按平台隔离**：

```
workbuddy 熔断 → 不影响 trae
禁用 trae-cn  → 它退出调度，但账号数据保留
```

### 热路径缓存

`dispatch._strategy_pick()` 在**每个 `/v1` 请求**上调用，不能每次查库：

```python
cached_pick()   # TTL 5s + 配置变更主动 invalidate + 代次校验
```

实测：2000 次调用 2.01ms（0.001ms/次）。

**配置变更必须立即生效**：`save_routing()` / `set_enabled()` 内部调
`invalidate()`。忘了这一步，用户点保存后要等 5 秒才看到变化。

---

## 7. 风控哨兵（跨平台聚合）

位置：`admin/sentinel.py`。

### 职责划分

```
哨兵（admin）  ←── 上报 ──  各平台网关
  · 存储（Redis）
  · 去重
  · 熔断判定
  · 告警
```

**「哪个业务码代表封控」是平台知识**，由平台决定是否调用 `note_failure()`。
哨兵只做聚合，不判断码的语义。

### 存储

| 数据 | 位置 | 原因 |
|---|---|---|
| 信号明细 | Redis ZSET + TTL 900s | 高频、短命 |
| 暂停状态 | `system_settings` | 跨重启保留 |

Redis 不可用时**降级到进程内存**（重启即丢，但不会因此拒服务）。

### 熔断判据

按**不同账号数**，不是事件数：

```
1 个账号重试 5 次 → 5 条事件，但只有 1 个账号 → 不熔断
3 个不同账号被封 → 熔断
```

### 平台隔离（用户核心要求）

```
wb:sentinel:<platform>:events     ← 按平台分键
```

A 平台熔断返回 503，**B 平台请求成功率 100% 不受影响**。

---

## 8. 平台作用域（scope）

三态语义，写错会导致「切了没效果」或「看到别人的数据」：

```python
None  # 没指定平台 → 不过滤（老脚本 / curl 行为不变）
[]    # 指定了平台但没有账号 → 结果必须为空，不能退化成全量
[...] # 正常收窄
```

所有统计接口（`/api/stats/usage`、`/api/logs`、`/api/credits/expiry`、
`/api/models/configs`）都必须用 `scope.apply()` 或 `filter_accounts()`。

> ⚠️ 统计缓存键**必须带平台维度**。漏了会表现成「切到 A 平台仍显示
> B 平台的总数」—— 接口和 SQL 都对，只是命中旧缓存。

---

## 9. 验证与测试

> 三类验证**不能互相替代**：
>
> | 类型 | 回答的问题 | 工具 |
> |---|---|---|
> | 契约 | 平台"长得像不像"一个平台 | `test_gateway_contract.py` / `verify_platform.py` |
> | 行为 | 请求**有没有走对路、记对账** | `check_hotpath.py` |
> | 端到端 | 用户路径能不能走完 | HTTP / Playwright（§9.3、§9.4） |
>
> 契约全绿但路由/计费错了，是这批 bug 的常见形态（§12）。

### 9.1 契约测试（必须全过）

```bash
python tests/test_gateway_contract.py
```

它检查：

* 每个平台能加载 `platform_gateway`
* `isinstance(gw, Gateway)` / `AccountOps` / `ClientProbe`
* 11 项账号操作**全部可调用**
* `task_runners()` 返回 dict 且全部可调用
* `client_probe` 五项方法齐全
* **admin 里没有新增平台专有名词**（存量冻结在
  `tests/gateway_contract_baseline.txt`，只允许减少不允许增加）
* `run_task()` 不依赖硬编码任务名

### 9.2 平台自测

```bash
python tests/test_trae_platform.py        # 172 项
```

### 9.2.1 单个平台验收（接完平台 / 改完平台）

```bash
# 结构（不需要服务）：登记 / 三个 Protocol / 11 项操作 / 任务 / 探测 / 卡片 / 幂等
python admin/platform/skill/scripts/verify_platform.py --platform raccoon

# 含运行检查（需要服务 + 管理员账号）：平台在列表 / 卡片有值 / 账号不跨平台重复
python admin/platform/skill/scripts/verify_platform.py --platform raccoon \
    --base http://127.0.0.1:8790 --user xiaofan --password 123456
```

实测输出（小浣熊）：`PASS=19 FAIL=0`，账号隔离逐平台列出。

### 9.2.2 热路径不变量（改了 dispatch / 模型目录 / 计费 / 凭证）

```bash
python admin/platform/skill/scripts/check_hotpath.py
```

29 项断言，覆盖：预读判定、大 body 取模型名、后缀解析、六种倍率、
DSH 能力字段与白名单、导出信封、脱敏 uid、积分空结构、平台内 id 还原、
动作声明 ↔ 前端对齐。**任何一项红都别上线** —— 这些错不会报错，
只会静默走错平台 / 记错账。

### 9.3 端到端检查清单

**A. 平台装配**

```python
from admin.platform.loader import load_platforms
from admin.platform import registry

status = load_platforms()
assert not status["failed"], status["failed"]        # 装配无失败
for pid in registry.ids():
    spec = registry.get(pid)
    gw = registry.load_platform_gateway(spec, spec.realm)
    assert gw is not None, pid                        # 每个平台都能加载
```

**B. 账号隔离**

```python
# 各平台账号数之和必须等于全量，且互不重叠
counts = {pf: len(call("/api/accounts", pf)["items"])
          for pf in (None, "workbuddy", "trae-cn", "trae-intl")}
assert counts["workbuddy"] + counts["trae-cn"] + counts["trae-intl"] == counts[None]
```

**C. 概览卡片动态**

```python
for pid in registry.ids():
    cards = call(f"/api/platforms/{pid}/overview")["cards"]
    assert cards, pid
    assert all("key" in c and "label" in c and "value" in c for c in cards)
```

**D. 调度策略语义**（不能只看顺序，要验证语义）

```python
# balance 必须按**比例**降序
ratios = [(i["platform"], i["balance_ratio"]) for i in gr.order(db)]
assert all(ratios[i][1] >= ratios[i+1][1] for i in range(len(ratios)-1))

# round_robin 必须随时间变化
firsts = {simulate_minute(m) for m in range(3)}
assert len(firsts) >= 2

# random 多次调用必须变化
assert len({gr.order(db)[0]["platform"] for _ in range(30)}) >= 2
```

**E. 禁用隔离**

```python
gr.set_enabled(db, "trae-cn", False)
assert all(i["platform"] != "trae-cn" for i in gr.order(db))
gr.set_enabled(db, "trae-cn", True)
```

**F. 哨兵多平台**

```python
from admin import sentinel
sentinel.reset()
sentinel.note_failure("workbuddy", 1, "waf", code="403", status=403)
sentinel.note_failure("trae", 158, "risk_control", code="4015", status=403)
assert sentinel.status("workbuddy")["total_events"] == 1
assert sentinel.status("trae")["total_events"] == 1        # 不串
```

**G. 美元/积分单位**（这类错误最难发现）

Trae 国际版的坑：`basic_usage_amount` 是 **milli 美元**，
`basic_usage_limit` 是**美元** —— 两个字段单位不同：

```python
detail = quota_detail({"user_entitlement_pack_list": [{
    "usage": {"basic_usage_amount": 0.00066},          # milli
    "entitlement_base_info": {"quota": {"basic_usage_limit": 1}}}]})  # 美元
assert abs(detail["usd_used"] - 0.66) < 0.01           # ×1000
```

> 验证方法：用上游网页端显示的金额做**交叉验证**。
> 0.00066 × 1000 = $0.66 ≈ 网页的 $0.65（差值是一次新请求）。

### 9.4 浏览器验证

```python
with sync_playwright() as p:
    page.goto(BASE + "/admin?tab=gateway")
    # 平台卡片数 == registry.ids() 数量（不是前端写死的）
    assert page.eval_on_selector_all(".gw-platform-row", "els => els.length") == 3
    # 日志表必须有平台列
    headers = page.eval_on_selector_all("#panel-logs thead th", "els => els.map(e => e.textContent)")
    assert "平台" in headers
    # 控制台零错误
    assert not errors
```

---

## 10. 常见错误清单

| 症状 | 根因 | 修法 |
|---|---|---|
| 切了平台但统计没变 | 缓存键漏了平台维度 | 缓存键加 `platform` + `realm` |
| 切了平台但底部统计是旧的 | 前端没重新拉 | 平台变更事件里 `loadXxx()` |
| 筛选器显示 0 但有数据 | 该平台路径没更新计数变量 | 两条路径都要更新 |
| 账号池显示 5 个（应 2 个） | 请求没带 `X-Platform` | `api()` 兜底读 localStorage |
| 按钮点了没反应 | 判断用了旧平台名（`=== "trae"`） | 用 `spec.storage_platform` |
| 模型列表有调不通的 | `/v1/models` 没过滤会员模型 | 按 `free` 字段过滤 |
| 已用金额永远 0 | 单位换算错误 | 交叉验证上游网页显示值 |
| 配置保存后不生效 | 缓存没失效 | 变更时调 `invalidate()` |
| 熔断影响所有平台 | 哨兵 key 没带平台 | `wb:sentinel:<platform>:events` |
| 重复登记「平台已存在」 | `install()` 不幂等 | 先 `get()` 判断再 `register()` |
| **偶发 400「模型 xxx-平台 不存在」** | 预读 body 条件太窄（chunked / 大 body） | 写请求一律预读 + 回放 |
| **某平台模型全部 400** | 只剥后缀、没还原上游 id | `upstream_of_in_platform()` |
| **积分显示 0** | 查倍率没限定平台 / slug 查不到 | `_billing_candidates` + 平台过滤 |
| **余额被清零** | 积分接口空结构被取 0 | 空结构抛错，保留快照 |
| **刷新余额报「凭证缺少 auth」** | 前端写死默认平台接口 | 按 `storage_platform` 拼前缀 |
| **扫码后账号全挂** | 未验证的票覆盖了可用凭证 | 入库前先打业务接口 |
| **导出的文件导不回来** | 导出信封的 `item` 没拆 | 前后端都拆壳 |
| **扫描勾选后 0 导入** | 拿脱敏 uid 做全等比较 | 用 `uid_full` + 前缀兜底 |
| **同步成功但列表为空** | 聚合视图过滤掉单平台组 | 聚合渲染全部组 |
| **模型能力/上下文全空** | `/v1/models` 缺 `contextWindow` 等 | 按 DSH schema 补字段 |
| **WorkBuddy 稳定 4~6% 400/11128** | `/v1/*` 只插零宽空格，没整行删 billing header | 五·补52 / [REQUEST-BODY-CONTRACT](REQUEST-BODY-CONTRACT.md) |
| **CodeArts 200 流无 finish_reason、客户端无限重试** | `developer` 角色 + 上游 `error_code` 错误帧没被识别 | 五·补51 / 同上 |

---

## 11. 验收标准

改完后必须满足：

1. **加第三个平台只改两处**：新建目录 + `loader.BUILTIN` 加一行
2. **删掉一个平台模块**，其它平台测试全过、运行不受影响
3. **某平台熔断**，其它平台请求成功率不受影响
4. **`admin/` 里搜不到平台专有名词**（除注册表与装配列表）
   —— 用 `test_gateway_contract.py` 的 `test_admin_no_platform_terms` 自动检查
5. 每个平台能独立回答「我的 11 项账号操作」

第 4 条是最硬的指标。存量已冻结，只允许减少。

---

## 12. 稳定性铁律（热路径十一条）

这十一条是**踩过坑之后总结出来的不变量**。它们的共同点：违反了不会报错，
只会让请求静默走到错误的平台 / 错误的计价 / 错误的状态。

| # | 铁律 | 违反后的现象 | 细节 |
|---|---|---|---|
| 1 | **写请求一律预读 body**（含 chunked），读完必须回放 | 偶发 400「模型 xxx-平台 不存在」 | [PROTOCOL §2](PROTOCOL-ROUTING-CACHE.md) |
| 2 | **平台确定后必须按平台还原上游 model_id** | 同名模型某平台全 400 | 五·补26 |
| 3 | **缓存键必须带 `platform` + `group_id`** | 切平台没效果 / 分组越权 | 本文 §8 |
| 4 | **上游给 `credits=0` 也要按倍率估算**，且查倍率必须先限定平台 | 积分永远 0 / 记到别家的价 | 五·补28 |
| 5 | **余额空结构必须抛错**，不能取 0 | 真实余额被清零，账号还显示 active | 五·补29 |
| 6 | **凭证写库前先证明它能用**（扫码更要） | 好票被坏票覆盖，号突然全挂 | 五·补31 |
| 7 | **「不支持」要返回 `supported:false` + 原因**，不能返回空 | 用户以为客户端没登录 | 五·补36 |
| 8 | **展示数据（余额/模型数）不许在请求线程里同步拉上游** | 列表接口 2.8 秒，几千个号几十秒 | 五·补39 / [INTEGRATION §四](INTEGRATION-CHECKLIST.md) |
| 9 | **扫描路径不许写死盘符 / 目录名 / `C:\Users`** | 用户换盘或改父文件夹后整平台扫不到客户端 | 五·补45 / `install_paths.py` |
| 10 | **上游会话 id 必须由客户端真实 id 稳定派生**（`pool.stable_session_id`），禁用 `uuid4()` | 缓存命中永远 0；一个号几分钟冒出几十个会话（风控） | 五·补47 / [PROTOCOL §4.7](PROTOCOL-ROUTING-CACHE.md) |
| 11 | **请求体净化规则是平台私有的，且 `/gw` 与 `/v1/*` 两条链路必须同步**（先整行删指纹，再零宽脱敏） | 400/11128 稳定 4~6%；或 200 流无 `finish_reason` 无限重试、日志误记成功 | 五·补51、五·补52 / [REQUEST-BODY-CONTRACT.md](REQUEST-BODY-CONTRACT.md) |

### 这些规则为什么值得单独成节

每条都对应一次「看起来像缓存 / 像白名单 / 像平台抽风」的故障，实际全是
**我们自己的静默降级**。判断标准很简单：

> **任何「为了性能 / 兼容而跳过某一步」的代码，问一句：跳过之后
> 下游会得到什么？如果答案是「一个看起来合法但错误的默认值」，就是 bug。**

### 自动化守卫

```bash
python admin/platform/skill/scripts/check_hotpath.py
```

覆盖：预读判定（300B / 600KB / chunked / >32MB）、模型名提取、
平台内 id 还原、六种倍率形态解析、能力字段（DSH schema）、导出信封拆解、
脱敏 uid 匹配、积分空结构抛错。

**另外两条不变量不靠这个脚本，靠人工量**（都在 §7.6 的清单里）：
列表接口必须 <200ms；一条请求只能产生一条「进行中」记录。

退出码 `0` 全绿；`1` 有不变量被破坏；`2` 只是环境缺依赖（不算失败）。

---

## 13. 快速命令

```bash
# 契约完整性（新增平台后必跑）
python tests/test_gateway_contract.py

# 平台自测
python tests/test_trae_platform.py

# 全量回归
python tests/test_pool.py
python tests/test_credits.py
python tests/test_console_boot.py
python tests/test_hardening.py

# ★ 热路径不变量（改了 dispatch / models_router / 计费 / 凭证就要跑）
python admin/platform/skill/scripts/check_hotpath.py

# 跨平台同名模型冲突扫描（退出码 2 = 有未映射）
python scripts/check_model_conflicts.py

# 服务
python -m uvicorn admin.server:app --host 0.0.0.0 --port 8790
```

关键接口：

```
GET  /api/platforms                      平台列表 + 账号数
GET  /api/platforms/{id}/overview        某平台的概览卡片
GET  /api/gateway/config                 调度策略 + 优先级 + 禁用
PUT  /api/gateway/config                 保存策略
GET  /api/gateway/health                 全部平台健康度
GET  /api/gateway/order                  当前调度顺序（排障）
POST /api/gateway/disable                禁用平台
POST /api/gateway/enable                 恢复平台
GET  /api/sentinel/status                哨兵信号
GET  /api/overview                       跨平台聚合
```
