# Run through scripts/build_desktop.py; only explicitly listed product assets are included.
from pathlib import Path
import json
import platform
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
version = json.loads((root / 'desktop/version.json').read_text(encoding='utf-8'))['version']

# 打包产物名跟着目标平台走：Windows 是单文件 exe，macOS 是 .app 目录包。
MACHINE = (platform.machine() or '').lower()
ARCH = 'arm64' if MACHINE in ('arm64', 'aarch64') else 'x64'
MACOS = sys.platform == 'darwin'
name = (f'miraihub-{version}-macos-{ARCH}' if MACOS
        else f'miraihub-{version}-windows-x64')
datas = [(str(root / 'admin/static'), 'admin/static'),
         # The authenticated management console lives outside the public
         # static directory and must be present in the desktop bundle too.
         (str(root / 'admin/console'), 'admin/console'),
         # admin/data/ 是运行期用 read_text() 读的 JSON（上游错误文案表），
         # 漏打包会让 EXE/APK 启动即 FileNotFoundError：源码运行正常，只有产物才暴露。
         (str(root / 'admin/data'), 'admin/data'),
         # 各平台的目录快照（模型清单兜底）。漏打包时 EXE 启动会刷
         # 「trae 目录加载失败」——不影响启动，但模型列表会缺兜底数据。
         (str(root / 'admin/platform/trae/data'), 'admin/platform/trae/data'),
         (str(root / 'turing_helper.js'), '.'),
         (str(root / 'desktop/version.json'), 'desktop'),
         (str(root / 'desktop/apply-update.ps1'), 'desktop'),
         (str(root / 'desktop/assets/app.svg'), 'desktop/assets'),
         # 图标由构建脚本从 app.svg 生成：Windows 是 .ico，macOS 是 .icns。
         # 两个都按「存在才收」处理 —— 缺图标只会让包用默认图标，
         # 但不该让整个构建失败（PyInstaller 的 datas 找不到路径是硬错误）。
         *([(str(p), 'desktop/assets') for p in
            ((root / 'desktop/assets/app.ico'), (root / 'desktop/assets/app.icns'))
            if p.is_file()]),
         (str(root / 'LICENSE'), '.')]
datas += collect_data_files('webview')
datas += collect_data_files('tzdata')
hidden = collect_submodules('admin') + collect_submodules('desktop')
hidden += ['uvicorn.logging', 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.h11_impl',
           'uvicorn.lifespan.on', 'sqlalchemy.dialects.sqlite', 'sqlalchemy.dialects.mysql.pymysql',
           'converter', 'anthropic_adapter', 'responses_adapter', 'responses_projection',
           'desensitize', 'upstream_compat', 'repository_instructions', 'model_metadata', 'wb_install', 'wb_asar', 'wb_envcheck']
# 各平台的 WebView / 托盘后端：只收当前目标平台的那一个（另一端的模块在
# 这里的解释器里根本不装，收进去只会让构建报「找不到」）。
if MACOS:
    hidden += ['webview.platforms.cocoa']
else:
    hidden += ['webview.platforms.edgechromium', 'webview.platforms.winforms', 'clr', 'pythonnet']
hidden += [
           # 小浣熊扫码授权二维码（raccoon webauth 在函数内 import，EXE 里缺了
           # 就是「二维码出不来」—— 2026-11-07 排查结论，见 webauth.qr_svg）
           'qrcode', 'qrcode.image.svg', 'qrcode.image.pil', 'qrcode.main', 'qrcode.util',
           'PIL.Image', 'PIL.ImageDraw']
# Tray shell: pystray loads its backend lazily, so collect the current platform's one.
hidden += (['pystray._darwin'] if MACOS else ['pystray._win32', 'pystray._util'])
a = Analysis([str(root / 'desktop_entry.py')], pathex=[str(root)], datas=datas, hiddenimports=hidden,
             excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'cefpython3',
                       'cairosvg', 'resvg_py', 'numpy', 'scipy', 'matplotlib', 'pytest',
                       'IPython', 'watchfiles', 'websockets', 'httptools',
                       *(['pywin32', 'win32api', 'win32com'] if MACOS else []),
                       # 下面四个在 EXE 里占了大头却完全没用到（实测统计）：
                       #   Pythonwin\mfc140u.dll   2.6 MB  pywin32 的 MFC 界面层，
                       #                                    只走 win32api/win32com 用不到它
                       #   PIL._avif              4.1 MB  AVIF 编解码，控制台不碰 AVIF
                       #   asyncpg                0.3 MB  PostgreSQL 驱动，只支持 MySQL/SQLite
                       # 排除后功能不受影响，只是少几个永远不会 import 的二进制。
                       'Pythonwin', 'pywin', 'PIL._avif', 'asyncpg'])

