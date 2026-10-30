---
name: gateway-onboarding
description: 从零接入一个全新上游客户端作为网关平台的可执行手册。当需要新增平台（例如 Qoder、Cursor、Windsurf 等任何 IDE/CLI 客户端）、把它的模型能力转成 OpenAI/Anthropic/Responses 端点、并补齐账号池（扫描/导入/导出/用量/签到任务）时使用。含拆包、找接口、验证、转协议、接权益、接账号池的完整步骤与判据。
---

# 接入新网关：从拆包到跑通

这份手册的目标是**照着做就能做出一个新平台**。以 Qoder CN 为实例，
每一步都带真实命令、真实输出、和「怎么判断你做对了」。

> 与 `gateway-platform`（架构规范）的分工：
> 那份讲「代码该放哪、契约长什么样」；
> **这份讲「一个陌生客户端，怎么把它拆开、找到接口、接进来」**。

---

## 0. 全景：六步

```
①  定位安装目录      找到真正的 app（不是 Launcher）
②  拆包              解 asar / 脱壳 exe，拿到源码
③  找上游接口        请求地址、鉴权、请求体、响应体
④  验证连通性        不带凭证也要能区分「404 不存在」与「401 缺权限」
⑤  转协议            转成 /v1/chat/completions、/v1/messages、/v1/responses
⑥  接账号池          扫描本机 / 导入 / 导出 / 用量 / 权益任务
```

每一步都有**完成的判据**。没到判据就别往下走 —— 后面会全部返工。

---

## 1. 定位安装目录

### 踩坑：Launcher 不是应用

```
C:\Users\<u>\AppData\Local\Qoder CN\Qoder CN Launcher\
    Qoder CN Launcher.exe      128 KB     ← 只是引导器
    install.ini
```

128 KB 的 exe 装不下一个 IDE。**读 `install.ini` 拿真实路径**：

```ini
[launcher]
installDir=D:\Qoder CN
appExecutable=Qoder CN.exe
targetVersion=0.4.11
```

### 通用定位顺序

```powershell
# 1) 常见安装根
@("$env:LOCALAPPDATA\Programs", "$env:LOCALAPPDATA", "$env:PROGRAMFILES",
  "D:\", "E:\") | ForEach-Object {
  if (Test-Path $_) {
    Get-ChildItem $_ -Filter "*<Product>*.exe" -Recurse -Depth 3 -EA SilentlyContinue |
      ForEach-Object { $_.FullName }
  }
}

# 2) 读 install.ini / product.json 找 installDir
# 3) 看进程（客户端正在跑时最准）
Get-Process | Where-Object { $_.Name -match '<Product>' } | Select-Object Path
```

### 判据

- [ ] 找到的 exe **≥ 50 MB**（Electron 主程序量级）
- [ ] 同级有 `resources/` 目录
- [ ] `resources/` 里有 `app.asar`（Electron）或大量 `.dll`（原生）

---

## 2. 拆包

### 2.1 Electron：app.asar

`app.asar` 是**自定义容器**，不是 tar。结构：

```
[ 8 字节 pickle 头 ][ JSON 目录表 ][ 文件数据连续排列 ]
```

用 Node 直接读（`asar` 包可能没装，别依赖它）：

```javascript
// list.js —— 列出全部文件
const fs = require("fs");
const buf = fs.readFileSync(process.argv[2]);
const headerSize = buf.readUInt32LE(12);          // 关键：偏移 12
const header = JSON.parse(buf.slice(16, 16 + headerSize).toString("utf8"));
const out = [];
(function walk(node, prefix) {
  for (const [name, info] of Object.entries(node.files || {})) {
    const full = prefix + "/" + name;
    if (info.files) walk(info, full);
    else out.push({ path: full, size: info.size, offset: info.offset });
  }
})(header, "");
console.log("总文件:", out.length);
out.filter(f => f.path.endsWith(".js"))
   .sort((a, b) => b.size - a.size).slice(0, 25)
   .forEach(f => console.log((f.size/1024).toFixed(0).padStart(7), "KB", f.path));
fs.writeFileSync(process.argv[3], JSON.stringify(out));
```

