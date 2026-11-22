# MiraiHub 前端 v2 重设计 - 执行计划与 Prompt

## 🎯 总体目标

为 MiraiHub 网关管理后台（admin/console）开发全新的 SPA 前端（`admin/ui/`），替代旧版，要求：
- **美观大气、易用实用、简约好看**
- 功能 100% 保留（1:1 复刻旧版，不删功能）
- 基于统一的设计系统与 TypeScript 组件库
- 完整的无障碍与国际化支持
- 生产级代码质量

---

## 📚 参考文档

所有功能详细规格已编写完成，位置：**`docs/ui-v2/`**

### 核心设计文档
- `01-direction.md` - 整体方向与架构
- `03-shell.md` - 应用壳层（导航、侧栏、认证）
- `04-components.md` - 通用组件库规范
- `05-patterns.md` - 交互模式与代码范式
- `06-pages-a.md / b / c / d` - 各页面设计稿与交互
- `07-prompts-setup.md` - 环境与工具配置
- `08-prompts-pages.md` - 分步实现 prompt 模板
- `09-acceptance.md` - 验收标准

### 功能契约（逐页详细规格）
**位置**: `docs/ui-v2/contracts/*.md`

| 页面 | 文件 | 职责 |
|------|------|------|
| P1 | overview.md | 概览 KPI、余额、使用统计、告警 |
| P2 | sentinel.md | 实时风控监控、拓扑动画、流量实时 |
| P3 | sessions.md | 会话管理、踢下线、拉黑设备 |
| P4 | platforms.md | 平台配置、账号绑定、性能基准 |
| P5 | gateway.md | 策略配置、拓扑演示、性能对标 |
| P6 | api-keys.md | API密钥 CRUD、使用记录、权限配置 |
| P7 | logs.md | 日志查询、高级过滤、导出、SSE 推送 |
| P8 | models.md | 模型管理、启用/禁用、批量操作 |
| P9 | settings.md | 系统设置、维护工具、备份恢复 |
| P10 | model-market.md | 模型行情展示、定价、可用性对比 |
| P11 | stats.md | 数据看板、KPI、图表（柱/面积/趋势）、排行 |
| P12 | account-analytics.md | 账号分析、按账号聚合、对标卡 |
| P13 | accounts.md | 账号管理、状态过滤、批量导入 |
| P14 | cache-analytics.md | 缓存分析、命中率、节省统计 |
| P15 | errors.md | 错误分类、告警规则、事件审计 |

**每份契约包含**:
- 页面结构与 ASCII 图
- 完整 API 规范（请求/响应）
- 数据展示规范（格式化、着色、动画）
- 交互流程（加载、搜索、编辑、删除等）
- 边界情况与容错
- 实现要点（性能、无障碍、i18n）

---

## 🛠️ 技术栈

### 前端框架与库
```
React 18.3+
TypeScript 5.x
Vite (构建)
pnpm (包管理)

UI 库：
- 通用组件：自研组件库（见 04-components.md）
- 图表：SVG 自绘（见 dashboard 图表规范）
- 动画：CSS + RAF（见 拓扑/粒子流)
- 表格：虚拟滚动（见 logs.md / accounts.md）
- 拖拽：FLIP 动画（见 gateway.md 排序）

数据流：
- 状态管理：React Context / Zustand（轻量级）
- 数据获取：fetch + 自定义 hook
- 缓存：内存 + localStorage（见各页契约）

样式：
- CSS-in-JS：Tailwind CSS + 自定义 CSS Variables
- 主题系统：亮/暗模式（见 05-patterns.md）
- 响应式：Mobile-first（见 边界情况）

无障碍 & i18n：
- ARIA 标签、语义 HTML、键盘导航
- i18n-js（或 react-i18next）
- 语言文件：zh-CN / en-US（见各页文案)
```

### 后端接口
- 基础路径：`/api/`
- 鉴权：Cookie 会话（登录页独立 HTML）
- 平台上下文：`X-Platform` 请求头
- 响应格式：`{ ok: bool, data?: any, detail?: string }`

---

## 📋 分阶段开发计划

### 第一阶段：基础设施（1-2 天）
**目标**: 建立项目脚手架、通用组件库、布局壳层

