# 功能契约：更新与备份（Maintenance）

- **页面 key / 路由**：`maintenance` → `/maintenance`（hash 路由：`/admin/v2/#/maintenance`）
- **设计稿**：`docs/ui-v2/06-pages-d.md` P24（模板 C：左锚点目录 + 区块卡片）
- **旧版位置**：`admin/console/index.html:2696-2700`（`#panel-maintenance`）、`admin/console/review-improvements.js`（`WBReview.loadUpdate/backup/doRestore/doRestartService/editBackupDir`）、`admin/console/desktop.js`（版本更新对话框，1–160 行）、`admin/console/release-notes.js`（Markdown 渲染）
- **后端**：`admin/routers/maintenance.py`、`admin/routers/releases.py`、`desktop/updates.py`、`desktop/version.json`、`desktop/app.py`（pywebview 桥 `download_update/apply_update/update_status`）
- **新版前端**：`src/pages/MaintenancePage.vue` + `src/api/maintenance.ts`

---

## 一、区块（按旧版出现顺序）

| # | 区块（旧版原中文标题） | 新版卡片标题 | 说明 |
|---|---|---|---|
| 1 | 软件版本 / 检查更新 | 当前版本 | 版本号 + 部署方式 + 检查更新；有新版本时显示版本号、发布说明（Markdown）、升级操作 |
| 2 | 备份数据 | 数据库备份 | 只读位置信息（类型 / 文件 / 大小 / 可写）+ 备份位置可改（模板 C 的保存只作用于这一张卡） |
| 3 | 备份数据 | 备份记录 | 立即备份 + 备份列表（时间 · 大小 · 下载） |
| 4 | 升级与重启 | 恢复与重启 | 旧版同一卡片里的「恢复备份」「立即重启」；两者都要 `ADMIN_ALLOW_FS_OPS=1` 才可用 |

页面无表单草稿的区块一律 `hideSave`（模板 C 的保存按钮只用于「备份位置」）。四张卡片都常驻渲染（数据未到时显示「正在读取备份信息…」），因为 `SettingsLayout` 的锚点观察器只在挂载时绑定一次。

---

## 二、数据展示

### 2.1 当前版本（`GET /api/maintenance/update`）

| 展示项 | 来源字段 | 格式 / 规则 |
|---|---|---|
| 当前版本 | `version`（string，如 `1.1.4`） | `v{version}`，等宽数字 |
| 部署方式 | 前端推断（见 §2.5） | 分段控件：Docker 容器 / 源码部署 / EXE 桌面版 / APK 安卓版 |
| 最新正式版 | `latest.tag` | 无则显示「暂未获取」 |
| 状态徽章 | `error` / `latest` / `available` | `error` 或 `latest=null` → warn「版本检查未完成」；`available` → brand「有新版本可升级」；否则 neutral「暂无新版本」 |
| 检查失败原因 | `error`（string，可空） | 原样显示，例如「检查更新失败（HTTP 403），稍后可重试」 |
| 发布说明 | `GET /api/app/releases` 的 `latest.body` | `marked`（gfm+breaks）→ `DOMPurify` 白名单 → 插入；外链加 `target=_blank rel=noopener noreferrer` |
| 说明链接 | `releases_url` | 外链「版本与更新公告」 |

### 2.2 升级操作

| 部署方式 | 展示什么 | 来源 |
|---|---|---|
| Docker 容器 | 命令块（默认页签）：`commands.docker` 或（勾选国内网络）`commands_cn.docker` | `update` |
| 源码部署 | 命令块：`commands.linux` / `commands_cn.linux` | `update` |
| EXE 桌面版 | 在 pywebview 外壳内：`latest.installable` 为真 →「下载并安装更新」；否则只给 Releases 链接 + 说明 | `releases.latest` |
| APK 安卓版 / 浏览器 | `latest.asset.browser_download_url` 外链「下载最新版 EXE / APK」 | `releases.latest` |

- 「国内网络环境（pip 走清华镜像）」勾选项只在 `commands_cn` 存在时出现（旧版 `maintenance-cn-toggle`）。
- 命令来自后端，前端**不执行**任何命令；只有 EXE 外壳才可能安装更新，且必须 `installable` 为真（有安装包 + SHA256 校验文件/摘要）。
- 部署方式默认值：桌面外壳 → `exe`；安卓外壳（`window.WBMobile`）→ `apk`；否则沿用上次选择（localStorage `mh.maintenance.deploy`），首次为 `docker`（旧版默认页签）。检测不到容器/源码差异时明确写「服务端部署无法自动区分容器与源码」。