```javascript
// extract.js —— 按需提取单个文件
const aligned = Math.ceil(headerSize / 4) * 4;    // ⚠️ 必须 4 字节对齐
const base = 8 + 8 + aligned;
const start = base + Number(entry.offset);
fs.writeFileSync(dest, buf.slice(start, start + entry.size));
```

**两个必踩的坑**：

| 坑 | 现象 | 修法 |
|---|---|---|
| 偏移算错 4 字节 | 解出的 JS 开头乱码 / `JSON.parse` 失败 | `Math.ceil(headerSize/4)*4` |
| 用 `entry.offset` 当绝对位置 | 读到别人的数据 | 要加 `base` |

### 2.2 只有 exe：三种情况

| 特征 | 判断 | 做法 |
|---|---|---|
| >= 100 MB，有 `resources/app.asar` | Electron 外壳 | **别拆 exe**，拆 asar（业务在 JS 里） |
| 20-80 MB，无 asar | 原生 + Web 前端 | `strings` 找 URL，或看 `resources/` 下的 `.node` / `.dll` |
| 有 `.NET` 特征 | C# | `dnSpy` / `ILSpy` |

```powershell
# 快速抓 URL（不需要反编译）
strings -n 12 app.exe | Select-String -Pattern 'https://[a-z0-9\.\-]+'
# Windows 没有 strings 时：
[regex]::Matches((Get-Content app.exe -Raw -Encoding Byte | ForEach-Object {[char]$_}) -join '',
                 'https://[a-zA-Z0-9\.\-]{6,60}') | ForEach-Object { $_.Value } | Sort-Object -Unique
```

### 2.3 别漏了这几个位置

```
resources/app.asar                       ← 主进程 + renderer
resources/app.asar.unpacked/             ← 原生模块 + 大 SDK（★ 最常被漏）
resources/extensions/<vendor>.*/         ← VSCode 系的功能扩展
resources/bin/*.node                     ← 原生插件
```

Qoder 的真实业务逻辑在：

```
resources/app.asar.unpacked/node_modules/@qoder-ai/qoder-cn-agent-sdk/
    dist/_worker/qoder-worker-runtime.obf.mjs     32.8 MB   ← 真正的 AI 运行时
```

**主进程 `electronMain.js` 17 MB 里几乎没有 URL** —— 如果搜不到接口，
先怀疑「业务在 unpacked 里」，不要在 asar 里死磕。

### 判据

- [ ] 能列出全部文件与大小
- [ ] 找到 ≥ 1 个 MB 级、名字含 `agent` / `sdk` / `core` / `runtime` 的 JS
- [ ] 该文件里能搜到 `https://`

---

## 3. 找上游接口

### 3.1 搜索顺序（从粗到细）

```powershell
$f = "path\to\bundle.mjs"
$c = [System.IO.File]::ReadAllText($f)   # 30MB 也能一次读进内存

# ① 先看有多少可搜的东西
foreach ($kw in @('chat/completions','v1/messages','/v1/responses',
                  'Authorization','Bearer','apiKey')) {
  "{0,-22} {1}" -f $kw, ([regex]::Matches($c, [regex]::Escape($kw))).Count
}

# ② 抓所有 host
[regex]::Matches($c, 'https://([a-zA-Z0-9\.\-]+\.[a-z]{2,})') |
  ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique

# ③ 抓所有 API 路径
[regex]::Matches($c, '"/[a-zA-Z0-9_/\-]{4,80}"') |
  ForEach-Object { $_.Value } | Sort-Object -Unique |
  Where-Object { $_ -match 'api|v1|v2|model|chat|usage|quota' }
```

### 3.2 混淆代码里怎么追 host

Qoder 的写法（压缩 + 三元表达式）：

