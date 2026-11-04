"""把号池里的账号「注入」成本机客户端的当前登录态。

## 为什么需要这个模块（2026-11-04 实测）

前台点「注入本机客户端」原本是三步：备份 → 覆盖 `workbuddy-desktop.info`
→ 回 200。接口一直是 200，日志里也一直是成功，但**用户看到的是没生效**：

    11:26:02  POST /api/accounts/157/inject  200 OK     ← 文件已写入
    11:28:40  客户端把 workbuddy-desktop.info 重写了一遍   ← 注入被覆盖

原因是客户端**把登录态放在自己的内存里**，只把 `.info` 当成落盘副本：
它刷一次令牌（或渲染进程重载）就整份重写，注入的东西当场消失。

同一个目录里还有一份 `.observation.json`（`<info 名>.<16 位十六进制>.
observation.json`），内容是客户端对这个登录态的旁证记录：

    {"version":1,"identity":{"userId":"<uid>","accountType":"personal"},
     "sessionId":"...","active":true,"updatedAt":1793762793323,"facts":[]}

**只改 `.info` 不改它**，等于让客户端手里拿着两套互相矛盾的"我是谁"。
所以这里两个文件一起写，写完再读回来验一遍。

## 那 16 位后缀是什么

试过 md5/sha1/sha256 over 路径 / 文件名 / uid / sessionId 的各种拼接，
都不匹配 —— 它是客户端自己生成的实例标识。**不猜**：直接复用现场已有的
那个文件名，只改里面的内容。没有就不创建（乱造一个反而可能让客户端
认不出这个登录态）。

## 客户端在跑怎么办

文件本身能写，但内存里的会话仍然压着它，写完撑不过一次令牌刷新。
这种情况下**必须重启客户端**才生效。这里不擅自 kill —— 用户可能正在用
（本工具的桌面端就跑在它里面）。检测到在跑就如实回报 `needs_restart`，
前端据此提示；用户显式要求（`restart=True`）才做「优雅关闭 → 写入 →
重新拉起」。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

_logger = logging.getLogger(__name__)

#: 客户端读的那个「当前登录态」文件名（**不是** `-ai` / 带时间戳的历史副本）
ACTIVE_INFO_NAME = "workbuddy-desktop.info"

#: 客户端可执行文件名（用来判断「是否在运行」）
CLIENT_EXE = "WorkBuddy.exe"


def auth_dir() -> Path:
    """客户端登录态目录（与 `settings.CLIENT_AUTH_DIR` 同一口径）。"""
    from admin.config import settings

    raw = settings.CLIENT_AUTH_DIR or os.path.expandvars(
        r"%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth")
    return Path(os.path.expandvars(raw))


def active_info() -> Path:
    return auth_dir() / ACTIVE_INFO_NAME


def observation_files(info: Path) -> list[Path]:
    """该 `.info` 对应的 observation 旁证文件（可能 0 个或多个）。"""
    parent, name = info.parent, info.name
    try:
        return sorted(p for p in parent.iterdir()
                      if p.name.startswith(name + ".")
                      and p.name.endswith(".observation.json"))
    except OSError:
        return []


def client_running() -> list[dict]:
    """正在运行的客户端进程（空列表 = 没在跑）。**绝不抛异常**。

    用 `tasklist` 而不是 psutil：这台机器上服务是免安装 Python 跑的，
    多一个三方依赖就多一处装不上的可能。
    """
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + CLIENT_EXE,
                              "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=8,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        rows = []
        for line in (out.stdout or "").splitlines():
            cells = [c.strip('" ') for c in line.split('","')]
            if len(cells) >= 2 and CLIENT_EXE.lower() in cells[0].lower():
                rows.append({"pid": int(cells[1]) if cells[1].isdigit() else 0})
        return rows
    except Exception:  # noqa: BLE001 - 探测失败当作「没在跑」
        _logger.debug("探测客户端进程失败", exc_info=True)
        return []


def _stop_client(timeout: float = 20.0) -> bool:
    """优雅关闭客户端（先发关闭请求，超时才强杀）。返回是否已全部退出。"""
    if not client_running():
        return True
    for force in (False, True):
        try:
            args = ["taskkill", "/IM", CLIENT_EXE]
            if force:
                args.insert(1, "/F")
            subprocess.run(args, capture_output=True, timeout=15,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:  # noqa: BLE001
            _logger.warning("关闭客户端失败 force=%s", force, exc_info=True)
        deadline = time.time() + timeout / 2
        while time.time() < deadline:
            if not client_running():
                return True
            time.sleep(0.5)
    return not client_running()


def _start_client() -> bool:
    """重新拉起客户端（脱离父进程，服务退出也不影响它）。"""
    try:
        from wb_install import WB

        install = WB.install_dir()
        exe = Path(install) / CLIENT_EXE if install else None
        if exe is None or not exe.is_file():
            return False
        subprocess.Popen(
            [str(exe)],
            cwd=str(exe.parent),
            close_fds=True,
            creationflags=(getattr(subprocess, "DETACHED_PROCESS", 0)
                           | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)))
        return True
    except Exception:  # noqa: BLE001
        _logger.warning("重新拉起客户端失败", exc_info=True)
        return False


def _write_atomic(path: Path, text: str) -> None:
    """先写临时文件再替换。

    客户端随时可能来读这两个文件，**不能让它读到半份 JSON** ——
    半份 JSON 会被当成「凭证损坏」，客户端直接登出，比不注入更糟。
    """
    tmp = path.with_name(path.name + ".tmp-inject")
    with open(tmp, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _sync_observation(info: Path, uid: str, account_type: str) -> list[str]:
    """把 observation 旁证里的 identity 改成新账号。返回改过的文件名。

    内容缺字段就整份重建 —— 这几项是客户端的记账，宁可给全。
    """
    touched = []
    now_ms = int(time.time() * 1000)
    for path in observation_files(info):
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except Exception:  # noqa: BLE001
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("version", 1)
        identity = data.get("identity")
        if not isinstance(identity, dict):
            identity = {}
        identity["userId"] = uid
        identity["accountType"] = account_type or identity.get("accountType") or "personal"
        data["identity"] = identity
        data["sessionId"] = uuid.uuid4().hex[:24]
        data["active"] = True
        data["updatedAt"] = now_ms
        data.setdefault("facts", [])
        _write_atomic(path, json.dumps(data, ensure_ascii=False))
        touched.append(path.name)
    return touched


def _read_identity(info: Path) -> dict:
    """读回 `.info` 里真正的身份（解密后）。失败返回 {}。"""
    try:
        from admin.platform.workbuddy.core.credential_codec import normalize_credential

        payload = json.loads(normalize_credential(info.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001 - 校验是可选项，坏了不影响结论
        return {}
    if not isinstance(payload, dict):
        return {}
    account = payload.get("account") or {}
    return {"uid": str(account.get("uid") or ""),
            "account_type": str(account.get("accountType") or "")}


def inject(*, payload: str, uid: str, name: str = "",
           account_type: str = "", restart: bool = False) -> dict:
    """把 `payload`（客户端格式的 `.info` 全文）落成当前登录态。

    返回结构刻意把「写了」和「生效了」分开：

        ok            文件确实写进去了
        verified      写完之后读回来是**目标账号**
        overwritten   客户端在 1.2 秒内把它改回去了（= 内存里还有旧会话）
        needs_restart 客户端在运行 → 不重启客户端，注入撑不过一次令牌刷新
    """
    info = active_info()
    if not info.parent.is_dir():
        raise FileNotFoundError("客户端登录目录不存在: %s" % info.parent)

    running_before = client_running()
    stopped = False
    if running_before and restart:
        stopped = _stop_client()

    backup = ""
    if info.exists():
        backup = "%s.bak-%s" % (info, datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(info, backup)

    _write_atomic(info, payload)
    observations = _sync_observation(info, uid, account_type)

    # 写完等一拍再验：客户端重写磁盘是异步的，立刻读一定是「我写的」。
    time.sleep(1.2)
    seen = _read_identity(info)
    overwritten = bool(seen.get("uid")) and seen.get("uid") != uid
    verified = bool(seen.get("uid")) and seen.get("uid") == uid

    restarted = False
    running_after = client_running()
    if restart and stopped:
        restarted = _start_client()

    return {
        "ok": True,
        "target": str(info),
        "backup": backup,
        "observations": observations,
        "uid": uid,
        "name": name,
        "verified": verified,
        "overwritten": overwritten,
        "seen_uid": seen.get("uid") or "",
        "client_was_running": bool(running_before),
        "client_running": bool(running_after),
        "stopped": stopped,
        "restarted": restarted,
        # 「需要重启才生效」= 客户端正跑着、我们又没被允许重启它
        "needs_restart": bool(running_before) and not stopped,
    }
