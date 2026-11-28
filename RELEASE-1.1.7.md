# MiraiHub 1.1.7

> 这版主要是**修上一版桌面端的问题**：1.1.6 换掉旧控制台之后，桌面版进不去工作台 ——
> 打开就一直在「加载 / 提示需要登录」之间闪。另外统一了三端图标、补回无边框窗口的
> 窗口按钮。

## 修了什么

### 1. 桌面版「无限卡闪 + 一直提示需要登录」（最严重）

**现象**：双击 EXE 能起来，但窗口里页面反复重载，一直跳「未登录」，根本用不了。

**根因**：桌面壳靠 `pywebview.api.desktop_login()` 签一个本地令牌，再拿它去
`/api/login/session` 换 HttpOnly Cookie。但 pywebview 的返回值回调表
`_returnValuesCallbacks` 在页面发生 hash 路由跳转（`/` → `#/overview`）之后就会失效：

```
TypeError: window.pywebview._returnValuesCallbacks.desktop_login.xxx is not a function
```

令牌永远拿不到 → 页面所有 API 401 → 前端统一跳 `/admin` → 整页重载 → 又 401。
1.1.6 里前端压根没接这条链路，1.1.7 的前几个内部构建接了 JS 桥，同样撞上这个失效问题。

**修法**：绕开 JS 桥，改成 **URL 一次性换票**：

```
桌面壳 load_url:  /admin?desktop=1&token=<local_shell JWT>
        ↓ 后端 admin_index 校验签名 + local_shell:true
        ↓ 下发 HttpOnly 会话 Cookie
   303 → /admin?desktop=1      （令牌从地址栏抹掉，不进历史也不滞留）
```

不依赖任何 JS 回调，落地即生效。

### 2. 窗口右上角的关闭按钮是空白

`WindowControls.vue` 里用了 `<X>` 但 import 只写了 `Copy / Minus / Square` ——
关闭键里没有任何图标，悬停时只剩一块红色背景，就是你说的「一坨长方形」。

### 3. 点「退出登录」又卡死

桌面版是**免登**的：进工作台靠一次性令牌换 Cookie，没有「登录」这个动作。
点退出会清 Cookie 并 `location.replace('/admin')`，把 `?desktop=1&token=` 一并丢掉，
于是又回到「401 → 重载 → 401」。

本地运行时并没有可退出的会话，所以桌面壳里**直接隐藏这个入口**（登录设备保留）。

### 4. 三端图标统一

之前是三套：网页是「深色底 + 银灰 M」，桌面和安卓是「蓝底 + 星形拓扑」。
现在全部对齐网页版那套：

- 网页 `favicon.svg`：原样（基准）
- 桌面 `app.svg` → `app.ico`（16/24/32/48/64/128/256 七档）+ `app.png`
- Android：自适应图标前景/背景 + 老版本兜底图标 + `mipmap-*` 各密度

### 5. 顺带补的桌面端体验

- **无边框窗口 + 自绘窗口按钮**（− □ ×）：1.1.6 删掉旧控制台后，工作台右上角
  完全没有窗饰，只能靠任务栏右键。现在贴着右上角补回，关闭键悬停变红。
- 窗口拖拽区、最大化/还原状态同步、窗口尺寸夹到工作区。

### 6. Android

- 启动页重做（深色/浅色跟随系统、安全区适配、品牌标识）
- 自适应图标 + Android 13 主题图标（monochrome 层）
- `Theme.MiraiHub` 主题，取代写死的 `Theme.Material.Light.NoActionBar`

## 已知问题

- **移动端顶部布局重叠**：窄屏下页面标题与面包屑会挤在一起，汉堡按钮压住文字。
  下版修（这是新版后台的响应式遗留，不影响功能）。
- 后台功能对齐全表（86 项，20 项优先）见 `docs/ui-v2/MERGE-GAP-REPORT.md`。

## 升级说明

- **Windows**：直接覆盖安装。数据目录不变。
- **Android**：覆盖安装。签名一致；若提示冲突先卸载（本地账号库会丢，建议先在电脑导出备份）。
- **从 1.1.6 升上来**：`.env` 里的 `MIRAIHUB_CONSOLE` 可以删了（1.1.6 起已无效）。

## 下载

| 平台 | 文件 |
|---|---|
| Windows x64 | `miraihub-1.1.7-windows-x64.exe` |
| Android arm64 | `miraihub-1.1.7-android-arm64.apk` |

每个产物都带 `.sha256` 校验文件。macOS 包需要在 macOS 上构建，本版未附带。