### 2.3 数据库备份（`GET /api/maintenance/update` 携带的位置信息）

| 展示项 | 来源字段 | 规则 |
|---|---|---|
| 数据库类型 | `database` | 大写（SQLITE / MYSQL） |
| 数据库文件 | `db_path` | 等宽；MySQL 显示 `host:port/db` |
| 数据库大小 | `db_size` | `formatBytes`（B/KB/MB/GB，1 位小数）；`db_exists=false` → `—` |
| 备份存放位置 | `backup_dir` | 可编辑（`fs_ops=true` 时给输入框 + 保存），否则等宽只读 |
| 位置是否默认 | `backup_dir_is_default` | false 时出现「恢复默认位置」 |
| 位置可写 | `backup_dir_writable` | false → danger Badge「不可写」 |
| 私有提示 | `backup_note` | 卡片底部 12px 说明（旧版「备份包含账号凭据和 API Key，请私密保存」） |

### 2.4 备份列表（时间 · 大小 · 下载，「备份记录」卡）

后端**没有**备份列表接口：`POST /api/maintenance/backup` 生成临时文件后以 `FileResponse` 返回，响应结束即删除（`BackgroundTask(path.unlink)`），历史文件不在服务器留存。因此列表 = **本次页面会话内**发起的备份记录（内存态，不落 localStorage，避免刷新后出现过期死链）。

| 列 | 内容 | 格式 |
|---|---|---|
| 时间 | 备份发起完成的时刻 | `fmtTime`（今天 `HH:mm:ss`） |
| 大小 | 响应 blob 字节数 | `formatBytes` |
| 下载 | 内存中的同一份 blob 再存一次 | 无网络请求；已在刷新后丢失时该行不会存在 |

空状态：「还没有备份。立即备份会把数据库快照下载到本机。」

### 2.5 恢复与重启

| 展示项 | 来源 | 规则 |
|---|---|---|
| 运维开关状态 | `fs_ops`（bool） | false 时两个按钮禁用，并显示关闭原因与开启方法（`ADMIN_ALLOW_FS_OPS=1`，生产环境路由整体 404） |
| 备份格式 | `backup_supported`（bool） | 仅 SQLite 支持在线备份/恢复；MySQL 显示 `mysql_backup_command` |

---

## 三、交互

| 交互 | 中文文案 | 接口 | 确认 / 结果 |
|---|---|---|---|
| 检查更新 | 「检查更新」 | `GET /api/maintenance/update` + `GET /api/app/releases` | 无确认；按钮 loading；完成无 Toast（卡片内显示状态徽章） |
| 版本与更新公告 | 「版本与更新公告」 | 外链 `releases_url` | 新窗口打开 |
| 切换部署方式 | 「Docker 容器 / 源码部署 / EXE 桌面版 / APK 安卓版」 | 无 | 记住选择（localStorage） |
| 勾选国内网络 | 「国内网络环境（pip 走清华镜像）」 | 无 | 命令块切到 `commands_cn` |
| 复制命令 | 「复制」 | 无 | `CodeBlock` 自带，成功后 Toast「已复制」 |
| 下载并安装更新（EXE 外壳） | 「下载并安装更新」 | `pywebview.api.download_update(release_id)` | 确认「下载并安装更新？」正文「将下载 v{tag} 安装包并校验 SHA256，校验通过后再重启安装。」｜成功 Toast「已开始下载更新」；失败 Toast「下载更新失败：<原因>」 |
| 重启并安装更新（EXE 外壳） | 「重启并安装更新」 | `pywebview.api.apply_update()` | 确认「重启并安装更新？」正文「客户端会关闭并替换可执行文件，更新期间界面不可用。」｜失败 Toast「安装更新失败：<原因>」 |
| 下载最新版安装包（浏览器 / APK） | 「下载最新版 EXE」/「下载最新版 APK」 | 外链 `asset.browser_download_url` | 无确认（不触碰本机文件系统） |
| 立即备份 | 「立即备份」 | `POST /api/maintenance/backup` | 确认「立即备份数据库？」正文「备份含账号凭据与 API Key，请私密保存。备份文件会直接下载到本机。」｜成功 Toast「已备份并下载：{文件名}（{大小}）」；失败 Toast「备份失败：<原因>」 |
| 再次下载（列表行） | 「下载」 | 无（内存 blob） | 成功 Toast「已保存：{文件名}」；失败 Toast「保存失败：<原因>」 |
| 修改备份位置 | 「保存」 | `PUT /api/maintenance/backup/dir` `{path}` | 未修改时保存禁用；成功 Toast「已更新备份位置」；失败 Toast「保存备份位置失败：<原因>」；`fs_ops=false` 时隐藏保存 |
| 恢复默认位置 | 「恢复默认位置」 | `PUT /api/maintenance/backup/dir` `{reset:true}` | 确认「恢复默认备份位置？」｜成功 Toast「已恢复默认位置：{dir}」 |
| 恢复备份 | 「恢复备份」 | `POST /api/maintenance/restore`（`application/octet-stream` 原始字节） | 确认「恢复备份？」正文「将用上传的文件**整体替换**当前数据库（账号、Key、日志、配置全部回到备份时刻），恢复前会自动保存一份安全副本。」要求输入「恢复」｜成功 Toast「已恢复备份：账号 {n} · Key {m}；恢复前副本 {safety_copy}」；失败 Toast「恢复备份失败：<原因>」 |
| 立即重启 | 「立即重启」 | `POST /api/maintenance/restart` `{confirm:true}` | 确认「立即重启服务？」正文「服务约 2 秒内重启，期间请求会短暂失败。」｜成功 Toast「已触发重启：服务约 2 秒后恢复，稍后刷新本页」；429 时 Toast 后端文案 |

