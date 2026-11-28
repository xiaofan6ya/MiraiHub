from __future__ import annotations

import ctypes
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

import webbrowser

from desktop.common import ROOT, VERSION, atomic_json, data_dir
from desktop.runtime import (configure_node, ensure_node, ensure_webview2,
                             node_executable)
from desktop.tray import TrayController, available as tray_available
from desktop.updates import UpdateService


class _NullTray:
    """Stand-in used before the real tray exists and when tray mode is off."""

    def set_state(self, state):
        pass

    def notify(self, message):
        pass

    state = "stopped"


#: Windows 专用的「不要弹控制台窗口」标志；POSIX 上必须传 0，否则 Popen 会
#: 因为不认识这个关键字而直接抛异常。
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _applescript_quote(text: str) -> str:
    """把一段文字塞进 AppleScript 的字符串字面量里。"""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _message_box(message: str, title: str = "MiraiHub", flags: int = 64) -> None:
    """弹一个系统对话框。

    Windows 用 MessageBoxW；macOS 用 osascript（不引入 pyobjc 依赖）。两个都
    弹不出来时（CI、无人值守）只写日志 —— **弹框失败不能带走启动流程**。
    """
    try:
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, message, title, flags)
            return
        if sys.platform == "darwin":
            script = 'display dialog %s with title %s buttons {"好"} default button 1' % (
                _applescript_quote(message), _applescript_quote(title))
            subprocess.run(["osascript", "-e", script], capture_output=True,
                           timeout=300, check=False)
            return
    except Exception:  # noqa: BLE001 - 没有图形环境
        logging.getLogger("desktop").debug("系统对话框不可用", exc_info=True)
    logging.getLogger("desktop").info("%s：%s", title, message)


def _confirm(message: str, title: str = "MiraiHub") -> bool:
    """「是 / 否」对话框，返回是否点了「是」。macOS 走 osascript 的按钮文本。"""
    try:
        if os.name == "nt":
            return ctypes.windll.user32.MessageBoxW(None, message, title, 0x00010024) == 6
        if sys.platform == "darwin":
            script = ('display dialog %s with title %s buttons {"稍后", "立即更新"} '
                      'default button 2 with icon note' % (
                          _applescript_quote(message), _applescript_quote(title)))
            done = subprocess.run(["osascript", "-e", script], capture_output=True,
                                  timeout=300, check=False)
            return "立即更新" in (done.stdout or b"").decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        logging.getLogger("desktop").debug("确认对话框不可用", exc_info=True)
    return False


def _lock_instance(handle) -> bool:
    """独占打开本实例锁：Windows 用 msvcrt，macOS/Linux 用 fcntl。"""
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _webview_gui():
    """pywebview 的后端：Windows 用 EdgeChromium，macOS 交给它自己选 Cocoa。"""
    return "edgechromium" if os.name == "nt" else None


def _window_icon() -> str:
    """窗口/包图标：macOS 用 .icns，Windows 用 .ico（都找不到就交给 pywebview）。"""
    assets = ROOT / "desktop/assets"
    if sys.platform == "darwin":
        candidates = (assets / "app.icns", assets / "app.png")
    else:
        candidates = (assets / "app.ico", assets / "app.png")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return ""


#: PyInstaller onefile 会在 %TEMP% 下解压一份 _MEIxxxxxx 运行目录。
#: 正常退出时它会自删；但**关机/重启/任务管理器结束进程**这类硬杀不会触发
#: 清理，目录就永远留在 C 盘。实测单份 152 MB，攒十几次就是一个多 GB。
_MEI_PREFIX = "_MEI"


def _mei_in_use(path: str) -> bool:
    """这个 _MEI 目录是否还挂在别的存活进程上。

    判断方式：用 handle 类工具太重，这里直接尝试**独占重命名** ——
    能改名成功说明没有进程持有里面的文件句柄（Windows 上正在运行的
    exe/dll 会被锁住，改名会失败）。失败就认为是活的，跳过不删。
    """
    try:
        probe = path + ".delete-probe"
        os.rename(path, probe)
        os.rename(probe, path)
        return False
    except OSError:
        return True


def cleanup_stale_mei_dirs() -> int:
    """清理上次异常退出留下的 _MEI* 目录，返回释放的字节数。

    ## 为什么在**启动时**清而不是退出时

    被杀进程时没有任何机会跑退出代码（关机、重启、任务管理器结束、蓝屏）。
    所以唯一可靠的时机是「下一次启动」。

    ## 为什么不会误删自己

    sys._MEIPASS 指向当前进程正在用的那个目录，先把它排除；
    另外还在跑的其它实例（多开、或托盘常驻）会被 _mei_in_use()
    识别为活的而跳过。
    """
    import shutil
    import tempfile

    # macOS / Linux 上改名一个「正在被别的进程使用」的目录是允许的，
    # 探测会误判成「没人用」进而删掉活实例的目录。这条清理只在 Windows 做。
    if os.name != "nt":
        return 0

    current = ""
    mei_base = getattr(sys, "_MEIPASS", "") or ""
    if mei_base:
        try:
            current = os.path.normcase(os.path.abspath(mei_base))
        except Exception:
            current = ""
    # 非 frozen（源码运行）时不该动 TEMP —— 那是别人程序的目录
    if not getattr(sys, "frozen", False):
        return 0
    freed = 0
    try:
        entries = os.listdir(tempfile.gettempdir())
    except OSError:
        return 0
    for name in entries:
        if not name.startswith(_MEI_PREFIX):
            continue
        path = os.path.join(tempfile.gettempdir(), name)
        try:
            if not os.path.isdir(path):
                continue
            if current and os.path.normcase(os.path.abspath(path)) == current:
                continue
            if _mei_in_use(path):
                continue
            size = 0
            for root, _dirs, files in os.walk(path):
                for f in files:
                    try:
                        size += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.exists(path):
                freed += size
        except Exception:
            continue
    return freed


#: 默认端口。用户没手动指定时永远先试这个。
DEFAULT_PORT = 8790

#: 备选端口：首选端口被占时按顺序往下试。
#: 用固定的一小段而不是随机端口 —— 端口稳定，用户收藏的
#: http://127.0.0.1:8790/v1 才不会某天突然失效。
FALLBACK_PORTS = (8790, 8791, 8792, 8793, 8794, 8795, 8800, 8080, 8000)


def _port_free(port: int) -> bool:
    """端口能否被我们绑定（含 SO_REUSEADDR，避免 TIME_WAIT 误判）。"""
    if not 1 <= port <= 65535:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # 先看有没有人在听：connect 成功说明已经在跑（自己的旧实例也算）
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def select_port(preferred: int) -> int:
    """选服务端口：**优先稳定，其次随机**。

    ## 为什么不能直接 bind(0)

    原实现只要首选端口绑不上就 bind(("0.0.0.0", 0))，让系统给一个随机
    高位端口（实际见到的 54293、50069 都是这么来的）。问题有两层：

      1. 那个随机端口会被 save_port() 写回 config.json，**下次启动
         直接用它**，于是「8790 明明空着却一直用 54293」。
      2. 端口每次可能不同，用户收藏的 API 地址、客户端里填的 base_url
         全都会失效。

    所以这里改成：首选 → 固定备选段 → 最后才随机。随机只在
    「一整段都被占」时发生，属于兜底而不是常态。
    """
    preferred = int(preferred)
    if not 1 <= preferred <= 65535:
        raise ValueError("服务端口必须在 1 到 65535 之间")

    # 动态端口段（IANA 的 ephemeral range）。旧版本 bind(0) 拿到的
    # 54293、50069 都落在这里。
    #
    # **这种值不当作「用户指定的端口」**，否则历史遗留的随机端口会被
    # 永久优先使用 —— 而它本来就是兜底产物，不该有最高优先级。
    # 这正是「8790 明明空着却一直用 54293」的另一半原因。
    if 49152 <= preferred <= 65535:
        order = list(dict.fromkeys([*FALLBACK_PORTS, preferred]))
    else:
        order = list(dict.fromkeys([preferred, *FALLBACK_PORTS]))

    for port in order:
        if _port_free(port):
            return port

    # 整段都占用了：交给系统分配，但调用方**不应**把这个结果写进 config
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("0.0.0.0", 0))
        return sock.getsockname()[1]


def save_port(base: Path, config: dict, port: int) -> None:
    """把实际使用的端口写回配置。

    **只有固定端口才落盘**。系统随机分配的端口（>49151 那段）不写 ——
    否则下次启动会优先用那个随机值，用户就再也回不到 8790 了。
    这正是「8790 空着却一直跑在 54293」的成因。
    """
    if config.get("port") == port:
        return
    if port not in (DEFAULT_PORT, *FALLBACK_PORTS):
        return
    # Write only after our own backend confirms readiness, never on a failed start.
    config["port"] = port
    atomic_json(base / "config.json", config)


def configure_environment(base: Path) -> dict:
    file = base / "config.json"
    if file.is_file():
        config = json.loads(file.read_text(encoding="utf-8"))
    else:
        config = {"port": 8790, "username": "desktop", "password": secrets.token_urlsafe(32),
                  "jwt_secret": secrets.token_urlsafe(48)}
        atomic_json(file, config)
    if "converter_api_key" not in config:
        config["converter_api_key"] = "wb-" + secrets.token_urlsafe(32)
        atomic_json(file, config)
    os.environ.update({"WORKBUDDY_DESKTOP": "1", "WORKBUDDY_CLIENT_RUNTIME": "1",
                       "WORKBUDDY_DESKTOP_DATA": str(base),
                       "ADMIN_DATABASE_URL": "sqlite:///" + (base / "workbuddy.db").as_posix(),
                       "ADMIN_GATEWAY_LIMIT_BACKEND": "memory", "ADMIN_HOST": "0.0.0.0",
                       "ADMIN_USERNAME": config["username"], "ADMIN_PASSWORD": config["password"],
                       "ADMIN_JWT_SECRET": config["jwt_secret"], "PYTHONUTF8": "1",
                       "CONVERTER_API_KEY": config["converter_api_key"],
                       "CONVERTER_LOG": str(base / "logs/converter.log")})
    configure_node(base)
    os.chdir(base)
    return config


