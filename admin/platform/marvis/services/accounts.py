"""腾讯 Marvis 账号池：凭证落库 / 导入 / 额度同步。

## 凭证形态

一份 Marvis 登录态就是这几个字段（magpie 插件落盘的也是同一份）：

    {
      "openid":       "…",          # 身份，必需
      "access_token": "…",          # 必需
      "refresh_token": "…",         # 可选
      "guid":         "…",          # 设备标识，必需 —— 缺了上游一律 -109
      "login_type":   "1" | "2",    # 1=QQ，2=微信
      "nickname":     "…",          # 卡片上显示的名字
      "avatar":       "…"           # 可选
    }

`uid` 用 `openid`（唯一且稳定）；`accountId`（展示名）用昵称，取不到就退手机号
尾号以外的兜底 —— 不拿内部 ID 冒充名字。

## `guid` 为什么是必需项

上游每个请求都要 `Ual-Access-Guid`，缺了直接 -109 参数无效。它是**设备**标识，
不是账号标识：同一个号在不同设备上 guid 不同也能用，但每个号必须有一个。
导入时没带 guid 的凭证会**补一个**并写回库 —— 否则这份凭证永远换不出登录态。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.marvis.core import client, endpoints

logger = logging.getLogger("admin.platform.marvis.accounts")

PLATFORM = "marvis"

#: 额度快照的有效期（秒）。超过这个时间的号，列表页只返回旧快照，
#: 由后台线程去刷 —— 列表接口**绝不同步打上游**（50 个号能把接口拖到秒级）。
QUOTA_TTL = 300


def quota_is_fresh(account: Account) -> bool:
    """额度快照是否还在有效期内。"""
    synced = getattr(account, "credits_synced_at", None)
    if synced is None:
        return False
    return (datetime.utcnow() - synced).total_seconds() < QUOTA_TTL


def snapshot_of(account: Account) -> dict:
    """上一次的额度快照（列表接口只读它，不打上游）。"""
    try:
        snap = json.loads(getattr(account, "credits_snapshot", "") or "{}")
    except (ValueError, TypeError):
        snap = {}
    return snap if isinstance(snap, dict) else {}

#: 钱包没读到时，卡片显示「剩余额度未知」，路由不计入 —— 所以余额字段允许为空。
#: 一旦有读数，`balance_remain/total` 都写真实值。


def credentials_of(account: Account) -> dict:
    """从账号行里取出凭证字典。坏 JSON 当空 dict（不让一行坏数据打挂整池）。"""
    try:
        data = json.loads(account.auth_json or "{}")
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def token_of(data: dict) -> str:
    return str(data.get("access_token") or data.get("accessToken") or "")


def openid_of(data: dict) -> str:
    return str(data.get("openid") or data.get("openId") or "")


def guid_of(data: dict) -> str:
    return str(data.get("guid") or "")


def realm_of(account: Account) -> str:
    return (account.domain or "cn").strip() or "cn"


def public_view(account: Account, *, quota: dict | None = None,
                include_credential: bool = False) -> dict:
    """给控制台的账号视图。**默认不含 token**。

    `quota` 是调用方传入的额度快照（列表接口读缓存列，不在这里打上游）。
    """
    data = credentials_of(account)
    view = {
        "id": account.id,
        "platform": PLATFORM,
        "name": account.name or "",
        "uid": account.uid or "",
        "realm": realm_of(account),
        "status": account.status,
        "openid": openid_of(data),
        "login_type": str(data.get("login_type") or "1"),
        "has_guid": bool(guid_of(data)),
        "nickname": str(data.get("nickname") or ""),
        "avatar": str(data.get("avatar") or ""),
        "balance_total": account.balance_total,
        "balance_remain": account.balance_remain,
        "quota": quota if isinstance(quota, dict) else snapshot_of(account),
        "quota_fresh": quota_is_fresh(account),
        "last_sync_at": account.last_sync_at.isoformat()
        if getattr(account, "last_sync_at", None) else None,
        "last_err_msg": account.last_err_msg or "",
    }
    if include_credential:
        view["credential"] = data
    return view


def accounts_for(db: Session, realm: str = "") -> list[Account]:
    query = db.query(Account).filter(Account.platform == PLATFORM)
    if realm:
        query = query.filter(Account.domain == realm)
    return query.order_by(Account.id.asc()).all()


def select(db: Session, realm: str = "", *, exclude_ids: set[int] | None = None):
    """挑一个可用账号：优先「有余额且最近没出错」的。

    排序口径（与网关选号一致的地方尽量对齐）：
      · `status='active'` 且不在冷却中；
      · 有余额读数的排在没有读数的前面（没读数 = 不知道还能不能用）；
      · `last_used_at` 早的先上，避免一个号被打爆。
    """
    now = datetime.utcnow()
    query = db.query(Account).filter(Account.platform == PLATFORM,
                                     Account.status == "active")
    if realm:
        query = query.filter(Account.domain == realm)
    rows = query.all()
    candidates = []
    for account in rows:
        if exclude_ids and account.id in exclude_ids:
            continue
        until = getattr(account, "cool_until", None)
        if until and until > now:
            continue
        has_balance = account.balance_remain is not None
        candidates.append((0 if has_balance else 1,
                           account.last_used_at or datetime.min,
                           account.id, account))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][3]


def _upsert(db: Session, payload: dict) -> tuple[Account, bool]:
    """按 openid 幂等写入。返回 `(行, 是否新建)`。"""
    data = dict(payload)
    openid = openid_of(data)
    if not openid:
        raise ValueError("凭证缺少 openid")
    if not token_of(data):
        raise ValueError("凭证缺少 access_token")
    # 缺 guid 的凭证永远换不出登录态 —— 补一个并写回。
    if not guid_of(data):
        data["guid"] = str(uuid.uuid4())
    data.setdefault("login_type", "1")

    realm = str(data.get("realm") or "cn")
    uid = openid
    name = str(data.get("nickname") or "").strip() or ("Marvis " + openid[-6:])

    account = (db.query(Account)
               .filter(Account.platform == PLATFORM, Account.uid == uid)
               .first())
    created = account is None
    if created:
        account = Account(platform=PLATFORM, uid=uid, name=name,
                          domain=realm, status="active")
        db.add(account)
    account.name = name
    account.domain = realm
    account.auth_json = json.dumps(data, ensure_ascii=False)
    account.status = "active"
    account.session_dead_fails = 0
    account.consecutive_fails = 0
    account.cool_until = None
    account.cool_kind = ""
    account.last_err_msg = ""
    db.commit()
    db.refresh(account)
    return account, created


def record_from_payload(raw) -> dict:
    """把用户粘进来的东西归一成凭证 dict。

    接受：整段 JSON 对象 / `{"credential": {...}}` 包装 / magpie 落盘的同形态对象。
    """
    data = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ValueError("凭证为空")
        try:
            data = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("不是合法 JSON：%s" % exc) from exc
    if not isinstance(data, dict):
        raise ValueError("凭证必须是 JSON 对象")
    inner = data.get("credential") or data.get("auth") or data.get("metadata")
    if isinstance(inner, dict):
        merged = {**inner, **{k: v for k, v in data.items() if k in
                             ("openid", "access_token", "openId", "accessToken",
                              "refresh_token", "guid", "login_type", "nickname")}}
        data = merged
    return data


def import_record(db: Session, record: dict) -> tuple[Account, bool]:
    return _upsert(db, record_from_payload(record))


def import_batch(db: Session, text: str) -> dict:
    """批量导入：JSON 数组 / JSONL / 单个对象。

    与其它平台一致返回 `added/updated/failed/total/errors`，
    控制台据此显示「新增几个 / 更新几个 / 失败几条」。
    """
    raw = (text or "").strip().lstrip("\ufeff")
    if not raw:
        return {"ok": False, "added": 0, "updated": 0, "failed": 0, "total": 0,
                "errors": ["没有内容"]}

    records: list = []
    parsed = None
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        parsed = None
    if isinstance(parsed, list):
        records = parsed
    elif isinstance(parsed, dict):
        inner = parsed.get("accounts") or parsed.get("items")
        records = inner if isinstance(inner, list) else [parsed]
    else:
        for line in raw.splitlines():
            line = line.strip().rstrip(",")
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue

    added = updated = failed = 0
    errors: list[str] = []
    for index, record in enumerate(records, 1):
        try:
            _account, created = import_record(db, record)
            if created:
                added += 1
            else:
                updated += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            if len(errors) < 50:
                errors.append("第 %d 条：%s" % (index, str(exc)[:120]))

    return {"ok": not errors, "added": added, "updated": updated,
            "failed": failed, "total": len(records), "errors": errors}


def sync_quota(db: Session, account: Account) -> dict:
    """拉一次令牌钱包，写回余额字段。

    余额口径：`balance_remain = avail_tokens`（可用），`balance_total = total_tokens`。
    两个都写成**计费 token**，与上游语义一致。
    """
    data = credentials_of(account)
    realm = realm_of(account)
    try:
        wallet = client.fetch_wallet(data, realm, account_id=account.id)
    except client.UpstreamError as exc:
        account.last_err_at = datetime.utcnow()
        account.last_err_msg = exc.message[:240]
        account.last_upstream_error = json.dumps(exc.to_dict(), ensure_ascii=False)
        db.commit()
        raise

    total = _as_int(wallet.get("total_tokens"))
    used = _as_int(wallet.get("used_tokens"))
    avail = _as_int(wallet.get("avail_tokens"))
    if avail is None and total is not None and used is not None:
        avail = max(0, total - used)
    account.balance_total = total
    account.balance_remain = avail
    account.last_sync_at = datetime.utcnow()
    # 额度快照 + 采集时间：列表接口只读这两列（`quota_is_fresh` / `snapshot_of`），
    # 不打上游。缺了它们，列表页要么永远「额度未知」，要么每次刷新都同步打一遍。
    account.credits_snapshot = json.dumps({
        "total": total, "used": used, "avail": avail,
        "frozen": _as_int(wallet.get("frozen_tokens")),
        "unit": "tokens",
    }, ensure_ascii=False)
    account.credits_synced_at = datetime.utcnow()
    account.last_err_msg = ""
    account.last_err_at = None
    account.last_upstream_error = ""
    db.commit()
    return {"total": total, "used": used, "avail": avail,
            "frozen": _as_int(wallet.get("frozen_tokens")),
            "raw": wallet}


def _as_int(value):
    try:
        if value is None:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def ensure_token(db: Session, account: Account) -> dict:
    """取一份可用凭证。

    Marvis 没有独立的 refresh 接口（续签要整份凭证），所以这里只做**完整性**
    校验：缺 openid / access_token / guid 的直接判失效，让上层换号或重新扫码，
    而不是发一个注定 -109 的请求出去。
    """
    data = credentials_of(account)
    if not openid_of(data) or not token_of(data):
        raise client.UpstreamError(
            "账号凭证不完整（缺 openid / access_token），请重新扫码登录",
            status=401, kind="auth")
    if not guid_of(data):
        # 历史数据可能没 guid：补一个并写回，否则这份凭证永远换不出登录态。
        data["guid"] = str(uuid.uuid4())
        account.auth_json = json.dumps(data, ensure_ascii=False)
        db.commit()
    return data


def summarize(views) -> dict:
    """汇总一组账号视图（`public_view` 的返回值列表）。

    接受的是**视图列表**而不是 db —— 因为 `MarvisAccountOps.list()` 已经
    把行读出来了，再传 db 进去就是二次查询。
    """
    rows = [v for v in (views or []) if isinstance(v, dict)]
    total = len(rows)
    active = sum(1 for v in rows if v.get("status") == "active")
    return {
        "accounts": total,
        "active": active,
        "disabled": total - active,
        "balance_total": sum(int(v.get("balance_total") or 0) for v in rows),
        "balance_remain": sum(int(v.get("balance_remain") or 0) for v in rows),
        "updated_at": time.time(),
    }


def summarize_db(db: Session, realm: str = "") -> dict:
    """按库直接汇总（路由层用；不依赖已构造好的视图）。"""
    return summarize([public_view(a) for a in accounts_for(db, realm)])