危险操作（备份下载 / 恢复 / 重启 / 安装更新）**全部**走 `useConfirm()`；所有结果用 Toast（`useToast`），错误文案 `<动作>失败：<原因>`。

---

## 四、接口

| 方法 | 路径 | 参数 / 请求体 | 响应用到的字段 | 调用时机 | 后端处理函数 |
|---|---|---|---|---|---|
| GET | `/api/maintenance/update` | 无 | `version` `latest{tag,name,url,date}` `available` `error` `releases_url` `database` `backup_supported` `fs_ops` `db_exists` `db_size` `db_path` `backup_dir` `backup_dir_default` `backup_dir_is_default` `backup_dir_writable` `desktop_mode` `commands{docker,linux}` `commands_cn{docker,linux}` `backup_note` `mysql_backup_command` | 进入页面 + 「检查更新」 | `admin/routers/maintenance.py:update_info`（→ `_backup_location`、`admin/routers/releases.py:service`） |
| GET | `/api/app/releases` | 无 | `latest{id,tag,name,body,date,url,asset{browser_download_url,size},installable}` `releases[]` `error` `releases_url` | 与上同一次刷新 | `admin/routers/releases.py:releases`（→ `desktop/updates.py:UpdateService.check`） |
| POST | `/api/maintenance/backup` | 无（空体） | 二进制 `FileResponse`：`Content-Disposition: attachment; filename=workbuddy-backup-YYYYmmdd-HHMMSS.db` | 「立即备份」 | `admin/routers/maintenance.py:backup` |
| GET | `/api/maintenance/backup/info` | 无 | 同 `_backup_location` 字段 | 备份位置保存后的回读 | `admin/routers/maintenance.py:backup_info` |
| PUT | `/api/maintenance/backup/dir` | `{path: string}` 或 `{reset: true}` | `ok` `backup_dir` `backup_dir_default` `backup_dir_is_default` | 「保存」/「恢复默认位置」 | `admin/routers/maintenance.py:set_backup_dir`（`_fs_guard`） |
| POST | `/api/maintenance/restore` | 原始字节（`application/octet-stream`，≤512MB） | `ok` `tables{accounts,api_keys,usage_logs,model_configs}` `safety_copy` `note` | 「恢复备份」 | `admin/routers/maintenance.py:restore`（`_fs_guard` → `_restore_sqlite_bytes`） |
| POST | `/api/maintenance/restart` | `{confirm: true}` | `ok` `message` | 「立即重启」 | `admin/routers/maintenance.py:restart_service`（`_fs_guard` + 60s 频控 + `os._exit`） |
| GET | `/api/app/version` | 无 | `version` `releases_url` | 不进维护页（外壳/登录页用） | `admin/routers/releases.py:version` |

