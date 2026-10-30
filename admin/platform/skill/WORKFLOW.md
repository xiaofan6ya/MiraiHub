# 六个阶段的固定流程（含每阶段的可执行验证）

这份是**操作手册的骨架**。工具会变、平台会变，但这六步是死的。

> 配套脚本（都在 `scripts/`）：
> `recon.py` → `probe.py` → `creds.py` → `scaffold.py` → `verify_platform.py`
>
> 每一步都有**退出判据**。没到判据就不要往下走 —— 后面会全部返工。

---

## 阶段 0：准备（不碰客户端）

```bash
# 确认工具有没有
node --version        # 拆 asar 需要
python --version      # ≥ 3.10
```

**判据**：能跑 `node -e "console.log(1)"`。

---

## 阶段 1：侦察（recon.py）

```bash
python scripts/recon.py "<安装目录>" --min-kb 1 --top 24 --out recon.json
```

### 三个必须避开的坑（都实测踩过）

**坑 1：Launcher 不是应用**
```
< 1 MB 的「主程序」都是引导器
→ 读 install.ini / product.json 拿 installDir
```

**坑 2：按体积选文件是错的**
```
第一次跑：取了最大 8 个 js → 全是 xlsx / babel / monaco
paths 命中数: 0
```
脚本已改成按**路径关键词打分**（`main` +10 / `xlsx` −15）。

**坑 3：`--min-kb 200` 会漏掉所有业务文件**
```
Loomy 的关键文件只有 605 字节：
    auth-session-runtime.js    605 B   ← 凭证存储名在这里
    auth-session-controller.js 3.8 KB
```
**用 `--min-kb 1`。**

### 判据

- [ ] `recon.json` 里有 ≥ 3 个 host
- [ ] `paths` 里能挑出 ≥ 5 条像接口的路径
- [ ] 找到业务代码目录（常在 `app.asar.unpacked/`，**不在**主进程）

---

## 阶段 2：探针（probe.py）

```bash
python scripts/probe.py --recon recon.json --top 6 --out probe.json
```

### 判据表（全部价值在这）

| 返回 | 结论 | 下一步 |
|---|---|---|
| 200 + 数据 | 公开端点 | 直接接 |
| **401 / 403** | **端点存在**，缺凭证 | 去设计授权 |
| **404** | 路径错 / host 不提供 | 换 host 或路径 |
| 405 | 方法错 | 换 GET/POST |
| **400 + 业务码** | **鉴权已通过**，只是参数错 | ★ 最理想 |
| 超时 | 网络/需代理 | 先解决网络 |

> `400 参数错` 比 `200` 更有价值：它证明你的凭证被接受。

### 必须确认的三件事

- [ ] 哪个 host 活着
- [ ] 走哪种鉴权（Bearer / 双头 / 自定义签名）
- [ ] 是否需要**请求签名**（403 + "Signature invalid"）

---

## 阶段 3：凭证（creds.py）★ 最容易卡住

```bash
python scripts/creds.py --source "<unpacked 目录>" \
    --dir "<数据目录>" --verify-url "<只读接口>"
```

### 三条铁律

**铁律 1：不要读进程内存。**
内存扫描必须让客户端开着 —— 用户关了你就抓不到，不能作为产品能力。
（实测被拒绝：「我不开你怎么读？」）

**铁律 2：产品名 ≠ 数据目录名。**

| 产品 | 猜的目录 | 真实目录 |
|---|---|---|
| Qoder | `%APPDATA%\Qoder CN` | `%APPDATA%\com.qodercn.app.stable` |
| Loomy | `%APPDATA%\loomy` | `C:\Users\Public\Loomy\<sha256(user)[:12]>\userData` |

**必须读源码里的路径构造函数**，不能猜。

**铁律 3：配置有两个位置时，读运行时那一份。**

这是 Loomy 上最大的坑：

```
resources/opencode-home-template/opencode.json     6.9 KB   模板
    baseURL: https://ossptest.voicecloud.cn/...      ← 测试环境

<configRoot>/opencode/opencode.json                21.3 KB  运行时 ★
    baseURL: https://loomyad.xunfei.cn/api/v1        ← 真实
```

读模板 → 所有请求返回 `100002 登录已失效` →
**看起来像凭证过期，其实是地址错了**。

判断方法：比大小（模板小、运行时大）、比 mtime（运行时新）。

