"""小浣熊本机凭证发现（扫描入库）。

## 客户端凭证在哪（逆向 `@raccoonoffice-desktop` 实测）

客户端主进程 `build/electron/main/agent/boxAgentAuthFile.js`：

    authPath = BOX_AGENT_CONFIG_DIR
               or resolveBoxAgentHome(homedir)/config/auth.json
    resolveBoxAgentHome = BOX_AGENT_HOME or ~/.box-agent

而客户端启动时把 `BOX_AGENT_HOME` 指到自己的 profile 目录，**实测本机**：

    C:/Users/<user>/.office-raccoon/agent/config/auth.json

内容是**明文 JSON**（0600）：

    {"access_token": "<JWT>", "office_identity": "personal", ...}

- `access_token` 是 IAM JWT：`iss=xiaohuanxiong.com/iam`、`sub=pu-…`（即 uid）、
  `exp`（30 天）。客户端源码 `iamToken.isIamToken` 以 `iss` 后缀 `/iam` 识别；
  是 IAM token 时**不存 refresh_token**（续期走服务端会话）。
- PR #9 作者说"凭证在 Electron LevelDB 里扫不出来"——那是旧版/误判，
  现版本就是明文 auth.json，直接读。

JWT 解码纯标准库（base64 url + json），与 loomy 的做法一致。
"""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("admin.platform.raccoon.scan")

PLATFORM = "raccoon"

#: 客户端配置目录候选（按优先级）。
#: `BOX_AGENT_CONFIG_DIR` > `BOX_AGENT_HOME/config` > ~/.office-raccoon/agent/config
#: > ~/.box-agent/config（框架默认值）。
_CANDIDATE_DIRS = (
    os.path.join("%(home)s", ".office-raccoon", "agent", "config"),
    os.path.join("%(home)s", ".box-agent", "config"),
)


def jwt_claims(token: str) -> dict:
    """解 JWT payload（不验签——签名与账号状态由服务端校验，口径同客户端）。"""
    token = str(token or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(pad))
        return claims if isinstance(claims, dict) else {}
    except Exception:  # noqa: BLE001 - 非 JWT 直接当不可解析
        return {}


def is_iam_token(token: str) -> bool:
    """客户端口径：`iss` 以 `/iam` 结尾即 IAM token。"""
    iss = str(jwt_claims(token).get("iss") or "")
    return iss.endswith("/iam")


def _candidate_dirs() -> list[Path]:
    out: list[Path] = []
    env_cfg = (os.getenv("BOX_AGENT_CONFIG_DIR") or "").strip()
    env_home = (os.getenv("BOX_AGENT_HOME") or "").strip()
    if env_cfg:
        out.append(Path(env_cfg))
    if env_home:
        out.append(Path(env_home) / "config")
    home = Path(os.path.expanduser("~"))
    for tpl in _CANDIDATE_DIRS:
        out.append(Path(tpl % {"home": str(home)}))
    seen: set[str] = set()
    uniq = []
    for p in out:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def auth_file_candidates() -> list[Path]:
    """所有可能存在的 auth.json 路径（存在的才返回）。"""
    out = []
    for d in _candidate_dirs():
        p = d / "auth.json"
        try:
            if p.is_file():
                out.append(p)
        except OSError:
            continue
    return out


def read_credentials() -> dict | None:
    """读本机客户端凭证（**不落库**，仅供扫描展示与导入）。

    返回 normalize_record 可直接消费的形状：
        {"token": ..., "uid": ..., "expiresAt": <ms>, "office_identity": ...}
    """
    for path in auth_file_candidates():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        token = str(raw.get("access_token") or raw.get("token")
                    or raw.get("auth_token") or "").strip()
        if not is_iam_token(token):
            # 非法/非 IAM token（可能登录态已清空）——跳过这个文件
            logger.debug("跳过非 IAM token 的凭证文件 %s", path)
            continue
        claims = jwt_claims(token)
        uid = str(claims.get("sub") or "")
        exp = claims.get("exp")
        return {
            "token": token,
            "uid": uid,
            "expiresAt": int(exp * 1000) if isinstance(exp, (int, float)) and exp else None,
            "office_identity": str(raw.get("office_identity") or "personal"),
            "office_org_name": str(raw.get("office_org_name") or ""),
            "source_file": str(path),
        }
    return None


def _mask(value: str, keep: int = 4) -> str:
    value = str(value or "")
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * max(1, len(value) - keep - 3) + value[-3:]


def discover() -> dict:
    """扫描本机（**不含完整 token**），供控制台展示与确认导入。"""
    items: list[dict] = []
    dirs = []
    for path in auth_file_candidates():
        dirs.append(str(path))
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        token = str(raw.get("access_token") or raw.get("token")
                    or raw.get("auth_token") or "")
        claims = jwt_claims(token)
        uid = str(claims.get("sub") or "")
        if not is_iam_token(token) or not uid:
            continue
        exp = claims.get("exp")
        import time as _time
        expires_desc = ""
        if isinstance(exp, (int, float)) and exp:
            days = (exp - _time.time()) / 86400
            expires_desc = ("约 %d 天后到期" % days) if days > 0 else "已过期"
        items.append({
            "source": str(path),
            "uid": _mask(uid, 8),
            "uid_full": uid,          # 勾选导入用（uid 本身不敏感，token 才是）
            "identity": str(raw.get("office_identity") or "personal"),
            "expires": expires_desc,
            "has_plain_credential": True,
        })
    hint = ""
    if not items:
        hint = ("没找到小浣熊客户端登录态。请先安装并登录办公小浣熊客户端，"
                "或改用「授权登录」（扫码，免客户端）。"
                "预期位置：~/.office-raccoon/agent/config/auth.json")
    return {
        "platform": PLATFORM,
        "data_dirs": dirs,
        "items": items,
        "can_import": bool(items),
        "hint": hint,
    }