**降级路径**（都必须按只读信息渲染，不能当成功能可用）：

- `GET /api/app/releases` → **503**：生产镜像按最小面构建，不含 `desktop/updates.py`（`releases.py:service` 抛 503「当前部署不包含客户端更新模块…请用镜像升级」）。此时 `/api/maintenance/update` 的 `error` 里也是同一句原因 → 徽章 warn、发布说明区显示 Empty + Releases 外链，命令块隐藏（`commands=null`）。
- `fs_ops=false`（`ADMIN_ALLOW_FS_OPS` 未开）：`PUT /backup/dir`、`POST /restore`、`POST /restart` 返回 **409**（非生产）或 **404**（生产）。页面据 `fs_ops` 预先禁用按钮并写清开启方法，不靠点下去才报错。
- 非 SQLite（`backup_supported=false`）：「立即备份」禁用，显示 `mysql_backup_command` 命令块。
- 「备份下载」是 `POST`，而 `api/http.ts` 的 `download()` 只支持 GET（该文件不在本任务改动范围）→ 在 `src/api/maintenance.ts` 内实现 `downloadBackup()`：复用 `resolveUrl` / `readCookie(CSRF_COOKIE_NAME)` / `CSRF_HEADER_NAME` / `filenameFromContentDisposition` / `messageFromPayload`，页面仍然不直接 `fetch`。差异已记入「实现对照」。

---

## 五、跨页联动

- **平台上下文**：本页不依赖 `X-Platform`（`/api/maintenance/*`、`/api/app/*` 均不读平台作用域；`/api/maintenance/credit-events` 才是积分流水的平台作用域接口），因此不订阅 `usePlatformStore().onChange`。页面进入时不因切平台重取。
- **版本提示**：侧栏版本号 / 启动提示由 `src/platform/version.ts` 负责（`/api/app/version` + `/api/app/releases`），本页不重复实现弹窗；只在卡片里给入口。
- **外链**：`releases_url`（`https://github.com/xiaofan6ya/MiraiHub/releases`）与安装包 `asset.browser_download_url` 统一 `target=_blank rel="noopener noreferrer"`。
- **401**：`http` 层的 `onUnauthorized()` 负责跳登录页，页面不处理。
- **URL 状态**：备份列表分页同步到路由 query（`useTableQuery('maintenance-backups')` → `?page=&size=`）；部署方式记 localStorage（不污染 URL）。

---

## 六、边界情况

| 场景 | 表现 |
|---|---|
| 首次加载 | 卡片 `loading` 转圈（`SettingsCard loading`），内容区一行 12px「正在读取版本与备份信息…」；不闪屏、不放骨架闪烁动画（01-direction §6） |
| 刷新中 | 保留旧数据，按钮 loading |
| 空（无新版本） | 不显示发布说明与升级命令区，徽章「暂无新版本」，只留检查更新与公告链接 |
| 空（无备份记录） | 备份列表 `Empty`：「还没有备份。立即备份会把数据库快照下载到本机。」 |
| 空（无发布说明） | `Empty` 紧凑态：「该部署没有提供发布说明」+ Releases 链接 |
| 错误（整页首次失败） | `ErrorBlock`「读取维护信息失败：<原因>」+「重试」 |
| 错误（releases 503/网络失败） | 只降级发布说明区与安装包入口，版本卡其余信息照常；原因用 `Empty` / 12px 文案显示 |
| `fs_ops=false` | 恢复 / 重启按钮禁用 + 说明「需要 ADMIN_ALLOW_FS_OPS=1」；备份位置只读 |
| `backup_supported=false` | 「立即备份」禁用，显示 `mysql_backup_command`；备份列表只读说明 |
| 桌面能力 | 仅 `isDesktop()`（`window.pywebview`）为真且 `installable` 时出现「下载并安装更新」；浏览器/APK 一律只给下载链接 |
| Markdown | 先 `normalizeReleaseNotes` 剥掉旧版 `\`\`\`markdown` 围栏，再 `marked` → `DOMPurify` 白名单（`script/style/iframe` 等全部丢弃，`on*` 属性丢弃，`javascript:` 链接去掉 href） |
| 手机宽度 | 分段控件与命令页签换行；KV 的标签/值上下堆叠（`KV` 自带）；表格按 DataTable 手机卡片模式 |
| 文本长度 | 发布说明 `max-height: 360px` 内滚动；`db_path` / `backup_dir` 允许换行（`word-break: break-all`） |

