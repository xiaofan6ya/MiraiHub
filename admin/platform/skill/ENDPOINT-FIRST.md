# 排查「客户端能调、网关调不通」的标准动作

> 这份文档是一次真实踩坑的总结：某个模型在官方客户端能正常对话，
> 但我们的网关怎么调都报「模型不存在 / 无法识别」。
> 我在**错误的端点上试了 20 多种参数组合**，全失败；
> 最后对照开源实现才发现——**客户端根本不用那个端点**。

**核心教训：先确认「客户端走哪个端点」，再谈参数。**
端点错了，参数试一万遍也没用。

---

## 一、我犯的错（照镜子）

| 我的做法 | 为什么错 |
|---|---|
| 看到 4023「model unknown」，就去试各种 model_name 变体 | 4023 是**端点级**的拒绝，改名字无效 |
| 抱着 `llm_raw_chat` 试 body 字段 | 这个端点是「内联补全/agent」用的，聊天主通道是另一个 |
| 反复清账号冷却、换账号 | 跟账号冷却无关，是请求打错了门 |
| 抓包（mitmproxy / 自签证书） | Electron 有自己的证书策略，不读 Windows 根库，TLS 直接断 |
| 读客户端 bundle 找线索 | **这条其实是对的**，但我找错了位置就放弃了 |

**最致命的误判**：把「4023 模型不存在」当成「账号没权限」，
于是反复去查账号权益、清冷却、换账号——**浪费了大量时间**。

**正确理解**：同一个端点对不同场景返回不同错误码，
错误码**本身**就是定位线索（见第三节的对照表）。

---

## 二、标准排查顺序（照做，别跳步）

### 第 1 步：先确认端点数（最高优先级）

平台协议通常有**多个聊天端点**，不要假设只有一个：

```bash
# 从客户端 bundle 里挖端点表（最可靠）
grep -oE '"[a-zA-Z]+API":"[^"]*"' app.js | sort -u
```

真实的例子（某个 IDE 客户端的端点表）：
```
modelListAPI:        api/ide/v1/model_list          ← 只返回 6 个模型（旧）
fusionModelListAPI:  api/ide/v1/get_model_list      ← 完整模型表
llmRawChatAPI:       api/ide/v1/llm_raw_chat        ← 补全/agent 用
fusionRawChatAPI:    api/ide/v2/llm_raw_chat        ← 真·聊天主通道（v2 ！）
chatAPI:             api/ide/v1/chat                ← 会话式聊天（信封格式）
```

**结论：同一个模型，走不同端点结果完全不同。**
v1 返回 4023，v2 返回 400（参数错），改对参数后 v2 直接成功。

### 第 2 步：找开源实现对照（最省时间）