def setup_logging(base: Path):
    logs = base / "logs"
    logs.mkdir(exist_ok=True)
    handler = RotatingFileHandler(logs / "desktop.log", maxBytes=4*1048576,
                                 backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def parent_watchdog():
    """父进程**意外消失**时的灾难恢复 —— 正常路径不该依赖它。

    计划任务 4 要求：父进程要在孩子关闭完之前保持存活。正常停止/重启/退出
    都由控制器等真实退出码，所以这个看门狗只在「父进程被强杀（任务管理器、
    关机、蓝屏）」时兜底：孩子发现父进程句柄结束就直接退出，避免留下孤儿
    监听端口。
    """
    pid = int(os.environ.get("WB_DESKTOP_PARENT", "0"))
    if not pid or os.name != "nt":
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        os._exit(0)
    kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
    os._exit(0)


#: Uvicorn 在**排空归零之后**等待在途 ASGI task 收尾的宽限（秒）。
#:
#: 它只作用于 should_exit 之后：此时业务计数已经是 0，用户决定的排空
#: 阶段本身没有秒表。保留原来的 5 秒，避免把「关闭钩子卡死」变成新问题。
SHUTDOWN_GRACE_SECONDS = 5


def backend(port: int) -> int:
    """子进程入口：跑**一代** worker，返回真实退出码。

    契约（CONTRACT 第 4 节）：正常停止 0、受理的计划重启 75，其余沿用错误码。
    父进程靠这个退出码决定要不要开下一代 —— 旧 uvicorn.run() 永远返回 0，
    父进程根本分不出「用户点了重启」和「进程崩了」。
    """
    base = data_dir()
    configure_environment(base)
    setup_logging(base)
    threading.Thread(target=parent_watchdog, daemon=True).start()
    # ⚠️ 这里**不再校验** _CONVERTER_EMBEDDED。
    #
    # 那个标志表示「内嵌的 /gw 兼容网关是否挂载成功」。/gw 已经**永久下线**
    # （它是读桌面端登录文件的第二入口，绕过账号池与计费，见 admin/server.py
    # 的说明），标志现在恒为 False。继续校验会让桌面版**必然启动失败**：
    #
    #     RuntimeError: 内嵌网关未能加载，请检查打包依赖
    #
    # 内嵌网关不再是必需依赖 —— 真正需要的只是 admin.server.app 本身，
    # 上面那行 import 失败会自然抛异常，不需要额外的标志位把关。
    from admin.server import app
    from admin.db import init_db
    if not init_db():
        raise RuntimeError("本地数据库初始化失败")
    def runtime_watch():
        for _ in range(600):
            if node_executable(base).is_file():
                from admin.turing_token import clear_cache, warmup
                clear_cache()
                warmup()
                return
            time.sleep(1)
    threading.Thread(target=runtime_watch, daemon=True).start()
    # 运行器（admin/managed_worker.py）持有真实的 uvicorn.Server：
    # 封口 -> 等真实计数归零 -> 停服 -> 返回退出码。控制通道的目录与
    # authkey 由父进程用环境变量注入，这里不再自己生成。
    from admin import managed_worker
    return managed_worker.serve(
        app, host="0.0.0.0", port=port, proxy_headers=False, log_level="info",
        log_config=None, access_log=False, loop="asyncio", http="h11", ws="none",
        timeout_graceful_shutdown=SHUTDOWN_GRACE_SECONDS)

#: 计划重启的退出码（CONTRACT 第 4 节 EXIT_PLANNED_RESTART）。
PLANNED_RESTART_CODE = 75
#: 正常停止的退出码（CONTRACT 第 4 节 EXIT_OK）。
NORMAL_EXIT_CODE = 0

#: 就绪判定探针：只认本代 nonce，并且回执里的 instance_id 必须是本代。
READY_PATH = "/api/app/desktop-ready"

#: 契约第 1 节的快照字段。Bridge.startup_status() 增量返回它们，
#: 同时保留旧的 message / failed 两个字段。
SNAPSHOT_FIELDS = ("phase", "action", "instance_id", "operation_id", "active_requests",
                   "active_jobs", "elapsed_ms", "can_restart", "can_force")

#: 用户意图 -> 控制通道动作。
_ACTION_FOR = {"start": "stop", "stop": "stop", "quit": "stop",
               "restart": "restart", "update": "update"}

#: 动作 -> 主标题（计划任务 4 第 7 条的示例文案）。
_HEADLINE = {"stop": "服务正在停止", "restart": "正在重启本地服务", "update": "正在准备更新"}

logger = logging.getLogger("desktop")


def _control_module():
    """延迟导入控制通道：桌面父进程不必为了状态查询拉起整包 admin。"""
    from admin import lifecycle_control

    return lifecycle_control


def _kill_process_tree(pid: int) -> None:
    """**只有**用户明确确认的强制结束才允许走到这里。

    正常路径先 BEGIN 再等真实退出码；这个函数是「控制通道完全无应答、
    用户已经二次确认」时的最后手段，结束的是父进程自己持有的那棵进程树。
    """
    if not pid:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True,
                       creationflags=_NO_WINDOW)
        return
    # POSIX：起服务时开了独立进程组（start_new_session），这里杀整组，
    # 避免 python shim 留下的孤儿继续占着端口。先 TERM 再 KILL。
    import signal
    try:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            os.kill(pid, signal.SIGTERM)
        for _ in range(20):
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.1)
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        logger.debug("强制结束进程失败（可能已退出）", exc_info=True)


def _webview_windows():
    """当前还活着的 pywebview 窗口（没有 webview 时返回空表）。"""
    try:
        import webview
    except Exception:
        return []
    return list(getattr(webview, "windows", []) or [])


def _clamp_window_to_work_area(window) -> None:
    """把无边框窗口的位置和大小钳回显示器工作区。

    ## 为什么需要（v1.1.6 实测：200% 缩放下底部越界 39px）

    pywebview 的 WinForms 表单按「初始逻辑尺寸 × AutoScaleMode.Dpi」得到
    物理尺寸，而这个物理值不参与任何工作区判断 —— 逻辑 860px 在 200% 下变成
    1720px，屏幕工作区往往只剩 1681px，底边就压进任务栏。无边框窗口没有
    系统 WM_NCCALCSIZE 兜底，WinForms 也不会自动收，越界就这么留下来。

    最大化不在这里处理（Bridge.window_action 里 MaximizedBounds 已钉到
    工作区）；这里只管 Normal 状态下的初始几何，顺带把被外部脚本（用户的
    摆位 bat）挪出屏幕的位置拉回来。
    """
    if os.name != "nt" or window is None:
        return
    try:
        from System.Windows.Forms import FormWindowState, Screen

        native = window.native
        if native is None or native.WindowState != FormWindowState.Normal:
            return
        area = Screen.FromHandle(native.Handle).WorkingArea
        bounds = native.Bounds
        width, height = min(bounds.Width, area.Width), min(bounds.Height, area.Height)
        x = min(max(bounds.X, area.Left), area.Right - width)
        y = min(max(bounds.Y, area.Top), area.Bottom - height)
        if (x, y, width, height) != (bounds.X, bounds.Y, bounds.Width, bounds.Height):
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
            user32.SetWindowPos(native.Handle.ToInt64(), None, x, y, width, height,
                                SWP_NOZORDER | SWP_NOACTIVATE)
            logger.debug("窗口几何已钳回工作区：%s", (x, y, width, height))
    except Exception:                 # noqa: BLE001 - 钳制失败不能挡启动
        logger.debug("窗口工作区钳制失败", exc_info=True)


