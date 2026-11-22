# 03 外壳：信息架构、导航、顶栏、路由

## 1. 信息架构（24 个页面，9 个分组）

沿用旧版的分组（`admin/console/index.html` 第 1201–1209 行的 `data-group-tabs`）。页面名与旧版 `console-app.js:1388` 的 `_SUB_TAB_LABELS` 完全一致（已核对）。

| 分组 | 图标 (lucide) | 页面 key → 路由 → 名称 |
|---|---|---|
| 概览 | `LayoutDashboard` | overview → `/overview` 概览 · sentinel → `/sentinel` 风控哨兵 · stats → `/stats` 数据看板 |
| 平台 | `Boxes` | platforms → `/platforms` 平台管理 · gateway → `/gateway` 平台网关 |
| 账号 | `Users` | accounts → `/accounts` 账号池 · credits → `/credits` 积分到期 · journal → `/journal` 积分流水 · growth → `/growth` 成长计划 |
| 调用 | `Activity` | usage → `/usage` 使用记录 · logs → `/logs` 用量日志 · keys → `/keys` API Keys · ip → `/ip` IP 管理 |
| 配置 | `SlidersHorizontal` | models → `/models` 模型配置 · market → `/market` 模型广场 · groups → `/groups` 分组管理 · schedules → `/schedules` 定时任务 |
| 接入 | `Plug` | client → `/client` 客户端参数 · sync → `/sync` 同步设置 · alerts → `/alerts` 预警管理 |
| 智能体 | `Bot` | agent → `/agent` 智能体 |
| 公开入口 | `DoorOpen` | openentry → `/openentry` 公开注册入口 |
| 系统 | `Settings` | settings → `/settings` 设置 · maintenance → `/maintenance` 更新与备份 |

另有两个非页面入口：**登录设备**（抽屉，见 06-pages-d）、**退出登录**。

路由用 vue-router 的 **hash 模式**（`/admin/v2/#/accounts`），这样后端只需要服务一个 `index.html`。
兼容旧链接：如果 URL 带 `?tab=xxx` 且 xxx 在上表中，启动时 `router.replace('/xxx')`。
未知路由 → `/overview`。路由 `meta` 里写 `{ group, title, icon }`，导航、面包屑、命令面板都从路由表生成，不要手写第二份。

## 2. 页面框架

```
┌────────────┬──────────────────────────────────────────────────────────────┐
│ ◆ MiraiHub │ 平台上下文 [● WorkBuddy ▾]       ⌕ 搜索或跳转  Ctrl K   ● 运行中  ⧉ API 地址  ◐ │  ← 顶栏 56px
│            ├──────────────────────────────────────────────────────────────┤
│ ▣ 概览     │ 账号池                                          [刷新] [添加账号]│  ← 页头
│ ▢ 平台     │ 账号池   积分到期   积分流水   成长计划                          │  ← 分组内页签（同组 >1 页时）
│ ▣ 账号  ●  │──────────────────────────────────────────────────────────────│
│ ▢ 调用     │                                                              │
│ ▢ 配置     │   页面内容（最大宽 1440px，左对齐，左右 32px 边距）              │
│ ▢ 接入     │                                                              │
│ ▢ 智能体   │                                                              │
│ ▢ 公开入口 │                                                              │
│ ▢ 系统     │                                                              │
│            │                                                              │
│ ────────── │                                                              │
│ 登录设备   │                                                              │
│ v1.1.4 更新│                                                              │
│ 退出登录   │                                                              │
└────────────┴──────────────────────────────────────────────────────────────┘
```

### 2.1 侧栏（宽 224px，`--surface` 底，右侧 1px `--border`）

- 顶部品牌区 64px 高：`/api/app/icon` 图标 28px + 「MiraiHub」15px/600 + 下方「管理后台」12px `--fg-muted`。
- 9 个分组项：高 36px，左右内边距 12px，图标 18px + 文字 14px，间距 2px。
  - 悬停：`--surface-hover` 底。
  - 选中（当前路由属于该组）：`--brand-soft` 底 + `--brand` 文字和图标 + 字重 600。不要左侧竖条。
  - 点击分组 → 跳到该组**上次访问的页面**（存 `localStorage['mh.lastTab.<group>']`），没有则第一个页面。
- 分组项右侧可选**提示点**：账号组有冷却/熔断账号时显示 6px `--warn` 圆点；概览组哨兵检测到批次封控时显示 `--danger` 圆点（数据来自外壳的心跳，见 §4）。
- 底部（贴底，上方 1px `--border-soft`）：
  - 「登录设备」（`MonitorSmartphone` 图标）→ 打开登录设备抽屉。
  - 版本号行：「v1.1.4」+ 有新版本时右侧显示 `--brand` 小字「有更新」，点击打开更新对话框。
  - 「退出登录」（`LogOut` 图标），点击后弹确认「退出登录？」。
- 宽度 ≤1280px 时侧栏可收起成 64px 只显示图标（收起按钮在品牌区右侧，状态存 `localStorage['mh.sideCollapsed']`），收起时悬停显示 tooltip。

### 2.2 顶栏（高 56px，`--surface` 底，下方 1px `--border`，吸顶）