# ===== 体积优化：剔除 imageio_ffmpeg 内置的 ffmpeg 二进制（29.7 MB）=====
#
# 29.7 MB 的 ffmpeg.exe 是 EXE 里最大的一项，占总体积 40%。
# 它只服务「长视频拼接」（admin/video_merge.py），而那个模块的查找顺序是：
#   1. 环境变量 WB_FFMPEG
#   2. imageio_ffmpeg 自带二进制   ← 我们要剔掉的就是这个
#   3. 系统 PATH 里的 ffmpeg
#
# 剔掉后行为是**安全降级**而不是坏掉：
#   · 系统装了 ffmpeg → 走第 3 顺位，功能照常
#   · 没装 → video_merge.available() 返回 False，前端「合并」按钮置灰，
#     不会生成坏文件
#
# 只剔除 binaries 数据文件，**保留 imageio_ffmpeg 这个 Python 模块本身** ——
# 它的 get_ffmpeg_exe() 找不到时会抛异常，正好被上层 except 接住转去查 PATH。
# 剔除清单：每项都实测过「打了也不 import」。
#
#   ffmpeg-win/linux/darwin  29.7 MB  长视频拼接的内置二进制。
#                                    剔除后 video_merge 改走系统 ffmpeg；
#                                    系统没有时 available() 返回 False，
#                                    前端按钮置灰，不会产生坏文件。
#   Pythonwin                2.9 MB  pywin32 的 MFC 图形层（mfc140u.dll +
#                                    win32ui.pyd）。只走 win32api/win32com 用不到，
#                                    pystray._win32 也不依赖它。
#                                    （excludes 管不住 DLL，只能在这里过滤）
_UNWANTED = ('ffmpeg-win', 'ffmpeg-linux', 'ffmpeg-darwin', 'ffmpeg.exe',
             'pythonwin', 'win32ui.pyd', 'mfc140u.dll')
_d0, _b0 = len(a.datas), len(a.binaries)
a.datas = [d for d in a.datas
           if not any(k in d[0].lower() for k in _UNWANTED)]
a.binaries = [b for b in a.binaries
              if not any(k in b[0].lower() for k in _UNWANTED)]
print('  [size] 剔除 datas %d 项 / binaries %d 项' % (_d0 - len(a.datas), _b0 - len(a.binaries)))

pyz = PYZ(a.pure)

if MACOS:
    # macOS 用 onedir + BUNDLE：.app 里直接摊开二进制与资源。
    #
    # 不用 onefile 的理由不是「风格」：PyInstaller 对 onefile + .app 会明确
    # 警告（v7 起直接报错），而且 onefile 的 .app 每次启动都要把几十 MB
    # 解压到临时目录、退出再删 —— 首次启动慢、崩溃还会留下 _MEI 残留。
    # onedir 的 .app 启动就是 exec 一个二进制。
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, debug=False,
              bootloader_ignore_signals=False, strip=False, upx=False,
              console=False, icon=str(root / 'desktop/assets/app.icns'))
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=name)
    # .app 目录包：Info.plist 里写明应用名、版本与最低系统版本（macOS 12+，
    # 对应 WKWebView 与 pywebview 6 的要求）。
    app = BUNDLE(
        coll,
        name=f'{name}.app',
        icon=str(root / 'desktop/assets/app.icns'),
        bundle_identifier='ai.miraihub.desktop',
        info_plist={
            'CFBundleName': 'MiraiHub',
            'CFBundleDisplayName': 'MiraiHub',
            'CFBundleShortVersionString': version,
            'CFBundleVersion': version,
            'CFBundleExecutable': name,
            'LSMinimumSystemVersion': '12.0',
            'NSHighResolutionCapable': True,
            'NSHumanReadableCopyright': 'MiraiHub',
        },
    )
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=name, debug=False,
              bootloader_ignore_signals=False, strip=False, upx=False, console=False,
              icon=str(root / 'desktop/assets/app.ico'),
              version=str(root / 'build/version-resource.txt'))
