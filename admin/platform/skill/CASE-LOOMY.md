# 实战案例 2：Loomy（无凭证摸底 → 定位明文凭证）

这份是 **`NO-CREDENTIAL.md` 的实操验证**：全程**没有向用户要任何凭证**，
也没有读进程内存（那种做法必须让客户端开着，不能作为产品能力）。

---

## 0. 为什么单列一份

Qoder 那次用了用户给的 PAT（**那是作弊**）。Loomy 这次严格按规范走：

```
拆包 → 找 host → 找接口 → 探针验证 → 读源码定位凭证 → 读文件 → 验证
```

结果：**找到了明文凭证文件**，且发现它已过期。
这比「拿到一个能用的 token」更有价值 —— 因为它证明了方法可复用。

---

## 1. recon 脚本的第一次失败（重要教训）

第一次跑 `scripts/recon.py` 对 `D:\Loomy`：

```
主提取了「最大的 8 个 js」
→ xlsx.js / babel / codeMirrorModule / playwright-core
→ paths 命中数: 0
→ hosts 全是 github.com / w3.org / open.feishu.cn（文档链接）
```

**「体积大」和「是业务代码」零相关。** 打包产物里最大的往往是
`xlsx` / `pdfjs` / `babel` / `monaco`。

### 修正：按路径打分选文件

```python
PATH_BOOST   = main +10, app +8, api +10, service +8, auth +8, out/ +6 …
PATH_PENALTY = node_modules/ -20, xlsx -15, pdfjs -15, babel -15, monaco -15 …
```

### 第二个失败：阈值太高

改完打分后仍然漏 —— 因为 `--min-kb 200` 把业务文件全过滤掉了：

```
D:\Loomy\resources\app.asar.unpacked\electron\
    model-service.js              43 KB
    auth-session-controller.js     3.8 KB   ← 关键文件，只有 3.8 KB
    auth-session-runtime.js        605 B    ← ★ 答案就在这里
```

**业务文件常常只有几百字节到几十 KB。**

> 修正：`--min-kb` 默认降到 1，只排除明显的依赖目录。

---

## 2. 真实的结构（Loomy）

```
D:\Loomy\resources\
    app.asar                    220 MB / 12593 文件 / 7676 js
    app.asar.unpacked\electron\    50 个业务 .js（几 KB 一个）
    opencode-home-template\
        opencode.json            ← ★ provider 配置（baseURL 在这）
    app.asar.unpacked\electron\xfyun\
        account-service.js       ← ★ 登录接口
    app.asar.unpacked\electron\auth\
        auth-session-runtime.js  ← ★ 凭证存储名
    .env.prod                    LOOMYENC1:<加密>（放弃）
```

### 找到 host 的三条路（都无需凭证）

| 路径 | 结果 |
|---|---|
| `.env.prod` | `LOOMYENC1:` 加密，跳过 |
| `opencode.json`（模板） | ★ **`baseURL` 明文** |
| 源码里 `https://` 正则 | `loomy.xunfei.cn` / `static-res.xfinfr.com`（次要） |

`opencode-home-template/opencode.json` 是关键：

```json
"provider": {
  "imodel": {
    "npm": "@ai-sdk/openai-compatible",
    "options": {
      "baseURL": "https://ossptest.voicecloud.cn/loomy/integration/api/v1",
      "useSessionAuth": true,
      "timeout": 1200000
    }
  }
}
```

> **教训**：客户端把「模型接入配置」外置成模板文件时，那里就是最快的突破口。
> 比在 220 MB asar 里搜 URL 快十倍。

---

## 3. 探针：确认端点存在与鉴权类型

```bash
python scripts/probe.py \
  --host "https://ossptest.voicecloud.cn/loomy/integration/api/v1" \
  --path "/models" --method GET \
  --path "/chat/completions" --method POST
```

结果：

```
[404] GET  /models             → 路径不存在（或需要认证后才暴露）
[200] POST /chat/completions   → 200，但 body 是业务错误
```

`/chat/completions` 的真实响应：

```json
{"code":"100002","desc":"缺少 token"}
```

**这就是 `param_error` 级别的证据** —— 端点存在、鉴权通过、只缺令牌。
按判据表，这是最理想的信号。

---

## 4. 读源码定位凭证（这是全流程的核心）

### 4.1 从鉴权头反查

```javascript
// search-service.js / llm-completion.js
headers.Authorization = `Bearer ${sessionToken}`
if (useSessionAuth) headers.token = sessionToken       // ← 双头！
```

**双头模式**：`Authorization: Bearer <x>` + `token: <x>`。
只发一个会 401，而错误信息不一定提示少了哪个。

### 4.2 从请求头反查（一个坑）

```javascript
// request-headers.js
headers.traceparent = generateTraceparent()   // 00-<32hex>-<16hex>-01
```

```javascript
// llm-completion.js 的注释（实测记录）
// 实测 Loomy iModel 缺 traceparent 时 chat/completions 会挂死直到超时。
```

