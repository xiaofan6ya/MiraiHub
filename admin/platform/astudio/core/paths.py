"""AStudio 桌面端本机路径探测（扫描入库的凭证源）。

## 凭证在哪儿（桌面端实测）

    <DataRoot>/userdata/astron-session.json
        {"modelBearerToken": "...", "token": "...", "ssoSessionId": "...",
         "accountId": "...", "uid": "...", "banned": false}
        - modelBearerToken 是模型凭据（JWT，约 3 年）；
        - token / ssoSessionId / accountId 是网页会话 Cookie 材料
          （积分 / 签到用，三样里 token + accountId 必需）；
        - banned === true 表示账号已被封禁，不可用。
    <DataRoot>/runtime/acode-home/config.toml
        [model_providers.astron-spark] 的 experimental_bearer_token
        —— 只有模型凭据，**没有网页会话**（积分 / 签到不可用）。

DataRoot 查找顺序：
    1. ASTUDIO_HOME（或 SYNARAHOME / SYNARA_HOME）环境变量；
    2. Windows 注册表 HKCU\\Software\\AStudioStorage 的
       Production / Canary / DevelopmentDataRoot（非 Windows 跳过）；
    3. 兜底 ~/.acode/acode/。

- 文件是明文 JSON（客户端自己就没加密）。
- 扫描路径禁止写死盘符/用户名：统一从 os.path.expanduser 展开，
  允许环境变量覆盖（多 profile / 迁移盘场景）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

#: 注册表查询的 profile 前缀（桌面端按 build 环境登记）。
_REGISTRY_KEY = r"HKCU\Software\AStudioStorage"
_REGISTRY_PREFIXES = ("Production", "Canary", "Development")

#: 兜底数据根（桌面端未在注册表登记时也可能落在这里）。
FALLBACK_DATA_ROOT = os.path.join(os.path.expanduser("~"), ".acode", "acode")

#: 凭据异常地长的阈值（JWT 正常远小于此；异常长多半是粘贴错了对象）。
MAX_KEY_LENGTH = 4096


def configured_root() -> str:
    """ASTUDIO_HOME / SYNARA_HOME 环境变量指定的数据根（空则无）。"""
    for name in ("ASTUDIO_HOME", "SYNARA_HOME", "SYNARAHOME"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def registry_data_roots() -> list[str]:
    """Windows 注册表登记的 DataRoot（非 Windows 返回空）。"""
    if not sys.platform.startswith("win"):
        return []
    roots = []
    for prefix in _REGISTRY_PREFIXES:
        value = _registry_query(prefix + "DataRoot")
        if value:
            roots.append(value)
    return roots


def _registry_query(value_name: str) -> str:
    try:
        result = subprocess.run(
            ["reg", "query", _REGISTRY_KEY, "/v", value_name, "/reg:64"],
            capture_output=True, text=True, timeout=5,
            check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        import re
        match = re.search(r"REG_SZ\s+(\S[^\r\n]*)", result.stdout or "")
        return match.group(1).strip() if match else ""
    except Exception:  # noqa: BLE001 - 读注册表失败按「没有登记」处理
        return ""


def data_roots() -> list[str]:
    """按顺序去重后的全部候选数据根。"""
    roots = []
    configured = configured_root()
    if configured:
        roots.append(configured)
    roots.extend(registry_data_roots())
    roots.append(FALLBACK_DATA_ROOT)
    seen = set()
    out = []
    for root in roots:
        norm = os.path.normpath(root)
        if norm in seen:
            continue
        seen.add(norm)
        out.append(root)
    return out


def session_files() -> list[str]:
    """全部候选的 astron-session.json（存在的才返回）。"""
    out = []
    for root in data_roots():
        path = os.path.join(root, "userdata", "astron-session.json")
        if os.path.isfile(path):
            out.append(path)
    return out


def config_files() -> list[str]:
    """全部候选的 config.toml（存在的才返回）。"""
    out = []
    for root in data_roots():
        path = os.path.join(root, "runtime", "acode-home", "config.toml")
        if os.path.isfile(path):
            out.append(path)
    return out


def read_session(path: str) -> dict | None:
    """读一个 astron-session.json → 标准记录（解析失败/无凭据返回 None）。

    返回结构（入库 payload 的超集）：
        {"key", "uid", "session", "name", "source", "file", "banned",
         "problem"（读不出来时的原因）}
    """
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:  # noqa: BLE001 - 单个文件坏了不拖垮整次扫描
        return {"problem": "%s 不是有效 JSON" % path,
                "file": path, "source": "local-scan"}
    if not isinstance(payload, dict):
        return {"problem": "%s 不是对象" % path, "file": path,
                "source": "local-scan"}
    banned = payload.get("banned") is True
    key = str(payload.get("modelBearerToken") or "").strip()
    if not key:
        # 老版本登录态可能只有 token（网页会话 token 当模型凭据用）。
        # 两者都缺才算坏；缺 modelBearerToken 但 token 在时按「只有模型
        # 凭据」对待（无网页会话）。
        key = str(payload.get("token") or "").strip()
    if not key:
        return {"problem": "%s 里没有 modelBearerToken/token" % path,
                "file": path, "source": "local-scan", "banned": banned}
    if len(key) > MAX_KEY_LENGTH:
        return {"problem": "%s 的凭据异常地长" % path, "file": path,
                "source": "local-scan", "banned": banned}
    uid = str(payload.get("uid") or "").strip() or None
    web_token = str(payload.get("token") or "").strip()
    account_id = str(payload.get("accountId") or "").strip()
    session = None
    if web_token and account_id:
        session = {"token": web_token, "account_id": account_id}
        sso = str(payload.get("ssoSessionId") or "").strip()
        if sso:
            session["sso_session_id"] = sso
    return {
        "key": key,
        "uid": uid,
        "session": session,
        "name": uid or (account_id or ""),
        "source": "local-scan",
        "file": path,
        "banned": banned,
    }


def read_config_token(path: str) -> dict | None:
    """读 config.toml 的 [model_providers.astron-spark] 段 → 模型凭据。

    该来源**没有网页会话**：只有模型凭据，积分/签到不可用（如实标注）。
    """
    try:
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
    except Exception:  # noqa: BLE001
        return None
    import re
    section = re.search(r"^\s*\[\s*model_providers\s*\.\s*astron-spark\s*\]\s*$",
                        content, re.MULTILINE)
    if section is None:
        return None
    body = content[section.end():]
    next_section = re.search(r"^\s*\[", body, re.MULTILINE)
    if next_section is not None:
        body = body[:next_section.start()]
    token = re.search(r'^\s*experimental_bearer_token\s*=\s*"([^"]+)"\s*$',
                      body, re.MULTILINE)
    if token is None:
        return None
    key = token.group(1).strip()
    if not key or len(key) > MAX_KEY_LENGTH:
        return None
    uid_match = re.search(r'^\s*uid\s*=\s*"([^"]+)"\s*$', body, re.MULTILINE)
    return {
        "key": key,
        "uid": uid_match.group(1).strip() if uid_match else None,
        "session": None,
        "name": uid_match.group(1).strip() if uid_match else "",
        "source": "local-scan",
        "file": path,
        "banned": False,
    }


def discover() -> list[dict]:
    """扫描本机全部 AStudio 登录态（不含敏感字段原文，供列表展示）。

    字段对齐通用扫描弹窗（showScanResult）的判定：
      * `has_plain_credential` / `file` —— 「可导入」的判定键；
      * `uid_full` —— 勾选框 data-uid 用的完整 uid（导入按它过滤）；
      * `uid` —— 展示用短标识。
    """
    items = []
    seen_keys: set[str] = set()
    for path in session_files():
        record = read_session(path)
        if record is None:
            continue
        key = record.get("key") or ""
        if key:
            if key in seen_keys:
                continue
            seen_keys.add(key)
        items.append(_scan_item(record))
    for path in config_files():
        record = read_config_token(path)
        if record is None:
            continue
        key = record.get("key") or ""
        if key in seen_keys:
            continue
        seen_keys.add(key)
        items.append(_scan_item(record))
    return items


def _scan_item(record: dict) -> dict:
    problem = record.get("problem")
    key = str(record.get("key") or "")
    uid = str(record.get("uid") or "")
    if not uid and key:
        import hashlib
        uid = "ast-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    session = record.get("session") if isinstance(record.get("session"), dict) else None
    item = {
        "uid": uid[:18] + ("…" if len(uid) > 18 else ""),
        "uid_full": uid,
        "name": record.get("name") or "AStudio",
        "realm": "cn",
        "has_session": bool(session),
        "has_plain_credential": not problem,
        "file": record.get("file") or "",
        "path": record.get("file") or "",
        "token_tail": key[-6:] if len(key) > 6 else "",
        "source": "local-scan",
        "banned": bool(record.get("banned")),
    }
    if problem:
        item["problem"] = problem
    elif not session:
        item["note"] = "只有模型凭据（无网页会话），积分/签到不可用"
    elif record.get("banned"):
        item["note"] = "账号已被封禁，不可用"
    return item