从左到右：
1. **平台上下文选择器**（关键全局控件，替代旧版 `platform-switch.js` 的切换器）：
   - 外观：平台 logo 20px + 平台名 + 账号数徽章 + `ChevronDown`。
   - 点击展开下拉（宽 320px）：列出所有平台，每行 = logo + 名称 + 区域（国内/国际）+ 可用账号数 / 总账号数 + 状态圆点；顶部有搜索框；当前项打勾。
   - 数据来源（已核对）：平台列表 `GET /api/platforms` → `{ items, default, loader }`，每个 item 带 `account_count`、`realm_counts`；
     同一平台的不同区域（如 Trae 国内版 / 国际版）是独立 item（带 `realm`）。当前平台存 `localStorage['wb_platform_current'] = {platform, realm}`，
     后端另有 `GET/PUT /api/platforms/current`。新版两处都要读写，与旧版 `platform-switch.js` 行为一致。
   - 选择后：写入 `usePlatformStore().current`，触发所有依赖平台的页面重新取数（旧版通过 `document` 事件 `wb-platform-change` 通知）。
   - 平台专属页面（旧版 `_PLATFORM_ONLY_TABS`，例如「成长计划」只属于某个平台）：切到不支持的平台时，自动跳回账号池，不要停在空页面。
   - 只在「依赖平台上下文」的页面显示（完整列表见 `contracts/platform-context.md`）；其他页面这个位置显示面包屑「分组 / 页面」。
2. 弹性空白。
3. **命令面板入口**：一个 240px 宽、看起来像搜索框的按钮「搜索或跳转」+ 右侧 `Ctrl K` 键帽。
4. **服务状态**：`●` 圆点 + 文字。`运行中`（ok）/ `连接中断`（danger）/ `重连中…`（warn）。判定方式与旧版 `topbarMode` 一致（执行时核对）。悬停 tooltip 显示上游地址与最后一次心跳时间。
5. **复制 API 地址**：图标按钮 `Copy` + 文字「API 地址」，点击复制 OpenAI Base URL，Toast「已复制 http://…/v1」。
6. **主题切换**：图标按钮，点击循环 浅色 → 深色 → 跟随系统，图标 `Sun` / `Moon` / `MonitorCog`。存 `localStorage['mh.theme']`；首次加载时若无该键，读取旧版键 `localStorage['wb_console_theme']`（值 `dark` / `light`）作为初始值。

### 2.3 页头（每个页面统一，组件 `PageHeader`）

```
账号池  ⓘ                                     [次要操作…] [主操作]
账号池   积分到期   积分流水   成长计划
```
- 第一行：标题 `--fs-h1`，右侧操作区。标题后可选 `ⓘ` 说明气泡（放旧版页面副标题那段长文字）。
- 第二行：同组页签（`Tabs` 线型），仅当该组有多于 1 个页面时出现。页签下方 1px `--border`，选中项 2px `--brand` 下划线。
- 页头不吸顶；页头下方 20px 开始内容。
- 不再出现旧版那种「顶栏标题 + 页面大标题 + 卡片标题」三重重复。

### 2.4 命令面板（`Ctrl/⌘ + K`）

- 居中浮层，宽 560px，顶部输入框，下面分两组结果：「页面」（24 个页面，显示分组名）与「操作」（添加账号、导入账号、创建 Key、复制 API 地址、切换主题、切换平台 → 子列表、退出登录）。
- 支持拼音首字母？不要求。只做中文/英文子串匹配。
- 上下键选择，回车执行，Esc 关闭。

## 3. 响应式

| 断点 | 行为 |
|---|---|
| ≥1280 | 完整布局，侧栏 224px |
| 1024–1279 | 侧栏默认收起为 64px，页面边距 24px |
| 641–1023 | 侧栏隐藏，顶栏左侧出现汉堡按钮，点击从左侧滑出抽屉（宽 264px，有遮罩）；命令面板入口缩成图标 |
| ≤640（手机，含 Android APK） | 同上；表格全部切换为卡片列表（见 04-components 的 `DataTable` 手机模式）；页头操作区只保留主操作，其余收进 `⋯` 菜单；统计卡一行 2 个 |

## 4. 外壳级的数据与轮询

外壳只做三件事，其余数据都由页面自己取：
1. **会话检查**：启动时调用旧版用来判断登录态的接口（执行时核对，旧版在 401 时显示 `#login`）。任何接口返回 401 → 跳转 `/admin`（由后端给登录页），并带上当前 hash 以便登录后返回。
2. **心跳**：每 15 秒调用一次轻量接口（沿用旧版顶栏状态所用接口），更新服务状态、侧栏提示点。页面不可见（`document.hidden`）时暂停。
3. **平台列表**：启动时加载一次，平台上下文选择器和所有页面共用（`usePlatformStore`）。

## 5. 桌面版与安卓版

- 桌面（pywebview）：顶栏整条加 `pywebview-drag-region` class，保留旧版 `desktop.js` 暴露的能力（更新检查、打开目录等），执行时把 `WBDesktop` 的调用封装进 `src/platform/desktop.ts`，只在 `window.pywebview` 存在时启用。
- 安卓：沿用 `android/web/*` 注入方式；新版需要在 ≤640px 下可用，并保留旧版 mobile.js 提供的设置入口（执行时核对）。