```javascript
Os  = "cn" == "cn"                        // 恒 true → CN 版
kOe = Os ? "qoder.com.cn" : "qoder.sh"
ZGA = Os ? "gateway.qoder.com.cn" : "api2-v2.qoder.sh"
Cel = { prod: ZGA, daily: XGA, test: zGA }

D1i() { return process.env.QODER_MODEL_SERVER_HOST || Cel[Dw()] }
btt() { return `https://${D1i()}/model/v1/chat/completions` }
```

**追法**：从已知常量反查赋值。

```powershell
# 找 `XXX = "..."` 形式
[regex]::Matches($c, '\bZGA\s*=\s*[^;,}]{2,120}')
# 找三元里的两个分支
[regex]::Matches($c, 'Os\s*\?\s*"([^"]+)"\s*:\s*"([^"]+)"')
```

**结论落到一张表**（这是接网关的核心产物）：

| 用途 | 路径 | 鉴权 |
|---|---|---|
| 对话 | `https://gateway.qoder.com.cn/model/v1/chat/completions` | `Bearer <security_oauth_token>` |
| Agent SSE | `/algo/api/v2/service/pro/sse/agent_chat_generation` | 同上 |
| 额度用量 | `https://openapi.qoder.com.cn/api/v2/quota/usage` (GET) | 同上 |
| 区域端点 | `/algo/api/v5/service/region/endpoints` | 同上 |
| 健康检查 | `/algo/api/v1/ping` | 无需 |

### 3.3 凭证存哪

```powershell
# 关键词
foreach ($kw in @('keytar','safeStorage','secretStorage','Credentials',
                  'auth-state','globalStorage','storage.json')) { ... }
```

Qoder 的答案：

```javascript
await import("keytar")                       // → Windows 凭据管理器
service = "com.qoder/auth-state-v1"
```

对应 `.code-workspace` 之外的凭证链：

```
keytar("com.qoder/auth-state-v1") → JSON
    → security_oauth_token   (首选，Bearer)
    → access_token           (兜底)
    → refresh_token          (刷新时用)
```

### 判据

- [ ] 有 **host 表**（每个用途对应哪个域名）
- [ ] 有 **路径表**
- [ ] 有 **鉴权方式**（header 名 + token 从哪来）
- [ ] 知道 token 的**刷新接口**（否则 1 小时后全挂）

---

## 4. 验证连通性（这一步能省掉几小时）

**不要等接完代码才发现接口不对。** 先用裸 HTTP 打一遍。

### 4.1 判据表

| 返回 | 含义 | 结论 |
|---|---|---|
| `200` + 业务数据 | 端点存在、无需鉴权 | ✅ 可直接接 |
| `401` `TOKEN_INVALID` | **端点存在**，只缺凭证 | ✅ 路径对，去搞 token |
| `404` | 路径错 **或** 该 host 不提供 | ❌ 换 host / 换路径 |
| 超时 | 网络 / 需要代理 | ⚠️ 先解决网络 |
| `9004 parameters incorrect` | **鉴权已通过**，只是参数错 | ✅ 最理想：证明整套通了 |

> `9004` 这种「参数错」比 `200` 更有价值 —— 它证明你的 token 被接受了。

### 4.2 实测脚本

```python
import json, ssl, urllib.request, urllib.error

for url, method in (
    ("https://gateway.qoder.com.cn/algo/api/v1/ping", "POST"),
    ("https://openapi.qoder.com.cn/api/v2/quota/usage", "GET"),
):
    req = urllib.request.Request(url, data=b"{}" if method == "POST" else None,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print(r.status, url, r.read()[:120])
    except urllib.error.HTTPError as e:
        print(e.code, url, e.read()[:120])
```

真实输出（Qoder）：

```
[200] /algo/api/v1/ping                  pong
[401] /api/v2/quota/usage                {"code":"TOKEN_INVALID","message":"missing authorization token"}
[401] /algo/api/v5/service/region/endpoints  {"code":"TOKEN_INVALID",...}
[404] /model/v1/chat/completions         (空)
```

**读法**：`ping` 返回 `pong` → host 对。两个 401 → 路径对、缺 token。
`/model/v1/chat/completions` 404 → 该路径需要 BYOK 配置或走另一个 host，
**不是**主路径。主路径是 agent SSE。

### 判据

- [ ] 至少一个端点返回 `200` 或 `401`（证明 host 活着）
- [ ] 每个要用到的路径都有明确结论（可用 / 缺凭证 / 不存在）

---

## 5. 转协议

上游给什么协议，就要能转成用户要的三种之一。**先判断上游属于哪类**。

### 5.1 上游协议识别

| 上游特征 | 类型 | 转换难度 |
|---|---|---|
| `choices[].delta.content` | OpenAI 兼容 | ★ 直接透传 |
| `content_block_delta` / `message_start` | Anthropic 原生 | ★★ 转 OpenAI 要映射 |
| 自定义 envelope（`{"code":0,"data":{...}}`） | 私有 | ★★★ 必须手写 |
| SSE 里带 `event:` 行 | 需按事件分流 | ★★★ |

