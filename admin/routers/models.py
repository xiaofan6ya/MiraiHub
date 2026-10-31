"""模型配置管理：获取可用模型列表 / 系统级&用户级白名单 / 启用禁用。

使用方式：
  1. 先调用 POST /api/models/sync 从后端拉取可用模型列表（自动入库）
  2. 在列表中启用/禁用特定模型
  3. 代理转发时自动过滤不在白名单内的模型
"""
import httpx
import inspect
import json
import re
import time
import threading
import statistics
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

import logging

from admin import backend
from admin.account_binding import scope_accounts
from admin.config import settings
from admin.db import get_db
from admin.models import Account, ModelConfig
# 模块级导入 registry：`_catalog_of` 要按落库平台分派网关，
# 那些函数在局部重复 import 容易漏（原来只有 3 处局部导入）
from admin.platform import registry
from admin.security import require_admin
from model_metadata import (metadata, credit_number, model_type, type_tags,
                            is_media_model, is_video_model)

logger = logging.getLogger("admin.routers.models")

router = APIRouter(prefix="/api/models", tags=["models"])
_sync_lock = threading.Lock()
_probe_lock = threading.Lock()


class ModelToggleIn(BaseModel):
    enabled: bool = True


class ModelBatchIn(BaseModel):
    model_ids: list[str] = []
    enabled: bool = True
    level: str = "system"  # system | user


class ModelDisplayIn(BaseModel):
    show_multiplier: bool = True
    show_busy: bool = True
    official_only: bool = True