---

## 七、旧版有但设计稿没写到的功能

| 旧版功能 | 新版位置 | 说明 |
|---|---|---|
| 恢复备份（`WBReview.pickRestore/doRestore`） | 本页「恢复与重启」卡片 | 保留；确认文案同旧版（整体替换 + 安全副本），额外要求输入「恢复」 |
| 立即重启服务（`WBReview.doRestartService`） | 同上 | 保留；旧版重启后 8s 自动 `location.reload()`，新版改为 Toast + 提示手动刷新（避免后端重启期间刷新拿到 502 页） |
| 修改 / 恢复默认备份目录（`editBackupDir/resetBackupDir`） | 「数据库备份」卡片 | 保留；用页内输入框 + 模板 C 的保存/取消，不再用 `window.prompt` |
| 历史版本列表（`desktop.js` 的「历史版本」） | 不在本页 | 设计稿 P24 只要求最新版发布说明；完整历史在 Releases 外链（`releases_url`），避免把更新对话框整段搬进设置页 |
| 启动时自动弹更新对话框（`desktop.js startupPrompt`） | 外壳侧 | 由 `src/platform/version.ts` + 桌面壳负责，本页不弹 |
| 本地运行时（exe/apk）免登录标记 `desktop=1`、窗口按钮 | 外壳侧 | 与维护功能无关，已由 `src/layout/TopBar.vue` / `src/platform/desktop.ts` 承接 |

---

## 八、实现对照

| 功能 | 新版位置 | 状态 |
|---|---|---|
| 当前版本号 | `MaintenancePage.vue` 当前版本卡 | 已实现 |
| 部署方式（源码 / EXE / APK / Docker） | 同上，`Segmented` + `detectDeployMethod()` | 已实现（服务端部署无法自动区分容器与源码，默认 Docker 并允许手选、记住选择） |
| 检查更新 | 页头「检查更新」→ `getUpdateInfo()` + `getReleasesInfo()` | 已实现 |
| 新版本号展示 | 当前版本卡「最新正式版」+ 状态徽章 | 已实现 |
| 发布说明 Markdown（marked + DOMPurify） | `renderReleaseNotes()` → 发布说明区 | 已实现（503 时降级为 Empty + 外链） |
| 按部署方式执行升级操作 | `commandFor()` + CodeBlock；桌面壳走 `download_update/apply_update` | 已实现（浏览器/APK 只下载安装包；不伪造本机安装能力） |
| 国内网络环境命令（`commands_cn`） | 命令区勾选项 | 已实现 |
| 数据库信息只读（类型 / 路径 / 大小 / 可写） | 数据库备份卡 `KV` | 已实现 |
| 立即备份 | 「立即备份」→ `downloadBackup()` | 已实现（差异：接口是 POST，走 `api/maintenance.ts` 的 POST 版下载助手，原因见 §4） |
| 备份列表（时间 · 大小 · 下载） | `DataTable` + 会话内记录 | 已实现（差异：后端无列表接口，列表为本页会话内的备份记录；原因见 §2.4） |
| 备份下载（列表行再次保存） | 行内「下载」按钮（内存 blob） | 已实现 |
| 修改 / 恢复默认备份位置 | 备份卡输入框 + 保存 / 恢复默认位置 | 已实现（受 `fs_ops` 控制） |
| 恢复备份 | 恢复与重启卡 | 已实现（受 `fs_ops` 控制） |
| 立即重启服务 | 恢复与重启卡 | 已实现（受 `fs_ops` 控制） |
| 四种状态（首次 / 刷新 / 空 / 错误） | 各卡片 | 已实现 |
| 备份列表分页同步路由 query | `useTableQuery('maintenance-backups')` | 已实现 |
| 平台上下文订阅 | — | 不适用（本页接口不读 `X-Platform`，见 §5） |
| 危险操作确认 + Toast | `useConfirm()` / `useToast()` | 已实现 |
| Markdown 强制 DOMPurify | `renderReleaseNotes()`（白名单 + 链接硬化） | 已实现 |