Qoder 是**双协议**：

```javascript
if (A.endsWith("/chat/completions")) openaiPath(A)
if (A.endsWith("/messages")) anthropicPath(A)     // /v1/messages
```

所以接 Qoder 时**优先用它的 OpenAI 兼容端点**，省掉一整套映射。

### 5.2 三个端点最小实现

```python
# 1) OpenAI
POST /v1/chat/completions
  body: {model, messages, stream, tools?, temperature?, max_tokens?}
  resp: {"choices":[{"delta":{"content":"..."}}], "usage":{...}}

# 2) Anthropic
POST /v1/messages
  body: {model, messages, system?, max_tokens, stream}
  resp: event: content_block_delta / message_delta / message_stop

# 3) Responses
POST /v1/responses
  body: {model, input, stream}
  resp: event: response.output_text.delta / response.completed
```

### 5.3 必须处理的四件事

| 事项 | 做错的后果 |
|---|---|
| **SSE 直通不缓冲** | 会看到「卡住 30 秒然后一次全出来」 |
| `usage` 从最后一帧取 | 账目永远 0（`stream_options.include_usage`） |
| `stop_reason` 映射 | `length`→`max_tokens`、`tool_use`→`tool_calls` |
| 错误码翻译 | 上游 `TOKEN_INVALID` 要变成 `401`，不能让客户端看到 500 |

### 5.4 流式骨架

```python
async def stream_upstream(...):
    async with client.stream("POST", url, json=body, headers=hdrs) as resp:
        if resp.status_code >= 400:
            raise UpstreamError(resp.status_code, await resp.aread())
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            yield to_openai_chunk(chunk)      # 逐帧转换，别攒
```

### 判据

- [ ] `curl -N` 能看到**逐字输出**（不是一次性）
- [ ] 最后一帧带 `usage`（prompt/completion/total tokens）
- [ ] 上游 4xx/5xx 被翻译成本协议的错误体

---

## 6. 接账号池

到这里「能对话」了，但还不是一个平台。账号池要做六件事。

### 6.1 扫描本机

**目标**：找到本机客户端里已登录的账号，**但绝不泄露 token**。

```python
def scan_local() -> dict:
    return {
        "items": [{
            "name": mask(name),          # 只给可识别信息
            "uid": mask(uid),
            "email": mask(email),
            "plan": plan,
            "has_token": bool(token),    # 布尔，不给值
            "path": str(config_path),    # 从哪读到的
        }],
        "errors": [...],
    }
```

Qoder 的读取位置（按优先级）：

```
1. keytar service "com.qoder/auth-state-v1"
2. %APPDATA%\Qoder CN\User\globalStorage\storage.json
3. %USERPROFILE%\.qoder\                      （CLI 版）
```

> ⚠️ 扫出来的 token **只用于导入**，接口响应里绝不返回。
> 这条不做，等于把自己的凭证打进日志。

### 6.2 导入 / 导出

```python
# 导入：接受三种形态
POST /api/<platform>/import
  {"credential": {...}}                 # 单条
  {"credential": [{...}, {...}]}        # 数组
  {"path": "C:/backup.json"}            # 备份文件

# 导出：必须脱敏
POST /api/<platform>/export
  → {"accounts": [{id, name, uid, plan, expires_at}]}   # 无 token
  → 或 {"format": "backup", "blob": "<加密>"}            # 完整备份
```

**导出两种模式**必须分清楚：给人看的（脱敏）/ 用于恢复的（加密）。

### 6.3 用量

```python
# 上游接口（需要凭证）
GET https://openapi.qoder.com.cn/api/v2/quota/usage
  Authorization: Bearer <token>
→ {quota, used, remaining, ...}

# 本网关账本（不需要上游，永远可用）
SELECT model, COUNT(*), SUM(total_tokens)
FROM usage_logs WHERE platform = '<platform>' GROUP BY model
```

> ⚠️ **必须同时给两个来源**。上游接口经常失效（凭证过期、
> 只对 IDE 开放、区域限制），本地账本是唯一可靠的兜底。
>
> 这个坑我踩过：Trae 的用量接口对 IDE token **恒返回 total=0**，
> 而代码里调了一个不存在的函数被 `except` 吞掉，于是「用量」永远空。
> 用户以为「没用过」，其实是查不到。

