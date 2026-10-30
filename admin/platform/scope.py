"""平台作用域：让「切换平台」成为**全局数据上下文**，而不是账号面板的局部筛选。

问题背景：切换平台之后，使用记录、用量日志、积分到期这些
数据仍然是全平台混算的 —— 平台越多，数字越没法看。

因此约定：**控制台在每次请求上带 `X-Platform`，后端据此收窄所有统计数据**。

    X-Platform: trae        -> 只看 trae（两个区都算）
    X-Platform: trae:cn     -> 只看 trae 国内版
    X-Platform: workbuddy   -> 只看 workbuddy
    缺省 / 空               -> 不过滤（保持老 API 调用方的行为）

`usage_logs` 没有 platform 列（它是按账号记账的），所以统一走
「先解析出该平台/该区的账号 id 集合，再 `account_id IN (...)`」。
这样不需要给历史数据做任何迁移，也不可能出现「日志行没打平台标记」的脏数据。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform import registry

#: 空串表示「不过滤」，用于区分「没指定平台」与「指定了一个查不到的平台」。
ALL = ""


def parse(value: str | None) -> tuple[str, str]:
    """把请求里的平台标识解析成 `(storage_platform, realm)`。

    返回的**第一个值是落库平台**（`accounts.platform`），不是对外 id ——
    因为后续所有查询都按它过滤。`trae-cn` 与 `trae-intl` 都映射到
    `("trae", "cn"|"intl")`。

        `trae-cn`   -> ("trae", "cn")    国内版（固定区域）
        `trae-intl` -> ("trae", "intl")  国际版（固定区域）
        `trae`      -> ("trae", "")      整个 Trae（两区都算）
        `workbuddy` -> ("workbuddy", "")
        `""`/未知   -> ("", "")          不过滤
    """
    raw = str(value or "").strip()
    if not raw:
        return ALL, ""
    platform_id, realm = registry.split_selector(raw)
    # 兼容老写法：`trae` = 整个 Trae，`trae:cn` = 国内版
    if platform_id == "trae":
        if realm in ("cn", "intl"):
            return "trae", realm
        return "trae", ""
    spec = registry.get(platform_id)
    if spec is None:
        # 只写了区域（如 `cn`），按默认平台解释
        default = registry.get(registry.DEFAULT_PLATFORM)
        if default and default.realms:
            normalized = registry.normalize_realm(default, platform_id)
            if normalized == platform_id:
                return default.storage(), normalized
        return ALL, ""
    # 固定区域的平台（trae-cn / trae-intl）：区域由 spec 决定，忽略传入值
    if spec.realm:
        return spec.storage(), spec.realm
    if not realm:
        # 裸平台名：平台级，不按区收窄
        return spec.storage(), ""
    return spec.storage(), registry.normalize_realm(spec, realm)


def from_request(request) -> tuple[str, str]:
    """从请求头 / 查询串取平台作用域。

    头优先：控制台走 `X-Platform`；未登录的脚本 / curl 用 `?platform=`。
    """
    if request is None:
        return ALL, ""
    try:
        header = request.headers.get("x-platform")
    except Exception:  # noqa: BLE001 - 非 Starlette Request（测试桩）
        header = None
    value = header
    if not value:
        try:
            value = request.query_params.get("platform")
        except Exception:  # noqa: BLE001
            value = None
    return parse(value)


def account_ids(db: Session, platform: str, realm: str = "") -> list[int] | None:
    """该平台（或平台+区域）下的账号 id；`None` 表示不限制。

    返回 `None` 而不是空列表，是为了让调用方区分：
      * `None`      -> 没指定平台，不要加过滤条件
      * `[]`        -> 指定了平台但一个账号都没有 -> **必须返回空结果**
    这两者混用会让「切换到一个没有账号的平台」意外显示其它平台的数据。
    """
    if not platform:
        return None
    query = db.query(Account.id).filter(Account.platform == platform)
    if realm:
        query = query.filter(Account.domain == realm)
    return [row[0] for row in query.all()]


def apply(query, db: Session, platform: str, realm: str = "", column=None):
    """给查询加上平台约束。

    `column` 是「账号 id 列」（例如 `UsageLog.account_id`）。
    不传时按**账号表本身**过滤（`Account.platform`），因为调用方传进来的
    就是 `db.query(Account)`；传了则按该列 `IN (...)` 过滤日志类查询。

    返回值是 `(query, short_circuit)`：`short_circuit=True` 表示
    「该平台没有账号，结果必然为空」，调用方应直接返回空而不查库。
    """
    ids = account_ids(db, platform, realm)
    if ids is None:
        return query, False
    if not ids:
        return query, True
    if column is not None:
        return query.filter(column.in_(ids)), False
    from admin.models import Account
    query = query.filter(Account.platform == platform)
    if realm:
        query = query.filter(Account.domain == realm)
    return query, False


def filter_accounts(query, db: Session, platform: str, realm: str = ""):
    """给 `Account` 查询加平台约束（账号池 / 积分到期等直接用）。"""
    if not platform:
        return query
    query = query.filter(Account.platform == platform)
    if realm:
        query = query.filter(Account.domain == realm)
    return query


def label(platform: str, realm: str = "") -> str:
    """人类可读的平台名（用于响应体与错误文案）。

    ⚠️ 传进来的 `platform` 是**落库平台**（`trae`），不是对外 id
    （`trae-cn` / `trae-intl`）。`registry.get("trae")` 会返回 None，
    原来于是回落成「全部平台」—— 统计明明只算了国内版，标签却说全平台。
    这里按「落库平台 + 区域」反查对外 id。
    """
    from admin.platform import registry

    if not platform:
        return "全部平台"
    # 先尝试当成对外 id（workbuddy 这类单平台就是自己的落库值）
    spec = registry.get(platform)
    if spec is not None and not spec.realm:
        return spec.label
    # 落库平台 -> 找固定该区域的对外平台（trae + cn -> trae-cn）
    for candidate in registry.all_platforms():
        if candidate.storage() != platform:
            continue
        if realm and candidate.realm == realm:
            return candidate.label
    # 没有精确匹配（例如裸 `trae` = 两区都算）：用落库平台的通用名
    if not realm:
        for candidate in registry.all_platforms():
            if candidate.storage() == platform:
                # `Trae 国内版` -> `Trae`：去掉区域后缀
                base = candidate.label
                for suffix in (" 国内版", " 国际版", "国内版", "国际版"):
                    if base.endswith(suffix):
                        return base[: -len(suffix)].strip()
                return base
    for candidate in registry.all_platforms():
        if candidate.storage() == platform:
            return "%s · %s" % (
                (candidate.label or platform).split()[0],
                {"cn": "国内版", "intl": "国际版"}.get(realm, realm))
    return platform