SPLASH = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>
*{box-sizing:border-box}body{margin:0;background:linear-gradient(150deg,#dfe9ff,#f5f7fa 55%,#e4ebfb);color:#1b2430;font:15px 'Segoe UI','Microsoft YaHei',sans-serif;display:grid;place-items:center;min-height:100vh}
main{width:min(480px,90vw);padding:40px;background:#fffffff0;border:1px solid white;border-radius:28px;box-shadow:0 20px 70px #1a243320}.tag{color:#3B66E8;font-size:12px;letter-spacing:2px}h1{font-size:28px;margin:10px 0 26px}p{color:#5c6673;line-height:1.8}.bar{height:5px;background:#e6ecf6;border-radius:6px;overflow:hidden}.bar:after{content:'';display:block;width:40%;height:100%;background:#3B66E8;animation:load 1.3s infinite}@keyframes load{to{transform:translateX(260%)}}
button{padding:11px 16px;border:0;border-radius:10px;cursor:pointer;background:#e8eef7;color:#33587F;margin:4px 6px 4px 0}button:disabled{opacity:.5;cursor:default}.hint{font-size:12px;color:#7d8794}textarea{width:100%;height:190px;border:1px solid #d8e0ea;border-radius:10px;padding:12px;resize:vertical}
#forceDialog{position:fixed;inset:0;background:#1b243066;display:grid;place-items:center}#forceDialog[hidden]{display:none}#forceDialog>div{width:min(420px,90vw);background:#fff;border-radius:18px;padding:24px;box-shadow:0 20px 60px #1a243344}
</style><div class="pywebview-drag-region" style="position:fixed;top:0;left:0;right:136px;height:36px"></div><div style="position:fixed;top:0;right:0"><button aria-label="最小化" onclick="pywebview.api.window_action('minimize')">−</button><button aria-label="最大化" onclick="pywebview.api.window_action('maximize')">□</button><button aria-label="关闭窗口" onclick="pywebview.api.window_action('close')">×</button></div><main><div class="tag">你的本地 AI 网关</div><h1>MiraiHub</h1><p class="hint">v__VERSION__</p><p id="status" role="status" aria-live="polite">正在启动本地服务…</p><p class="hint" id="detail"></p><div class="bar" id="bar"></div><p class="hint">就绪后自动进入工作台，无需登录管理台。<br>首次运行可能稍慢，请保留账号数据。</p>
<button id="retryButton" onclick="retryStart()">重试启动</button><button id="forceButton" hidden onclick="openForce()">强制结束…</button><button id="diagnosticButton" hidden onclick="showDiagnostic()">查看诊断</button><section id="diagnostic" hidden><p>服务尚未启动也可复制或导出诊断。</p><textarea id="report" readonly aria-label="启动诊断"></textarea><button onclick="copyReport()">复制诊断</button><button onclick="downloadReport()">导出诊断</button></section></main>
<div id="forceDialog" hidden role="dialog" aria-modal="true" aria-labelledby="forceTitle"><div><p><strong id="forceTitle">确定强制结束本地服务？</strong></p><p class="hint" id="forceCount">最近已知：状态未知。</p><p class="hint">强制结束会中断正在进行的请求与落库。默认继续等待。</p><button id="keepWaiting" onclick="keepWaiting()">继续等待</button><button id="confirmForce" onclick="confirmForce()">强制结束</button></div></div>
<script>
const began=Date.now();let reading=false;let last={};
function $(id){return document.getElementById(id)}
async function showDiagnostic(){try{const r=await pywebview.api.startup_diagnostic();$('report').value=r.report;$('diagnostic').hidden=false}catch(e){$('status').textContent='诊断暂不可用，请重试'}}
function copyReport(){const r=$('report');r.select();document.execCommand('copy')}
function downloadReport(){const u=URL.createObjectURL(new Blob([$('report').value],{type:'text/plain;charset=utf-8'}));const a=document.createElement('a');a.href=u;a.download='miraihub-startup-diagnostic.txt';a.click();setTimeout(function(){URL.revokeObjectURL(u)},1000)}
async function retryStart(){try{await pywebview.api.retry_start()}catch(e){}}
function openForce(){if(!last.can_force){return}$('forceCount').textContent='最近已知：'+(last.message||'状态未知');$('forceDialog').hidden=false;try{$('keepWaiting').focus()}catch(e){}}
function keepWaiting(){$('forceDialog').hidden=true}
async function confirmForce(){const b=$('confirmForce');b.disabled=true;try{await pywebview.api.force_stop(true)}catch(e){}$('forceDialog').hidden=true;b.disabled=false}
async function poll(){if(reading||!window.pywebview||!window.pywebview.api){return}reading=true;try{const s=await pywebview.api.startup_status();last=s||{};$('status').textContent=s.message||'';$('detail').textContent=s.detail||'';$('bar').hidden=!!s.failed;$('forceButton').hidden=!s.can_force;$('retryButton').disabled=!(s.phase==='stopped'||s.phase==='failed');$('diagnosticButton').hidden=!(s.failed||Date.now()-began>30000)}catch(e){}finally{reading=false}}
setInterval(poll,1000);poll();
</script></html>""".replace("__VERSION__", VERSION)


class Generation:
    """一代后端：Popen 句柄 + 本次启动的随机代号 + 本代控制通道。

    instance_id 与 nonce 都是随机不透明值，**不是凭据**：
    它们只用来回答「这个回执/这个端口还是不是本代」。
    """

    __slots__ = ("instance_id", "nonce", "port", "control_dir", "authkey", "env",
                 "command", "process", "owner", "ready", "verified", "exit_code",
                 "started_at")

    def __init__(self, *, instance_id, nonce, port, control_dir, authkey, env, command):
        self.instance_id = instance_id
        self.nonce = nonce
        self.port = port
        self.control_dir = Path(control_dir)
        self.authkey = authkey
        self.env = env
        self.command = command
        self.process = None
        self.owner = None
        self.ready = False
        self.verified = False
        self.exit_code = None
        self.started_at = time.monotonic()

    def __repr__(self):
        return "Generation(instance=%s, port=%s, ready=%s)" % (
            self.instance_id, self.port, self.ready)


class Bridge:
    """Expose methods only: pywebview recursively serializes public object attributes."""
    def __init__(self, controller):
        self._controller = controller

    def startup_status(self):
        return self._controller.startup_status()

    def save_text(self, filename, text, mime="application/json"):
        return self._controller.save_text(filename, text, mime)

    def startup_diagnostic(self):
        return self._controller.startup_diagnostic()

    def desktop_login(self):
        return self._controller.desktop_login()

    def check_updates(self):
        return self._controller.check_updates()

    def update_status(self):
        return self._controller.update_status()

    def retry_runtime(self):
        return self._controller.retry_runtime()

    def retry_start(self):
        return self._controller.retry_start()

    def download_update(self, release_id):
        return self._controller.download_update(release_id)

    def apply_update(self):
        return self._controller.apply_update()

    def retry_install(self):
        return self._controller.retry_install()

    def start_old_version(self):
        return self._controller.start_old_version()

    def force_stop(self, confirm=False):
        return self._controller.force_stop(confirm)

    def window_frame(self):
        """前端探测窗口形态：frameless 时由前端自绘拖拽区 + 窗口控制按钮。

        Windows 上窗口无边框（_create_window 的 frameless），macOS 保留原生
        标题栏 —— 前端只有知道这一点，才决定要不要渲染 − □ × 和拖拽条。
        旧版客户端没有这个方法，前端按 UA 兜底（edgechromium 的 UA 必含
        Windows NT，WKWebView 必含 Macintosh）。
        """
        return {"frameless": os.name == "nt", "platform": sys.platform}

    def window_state(self):
        """当前窗口状态（normal/maximized/minimized）。

        拖拽区在 mousedown 时问一次：用户可能在启动页点了最大化，随后导航进
        工作台 —— 前端的本地标记会丢，只有壳知道真实状态。
        """
        window = self._controller.window
        try:
            return {"state": str(window.native.WindowState).lower()}
        except Exception:                 # noqa: BLE001 - 拿不到就按可拖处理
            return {"state": "normal"}

    def window_action(self, action):
        window = self._controller.window
        if action == "minimize":
            window.minimize()
        elif action == "maximize":
            if str(window.native.WindowState) == "Maximized":
                window.restore()
            else:
                # ⚠️ frameless 窗口直接 Maximized 会**盖住系统任务栏**：
                # 无边框表单最大化时 WinForms 不按工作区裁剪，而是铺满整屏。
                # 先把 MaximizedBounds 钉到「屏幕工作区」（扣掉任务栏），
                # 再最大化 —— 点最大化按钮后就不再遮任务栏。
                try:
                    from System.Windows.Forms import Screen
                    native = window.native
                    screen = Screen.FromHandle(native.Handle)
                    native.MaximizedBounds = screen.WorkingArea
                except Exception:
                    logging.debug("MaximizedBounds 设置失败，退回默认最大化",
                                  exc_info=True)
                window.maximize()
        elif action == "close":
            # × 只是「收起来」，不是「停服务」：托盘模式隐藏，无托盘模式排队退出。
            if self._controller.request_window_close():
                window.destroy()
        elif action.startswith("resize-") and os.name == "nt":
            edges = {"left": 1, "right": 2, "top": 3, "top-left": 4, "top-right": 5,
                     "bottom": 6, "bottom-left": 7, "bottom-right": 8}
            edge = edges.get(action.removeprefix("resize-"))
            if edge and str(window.native.WindowState) == "Normal":
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
                user32.PostMessageW(window.native.Handle.ToInt64(), 0x0112, 0xF000 + edge, 0)


class DesktopAPI:
    """桌面父进程：一个小型**串行控制器** + 窗口 / 托盘 / 更新。

    为什么不再「谁先到谁动手」
    --------------------------
    启动、停止、重启、退出、更新原来是五条各自加锁的路径：_start_worker
    发现旧句柄活着就 terminate()，run() 的 finally 再 stop() 一次，托盘的
    × 又走另一套。同一个时刻可能有人在建进程、有人在杀进程。

    现在只有一个串行线程执行「意图」，其余线程（Bridge、托盘、GUI 回调）
    只做两件事：锁内登记意图/状态，锁外调用 I/O。锁只保护状态、代号与
    句柄替换；网络探测、子进程等待、JS 调用都在锁外，GUI 永不被后台卡住。

    两个旗标必须分开
    ----------------
    _want_quit：**用户想退出**（排空最开始就要记下，用来压住自动重生）；
    _allow_close：**现在可以销毁窗口**（只有确认后端实退之后才置位）。
    把两者合成一个 _quitting 就是老代码的症状：一开始排空就放行关闭，
    窗口把由它自己发起的 Agent/SSE 请求一起带走。
    """

    def __init__(self, base: Path, config: dict, *, spawn=None, owner_factory=None,
                 readiness=None, installer=None, update_target=None, windows=None,
                 port_picker=None, clock=None, sleep=None, poll_interval: float = 1.0,
                 start_timeout: float = 90.0, seal_timeout: float = 20.0,
                 late_exit_seconds: float = 30.0, tray_mode: bool = False) -> None:
        self.base, self.config = base, config
        self.window = None
        self.tray = _NullTray()
        self.tray_mode = bool(tray_mode)
        self.port = 0
        #: 启动诊断/进度层用的两行文案（旧字段 message 保持不变）。
        self.message = "正在启动本地服务…"
        self.detail = ""
        self.failed = False
        self.runtime_message = "正在准备 Node.js…"
        self.runtime_busy = False
        self.update_state = {"busy": False, "message": "", "ready": False}
        self.update_file = None
        self.tray_startup_update_check = False
        self.updates = UpdateService(base)
        self.runtime_lock = threading.Lock()
        self.update_lock = threading.Lock()
        # -- 控制器状态（全部由 _cond 保护） --------------------------------
        self._cond = threading.Condition(threading.RLock())
        self._run_lock = threading.Lock()
        self._generation = None
        self._pending = deque()
        self._phase = "stopped"
        self._action = None
        self._operation_id = None
        self._instance = None
        self._snapshot = {}
        self._drain_started = 0.0
        self._want_quit = False
        self._allow_close = False
        self._destroyed = False
        self._finished = False
        self._thread = None
        self._monitor = None
        self._restart_op = None
        self._ops = {}
        self._tray_state = "stopped"
        self._installer_launched = False
        self._install_failed = False
        self._accepting = True
        self._closing = threading.Event()
        self._monitor_stop = threading.Event()
        # -- 可替换协作者（测试用 fake Popen / 窗口 / 托盘 / 事件闩） --------
        self._spawn = spawn or self._spawn_backend
        self._owner_factory = owner_factory or self._make_owner
        self._readiness = readiness or self._http_readiness
        self._installer = installer or self._launch_installer
        self._update_target = Path(update_target) if update_target is not None else None
        self._windows = windows or _webview_windows
        self._port_picker = port_picker or select_port
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._poll_interval = max(0.005, float(poll_interval))
        self._start_timeout = float(start_timeout)
        self._seal_timeout = float(seal_timeout)
        self._late_exit_seconds = float(late_exit_seconds)

    # ------------------------------------------------------------------
    # 对外只读状态
    # ------------------------------------------------------------------
    @property
    def phase(self) -> str:
        with self._cond:
            return self._phase

    @property
    def allow_close(self) -> bool:
        with self._cond:
            return self._allow_close

    @property
    def current_generation(self):
        with self._cond:
            return self._generation

    def running(self) -> bool:
        """True only while the backend child process is alive."""
        generation = self.current_generation
        return generation is not None and self._poll(generation) is None

    def startup_status(self):
        """Bridge.startup_status()：契约第 1 节快照 + 旧的 message / failed。"""
        with self._cond:
            snapshot = dict(self._snapshot)
            status = {name: snapshot.get(name) for name in SNAPSHOT_FIELDS}
            status["phase"] = self._phase
            status["action"] = self._action or snapshot.get("action")
            status["instance_id"] = self._instance or snapshot.get("instance_id")
            status["operation_id"] = self._operation_id or snapshot.get("operation_id")
            status["message"] = self.message
            status["detail"] = self.detail
            status["failed"] = bool(self.failed)
            status["waiting_seconds"] = (int(max(0.0, self._clock() - self._drain_started))
                                         if self._drain_started else 0)
            if self._phase in ("draining", "stopping", "unreachable"):
                status["can_force"] = bool(snapshot.get("can_force", True))
            else:
                status["can_force"] = False
            status["can_restart"] = bool(snapshot.get("can_restart")) if snapshot else (
                self._phase in ("ready", "stopped", "failed"))
            status["closed"] = bool(self._allow_close)
        return status

    def tray_action_enabled(self, action: str) -> bool:
        """托盘菜单按阶段禁用（原「启动服务」不再任何时刻可点）。"""
        alive = self.running()
        with self._cond:
            phase = self._phase
        if action == "start":
            return not alive and phase in ("stopped", "failed")
        if action == "stop":
            return alive and phase in ("starting", "ready")
        if action == "restart":
            return alive and phase == "ready"
        if action == "progress":
            return phase in ("draining", "stopping", "unreachable", "failed")
        return action == "quit"

    # ------------------------------------------------------------------
    # 意图登记（锁内只登记，绝不等待）
    # ------------------------------------------------------------------
    def start(self):
        return self._submit("start")

    def retry_start(self):
        return self._submit("start")

    def restart(self):
        return self._submit("restart")

    def stop_services(self):
        return self._submit("stop")

    def quit(self):
        return self._submit("quit")

    def apply_update(self):
        """更新：先校验/可写性预检，再登记 action=update 进入同一条串行路径。

        macOS 例外：.app 是个目录包，运行中替换内核（签名 / 启动器 / 资源）
        比 Windows 换 exe 危险得多，所以只打开下载目录让用户手动替换 ——
        「装不上」永远比「装坏了打不开」好。Windows 仍走排空 → 实退 → 安装器。
        """
        if not self.update_file or not self.update_state.get("ready"):
            return {"error": "请先完成下载和校验"}
        if sys.platform == "darwin":
            # 先把已验证的包亮给用户，再提示替换；整个过程不停服务、不排空。
            try:
                subprocess.run(["open", "-R", str(self.update_file)],
                               check=False, timeout=10, creationflags=_NO_WINDOW)
            except Exception:  # noqa: BLE001 - 没有 Finder 也要给出路径
                logger.debug("打开更新包所在目录失败", exc_info=True)
            message = "更新包已下载并打开所在目录，请手动替换应用"
            self.tray.notify("更新包已下载，已打开其所在目录；"
                             "退出本客户端后替换「应用程序」里的 MiraiHub 即可")
            return {"ok": True, "manual": True, "path": str(self.update_file),
                    "message": message}
        try:
            target = self._resolve_update_target()
            self._probe_target_writable(target)
        except Exception as exc:
            # 预检失败：继续服务，不动后端。
            return {"error": "无法安装更新：%s" % exc}
        return self._submit("update")

    def _submit(self, kind: str) -> dict:
        """登记一个用户意图。锁只保护状态/代号/队列，不做任何 I/O。"""
        self._refresh_liveness()
        with self._cond:
            if self._allow_close:
                return {"ok": False, "error": "客户端正在关闭", "phase": self._phase,
                        "operation_id": self._operation_id, "duplicate": True}
            if kind == "quit":
                # 「用户想退出」在排空最开始就记下：它压住自动重生，但**不放行关闭**。
                self._want_quit = True
            duplicate = False
            actual = kind
            operation_id = self._operation_id
            if kind == "start":
                duplicate = (self._phase == "starting" or "start" in self._pending
                             or (self._phase == "ready" and self._generation is not None))
            elif kind == "restart":
                if (self._phase in ("starting", "draining", "stopping")
                        or "restart" in self._pending or "start" in self._pending):
                    duplicate = True
                elif self._generation is None or self._phase != "ready":
                    # 没有可重启的服务：按启动处理（不制造第二次 Popen）。
                    actual = "start"
                    duplicate = "start" in self._pending
                if not duplicate:
                    self._restart_op = self._restart_op or ("restart-" + secrets.token_hex(8))
                    operation_id = self._restart_op
            elif kind == "stop":
                duplicate = "stop" in self._pending or self._generation is None
            elif kind == "update":
                duplicate = bool(self._pending) or self._phase in ("draining", "stopping")
            if not duplicate and actual in self._pending:
                duplicate = True
            if not duplicate:
                self._pending.append(actual)
                self._cond.notify_all()
            receipt = self._receipt_locked(actual, duplicate=duplicate,
                                           operation_id=operation_id)
        # 线程创建放在锁外；_ensure_thread 自身幂等，重复点击不会开第二条控制线。
        self._ensure_thread()
        return receipt

    def _receipt_locked(self, kind, *, duplicate=False, operation_id=None):
        if operation_id is None:
            operation_id = self._restart_op if kind == "restart" else self._operation_id
        return {"ok": True, "kind": kind, "duplicate": bool(duplicate),
                "phase": self._phase, "action": self._action,
                "instance_id": self._instance, "operation_id": operation_id}

    def _refresh_liveness(self):
        """锁外探测一次存活；确认已退出的同代在这里收尾（**不**新建进程）。"""
        generation = self.current_generation
        if generation is None:
            return
        with self._cond:
            phase = self._phase
        if phase in ("draining", "stopping", "starting"):
            return
        code = self._poll(generation)
        if code is None:
            return
        self._retire(generation, code)
        if phase == "ready":
            self._fail("本地服务已退出（退出码 %s），可以点击重试" % code)

    # ------------------------------------------------------------------
    # 串行控制器
    # ------------------------------------------------------------------
    def _ensure_thread(self):
        with self._cond:
            if self._allow_close:
                return
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._controller_loop,
                                            name="desktop-controller", daemon=True)
            thread = self._thread
        thread.start()
        self._start_monitor()

    def _start_monitor(self):
        with self._cond:
            if self._monitor is not None:
                return
            self._monitor = threading.Thread(target=self._state_monitor,
                                             name="desktop-state", daemon=True)
            monitor = self._monitor
        monitor.start()

    def _controller_loop(self):
        while True:
            with self._cond:
                while not self._pending:
                    if self._allow_close:
                        return
                    self._cond.wait(0.25)
                kind = self._pending.popleft()
                self._cond.notify_all()
            with self._run_lock:
                try:
                    getattr(self, "_op_" + kind)()
                except Exception as exc:      # noqa: BLE001 - 控制器不能把进程带崩
                    logger.exception("桌面控制器执行 %s 失败", kind)
                    self._fail("操作失败：%s" % exc)
            with self._cond:
                self._cond.notify_all()
                if self._allow_close and not self._pending:
                    return

    # -- 各意图 ------------------------------------------------------------
    def _op_start(self):
        generation = self.current_generation
        if generation is not None:
            code = self._poll(generation)
            if code is None:
                if generation.ready:
                    # 已就绪：返回已有工作台，绝不再起第二个进程。
                    self._enter_workbench(generation)
                    return
                # 同一代正在启动：只报告状态（重试会继续探测这个活着的孩子）。
                self._await_ready(generation)
                return
            # 确认孩子退出之后才允许新建。
            self._retire(generation, code)
        if self._quit_requested():
            return
        self._begin_generation()

    def _op_stop(self):
        if self.current_generation is None:
            self._after_stop()
            return
        code = self._shutdown("stop")
        if code is None:
            return
        self._after_stop()

    def _op_quit(self):
        with self._cond:
            self._want_quit = True
        if self.current_generation is None:
            # 已停止时退出：直接收壳。
            self._complete_quit()
            return
        code = self._shutdown("quit")
        if code is None:
            return          # 还没实退：保留壳与诊断，等用户确认强退
        self._complete_quit()

    def _op_restart(self):
        generation = self.current_generation
        if generation is None or self._poll(generation) is not None:
            if generation is not None:
                self._retire(generation, generation.exit_code)
            self._op_start()
            return
        code = self._shutdown("restart", generation)
        if code is None:
            return
        self._after_restart(code)

    def _op_update(self):
        with self._cond:
            self._installer_launched = False
            self._install_failed = False
        code = self._shutdown("update")
        if code is None:
            return
        self._run_installer()

    # -- 收尾 --------------------------------------------------------------
    def _after_stop(self):
        with self._cond:
            self._phase = "stopped"
            self._action = None
            self._operation_id = None
            self._restart_op = None
            self._drain_started = 0.0
            self._snapshot = {}
            self.message = "服务已停止"
            self.detail = "可以点击「重试启动」重新拉起本地服务。"
            self.failed = False
            self._cond.notify_all()
        self._set_tray("stopped")
        self.tray.notify("服务已停止")
        if self._window_alive():
            # 已经排空且进程实退：此刻切回自包含本地壳不会打断任何在途工作。
            try:
                self.window.load_html(SPLASH)
            except Exception:
                logger.debug("无法刷新停止界面", exc_info=True)

    def _after_restart(self, code):
        if self._quit_requested():
            # 用户意图优先于自动重生（父进程持有的 stop/quit 压过 worker 的回执）。
            self._complete_quit()
            return
        if code in (PLANNED_RESTART_CODE, NORMAL_EXIT_CODE):
            self._begin_generation()
            return
        self._fail("本地服务重启失败（退出码 %s），可以点击重试" % code)

    def _complete_quit(self):
        self._want_quit = True
        with self._cond:
            if not self.failed:
                self._phase = "stopped"
                self.message = "服务已停止"
                self.detail = ""
            self._allow_close = True
            self._cond.notify_all()
        self._closing.set()
        self._close_windows_once()
        try:
            self.tray.stop()
        except Exception:
            logger.debug("停止托盘图标失败", exc_info=True)
        self._arm_late_exit_guard()

    def _arm_late_exit_guard(self):
        """GUI 循环偶发不返回时的兜底 —— **严格后移到后端已实退之后**。

        老代码在 quit() 里开了个 3 秒 os._exit(0)：排空还没结束就把进程带走，
        正在落库的请求直接断在中间。现在这个兜底只在「后端已退出 + 窗口已
        销毁」这个终点之后计时，且计时期间不再有任何业务在跑。
        """
        if self._late_exit_seconds <= 0:
            return
        threading.Thread(target=self._late_exit_watch, name="desktop-late-exit",
                         daemon=True).start()

    def _late_exit_watch(self):
        self._sleep(self._late_exit_seconds)
        if self._windows():
            logger.warning("GUI 循环在 %.0f 秒内未退出，兜底结束进程（后端已退出）",
                           self._late_exit_seconds)
            os._exit(0)

    def finish(self):
        """run() 的 finally：幂等收尾。正常路径早已完成退出。"""
        with self._cond:
            if self._finished:
                return
            self._finished = True
            self._want_quit = True
            if self._allow_close:
                return
        self._monitor_stop.set()
        acquired = self._run_lock.acquire(timeout=5)
        if not acquired:
            logger.warning("退出时控制器仍在处理，交给它自己收尾")
            return
        try:
            generation = self.current_generation
            if generation is not None and self._poll(generation) is None:
                self._shutdown("quit", generation)
            with self._cond:
                self._allow_close = True
        finally:
            self._run_lock.release()
        self._close_windows_once()

    # ------------------------------------------------------------------
    # 关准入 -> 排空 -> 后端实退
    # ------------------------------------------------------------------
    def _shutdown(self, kind, generation=None):
        """执行一次「关准入 -> 排空 -> 等真实退出」，返回退出码（None = 还没退）。

        顺序是计划任务 4 的硬要求：先 BEGIN 关准入，业务侧写完尾帧后进程
        才退出；父进程只有看到真实退出码（或句柄 poll 出结果）才继续。
        """
        generation = generation if generation is not None else self.current_generation
        if generation is None:
            return None
        code = self._poll(generation)
        if code is not None:
            self._retire(generation, code)
            return code
        action = _ACTION_FOR.get(kind, "stop")
        operation_id = self._operation_for(kind)
        receipt = self._seal(generation, action, operation_id)
        if not receipt.get("accepted"):
            self._publish_unreachable(action, operation_id)
            return None
        action = receipt.get("action") or action
        operation_id = receipt.get("operation_id") or operation_id
        self._publish_sealed(generation, action, operation_id, receipt)
        code = self._drain(generation, action)
        if code is None:
            with self._cond:
                self._phase = "stopping"
                self.message = "请求已完成，正在关闭服务"
                self.detail = "正在等待本地服务进程退出。"
                self._cond.notify_all()
            self._set_tray("stopping")
            return None
        self._retire(generation, code)
        return code

    def _operation_for(self, kind):
        """同一种意图复用同一个 operation_id（双击不产生第二个操作）。"""
        with self._cond:
            if kind == "restart":
                self._restart_op = self._restart_op or ("restart-" + secrets.token_hex(8))
                return self._restart_op
            key = "stop" if kind in ("stop", "quit") else kind
            if not self._ops.get(key):
                self._ops[key] = key + "-" + secrets.token_hex(8)
            return self._ops[key]

    def _seal(self, generation, action, operation_id):
        """向本代 worker 请求封口。连不上只代表「暂时观测不到」，绝不杀进程。"""
        control = _control_module()
        deadline = self._clock() + self._seal_timeout
        call_timeout = max(1.0, min(5.0, self._seal_timeout))
        while True:
            try:
                reply = generation.owner.begin(action, operation_id,
                                               generation.instance_id, timeout=call_timeout)
            except control.ControlRefused as exc:
                # worker 明确拒绝：采用它已经受理的动作（先到先得）。
                snapshot = dict(getattr(exc, "snapshot", {}) or {})
                return {"accepted": True, "adopted": True, "snapshot": snapshot,
                        "action": snapshot.get("action") or action,
                        "operation_id": snapshot.get("operation_id") or operation_id}
            except control.ControlUnavailable:
                if self._poll(generation) is not None:
                    return {"accepted": True, "process_gone": True, "snapshot": {},
                            "action": action, "operation_id": operation_id}
                if self._clock() > deadline:
                    return {"accepted": False, "unreachable": True}
                self._sleep(0.2)
            except Exception:                 # noqa: BLE001 - 通道实现异常按失联处理
                logger.exception("控制通道调用失败")
                if self._clock() > deadline:
                    return {"accepted": False, "unreachable": True}
                self._sleep(0.2)
            else:
                snapshot = dict(reply.get("snapshot") or {})
                return {"accepted": True, "adopted": False, "snapshot": snapshot,
                        "action": reply.get("action") or action,
                        "operation_id": reply.get("operation_id") or operation_id}

    def _publish_sealed(self, generation, action, operation_id, receipt):
        with self._cond:
            if generation is not self._generation:
                return
            self._phase = "draining"
            self._action = action
            self._operation_id = operation_id
            self._drain_started = self._clock()
            self._accepting = False
            snapshot = dict(receipt.get("snapshot") or {})
            if snapshot:
                self._snapshot = {name: snapshot.get(name) for name in SNAPSHOT_FIELDS}
            self.message, self.detail = self._progress_text_locked(action)
            self._cond.notify_all()
        self._set_tray("draining")

    def _publish_unreachable(self, action, operation_id):
        with self._cond:
            self._phase = "unreachable"
            self._action = action
            self._operation_id = operation_id
            if not self._drain_started:
                self._drain_started = self._clock()
            self.message = "状态暂不可用，正在重试观测"
            self.detail = "控制通道暂时没有应答，进程仍在；可以查看诊断或强制结束。"
            self._cond.notify_all()
        self._set_tray("failed")

    def _drain(self, generation, action):
        """轮询本代快照直到**进程真的退出**；没有任何自动强退秒表。"""
        while True:
            code = self._poll(generation)
            if code is not None:
                return code
            self._publish_progress(generation, action, self._read_snapshot(generation))
            self._sleep(self._poll_interval)

    def _read_snapshot(self, generation):
        if generation.owner is None:
            return {}
        try:
            return generation.owner.status()
        except Exception:                    # noqa: BLE001 - 观测失败不能卡住排空
            logger.debug("读取本代快照失败", exc_info=True)
            return {}

    def _publish_progress(self, generation, action, snapshot):
        tray_state = None
        with self._cond:
            if generation is not self._generation:
                return
            if snapshot:
                self._snapshot = {name: snapshot.get(name) for name in SNAPSHOT_FIELDS}
                if snapshot.get("instance_id"):
                    self._instance = snapshot["instance_id"]
                phase = snapshot.get("phase")
                if phase in ("draining", "stopping", "stopped", "failed", "unreachable"):
                    self._phase = phase
                tray_state = {"stopping": "stopping", "unreachable": "failed",
                              "failed": "failed"}.get(phase, "draining")
            self.message, self.detail = self._progress_text_locked(action)
            self._cond.notify_all()
        if tray_state:
            self._set_tray(tray_state)

    def _progress_text_locked(self, action):
        """进度文案：数量 / 已等待秒数 / 完成后会怎样（计划任务 4 第 7 条）。"""
        if self._phase == "stopping":
            message = "请求已完成，正在关闭服务"
        elif self._phase == "unreachable":
            message = "状态暂不可用，正在重试观测"
        else:
            headline = _HEADLINE.get(action or "stop", "服务正在停止")
            requests = self._snapshot.get("active_requests")
            jobs = self._snapshot.get("active_jobs")
            if requests is None:
                message = headline + " · 剩余数量状态未知"
            else:
                parts = ["剩余 %d 个请求" % int(requests)]
                if jobs:
                    parts.append("%d 个后台任务" % int(jobs))
                message = headline + " · " + "、".join(parts)
        waited = (int(max(0.0, self._clock() - self._drain_started))
                  if self._drain_started else 0)
        closing = ("完成后会自动重启。" if action == "restart" else "完成后会自动关闭。")
        return message, "已等待 %d 秒。%s" % (waited, closing)

    def _retire(self, generation, code):
        """只有确认退出才清空 process / port / 本代记录。"""
        with self._cond:
            generation.exit_code = code
            if self._generation is not generation:
                return
            self._generation = None
            self.port = 0
            self._drain_started = 0.0
            self._snapshot = {}
            self._action = None
            self._operation_id = None
            self._ops = {}
            self._phase = ("stopped" if code in (PLANNED_RESTART_CODE, NORMAL_EXIT_CODE)
                           else "failed")
            self._cond.notify_all()

    def _fail(self, message, *, tray_state="failed"):
        with self._cond:
            self.failed = True
            self._phase = "failed"
            self.message = message
            self.detail = "可以查看诊断、重试启动，或强制结束仍在运行的进程。"
            self._cond.notify_all()
        self._set_tray(tray_state)
        try:
            self.tray.notify(message)
        except Exception:
            logger.debug("托盘通知失败", exc_info=True)
        logger.warning("桌面壳状态失败：%s", message)

    def _set_tray(self, state):
        if self._tray_state == state:
            return
        self._tray_state = state
        try:
            self.tray.set_state(state)
        except Exception:
            logger.debug("托盘状态刷新失败", exc_info=True)

    def _take_stop_intent(self):
        """冷启动期间点的停/退出/更新：取走最早的一个，用来在最早可联系时 BEGIN。"""
        with self._cond:
            for kind in ("quit", "stop", "update"):
                if kind in self._pending:
                    self._pending.remove(kind)
                    if kind == "quit":
                        self._want_quit = True
                    self._cond.notify_all()
                    return kind
        return None

    def _quit_requested(self):
        with self._cond:
            return self._want_quit

    def _poll(self, generation):
        if generation is None or generation.process is None:
            return None
        if generation.exit_code is not None:
            return generation.exit_code
        code = generation.process.poll()
        if code is not None:
            generation.exit_code = code
        return code

    # ------------------------------------------------------------------
    # 启动一代
    # ------------------------------------------------------------------
    def _prepare_generation(self):
        """锁外准备本代配置：先选端口、再生成代号与仅本机的控制密钥。"""
        control = _control_module()
        port = int(self._port_picker(int(self.config.get("port", DEFAULT_PORT))))
        instance_id = secrets.token_hex(16)
        nonce = secrets.token_urlsafe(32)
        control_dir = self.base / "run" / ("%d-%s" % (port, instance_id[:8]))
        # supervised=True：本进程会负责拉起下一代，Web 重启才有所有者。
        control_env = control.prepare_control_secrets(control_dir, instance_id=instance_id)
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(ROOT / "desktop_entry.py"))
        command += ["--backend", str(port)]
        env = dict(os.environ, **control_env,
                   WB_DESKTOP_PARENT=str(os.getpid()), WB_DESKTOP_BOOT=nonce)
        return Generation(instance_id=instance_id, nonce=nonce, port=port,
                          control_dir=control_dir, authkey=control_env[control.AUTHKEY_ENV],
                          env=env, command=command)

    def _spawn_backend(self, generation):
        output = (self.base / "logs/backend.log").open("w", encoding="utf-8")
        try:
            return subprocess.Popen(
                generation.command, env=generation.env, stdout=output, stderr=output,
                creationflags=_NO_WINDOW,
                # POSIX：独立进程组，退出时能整组带走（macOS 没有 taskkill /T）。
                start_new_session=(os.name != "nt"))
        finally:
            output.close()

    def _make_owner(self, generation, process):
        control = _control_module()
        return control.WorkerOwner(generation.control_dir, generation.authkey,
                                   popen=process)

    def _begin_generation(self):
        self.failed = False
        try:
            generation = self._prepare_generation()
        except Exception as exc:          # noqa: BLE001 - 端口/密钥准备失败要可诊断
            self._fail("无法准备本地服务：%s" % exc)
            return
        with self._cond:
            self._generation = generation
            self._phase = "starting"
            self._action = None
            self._operation_id = None
            self._instance = generation.instance_id
            self._snapshot = {}
            self._drain_started = 0.0
            self.message = "正在启动本地服务…"
            self.detail = ""
            self._cond.notify_all()
        self._set_tray("starting")
        try:
            # 代号在 Popen **之前**就已登记：这期间任何重复 start 只会看到状态。
            process = self._spawn(generation)
        except Exception as exc:          # noqa: BLE001
            self._fail("本地服务启动失败：%s" % exc)
            self._retire(generation, generation.exit_code if generation.exit_code is not None
                         else -1)
            return
        with self._cond:
            generation.process = process
            self._cond.notify_all()
        try:
            generation.owner = self._owner_factory(generation, process)
        except Exception:                 # noqa: BLE001
            logger.exception("控制通道初始化失败")
            generation.owner = None
        # 句柄发布后**重读待处理意图**：冷启动期间点的停/退出在这里接上，
        # 最早能联系到孩子的时刻就执行 BEGIN。
        intent = self._take_stop_intent()
        if intent:
            code = self._shutdown(intent, generation)
            if code is not None:
                self._post_intent(intent)
            return
        self._await_ready(generation)

    def _post_intent(self, kind):
        if kind == "quit" or self._quit_requested():
            self._complete_quit()
            return
        if kind == "stop":
            self._after_stop()
            return
        if kind == "update":
            self._run_installer()
            return
        if kind == "restart":
            self._after_restart(PLANNED_RESTART_CODE)

    def _await_ready(self, generation):
        """等本代就绪：查 nonce、复查 instance_id、盯着真实进程与用户意图。"""
        deadline = self._clock() + self._start_timeout
        while True:
            intent = self._take_stop_intent()
            if intent:
                # 冷启动中退出：记下目标，最早可联系时 BEGIN，然后按意图收尾。
                code = self._shutdown(intent, generation)
                if code is not None:
                    self._post_intent(intent)
                return
            probe = None
            try:
                probe = self._readiness(generation)
            except Exception:             # noqa: BLE001 - 探测失败等于没就绪
                probe = None
            if isinstance(probe, dict) and probe.get("ready"):
                reported = probe.get("instance_id")
                if reported and reported != generation.instance_id:
                    # 端口被另一实例接管：即使 TCP 通也不能当成自己这一代。
                    self._fail("控制台端口已被另一个实例接管，请重试启动")
                    return
                generation.ready = True
                generation.verified = bool(reported)
                # 确认本代就绪之后、发布 ready 之前落盘端口：任何看到 ready 的
                # 调用方读到的端口都已经是最新的，不存在"先看到就绪再读到旧端口"。
                try:
                    save_port(self.base, self.config, generation.port)
                except Exception:         # noqa: BLE001 - 落盘失败不能让启动失败
                    logger.exception("保存端口失败（忽略）")
                with self._cond:
                    if generation is not self._generation:
                        return
                    self._phase = "ready"
                    self._action = None
                    self._operation_id = None
                    self._restart_op = None
                    self._drain_started = 0.0
                    self._instance = reported or generation.instance_id
                    self.port = generation.port
                    self.message = "本地服务已就绪"
                    self.detail = ""
                    self.failed = False
                    self._cond.notify_all()
                self._set_tray("running")
                self._enter_workbench(generation)
                self._maybe_smoke_test()
                return
            code = self._poll(generation)
            if code is not None:
                self._retire(generation, code)
                self._fail("本地服务启动失败（退出码 %s），详细原因见 logs/backend.log" % code)
                return
            if self._clock() > deadline:
                # 孩子还活着：保留句柄与阶段，重试会继续探测它，而不是再起一个。
                self._fail("本地服务启动超时，可以点击重试（进程仍在运行）")
                return
            self._sleep(0.15)

    def _http_readiness(self, generation):
        """默认就绪判定：本代 nonce + 回执里的 instance_id。"""
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (generation.port, READY_PATH),
            headers={"X-Desktop-Boot": generation.nonce})
        try:
            with urllib.request.urlopen(request, timeout=1) as response:
                payload = json.load(response)
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _confirm_ready(self, generation, attempts=4, interval=0.25):
        """打开浏览器前的**新鲜**就绪判定（绝不用 TCP 通就发登录信息）。"""
        for index in range(max(1, attempts)):
            try:
                probe = self._readiness(generation)
            except Exception:             # noqa: BLE001
                probe = None
            if isinstance(probe, dict) and probe.get("ready"):
                reported = probe.get("instance_id")
                if not reported or reported == generation.instance_id:
                    return True
            if index + 1 < attempts:
                self._sleep(interval)
        return False

    def _enter_workbench(self, generation):
        with self._cond:
            if generation is not self._generation or not generation.ready:
                return
            if self._phase != "ready":
                return                # 排空/关闭中绝不重新导航（会打断在途工作）
            port = generation.port
        if not self._window_alive():
            if self.tray_mode:
                self.tray.notify("服务已启动 · 控制台端口 %d" % port)
            return
        try:
            # 桌面壳免登：把一次性 `local_shell` 令牌放进 URL，服务端换 HttpOnly
            # Cookie 后 303 把令牌从地址栏抹掉。
            #
            # 为什么不用 JS 桥（`pywebview.api.desktop_login()`）：pywebview 的
            # 返回值回调表 `_returnValuesCallbacks` 在 Vue 的 hash 路由跳转
            # （`/` → `#/overview`）之后会失效，调用报
            # `... is not a function`，令牌永远换不到 → 页面 401 → 重载 → 死循环。
            # URL 换票不依赖任何 JS 回调，落地即生效。
            token = self._local_shell_token()
            self.window.load_url(
                "http://127.0.0.1:%d/admin?desktop=1&token=%s" % (port, token))
        except Exception:
            logger.exception("进入工作台失败")

    def _maybe_smoke_test(self):
        if os.getenv("WB_DESKTOP_SMOKE"):
            threading.Thread(target=self._smoke_test, name="desktop-smoke",
                             daemon=True).start()

    def _smoke_test(self):
        result = {"ok": False, "version": VERSION, "port": self.port}
        # 版本号在界面上的落点变了：旧控制台是 `#appVersion`（页脚），
        # 新版 Vue 后台是侧栏底部 `.foot-version .ver`。两个都认，
        # 否则后台换皮之后打包冒烟会永远等不到 ready。
        probe = (
            "(() => {"
            "  const app = document.getElementById('app');"
            "  if (!app || app.classList.contains('hidden')) return '';"
            "  const el = document.querySelector('.foot-version .ver')"
            "          || document.getElementById('appVersion');"
            "  return el ? String(el.textContent || '') : '';"
            "})()"
        )
        want = "v" + VERSION
        try:
            for _ in range(90):
                if self._closing.wait(.5):
                    break
                text = self.window.evaluate_js(probe) or ""
                if want in text:
                    result.update(ok=True, title=self.window.evaluate_js("document.title"),
                                  footer=text)
                    break
        except Exception as exc:          # noqa: BLE001
            result["error"] = str(exc)
        finally:
            atomic_json(Path(os.environ["WB_DESKTOP_SMOKE"]), result)
            # 打包验证要真的结束进程：先放行关闭，再只销毁一次。
            with self._cond:
                self._allow_close = True
            self._close_windows_once()

    # ------------------------------------------------------------------
    # 窗口与本地壳
    # ------------------------------------------------------------------
    def _window_alive(self, window=None):
        window = self.window if window is None else window
        if window is None:
            return False
        try:
            return window in (self._windows() or [])
        except Exception:                 # noqa: BLE001
            return False

    def _create_window(self):
        import webview
        window = webview.create_window(
            "MiraiHub · v%s" % VERSION, html=SPLASH, js_api=Bridge(self),
            width=1280, height=860, min_size=(900, 600),
            background_color="#f5f6f8", text_select=True,
            # 无边框窗口是 Windows 定制的拖动区方案；macOS 交给原生标题栏。
            frameless=(os.name == "nt"), easy_drag=False)
        # 首窗口与「第二次打开/恢复」绑定同一套 closing/closed 处理。
        window.events.closed += self._on_window_closed
        window.events.closing += self.on_window_closing
        return window

    def request_window_close(self):
        """× 的统入口：托盘模式只隐藏，无托盘模式排队退出。

        返回 True 表示「现在可以真正关闭」。排空开始时**绝不**返回 True ——
        那会把窗口自己发起的 Agent/SSE 一起带走。
        """
        with self._cond:
            if self._allow_close:
                return True
            tray_mode = self.tray_mode
        if tray_mode:
            try:
                if self.window is not None:
                    self.window.hide()
                self.tray.notify("MiraiHub 已收进托盘，双击图标可重新打开")
            except Exception:
                logger.debug("隐藏窗口失败，改用默认关闭行为", exc_info=True)
                return True
            return False              # 对 backend 零调用：不停服务、不排空
        self.quit()
        return False

    def on_window_closing(self, *args):
        """pywebview 的 closing 事件：False = 拦下关闭，True = 放行。"""
        return self.request_window_close()

    def _on_window_closed(self, *args):
        self.window = None

    def open_window(self):
        """显示（或创建）嵌入控制台窗口。"""
        if self._window_alive():
            try:
                self.window.show()
                self.window.restore()
            except Exception:
                logger.exception("恢复窗口失败")
            generation = self.current_generation
            if generation is not None and generation.ready:
                self._enter_workbench(generation)
            return {"ok": True}
        try:
            self.window = self._create_window()
        except Exception as exc:          # noqa: BLE001
            logger.exception("窗口创建失败")
            self.window = None
            self.tray.notify("窗口创建失败，请改用「在浏览器中打开」")
            return {"error": str(exc)}
        _clamp_window_to_work_area(self.window)
        generation = self.current_generation
        if generation is not None and generation.ready:
            self._enter_workbench(generation)
        return {"ok": True}

    def open_progress_window(self):
        """托盘「查看退出进度」：唤回窗口并推非破坏性进度层（不 load_html）。"""
        result = self.open_window()
        self._push_progress_overlay()
        return result

    def _push_progress_overlay(self):
        """在**原页面**上显示进度层：只调 JS，不 load_html/load_url/destroy。"""
        window = self.window
        if window is None:
            return
        status = self.startup_status()
        payload = {name: status.get(name) for name in
                   ("phase", "action", "message", "detail", "active_requests",
                    "active_jobs", "can_force", "waiting_seconds")}
        script = ("window.WBDesktopLifecycle&&window.WBDesktopLifecycle.show?"
                  "window.WBDesktopLifecycle.show(%s):null"
                  % json.dumps(payload, ensure_ascii=False))
        try:
            window.evaluate_js(script)
        except Exception:
            logger.debug("进度层不可用（页面没有实现桥）", exc_info=True)

    def _close_windows_once(self):
        with self._cond:
            if self._destroyed:
                return
            self._destroyed = True
        destroyed = 0
        for window in list(self._windows() or []):
            try:
                window.destroy()
                destroyed += 1
            except Exception:
                logger.exception("销毁窗口失败")
        if not destroyed and self._window_alive():
            try:
                self.window.destroy()
            except Exception:
                logger.exception("销毁窗口失败")

    def _state_monitor(self):
        """后台意外退出的对账：只在 ready / unreachable 阶段动手。"""
        while not self._monitor_stop.wait(1.0):
            generation = self.current_generation
            if generation is None:
                continue
            if self._poll(generation) is None:
                continue
            with self._cond:
                phase = self._phase
                quitting = self._want_quit
            if phase in ("draining", "stopping", "starting", "stopped", "failed"):
                continue
            self._retire(generation, generation.exit_code)
            if quitting:
                self._complete_quit()
            elif phase == "ready":
                self._fail("本地服务意外退出（退出码 %s），可从托盘重试"
                           % generation.exit_code)

    # ------------------------------------------------------------------
    # 控制台 / 导出 / 诊断
    # ------------------------------------------------------------------
    def _is_local_console_url(self) -> bool:
        """只认本壳自己加载的本地控制台页（scheme/host/port/path 全核对）。

        曾经要求 `?desktop=1`：但 401 统一跳转是 `location.replace('/admin')`，
        查询串会被丢掉 —— 页面重载后凭据换发就永远过不了这道闸，壳陷入
        「401 → 重载 → 401」死循环。窗口内只会出现本服务自己的页面（外链一律
        交系统浏览器打开，见 webview.settings），因此查询串不再是安全判据。
        """
        current = urllib.parse.urlsplit(self.window.get_current_url() or "")
        return (current.scheme == "http"
                and current.hostname == "127.0.0.1"
                and current.port == self.port
                and current.path.rstrip("/") == "/admin")

    def save_text(self, filename, text, mime="application/json"):
        """Save only after the owner chooses a destination in the native picker."""
        if not self._is_local_console_url():
            return {"ok": False, "error": "只允许本地工作台导出文件"}
        if not isinstance(text, str) or len(text.encode("utf-8")) > 32 * 1024 * 1024:
            return {"ok": False, "error": "导出文件过大"}
        import re
        import webview
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', str(filename))[:120]
        if Path(name).suffix.lower() not in ('.info', '.json', '.jsonl', '.csv', '.txt'):
            return {"ok": False, "error": "不支持的导出文件类型"}
        try:
            selected = self.window.create_file_dialog(webview.FileDialog.SAVE, save_filename=name)
            if not selected:
                return {"ok": False, "cancelled": True}
            target = selected[0] if isinstance(selected, (list, tuple)) else selected
            Path(target).write_bytes(text.encode("utf-8"))
            return {"ok": True}
        except Exception:
            return {"ok": False, "error": "文件保存失败，请更换保存位置后重试"}

    def _local_shell_token(self) -> str:
        """签一个 `local_shell` 令牌（与 `desktop_login` 同一把密钥、同一份载荷）。

        只用于「进入工作台」时拼 URL 换 Cookie，**不落盘、不写日志**。
        """
        import jwt
        return jwt.encode({"sub": self.config["username"], "local_shell": True},
                          self.config["jwt_secret"], algorithm="HS256")

    def desktop_login(self):
        if not self._is_local_console_url():
            raise RuntimeError("只允许本地桌面界面登录")
        import jwt
        # Local ownership authenticates the shell; no password form or login request.
        # 控制通道的 authkey/nonce 绝不参与这里 —— 只签本机所有权会话。
        token = jwt.encode({"sub": self.config["username"], "local_shell": True},
                           self.config["jwt_secret"], algorithm="HS256")
        return {"token": token}

    def startup_diagnostic(self):
        from admin.request_metrics import safe_error_text
        import platform
        with self._cond:
            phase = self._phase
            instance = self._instance or "（无）"
            operation = self._operation_id or "（无）"
            message, detail = self.message, self.detail
            snapshot = dict(self._snapshot)
        generation = self.current_generation
        report = ("MiraiHub %s 启动诊断\n系统：%s\n阶段：%s\n文案：%s %s\n"
                  "本代代号：%s\n当前操作：%s\n环境：%s\n" % (
                      VERSION, platform.platform(), phase, message, detail,
                      instance, operation, self.runtime_message))
        if generation is None:
            report += "服务进程：未启动\n"
        else:
            code = self._poll(generation)
            report += "服务进程：PID %s，退出码 %s（None 表示运行中），端口 %s\n" % (
                getattr(generation.process, "pid", "?"), code, generation.port)
        if snapshot:
            report += "最近快照：%s\n" % json.dumps(
                {name: snapshot.get(name) for name in SNAPSHOT_FIELDS}, ensure_ascii=False)
        log = self.base / "logs/backend.log"
        if log.is_file():
            with log.open("rb") as source:
                source.seek(max(0, log.stat().st_size - 8192))
                report += "\n本地服务日志末尾（凭据脱敏）：\n" + source.read(8192).decode(
                    "utf-8", errors="replace")
        return {"report": safe_error_text(report)}

    def open_console(self):
        """在系统浏览器打开控制台：**只**在本代 nonce + instance_id 都核对后发登录信息。"""
        generation = self.current_generation
        if generation is None or not generation.ready or self._phase != "ready":
            self.tray.notify("本地服务尚未就绪，无法打开控制台")
            return {"error": "服务未就绪"}
        if not self._confirm_ready(generation):
            # 端口可能已经被另一实例接管：TCP 通、版本号相同都不算本代就绪。
            self.tray.notify("本地服务状态已变化，请稍后重试")
            return {"error": "服务未就绪"}
        import jwt
        token = jwt.encode({"sub": self.config["username"], "local_shell": True},
                           self.config["jwt_secret"], algorithm="HS256")
        url = "http://127.0.0.1:%d/admin?token=%s" % (generation.port, token)
        webbrowser.open(url)
        self.tray.notify("控制台已在浏览器打开（端口 %d，免登录）" % generation.port)
        return {"ok": True, "url": url}

    # ------------------------------------------------------------------
    # 强制结束
    # ------------------------------------------------------------------
    def force_stop(self, confirm=False):
        """手动强退：必须二次确认，并绑定当前代 + 当前操作。"""
        if confirm is not True:
            return {"error": "需要显式确认后才能强制结束"}
        generation = self.current_generation
        if generation is None:
            return {"ok": True, "phase": self.phase}
        with self._cond:
            operation_id = self._operation_id
            if operation_id is None:
                operation_id = self._operation_for("quit")
            can_force = self._phase in ("draining", "stopping", "unreachable")
            instance_id = generation.instance_id
        if not can_force:
            return {"error": "当前没有可加速的操作"}
        control = _control_module()
        try:
            receipt = generation.owner.force(operation_id, instance_id, True)
        except control.ControlRefused as exc:
            return {"error": "本次操作已不可加速（%s）" % (getattr(exc, "reason", "refused"),)}
        except Exception:                 # noqa: BLE001 - 无应答：只有所有者能收自己的进程树
            logger.warning("强制结束：控制通道无应答，由父进程结束本代进程树")
            _kill_process_tree(getattr(generation.process, "pid", None))
            self._settle_after_kill(generation)
            return {"ok": True, "forced_by_owner": True}
        self._settle_after_kill(generation)
        return {"ok": True, "receipt": {name: receipt.get(name) for name in
                                        ("ok", "action", "operation_id", "instance_id")}}

    def _settle_after_kill(self, generation):
        """强退之后由父进程补一次收尾（worker 可能已经没法自己走完）。"""
        def settle():
            for _ in range(600):
                if self._poll(generation) is not None:
                    self._retire(generation, generation.exit_code)
                    if self._quit_requested():
                        self._complete_quit()
                    else:
                        self._after_stop()
                    return
                self._sleep(0.1)
        threading.Thread(target=settle, name="desktop-force-settle", daemon=True).start()

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------
    def _resolve_update_target(self):
        if self._update_target is not None:
            return Path(self._update_target)
        return Path(sys.executable).resolve()

    def _probe_target_writable(self, target):
        """可写性预检：装不进去就别先停服务。"""
        source = Path(self.update_file)
        if not source.is_file():
            raise ValueError("更新包不存在，请重新下载")
        probe = target.parent / (".wb-update-%d.tmp" % os.getpid())
        probe.touch()
        probe.unlink()

    def _launch_installer(self, source, target, bootloader_pid):
        """启动现有的 PowerShell 安装脚本（等宿主退出后替换 EXE）。"""
        script = self.base / "updates" / "apply-update.ps1"
        script.parent.mkdir(parents=True, exist_ok=True)
        # 仓库里的脚本是不带 BOM 的 UTF-8；PowerShell 5.1 只有看到 BOM 才按
        # UTF-8 解析，所以运行时副本统一写 utf-8-sig（读时也按 utf-8-sig，
        # 脚本若将来带了 BOM 不会变成双 BOM）。
        script.write_text((ROOT / "desktop/apply-update.ps1").read_text(encoding="utf-8-sig"),
                          encoding="utf-8-sig")
        env = dict(os.environ, WB_UPDATE_SOURCE=str(source), WB_UPDATE_TARGET=str(target),
                   WB_UPDATE_PID=str(bootloader_pid),
                   WB_UPDATE_LOG=str(self.base / "logs/update.log"))
        subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive",
                          "-ExecutionPolicy", "Bypass", "-File", str(script)],
                         env=env, creationflags=_NO_WINDOW, close_fds=True)

    def _run_installer(self):
        with self._cond:
            if self._installer_launched:
                # 同一操作至多启动一次安装脚本。
                self._allow_close = True
                self._cond.notify_all()
                self._close_windows_once()
                return
        try:
            target = self._resolve_update_target()
            bootloader_pid = int(os.environ.get("WB_BOOTLOADER_PID", os.getpid()))
            self._installer(Path(self.update_file), target, bootloader_pid)
        except Exception as exc:          # noqa: BLE001
            logger.exception("更新安装器启动失败")
            with self._cond:
                self.failed = True
                self._install_failed = True
                self._phase = "failed"
                self.message = "更新安装器未能启动：%s" % exc
                self.detail = "旧版本与已验证的安装包都已保留，可以重试安装或启动旧版。"
                self._cond.notify_all()
            self._set_tray("failed")
            return
        with self._cond:
            self._installer_launched = True
            self._install_failed = False
        self._complete_quit()

    def retry_install(self):
        with self._cond:
            if self._installer_launched:
                return {"error": "本次更新已经启动过安装器"}
            if not self._install_failed:
                return {"error": "当前没有待重试的安装"}
            self._install_failed = False
        self._run_installer()
        return {"ok": True}

    def start_old_version(self):
        """「启动旧版」：安装器没启动成功时，直接用当前 EXE 重新开一个宿主。"""
        try:
            subprocess.Popen([sys.executable], close_fds=True,
                             creationflags=_NO_WINDOW)
        except Exception as exc:          # noqa: BLE001
            return {"error": "无法启动旧版本：%s" % exc}
        self._complete_quit()
        return {"ok": True}

    def check_updates(self):
        result = self.updates.check()
        result.update(runtime=self.runtime_message, update=self.update_state,
                      startup_prompt_handled=self.tray_startup_update_check)
        return result

    def _check_tray_startup_update(self):
        """A tray-only launch checks too, without starting the API service."""
        try:
            result = self.updates.check()
            latest = result.get("latest")
            if not result.get("available") or not latest or self._closing.is_set():
                return
            confirmed = _confirm(
                "发现新版本 %s（当前 v%s）。\n\n是否立即更新？\n"
                "选择「是」将下载、校验并重启客户端；选择「否」暂不更新。"
                % (latest["tag"], VERSION),
                "MiraiHub · 版本更新")
            if not confirmed or self._closing.is_set():
                return
            response = self.download_update(latest["id"])
            if response.get("error"):
                self.tray.notify(response["error"])
                return
            while self.update_state.get("busy"):
                if self._closing.wait(.25):
                    return
            if self.update_state.get("ready") and not self._closing.is_set():
                response = self.apply_update()
                if response.get("error"):
                    self.tray.notify(response["error"])
            else:
                self.tray.notify(self.update_state.get("message") or "更新下载失败，请稍后重试")
        except Exception:
            logging.exception("Tray startup update check failed")

    def update_status(self):
        return dict(self.update_state, runtime=self.runtime_message)

    def retry_runtime(self):
        if self.runtime_lock.acquire(blocking=False):
            threading.Thread(target=self._runtime_worker, daemon=True).start()
        return {"ok": True}

    def _runtime_worker(self):
        self.runtime_busy = True
        try:
            ensure_node(self.base, lambda message: setattr(self, "runtime_message", message))
            self.runtime_message = "Node.js 已就绪 · Python 与项目依赖已内置"
        except Exception as exc:
            self.runtime_message = str(exc)
            logging.exception("Node setup failed")
        finally:
            self.runtime_busy = False
            self.runtime_lock.release()

    def download_update(self, release_id):
        if not getattr(sys, "frozen", False):
            return {"error": "源码模式请使用 Releases 中的 EXE"}
        if not self.update_lock.acquire(blocking=False):
            return {"error": "更新下载正在进行"}
        self.update_state = {"busy": True, "message": "正在下载更新…", "ready": False}
        threading.Thread(target=self._download_worker, args=(release_id,), daemon=True).start()
        return {"ok": True}

    def _download_worker(self, release_id):
        try:
            self.update_file = self.updates.stage(
                release_id,
                lambda n, total: self.update_state.update(
                    message="下载更新：%.1f / %.1f MB" % (n / 1048576, total / 1048576)))
            self.update_state.update(busy=False, ready=True, message="校验通过，点击重启安装")
        except Exception as exc:
            self.update_state.update(busy=False, ready=False, message=str(exc))
            logging.exception("Update download failed")
        finally:
            self.update_lock.release()


def run(tray_mode: bool = False):
    """桌面向前端入口。

    tray_mode: 双击 EXE 或开机自启时的默认路径 —— 托盘是常驻兜底，
    主窗口同时创建；窗口模式（--no-tray / --smoke-test）关闭窗口即退出。
    """
    if sys.platform not in ("win32", "darwin"):
        raise RuntimeError("桌面版支持 Windows 10/11 x64 与 macOS 12+")
    import webview
    base = data_dir()
    # Use a per-user lock rather than a PID file (stale PID files cannot block startup).
    lock = (base / "desktop.lock").open("a+b")
    lock.seek(0)
    if not _lock_instance(lock):
        _message_box("MiraiHub 已在运行，请查看系统托盘图标。", "MiraiHub", 64)
        lock.close()
        return
    # macOS 的托盘（pystray 的 AppKit 后端）和 pywebview 的 Cocoa 主循环抢同一个
    # run loop，同时跑会有一个起不来。macOS 上固定走窗口模式，托盘菜单不提供。
    if sys.platform == "darwin" and tray_mode:
        logging.getLogger("desktop").info("macOS 上不使用托盘，改为窗口模式")
        tray_mode = False
    api = tray = None
    try:
        config = configure_environment(base)
        setup_logging(base)

        # 清掉上次硬杀留下的 _MEI 目录（关机/重启/结束进程都不会触发自删）。
        # 放在 setup_logging 之后，这样清理量能记进日志；放在起服务之前，
        # 避免边跑边删占磁盘。
        try:
            _freed = cleanup_stale_mei_dirs()
            if _freed:
                logging.getLogger("desktop").info(
                    "已清理上次残留的 _MEI 运行目录，释放 %.1f MB", _freed / 1048576)
        except Exception:
            logging.getLogger("desktop").debug("清理 _MEI 目录失败", exc_info=True)
        ensure_webview2(base)
        api = DesktopAPI(base, config, tray_mode=tray_mode)
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

        # 启动阶段诊断：windowed EXE 没有控制台，窗口不出时无从下手。
        # 每一步落到 <data>/logs/desktop-boot.log，事后能一眼看出停在哪。
        def _boot(mark: str) -> None:
            try:
                (base / "logs").mkdir(parents=True, exist_ok=True)
                with (base / "logs" / "desktop-boot.log").open("a", encoding="utf-8") as fh:
                    fh.write("%s %s\n" % (time.strftime("%H:%M:%S"), mark))
            except Exception:
                pass

        _boot("run():tray_mode=%s" % tray_mode)
        if tray_mode:
            tray = TrayController(api, ROOT, VERSION)
            api.tray = tray
            started = tray.start()
            _boot("tray.start()=%s" % started)
            if not started:
                # 托盘导入失败：回退到窗口模式，但保留同一套控制器。
                _message_box("托盘组件不可用，将以窗口模式启动。\n\n数据和日志：" + str(base),
                             "MiraiHub", 48)
                tray_mode = False
                api.tray_mode = False
                api.tray = _NullTray()
                tray = None

        if not tray_mode:
            # 窗口模式：× 的第一次由控制器拦下（排队 quit），后端实退后才销毁。
            api.window = api._create_window()
            api.tray_mode = False
            _boot("window created (window mode)")

            def start_window_shell():
                _clamp_window_to_work_area(api.window)
                api.retry_runtime()
                api.retry_start()

            webview.start(start_window_shell,
                          gui=_webview_gui(), private_mode=False,
                          storage_path=str(base / "webview"),
                          icon=_window_icon())
            _boot("webview.start() returned (window mode)")
            return

        # Tray mode: 双击 EXE 就**自动起服务并直接显示主窗口**，
        # 托盘继续驻留以便关闭窗口后还能唤回。
        api._start_monitor()          # 幂等：控制器里也会确保同一个对账线程
        tray.notify("MiraiHub 已启动，正在打开界面…")
        api.tray_startup_update_check = True

        api.window = api._create_window()
        _boot("window created (tray mode)")

        def start_tray_shell():
            # 先起服务，再等就绪后导航：服务没起来时页面自己显示阶段与重试按钮。
            _boot("start_tray_shell entered")
            _clamp_window_to_work_area(api.window)
            api.retry_runtime()
            api.retry_start()
            threading.Thread(target=api._check_tray_startup_update,
                             name="startup-update", daemon=True).start()

        webview.start(start_tray_shell, gui=_webview_gui(),
                      private_mode=False, storage_path=str(base / "webview"),
                      icon=_window_icon())
    except Exception as exc:
        logging.exception("Desktop failed")
        # windowed EXE 没有控制台，MessageBox 又可能在无人值守时被忽略；
        # 把完整堆栈落到数据目录，事后能查（这条救过好几次打包问题）。
        try:
            import traceback
            (base / "logs").mkdir(parents=True, exist_ok=True)
            with (base / "logs" / "desktop-crash.log").open("a", encoding="utf-8") as fh:
                fh.write("=" * 60 + "\n")
                fh.write(str(exc) + "\n")
                fh.write(traceback.format_exc() + "\n")
        except Exception:
            pass
        _message_box(str(exc) + "\n\n数据和日志：%s" % base, "MiraiHub", 16)
    finally:
        # 幂等：正常路径早已退出；异常路径做一次有界收尾（父进程留在孩子关闭前）。
        if api:
            api.finish()
        if tray:
            tray.stop()
        lock.close()
