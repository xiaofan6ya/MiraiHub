"""Account ownership shared by key management and every gateway selection path."""
import json

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import aliased

from admin.models import Account, ApiKey


def _owned_pairs(db):
    """每个 Key 独占的账号：`{key_id: set(account_id)}`。

    两种独占形态都要收进来：
      * `account_id`（单选，旧行为）
      * `account_ids`（JSON 数组，多选）

    ## 为什么必须合起来看（这是个真实事故）

    选号时"排除别人独占的号"那段只检查 `ApiKey.account_id`（单选），
    **完全没看 `account_ids`（多选）**。于是只要一个账号是通过「多选」
    绑定的，公共池和平台独占的 Key 都还能照常打到它 ——
    用户实测：「设置了独占，其他 Key 还能打到这个账号」。
    """
    out: dict[int, set[int]] = {}

    def add(key_id, acc_id):
        try:
            aid = int(acc_id)
        except (TypeError, ValueError):
            return
        if aid > 0:
            out.setdefault(int(key_id), set()).add(aid)

    for key in (db.query(ApiKey)
                .filter(or_(ApiKey.account_id.is_not(None),
                            ApiKey.account_ids.is_not(None),
                            ApiKey.account_ids != ""))
                .all()):
        add(key.id, key.account_id)
        for aid in _multi_ids(key):
            add(key.id, aid)
    return out


def _uid_set(db, account_ids) -> set[str]:
    """这些账号对应的上游 UID 集合（用于把同 UID 的重复记录一起保护起来）。"""
    if not account_ids:
        return set()
    rows = (db.query(Account.uid)
            .filter(Account.id.in_(list(account_ids)), Account.uid.is_not(None),
                    Account.uid != "").all())
    return {r[0] for r in rows if r[0]}


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
    own_id = int(getattr(key, "id", 0) or key_id or 0)
    # 本 Key 自己独占的账号，任何分支都不能把自己排除掉
    mine = set(_multi_ids(key)) if key is not None else set()
    if account_id:
        try:
            mine.add(int(account_id))
        except (TypeError, ValueError):
            pass

    def not_others(query):
        """排除**别的 Key** 独占的账号（两种绑定形态 + 同 UID 都算）。"""
        pairs = _owned_pairs(query.session)
        foreign: set[int] = set()
        for owner_key, acc_ids in pairs.items():
            if owner_key == own_id:
                continue                      # 自己独占的留着
            foreign |= acc_ids
        # 同 UID 的重复记录也一起排除：否则换个 id 就能绕过独占
        foreign_uids = _uid_set(query.session, foreign)
        if not foreign and not foreign_uids:
            return query
        cond = []
        if foreign:
            cond.append(~Account.id.in_(list(foreign)))
        if foreign_uids:
            cond.append(or_(Account.uid.is_(None), Account.uid == "",
                            ~Account.uid.in_(list(foreign_uids))))
        return query.filter(*cond)

    # ---- 1) 多选独占 ----
    ids = _multi_ids(key) if key is not None else []
    if ids:
        return query.filter(Account.id.in_(ids))

    # ---- 2) 单账号独占 ----
    #
    # ⚠️ `account_id` 有**两个来源**，漏掉第二个就是 bug：
    #   * 调用方显式传入（排障 / 独占 Key 的探测）→ `account_id` 形参
    #   * Key 自己绑定了独占账号 → `key.account_id`
    # 只判断形参时，绑定独占的 Key 走的是「公共池」分支：
    # 它自己那个号反而被排除、别的号全都能用 —— 与独占语义完全相反
    # （实测：account_id=#156 的 Key 最终拿到 8 个号且不含 #156）。
    bound = account_id
    if not bound and key is not None:
        bound = getattr(key, "account_id", None)
    if bound:
        owner = select(ApiKey.id).where(
            ApiKey.id == (key_id or own_id),
            ApiKey.account_id == bound,
            ApiKey.status == "active").exists()
        return query.filter(Account.id == bound, owner)

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
        query = query.filter(Account.platform == platform)
        return not_others(query)

    # ---- 4) 公共池（现有行为）----
    return not_others(query)


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