**任务**:
1. 创建 `admin/ui/` 目录结构
   ```
   admin/ui/
   ├── src/
   │   ├── components/       # 通用组件（Form, Table, Modal, Card 等）
   │   ├── pages/            # 页面组件
   │   ├── hooks/            # 自定义 hook（useFetch, useLocalStorage 等）
   │   ├── utils/            # 工具函数（格式化、API 调用、验证等）
   │   ├── styles/           # 全局样式 + CSS 变量
   │   ├── i18n/             # 国际化文案
   │   ├── App.tsx           # 应用入口
   │   └── main.tsx          # Vite 入口
   ├── public/
   ├── vite.config.ts
   ├── tsconfig.json
   └── package.json
   ```

2. 实现通用组件库（见 `04-components.md`）
   - Layout：主容器、侧栏、导航栏
   - Form：输入框、选择器、日期选择、切换
   - Table：基础表格 + 虚拟滚动
   - Modal / Drawer / Toast / Popover
   - 卡片、徽章、加载态、空状态
   - 数字格式化组件（`<FormattedNum>`, `<FormattedCredit>`等）

3. 实现应用壳层（见 `03-shell.md`）
   - 导航栏（LOGO、标签切换、用户菜单）
   - 侧栏（平台切换、页面列表、状态指示器）
   - 主容器（下拉刷新、错误边界、加载态）
   - 认证检查（重定向到登录页）

4. 实现全局状态与 hook
   - `useAuth()` - 登录态、用户信息
   - `useApi(url, options)` - 数据获取 + 缓存
   - `usePlatform()` - 平台上下文 + 事件监听
   - `useLocalStorage(key)` - 本地存储

5. 配置样式系统
   - CSS Variables（`--c-ok`, `--c-warn`, `--c-danger` 等）
   - Tailwind 配置（主题、响应式断点）
   - 亮/暗主题切换
   - 字体、间距、圆角常量

**验收**:
- 应用可启动（`vite dev`）
- 能进入首页（认证检查正常）
- 侧栏能显示所有页面标签
- 能切换平台（触发全局事件）

---

### 第二阶段：核心页面（3-4 天）
**目标**: 实现 P1、P2、P11（最复杂的 3 个页面）

**优先级排序**:

#### P1 - 概览页（overview.md）
1. 获取概览数据（`GET /api/overview?days=1`）
2. 渲染 KPI 卡片矩阵（7 张卡）
3. 渲染缓存卡片（可折叠表格）
4. 渲染账号监控卡片（聚合计数）
5. 渲染告警横幅（从风控状态）
6. 时间粒度切换（1/7/30 天）
7. 平台切换联动（重新获取数据）
8. 数字格式化与着色规则

**验收**:
- 页面能加载数据
- 卡片数值正确、格式化正确
- 时间切换能重新加载数据
- 着色规则生效（绿/黄/红）

#### P2 - 风控哨兵（sentinel.md）
1. 拓扑动画库集成（WBTraffic）
2. 获取告警状态（`GET /api/sentinel/status`）
3. 渲染 KPI 四格卡片
4. 渲染平台健康条（成功/失败堆叠）
5. 轮询最近请求（4 秒一次）
6. 粒子流动画（客户端→哨兵→平台）
7. 暂停/恢复按钮与对话框

**验收**:
- 拓扑动画能正常显示
- 实时流量能推送新粒子
- 暂停/恢复能调用 API
- `document.hidden` 时停止轮询

#### P11 - 数据看板（stats.md）
1. 获取看板数据（`GET /api/stats/dashboard`）
2. 渲染 KPI 7 张卡
3. 渲染性能健康条 + TOP 模型芯片
4. 实现堆叠柱状图（SVG）
5. 实现堆叠面积图（切换视图）
6. 实现多线趋势图
7. 实现排行条形图
8. 时间窗口/粒度切换

**验收**:
- 图表能正常渲染
- 鼠标悬停显示 tooltip
- 视图切换能平滑过渡
- 时间窗口/粒度切换能重新加载数据

---

### 第三阶段：数据管理页面（2-3 天）
**目标**: 实现 P4、P5、P6、P7、P8、P13（表格、表单、编辑等）

#### P4 - 平台管理（platforms.md）
- 平台列表 + 搜索、排序
- 右侧详情面板（账号表）
- 添加/编辑/删除 账号对话框
- 批量导入弹出框

#### P5 - 平台网关（gateway.md）
- 策略选择器（6 个无线按钮）
- 拓扑动画（简化版）
- 可拖拽排序平台列表（FLIP 动画）
- 性能对标卡（表格）

#### P6 - API 密钥管理（api-keys.md）
- 密钥列表表格（搜索、分页、排序）
- 创建/编辑对话框（10+ 字段表单）
- 密钥一次性显示窗口
- 使用记录表