### 6.4 权益 / 签到 / 任务

```python
# 签到（有就接，没有要显式说）
def checkin(db):
    if not supports_checkin:
        return {"supported": False, "summary": "该平台没有签到接口"}
    ...

# 任务：注册进调度器，名字由平台自己声明
def task_runners() -> dict:
    return {
        "refresh_quota": refresh_quota,     # 刷额度
        "daily_checkin": daily_checkin,     # 签到
        "keepalive": keepalive,             # 保活
    }
```

Qoder 的权益相关路径（拆包所得）：

```
/algo/api/v2/service/business/finish     业务完成上报（可能关联任务）
/algo/api/v2/byok/check                  BYOK 校验
/algo/api/v2/byok/config                 BYOK 配置
/api/v2/quota/usage                      额度用量
```

### 6.5 健康检查

分段返回，让用户知道**卡在哪一段**：

```python
def health(db, account_id) -> dict:
    return {"stages": {
        "token":   {"ok": True,  "detail": "有效期至 ..."},
        "models":  {"ok": True,  "detail": "发现 12 个模型"},
        "chat":    {"ok": False, "detail": "上游 401"},
        "quota":   {"ok": True,  "detail": "剩余 88%"},
    }}
```

### 判据

- [ ] 扫描能列出本机账号，且响应**不含 token 明文**
- [ ] 导入能接受单条 / 数组 / 文件三种形态
- [ ] 导出区分脱敏与备份两种
- [ ] 用量同时给上游 + 本地账本，且能区分「查不到」与「没有」
- [ ] 签到/任务：不支持时**显式返回 `supported: false`**
- [ ] 健康检查分段，能定位到具体哪一步失败

---

## 7. 落地检查清单

新建 `admin/platform/<name>/` 后，逐项打勾：

```
[ ] __init__.py            install() 幂等，register(PlatformSpec)
[ ] gateway.py             转发实现（dispatch 调用）
[ ] platform_gateway.py    Gateway 契约（含 overview_cards）
[ ] core/client.py         上游 HTTP（带 token 刷新）
[ ] core/protocol.py       协议转换
[ ] services/pool.py       选号 / 冷却 / 熔断
[ ] routers/accounts.py    /api/<name>/* 管理接口
[ ] loader.py BUILTIN      加一行
[ ] platform-icons.js      图标（可选）
```

运行验证：

```bash
python tests/test_gateway_contract.py     # 契约（必须全过）
python tests/test_<name>_platform.py      # 平台自测
```

端到端：

```bash
# 1) 平台被识别
curl -H "X-Admin-Token: $T" localhost:8790/api/platforms

# 2) 概览卡片有内容
curl -H "X-Admin-Token: $T" localhost:8790/api/platforms/<id>/overview

# 3) 对话真的通（流式）
curl -N -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"<model>","messages":[{"role":"user","content":"hi"}],"stream":true}' \
  localhost:8790/v1/chat/completions

# 4) 用量有数（本地账本一定有）
curl -H "X-Admin-Token: $T" "localhost:8790/api/<name>/usage?days=30"
```

---

## 8. Qoder 实例：完整结论

拆包 NetEase（`D:\Qoder CN`，v0.4.11）得到：

### 版本判定

```javascript
Os = "cn" == "cn"        // 恒 true → 走 CN 分支
```

### 域名表

| 变量 | CN 值 | 用途 |
|---|---|---|
| `kOe` | `qoder.com.cn` | 后缀 |
| `ZGA` (prod model) | `gateway.qoder.com.cn` | **主网关** |
| `j3` / `_C` / `V3` | `gateway.qoder.com.cn` | US/SG/JP 同域 |
| `FJ` | `openapi.qoder.com.cn` | openapi |
| `vJ` | `center.qoder.sh` | center |

### 接口表

