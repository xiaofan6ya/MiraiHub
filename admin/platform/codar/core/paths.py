"""科搭 Codar 本机登录态探测（「扫描本机」的数据源）。

## 凭证在哪

科搭客户端把登录令牌写在自己的配置文件里：

    macOS / Linux   ~/.codar/config.json
    Windows         %USERPROFILE%\\.codar\\config.json
    $CODAR_HOME     $CODAR_HOME/config.json（客户端认这个变量，这里也认）

令牌在 `provider.speedai.token`。

## 两个必踩的坑（都写在这里，不再散落各处）

1. **必须按 `utf-8-sig` 读**。客户端在 Windows 上写过的配置带 BOM，用普通
   UTF-8 读会让 `json.loads` 拒掉**整份文件**，表现是「明明登录了却扫描
   不到」。
2. **只读，绝不回写**。这份配置是客户端自己的，写它等于在客户端背后改它的
   状态；网关的续期只更新自己的账号库。

## 令牌是 JWT

载荷里有账号名（`username`）与到期时间（`exp`，约 7 天）。导入前先校验：
**有账号名、未过期**。过期令牌拿去除打接口只会得到 401，然后把「登录过期」
伪装成「账号坏了」—— 所以这里直接拒绝并给出可执行的下一步：
打开客户端刷新登录。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import time

#: 客户端配置文件相对数据根的名字
CONFIG_FILE = "config.json"
#: 配置上限：1 MiB 约是真实配置的二十倍，超出说明这不是科搭的配置
MAX_CONFIG_BYTES = 1024 * 1024

#: 令牌里允许出现的字符：可见 ASCII、无空白（HTTP 头放不下别的）
_TOKEN_SHAPE = re.compile(r"^[\x21-\x7e]+$")
#: 账号名：任意文字、数字、点、下划线、短横线、空格，最长 64
_USERNAME_SHAPE = re.compile(r"^[\w.\- ]{1,64}$", re.UNICODE)
#: 看起来像 JWT 的串不是账号名
_JWT_IN_NAME = re.compile(r"eyJ[A-Za-z0-9_-]{8,}")
_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")


def config_home() -> str:
    """数据根：`CODAR_HOME` **原样**（不 strip）—— 客户端也原样认它，
    我们 trim 了就会去读一个客户端从未写过的路径。空值视为未设置。"""
    raw = os.environ.get("CODAR_HOME")
    if isinstance(raw, str) and raw:
        return raw
    return os.path.join(os.path.expanduser("~"), ".codar")


def config_path() -> str:
    return os.path.join(config_home(), CONFIG_FILE)


def shown_path(path: str) -> str:
    """给消息用的路径：家目录部分写成 `~`（截图/日志里不带用户名）。"""
    home = os.path.expanduser("~")
    if not home:
        return path
    try:
        if os.path.commonpath([os.path.abspath(path), os.path.abspath(home)]) \
                == os.path.abspath(home):
            relative = os.path.relpath(path, home)
            return "~" + os.sep + relative
    except (ValueError, OSError):
        pass
    return path


class ScanError(Exception):
    """本机登录态读不出来（带**可执行**的下一步文案）。"""


def read_config_text(path: str) -> str:
    """按上限读配置文本；不是普通文件 / 太大 / 权限不足都抛 ScanError。"""
    shown = shown_path(path)
    try:
        info = os.stat(path)
    except FileNotFoundError:
        raise ScanError("没有找到 %s：先在这台机器上登录一次科搭客户端，"
                        "或直接把令牌粘贴进来导入" % shown) from None
    except OSError as exc:
        raise ScanError("读不到 %s（%s）：检查文件权限后重试，"
                        "或直接把令牌粘贴进来导入"
                        % (shown, exc.strerror or exc.__class__.__name__)) from None
    if not os.path.isfile(path):
        raise ScanError("%s 不是普通文件，不是科搭的配置" % shown)
    if info.st_size > MAX_CONFIG_BYTES:
        raise ScanError("%s 超过 1 MB，不像是科搭的配置" % shown)
    try:
        with open(path, encoding="utf-8-sig") as handle:
            return handle.read()
    except OSError as exc:
        raise ScanError("读不到 %s（%s）：检查文件权限后重试"
                        % (shown, exc.strerror or exc.__class__.__name__)) from None


def load_config(path: str = "") -> dict:
    """读客户端配置 → dict（JSON 坏了抛 ScanError）。"""
    target = path or config_path()
    shown = shown_path(target)
    text = read_config_text(target)
    try:
        payload = json.loads(text.lstrip("\ufeff"))
    except ValueError:
        raise ScanError("%s 不是合法 JSON，可能已损坏：打开科搭客户端重新登录一次"
                        % shown) from None
    return payload if isinstance(payload, dict) else {}


def token_in(payload: dict) -> str:
    """从配置里取 `provider.speedai.token`（没有就是空串）。"""
    provider = payload.get("provider") if isinstance(payload, dict) else None
    speedai = provider.get("speedai") if isinstance(provider, dict) else None
    if not isinstance(speedai, dict):
        return ""
    return str(speedai.get("token") or "").strip()


def payload_of(token: str):
    """JWT 载荷（不验签：验签是上游的事，这里只读账号名与到期）。"""
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return None
    body = parts[1].replace("-", "+").replace("_", "/")
    body += "=" * (-len(body) % 4)
    try:
        claims = json.loads(base64.b64decode(body, validate=False).decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    return claims if isinstance(claims, dict) else None


def _clean_username(value) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 64:
        return ""
    if not _USERNAME_SHAPE.match(name):
        return ""
    if _JWT_IN_NAME.search(name):
        return ""
    parts = name.split(".")
    if len(parts) >= 3 and all(_SEGMENT.match(part) for part in parts) \
            and len(parts[-1]) >= 16:
        return ""
    return name


def username_of(token: str) -> str:
    """令牌里的账号名（读不到给空串）。"""
    claims = payload_of(token) or {}
    for key in ("username", "user_name", "name", "account"):
        name = _clean_username(claims.get(key))
        if name:
            return name
    return ""


def expires_at(token: str) -> int:
    """令牌声明的到期时刻（epoch 秒）；没说给 0（**不猜**）。"""
    claims = payload_of(token) or {}
    raw = claims.get("exp")
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, (int, float)):
        return int(raw) if raw > 0 else 0
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return 0


def expired(token: str, *, now: float | None = None) -> bool:
    """令牌是否已过期。**没说 exp 不算过期**（过期与否由上游回答）。"""
    when = expires_at(token)
    if not when:
        return False
    return (now if now is not None else time.time()) >= when


def token_shape_ok(token: str) -> bool:
    return bool(token) and bool(_TOKEN_SHAPE.match(token))


def verify_token(token: str) -> dict:
    """导入前的校验：返回 {token, username, expires_at}，不合格抛 ScanError。

    校验的三件事都有明确理由：
      * 形状 —— 含空白/换行的令牌放不进 HTTP 头，存下来只会在每个请求上失败；
      * 账号名 —— 账号侧的接口（点数/签到）要拿它当请求参数，缺了就用不了；
      * 未过期 —— 过期令牌打接口只会 401，用户会把「该刷新」当成「号坏了」。
    """
    said = str(token or "").strip()
    if not said:
        raise ScanError("没有令牌：先在这台机器上登录一次科搭客户端，"
                        "或把令牌粘贴进来")
    if not token_shape_ok(said):
        raise ScanError("令牌里有空白、换行或非 ASCII 字符，不是可用的令牌")
    name = username_of(said)
    if not name:
        raise ScanError("令牌里没有账号名，无法用于点数与签到："
                        "打开科搭客户端重新登录后再导入")
    when = expires_at(said)
    if when and expired(said):
        raise ScanError("令牌已过期（%s）：打开科搭客户端刷新登录后再导入"
                        % time.strftime("%Y-%m-%d %H:%M", time.localtime(when)))
    return {"token": said, "username": name, "expires_at": when}


def app_token(path: str = "") -> dict:
    """读本机登录态 → {token, username, expires_at, file} 或 {error}。

    只读：客户端配置始终是客户端自己的。
    """
    target = path or config_path()
    try:
        payload = load_config(target)
    except ScanError as exc:
        return {"error": str(exc), "file": target}
    token = token_in(payload)
    if not token:
        return {"error": "%s 里没有 provider.speedai.token：先在这台机器上登录"
                         "一次科搭客户端" % shown_path(target),
                "file": target}
    try:
        verified = verify_token(token)
    except ScanError as exc:
        return {"error": str(exc), "file": target}
    verified["file"] = target
    return verified


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def discover() -> list[dict]:
    """扫描本机登录态。

    返回**不含令牌**的清单（可直接回给前端）：账号名、来源文件、到期时间、
    是否可用。`uid` / `uid_full` 都是账号名 —— 导入按 `uid_full` 匹配。
    """
    path = config_path()
    if not os.path.isfile(path):
        return []
    found = app_token(path)
    if found.get("error"):
        return [{
            "uid": "codar-config",
            "uid_full": "codar-config",
            "name": "科搭客户端配置",
            "realm": "cn",
            "path": path,
            "file": path,
            "has_plain_credential": False,
            "valid": False,
            "error": str(found["error"]),
        }]
    name = found["username"]
    expires = int(found.get("expires_at") or 0)
    return [{
        "uid": name,
        "uid_full": name,
        "name": name,
        "realm": "cn",
        "expires_at_ms": expires * 1000 if expires else 0,
        "expires_in_days": round((expires - time.time()) / 86400, 1)
        if expires else None,
        "token_tail": ("…" + _fingerprint(found["token"])[-4:])
        if found.get("token") else "",
        "has_plain_credential": True,
        "valid": True,
        "path": path,
        "file": path,
        "source": "local-scan",
    }]
