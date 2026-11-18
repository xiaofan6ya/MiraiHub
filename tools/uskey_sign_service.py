"""独立的 uskey 签名服务 —— 在 wine 的 python 里跑，给 Linux 网关用。

## 场景

    Linux 上的网关不能 ctypes 加载 Windows PE，所以把「签名」这一步
    交给一个跑在 wine 里的轻量服务。网关只发 HTTP。

## 部署（Linux）

    1. 装 wine 与一个 Windows 版 python（wine 里能跑的）
       Ubuntu/Debian:
           sudo apt install wine64
       Windows python 建议用 embeddable 包（无需安装器）：
           curl -LO https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip
           mkdir -p /opt/marvis-sign/py && cd /opt/marvis-sign/py && unzip -q ../python-3.11.9-embed-amd64.zip

    2. 把两个 DLL 和本脚本放一起
           /opt/marvis-sign/
               beacon_wrapper.dll
               qimei.dll
               uskey_sign_service.py

    3. 起服务（wine 会把 D:\\ 映射到 ~/.wine/dosdevices/d:，这里直接用相对路径）
           cd /opt/marvis-sign
           wine py/python.exe uskey_sign_service.py --port 8791

    4. 网关侧设置
           export MARVIS_USKEY_SERVICE=http://127.0.0.1:8791/sign
       然后 `uskey_signer` 会自动走远程模式。

    5. 用 systemd 常驻（示例见文末注释）

## 为什么用 HTTP

    wine 的 unix socket 支持不稳定，loopback TCP 没问题。
    只监听 127.0.0.1，外面访问不到。

## 接口

    GET  /health         -> {"ok": true, "mode": "wine", "dll": "..."}
    POST /sign           -> {"ok": true, "uskey": "..."}
         body: {"busiid": "...", "bus_info": "..."}
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: 输出缓冲大小。uskey 是 376/392 字节，base64 后 504/524 字符，
#: 给 16KB 足够（实测 buf 不足会返回 -505）。
BUF_SIZE = 16384

#: beacon_get_uskey 的 busiid 上限 9 字节，超过会让输出多 16 字节。
BUSIID_MAX = 9


class Signer:
    """进程内单例：加载一次 DLL，之后复用。"""

    def __init__(self, dll_path: str):
        self.dll_path = os.path.abspath(dll_path)
        self.error = ""
        self._lib = None
        self._fn = None
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        d = os.path.dirname(self.dll_path)
        if hasattr(os, "add_dll_directory") and d:
            try:
                os.add_dll_directory(d)
            except OSError:
                pass
        try:
            lib = ctypes.CDLL(self.dll_path)
        except Exception as exc:  # noqa: BLE001
            self.error = "加载 DLL 失败：%s: %s" % (type(exc).__name__, exc)
            return

        init = getattr(lib, "beacon_init", None)
        if init is not None:
            init.argtypes = []
            init.restype = ctypes.c_int
            try:
                init()
            except Exception as exc:  # noqa: BLE001
                self.error = "beacon_init 失败：%s" % exc
                return

        fn = getattr(lib, "beacon_get_uskey", None)
        if fn is None:
            self.error = "DLL 里没有 beacon_get_uskey 导出"
            return
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                       ctypes.c_int]
        fn.restype = ctypes.c_int

        # 自检
        probe = ctypes.create_string_buffer(BUF_SIZE)
        ret = fn(b"7800255", b"marvisAgentTag=main", probe, BUF_SIZE)
        if ret < 0:
            self.error = "自检返回 %d（DLL 没正常工作）" % ret
            return

        self._lib = lib
        self._fn = fn

    def sign(self, busiid: str, bus_info: str) -> str:
        if self._fn is None:
            raise RuntimeError(self.error or "签名器未就绪")
        busiid = (busiid or "7800255")[:BUSIID_MAX]
        with self._lock:
            buf = ctypes.create_string_buffer(BUF_SIZE)
            ret = self._fn(busiid.encode("utf-8"),
                           bus_info.encode("utf-8"), buf, BUF_SIZE)
            if ret < 0:
                raise RuntimeError("beacon_get_uskey 返回 %d" % ret)
            text = buf.value.decode("ascii", "replace").strip()
        if not text:
            raise RuntimeError("beacon_get_uskey 返回空")
        return text


_signer: Signer | None = None


class Handler(BaseHTTPRequestHandler):
    server_version = "MarvisUskeySign/1.0"

    # ## 为什么开 HTTP/1.1
    #
    # 默认是 HTTP/1.0，每个请求都新建 TCP 连接。
    # 实测单次签名 30ms，其中大部分是建连开销 —— 而 uskey 是**每个上游请求
    # 都要签一次**的，这个延迟会直接加到网关的响应时间上。
    #
    # 开 1.1 后客户端可以复用连接，延迟能降到个位数毫秒。
    # 前提是每个响应都带准确的 Content-Length（下面的 _json 已保证）。
    protocol_version = "HTTP/1.1"

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("/health", ""):
            assert _signer is not None
            self._json(200, {
                "ok": _signer.error == "",
                "mode": "wine",
                "dll": _signer.dll_path,
                "error": _signer.error,
                "pid": os.getpid(),
            })
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/sign":
            self._json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            req = json.loads(raw.decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            self._json(400, {"ok": False, "error": "请求体解析失败: %s" % exc})
            return

        busiid = str(req.get("busiid") or "7800255")
        bus_info = str(req.get("bus_info") or "")
        if not bus_info:
            self._json(400, {"ok": False, "error": "bus_info 不能为空"})
            return

        assert _signer is not None
        try:
            uskey = _signer.sign(busiid, bus_info)
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"ok": False, "error": str(exc)[:300]})
            return
        self._json(200, {"ok": True, "uskey": uskey})

    def log_message(self, fmt: str, *args) -> None:  # noqa: A002
        # 只记签名请求，不记 health 轮询，避免日志被刷满
        try:
            msg = fmt % args
        except Exception:  # noqa: BLE001
            msg = fmt
        if "/health" in msg:
            return
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), msg))


def find_dll(explicit: str = "") -> str:
    """按顺序找一个可用的 beacon_wrapper.dll。

    顺序：
        1. --dll 显式指定
        2. 脚本同目录（wine 部署时就是把 DLL 和脚本放一起）
        3. 项目内 vendor/beacon（源码仓库里的那份）
        4. 环境变量 MARVIS_BEACON_DLL

    找不到就返回空串，由调用方报错。
    """
    cands: list[str] = []
    if explicit:
        cands.append(explicit)

    here = os.path.dirname(os.path.abspath(__file__))
    cands.append(os.path.join(here, "beacon_wrapper.dll"))
    cands.append(os.path.join(here, "..", "vendor", "beacon",
                              "beacon_wrapper.dll"))

    env = (os.environ.get("MARVIS_BEACON_DLL") or "").strip()
    if env:
        cands.append(env)

    for c in cands:
        p = os.path.abspath(os.path.expanduser(c))
        if os.path.isfile(p):
            return p
    return ""


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8791)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--dll", default="",
                   help="beacon_wrapper.dll 路径；默认自动找")
    args = p.parse_args(argv)

    dll = find_dll(args.dll)
    if not dll:
        print("❌ 找不到 beacon_wrapper.dll")
        print("   试过的位置：")
        print("     · --dll 参数")
        print("     · 脚本同目录")
        print("     · ../vendor/beacon/")
        print("     · 环境变量 MARVIS_BEACON_DLL")
        print()
        print("   wine 部署时，把 beacon_wrapper.dll 和 qimei.dll")
        print("   与本脚本放在同一目录即可。")
        return 1

    global _signer
    _signer = Signer(dll)
    if _signer.error:
        print("❌ 签名器未就绪：%s" % _signer.error)
        return 1

    print("=" * 66)
    print("Marvis uskey 签名服务")
    print("=" * 66)
    print("  DLL    : %s" % _signer.dll_path)
    print("  监听   : http://%s:%d" % (args.host, args.port))
    print("  接口   : GET /health   POST /sign")
    print("  平台   : %s" % sys.platform)
    print()
    print("  在 wine 里运行时，这里应该显示 win32。")
    print()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n收到中断，退出")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# systemd 常驻示例（/etc/systemd/system/marvis-uskey.service）
#
#   [Unit]
#   Description=Marvis uskey sign service (wine)
#   After=network.target
#
#   [Service]
#   Type=simple
#   WorkingDirectory=/opt/marvis-sign
#   Environment=WINEDEBUG=-all
#   ExecStart=/usr/bin/wine /opt/marvis-sign/py/python.exe \
#             uskey_sign_service.py --port 8791
#   Restart=always
#   RestartSec=5
#   # wine 需要 HOME，用系统账号的即可
#   User=www
#
#   [Install]
#   WantedBy=multi-user.target
#
# 启用：
#   sudo systemctl daemon-reload
#   sudo systemctl enable --now marvis-uskey
#   curl -s localhost:8791/health
# ---------------------------------------------------------------------------
