"""Live display metadata for the public model list; model IDs stay stable."""
from datetime import datetime
import logging

from admin.model_busy import LABELS, normalize_response
from model_metadata import METADATA_FIELDS, get_model_vendor, model_type, type_tags

_logger = logging.getLogger(__name__)


def display_options(db):
    import json
    from admin.models import SystemSetting
    row = db.get(SystemSetting, "model_display")
    try:
        value = json.loads(row.value) if row else {}
    except (ValueError, TypeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    return {key: value.get(key) is not False
            for key in ("show_multiplier", "show_busy", "official_only")}


def official_order(db) -> list[str]:
    """官方前端可见模型 ID 列表（同步时由 cli agent 名单落库）；未同步为空。"""
    import json
    from admin.models import SystemSetting
    row = db.get(SystemSetting, "model_official_order")
    try:
        value = json.loads(row.value) if row else []
    except (ValueError, TypeError):
        value = []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def apply_official_visibility(entries, order, enabled=True):
    """官方同显的本地目录版：过滤 + 官方排序。

    order 为空（从未同步过官方名单）时不做任何过滤，避免误伤；
    order 里没有的条目排在官方模型之后，保持可见但明确是「旧模型」。
    """
    if not enabled or not order:
        return entries
    rank = {mid: idx for idx, mid in enumerate(order)}
    return sorted(entries,
                  key=lambda entry: (1, 0) if entry["id"] not in rank
                  else (0, rank[entry["id"]]))


def enrich_entries(session, entries, on_error=None, options=None):
    """Read official capacity on every listing, using the catalog's identity.

    Only request models already filtered by the caller's permissions. Capacity
    failures must not hide the catalog or turn stale observations into live data.
    """
    options = options or {"show_multiplier": True, "show_busy": True}
    ids = sorted({entry["id"] for entry in entries})
    levels = {model: "unknown" for model in ids}
    observed_at = None
    stale = False
    if ids and options["show_busy"]:
        try:
            fresh = {}
            for offset in range(0, len(ids), 50):
                batch = ids[offset:offset + 50]
                fresh.update(normalize_response(batch, session.fetch_model_busy(batch)))
            levels = fresh
            observed_at = datetime.utcnow().isoformat() + "Z"
        except Exception as error:
            stale = True
            _logger.warning("模型列表繁忙状态获取失败，返回未知状态")
            if on_error is not None:
                on_error(error)
    return format_entries(entries, levels, options, observed_at, stale)


def format_entries(entries, levels=None, options=None, observed_at=None, stale=True):
    levels = levels or {}
    options = options or {"show_multiplier": True, "show_busy": True}
    result = []
    for entry in entries:
        kind = model_type(entry)
        metadata = {key: entry[key] for key in METADATA_FIELDS if key in entry}
        if isinstance(entry.get("metadata"), dict):
            metadata.update(entry["metadata"])
        vendor = entry.get("vendor") or metadata.get("vendor") or get_model_vendor(entry["id"])
        metadata["vendor"] = vendor
        owned_by = "deepseek" if str(vendor).lower() == "1017" else entry.get("owned_by", "codebuddy")
        level = levels.get(entry["id"], "unknown")
        multiplier = entry.get("credit_multiplier")
        # 图片与视频都按上游 usage.credit 实际用量记账，不套 token 倍率。
        media = kind in ("image", "video")
        credit_label = '按实际用量' if media else f"×{multiplier:g}" if multiplier is not None else "×未知"
        parts = [entry["id"]]
        if options["show_multiplier"]:
            parts.append(credit_label)
        if options["show_busy"]:
            parts.append(LABELS[level])
        item = {**entry,
                "vendor": vendor, "metadata": metadata, "owned_by": owned_by,
                "model_type": kind, "tags": type_tags(entry),
                **({"credit_multiplier": None, "billing_mode": "upstream_usage", "is_free": False}
                   if media else {}),
                "name": ' · '.join(parts),
                "busy_level": level, "busy_label": LABELS[level],
                "busy_observed_at": observed_at, "busy_stale": stale}
        result.append(item)
    return result