#### P7 - 使用日志（logs.md）
- 高级搜索与多条件过滤
- 日志表格（虚拟滚动处理大数据）
- Keyset 分页（游标方式）
- SSE 实时推送新日志
- CSV 导出功能

#### P8 - 模型管理（models.md）
- 模型列表表格（聚合视图/平铺视图）
- 编辑模型对话框
- 批量启用/禁用按钮
- 从后端同步、测速等操作

#### P13 - 账号管理（accounts.md）
- 账号列表（搜索、过滤、排序、分页）
- 侧栏快速过滤（按状态聚合计数）
- 详情抽屉（点击行）
- 添加/编辑/批量导入账号
- 暂停/启用/删除操作

**通用需求**（所有表格页面）:
- 搜索防抖（500ms）
- 表格排序与分页
- 行操作（编辑、删除、切换状态等）
- 批量操作（多选、全选、批量操作按钮）
- 确认对话框（危险操作）
- 加载态、错误提示、空状态

**验收**:
- 表格能正常显示和分页
- 搜索、过滤、排序能生效
- CRUD 操作能正常进行
- 确认对话框正常工作
- 表单验证规则生效

---

### 第四阶段：分析与辅助页面（1-2 天）
**目标**: 实现 P9、P10、P12、P14、P15

#### P9 - 设置与维护（settings.md）
- 表单（平台排序、时区、语言等）
- 维护工具按钮（健康检查、清空统计、备份恢复）
- 操作确认与结果提示

#### P10 - 模型广场（model-market.md）
- 模型卡片矩阵（网格布局）
- 时间窗口切换（1h/24h/3d/7d）
- 搜索、平台/可用性过滤
- 排序选项（价格、速度、成功率）

#### P12 - 账号分析（account-analytics.md）
- 账号分析表格
- 点击行展示对标卡 + 趋势图
- 趋势图（请求+消耗双 Y 轴）

#### P14 - 缓存分析（cache-analytics.md）
- 概览卡片（命中率、节省、容量）
- 按模型分类表格
- 按平台分类表格
- 趋势图

#### P15 - 错误分析（errors.md）
- 错误分类分布（柱状图）
- 告警规则列表
- 创建/编辑规则对话框
- 规则测试功能

**验收**:
- 页面能正常加载和交互
- 图表正常显示
- 操作能调用对应 API

---

### 第五阶段：质量与优化（1 天）
**目标**: 完善无障碍、国际化、性能、兼容性

**任务**:
1. **无障碍 (WCAG 2.1 AA)**
   - 添加 ARIA 标签、role、aria-label
   - 确保键盘导航完整（Tab、Enter、Esc）
   - 颜色不单独依靠（文字/图标补充）
   - 表单标签与输入框关联

2. **国际化 (i18n)**
   - 提取所有文案到 i18n 配置
   - 支持 zh-CN / en-US（可扩展）
   - 日期/数字格式国际化

3. **性能**
   - 表格虚拟滚动（1000+ 行）
   - 图表一次性生成（避免逐帧 append）
   - 数据缓存 1-2 分钟
   - 路由懒加载（见 vite 配置）
   - Lighthouse 评分 > 90

4. **兼容性**
   - Chrome / Firefox / Safari 最新版
   - 响应式（Mobile 375px ~ Desktop 1920px）
   - 暗模式正常显示

5. **测试**
   - 单元测试（工具函数、格式化）
   - 集成测试（关键交互流程）
   - E2E 测试（Cypress 或 Playwright）
   - 覆盖率 > 80%

---

## 🎨 实现关键点

### 数字格式化
```typescript
// 见 overview.md / stats.md 的格式化规范
ovNum(n)      // 1.23B / 12.34M / 1.2K / 123
ovInt(n)      // 1,234,567（千分位）
ovCredit(n)   // 1,234.56（千分位 + 2位小数）
fmtTime(ts)   // YYYY-MM-DD HH:mm:ss（UTC+8）
pct(ratio)    // 98.40%（ratio * 100 + 2位小数）
```

### 着色规则
```typescript
// 成功率三档
success_rate >= 0.98 ? 'ok' : success_rate >= 0.90 ? 'warn' : 'danger'

// 余额警告
balance >= avg_daily * 10 ? 'ok' : balance >= avg_daily * 3 ? 'warn' : 'danger'

// 缓存命中率
hit_rate >= 0.10 ? 'ok' : hit_rate >= 0.05 ? 'warn' : 'neutral'
```