@router.get("/display")
def model_display(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    from admin.model_listing import display_options
    return display_options(db)


@router.put("/display")
def save_model_display(body: ModelDisplayIn, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    from admin.models import SystemSetting
    row = db.get(SystemSetting, "model_display")
    if row is None:
        row = SystemSetting(key="model_display")
        db.add(row)
    row.value = json.dumps(body.dict())
    db.commit()
    return body.dict()


def _invalidate_routing_cache() -> None:
    """模型配置一变，立刻让网关侧的模型目录缓存失效。

    网关按模型选平台时会缓存 `model_configs`（10 秒）——
    不主动失效的话，「刚同步完新模型 → 马上调用」会得到 404
    `model_not_found`，看起来像同步失败。
    """
    try:
        from admin.platform import dispatch

        dispatch.invalidate_model_cache()
    except Exception:  # noqa: BLE001 - 缓存清理失败不影响保存
        pass


def _get_enabled_models(db: Session) -> set[str]:
    """获取当前启用的模型 ID 集合（用于代理过滤）。"""
    rows = db.query(ModelConfig).filter(ModelConfig.enabled == 1).all()
    return {m.model_id for m in rows}


def _get_free_models(db: Session) -> set[str]:
    """获取当前启用且**真免费**的模型 ID 集合。

    ## 判定规则

    **只有 0 倍率才算免费。**

      * `credit_multiplier == 0`  -> 免费 ✓
      * `credit_multiplier > 0`   -> 收费
      * `credit_multiplier < 0`   -> **无倍率概念**（Loomy 按 token 计量），
        不是免费 —— 排除
      * `credit_multiplier is NULL` -> 老数据没同步过倍率，按免费处理
        （保持向后兼容；新同步的都会写具体值）

    原来把 `is NULL` 和 `== 0` 一起当免费，还漏了负值 ——
    Loomy 这种「无量纲」平台会被整体误判成免费。
    """
    rows = db.query(ModelConfig).filter(
        ModelConfig.enabled == 1,
        (
            (ModelConfig.credit_multiplier == 0)
            | (ModelConfig.credit_multiplier.is_(None))
        ),
        # 排除负值（无量纲标记）
        (ModelConfig.credit_multiplier >= 0)
        | (ModelConfig.credit_multiplier.is_(None)),
    ).all()
    return {m.model_id for m in rows if not is_media_model(m.model_id)}


def _get_free_models_scoped(db: Session) -> set[tuple[str, str]]:
    """按平台区分的免费模型集合 `{(platform, model_id)}`。

    ## 为什么需要它

    `_get_free_models` 返回裸 `model_id`。多平台之后这是错的：
    `qwen3.8-flash` 在 Trae 免费（倍率 0），在 Loomy 无量纲（-1），
    只按 id 判会把 Loomy 那个也算成免费（实测发生过）。

    新代码应该用这个函数；`_get_free_models` 只留给尚未改造的调用方。
    """
    rows = db.query(ModelConfig).filter(
        ModelConfig.enabled == 1,
        (
            (ModelConfig.credit_multiplier == 0)
            | (ModelConfig.credit_multiplier.is_(None))
        ),
        (ModelConfig.credit_multiplier >= 0)
        | (ModelConfig.credit_multiplier.is_(None)),
    ).all()
    from admin.platform import registry

    return {
        ((m.platform or registry.DEFAULT_PLATFORM).lower(), m.model_id)
        for m in rows
        if not is_media_model(m.model_id)
    }


def _is_free_model(db: Session, platform: str, model_id: str) -> bool:
    """某个平台的某个模型是否免费（0 倍率或未同步）。"""
    from admin.platform import registry

    target = (platform or registry.DEFAULT_PLATFORM).lower()
    return (target, model_id) in _get_free_models_scoped(db)


def _is_model_allowed(db: Session, model_id: str) -> bool:
    """检查模型是否在白名单内。没有任何配置时放行全部（向后兼容）。"""
    configs = db.query(ModelConfig).all()
    if not configs:
        return True  # 未配置任何模型规则，放行
    enabled = {m.model_id for m in configs if m.enabled == 1}
    return model_id in enabled


def _official_order(db: Session, platform: str = "") -> list[str]:
    """某个平台的官方可见顺序（按官方排序的 model_id 列表）。

    ## 为什么按平台（这是个真 bug）

    原来存在**一个** SystemSetting key `model_official_order` 里。
    哪个平台最后同步，它的列表就覆盖全部 ——
    实测 Loomy 同步后，这个 key 变成 Loomy 的 9 个模型，
    于是判 WorkBuddy 的 40 个模型时「一个都不在 official 里」→ **全部隐藏**，
    前端显示 0 条。

    ## 存储格式

        {"workbuddy": [...], "trae": [...], "qoder": [...], ...}

    兼容旧的裸数组格式（迁移到 DEFAULT_PLATFORM）。
    """
    from admin.models import SystemSetting
    from admin.platform import registry

    row = db.get(SystemSetting, "model_official_order")
    try:
        value = json.loads(row.value) if row else {}
    except (ValueError, TypeError):
        value = {}

    # 旧格式：裸数组 —— 那是 WorkBuddy 的（当年唯一的来源）
    if isinstance(value, list):
        value = {registry.DEFAULT_PLATFORM: value}
    if not isinstance(value, dict):
        value = {}

    target = (platform or registry.DEFAULT_PLATFORM).strip().lower()
    items = value.get(target)
    if not isinstance(items, list):
        return []
    return [str(item) for item in items if isinstance(item, str) and item]


def _store_official_order(db: Session, official_ids: list[str],
                          platform: str = "") -> None:
    """按平台写入官方顺序（不覆盖别的平台）。"""
    from admin.models import SystemSetting
    from admin.platform import registry

    row = db.get(SystemSetting, "model_official_order")
    if row is None:
        row = SystemSetting(key="model_official_order")
        db.add(row)
    try:
        value = json.loads(row.value) if row.value else {}
    except (ValueError, TypeError):
        value = {}
    # 旧格式迁移
    if isinstance(value, list):
        value = {registry.DEFAULT_PLATFORM: value}
    if not isinstance(value, dict):
        value = {}

    target = (platform or registry.DEFAULT_PLATFORM).strip().lower()
    value[target] = list(official_ids or [])
    row.value = json.dumps(value, ensure_ascii=False)


def _billing_mode(model) -> str:
    """该模型用哪种计费口径。

    ## 三种口径

      * `upstream_usage`  媒体模型（图片/视频）—— 按上游 `usage.credit` 实际积分
      * `token_multiplier` 有倍率（> 0 收费 / == 0 免费）—— 本站按倍率定价
      * `upstream_token`   无倍率（< 0 或 NULL）—— 该平台按实际 token 计量

    ## 为什么按值判而不是按平台名

    原来写死 `platform == "trae"`，每加一个平台都要改这里。而实测：

        workbuddy  倍率 > 0        -> token_multiplier
        trae       无量纲(-1)      -> upstream_token
        qoder      有 price_factor -> token_multiplier
        loomy      无量纲(-1)      -> upstream_token

    按平台名判会把 Qoder 和 Loomy 归到同一个桶里 —— 它们计费方式不同。
    """
    if is_media_model(model.model_id):
        return "upstream_usage"
    multiplier = model.credit_multiplier
    if multiplier is None or multiplier < 0:
        return "upstream_token"
    return "token_multiplier"


#: 显示名末尾的倍率后缀：` x0.5`（半角空格 + x + 数字）
_NAME_SUFFIX_MULT_RE = re.compile(r"\s+[xX](\d+(?:\.\d+)?)\s*$")
#: 显示名末尾的括号（半角或全角）
_NAME_PAREN_RE = re.compile(r"^(.*?)\s*(?:[（(]([^（）()]*)[）)])\s*$")
#: 括号里恰好是 `x0.5`
_PAREN_MULT_RE = re.compile(r"^[xX](\d+(?:\.\d+)?)$")


def split_display_multiplier(value: str) -> tuple[str, str]:
    """从模型显示名里拆出 `(纯名称, 倍率字符串)`。

    照抄客户端 `splitModelDisplayName` 的逻辑（`app.asar` 里实测）：

        "DeepSeek V4.1 Flash(x2.0)"        -> ("DeepSeek V4.1 Flash", "2.0")
        "MiniMax M3 （x4.0）"               -> ("MiniMax M3", "4.0")
        "Spark-X2.6 正式版(限时免费)(x0.0)"  -> ("Spark-X2.6 正式版", "0.0")
        "Kimi k2.6 x6.5"                   -> ("Kimi k2.6", "6.5")

    ## 为什么要照抄而不自己写

    Loomy 的倍率格式不统一，实际情况包括：
      * 全角括号 `（）` 和半角 `()` 混用
      * 两层后缀：`(限时免费)(x0.0)` —— 徽标 + 倍率
      * `x` 前后空格不一致

    自己写正则很容易漏其中一种，把倍率当成名称的一部分。
    """
    original = str(value or "").strip()
    if not original:
        return "", ""
    name = original
    multiplier = ""
    # 客户端也是最多两轮
    for _ in range(2):
        match = _NAME_SUFFIX_MULT_RE.search(name)
        if match:
            multiplier = match.group(1)
            name = name[:match.start()].strip()
            continue
        parent = _NAME_PAREN_RE.match(name)
        if not parent:
            break
        next_name = (parent.group(1) or "").strip()
        next_badge = (parent.group(2) or "").strip()
        if not next_name or not next_badge:
            break
        name = next_name
        mult_match = _PAREN_MULT_RE.match(next_badge)
        if mult_match:
            multiplier = mult_match.group(1)
        # 非倍率括号（如「限时免费」）直接丢掉，继续看下一层
    return (name or original), multiplier


def _parse_multiplier(model: dict) -> tuple[float, str]:
    """从上游模型条目解析倍率，返回 `(倍率, 原始文本)`。

    ## 三种上游格式（实测）

        workbuddy  {"credits": "x0.50"}      -> 0.5
        trae       {"credits": "x0.79"}      -> 0.79
        qoder      {"price_factor": 0.5}     -> 0.5   数字，不是字符串

    ## 为什么必须分开处理

    原实现只读 `credits` 字符串。Qoder 的倍率在 `price_factor`（数字），
    于是全部被当成 0 -> **被判成免费**（实际 `dmodel` 是 0.5 倍率），
    看到的倍率列全是 0。

    ## 0 倍率才算免费（用户的规则）

      * `price_factor == 0` 且 `is_free` -> 真免费，返回 0.0
      * `price_factor > 0` -> 收费，返回该值
      * **没有倍率字段**（Loomy 纯计量）-> 返回 -1.0，
        代表「无倍率概念」，**不能当免费**

    返回 -1.0 而不是 0 是关键：0 会被 `credit_multiplier == 0` 的
    免费判定命中，把整平台的模型误标成免费。
    """
    # 1) 数字型倍率：price_factor（Qoder 用这个）
    #
    # ⚠️ 各平台把特有字段放在命名空间子对象里（`x_qoder` / `x_loomy`），
    # 因为这些字段不属于 OpenAI /v1/models 标准。
    # 解析时必须**同时看顶层和子对象** —— 只看顶层会漏掉全部平台的倍率。
    scopes = [model]
    for key, value in model.items():
        if isinstance(key, str) and key.startswith("x_") and isinstance(value, dict):
            scopes.append(value)

    for scope in scopes:
        for key in ("price_factor", "priceFactor", "original_price_factor"):
            value = scope.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                factor = float(value)
                return factor, ("%.2f×" % factor)

    # 2) WorkBuddy / Trae：credits 字符串（"x0.50"）
    raw = model.get("credits") or ""
    if raw:
        multiplier = credit_number(raw) or 0.0
        match = re.search(r"x\s*([0-9]+(?:\.[0-9]+)?)", str(raw))
        if match:
            try:
                multiplier = float(match.group(1))
            except (ValueError, TypeError):
                pass
        return multiplier, str(raw)

    # 3) 显示名后缀：`DeepSeek V4.1 Flash(x2.0)` / `MiniMax M3 （x4.0）`
    #
    # ⚠️ Loomy 就属于这种。它**没有独立的倍率字段**，
    # 把倍率拼在模型显示名末尾 ——
    # 我一开始只搜 `price_factor` / `multiplier` 等字段名，全都 miss，
    # 于是误判成「无倍率概念」。实际是 `name` 里带 `(x2.0)`。
    #
    # 显示名可能来自 `displayName` / `display_name` / `name`，
    # 倍率还可能被「徽标」挡在外面：`Spark-X2.6 正式版(限时免费)(x0.0)`。
    for key in ("displayName", "display_name", "name", "id"):
        text = model.get(key)
        if not isinstance(text, str) or not text.strip():
            continue
        _clean, found = split_display_multiplier(text)
        if found:
            try:
                return float(found), ("x%s" % found)
            except (ValueError, TypeError):
                continue

    # 4) 真的没有倍率信息
    return -1.0, ""


def _official_order_by_platform(db) -> dict:
    """每个平台自己的「官方可见顺序」。

    WorkBuddy 的 catalog 里带 `official` 子集（官方前端可见的模型，
    按官方顺序）。其它平台没有这个概念 —— 没有就**不放进字典**，
    调用方按「无排序信息 → 全部可见」处理。

    ## 为什么必须按平台

    `official_rank` 原来只有一份（由 WorkBuddy 的 catalog 生成）。
    选「WorkBuddy + 官方同显」时，40 个模型全被判成旧模型 → 显示 0 条。
    而 Qoder / Loomy 靠 `platform != DEFAULT` 恰好蒙对 true ——
    那是巧合，不是设计。
    """
    out: dict = {}
    try:
        from admin.models import SystemSetting
        from admin.platform import registry

        row = db.get(SystemSetting, "model_official_order")
        try:
            value = json.loads(row.value) if row else {}
        except (ValueError, TypeError):
            value = {}
        if isinstance(value, list):
            # 旧格式：全是 WorkBuddy 的
            value = {registry.DEFAULT_PLATFORM: value}
        if isinstance(value, dict):
            for pid, items in value.items():
                if isinstance(items, list) and items:
                    out[str(pid).lower()] = [str(i) for i in items if i]
    except Exception:  # noqa: BLE001 - 读不到就当没有
        pass
    return out


def _is_official_visible(model, rank_by_platform: dict) -> bool:
    """该模型在**它自己平台**的官方顺序里是否可见。

    三种情况：
      * 有官方顺序 → 看模型在不在里面
      * 没有官方顺序（rank 缺失）→ **视为可见**
        （Qoder / Loomy 没这个概念，不能因为缺数据就全隐藏）
    """
    from admin.platform import registry

    platform = (model.platform or registry.DEFAULT_PLATFORM).lower()
    ranks = rank_by_platform.get(platform)
    if ranks is None:
        return True
    return model.model_id in ranks


@router.get("/aggregate")
def list_aggregate(level: Optional[str] = None,
                   _: bool = Depends(require_admin),
                   db: Session = Depends(get_db)):
    """聚合模型列表：**同一模型跨平台合成一项**。

    ## 为什么需要

    多平台上线后，同一个模型在不同平台叫不同名字：

        DeepSeek-V4.1-Flash / deepseek-v4.1-flash / DeepSeek-V4-Flash
        GLM-5.3-Flash / gfmodel / glm-5.3-flash

    前端平铺显示用户根本看不出「这其实是同一个模型，可以跨平台调度」。
    这里按 `model_alias.canonical_key()` 归组，每项带**各平台明细**，
    前端悬停时展开。

    ## 返回

        {"items": [{key, name, platform_count, min_multiplier, free_any,
                    platforms: [{platform, model_id, multiplier, description,
                                 enabled, billing_mode}]}],
         "total_models", "total_groups", "multi_platform_groups"}
    """
    from admin.model_alias import aggregate_payload

    query = db.query(ModelConfig)
    if level:
        query = query.filter(ModelConfig.level == level)
    rows = query.order_by(ModelConfig.platform.asc(),
                          ModelConfig.model_id.asc()).all()

    payload_rows = []
    for model in rows:
        platform = (model.platform or registry.DEFAULT_PLATFORM).lower()
        # 展示名（Qoder 的 dfmodel -> DeepSeek-Flash）参与归一，否则
        # 内部代号模型永远单独成组，表现就是「同一模型没聚合」。
        from admin.model_alias import model_display

        payload_rows.append({
            "model_id": model.model_id,
            "platform": platform,
            "display_name": model_display(getattr(model, "upstream_metadata", None)),
            "credit_multiplier": model.credit_multiplier,
            "description": model.description or "",
            # `note` 里可能带「该账号未开通（上游 4023）」这类实测结论，
            # 一并透出，悬停时能直接看到为某个模型为什么调不通。
            "note": (getattr(model, "note", "") or ""),
            "enabled": bool(model.enabled),
            "billing_mode": _billing_mode(model),
        })
    result = aggregate_payload(payload_rows)
    # 哪些平台的模型 id 自带区后缀（能自己区分国内/国际）
    realm_aware = set()
    for row in payload_rows:
        mid = str(row.get("model_id") or "")
        if ":" in mid and mid.rsplit(":", 1)[-1].lower() in ("cn", "intl"):
            realm_aware.add(row["platform"])
    for item in result["items"]:
        _expand_realms(item, registry, realm_aware)
        for entry in item["platforms"]:
            entry["platform_label"] = _platform_label(
                entry.get("platform_id") or entry["platform"])
    # 展开区之后平台数会变（Qoder 国内 + 国际算两个可调度目标），要重算
    for item in result["items"]:
        item["platform_count"] = len(item["platforms"])
    result["multi_platform_groups"] = sum(
        1 for item in result["items"] if item["platform_count"] > 1)
    result["items"].sort(key=lambda item: (-item["platform_count"],
                                          (item.get("name") or "").lower()))
    return result


def _readable_client_id(row, platform_id: str) -> str:
    """给控制台用的「客户端调用名」：可读模型名 + 平台后缀。

    与 `/v1/models` 的命名规则一致：
        dfmodel (qoder)      -> deepseek-flash-qoder
        dfmodel (qoder-intl) -> deepseek-flash-qoder-intl
    已经是可读名的（`hy3` / `glm-5.3`）保持原样。
    """
    from admin.platform import models_router as mr
    from admin.platform import registry

    raw = str(getattr(row, "model_id", "") or "")
    storage = str(getattr(row, "platform", "") or "").lower()
    if not raw or not storage:
        return raw
    base = mr.readable_model_id(storage, raw)
    # 只有一个区的平台不加后缀（workbuddy / loomy），
    # 多区的按区加（qoder / qoder-intl / trae-cn / trae-intl）
    try:
        realms = mr._storage_realms().get(storage) or [""]
    except Exception:  # noqa: BLE001
        realms = [""]
    if len(realms) <= 1:
        return base
    pid = str(platform_id or "")
    return base if base.endswith("-" + pid) else "%s-%s" % (base, pid)


@router.get("/configs")
def list_configs(
    level: Optional[str] = None,
    platform: Optional[str] = None,
    _: bool = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """列出所有模型配置。

    **模型表保持公共一张表**，但按 `platform` 分组返回：
    平台一多，几十个模型混在一起根本分不清哪个模型属于哪个平台。
    响应的 `platforms` 给出分组汇总（每个平台多少个模型），
    前端直接照着渲染分组标题即可，不需要自己聚合。
    """
    q = db.query(ModelConfig)
    if level:
        q = q.filter(ModelConfig.level == level)
    if platform:
        q = q.filter(ModelConfig.platform == platform)
    rows = q.order_by(ModelConfig.id.asc()).all()
    from admin.platform import registry

    # 官方顺序**按平台**：WorkBuddy 的 catalog 只代表 WorkBuddy，
    # 拿它判 Qoder / Loomy 的模型等于用错尺子
    # （实测后果：选「WorkBuddy + 官方同显」时 40 个模型全被隐藏）。
    official_by_platform = _official_order_by_platform(db)
    official = official_by_platform.get(registry.DEFAULT_PLATFORM, [])
    official_rank_by_platform = {
        pid: {mid: rank for rank, mid in enumerate(ids)}
        for pid, ids in official_by_platform.items()
    }
    # ---- 按「storage + 区」展开 ----
    # Qoder / Trae 的国内版和国际版共用同一个 storage，库表里只有一份记录。
    # 直接返回原表 → 前端只看到 4 个平台、同模型也无法区分区
    #。
    # 这里把每条记录展开成「每个区一条」，`platform` 换成区级 id。
    from admin.platform.models_router import _entry_targets

    # 有 `:intl` 孪生说明该平台分两套模型表（Trae），否则共用一份（Qoder）
    _twins = {(str(r.platform or "").lower(), str(r.model_id or "")[:-5])
              for r in rows if str(r.model_id or "").endswith(":intl")}
    from admin.platform.models_router import _capabilities_of

    _caps_cache = {id(m): _capabilities_of(m) for m in rows}
    expanded: list[dict] = []
    for m in rows:
        storage = str(m.platform or registry.DEFAULT_PLATFORM).lower()
        mid = str(m.model_id or "")
        for pid, realm in _entry_targets({
                "platform": storage, "model_id": mid,
                "_has_intl_twin": (storage, mid) in _twins}):
            expanded.append({"row": m, "platform": pid, "realm": realm,
                             "caps": _caps_cache.get(id(m)) or {}})

    groups: dict[str, dict] = {}
    for _e in expanded:
        key = _e["platform"]
        m = _e["row"]
        bucket = groups.setdefault(key, {"platform": key, "count": 0, "enabled": 0,
                                         "label": _platform_label(key)})
        bucket["count"] += 1
        if m.enabled:
            bucket["enabled"] += 1

    return {
        "official_total": len(official),
        "platforms": sorted(groups.values(), key=lambda g: g["platform"]),
        "items": [
            {
                "id": m.id,
                "level": m.level,
                # 用**区级 id**（qoder / qoder-intl），而不是 storage；
                # 否则前端分组里国内版国际版混在一起、也无法区分。
                "platform": _e["platform"],
                "realm": _e["realm"],
                "platform_label": _platform_label(_e["platform"]),
                # 模型名用**可读名**（`deepseek-flash-qoder`），
                # 与 `/v1/models` 保持同一套命名 —— 否则控制台显示
                # `dfmodel-qoder`、客户端显示 `deepseek-flash-qoder`，
                # 两边对不上，用户不知道该复制哪个。
                "model_id": _readable_client_id(m, _e["platform"]),
                # 上游原始 id 单独给出，方便排障时对照
                "upstream_id": m.model_id,
                # 能力信息（上下文 / 输出 / 图片 / 推理）：
                # 之前只有 metadata 里藏着，前端拿不到，于是模型表格
                # 一律显示「256K / 32K / 纯文本」。
                "caps": _e.get("caps") or {},
                "enabled": bool(m.enabled),
                "note": m.note,
                "description": m.description or "",
                "metadata": _stored_metadata(m),
                "model_type": model_type({"model_id": m.model_id, "metadata": _stored_metadata(m)}),
                "tags": type_tags({"model_id": m.model_id, "metadata": _stored_metadata(m)}),
                # 计费口径：**按倍率值判断，不按平台名**。
                #
                #   media（图片/视频） -> `upstream_usage`（按上游 usage.credit 实际积分）
                #   倍率 > 0           -> `token_multiplier`（本站按倍率定价）
                #   倍率 == 0          -> `token_multiplier`（免费，仍属倍率体系）
                #   倍率 < 0 或 NULL   -> `upstream_token`（该平台按实际 token 计量，
                #                        没有倍率概念 —— Trae / Loomy 都是这种）
                #
                # 原来写死 `platform == "trae"`，新增平台就得改这里；
                # 而 Qoder 有真倍率（price_factor）、Loomy 没有 ——
                # 按平台名判会把两者都归错。
                "billing_mode": _billing_mode(m),
                # 倍率**原样输出**，不要用 `or 0`：
                #   > 0  -> 收费，前端显示 ×N
                #   = 0  -> 免费（FREE）
                #   < 0  -> 无量纲（该平台按 token 计量，没有倍率概念）
                #   None -> 尚未同步
                #
                # 原来 Trae 硬编码成 None、其它平台用 `or 0` ——
                # 后者把 Loomy 的 -1 抹成 0，前端就显示成 FREE（误述计费方式）。
                "credit_multiplier": m.credit_multiplier,
                "credits_raw": m.credits_raw or "",
                # 真免费 = 倍率恰好为 0
                "is_free": (not is_media_model(m.model_id)
                            and m.credit_multiplier == 0),
                # 上游套餐可用性（`plan_free`）——与 `is_free` **不是一回事**：
                #   `is_free`     = 本站不计费（倍率为 0），是**定价**属性；
                #   `plan_free`   = 上游免费账号能不能调，是**权限**属性。
                # Trae 的模型由客户端 `identity_list` 推导（见 desktop_catalog），
                # 其它平台没有这个概念，给 None 表示「不适用」。
                "plan_free": _stored_metadata(m).get("free"),
                # 视频模型的时长/分辨率/参考素材能力（来自上游目录 videoGeneration）
                "video_capability": (_stored_metadata(m).get("videoGeneration")
                                     if is_video_model({"model_id": m.model_id}) else None),
                # 官方前端可见性：official_order 为空（从未同步）时视为全部可见。
                #
                # ⚠️ `official` 是 **WorkBuddy 的官方模型名单**，只对
                # `platform='workbuddy'` 有意义。拿它去判 Trae 的模型，
                # 结果就是 9 个 Trae 模型（gpt-6.1-sol / gpt-6-astra …）全被
                # 标成「旧模型」并默认隐藏 —— 但它们明明能正常调用。
                # 因此非 workbuddy 平台一律视为可见。
                # 按**该模型所属平台**的官方顺序判定。
                # 该平台没有官方顺序数据时视为全部可见（而不是恰好「非默认平台」）。
                "official_visible": _is_official_visible(
                    m, official_rank_by_platform),
                "official_order": (official_rank_by_platform.get(
                    (m.platform or registry.DEFAULT_PLATFORM).lower(), {})
                    .get(m.model_id)),
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for _e in expanded
            for m in [_e["row"]]
        ]
    }


def _expand_realms(item: dict, registry, realm_aware: set[str]) -> None:
    """把「一个存储平台、多个区」拆成可读的多个调度目标。

    两种区信息来源必须都照顾到：

    · **模型 id 自带区后缀**（Trae：`xxx` = 国内、`xxx:intl` = 国际）
      -> 按后缀标成对应区的平台名，国内/国际各自独立成条且互不覆盖倍率。
    · **模型表共用一份**（Qoder：`dfmodel` 国内国际都叫这个）
      -> 一个条目展开成「中国版 / 国际版」两条。

    之前只处理了后者，导致 Trae 的两条都显示成 "Trae"；后来又误把
    `:intl` 的条目当成默认区条目，两行都变成 "Trae" 且倍率串了。
    """
    specs_by_storage: dict[str, list] = {}
    for spec in registry.all_platforms(enabled_only=False):
        specs_by_storage.setdefault(spec.storage(), []).append(spec)

    out: list[dict] = []
    for entry in item.get("platforms") or []:
        model_id = str(entry.get("model_id") or "")
        suffix = model_id.rsplit(":", 1)[-1].lower() if ":" in model_id else ""
        storage = str(entry.get("platform") or "")
        specs = specs_by_storage.get(storage) or []

        # 1) 带区后缀：直接定位到那个区
        if suffix in ("intl", "cn"):
            spec = next((sp for sp in specs if sp.realm == suffix), None)
            clone = dict(entry)
            clone["realm"] = suffix
            if spec is not None:
                clone["platform_id"] = spec.id
                clone["platform_label"] = spec.label
            out.append(clone)
            continue

        # 2) 不带后缀 + 该 storage 有多个区：这是默认区（国内版）的条目
        if len(specs) > 1 and storage not in realm_aware:
            for spec in specs:
                clone = dict(entry)
                clone["platform_id"] = spec.id
                clone["realm"] = spec.realm or ""
                clone["platform_label"] = spec.label
                out.append(clone)
            continue
        if len(specs) > 1:
            default_spec = next((sp for sp in specs if sp.realm == "cn"), specs[0])
            clone = dict(entry)
            clone["platform_id"] = default_spec.id
            clone["realm"] = default_spec.realm or "cn"
            clone["platform_label"] = default_spec.label
            out.append(clone)
            continue

        # 3) 单区 / 无区：原样
        out.append(entry)
    item["platforms"] = out


def _platform_label(value: str | None) -> str:
    """存储层平台 id -> 展示名。

    注意 `trae` 现在**不是**对外平台 id（已拆成 trae-cn / trae-intl），
    但它仍是 `model_configs.platform` 的落库值。必须显式映射，
    否则 registry 查不到就回落成 "WorkBuddy"，把 Trae 的模型标错平台。
    """
    from admin.platform import registry

    key = str(value or "").strip().lower()
    if key == "trae":
        return "Trae"
    spec = registry.get(key or registry.DEFAULT_PLATFORM)
    if spec:
        return spec.label
    return key or "WorkBuddy"


def _product_id_of(platform: str) -> str:
    """该平台的客户端 productId（`dynamic-text/<productId>.json` 的前缀）。

    目前只有 Qoder 有这套「内置文案」机制。其它平台返回空，
    `_description` 会直接走「由能力字段拼」那一级。
    """
    mapping = {"qoder": "qoder"}
    return mapping.get((platform or "").lower(), "")


def _description(model, platform: str = "", product_id: str = "") -> str:
    """模型描述（三级优先）。

    ## 1) dynamic-text（客户端内置文案）
    用户截图弹窗里那句「深度求索正式版模型（…），Agent 能力、世界知识
    与推理性能全面领先。」来自这里：

        <安装目录>/resources/dynamic-text/<productId>.json
        {"zh": {"model.<key>.description": "…"}}

    ## 2) 模型自带的 description
    catalog 里通常没有，但别家可能有。

    ## 3) 由能力字段拼
    上下文 / 是否推理 / 是否视觉 / 是否支持工具调用 —— 这是**参数**，
    不是文案，但比「暂未提供」有用。

    ⚠️ 顺序不能反：能力拼出来的东西会盖掉真正的营销文案。
    """
    if not isinstance(model, dict):
        return ""

    # 1) dynamic-text
    if product_id:
        try:
            from admin import dynamic_text

            key = str(model.get("id") or model.get("key") or "")
            text = dynamic_text.model_description(key, product_id=product_id)
            if not text:
                # 有些平台把 key 放在命名空间里
                for name, value in model.items():
                    if (isinstance(name, str) and name.startswith("x_")
                            and isinstance(value, dict)):
                        inner = str(value.get("key") or value.get("model_id") or "")
                        if inner:
                            text = dynamic_text.model_description(
                                inner, product_id=product_id)
                            if text:
                                break
            if text:
                return text
        except Exception:  # noqa: BLE001 - 读不到就走下一级
            pass

    # 2)(3) 模型自带描述 -> 能力拼
    from admin.model_desc import describe_model

    return describe_model(model)

def _stored_metadata(row):
    try:
        value = json.loads(row.upstream_metadata or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


_description_checked = {}


#: 描述缓存有效期（秒）
_DESCRIPTION_TTL = 300
#: 正在刷新的 scope，避免并发重复拉取
_description_refreshing: set = set()


def _spawn_description_refresh(scope) -> None:
    """后台线程刷新模型描述（不阻塞 HTTP 请求）。"""
    if scope in _description_refreshing:
        return
    _description_refreshing.add(scope)

    def worker():
        try:
            from admin.db import SessionLocal

            with SessionLocal() as db:
                with _sync_lock:
                    if (time.monotonic()
                            - _description_checked.get(scope, -float("inf"))
                            < _DESCRIPTION_TTL):
                        return
                    catalog = _fetch_catalog(db)
                    descriptions = {m.get("id"): m
                                    for m in catalog.get("models") or []}
                    # **必须按平台匹配模型**：`deepseek-v4.1-flash` 这种 ID
                    # 在 trae 和 workbuddy 都存在（实测 13 个同名 ID）。
                    # 以前这里只按 model_id 查，WorkBuddy 目录里的价格会把
                    # Trae 的倍率覆盖掉（用户手填的 0.12 变成 0.11）。
                    for row in db.query(ModelConfig).all():
                        model = descriptions.get(row.model_id)
                        if not model:
                            continue
                        owner = str(model.get("platform") or "").lower()
                        if owner and owner != str(row.platform or "").lower():
                            continue
                        row.description = _description(model)
                        row.upstream_metadata = json.dumps(
                            metadata(model), ensure_ascii=False)
                        # 倍率只在库里没设过时才写入，**不覆盖用户手填的值**
                        price = credit_number(model.get("credits"))
                        if price is not None and row.credit_multiplier in (None, 0):
                            row.credit_multiplier = price
                            row.credits_raw = str(model["credits"])
                    db.commit()
                    _description_checked[scope] = time.monotonic()
                    if len(_description_checked) > 128:
                        _description_checked.pop(next(iter(_description_checked)))
        except Exception:  # noqa: BLE001 - 后台失败不影响请求
            logger.exception("后台刷新模型描述失败")
        finally:
            _description_refreshing.discard(scope)

    threading.Thread(target=worker, name="model-desc-refresh",
                     daemon=True).start()


@router.get("/descriptions")
def model_descriptions(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """返回模型描述与元数据。

    **这个接口绝不阻塞在网络请求上。** 以前它每次进来都要 `_fetch_catalog()`
    拉一遍上游目录，实测 1434ms —— 而调用方只是要「读描述」。
    现在：数据过期就把刷新丢到后台线程，本次仍返回库里现成的内容。
    """
    candidates = scope_accounts(db.query(Account)).filter(Account.status == "active").order_by(Account.id).all()
    scope = tuple((a.id, a.uid) for a in candidates)

    stale = (time.monotonic() - _description_checked.get(scope, -float("inf"))
             >= _DESCRIPTION_TTL)
    if stale:
        _spawn_description_refresh(scope)

    return {"items": [{"model_id": m.model_id, "description": m.description or "",
                       "metadata": _stored_metadata(m), "credit_multiplier": m.credit_multiplier,
                       "credits_raw": m.credits_raw}
                      for m in db.query(ModelConfig).all()]}


#: 忙闲等级缓存（秒）：`get_busy_levels` 会探上游容量，实测约 1.2 秒。
_busy_cache: dict = {"at": 0.0, "value": None}
_BUSY_TTL = 30.0


@router.get("/busy")
def model_busy(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """上游忙闲等级（独立于本地启用状态与冷却）。

    忙闲是分钟级变化的指标，不必每次请求都重算。30 秒缓存。
    """
    from admin.model_busy import get_busy_levels, LABELS

    now = time.monotonic()
    cached = _busy_cache["value"]
    if cached is not None and now - _busy_cache["at"] < _BUSY_TTL:
        return cached

    models = [row.model_id for row in db.query(ModelConfig.model_id).distinct().all()]
    result = get_busy_levels(db, models)
    payload = {"items": [{"model_id": model, "busy_level": level,
                          "busy_label": LABELS[level]}
                         for model, level in result["levels"].items()],
               "observed_at": result["observed_at"], "stale": result["stale"]}
    _busy_cache.update({"at": now, "value": payload})
    return payload


def _do_sync_models(db: Session, account_id: int | None = None,
                    platform: str = "") -> dict:
    with _sync_lock:
        return _sync_models_locked(db, account_id, platform)


def _catalog_of(acc, platform: str, db) -> dict:
    """取某个账号所属平台的模型目录。

    ## 为什么必须按平台分派

    原来这里只有一条路：`backend.AccountSession` —— 那是 **WorkBuddy 的实现**。
    后果：`model_configs` 表里只有 `trae`(70) 和 `workbuddy`(40)，
    **Qoder / Loomy 的模型一条都进不来**，前端「模型配置」只显示两个平台。

    ## 两条路

      * `workbuddy` → 保留原实现（返回 WorkBuddy 特有结构，含官方可见子集）
      * 其它平台   → 走 `Gateway.list_models()`，已是标准 `/v1/models` 形状
    """
    if platform in ("", "workbuddy"):
        from admin import backend

        original = acc.auth_json
        with backend.AccountSession(original) as sess:
            try:
                data = sess.fetch_models_catalog()
            finally:
                updated_auth = sess.updated_json()
                db.query(Account).filter(
                    Account.id == acc.id,
                    Account.auth_json == original).update(
                    {Account.auth_json: updated_auth}, synchronize_session=False)
                db.commit()
        return _normalize_catalog(data)

    spec = _spec_for_storage(platform)
    if spec is None:
        # 平台注册表没加载 / 该 storage 没有对应平台时，**必须带原因**返回：
        # 静默返回空清单会让调用方以为「上游没给模型」，而真正的原因是
        # 装配缺失 —— 实测过 4 个平台全 0 条且无任何报错，极难排查。
        return {"models": [], "official": [],
                "reason": "平台 %r 未登记（注册表里没有它的 storage），"
                          "请确认 admin.platform.loader.load_platforms() 已执行"
                          % (platform or "?")}
    gateway = registry.load_platform_gateway(spec, spec.realm)
    if gateway is None or not callable(getattr(gateway, "list_models", None)):
        return {"models": [], "official": [],
                "reason": "平台 %s 没有提供 list_models（%s）"
                          % (spec.id, getattr(spec, "platform_gateway", "") or "-")}

    # `list_models` 需要 Request 来读平台/区域头。
    # 构造最小 scope —— 同进程内调用，不走网络。
    from starlette.requests import Request

    from admin.platform.shared import INTERNAL_KEY

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/models",
        "headers": [(b"x-platform", (spec.id or platform).encode())],
        "query_string": b"",
        # 内部调用标记：让平台的 `_auth()` 放行（见 shared.internal 的说明）。
        # 没有它，非默认平台的模型同步永远 401 —— 实测 workbuddy 拿到 36 个
        # 模型，trae / qoder / loomy 一律 0 个且不报错。
        INTERNAL_KEY: True,
    }
    response = _call_maybe_async(gateway.list_models, Request(scope))
    payload = _response_json(response)
    # 平台自己的说明（「号池没有可用 X 账号，仅返回配置目录」这类）带出去，
    # 同步结果里能直接看出「0 条到底是上游没给，还是没号」。
    status = getattr(response, "status_code", 200)
    models = payload.get("data") or payload.get("models") or []
    out = _normalize_catalog({"models": models})
    if not out["models"]:
        out["reason"] = str(payload.get("note")
                            or payload.get("detail")
                            or ("上游返回 HTTP %s 且没有模型" % status))[:200]
    elif payload.get("note"):
        out["reason"] = str(payload.get("note"))[:200]
    return out


def _call_maybe_async(fn, *args):
    """调用可能是协程的函数，返回结果。

    `Gateway.list_models` 在各平台实现里是 `async def`（要读 request.json
    或做上游 IO），而 `sync_models` 是同步的。两种上下文都要能跑：

      * 无事件循环（调度器 / 后台线程）→ `asyncio.run()`
      * **已有**事件循环（async handler 里调进来）→ 新线程里 run，
        否则 `asyncio.run()` 会抛
        "cannot be called from a running event loop"
    """
    result = fn(*args)
    if not inspect.isawaitable(result):
        return result

    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 没有运行中的循环 —— 直接跑
        return asyncio.run(result)

    # 已有循环：放到新线程里跑，避免嵌套
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, result).result(timeout=120)


def _spec_for_storage(storage: str):
    """按**落库平台名**找 PlatformSpec（不按对外 id）。

    Trae / Qoder 都是「一个落库平台 + 多个对外 id」，
    所以必须比对 `spec.storage()` 而不是 `spec.id`。

    ⚠️ 查不到时要**先补一次装配再查**：`admin/server.py` 在 import 时会调
    `load_platforms()`，但调度器 / CLI / 测试可能跑在它之前。那种情况下
    注册表里只有 `workbuddy`（非空，所以「空表才加载」的判断不成立），
    其余平台一律被判成「未登记」，模型同步静默返回 0 条 ——
    这个失败方式极难查，实测在 2026-10-31 就踩过。
    """

    def find(name: str):
        for pid in registry.ids():
            item = registry.get(pid)
            if item is not None and item.storage() == name:
                return item
        return None

    hit = find(storage)
    if hit is not None:
        return hit
    try:
        from admin.platform.loader import load_platforms

        load_platforms()
    except Exception:  # noqa: BLE001 - 装配失败就返回 None，由调用方报原因
        return None
    return find(storage)


def _response_json(response) -> dict:
    """从 JSONResponse 里取 dict（同进程，不走网络）。"""
    body = getattr(response, "body", None)
    if not body:
        return {}
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_catalog(data) -> dict:
    """归一模型清单。

    两个来源结构不同：
      * WorkBuddy 的 `fetch_models_catalog` 已返回 `{"models", "official"}`
      * 网关 `list_models` 返回 `{"object":"list","data":[...]}`
    """
    if not isinstance(data, dict):
        return {"models": [], "official": []}
    models = data.get("models")
    if models is None:
        models = data.get("data") or data.get("items") or []
    official = data.get("official")
    if official is None:
        # 没有「官方子集」概念时全部视为可见
        official = list(models)
    return {"models": list(models or []), "official": list(official or [])}


def _catalog_for_platform(db, storage: str) -> dict:
    """同步**一个落库平台**的模型目录。

    与 `_fetch_catalog` 的区别（这是多平台下的关键）：
    `_fetch_catalog` 是「按余额高低随便找个能用的号拉一份目录」，
    拿到第一份就 `break` —— 于是「同步模型」实际只覆盖了余额最高的
    那一个平台，其余平台永远是空的。用户切到 Qoder / Loomy 再看，
    就是一片空白（这条路径在 2026-10-31 实测复现）。

    ## 为什么会话必须分开处理（这里是踩过的坑）

    平台自己的 `list_models()` **内部会再选一次号**（`shared.select_account`）。
    如果外层先 `account_slot(acc)` 把这个号占住，内层就选不到它 ——
    一个账号都没有时直接返回空目录。Loomy 只有 1 个号，
    所以它必然失败；Qoder / Trae 有多个号，侥幸轮到下一个才通过。
    实测：`account_slot` 内调 `select_account` 返回 `None`。

    所以：
      * 默认平台（workbuddy）走 `AccountSession`，**必须**占号 —— 它要那个号的 token；
      * 其它平台**不占号**，把它交给平台网关自己选（它有完整的
        冷却 / 在途 / 绑定判断，比这里更准）。
    """
    query = (db.query(Account)
             .filter(Account.status == "active", Account.platform == storage)
             .order_by(Account.balance_remain.desc(), Account.id.asc()))
    rows = query.all()
    if not rows:
        return {"models": [], "official": [], "platform": storage,
                "reason": "该平台没有可用（active）账号"}

    if storage not in ("", registry.DEFAULT_PLATFORM):
        # 非默认平台：直接问平台网关，**不占号**（避免与它内部的选号自锁）。
        try:
            catalog = _catalog_of(rows[0], storage, db)
        except Exception as error:  # noqa: BLE001
            db.rollback()
            return {"models": [], "official": [], "platform": storage,
                    "reason": "%s: %s" % (type(error).__name__,
                                          str(error)[:160])}
        catalog["platform"] = storage
        return catalog

    last_error = None
    for acc in rows:
        try:
            from admin import maintenance

            with maintenance.account_slot(acc, db) as reason:
                if reason:
                    last_error = "账号忙碌/受保护：%s" % reason
                    continue
                catalog = _catalog_of(acc, storage, db)
                if catalog.get("models"):
                    catalog["platform"] = storage
                    return catalog
                last_error = catalog.get("reason") or "平台 %s 未返回模型" % storage
        except Exception as error:  # noqa: BLE001 - 单号失败换下一个
            db.rollback()
            try:
                maintenance.note_failure(db, acc, error)
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
            last_error = "%s: %s" % (type(error).__name__, str(error)[:140])
    return {"models": [], "official": [], "platform": storage,
            "reason": str(last_error or "该平台没有可用账号接手")[:200],
            "tried": tried}


def _catalog_platforms(db) -> list[str]:
    """需要同步的全部**落库平台**（去重，默认平台在前）。

    `trae-cn` / `trae-intl` 共用 storage `trae`，只同步一次；
    某个平台从来没登记过账号的，也照样列出来 —— 让同步结果里能看到
    「这个平台没有账号」，而不是它悄悄消失。
    """
    out: list[str] = []
    default = registry.DEFAULT_PLATFORM
    if default:
        out.append(default)
    for pid in registry.ids():
        item = registry.get(pid)
        storage = (item.storage() if item is not None else "") or ""
        if storage and storage not in out:
            out.append(storage)
    for (storage,) in (db.query(Account.platform).distinct().all()):
        key = (storage or "").strip().lower()
        if key and key not in out:
            out.append(key)
    return out


def _fetch_catalog(db, account_id=None, platform: str = "") -> dict:
    """取模型目录。

    `platform` 非空：只取这一个落库平台（前端按平台同步时用）。
    `platform` 为空：**遍历所有平台**，每个平台各拉一份并合并。

    返回形状统一为 `{"bundles": [每平台一份], "problems": {平台: 原因}}`。
    `bundles` 才是 `_sync_models_locked` 真正消费的字段；
    `models` / `official` 是各平台合并后的扁平清单（兼容旧调用方）。

    `account_id` 非空时保持原语义：只按这一个账号取（手工排障用）。
    """
    if account_id is not None:
        acc = db.query(Account).filter(Account.id == account_id).first()
        if acc is None:
            raise HTTPException(status_code=404, detail="账号不存在")
        storage = (getattr(acc, "platform", "") or "").strip().lower() \
            or registry.DEFAULT_PLATFORM
        one = _catalog_of(acc, storage, db)
        one["platform"] = storage
        return {"bundles": [one], "models": one.get("models") or [],
                "official": one.get("official") or [], "platform": storage}

    targets = [platform] if platform else _catalog_platforms(db)
    bundles: list[dict] = []
    problems: dict[str, str] = {}
    for storage in targets:
        got = _catalog_for_platform(db, storage)
        if got.get("models"):
            bundles.append(got)
        else:
            problems[storage] = str(got.get("reason") or "未返回模型")[:200]
    if not bundles:
        raise HTTPException(
            status_code=502,
            detail="所有平台都没取到模型目录：" + "；".join(
                "%s=%s" % (k, v) for k, v in problems.items())[:400])
    return {"bundles": bundles, "problems": problems,
            "models": [m for b in bundles for m in (b.get("models") or [])],
            "official": [m for b in bundles for m in (b.get("official") or [])]}


def _sync_one_platform(db: Session, bundle: dict) -> dict:
    """把**一个平台**的目录 upsert 进 model_configs。

    按 `(platform, model_id)` 去重，不是只按 `model_id` ——
    不同平台有同名模型（DeepSeek-V4.1-Flash 在三个平台都有），
    只按 model_id 会互相覆盖，插入时还会撞 uq_model_platform。
    """
    models_raw = bundle.get("models") or []
    target_platform = str(bundle.get("platform")
                          or registry.DEFAULT_PLATFORM).strip().lower()

    added = 0
    updated = 0
    # ⚠️ 去重键必须**小写**：唯一键 `uq_model_platform` 在 MySQL 的
    # `utf8mb4_*_ci` 排序规则下是**大小写不敏感**的，而 Trae 的上游
    # 同时给出 `DeepSeek-V4.1-Flash` 和 `deepseek-v4.1-flash` ——
    # 用区分大小写的集合去重时两条都当成新记录，INSERT 直接撞
    # `(1062) Duplicate entry 'trae-deepseek-v4.1-flash-system'`，
    # 整个同步失败、一条都进不来（2026-10-31 实测）。
    # 归一成小写后与数据库的判定口径一致。
    def _key(plat: str, mid: str) -> tuple[str, str]:
        return (str(plat or "").lower(), str(mid or "").lower())

    seen = {_key(p, m) for p, m in db.query(ModelConfig.platform,
                                            ModelConfig.model_id).all()}
    for m in models_raw:
        mid = m.get("id")
        if not mid:
            continue
        # ⚠️ 只跳过**我们自己注入的** auto。
        #
        # 原实现无条件跳过 `auto`，理由是「我们系统有自己的免费优先 auto」——
        # 那是主应用（默认平台）的机制。但某些平台的 `auto` 是**上游真实模型**
        # （有自己的 key / 展示名 / 倍率），跳过后模型列表就少一个，
        # 用户对照客户端会发现「列表不对」。
        #
        # 判据：默认平台的 auto 是我们注入的；其它平台的 auto 是上游的。
        if (mid.lower() == "auto"
                and target_platform == registry.DEFAULT_PLATFORM):
            continue
        # 解析积分倍率（三种上游格式见 _parse_multiplier）
        multiplier, raw_credits = _parse_multiplier(m)
        if _key(target_platform, mid) in seen:
            # 已存在：覆盖倍率 / 原始 credits / 备注（保留用户启停状态）
            # 同 platform 下大小写不同的写法也归到**库里那条**上，
            # 否则会一直尝试插入并一直撞唯一键。
            mc = db.query(ModelConfig).filter(
                ModelConfig.platform == target_platform,
                func.lower(ModelConfig.model_id) == str(mid).lower()).first()
            if mc:
                mc.credit_multiplier = multiplier
                mc.credits_raw = str(raw_credits)
                mc.description = _description(
                    m, target_platform, _product_id_of(target_platform))
                mc.upstream_metadata = json.dumps(metadata(m), ensure_ascii=False)
                if not mc.note:
                    mc.note = m.get("name") or ""
                updated += 1
            continue
        seen.add(_key(target_platform, mid))
        mc = ModelConfig(
            level="system",
            # ⚠️ 必须显式带 platform。不传会用列默认值（workbuddy），
            # 导致 Qoder / Loomy 的模型被记成 WorkBuddy 的，
            # 并撞上 uq_model_platform 唯一键。
            platform=target_platform,
            model_id=mid,
            enabled=1,
            note=m.get("name") or "",
            credit_multiplier=multiplier,
            credits_raw=str(raw_credits),
            description=_description(
                m, target_platform, _product_id_of(target_platform)),
            upstream_metadata=json.dumps(metadata(m), ensure_ascii=False),
        )
        db.add(mc)
        added += 1
    db.commit()
    return {"platform": target_platform, "fetched": len(models_raw),
            "added": added, "updated": updated}


def _sync_models_locked(db: Session, account_id: int | None = None,
                        platform: str = "") -> dict:
    """从后端拉取可用模型列表并同步到本地数据库（upsert）。

    需要有效的 active 账号；余额为 0 也允许查询模型目录。

    **逐个平台同步**：`_fetch_catalog` 现在返回 `bundles`（每平台一份），
    不再只取第一个能出模型的平台 —— 否则切到其它平台看模型配置是空的。
    `platform` 非空时只同步这一个落库平台。
    """
    catalog = _fetch_catalog(db, account_id, platform)
    bundles = catalog.get("bundles") or []
    per: dict[str, dict] = {}
    added = updated = fetched = 0
    for bundle in bundles:
        stats = _sync_one_platform(db, bundle)
        per[stats["platform"]] = stats
        added += stats["added"]
        updated += stats["updated"]
        fetched += stats["fetched"]
        official = [m.get("id") for m in (bundle.get("official") or [])
                    if isinstance(m, dict) and m.get("id")]
        _store_official_order(db, official, stats["platform"])

    total = db.query(ModelConfig).count()
    enabled_count = db.query(ModelConfig).filter(ModelConfig.enabled == 1).count()
    return {
        "total_fetched": fetched,
        "added": added,
        "updated": updated,
        "total_in_db": total,
        "enabled": enabled_count,
        # 每平台明细 + 没取到的平台及原因（前端直接展示，
        # 不再出现「同步完成但某个平台还是空的」这种无解释状态）
        "platforms": per,
        "problems": catalog.get("problems") or {},
    }


@router.post("/sync")
def sync_models(platform: str = "",
                _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """从后端拉取可用模型列表并同步到本地数据库（upsert）。

    `?platform=qoder` 只同步这一个**落库平台**；不传则所有平台各同步一份。
    """
    from admin.platform import scope as platform_scope

    storage = ""
    if platform:
        storage = platform_scope.parse(platform)[0] or platform.strip().lower()
    result = _do_sync_models(db, None, storage)
    _invalidate_routing_cache()
    return result


@router.patch("/{config_id}")
def toggle_model(
    config_id: int,
    body: ModelToggleIn,
    _: bool = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """启用/禁用某个模型。"""
    mc = db.query(ModelConfig).filter(ModelConfig.id == config_id).first()
    if not mc:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    mc.enabled = 1 if body.enabled else 0
    if body.enabled and mc.note:
        pass  # keep existing note
    db.commit()
    _invalidate_routing_cache()
    return {"id": mc.id, "model_id": mc.model_id, "enabled": bool(mc.enabled)}


@router.post("/batch-toggle")
def batch_toggle(
    body: ModelBatchIn,
    _: bool = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """批量启用/禁用模型。"""
    if body.level not in ("system", "user"):
        raise HTTPException(400, "无效模型级别")
    query = db.query(ModelConfig).filter(ModelConfig.level == body.level)
    if body.model_ids:
        query = query.filter(ModelConfig.model_id.in_(body.model_ids))
    count = query.update({ModelConfig.enabled: int(body.enabled)}, synchronize_session=False)
    db.commit()
    _invalidate_routing_cache()
    return {"updated": count, "enabled": body.enabled}


@router.delete("/{config_id}")
def delete_config(
    config_id: int,
    _: bool = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """删除某条模型配置（从白名单移除 = 禁用该模型）。"""
    mc = db.query(ModelConfig).filter(ModelConfig.id == config_id).first()
    if not mc:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    mid = mc.model_id
    db.delete(mc)
    db.commit()
    _invalidate_routing_cache()
    return {"id": config_id, "model_id": mid, "ok": True}


# ---------------------------------------------------------------------------
# 测速：用账号池直接对上游发请求，免 API Key
# ---------------------------------------------------------------------------

_SPEED_TEST_PROMPT = [{"role": "user", "content": "用一句话介绍一下你自己。"}]


@router.post("/speed-test")
async def speed_test(
    body: dict,
    request: Request,
    _: bool = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not _probe_lock.acquire(blocking=False):
        raise HTTPException(429, "已有模型检测正在进行", headers={"Retry-After": "3"})
    try:
        return await _speed_test_run(body, request, db)
    finally:
        _probe_lock.release()


async def _speed_test_run(body, request, db):
    """A real, bounded model request; upstream may charge credits."""
    model = str(body.get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="缺少 model")
    if is_video_model(model):
        raise HTTPException(400, "视频模型请通过 /v1/videos 调用，不支持文本测速")
    if is_media_model(model):
        raise HTTPException(400, "图片模型请通过生图接口调用，不支持文本测速")
    try:
        max_tokens = max(16, min(512, int(body.get("max_tokens") or 64)))
    except (TypeError, ValueError):
        max_tokens = 64
    messages = body.get("messages") or _SPEED_TEST_PROMPT

    if not _is_model_allowed(db, model):
        raise HTTPException(400, "模型未启用")
    from admin.routers.proxy import (_blocking_call, _select_account, _classify_error,
                                     _apply_account_policy, _parse_usage, _looks_like_inband_error,
                                     _error_reset_at)
    from admin.pool import POOL
    if POOL.waf.active():
        raise HTTPException(503, "出口保护中，稍后检测", headers={"Retry-After": str(max(1, POOL.waf.remaining()))})
    acc = await _blocking_call(_select_account, db, model=model, mark_picked=False)
    if not acc or not POOL.acquire(acc.uid or ""):
        raise HTTPException(503, "当前没有可检测账号（冷却或正在使用），稍后重试", headers={"Retry-After": "3"})
    from admin import upstream_http
    sess = None
    try:
        sess = await _blocking_call(backend.AccountSession, acc.auth_json, on_cancel=lambda session: session.close())
        headers = await _blocking_call(sess.get_headers)
    except BaseException:
        if sess is not None:
            sess.close()
        POOL.release(acc.uid or "")
        raise
    url = f"{settings.BACKEND}/v2/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": max_tokens,
    }

    t0 = time.perf_counter()
    ttft = None
    completion_tokens = None
    total_tokens = None
    prompt_tokens = None
    sample = ""
    sample_len = 0
    intervals = []
    previous_delta = None
    headers_ms = None
    completed = False
    raw_lines = []
    failure_kind = "transport"

    def _persist():
        try:
            acc.auth_json = sess.updated_json()  # 写回可能刷新的 token
            from admin.models import UsageLog
            usage = _parse_usage('\n'.join(raw_lines)) if completed else {}
            db.add(UsageLog(account_id=acc.id, model=model, api_key_id=0, use_case="model-probe",
                            error_kind="success" if completed else failure_kind,
                            ttfb_ms=round(ttft * 1000) if ttft is not None else None,
                            latency_ms=round((time.perf_counter() - t0) * 1000),
                            credits=usage.get('credits') or 0, prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens, total_tokens=total_tokens))
            db.commit()
        except Exception:
            db.rollback()

    try:
        async with upstream_http.client(request, 120, backend.HTTP_LIMITS) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as r:
                headers_ms = round((time.perf_counter() - t0) * 1000, 1)
                if r.status_code >= 400:
                    detail = await r.aread()
                    msg = detail.decode("utf-8", "replace")[:300]
                    failure_kind = _classify_error(r.status_code, msg)
                    await _blocking_call(_apply_account_policy, db, acc, failure_kind, r.status_code, msg,
                                         model=model, reset_at=_error_reset_at(r.headers, msg))
                    raise HTTPException(status_code=r.status_code, detail=f"上游返回错误: {msg}")
                async for chunk in r.aiter_lines():
                    # TTFT：首个非空增量（content 或 reasoning_content）即首字延迟
                    for line in chunk.splitlines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        p = line[5:].strip()
                        if p == "[DONE]":
                            completed = True
                            continue
                        if not p:
                            continue
                        raw_lines.append('data: ' + p)
                        if _looks_like_inband_error(p):
                            failure_kind = _classify_error(200, p)
                            await _blocking_call(_apply_account_policy, db, acc, failure_kind, 200, p, model=model,
                                                 reset_at=_error_reset_at(r.headers, p))
                            raise HTTPException(502, "上游模型返回错误，请查看账号模型冷却状态")
                        try:
                            obj = json.loads(p)
                        except Exception:
                            continue
                        usage = obj.get("usage")
                        if isinstance(usage, dict):
                            if usage.get("prompt_tokens") is not None:
                                prompt_tokens = usage["prompt_tokens"]
                            if usage.get("completion_tokens") is not None:
                                completion_tokens = usage["completion_tokens"]
                            if usage.get("total_tokens") is not None:
                                total_tokens = usage["total_tokens"]
                        for ch in obj.get("choices") or []:
                            if ch.get('finish_reason') is not None:
                                completed = True
                            delta = ch.get("delta") or {}
                            # 推理类模型把思考内容放在 reasoning_content（content 可能为空），
                            # 样例优先取 content，缺省回退 reasoning_content，保证总能看到输出。
                            piece = (delta.get("content") or "") or (delta.get("reasoning_content") or "")
                            if piece:
                                now = time.perf_counter()
                                if ttft is None:
                                    ttft = now - t0
                                if previous_delta is not None:
                                    intervals.append((now - previous_delta) * 1000)
                                previous_delta = now
                            if piece and sample_len < 400:
                                add = piece[:400 - sample_len]
                                sample += add
                                sample_len += len(add)
            if ttft is None or not completed:
                completed = False
                raise HTTPException(502, "测速流未完整结束或未返回有效文字")
            t_end = time.perf_counter()
    except HTTPException:
        completed = False
        raise
    except Exception as e:
        completed = False
        raise HTTPException(status_code=502, detail=f"测速请求失败: {e}")
    except BaseException:
        completed = False
        failure_kind = "cancelled"
        raise
    finally:
        import anyio
        with anyio.CancelScope(shield=True):
            try:
                await _blocking_call(_persist)
            finally:
                sess.close()
                POOL.release(acc.uid or "")
        from admin.post_login import enqueue_resources
        enqueue_resources(acc.id)

    total_dur = t_end - t0
    gen_dur = (total_dur - ttft) if ttft is not None else total_dur
    gen_dur = max(gen_dur, 0.0)
    speed_tps = round(completion_tokens / gen_dur, 2) if completion_tokens and gen_dur > 0 else None
    overall_tps = round(completion_tokens / total_dur, 2) if completion_tokens and total_dur > 0 else None
    ttft_ms = round(ttft * 1000, 1) if ttft is not None else None

    meta = backend.parse_auth_meta(acc.auth_json)
    return {
        "model": model,
        "health": "healthy",
        "observed_at": datetime.utcnow().isoformat() + 'Z',
        "headers_ms": headers_ms,
        "inter_delta_jitter_ms": round(statistics.pstdev(intervals), 1) if len(intervals) >= 2 else None,
        "jitter_samples": len(intervals),
        "jitter_scope": "inter_delta_including_generation",
        "prompt_tokens": prompt_tokens,
        "account": meta.get("nickname") or meta.get("uid") or "",
        "ttft_ms": ttft_ms,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "gen_duration_ms": round(gen_dur * 1000, 1),
        "total_duration_ms": round(total_dur * 1000, 1),
        "speed_tps": speed_tps,
        "overall_tps": overall_tps,
        "sample": sample,
    }
