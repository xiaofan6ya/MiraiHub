# 无凭证接入：用户不会给你 token，怎么办

这份是 `ONBOARDING.md` 里最容易被忽略但**最影响真实可用性**的一章。

> 前几份文档默认「你能拿到一个凭证去验证」。真实情况恰恰相反：
> 用户不会给你 PAT，不该把 token 贴进聊天，换台机器就失效。
> **你必须自己在无凭证状态下把路走通。**

---

## 0. 为什么「问用户要凭证」是错的

| 问题 | 后果 |
|---|---|
| 用户不知道在哪生成 PAT | 卡住，做不下去 |
| 用户不该把凭证贴进聊天记录 | 泄露，要撤销重发 |
| 换机器/换账号就失效 | 做出来的东西不能复用 |
| 用户可能只有一台机器、只有客户端登录态 | 根本没有 PAT 可给 |

**PAT 只能是最后兜底，不能是主路径。**

---

## 1. 无凭证能走多远？（答案：能走 90%）

不需要任何凭证就能确定：

```
✓ 安装目录与真实应用位置
✓ 拆包、拿源码
✓ 全部上游主机（host）
✓ 全部接口路径
✓ 每个接口的方法（GET/POST）
✓ 鉴权头名（Authorization / x-api-key / 自定义签名头）
✓ 路径是否存在（探针：401 vs 404）
✓ 是否需要请求签名（403 + "Signature invalid"）
✓ 凭证存在磁盘哪里（文件名、大小、是否加密）
✓ 模型/能力清单（如果接口公开或能从本地目录读）
```

**不能**在没有凭证时做到的：

```
✗ 真实对话（需要有效 token）
✗ 真实额度数值
✗ 签到领取
✗ 验证号池调度
```

但这些**恰好都是拿到凭证之后才有意义验证的东西**。
所以正确顺序是：

```
无凭证 → 摸清全貌 → 设计授权路径 → 用户授权（一次） → 验证功能
```

---

## 2. 五级授权方案（按优先级）

### 第 1 级：复用客户端已登录态（最优）

**用户什么都不用做** —— 客户端已经登录了，读它的凭证即可。

要查的地方（`scripts/creds.py` 会自动扫）：

```
%APPDATA%\<app-id>\           ← Electron 数据目录（名字可能≠产品名！）
%APPDATA%\<app-id>\auth*.dat  ← 加密凭证
%APPDATA%\<app-id>\main.sqlite ← 可能有 account_profiles
~/.<app>-cn/                  ← CLI 客户端
~/.<app>/.models/             ← 模型目录（常带 uid）
系统凭据管理器（keytar/DPAPI）
```

**三种常见情形**：

| 情形 | 表现 | 能不能直接读 |
|---|---|---|
| 明文 JSON | `storage.json` 里有 token 字段 | ✅ 直接读 |
| Electron DPAPI | `auth.v1.dat`，开头 `v10` | ⚠️ 本机用户上下文可解 |
| 只在系统钥匙串 | 磁盘找不到 | ⚠️ 要调 keytar/DPAPI |

> ⚠️ **产品名 ≠ 数据目录名**。实测 Qoder：找 `%APPDATA%\Qoder CN` 全部落空，
> 真实位置是 `com.qodercn.app.stable`。
>
> **唯一可靠的方式**是从运行中的进程读命令行：
> ```powershell
> Get-CimInstance Win32_Process -Filter "Name='<app>.exe'" |
>   Select-Object -ExpandProperty CommandLine
> # → --user-data-dir="C:\...\com.qoder...app.stable"
> ```

### 第 2 级：设备流授权（次优，用户体验好）

很多客户端用 device code flow：

```
1. 生成 challenge / nonce / machine_id
2. 拼授权 URL：{web_origin}/device/selectAccounts?challenge=...&nonce=...
3. 用户在浏览器打开 → 点「授权」（可能已登录，一点就完）
4. 后台轮询 {open_api}/api/v1/deviceToken/poll
5. 拿到 dt- token + drt- refresh
```

**在 recon 报告里找这些路径**：

```
/device/selectAccounts       授权页
/api/v1/deviceToken/poll     轮询
/api/v1/deviceToken/refresh  续期
/oauth/authorize             备选
```

有这套流程的平台**完全不需要用户手动复制任何东西** —— 点一次就好。

### 第 3 级：让用户在客户端里操作一次（上传 cookie/token）

有些平台只把凭证放内存。这时：

1. 引导用户在客户端里正常使用（它自己会发请求）
2. 同时提供「从浏览器/客户端导出」的明确步骤
3. **必须脱敏**：导出的只能是 token，不能带上其它个人信息

### 第 4 级：浏览器 OAuth 回调