### API 请求统一模式
```typescript
// 见 07-prompts-setup.md
const response = await fetch(`/api/xxx`, {
  method: 'GET/POST/PUT/DELETE',
  headers: {
    'Content-Type': 'application/json',
    'X-Platform': currentPlatform  // 重要：平台上下文
  },
  body: JSON.stringify(data)
})

const { ok, data, detail } = await response.json()
if (!ok) {
  toast(detail || '操作失败', 'error')
  return
}
// 处理 data
```

### 平台上下文事件
```typescript
// 切换平台时触发全局事件
window.addEventListener('wb-platform-change', (evt) => {
  const { platform } = evt.detail
  // 重新加载当前页面数据
  loadCurrentPageData()
})

// 页面卸载时移除监听
useEffect(() => {
  return () => {
    window.removeEventListener('wb-platform-change', handler)
  }
}, [])
```

### 拓扑动画集成（P2、P5）
```typescript
// 见 sentinel.md / gateway.md
const topology = new window.WBTraffic.Topology(container, {
  nodes: [/* 节点定义 */],
  links: [/* 连接定义 */]
})

// 粒子流动画
topology.fire('source', 'target', { duration: 500, color, tone })

// 脉冲效果
topology.pulse('node-id', 'danger' /* 可选颜色 */)
```

---

## ✅ 验收标准

### 功能验收
- [ ] 所有 15 页面都能加载并显示数据
- [ ] 所有 API 调用都有正确的错误处理
- [ ] 所有 CRUD 操作都有确认对话框（如需要）
- [ ] 平台切换能触发全局重载
- [ ] 搜索、过滤、排序都能生效
- [ ] 表单验证规则完整
- [ ] 分页、虚拟滚动正常工作

### 性能验收
- [ ] 首屏加载时间 < 3 秒
- [ ] 表格虚拟滚动处理 1000+ 行无卡顿
- [ ] 图表切换平滑（无闪烁）
- [ ] Lighthouse 评分 > 90

### 无障碍验收
- [ ] WCAG 2.1 AA 级别通过
- [ ] 键盘导航完整
- [ ] 屏幕阅读器能读出主要内容

### 国际化验收
- [ ] 支持 中文 / 英文
- [ ] 日期、数字格式正确
- [ ] RTL 语言可扩展（需要时）

### 代码质量
- [ ] TypeScript 无任何 `any` 类型
- [ ] ESLint + Prettier 通过
- [ ] 单元测试覆盖率 > 80%
- [ ] 注释清晰、代码易读

---

## 🚀 快速启动

```bash
# 1. 创建项目目录
mkdir -p admin/ui
cd admin/ui

# 2. 初始化 Vite + React
npm create vite@latest . -- --template react-ts

# 3. 安装依赖
pnpm install

# 4. 启动开发服务器
pnpm dev

# 5. 编译打包
pnpm build
```

---

## 📞 问题排查

| 问题 | 排查清单 |
|------|---------|
| API 返回 403 | 检查是否登录，是否带上 `X-Platform` 请求头 |
| 页面数据不更新 | 检查缓存策略是否生效，确认轮询间隔 |
| 拓扑动画不显示 | 确认 `window.WBTraffic` 已加载，container DOM 存在 |
| 表格虚拟滚动卡顿 | 检查是否有不必要的 re-render，考虑 useMemo 优化 |
| i18n 文案显示 key | 确认 i18n 配置文件已加载，key 拼写正确 |

---

## 📖 参考资源

- **设计文档总目录**: `docs/ui-v2/README.md`
- **旧版代码参考**: `admin/console/console-app.js`（1.6 万行，所有逻辑）
- **后端 API 文档**: `admin/routers/` 和 `admin/platform/`（Python FastAPI）
- **CSS 变量**: `docs/ui-v2/tokens.css`

---

## 💡 最佳实践

1. **分页面迭代** - 完成每一页的基本功能后再优化，不追求一次性完美
2. **充分利用旧版代码** - 格式化函数、交互逻辑、API 调用模式都可参考
3. **及时测试** - 每完成一个功能就对接 API 测试，不要堆积到最后
4. **边界情况优先** - 实现基本功能后立即处理加载态、错误、空状态
5. **性能从一开始** - 表格用虚拟滚动，图表一次性生成，避免事后优化

---

**预计总耗时**: 7-10 天（一个前端全职开发者）

**关键路径**:
- Day 1-2: 基础设施 + 通用组件库 + 壳层
- Day 3-5: P1、P2、P11 核心页面
- Day 6-8: 表格/表单页面（P4、P5、P6、P7、P8、P13）
- Day 9-10: 分析页面 + 质量优化

🎯 **下一步**: 选择 Day 1 的基础设施任务，或直接从 Day 3 的核心页面开始？
