"""Trae 双区模型目录：别名解析、元数据、区域推断。

目录来自 trae2api-hub 的快照（见 `scripts/build_trae_catalog.py`），只保留
对话相关字段。网关用它做三件事：

1. **把客户端写的模型名解析成上游真名**。上游只认 `model_list` 里的
   `name`，而且**大小写敏感**（实测 `deepseek-V3` 可用、`DeepSeek-V3` 回 4023），
   所以解析结果必须保留上游的原始大小写。
2. **按模型判断区域**。同一网关同时服务国内版与国际版，模型归属决定默认
   打哪个区；显式 `?realm=` / `X-Realm` / `X-Platform: trae:cn` 永远优先。
3. **给出 `/v1/models` 需要的元数据**（上下文、最大输出、是否推理模型）。

索引是**按区域隔离**的：`deepseek-V3` 在两个区都存在但归属不同，
用一张全局表会让国际版的清单混进国内版条目（客户端按 realm 过滤会失效）。
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path

logger = logging.getLogger("admin.platform.trae.catalog")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REALM_KEYS = ("cn", "intl")

_lock = threading.Lock()
_snapshots: dict[str, dict] = {}
#: realm -> {alias(归一) -> entry}
_index: dict[str, dict[str, dict]] = {}
#: realm -> {上游真名}
_live: dict[str, set[str]] = {}
#: realm -> {真名 -> 条目}。与 `_live` 的区别：`_live` 只有名字（用于判断
#: 「这个模型当前可用吗」），这里存**条目本体**，供 `realm_models()` 直接
#: 取出 —— 避免因为别名冲突（`DeepSeek-V4-Flash` vs 快照的
#: `deepseek-v4-flash`）把条目漏掉。
_live_entry: dict[str, dict[str, dict]] = {}
#: realm -> 快照里的条目（保持目录顺序，供「上游未返回时」兜底）
_static: dict[str, list[dict]] = {}


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _aliases_of(model: dict) -> set[str]:
    names = set()
    for key in ("key", "name", "upstream_key", "display_name"):
        if model.get(key):
            names.add(str(model[key]))
    for alias in model.get("aliases") or ():
        names.add(str(alias))
    out = set()
    for name in names:
        text = name.strip()
        if not text:
            continue
        out.add(text.lower())
        out.add(_compact(text))
    return out


def _load_snapshot(realm: str) -> dict:
    path = DATA_DIR / ("catalog_%s.json" % realm)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 目录缺失不应让网关起不来
        logger.warning("trae 目录加载失败 %s：%s", path, exc)
        return {"realm": realm, "models": []}


def _entry_from_snapshot(model: dict, realm: str) -> dict:
    return {
        "key": str(model.get("key") or model.get("name") or ""),
        "upstream": str(model.get("upstream_key") or model.get("name")
                        or model.get("key") or ""),
        "display_name": str(model.get("display_name") or model.get("name") or ""),
        "realm": realm,
        "model_type": str(model.get("model_type") or "chat_model"),
        "context_window": int(model.get("context_window") or 0),
        "max_tokens": int(model.get("max_tokens") or 0),
        "multimodal": bool(model.get("multimodal")),
        "fee_level": model.get("fee_model_level"),
        "provider": model.get("provider"),
        "source": "snapshot",
    }


def _build_index() -> tuple[dict, dict]:
    index: dict[str, dict[str, dict]] = {}
    static: dict[str, list[dict]] = {}
    for realm in REALM_KEYS:
        bucket: dict[str, dict] = {}
        ordered: list[dict] = []
        for model in (_snapshots.get(realm) or {}).get("models") or ():
            entry = _entry_from_snapshot(model, realm)
            if not entry["key"]:
                continue
            ordered.append(entry)
            for alias in _aliases_of(model):
                bucket.setdefault(alias, entry)
        index[realm] = bucket
        static[realm] = ordered
    return index, static


def reload() -> int:
    global _snapshots, _index, _static
    with _lock:
        _snapshots = {realm: _load_snapshot(realm) for realm in REALM_KEYS}
        _index, _static = _build_index()
        _live.clear()
    return sum(len(v) for v in _index.values())


def snapshot(realm: str) -> dict:
    if not _snapshots:
        reload()
    return _snapshots.get(realm) or {"realm": realm, "models": []}


def entry(model: str, realm: str | None = None) -> dict | None:
    """把任意写法解析成目录条目。给了 realm 时只看该区。"""
    if not _snapshots:
        reload()
    text = str(model or "").strip()
    if not text:
        return None
    realms = [realm] if realm in REALM_KEYS else list(REALM_KEYS)
    for candidate in (text.lower(), _compact(text)):
        for target in realms:
            hit = (_index.get(target) or {}).get(candidate)
            if hit:
                return hit
    return None


def upstream_name(model: str, realm: str | None = None) -> str:
    """客户端模型名 -> 上游真名。

    **`realm` 必须传**：两个区可以有同名模型（`deepseek-V3`、`glm-5.2`、
    `kimi-k3` …），但它们的上游名、配额、模型能力都不一样。不指定区就会
    跨区命中 —— 表现为「调国内版的 kimi-k3，跑到国际版的 kimi-k3 上」。
    网关侧一律从 `resolve_target()` 拿到确定的 realm 再调用。

    解析不到时**原样返回**：让上游回 4023 比网关瞎猜一个名字更好排查，
    而且用户可能用了一个我们目录里没有、但上游确实支持的新模型。
    """
    hit = entry(model, realm)
    if hit:
        return str(hit.get("upstream") or hit.get("key") or model)
    return str(model or "")


def realm_for(model: str) -> str:
    """模型默认归属区域。

    两区都有的模型（`deepseek-V3`）**返回空串**：此时「归属」没有唯一答案，
    硬选一个区会让客户端打到错误的区、拿到的额度与配额都不对。空串交给
    调用方回落平台默认区，并由客户端用 `X-Platform: trae:intl` 显式指定。
    """
    if not _snapshots:
        reload()
    text = str(model or "").strip()
    if not text:
        return ""
    realms = []
    for candidate in (text.lower(), _compact(text)):
        for realm in REALM_KEYS:
            if candidate in (_index.get(realm) or {}):
                realms.append(realm)
    unique = sorted(set(realms))
    return unique[0] if len(unique) == 1 else ""


def note_live_rows(realm: str, rows) -> int:
    """登记一批模型条目（来自客户端缓存或上游 model_list）。

    **调用顺序决定优先级**（后写覆盖先写）：

        1. 快照（`data/catalog_*.json`）—— 兜底，可能过期
        2. 上游 `model_list`         —— 实测只有 6~9 个旧模型
        3. 客户端 `state.vscdb`      —— **真实全量**，几十个新模型

    因此上层必须先灌上游清单、再灌客户端目录，否则会退回旧模型集合。
    ⚠️ 上游模型名大小写敏感：索引键归一，但 `upstream` 保留原始大小写。
    """
    added = 0
    realm = str(realm or "").strip().lower()
    if realm not in REALM_KEYS:
        return 0
    with _lock:
        bucket = _index.setdefault(realm, {})
        live = _live.setdefault(realm, set())
        for row in rows or ():
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("upstream") or "").strip()
            if not name:
                continue
            live.add(name)
            ctx = row.get("context_window")
            if ctx is None:
                ctx = row.get("context_window_size")
            if isinstance(ctx, dict):
                ctx = ctx.get("max") or ctx.get("default") or 0
            if isinstance(ctx, list):
                ctx = ctx[0] if ctx else 0
            try:
                context_window = int(ctx or 0)
            except (TypeError, ValueError):
                context_window = 0
            try:
                max_tokens = int(row.get("max_tokens") or 0)
            except (TypeError, ValueError):
                max_tokens = 0
            entry_value = {
                "key": name,
                "upstream": name,
                "display_name": str(row.get("display_name") or name),
                "realm": realm,
                "model_type": str(row.get("model_type") or "chat_model"),
                "context_window": context_window,
                "max_tokens": max_tokens,
                "multimodal": bool(row.get("multimodal")),
                "fee_level": row.get("fee_model_level"),
                "provider": row.get("provider"),
                "source": row.get("source") or "live",
                # 免费可用性：客户端目录会给 `free`（由 identity_list 推导）。
                # 上游 model_list 没有这个字段 -> 保持 None（未知），
                # 上层据此区分「确定要会员」与「不知道」，不会误藏模型。
                "free": row.get("free") if isinstance(row.get("free"), bool) else None,
                "identity_list": row.get("identity_list") or [],
            }
            # live 条目优先覆盖快照（它才是当前账号真正能调通的）
            for alias in {name, entry_value["display_name"]}:
                text = str(alias or "").strip()
                if not text:
                    continue
                bucket[text.lower()] = entry_value
                bucket[_compact(text)] = entry_value
            # **按真名单独记一份**：索引可能被后面同名不同写法的别名覆盖
            # （快照有 `deepseek-v4-flash`，客户端给的是 `DeepSeek-V4-Flash`），
            # 只靠 bucket 反查会漏掉这些条目。
            _live_entry.setdefault(realm, {})[name] = entry_value
            added += 1
    return added


def live_models(realm: str) -> list[str]:
    return sorted(_live.get(realm) or ())


def realm_models(realm: str) -> list[dict]:
    """该区的模型条目。顺序：实时清单优先（客户端/上游），快照剩余补后。"""
    if not _snapshots:
        reload()
    realm = str(realm or "").strip().lower()
    if realm not in REALM_KEYS:
        realm = "cn"
    bucket = _index.get(realm) or {}
    out: list[dict] = []
    seen: set[str] = set()
    # 1) 实时条目：直接按真名取，避免别名冲突导致漏项
    for name in sorted(_live_entry.get(realm) or {}):
        entry = _live_entry[realm][name]
        upstream = str(entry.get("upstream") or name)
        if upstream in seen:
            continue
        seen.add(upstream)
        out.append(entry)
    # 2) 只有名字、没有条目体的（理论上不该有）退回索引反查
    for name in sorted(_live.get(realm) or ()):
        if name in (_live_entry.get(realm) or {}):
            continue
        hit = bucket.get(name.lower()) or bucket.get(_compact(name))
        if hit is None:
            continue
        upstream = str(hit.get("upstream") or "")
        if upstream in seen:
            continue
        seen.add(upstream)
        out.append(hit)
    for item in _static.get(realm) or ():
        upstream = str(item.get("upstream") or "")
        if not upstream or upstream in seen:
            continue
        seen.add(upstream)
        out.append(item)
    return out


def realm_models_merged() -> list[dict]:
    """两区合并去重后的模型清单（无 realm 参数时用）。"""
    out: list[dict] = []
    seen: set[str] = set()
    for realm in REALM_KEYS:
        for item in realm_models(realm):
            key = str(item.get("upstream") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def all_aliases(realm: str | None = None) -> dict[str, str]:
    """alias -> upstream 名（客户端做模型名映射时用）。"""
    buckets = ([realm] if realm in REALM_KEYS else list(REALM_KEYS))
    out = {}
    for target in buckets:
        for alias, hit in (_index.get(target) or {}).items():
            out.setdefault(alias, str(hit.get("upstream") or ""))
    return out


reload()