```
/oauth/authorize → 用户授权 → 重定向带 code → 换 token
```

需要在本地起一个临时回调端口。适合有标准 OAuth 的平台。

### 第 5 级：PAT（最后手段）

只有前面全失败才用。且必须：

* 明确告诉用户在**哪里**生成（给准确 URL）
* 说明「这是兜底方案，长期建议用设备流」
* 生成的 PAT 立刻换成长效 token，PAT 本身不落库

---

## 3. 实测：Qoder 走了哪条路

| 步骤 | 结果 |
|---|---|
| 数据目录 | `com.qodercn.app.stable`（不是 `Qoder CN`） |
| 凭证文件 | `auth.v1.dat` 547 字节，开头 `v10`，**加密** |
| 解密实现 | 在原生模块（JS 里搜不到 `auth.v1` / `safeStorage`） |
| 内存扫描 | 只有过期的、**别的系统**的 JWT |
| keytar | `com.qoder/auth-state-v1`（要 DPAPI） |
| 明文 token | **没有** |

结论：**第 1 级失败**。

而它其实有**第 2 级**能力：

```
DEVICE_LOGIN_PATH  = "/device/selectAccounts"
DEVICE_POLL_PATH   = "/api/v1/deviceToken/poll"
DEVICE_REFRESH_PATH= "/api/v1/deviceToken/refresh"
```

所以正确做法是**设备流**，不是要 PAT。
（这次为了先把协议验证通，用了 PAT；但那不是产品方案。）

---

## 4. 「没有凭证」时怎么写平台模块

关键：**把「拿凭证」做成可替换的一层**，不要写死。

```python
class AuthProvider(Protocol):
    """凭证来源。实现可替换，上层不关心 token 从哪来。"""
    def scan_local(self) -> list[dict]:
        """本机已登录的账号（可能读不出 token）。"""
    def begin_device_flow(self) -> dict:
        """返回 {auth_url, poll} —— 用户点一次就好。"""
    def exchange(self, payload: dict) -> dict:
        """PAT / cookie / code 换 token。"""
    def refresh(self, refresh_token: str) -> dict:
```

于是：

```python
ops.import_local(db)     # 走 scan_local；读不出就返回 credential_encrypted
ops.begin_auth()         # 走设备流，返回 auth_url 给前端
ops.import_record(db, r) # 走 exchange（PAT / cookie）
```

**`import_local` 读不出 token 时必须显式说**：

```python
return {"ok": False, "reason": "credential_encrypted",
        "hint": "凭证加密在原生层，请用设备授权或 PAT"}
```

不能假装导入成功 —— 那会让用户以为号已经加进来了。

---

## 5. 验证顺序（无凭证也能推进）

```
阶段 1（无凭证，可完成 80%）
  [ ] recon.py 出报告：主机 / 路径 / 头名 / env
  [ ] probe.py 确认哪些端点存在（401 vs 404）
  [ ] 判断鉴权类型：Bearer / 签名 / 双令牌
  [ ] creds.py 找凭证位置，判断能否读
  [ ] 写出平台模块骨架 + 契约实现
  [ ] verify_platform.py 结构检查全绿（1-8 项）

阶段 2（用户授权一次）
  [ ] 走设备流或第 1 级读到 token
  [ ] 模型清单能拉到
  [ ] 单轮对话能通
  [ ] 额度能读到
  [ ] 签到/任务能跑

阶段 3（交付）
  [ ] verify_platform.py 全绿（含运行 + 浏览器）
  [ ] 三协议都能通
  [ ] 号池隔离正确
```

**阶段 1 全绿就说明骨架是对的** —— 后面只差一个 token。

---

## 6. 常见「没有凭证」的死胡同与出路

| 死胡同 | 出路 |
|---|---|
| 凭证加密且解密在原生层 | ① 设备流 ② 让用户先在客户端用一次再读 ③ 提供「粘贴 cookie」入口（明确标注临时方案） |
| 只有 PAT 没有设备流 | 只能要 PAT，但要说清路径；且 PAT 只换 token，不落库 |
| 连接口都探测不到（全 404） | 检查 host 是否猜错；试 `--common` 的探针路径；看是否有区域分流 |
| 探针全是超时 | 需要代理；或该 host 只对内网开放 |
| 拉不到模型清单 | 退化到「按请求体里的 model 原样转发」，不做校验 |
| 签名算法看不懂 | 优先找有无「不带签名」的等价端点；否则移植客户端里的实现 |

---

## 7. 一句话原则

> **能在无凭证状态下把「接口全貌 + 授权路径」确定下来，
> 才算真正会接平台。**
>
> 拿到 token 才敢动手，等于把「用户能不能给你凭证」当成前提 ——
> 而现实中这个前提经常不成立。
