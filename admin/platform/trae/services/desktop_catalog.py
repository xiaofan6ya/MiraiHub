"""从本机 Trae 客户端读取**真实模型目录**（只读）。

为什么必须走这里而不是上游 `model_list` 接口：

    实测（2026-10-27，本机双开 Trae）：
      · `POST /api/ide/v1/model_list` 只回 **6 个（国内版）/ 9 个（国际版）**
        旧模型（deepseek-V3 / gpt-4o / gemini_2.5_flash …）；
      · 客户端 `state.vscdb` 里的 `*_AI.agent.model.model_list_map`
        有 **几十个**当前真在用的新模型：
          gpt-6.1-sol / gpt-6-sol / gpt-6-luna / gpt-5.6-* / gpt-5.4 / gpt-5.2
          gemini-3.1-pro / gemini-3-flash-solo
          deepseek-v4.1-flash / deepseek-v4-pro-0813 / kimi-k3 / minimax-m3 …

  也就是说：**接口是给旧客户端留的兼容子集，客户端缓存才是真实全量**。
  用接口当唯一数据源，用户会看到「明明能用 GPT-6，面板里只有 GPT-5」。

数据源：`%APPDATA%/Trae/User/globalStorage/state.vscdb`（国际版）
        `%APPDATA%/Trae CN/User/globalStorage/state.vscdb`（国内版）
表结构：`ItemTable(key TEXT, value TEXT)`，
        key = `<uid>_AI.agent.model.model_list_map`，
        value = JSON `{分组 -> [模型对象, ...]}`。

只读打开（`mode=ro`），绝不修改客户端一个字节。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3

logger = logging.getLogger("admin.platform.trae.desktop_catalog")

#: 客户端目录名 -> realm
REALM_FOLDERS = {"intl": "Trae", "cn": "Trae CN"}

KEY_SUFFIX = "_AI.agent.model.model_list_map"

#: 这些分组是「真正可用于对话」的。其它分组（design/work 系列）是构建器/设计
#: 专用，模型集合是前者的子集，合并时以这些为主、其余只补元数据。
PRIMARY_GROUPS = ("solo_agent", "agent", "solo_coder", "solo_agent_lite")

#: 自定义模型的前缀：`custom_openai_compatible//xxx`。它们指向用户自己配的
#: 第三方端点，走本网关转发没有意义，且名字不是上游真名。
CUSTOM_PREFIX = "custom_openai_compatible"


def appdata_dir() -> str:
    return (os.environ.get("APPDATA")
            or os.path.join(os.path.expanduser("~"), "AppData", "Roaming"))


def state_path(realm: str) -> str:
    folder = REALM_FOLDERS.get(realm)
    if not folder:
        return ""
    path = os.path.join(appdata_dir(), folder, "User", "globalStorage", "state.vscdb")
    return path if os.path.isfile(path) else ""


def _context_of(model: dict) -> int:
    value = model.get("context_window")
    if isinstance(value, int) and value > 0:
        return value
    size = model.get("context_window_size")
    if isinstance(size, dict):
        for key in ("max", "default"):
            candidate = size.get(key)
            if isinstance(candidate, int) and candidate > 0:
                return candidate
            if isinstance(candidate, list) and candidate:
                first = candidate[0]
                if isinstance(first, int) and first > 0:
                    return first
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, int) and first > 0:
            return first
    if isinstance(size, list) and size:
        first = size[0]
        if isinstance(first, int) and first > 0:
            return first
    return 0


#: 免费用户可用的「身份」编号。
#:
#: 客户端每个模型都带 `features.access.data.identity_list`，这是一份
#: **套餐身份白名单** —— 只有列表里包含当前身份，模型才可选。
#: 实测（`Trae CN` / `Trae` 的 state.vscdb，2026-10）：
#:
#:     [0, 5, 1, 2, 3, 100]  -> 32 个（含 0 = 免费可用）
#:     [1, 2, 3, 100]        ->  1 个（无 0 = 需会员）
#:     [5, 4, 1, 2, 3]       ->  6 个（glm-5.2 / gpt-6-astra / gpt-5.5 …）
#:     [1, 4, 2, 3]          ->  3 个（gpt-6-sol / gpt-6.1-sol …）
#:
#: 界面上「锁 + 解锁新模型」提示出现的，正是**不含 0** 的那些 ——
#: 所以 `0` 就是免费身份。列表缺失时按「未知」处理（不当成付费排除）。
FREE_IDENTITY = 0


def _identity_list_of(model: dict) -> list[int]:
    """取模型的套餐身份白名单。`features` 可能是 JSON 字符串，两种都要认。"""
    features = model.get("features")
    if isinstance(features, str):
        try:
            features = json.loads(features)
        except (ValueError, TypeError):
            return []
    if not isinstance(features, dict):
        return []
    access = features.get("access")
    if not isinstance(access, dict):
        return []
    data = access.get("data")
    if not isinstance(data, dict):
        return []
    raw = data.get("identity_list")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, int)]


def is_free_model(identity: list[int]) -> bool:
    """免费账号能否调用。

    **不确定时返回 True**：`identity_list` 为空说明客户端没给这个字段
    （老版本客户端、自建模型等），此时按「可能可用」处理 ——
    宁可让用户自己试一次并被上游明确拒绝，也不要凭空把一个
    能用的模型藏起来。
    """
    if not identity:
        return True
    return FREE_IDENTITY in identity


def _max_tokens_of(model: dict) -> int:
    for key in ("max_tokens", "prompt_max_tokens"):
        value = model.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return 0


def read_models(realm: str) -> dict:
    """读取某区的全量模型。

    返回：`{"realm", "path", "groups", "models": [...], "error"}`。
    绝不抛异常 —— 客户端没装 / 没登录 / 库被锁都只返回 `error`，
    调用方回落到快照目录即可。
    """
    out = {"realm": realm, "path": "", "groups": {}, "models": [], "error": ""}
    path = state_path(realm)
    out["path"] = path
    if not path:
        out["error"] = "未找到 Trae 客户端数据库（%s）" % REALM_FOLDERS.get(realm, realm)
        return out

    # 只读打开：`mode=ro` + URI，避免在客户端写入时争锁、也避免误改。
    # Windows 路径要先转成正斜杠，URI 不接受反斜杠。
    uri = "file:" + path.replace("\\", "/") + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except Exception as exc:  # noqa: BLE001
        out["error"] = "打开客户端数据库失败：%s" % exc
        return out

    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT key, value FROM ItemTable WHERE key LIKE ? LIMIT 8",
            ("%" + KEY_SUFFIX,))
        rows = cursor.fetchall()
    except Exception as exc:  # noqa: BLE001
        out["error"] = "读取客户端数据库失败：%s" % exc
        return out
    finally:
        conn.close()

    if not rows:
        out["error"] = "客户端数据库里没有模型缓存（请先在本机登录 Trae 并打开一次对话）"
        return out

    merged: dict[str, dict] = {}
    groups_found: dict[str, int] = {}
    for _key, raw in rows:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(payload, dict):
            continue
        for group, models in payload.items():
            if not isinstance(models, list) or not models:
                continue
            groups_found[str(group)] = len(models)
            for model in models:
                if not isinstance(model, dict):
                    continue
                name = str(model.get("name") or "").strip()
                if not name or name.startswith(CUSTOM_PREFIX):
                    continue
                # 同一模型可能出现在多个分组，取「上下文/最大输出」信息最全的那份
                previous = merged.get(name)
                if previous is None:
                    merged[name] = model
                    continue
                if not _context_of(previous) and _context_of(model):
                    merged[name] = model

    if not merged:
        out["error"] = "客户端缓存里没有可用模型"
        return out

    models = []
    for name, model in merged.items():
        identity = _identity_list_of(model)
        models.append({
            "name": name,
            "upstream": name,
            "display_name": str(model.get("display_name") or name),
            "model_type": str(model.get("model_type") or "chat_model"),
            "context_window": _context_of(model),
            "max_tokens": _max_tokens_of(model),
            "multimodal": bool(model.get("multimodal")),
            "chat": bool(model.get("chat", True)),
            "realm": realm,
            "identity_list": identity,
            # 免费账号是否可用。判据见 FREE_IDENTITY 的注释。
            "free": is_free_model(identity),
        })
    models.sort(key=lambda item: (not item["free"], item["model_type"], item["name"]))
    out["groups"] = groups_found
    out["models"] = models
    out["free_count"] = sum(1 for m in models if m["free"])
    return out


def read_all() -> dict:
    """读两个区，返回 `{realm: read_models(realm)}`。"""
    return {realm: read_models(realm) for realm in REALM_FOLDERS}