### 追凭证的四跳（照这个顺序）

```
① grep "Authorization" / "Bearer"      → 找到鉴权头拼法
② grep "updateAuthSession" / "login"   → 找到登录后谁存
③ 读那个 controller                    → store.get('session')
④ 追 store 从哪来                      → new Store({ name: 'xxx' })
   → <userDataPath>/xxx.json
   再追 userDataPath 怎么算的           → 可能是 sha256(用户名) 之类
```

### 判据

- [ ] 知道凭证**文件名**（通常是 `<store name>.json`）
- [ ] 知道**完整路径**（含自定义前缀）
- [ ] 知道**是否加密**（开头 `v10` = DPAPI；`{` = 明文）
- [ ] 如果是明文，**验证过是否有效**

> 明文 + 有效 → 用户零操作，最理想
> 明文 + 失效 → 引导用户在客户端登录一次，再扫
> 加密 → 走设备流授权
> 都没有 → 只能让用户手动粘一次（**最后兜底**）

---

## 阶段 4：骨架（scaffold.py）

```bash
python scripts/scaffold.py --platform <id> --label "<Name>" \
    --realm cn --base-url "<真实 baseURL>"
```

生成 11 个文件，**直接能过结构检查**。然后填 3 个 TODO：

```
TODO 1  core/endpoints.py   真实路径与鉴权头
TODO 2  core/client.py      按上游响应结构调整
TODO 3  platform_gateway.py 有任务就登记 task_runners()
```

### 判据

```bash
python scripts/verify_platform.py --platform <id>
# 结构检查 1-8 项全绿
```

**结构全绿就说明骨架对了**，后面只差真实数据。

---

## 阶段 5：验证（verify_platform.py）

```bash
python scripts/verify_platform.py --platform <id> \
    --base http://127.0.0.1:8790 --browser
```

19 项检查，分三组：

| 组 | 检查什么 | 需要凭证 |
|---|---|---|
| 1-8 结构 | 契约、11 项操作、任务、探测、卡片、幂等、名词基线 | ❌ |
| 9-11 运行 | 平台列表、概览卡片、账号隔离 | 需服务 |
| 12 浏览器 | 卡片渲染、图标、切换后数据正确、零报错 | 需服务 |

### 三个必须自己测的（脚本测不出来）

**① 三协议真调**
```bash
/v1/chat/completions → 拿到内容
/v1/messages        → 拿到内容（不是空）
/v1/responses       → object == "response" 且有 output_text
```

**② 模型清单只含可调模型**
逐个模型试一次短回复，把返回空的/404 的筛掉：

```
DeepSeek-V4.1-Flash  finish=stop   content='OK'      ✅
spark-x2.6           finish=length content=''        ❌ 思考链吃满
Hy-Image-3.5         HTTP 404                        ❌ 图像模型
```

**③ 平台切换后数据真的变**
浏览器里逐平台切换，看账号池行数与余额是否跟着变。

---

## 阶段 6：易漏的四件事

见 **[INTEGRATION-CHECKLIST.md](INTEGRATION-CHECKLIST.md)**：图标 / 定时任务 / 哨兵 / 余额实时。

---

## 全流程一句话

```
拆包 → 找 host → 探针 → 找凭证 → 骨架 → 验证 → 补齐四件事
  ↑__________________ 这四步都不需要凭证 __________________↑
```

**前四步（能完成 80%）全都不需要用户提供任何东西。**
这是关键 —— 拿到凭证才敢动手，等于把「用户愿不愿意给」当前提，
而现实中这个前提经常不成立。

---

## 附：实测时间参考（Loomy，从零到交付）

| 阶段 | 耗时 | 主要卡点 |
|---|---|---|
| 1 侦察 | ~15 min | recon 脚本两次失败（体积选文件、阈值太高） |
| 2 探针 | ~5 min | 无 |
| 3 凭证 | ~40 min | 读错配置（模板 vs 运行时）→ 误判凭证失效 |
| 4 骨架 | ~10 min | REALMS 元组少逗号、docstring 反斜杠 |
| 5 验证 | ~25 min | responses 结构未转换、思考链吃满 max_tokens |
| 6 补齐 | ~15 min | 前端缺平台分支 |

**总计约 2 小时**。其中 40 分钟浪费在「读错配置」——
这条已经写进阶段 3 的铁律 3。