**先搜现成实现，再自己逆向。** 我这次就是最后才去抄
[Trae2api-cn](https://github.com/autumnsentiment/Trae2api-cn)，
10 分钟就拿到了正确结构。

```bash
git clone --depth 1 <repo> ../ref
grep -rn "llm_raw_chat\|/api/ide/v2" ../ref/src | head -20
```

重点看这几个函数：
- `build_*_body()`  → 请求体精确结构
- `build_*_headers()` → 必带的头
- `resolve_*_model()` → 模型名映射规则
- `*_ENDPOINT` 常量 → 端点与回退端点

### 第 3 步：认错误码含义（别乱猜）

| 错误码 | 真实含义 | 该动什么 |
|---|---|---|
| `4023 model unknown` | **这个端点**不认这个模型 | 换端点，不是换名字 |
| `4001 param invalid` | 端点认了，但 body 结构不对 | 修 body 字段 |
| `400`（无 body） | 结构差得太多，直接拒 | 对照参考实现重写 |
| `3003 all models failed` | 格式对了，缺会话上下文 | 补 client_info / session |
| `2001 failed to get app config` | 上游按 app_id 查不到配置 | 账号侧问题，见第五节 |
| `503 no_account` | 号池空（可能被模型级冷却清空） | 查 cooldown 表 |

**判断法**：错误码从 4023 → 400 → 2001 一路变化，说明**请求在逐步变对**，
不要因为没直接成功就回退。

### 第 4 步：最后才考虑抓包

抓包对 Electron 应用经常失败：
- 不读 Windows 根证书库 → TLS 直接断（`CERTIFICATE_UNKNOWN`）
- 不支持 `--proxy-server` 参数（报 `bad option`）

**替代方案（按优先级）**：
1. 读客户端本地缓存数据库（很多客户端把模型表存 SQLite）
2. 客户端 DevTools console 里 hook `fetch` 打印请求
3. 读客户端日志目录（`%APPDATA%/<App>/logs/`）

---

## 二·补、多端点的真实分层（最重要的案例）

同一个平台的模型**按类别分属不同端点**，这是最容易踩的坑：

| 模型类别 | 端点 | 表现 |
|---|---|---|
| IDE 补全 / chat_v3 类 | `POST /api/ide/v1/llm_raw_chat` | 老模型能通 |
| **SOLO / Agent 类** | **`POST /api/agent/v3/llm_utils_chat`**<br>`function = solo_work_lite` | **上面那个端点回 4023** |
| 会话式聊天 | `POST /api/ide/v1/chat` | 信封格式，缺 ctx 回 3003 |
| 完整模型表 | `GET /api/ide/v1/get_model_list` | 与 `model_list` 内容不同 |

**判据**：`deepseek-v4.1-flash` / `glm-5.3-flash` / `kimi-k3` / `minimax-m3`
这些「新模型」几乎都在 **SOLO 通道**。用 v1 打它们，永远 4023。

### SOLO 通道完整请求（已在本项目实现）

见 `admin/platform/trae/core/solo.py`。关键点：

```python
# 端点
POST {core}/api/agent/v3/llm_utils_chat

# 头（注意 Authorization 用 Cloud-IDE-JWT，不是 Bearer）
Authorization: Cloud-IDE-JWT {token}
X-Cloudide-Token / X-Ide-Token: {token}
X-Uid: {uid}
X-Machine-Id / X-Device-Id: 可用固定默认值（见下）
X-App-Id: 6eefa01c-1036-4c7e-9ca5-d891f63bfcd8
X-Ide-Version: 0.1.61
X-Ide-Version-Code: 20260820      # ← 不是 IDE 的 20260212，用错会 4001/4015
X-App-Version-Code: 20260820
X-Ide-Version-Type: stable
X-Device-Type: windows
X-OS-Version: Windows 11 Pro
X-Device-Brand: 83DG
Request-Traffic-Type: prod
User-Agent: Trae/0.1.61 antigravity-cockpit-tools

# 请求体（config_name 和 model 两个都要）
{"messages":[{"role":"user","content":[{"type":"text","text":"hi"}]}],
 "function":"solo_work_lite",
 "stream":true,
 "config_name":"deepseek-v4.1-flash",
 "model":"deepseek-v4.1-flash",
 "max_tokens":1000000}

# 响应（事件名和 OpenAI 完全不同）
event:metadata      data:{"session_id":...,"model":""}
event:timing_cost   data:{"platform_first_token_timing":645,...}   # ← TTFT 可观测
event:output        data:{"response":"...","reasoning_content":"..."}
event:done          data:{"finish_reason":"stop"}
```

**设备身份可以写死**：
```
X-Device-Id:  1149420231460112
X-Machine-Id: d979d81c1deda7a803cb6b76be69cff63b40dd24ea60d3fabbd865607f7ee695
```
这条通道只校验「token + 合法设备」，不要求与登录设备一致。
好处：号池里所有账号共享同一台"虚拟机"，不因本机变化失联。

**国际版限制**：`trae-intl` 的登录态在 SOLO 通道返回 401
（`code:1001 not able to authenticate`）。SOLO 基本只认国内版账号。

---

## 二·补 2、多项目交叉验证法

不要只看一个开源实现。至少抓 2~3 个对比，**差异处往往就是关键**：

```bash
for repo in A B C; do git clone --depth 1 <url> ../ref-$repo; done
grep -rn "llm_raw_chat\|llm_utils_chat\|agent/v3" ../ref-*/*.py | sort | uniq -c
grep -rn "X-Ide-Version-Code\|version_code" ../ref-*/*.py | sort -u
grep -rn "def build_.*body\|def .*_chat(" ../ref-*/*.py
```

实测差异示例：
```
项目 A: 只用 /api/ide/v1/llm_raw_chat      -> 对 v4.1 全部 4023
项目 B: 额外有 /api/agent/v3/llm_utils_chat -> v4.1 直接 200
```

**一个项目调不通，不代表这个平台不可调**，很可能是这个项目也没覆盖到。

---

## 三、从客户端本地缓存挖真东西（高价值技巧）

很多 Electron 客户端把**完整模型表**缓存在 SQLite 里，
比 API 返回的更全（API 常只返回「当前可用」的子集）。

```python
import sqlite3, json
db = r"%APPDATA%/<App>/User/globalStorage/state.vscdb"
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
cur = con.cursor()
cur.execute("SELECT key FROM ItemTable WHERE key LIKE '%model%'")
for (key,) in cur.fetchall():
    cur.execute("SELECT value FROM ItemTable WHERE key=?", (key,))
    data = json.loads(cur.fetchone()[0])
    # data 常是 {场景: [模型...]}，场景名就是端点的路由依据
    for scene, items in data.items():
        print(scene, [m.get("name") for m in items])
```

**挖出来的关键信息**：
- `scene` 名（如 `chat_v3` / `solo_agent`）= 上游路由依据
- `config_name` / `model_name` / `use_v2_process` = 该走哪个端点
- `selectable` / `status` = 客户端是否认为可用
- `activity_discount` = 真实计费倍率（`before_consumption_rate`）

> 同一份表可能有**多个 key**（带冒号/下划线两种前缀），
> 分别对应不同模式（IDE 模式 / SOLO 模式），**都要看**。

---

## 四、已知的协议陷阱（换个平台也可能遇到）

### 4.1 `messages[].content` 必须是 parts 数组
```python
# 错 —— 直接 400
{"role": "user", "content": "hi"}

# 对
{"role": "user", "content": [{"type": "text", "text": "hi"}]}
```

### 4.2 模型名有「配置名」和「上游名」两套
```
config_name = "DeepSeek-V4-Pro"        ← 配置里的名字
model_name  = "DeepSeek-V4-Pro__v2"    ← 上游 provider id（常带 __v2）
```
两者**可以不同**，body 里要各填各的。文档里如果只写了一个，另一个往往是 `__v2` 后缀。

### 4.3 会话 id 要出现两次
```python
{"conversation_id": sid, "session_id": sid}   # 同一个 uuid
```

### 4.4 需要 `Extra` 头（JSON 字符串，ASCII 编码）
```python
Extra = json.dumps({
    "agent_loop_id": sid, "api_key": token,
    "config_name": ..., "model_name": ..., "session_id": ...,
}, ensure_ascii=True, separators=(",", ":"))
# 注意 ensure_ascii=True —— HTTP 头只能是 ASCII
```

### 4.5 `stream` 恒为 True
即使客户端请求 `stream:false`，上游也常只支持 SSE，
需要在网关侧再做聚合（本项目 `core/protocol.py` 就是这么做的）。

---

## 五、区分「端点问题」和「账号问题」

推到 `2001 failed to get app config: record not found` 这类错误时，
说明**协议已经全对了**，剩下是账号侧：

| 现象 | 判定 | 处理 |
|---|---|---|
| 同一端点，A 模型 200、B 模型 4023 | B 不在该账号可用清单 | 查 `model_list`，别硬试 |
| 所有模型都 4023 / 2001 | 请求格式或端点错 | 回第 1/3 步 |
| 所有模型都 3003 | 缺会话上下文 | 补 client_info |
| `record not found` / `app config` | 该账号在上游没有应用配置 | **账号侧，代码无解**，需用户换号/开通 |

**重要**：不要因为一个模型不通就判定「整个平台不可用」。
一定要**用同一端点、同一账号，拿一个已知可用的模型做对照**：

```python
# 对照组：已知可用的模型
ok = call(model="deepseek-R1")
bad = call(model="deepseek-v4.1-flash")
# ok=200 且 bad=4023  -> 是「该模型没权限」
# ok 也失败           -> 是「端点/格式错」，回去查协议
```

---

## 六、交付前必做

- [ ] 同一端点 + 同账号，跑一个**已知 OK 的模型**做对照
- [ ] 把可用/不可用结论写进模型的 `note` 字段，前端悬停可见
- [ ] 不可用的模型**不要**直接 `enabled=0`（别的账号可能开通），只标注
- [ ] 平台能力（`supports_local_scan` 等）要如实声明，不支持就别暴露按钮
- [ ] 「不支持扫描」的平台**必须**提供授权登录入口，否则账号无法添加

---

## 七、一句话总结

> **4023 不是「模型错了」，是「你敲错门了」。**
> 先找端点表，再抄开源实现，错误码会告诉你进度；
> 抓包是最后手段，客户端本地缓存往往比抓包更有用。
