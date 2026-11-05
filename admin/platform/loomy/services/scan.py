"""Loomy 本机扫描：读明文 auth-session.json。

## 凭证位置（读源码算出来的，猜不到）

```javascript
// config-path.js
export function getConfigRoot() {
  return path.join('C:', 'Users', 'Public', 'Loomy', _getUserHash())
}
function _getUserHash() {
  return crypto.createHash('sha256').update(os.userInfo().username)
               .digest('hex').slice(0, 12)
}
export function getUserDataPath() {
  return path.join(getConfigRoot(), 'userData')
}
```

    %PUBLIC%/Loomy/<sha256(username)[:12]>/userData/auth-session.json

`%PUBLIC%` 由系统环境变量给出（系统盘不一定是 C）；目录名 `Loomy` 是客户端
写死的。实测本机为 `C:/Users/Public/Loomy/e7d3e769f3f5/userData/auth-session.json`
（137 B），但**代码里不能写死这个绝对路径**。

## 为什么不能靠猜

`%APPDATA%/loomy` 里也有东西（cookies、cache、leveldb），
但**没有凭证文件**。只猜那个目录会得出「找不到凭证」的错误结论。

## 凭证形态

明文 JSON：
```json
{"session": "...", "userid": "...", "phone": "...", "updatedAt": 1793082630934}
```

**没有 refresh 机制** —— 失效只能重新登录（`/session/refresh` 是 404）。
所以扫描策略是：读得到就用，失效就提示用户重新登录后再扫。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib

logger = logging.getLogger("admin.platform.loomy.scan")
PLATFORM = "loomy"

#: 凭证文件名（与 `core/endpoints.py` 的 CRED_FILE 一致）
CRED_FILE = "auth-session.json"

#: 也扫这些（兜底，通常是空的）
FALLBACK_DIRS = (
    "%APPDATA%/loomy",
    "%LOCALAPPDATA%/Loomy",
)


def mask(value: str, keep: int = 4) -> str:
    text = str(value or "")
    if len(text) <= keep * 2:
        return text[:keep] + "***" if text else ""
    return text[:keep] + "*" * min(8, len(text) - keep * 2) + text[-keep:]


def user_hash() -> str:
    """`sha256(username)[:12]` —— 与客户端算法一致。

    同样不能用 `os.getlogin()`：Linux 服务没有控制终端会抛 errno 6。
    原实现捕获异常后回落到空串，导致 user_hash 恒等于 sha256("")，
    扫描定位到的配置路径不对；改用与 Qoder 同一套无终端依赖的取法。
    """
    from admin.platform import host_identity

    username = host_identity.current_username()
    return hashlib.sha256(username.encode()).hexdigest()[:12]


def config_root_parent() -> pathlib.Path:
    """配置根的父目录（`%PUBLIC%/Loomy`），随系统环境变量走。"""
    from admin.platform.loomy.core import paths

    return paths.config_root_parent()


def config_root() -> pathlib.Path:
    """配置根（含 `userData/` 的那一层）。

    顺序：
    1. `LOOMY_CONFIG_ROOT` 覆盖（环境变量 / 后台设置）；
    2. `%PUBLIC%/Loomy/<sha256(用户名)[:12]>`（与客户端同算法）；
    3. 父目录下按 `userData/auth-session.json` 反查 —— 域用户、用户名变化、
       多账号、客户端改 hash 算法时也能找到，不再依赖用户名算得一模一样。
    """
    from admin.platform import install_paths as ip

    value, _source = ip.override(PLATFORM, "config_root")
    if value:
        return ip.expand(value)
    parent = config_root_parent()
    hashed = parent / user_hash()
    if (hashed / "userData" / CRED_FILE).is_file():
        return hashed
    try:
        for sub in sorted(parent.iterdir()):
            if sub.is_dir() and (sub / "userData" / CRED_FILE).is_file():
                return sub
    except OSError:
        pass
    return hashed


def user_data_dir() -> pathlib.Path:
    return config_root() / "userData"


def cred_path() -> pathlib.Path:
    return user_data_dir() / CRED_FILE


def opencode_config_path() -> pathlib.Path:
    return config_root() / "opencode" / "opencode.json"


def read_credentials() -> dict | None:
    """读明文凭证。**加密或缺失返回 None**（不做无谓的解密尝试）。"""
    path = cred_path()
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()[:4096]
    except OSError:
        return None
    if raw[:3] == b"v10" or raw[:1] not in (b"{", b"["):
        logger.info("auth-session.json 存在但不是明文 JSON")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if not (data.get("session") or data.get("token")):
        return None
    return data


def read_runtime_base_url() -> str:
    """从**运行时** opencode 配置读 baseURL。

    ⚠️ 不要读 `resources/opencode-home-template/opencode.json` ——
    那是模板，里面的 baseURL 指向测试环境（`ossptest.voicecloud.cn`）。
    读它会让你以为「凭证失效」，实际只是地址错。
    """
    path = opencode_config_path()
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (ValueError, OSError):
        return ""
    providers = data.get("provider") or {}
    for conf in providers.values():
        if not isinstance(conf, dict):
            continue
        base = str((conf.get("options") or {}).get("baseURL") or "")
        if base:
            return base.rstrip("/")
    return ""


def read_models() -> list[str]:
    """从运行时配置读模型清单（provider.models 的键）。"""
    path = opencode_config_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (ValueError, OSError):
        return []
    out: list[str] = []
    for conf in (data.get("provider") or {}).values():
        if isinstance(conf, dict):
            out.extend(str(k) for k in (conf.get("models") or {}))
    return out


def discover() -> dict:
    """扫描本机登录态（**不含完整 token**）。"""
    items: list[dict] = []
    dirs: list[str] = []

    root = config_root()
    if root.is_dir():
        dirs.append(str(root))

    creds = read_credentials()
    if creds:
        items.append({
            "source": str(cred_path()),
            "session": mask(str(creds.get("session") or "")),
            "userid": mask(str(creds.get("userid") or "")),
            "phone": mask(str(creds.get("phone") or "")),
            "updated_at": creds.get("updatedAt"),
            "has_plain_credential": True,
        })

    for pattern in FALLBACK_DIRS:
        path = pathlib.Path(os.path.expandvars(pattern))
        if path.is_dir():
            dirs.append(str(path))

    hint = ""
    if not items:
        hint = ("没找到 Loomy 凭证。预期位置：%s。"
                "请在客户端登录一次后重试。" % cred_path())
    elif not read_runtime_base_url():
        hint = "找到凭证但没读到运行时 baseURL（opencode.json），请确认客户端已启动过一次。"

    return {
        "platform": PLATFORM,
        "config_root": str(root),
        "data_dirs": dirs,
        "items": items,
        "can_import": bool(items),
        "base_url": read_runtime_base_url(),
        "models": read_models()[:30],
        "hint": hint,
        "note": "Loomy 无 refresh 机制，session 失效需在客户端重新登录后重新扫描",
        "errors": [],
    }
