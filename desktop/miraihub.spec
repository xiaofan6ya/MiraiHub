# Run through scripts/build_desktop.py; only explicitly listed product assets are included.
from pathlib import Path
import json
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
version = json.loads((root / 'desktop/version.json').read_text(encoding='utf-8'))['version']
name = f'miraihub-{version}-windows-x64'
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
         (str(root / 'desktop/assets/app.ico'), 'desktop/assets'),
         (str(root / 'desktop/assets/app.svg'), 'desktop/assets'),
         (str(root / 'LICENSE'), '.')]
datas += collect_data_files('webview')
datas += collect_data_files('tzdata')
hidden = collect_submodules('admin') + collect_submodules('desktop')
hidden += ['uvicorn.logging', 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.h11_impl',
           'uvicorn.lifespan.on', 'sqlalchemy.dialects.sqlite', 'sqlalchemy.dialects.mysql.pymysql',
           'converter', 'anthropic_adapter', 'responses_adapter', 'responses_projection',
           'desensitize', 'upstream_compat', 'repository_instructions', 'model_metadata', 'wb_install', 'wb_asar', 'wb_envcheck',
           'webview.platforms.edgechromium', 'webview.platforms.winforms', 'clr', 'pythonnet',
           # Tray shell: pystray loads its backend lazily, so collect them explicitly.
           'pystray._win32', 'pystray._util', 'PIL.Image', 'PIL.ImageDraw', 'PIL._tkinter_finder']
a = Analysis([str(root / 'desktop_entry.py')], pathex=[str(root)], datas=datas, hiddenimports=hidden,
             excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'cefpython3',
                       'cairosvg', 'resvg_py', 'numpy', 'scipy', 'matplotlib', 'pytest',
                       'IPython', 'watchfiles', 'websockets', 'httptools',
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
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=name, debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          icon=str(root / 'desktop/assets/app.ico'), version=str(root / 'build/version-resource.txt'))
