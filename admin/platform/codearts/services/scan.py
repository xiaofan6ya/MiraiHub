"""CodeArts 本机扫描：只读客户端登录态，不解密到前端。

## 为什么不直接把凭证给前端

`access_key/secret_key/security_token` 是**可直接调用上游的凭据**。
扫描结果只回「有谁、什么时候登录的、还剩多久过期」，具体凭证留在后端
（`import_local` 直接从同一份文件读，不需要前端中转）。

## 与「导入」的分工

* `discover()` —— 给用户看「我扫到了什么」，不含秘密；
* `import_local()` —— 真正落库（`services/accounts.py`），顺带刷一次额度。

这两件事分开，是为了让「扫描」在客户端未登录时也能给出**可读的原因**
（`no_client` / `no_login` / `dpapi`），而不是一个空列表。
"""
from __future__ import annotations

import logging
import os

from admin.platform.codearts.core import session

logger = logging.getLogger("admin.platform.codearts.scan")

PLATFORM = "codearts"


def mask(value: str, keep: int = 4) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= keep * 2:
        return text[:keep] + "***"
    return text[:keep] + "*" * 8 + text[-keep:]


def installed_dirs() -> list[str]:
    """客户端数据目录（存在的才返回）。"""
    path = session.app_data_dir()
    return [path] if os.path.isdir(path) else []


def discover() -> dict:
    """扫描本机登录态（**不含 AK/SK/STS**）。"""
    dirs = installed_dirs()
    items: list[dict] = []
    hint = ""
    error = ""
    kind = ""

    try:
        local = session.load_session()
    except session.SessionError as exc:
        kind = exc.kind
        error = exc.message
        if exc.kind == "no_client":
            hint = ("没找到 CodeArts 客户端数据目录（预期 %s）。"
                    "请先安装并登录 CodeArts 客户端。" % session.app_data_dir())
        elif exc.kind == "no_login":
            hint = "客户端已安装但还没有登录态，请在 CodeArts 里登录一次后重试。"
        elif exc.kind == "dpapi":
            hint = ("登录态无法解密：DPAPI/OSCrypt 密钥不匹配。"
                    "通常是换了 Windows 账户或把数据目录拷到别的机器 —— "
                    "请在当前账户下重新登录一次客户端。")
        else:
            hint = exc.message
        local = None

    if local is not None:
        items.append({
            "source": session.secret_file(),
            "uid": local.uid,
            "user_name": local.user_name,
            "domain_id": mask(local.domain_id),
            "access_key": mask(local.access_key),
            "has_security_token": bool(local.security_token),
            "has_refresh": bool(local.refresh_token),
            "expires_at": local.expires_at or None,
            "expires_in": local.expires_in,
            "created_at": local.created_at,
            "updated_at": local.updated_at,
            "can_refresh": bool(local.refresh_token
                                and local.dpop_private_key
                                and local.pkce_code_verifier),
        })
        if not local.security_token:
            hint = "登录态缺少 securityToken（临时凭证不完整），建议重新登录客户端。"

    return {
        "platform": PLATFORM,
        "config_root": session.app_data_dir(),
        "data_dirs": dirs,
        "items": items,
        "can_import": bool(items),
        "models": [],
        "hint": hint,
        "error": error,
        "error_kind": kind,
    }
