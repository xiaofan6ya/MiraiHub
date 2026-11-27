# MiraiHub 1.1.6

> 这是一次**后台彻底换代**：新版 Vue 3 控制台从「并行试验」变成唯一入口，
> 旧的手写 JS 控制台（`admin/console`，1.8 万行）整个删除。
> 同时补回了公开注册入口的官方登录、Android 端的原生设置能力。

## 核心变更

### 1. 后台只剩一套：`/admin` 就是新版

上一版（1.1.5）是双轨：`/admin` 走旧版，`/admin/v2` 走新版，靠 `MIRAIHUB_CONSOLE`
和 `?ui=v1` 切换。这一版把双轨拆了：

| | 1.1.5 | 1.1.6 |
|---|---|---|
| `/admin` | 旧控制台 | **新版（Vue 3）** |
| `/admin/v2` | 新版 | 301 → `/admin`（老书签不作废） |
| `?ui=v1` | 回退旧版 | 失效 |
| `MIRAIHUB_CONSOLE` | 选版本 | 失效 |
| `admin/console` | 在用 | **已删除** |

新版是 25 个页面 / 9 个分组、深浅双主题、中英双语、全程离线（字体图标图表都打进包里）。
旧的 `admin/static/login.html` 与 `join.html` 也一并删除 —— 登录页和注册页现在
和后台是同一次构建的产物。

### 2. 公开注册入口：官方登录接力回来了

WorkBuddy 的公开注册本来有两条路：手机号验证码、**官方登录接力**（跳官方授权页，
用户在同一台手机上登完，服务端轮询入库）。后端一直在，但新版注册页只认
`qr / auth_url / sms` 三种模式，把后端声明的 `official` 当成了未知模式 —— 于是
官方登录入口整条消失，只剩短信。

现在补回：

- 平台卡片显示「官方授权登录 · 支持手机号」
- 官方模式下文案改为「点下面的按钮打开官方授权页，用同一台手机完成登录，结果会自动出现」
- 两边可以互相切：短信页有「改用官方授权登录」，授权页有「改用手机号验证」
- 顺手补回验证码约束（8 位上限、只收数字、4–8 位校验），旧版有、新版退化成「非空即可」

### 3. Android：原生设置能力回来了

旧控制台的移动端设置面板里有四个按钮：重启本地服务、通知设置、电池优化、自启动。
它们通过 `window.WBAndroid` 桥接调 Java 侧（`MainActivity.MobileBridge`）。

后台换代后那块面板不再被加载，能力等于丢了。这一版在**设置 → Android 本机**里
补回完整一张卡：服务状态、服务地址、最近健康检查、通知/电池/后台限制/保活
四项状态，加四个操作按钮。卡片只在 APK 的 WebView 里渲染，浏览器里不出现。

### 4. APK 包瘦身

`build_android.py` 之前会把整个 `admin/console`（2.66 MB / 64 文件）复制进 APK，
再用 `mobile_html()` / `mobile_js()` 改写一份页面 —— 但 APK 的 WebView 加载的是
`http://127.0.0.1:<port>/admin`（一个 URL，不是本地文件），那份改写产物从来没被用过。

现在连同 `android/web/mobile.{css,js}`、`mobile_settings.js`、`settings.html` 一起删除，
打包脚本里那 170 行改写逻辑也去掉了。APK 里现在只有一套前端（新版），不会再出现
「APK 界面和网页界面不一样」的分叉。

### 5. 修掉的几个小问题

- 登录成功后 `location.replace('/admin/v2/')` → `/admin/`（少一跳 301）
- 侧栏底部还留着「切回旧版」按钮（指向已失效的 `?ui=v1`）→ 删除
- 桌面版打包冒烟的页面检查还在找旧控制台的 `#appVersion` 元素 → 改为兼容新旧
- `tests/test_public_entry.py` 还在断言已下线的 `doubao` 平台 → 去掉

## 已知问题

后台功能对齐还有一批没做完（内部审计出 86 项，其中 20 项优先）。影响最大的几处：

- **客户端参数页**：本机探测的后端接口都活着（`client-paths` / `detect-all` /
  `client-profile/export|import`），新版前端还没接 —— 网络盘、绿色版找不到客户端时
  没有人工兜底入口
- **每平台专属账号列**：Trae / Qoder / Loomy / CodeArts / MiniMax 的专属字段
  （额度总量、套餐、登录态有效期等）被统一成 9 列通用表
- **账号池**：不支持服务端分页，一次性拉全量在前端筛排
- 完整清单见 `docs/ui-v2/MERGE-GAP-REPORT.md`

## 升级说明

- **桌面版**：直接覆盖安装。数据目录不变。
- **Docker**：`git pull` 后 `docker compose up -d --build`。旧版 MySQL 数据不会自动迁 SQLite。
- **Android**：覆盖安装。如果提示签名冲突，先卸载旧版（本地账号库会丢，先在电脑上导出备份）。
- **从 1.1.5 升上来的**：如果你在 `.env` 里写过 `MIRAIHUB_CONSOLE=v1`，可以删掉了 ——
  这个变量已经没有任何作用。

## 下载

| 平台 | 文件 |
|---|---|
| Windows x64 | `miraihub-1.1.6-windows-x64.exe` |
| macOS arm64 / x64 | `miraihub-1.1.6-macos-*.zip` |
| Android arm64 | `dist/android/miraihub-1.1.6-android-arm64.apk` |

每个产物都带 `.sha256` 校验文件。
