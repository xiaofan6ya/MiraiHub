"""KroWork 凭证：`id_token` 的获取、缓存与续期。

## 上游要什么

对话端点只认一种东西：

    Authorization: Bearer <Keycloak id_token>

`id_token` 是 **Keycloak realm `krow` / client `krow-app`** 签发的 JWT，
`typ=ID`、`aud=krow-app`，寿命 **30 分钟**（实测 `exp - iat = 1800`）。
配套的 `x-user-id` 就是 JWT 里的 `sub`。

## 这个 token 从哪来（拆包结论）

KroWork 客户端**不让外部进程直接读 token 文件**，它走本地 daemon：

    客户端进程 ──open──▶  \\\\.\\pipe\\krowd   （用 %LOCALAPPDATA%\\krow\\.krowd_token 认证）
                        │
                        └──▶ 回 id_token（供 krow_openai 调上游）

证据（对 `krow_openai._client` 做 `sys.addaudithook` 实测，2026-11-19）：

    open      %LOCALAPPDATA%\\krow\\config.json          → 取 region
    open      %LOCALAPPDATA%\\krow\\.krowd_token         → 64 hex，管道握手口令
    open      \\\\.\\pipe\\krowd                          → 要 id_token
    subprocess REG.exe QUERY ...MachineGuid             → 生成 x-device-id

管道里是**自定义二进制帧**（Cython 编译，字符串常量已压缩，静态读不出帧头）。
因此网关不做管道裸读，而是走下面两级来源。

## 凭证来源链（按优先级，任一成功即止）

| 级 | 来源 | 用途 |
|---|---|---|
| 1 | 环境变量 `KROW_ID_TOKEN` | 上线/容器 |
| 2 | `%LOCALAPPDATA%\\krow\\id_token.txt`（或网关数据目录同名文件） | 面板/脚本落盘 |
| 3 | **sidecar**：借 KroWork 自带的 Python 3.12 环境跑 `krow_openai`，用 httpx 传输层钩子取走 Authorization | 本机有客户端时全自动 |
| 4 | 网关库里的 `auth_json`（`refresh_token` + Keycloak 换票） | 号池长期存活 |

第 3 级为什么用子进程：`krow_openai` 是 **cp312 原生扩展**，网关跑 3.10，
不能同进程 import。但 KroWork 自带 3.12 运行时，装一次包就能一直用，
而且**它会自己处理 30 分钟续期**（内部走管道），比网关自己续更可靠。

第 4 级是给「客户端没开」的场景兜底：Keycloak 的 `refresh_token` 寿命远长于
`id_token`，用 `grant_type=refresh_token` 换新票（`client_id=krow-app`）。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import endpoints

logger = logging.getLogger("admin.platform.krowork.token")

#: 提前多少秒判定过期（给上游 RTT 与重试留余量）
REFRESH_MARGIN = 180
#: sidecar 单次调用超时（要等它 import + 一次 list_models）
SIDECAR_TIMEOUT = 60


def data_dir() -> Path:
    """KroWork 数据目录候选（不存在则返回第一个作为「将来位置」）。"""
    local = os.environ.get("LOCALAPPDATA") or ""
    candidates = []
    if local:
        candidates.append(Path(local) / endpoints.DATA_DIR_NAME)
    home = Path.home()
    candidates.append(home / ".krow")
    candidates.append(home / "Krow" / ".krow")
    for path in candidates:
        if path.is_dir():
            return path
    return candidates[0] if candidates else Path(".")


# ---------------------------------------------------------------------------
# JWT（只解不验，用于读 exp / sub）
# ---------------------------------------------------------------------------
def jwt_payload(token: str) -> dict:
    """解 JWT 的 payload，**不校验签名**（网关不需要验，上游会验）。"""
    try:
        _, body, _ = (token or "").split(".")
    except ValueError:
        return {}
    pad = "=" * (-len(body) % 4)
    try:
        raw = base64.urlsafe_b64decode(body + pad)
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def token_exp(token: str) -> float:
    try:
        return float(jwt_payload(token).get("exp") or 0)
    except (TypeError, ValueError):
        return 0.0


def token_sub(token: str) -> str:
    return str(jwt_payload(token).get("sub") or "")


# ---------------------------------------------------------------------------
# 快照
# ---------------------------------------------------------------------------
@dataclass
class TokenSnapshot:
    id_token: str = ""
    source: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0
    detail: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.id_token)

    @property
    def live(self) -> bool:
        """还能用吗（留 REFRESH_MARGIN 余量）。"""
        if not self.id_token:
            return False
        exp = self.expires_at or token_exp(self.id_token)
        if not exp:
            return True  # 解不出 exp 就当能用，上游会告诉我们
        return exp - REFRESH_MARGIN > time.time()

    def public(self) -> dict:
        """给控制台看的摘要：**绝不带 token 原文**。"""
        exp = self.expires_at or token_exp(self.id_token)
        return {
            "source": self.source,
            "ok": self.ok,
            "live": self.live,
            "expires_at": int(exp) if exp else 0,
            "expires_in": max(0, int(exp - time.time())) if exp else 0,
            "sub": token_sub(self.id_token)[:8] + "…" if self.id_token else "",
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# 来源 1/2：显式给的值
# ---------------------------------------------------------------------------
def from_env() -> TokenSnapshot:
    for key in ("KROW_ID_TOKEN", "KROW_ACCESS_TOKEN"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return TokenSnapshot(id_token=value, source="env:%s" % key,
                                 expires_at=token_exp(value))
    return TokenSnapshot()


def from_file() -> TokenSnapshot:
    for folder in (data_dir(), Path.cwd()):
        path = folder / "id_token.txt"
        try:
            if path.is_file():
                value = path.read_text(encoding="utf-8").strip()
                if value:
                    return TokenSnapshot(id_token=value, source="file:%s" % path,
                                         expires_at=token_exp(value))
        except OSError:
            continue
    return TokenSnapshot()


# ---------------------------------------------------------------------------
# 来源 3：sidecar（借 KroWork 自带的 3.12 运行时）
# ---------------------------------------------------------------------------
#: 在 3.12 环境里跑的内联脚本：hook httpx 传输层，把 Bearer 捞出来。
#:
#: 为什么 hook `Client.send` 而不是读 `krow_openai` 的内部变量：
#: token 是 Cython 对象的私有字段，取不到；而**每条出站请求都带它**，
#: 从请求头拿是唯一稳定且不依赖具体版本的入口。
SIDECAR_SCRIPT = r'''
import json, sys
def main():
    out = {"ok": False, "token": "", "source": "sidecar"}
    try:
        import httpx
        cap = {}
        _orig = httpx.Client.send
        def send(self, request, **kw):
            try:
                auth = request.headers.get("authorization") or ""
                if auth.lower().startswith("bearer ") and not cap.get("token"):
                    cap["token"] = auth[7:]
                    cap["url"] = str(request.url)
            except Exception:
                pass
            return _orig(self, request, **kw)
        httpx.Client.send = send
        import krow_openai
        listing = krow_openai.list_models()
        out["token"] = cap.get("token", "")
        out["region"] = getattr(listing, "region", "")
        out["models"] = len(getattr(listing, "models", []) or [])
        out["ok"] = bool(out["token"])
        if not out["ok"]:
            out["error"] = "list_models() 没发出带 Authorization 的请求"
    except Exception as exc:
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
    sys.stdout.write(json.dumps(out))
main()
'''


def _version_ok(exe: str) -> bool:
    try:
        res = subprocess.run([exe, "-c", "import sys;print(sys.version_info[:2])"],
                             capture_output=True, text=True, timeout=15)
    except Exception:  # noqa: BLE001
        return False
    return "(3, 12)" in (res.stdout or "") or "(3, 13)" in (res.stdout or "")


def _python_candidates() -> list[str]:
    """候选 3.12+ 解释器（KroWork 自己的优先）。"""
    out: list[str] = []
    env = (os.environ.get("KROW_SIDECAR_PYTHON") or "").strip()
    if env:
        out.append(env)
    # KroWork suite 的 workspace venv（实测路径形态）
    home = Path.home()
    for root in (home / "Krow" / ".krow" / "suites", data_dir() / "suites"):
        try:
            for exe in root.glob("*/*/workspace/.venv/Scripts/python.exe"):
                out.append(str(exe))
        except OSError:
            pass
    for name in ("python3.12", "python3.13", "python.exe", "python"):
        found = shutil.which(name)
        if found:
            out.append(found)
    # data_dir 下可能有 uv 装的独立解释器
    try:
        for exe in data_dir().glob("**/python.exe"):
            out.append(str(exe))
    except OSError:
        pass
    seen: set[str] = set()
    uniq: list[str] = []
    for exe in out:
        key = exe.lower()
        if key in seen or not Path(exe).exists():
            continue
        seen.add(key)
        uniq.append(exe)
    return uniq


def sidecar_python() -> str:
    """挑一个能 import krow_openai 的 3.12 解释器（找不到返回空串）。"""
    probe = "import krow_openai,sys;print('OK',sys.version_info[:2])"
    for exe in _python_candidates():
        if not _version_ok(exe):
            continue
        try:
            res = subprocess.run([exe, "-c", probe], capture_output=True,
                                 text=True, timeout=25)
        except Exception:  # noqa: BLE001
            continue
        if "OK" in (res.stdout or ""):
            return exe
    return ""


def from_sidecar(python: str = "") -> TokenSnapshot:
    exe = python or sidecar_python()
    if not exe:
        return TokenSnapshot(source="sidecar",
                             detail="没找到能 import krow_openai 的 Python 3.12 环境")
    try:
        res = subprocess.run([exe, "-c", SIDECAR_SCRIPT], capture_output=True,
                             text=True, timeout=SIDECAR_TIMEOUT)
    except subprocess.TimeoutExpired:
        return TokenSnapshot(source="sidecar", detail="sidecar 超时（KroWork 可能没在跑）")
    except Exception as exc:  # noqa: BLE001
        return TokenSnapshot(source="sidecar", detail="%s: %s" % (type(exc).__name__, exc))
    raw = (res.stdout or "").strip()
    # logfire 之类会往 stdout 掺日志，取最后一个合法 JSON
    payload: dict = {}
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
                break
            except ValueError:
                continue
    if not payload:
        return TokenSnapshot(source="sidecar",
                             detail="sidecar 无输出：%s" % (res.stderr or "")[:200])
    token = str(payload.get("token") or "")
    return TokenSnapshot(
        id_token=token,
        source="sidecar",
        expires_at=token_exp(token),
        detail=str(payload.get("error") or ""),
        extra={"python": exe, "models": payload.get("models")},
    )


# ---------------------------------------------------------------------------
# 来源 4：Keycloak refresh_token 换票
# ---------------------------------------------------------------------------
def refresh_with(refresh_token: str, realm: str = "cn") -> TokenSnapshot:
    """用 refresh_token 换新 id_token（客户端没开时的唯一续命方式）。

    ## 两种 refresh_token，行为完全不同（2026-11-20 实测）

    | 类型 | 怎么来的 | 拿它换票的结果 |
    |---|---|---|
    | **会话型**（`typ=Refresh`，scope 里**没有** `offline_access`） | 授权时 scope 只写了 `openid` | Keycloak 回 `invalid_grant: Session doesn't have required client` |
    | **离线型**（`typ=Offline`，scope 含 `offline_access`） | 授权时 scope 带 `offline_access` | 正常换新票，**并且轮换**（旧 refresh 作废） |

    所以：
      * 授权入口必须带 `offline_access`（`webauth.SCOPE` 已改）；
      * 这里遇到「会话型」要给出**可操作**的提示，而不是一句 invalid_grant；
      * 成功换票后**必须**把新 refresh_token 写回库 —— 轮换后旧的就作废了。
    """
    import httpx

    if not refresh_token:
        return TokenSnapshot(source="refresh", detail="没有 refresh_token")
    payload = {
        "grant_type": "refresh_token",
        "client_id": "krow-app",
        "refresh_token": refresh_token,
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(20.0)) as client:
            resp = client.post(endpoints.auth_of(realm) + "/token", data=payload)
    except Exception as exc:  # noqa: BLE001
        return TokenSnapshot(source="refresh", detail="%s: %s" % (type(exc).__name__, exc))
    if resp.status_code >= 400:
        text = (resp.text or "")[:200]
        detail = "Keycloak %s: %s" % (resp.status_code, text)
        if "Session doesn't have required client" in text:
            detail = ("这个 refresh_token 是**会话型**（授权时没带 offline_access），"
                      "30 分钟后已随 SSO 会话作废。请在控制台重新走一次「授权登录」"
                      "（新流程已带 offline_access，之后才能自动续签 7 天）。")
        elif "Invalid refresh token" in text:
            detail = ("refresh_token 已被轮换或失效（同一枚只能用一次）。"
                      "请重新走「授权登录」。")
        return TokenSnapshot(source="refresh", detail=detail)
    body = resp.json() if resp.content else {}
    token = str(body.get("id_token") or body.get("access_token") or "")
    snap = TokenSnapshot(id_token=token, source="refresh", expires_at=token_exp(token),
                         refresh_token=str(body.get("refresh_token") or refresh_token),
                         extra={"expires_in": body.get("expires_in"),
                                "refresh_expires_in": body.get("refresh_expires_in")})
    if not token:
        snap.detail = "Keycloak 返回里没有 id_token"
    return snap


# ---------------------------------------------------------------------------
# 来源 5：账号池（**授权登录入库后的主路径**）
# ---------------------------------------------------------------------------
def from_accounts() -> TokenSnapshot:
    """从 `accounts` 表里取一个还活着的 KroWork 凭证（并顺手续期）。

    ## 为什么必须放在来源链里（2026-11-20 修）

    授权登录（手机号 / 回调）成功后，票是写进 **数据库** 的。
    但原来的来源链只有 env → 文件 → sidecar，**根本没看数据库** ——
    于是出现「账号明明入库了、状态正常，模型列表和额度却都取不到」：
    网关拿着空的进程级 token 去打上游，全被 401 挡回来。

    sidecar 只在客户端**正在运行且已登录**时可用，而 Kimi 这类场景恰恰是
    「客户端登出/没开，靠授权登录进的号」—— 那就是死路。
    """
    try:
        from admin.db import SessionLocal
        from admin.models import Account
    except Exception:  # noqa: BLE001 - 纯协议测试时没有 admin 包
        return TokenSnapshot(source="accounts", detail="没有数据库上下文")

    try:
        with SessionLocal() as db:
            rows = (db.query(Account)
                      .filter(Account.platform == "krowork",
                              Account.status == "active")
                      .order_by(Account.id.asc())
                      .all())
            if not rows:
                return TokenSnapshot(source="accounts", detail="号池为空")
            reasons: list[str] = []
            best = TokenSnapshot(source="accounts")
            for row in rows:
                data = {}
                try:
                    import json as _json

                    data = _json.loads(row.auth_json or "{}")
                except (ValueError, TypeError):
                    data = {}
                tok = str(data.get("id_token") or "")
                refresh = str(data.get("refresh_token") or "")
                exp = int(data.get("expires_at") or 0)
                region = str(data.get("region") or "cn")

                # 还活着 → 直接用
                if tok and (exp == 0 or exp - REFRESH_MARGIN > time.time()):
                    return TokenSnapshot(id_token=tok, source="accounts:%s" % row.id,
                                         refresh_token=refresh, expires_at=exp,
                                         extra={"account_id": row.id,
                                                "region": region})
                # 过期但有 refresh → 换新票并写回
                if refresh:
                    snap = refresh_with(refresh, region)
                    if snap.ok:
                        data.update({"id_token": snap.id_token,
                                     "expires_at": int(snap.expires_at or 0),
                                     "source": "refresh"})
                        if snap.refresh_token:
                            data["refresh_token"] = snap.refresh_token
                        try:
                            import json as _json

                            row.auth_json = _json.dumps(data, ensure_ascii=False)
                            db.commit()
                        except Exception:  # noqa: BLE001
                            db.rollback()
                        snap.source = "accounts:%s:refresh" % row.id
                        snap.refresh_token = str(data.get("refresh_token") or "")
                        snap.extra = {"account_id": row.id, "region": region}
                        return snap
                    reasons.append("acc %s refresh 失败：%s"
                                   % (row.id, str(snap.detail)[:80]))
                if tok and not best.ok:
                    best = TokenSnapshot(id_token=tok, source="accounts:%s:stale" % row.id,
                                         refresh_token=refresh, expires_at=exp,
                                         detail="凭证已过期",
                                         extra={"account_id": row.id,
                                                "region": region})
                elif not tok:
                    reasons.append("acc %s 没有 id_token" % row.id)
            if best.ok:
                return best
            return TokenSnapshot(source="accounts",
                                 detail="；".join(reasons[:3]) or "号池里没有可用凭证")
    except Exception as exc:  # noqa: BLE001
        return TokenSnapshot(source="accounts",
                             detail="读号池失败：%s: %s" % (type(exc).__name__, exc))


# ---------------------------------------------------------------------------
# 门面
# ---------------------------------------------------------------------------
class TokenStore:
    """进程内的 token 缓存 + 自动续期。线程安全（多个 /v1 请求同时来）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snap = TokenSnapshot()
        self._sidecar_python = ""
        self._last_error = ""

    @property
    def last_error(self) -> str:
        """最近一次取票失败的原始原因（给控制台排障用）。"""
        with self._lock:
            return self._last_error

    # -- 读 ---------------------------------------------------------------
    def get(self, *, force: bool = False) -> TokenSnapshot:
        with self._lock:
            if not force and self._snap.live:
                return self._snap
            self._snap = self._resolve()
            if not self._snap.ok:
                self._last_error = self._snap.detail or ""
            else:
                self._last_error = ""
            return self._snap

    def snapshot(self) -> TokenSnapshot:
        with self._lock:
            return self._snap

    def invalidate(self) -> None:
        with self._lock:
            self._snap = TokenSnapshot()

    def note_unauthorized(self) -> None:
        """上游回 401 时调用：丢掉缓存，下次重取。"""
        self.invalidate()

    # -- 内部 -------------------------------------------------------------
    def _resolve(self) -> TokenSnapshot:
        """按来源链找第一份**还活着**的 token。

        ## 顺序为什么是这样（2026-11-20 调整）

        1. `env` / `file`：显式给定的，优先（容器与手工调试）
        2. **`accounts`（号池）**：授权登录入库的票就在这里 —— 它不依赖
           客户端是否运行，是「账号已入库但取不到模型/额度」那个 bug 的修复点
        3. `sidecar` / `refresh`：客户端正在跑时票最新鲜，放最后兜底

        已过期但能拿到的（比如手工粘贴的旧票）不丢，留作最后手段 ——
        上游会给出明确的 401，比网关自己判「没有凭证」更利于排障。
        """
        fallback = TokenSnapshot()
        reasons: list[str] = []
        for step in (from_env, from_file, from_accounts, self._sidecar, from_sidecar):
            try:
                snap = step()
            except Exception as exc:  # noqa: BLE001 - 任何一级挂了都不能拖垮整条链
                reasons.append("%s 异常：%s" % (getattr(step, "__name__", "?"),
                                              exc))
                continue
            if snap.ok and snap.live:
                return snap
            if snap.ok and not fallback.ok:
                fallback = snap
            elif not snap.ok and snap.detail:
                reasons.append("%s: %s" % (snap.source or step.__name__,
                                           snap.detail))
        if fallback.ok:
            fallback.detail = fallback.detail or "凭证已过期"
            return fallback
        detail = "；".join(reasons[:4]) or (
            "没有可用凭证：请在控制台用「授权登录」入库，或启动 KroWork 并登录")
        return TokenSnapshot(detail=detail)

    def _sidecar(self) -> TokenSnapshot:
        if not self._sidecar_python:
            self._sidecar_python = sidecar_python()
        if not self._sidecar_python:
            return TokenSnapshot(source="sidecar", detail="无可用 3.12 解释器")
        return from_sidecar(self._sidecar_python)


#: 进程级单例。网关上所有请求共用一份（上游按 token 归因，不按连接）。
STORE = TokenStore()


def current(*, force: bool = False) -> TokenSnapshot:
    return STORE.get(force=force)


def id_token(*, force: bool = False) -> str:
    return current(force=force).id_token


def status() -> dict:
    snap = current()
    data = snap.public()
    data["sidecar_python"] = STORE._sidecar_python or ""
    data["data_dir"] = str(data_dir())
    return data