> **这类「缺了会挂死而不是报错」的头最难查**。
> 排查信号：请求不返回、超时、无错误体。

### 4.3 追凭证存储（关键四跳）

```
① grep "updateAuthSession"      → auth/auth-session-controller.js
② 读 controller                 → store.get('session') / store.set(...)
③ 追 store 从哪来               → auth/auth-session-runtime.js
④ 读 runtime                    → new Store({ name: 'auth-session' })
```

`auth-session-runtime.js`（全文 605 字节，就是答案）：

```javascript
const store = createStore({
  cwd: userDataPath,
  name: 'auth-session',        // → <userDataPath>/auth-session.json
  defaults: { session: '', userid: '', phone: '', updatedAt: 0 },
})
```

### 4.4 但 `userDataPath` 不是默认位置！

```javascript
// config-path.js
export function getConfigRoot() {
  if (process.platform !== 'win32') return null
  return path.join('C:', 'Users', 'Public', 'Loomy', _getUserHash())
}

export function _getUserHash() {
  const username = os.userInfo().username
  return crypto.createHash('sha256').update(username).digest('hex').slice(0, 12)
}

export function getUserDataPath() {
  if (process.platform !== 'win32') return getElectronUserDataPath()
  return path.join(getConfigRoot(), 'userData')
}
```

算出来：

```
C:\Users\Public\Loomy\<sha256("Administrator")[:12]>\userData
                   = e7d3e769f3f5
```

**如果只猜 `%APPDATA%\Loomy`，永远找不到这个文件。**

### 4.5 读到了

```json
// C:\Users\Public\Loomy\e7d3e769f3f5\userData\auth-session.json
{
  "session": "5e179c25f9bd4f89ad6efc66d408987c",
  "userid": "261027143030023889",
  "phone": "13974512963",
  "updatedAt": 1793082630934
}
```

**明文 JSON，137 字节。零解密。**

---

## 5. 验证：session 确实失效了

带上全部正确的头（含 `traceparent` + `loomy-version` + 双 token 头）：

```python
headers = {
    "Authorization": "Bearer " + SESSION,
    "token": SESSION,
    "traceparent": "00-<32hex>-<16hex>-01",   # 缺了会挂死
    "loomy-version": "1.0.0",
}
GET https://ossptest.voicecloud.cn/loomy/integration/api/v1/models
```

```
HTTP 200  {"code":"100002","desc":"登录已失效，请重新登录"}
```

### 为什么能确认真失效（而不是我请求写错）

1. 头已按源码逐字对齐（双 token + traceparent + version）
2. `100002` 在 `account-service.js` 里被明确归为 `AUTH_ERROR_CODES`
3. **Loomy 没有 refresh 接口** —— grep `/session/refresh` / `/login/account/refresh`
   全是 404。失效只能重新登录。
4. `updatedAt: 1793082630934` = 2026-10-27，一天前

---

## 6. 这次学到的六条（已写进脚本）

| 教训 | 落在哪 |
|---|---|
| 按体积选文件是错的，要按路径打分 | `recon.py` |
| 业务文件可能只有 605 字节，阈值不能高 | `recon.py --min-kb 1` |
| `electron-store` 的 `name` 就是文件名 | `creds.py` 的 STORE_NAME_RE |
| **产品名 ≠ userData 路径**，要读路径构造函数 | `creds.py` 的 PATH_FN_RE |
| 双头鉴权（`Authorization` + `token`） | 文档 + 验证函数 |
| 缺 `traceparent` 会挂死而非报错 | 文档「常见死胡同」 |

---

## 7. 无凭证摸底能到什么程度（这次实测）

```
✓ 安装结构、as.ar 拆分方式
✓ 业务代码位置（50 个几 KB 的文件）
✓ provider 配置（baseURL + useSessionAuth）
✓ 端点存在性（404 vs 200+业务码）
✓ 鉴权方式（双头 + traceparent + loomy-version）
✓ 凭证文件名（auth-session.json）
✓ 凭证真实路径（C:\Users\Public\Loomy\<hash>\userData）
✓ 凭证是否加密（明文）
✓ 凭证是否有效（无效，需重新登录）
✗ 真实对话（没有有效 token）
✗ 模型清单（需要有效 token 才能拉）
```

**结论：除了「真调一次」，其它全部可以无凭证完成。**

---

## 8. 一个必须承认的现实

Loomy 的 session **只能通过手机号+验证码或微信扫码登录获得**，
且**没有 refresh 机制**。所以：

* 无法做到「扫一次永久可用」
* 每次 session 过期都需要用户重新登录

这类平台的产品方案只能是：

```
1. 扫描本机 → 有 auth-session.json 就读（明文，零成本）
2. 验证有效性 → 失效则引导用户在客户端重新登录
3. 用户登录后 → 再次扫描自动同步（不用手动复制任何东西）
```

**这是「复用客户端登录态」的标准形态** —— 用户不需要给凭证，
他只需要在客户端里正常登录一次，网关自己读文件。
