"""Account ownership shared by key management and every gateway selection path."""
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import aliased

from admin.models import Account, ApiKey


def scope_accounts(query, account_id=None, key_id=None, key=None):
    """按 API Key 的绑定约束收窄账号池。

    ## 优先级（从高到低）

        1. `account_ids` 非空   -> 只用这些账号（多选独占）
        2. `account_id` 非空    -> 单账号独占（旧行为）
        3. `platform` 非空      -> 只在该平台的公共池里选
        4. 都不设               -> 全平台公共池

    ## 为什么要 `key` 参数

    调用点很多（_select_account / _transient_cool_fallback /
    _no_account_reason / 模型列表 …），逐个加散参数要改十几处。
    传整个 ApiKey 对象最省，以后新增约束也不用再改签名。
    """
    # ---- 1) 多选独占 ----
    ids = _multi_ids(key) if key is not None else []
    if ids:
        return query.filter(Account.id.in_(ids))

    # ---- 2) 单账号独占（旧行为，一行不动）----
    if account_id:
        owner = select(ApiKey.id).where(
            ApiKey.id == key_id, ApiKey.account_id == account_id,
            ApiKey.status == "active").exists()
        return query.filter(Account.id == account_id, owner)

    # ---- 3) 独占平台 ----
    platform = ""
    if key is not None:
        platform = str(getattr(key, "platform", "") or "").strip().lower()
    if platform:
        # 归一成**落库平台**：Key 里可能存「区级对外 id」，而
        # `accounts.platform` 存的是 storage 落库值。不归一就永远选不到号，
        # 表现是「绑了平台反而 503：没有可用账号」。
        try:
            from admin.platform import registry

            spec = registry.get(platform)
            if spec is not None:
                platform = spec.storage()
        except Exception:  # noqa: BLE001 - 注册表异常时按原值过滤
            pass
        # 独占平台时，**不排除**别的 Key 独占的账号 ——
        # 指定了平台，本意就是「用这个平台的号」。
        # 但要把「已被别的 Key 独占」的排除，否则会抢号。
        reserved = aliased(Account)
        owned = select(ApiKey.id).select_from(ApiKey).outerjoin(
            reserved, reserved.id == ApiKey.account_id).where(
                ApiKey.account_id.is_not(None),
                ApiKey.id != (getattr(key, "id", 0) or 0),
                or_(ApiKey.account_id == Account.id,
                    (reserved.uid != "") & (reserved.uid == Account.uid))).exists()
        return query.filter(Account.platform == platform, ~owned)

    # ---- 4) 公共池（现有行为）----
    reserved = aliased(Account)
    owned = select(ApiKey.id).select_from(ApiKey).outerjoin(
        reserved, reserved.id == ApiKey.account_id).where(
            ApiKey.account_id.is_not(None),
            or_(ApiKey.account_id == Account.id,
                (reserved.uid != "") & (reserved.uid == Account.uid))).exists()
    return query.filter(~owned)


def _multi_ids(key) -> list[int]:
    """解析 Key 的 `account_ids`（JSON 字符串）。"""
    raw = getattr(key, "account_ids", "") or ""
    if not raw:
        return []
    try:
        import json

        value = json.loads(raw)
        if isinstance(value, list):
            return [int(v) for v in value if str(v).isdigit()]
    except (ValueError, TypeError):
        pass
    return []


def validate_binding(db, account_id, key_id=None):
    if account_id in (None, 0, "0", ""):
        return None
    try:
        aid = int(account_id)
        if isinstance(account_id, bool) or str(aid) != str(account_id) or aid < 1:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise HTTPException(400, "账号 ID 必须是正整数或 0（不绑定）") from None
    account = db.query(Account).filter(Account.id == aid).first()
    if not account:
        raise HTTPException(400, f"账号 #{aid} 不存在")
    # Serialize reservations even when legacy rows share the same upstream UID.
    peers = db.query(Account).filter(
        or_(Account.id == aid, (Account.uid == account.uid) & (Account.uid != ""))
    ).order_by(Account.id).with_for_update().all()
    if not any(a.id == aid for a in peers):
        raise HTTPException(400, f"账号 #{aid} 不存在")
    conflict = db.query(ApiKey).filter(ApiKey.account_id.in_([a.id for a in peers]))
    if key_id:
        conflict = conflict.filter(ApiKey.id != key_id)
    if conflict.with_for_update().first():
        raise HTTPException(409, "该账号已被其他 API Key 独占绑定，请先解除原绑定")
    return aid


def validate_bindings(db, account_ids, key_id=None) -> list[int]:
    """校验多选账号（每个都要合法且未被别的 Key 独占）。

    与 `validate_binding` 同口径，只是批量。**任一失败就整体失败** ——
    部分成功会让用户以为全绑上了。
    """
    if not account_ids:
        return []
    out: list[int] = []
    for raw in account_ids:
        aid = validate_binding(db, raw, key_id)
        if aid and aid not in out:
            out.append(aid)
    return out


def binding_owners(db):
    """Map duplicate account records to the same visible reservation."""
    rows = db.query(Account).all()
    keys = {k.account_id: k for k in db.query(ApiKey).filter(ApiKey.account_id.is_not(None)).all()}
    by_uid = {a.uid: keys[a.id] for a in rows if a.uid and a.id in keys}
    return {a.id: keys.get(a.id) or by_uid.get(a.uid) for a in rows}