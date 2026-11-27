# MiraiHub 前端 v2 重设计方案

给执行模型（低成本模型）用的完整设计稿 + 分步执行提示词。按顺序读、按顺序做。

## 文件清单

| 文件 | 内容 | 谁读 |
|---|---|---|
| [01-direction.md](01-direction.md) | 设计方向、色彩、字体、间距、圆角、动效 | 每一步都读 |
| [tokens.css](tokens.css) | 可直接复制进项目的设计令牌（浅色/深色） | S01 |
| [03-shell.md](03-shell.md) | 信息架构、导航、顶栏、路由、响应式 | S04 |
| [04-components.md](04-components.md) | 公共组件清单与规格 | S05 及之后 |
| [05-patterns.md](05-patterns.md) | 页面模板、状态、数字/时间格式、文案规则 | 每一步都读 |
| [06-pages-a.md](06-pages-a.md) | 概览组、平台组 | 对应页面 |
| [06-pages-b.md](06-pages-b.md) | 账号组 | 对应页面 |
| [06-pages-c.md](06-pages-c.md) | 调用组 | 对应页面 |
| [06-pages-d.md](06-pages-d.md) | 配置 / 接入 / 智能体 / 公开入口 / 系统 / 登录 | 对应页面 |
| [07-prompts-setup.md](07-prompts-setup.md) | P00 公共前缀 + S01–S05 基建提示词 | 执行 |
| [08-prompts-pages.md](08-prompts-pages.md) | S06–S30 页面提示词 | 执行 |
| [09-acceptance.md](09-acceptance.md) | 验收清单、上线切换步骤 | 收尾 |

## 关键决策（已定，执行时不要改）

1. **新建独立 SPA，不改旧控制台。** 新代码在 `admin/ui/`，构建产物在 `admin/ui/dist/`。
   ~~先挂在 `/admin/v2/` 与旧版并行~~ → **已于 2026-11-27 完成合并**：`/admin` 现在就是这套前端，
   旧控制台（`admin/console`）与 `?ui=v1` / `MIRAIHUB_CONSOLE` 回退开关全部下线，
   `/admin/v2/*` 只保留 301 兼容跳转。功能缺口清单见 [MERGE-GAP-REPORT.md](MERGE-GAP-REPORT.md)。
2. **技术栈**：Vite + Vue 3（`<script setup>` + TypeScript）+ Naive UI + Pinia + vue-router（hash 模式）
   + ECharts（vue-echarts）+ lucide-vue-next 图标 + marked + DOMPurify。
   理由：Naive UI 自带完善的表格/表单/抽屉/深色主题/中文语言包，主题可用 JS 令牌整体覆盖，低成本模型最不容易写偏。
3. **离线可用**：所有字体、图标、库都打进包里，禁止 CDN（原项目明确要求断网可用）。
4. ~~构建产物提交进仓库~~ → **不再入库**：`admin/ui/dist/` 被 `.gitignore` 的 `dist/` 规则排除，
   由 `scripts/build_frontend.py` 在 EXE/APK 打包前执行 `npm run build` 生成后收录
   （见 `BUILD.md`）。手改前端后记得本地 `pnpm build`，否则 `/admin` 直接 404 并打日志。
5. **后端接口一律不改**。新前端只消费现有 `/api/*`。唯一的后端改动是 `/admin` 的静态服务路由
   （`admin/server.py:admin_index` / `ui_asset` / `ui_file`）。
6. **契约先行**：每个页面动手前，执行模型必须先从旧代码里抽出该页的接口与功能清单，写进
   `docs/ui-v2/contracts/<page>.md`，再按设计稿实现。功能清单就是验收标准，不允许丢功能。

## 旧前端位置（参考用，Web 路由已下线）

> **重要**：`admin/console/` 已不再被任何 Web 路由服务（`/admin/assets/*` 现在指向
> `admin/ui/dist/assets/`），但它**仍是 Android APK 的构建输入** ——
> `scripts/build_android.py:218` 会 `copytree(admin/console)` 再用 `mobile_js()` /
> `mobile_html()` 改写成本地页面。删这个目录之前先改打包脚本。

- 页面结构：`admin/console/index.html`（24 个 `<section id="panel-xxx">`）
- 主逻辑：`admin/console/console-app.js`（1.8 万行），`const TABS` 在第 1264 行
- 辅助脚本：`platform-switch.js`（当前平台上下文 `window.WBPlatform`）、`dashboard.js`、`market.js`、
  `agent.js`、`growth_center.js`、`traffic.js`、`platform-actions.js`、`ip-management.js`、`key-ip.js` 等
- 旧登录页 `admin/static/login.html` 与旧注册页 `admin/static/join.html` **已删除**
  （登录页/注册页随新版一起构建；`/static/login.html` 301 回 `/admin`，
  `/static/join.html` 仍走 `/join` 同一份产物与门禁）
- 后端：`admin/server.py`（`/admin` 门禁、CSP）、`admin/routers/*.py`、`admin/platform/**/routers/`
- 截图：`assets/screenshots/*.png`