| 路径 | 方法 | 鉴权 | 实测 |
|---|---|---|---|
| `/algo/api/v1/ping` | POST | 无 | **200 pong** |
| `/algo/api/v2/service/pro/sse/agent_chat_generation` | POST | Bearer | 未认证 |
| `/model/v1/chat/completions` | POST | Bearer | 404（需 BYOK） |
| `/api/v2/quota/usage` | GET | Bearer | **401 TOKEN_INVALID** |
| `/algo/api/v5/service/region/endpoints` | POST | Bearer | **401 TOKEN_INVALID** |
| `/trae/api/v3/...` | — | — | 无（那是 Trae） |

### 凭证

```
keytar service: "com.qoder/auth-state-v1"
token 字段:     security_oauth_token → access_token
鉴权头:         Authorization: Bearer <token>
```

### 环境变量（可覆盖）

```
QODER_MODEL_SERVER_HOST     覆盖 model server
QODER_SDK_ACCESS_TOKEN      直接给 token
QODER_PAT                   personal access token
```

### 协议

```
OpenAI:    /chat/completions   （优先用这个）
Anthropic: /v1/messages        （内置支持，无需转换）
```

支持 `capabilities.thinking` / `tools` / `cacheControl`，
所以可以完整映射三个端点。

---

## 9. 常见坑速查

| 症状 | 根因 | 修法 |
|---|---|---|
| exe 只有 128 KB | 那是 Launcher | 读 `install.ini` 的 `installDir` |
| 解出的 JS 乱码 | header 未 4 字节对齐 | `Math.ceil(headerSize/4)*4` |
| 主进程搜不到 URL | 业务在 `app.asar.unpacked` | 搜 `node_modules/@vendor/*sdk*` |
| 所有路径 404 | host 猜错 | 先打 `/algo/api/v1/ping` 类探针 |
| 401 就以为路径错 | 401 = 端点存在、缺凭证 | 去搞 token，别改路径 |
| 拿到 token 还是 401 | 用错字段（`access_token` vs `security_oauth_token`） | 两个都试 |
| 对话一次性返回 | 中间层缓冲了 SSE | 关 gzip、逐帧 flush |
| 用量永远 0 | 调了不存在的函数被 `except` 吞 | 直接查 `usage_logs` |
| 导出泄露 token | 没脱敏 | 两种导出模式分开 |
| 签到报错 | 该区没有这个接口 | 返回 `supported: false` |

---

## 10. 实战案例

完整走通一遍的实例见 **[CASE-QODER.md](CASE-QODER.md)** —— 从拆包到
三协议（Chat Completions / Anthropic / Responses）全部跑通的每一步、
真实输出、和踩到的坑。

### 它补充了本文没写的五条

| 教训 | 为什么重要 |
|---|---|
| **产品名 ≠ 数据目录名** | Qoder 数据在 com.qodercn.app.stable，找 Qoder CN 全部落空 |
| **业务代码可能在 pp.asar.unpacked** | 主进程 17 MB 里只有 1 个 URL，业务在 unpacked 的 SDK 里 |
| **Protocol 的 platform_id 属性容易漏** | 方法齐了但缺它，isinstance 判 False 且错误信息不提示 |
| **Protocol 方法签名不能有默认值差异** | (request, body=None) vs (request, body) 会导致检查失败 |
| **默认平台不能按字典序被抢占** | 新增平台若字母序靠前，会悄悄接管所有「未指定平台」的请求 |

### 顺序铁律（最重要的一条）

`
① 探针确认 host 活着（/ping 类接口返回 200）
② 确认路径存在（401 = 存在缺凭证；404 = 路径错）
③ 解决凭证
④ 才写协议转换
`

反过来做，会在「凭证」和「路径」两个未知量之间来回猜 ——
而这两类错误（401 / 404）在上游看来长得完全不同，混在一起就无法定位。

---

## 11. 接完协议之后（必读）

**[INTEGRATION-CHECKLIST.md](INTEGRATION-CHECKLIST.md)** —— 接完协议、能对话
之后最容易漏的四件事：

1. **平台图标** —— 用 lobe-icons（https://lobehub.com/icons/skill.md），
   没有官方图标就用母品牌，不要自己画几何占位
2. **定时任务** —— 三个地方都要通：平台声明 / 下拉清单 / 实际分派
3. **风控哨兵** —— 新平台必须上报，且 kind 要区分账号问题与实现问题
4. **余额实时** —— list() 要写回 DB 列，否则 /api/accounts 读的是旧值

漏了任何一项，新平台都是「看起来能用，实际是半成品」。
