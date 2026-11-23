"""科搭 Codar 账号池：扫描 / 导入 / 点数同步 / 令牌续期 / 目录缓存。

## 余额口径（两个池子，两个接口）

    主读数  POST {account}/v1/gettoken       {username, token:0}
            → {code:200, data?:{token, agent_token, …}}
              `token` 是通用点数，`agent_token` 是 agent 点数
    副读数  POST {account}/v1/agent/balance  {username}
            → {code:200, data?:{username, agent_balance}}

主读数的 `agent_token` 缺失时才用副读数补 —— 两条读数来自同一个账号，
一条死了另一条还能出数，所以**不能因为一条失败就把卡片空白**。

落到统一列：

    balance_remain = 通用点数（主读数）
    balance_total  = 通用 + agent（总点数）
    credits_snapshot = 两个数 + 各自的来源，供明细展示

「点数查询没有返回余额」与「点数就是 0」必须分开：前者抛错保留旧值，
后者才写 0。写反了会把一个有钱的号显示成空号。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.codar.core import client, endpoints, paths
from admin.security import clean_display_name

logger = logging.getLogger("admin.platform.codar.services.accounts")

PLATFORM = endpoints.PLATFORM

#: 令牌还剩这么久就提前换新（秒）。令牌寿命约 7 天，提前一天换，
#: 既不制造多余轮换，也不会让请求撞上「刚好过期」。
RENEW_MARGIN_S = 24 * 3600

#: 续期锁：同一账号串行。并发续期会让后至的请求拿着已作废的旧令牌去换，
#: 结果是「续期把号自己挤下线」。
_RENEW_LOCKS: dict[int, threading.Lock] = {}
_RENEW_GUARD = threading.Lock()

#: 点数字段名（上游两种写法都见过：主读数用 token/agent_token，副读数用
#: balance/agent_balance）。
GENERAL_NAMES = ("token", "remaining_points", "points", "balance",
                 "general_points", "general")
AGENT_NAMES = ("agent_token", "agentToken", "agent_points", "agentPoints")
AGENT_BALANCE_NAMES = ("agent_balance", "balance", "points", "agent_token",
                       "agentToken", "agent_points", "agentPoints",
                       "remaining_points")


def _renew_lock(account_id: int) -> threading.Lock:
    with _RENEW_GUARD:
        lock = _RENEW_LOCKS.get(account_id)
        if lock is None:
            lock = threading.Lock()
            _RENEW_LOCKS[account_id] = lock
        return lock


# ---------------------------------------------------------------------------
# 凭证
# ---------------------------------------------------------------------------
def credentials_of(account) -> dict:
    try:
        data = json.loads(getattr(account, "auth_json", "") or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def token_of(data: dict) -> str:
    return str((data or {}).get("token") or "").strip()


def username_of_account(account) -> str:
    """账号名：库里的 `username`，没有就从令牌载荷里读。"""
    data = credentials_of(account)
    name = str(data.get("username") or "").strip()
    if name:
        return name
    name = paths.username_of(token_of(data))
    return name or str(getattr(account, "uid", "") or "").strip()


def account_token(account) -> str:
    return token_of(credentials_of(account))


def expires_of(account) -> int:
    data = credentials_of(account)
    raw = data.get("expires_at")
    if isinstance(raw, (int, float)) and raw > 0:
        return int(raw)
    return paths.expires_at(token_of(data))


def realm_of(account) -> str:
    """单区平台：一律 cn（账号表里仍写 domain，供通用过滤使用）。"""
    return "cn"


def public_view(account: Account) -> dict:
    data = credentials_of(account)
    expires = expires_of(account)
    snapshot = {}
    try:
        snapshot = json.loads(account.credits_snapshot or "{}")
    except (TypeError, ValueError):
        snapshot = {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    return {
        "id": account.id,
        # 前端按这个字段选接口前缀，缺了会一律退回 WorkBuddy 的 /api/accounts
        "platform": PLATFORM,
        "name": account.name,
        "uid": account.uid,
        "realm": "cn",
        "status": account.status,
        "balance_remain": account.balance_remain,
        "balance_total": account.balance_total,
        "credits_snapshot": account.credits_snapshot,
        "credits_expiring": account.credits_expiring,
        "expires_at_ms": expires * 1000 if expires else 0,
        "expires_in_days": round((expires - time.time()) / 86400, 1)
        if expires else None,
        # 这个平台没有独立的 refresh token：续期用的就是同一枚令牌
        "has_refresh": False,
        "renewable": bool(expires),
        "auth_mode": "local-scan",
        "points_general": snapshot.get("general"),
        "points_agent": snapshot.get("agent"),
        "username": str(data.get("username") or username_of_account(account)),
        "source": str(data.get("source") or ""),
        "last_sync_at": account.balance_synced_at.isoformat()
        if account.balance_synced_at else None,
        "created_at": account.created_at.isoformat()
        if account.created_at else None,
        "last_err": account.last_err_msg or "",
    }


def accounts_for(db: Session, realm: str = "") -> list[Account]:
    query = db.query(Account).filter(Account.platform == PLATFORM)
    if realm:
        query = query.filter(Account.domain == realm)
    return query.order_by(Account.id.asc()).all()


# ---------------------------------------------------------------------------
# 入库
# ---------------------------------------------------------------------------
def record_of(record: dict) -> dict:
    """外部记录 → 标准入库载荷（token 必填，导入前先校验）。"""
    token = str(record.get("token") or record.get("key")
                or record.get("api_key") or "").strip()
    if not token:
        raise ValueError("凭证缺少 token")
    try:
        verified = paths.verify_token(token)
    except paths.ScanError as exc:
        # 导入路径按 ValueError 报错（与其它平台一致），
        # 但保留扫描侧那句可执行的下一步（「打开客户端刷新登录」）
        raise ValueError(str(exc)) from None
    name = str(record.get("name") or "").strip() or verified["username"]
    return {
        "token": verified["token"],
        "username": verified["username"],
        "expires_at": verified["expires_at"],
        "name": clean_display_name(name),
        "source": str(record.get("source") or "import"),
    }


def _upsert(db: Session, payload: dict) -> tuple[Account, bool]:
    """按 (platform, uid) 幂等写入；uid 用令牌里的账号名。

    账号名是上游账号侧接口的必需参数，也是同一个真实账号在两次导入之间
    唯一稳定的标识（令牌会换、指纹会变）。所以 uid = username。
    """
    uid = str(payload.get("username") or "").strip()
    if not uid:
        raise ValueError("凭证里没有账号名")
    existing = (db.query(Account)
                .filter(Account.platform == PLATFORM,
                        Account.uid == uid).first())
    if existing is None:
        # 同一枚令牌换了个名字也要认领旧行，别为同一账号落两行
        token = str(payload.get("token") or "")
        for row in accounts_for(db):
            if token and token_of(credentials_of(row)) == token:
                existing = row
                break
    created = existing is None
    if existing is None:
        existing = Account(platform=PLATFORM, uid=uid, status="active",
                           domain="cn", balance_remain=0, balance_total=0)
        db.add(existing)
    existing.uid = uid
    existing.name = clean_display_name(
        str(payload.get("name") or existing.name or uid or "科搭"))
    existing.domain = "cn"
    existing.auth_json = json.dumps(payload, ensure_ascii=False)
    existing.status = "active"
    existing.last_sync_at = datetime.utcnow()
    if created:
        existing.balance_remain = 0
        existing.balance_total = 0
    db.commit()
    db.refresh(existing)
    return existing, created


def import_record(db: Session, record: dict) -> tuple[Account, bool]:
    payload = record_of(record)
    account, created = _upsert(db, payload)
    try:
        from admin.platform import shared as _shared

        _shared.schedule_import_refresh(PLATFORM, [account.id])
    except Exception:  # noqa: BLE001 - 补刷排队失败不影响导入结果
        pass
    return account, created


def import_local(db: Session, realm: str = "",
                 uids: list[str] | None = None) -> dict:
    """扫描本机登录态并入库（`uids` 是扫描弹窗的勾选值，空 = 全导）。"""
    path = paths.config_path()
    found = paths.app_token(path)
    if found.get("error"):
        return {"imported": 0, "added": 0, "updated": 0, "scanned": 0,
                "reason": str(found["error"])}
    if uids:
        want = {str(uid) for uid in uids}
        if found["username"] not in want:
            return {"imported": 0, "added": 0, "updated": 0, "scanned": 1,
                    "reason": "勾选的账号与本机登录态不匹配"}
    payload = {
        "token": found["token"],
        "username": found["username"],
        "expires_at": int(found.get("expires_at") or 0),
        "name": found["username"],
        "source": "local-scan",
    }
    try:
        account, created = _upsert(db, payload)
    except ValueError as exc:
        return {"imported": 0, "added": 0, "updated": 0, "scanned": 1,
                "reason": str(exc)}
    try:
        from admin.platform import shared as _shared

        _shared.schedule_import_refresh(PLATFORM, [account.id])
    except Exception:  # noqa: BLE001
        pass
    return {"imported": 1, "added": 1 if created else 0,
            "updated": 0 if created else 1, "scanned": 1,
            "account": public_view(account)}


def import_batch(db: Session, text: str) -> dict:
    """批量导入：JSONL / JSON 数组 / 单对象 / 裸令牌（每行一个）。"""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "added": 0, "updated": 0, "failed": 0, "total": 0,
                "errors": ["没有提供任何凭证"]}
    items: list[dict] = []
    parsed = None
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        items = [parsed]
    elif isinstance(parsed, list):
        items = [row for row in parsed if isinstance(row, dict)]
    else:
        for line in text.splitlines():
            line = line.strip().rstrip(",")
            if not line or line in ("[", "]"):
                continue
            node = None
            try:
                node = json.loads(line)
            except ValueError:
                node = None
            if isinstance(node, dict):
                items.append(node)
            elif paths.token_shape_ok(line) and len(line) > 40:
                items.append({"token": line})
    if not items:
        return {"ok": False, "added": 0, "updated": 0, "failed": 0, "total": 0,
                "errors": ["没有解析出任何账号记录（JSONL / JSON 数组 / 裸令牌）"]}
    added = updated = failed = 0
    errors: list[str] = []
    for item in items:
        try:
            _account, created = import_record(db, item)
            added += 1 if created else 0
            updated += 0 if created else 1
        except Exception as exc:  # noqa: BLE001 - 单条失败不影响其余
            failed += 1
            errors.append("跳过一条：%s" % str(exc)[:160])
    return {"ok": failed == 0, "added": added, "updated": updated,
            "failed": failed, "total": len(items), "errors": errors[:20]}


# ---------------------------------------------------------------------------
# 令牌续期
# ---------------------------------------------------------------------------
def _fresh_enough(expires: int) -> bool:
    return bool(expires) and expires - RENEW_MARGIN_S > time.time()


def refresh_and_store(db: Session, account: Account, *,
                      stale_token: str = "") -> dict:
    """用 `/v1/token/refresh` 换新令牌并落库（同账号串行）。

    `stale_token` 是「刚被上游 401 的那枚」：进锁后如果发现库里的令牌
    已经不是它，说明别的线程刚换过 —— 直接用新的，不再换第二次。
    """
    key = int(getattr(account, "id", 0) or 0)
    with _renew_lock(key):
        try:
            db.refresh(account)
        except Exception:  # noqa: BLE001 - 未落库的新对象没得刷
            pass
        data = credentials_of(account)
        token = token_of(data)
        if stale_token and token and token != stale_token:
            return data
        if not token:
            raise client.UpstreamError("账号没有令牌，请重新扫描或导入",
                                       kind="auth")
        payload = client.request_json(
            "POST", endpoints.account_url(endpoints.REFRESH_PATH),
            token=token, account_id=key, context="令牌续期")
        body = client.unwrapped(payload)
        fresh = str((body or {}).get("token") or "").strip() if isinstance(body, dict) else ""
        if not fresh:
            raise client.UpstreamError("续期应答里没有新令牌", kind="auth",
                                       code="no_token")
        if not paths.token_shape_ok(fresh):
            raise client.UpstreamError("续期返回的令牌无法使用（含不可见字符）",
                                       kind="auth", code="unusable_token")
        expires = paths.expires_at(fresh)
        if expires and paths.expired(fresh):
            raise client.UpstreamError("续期返回的令牌已过期", kind="auth",
                                       code="expired_token")
        data = dict(data)
        data["token"] = fresh
        data["expires_at"] = expires
        data["username"] = paths.username_of(fresh) or data.get("username") or ""
        data["source"] = data.get("source") or "refresh"
        account.auth_json = json.dumps(data, ensure_ascii=False)
        account.last_sync_at = datetime.utcnow()
        db.commit()
        logger.info("科搭账号 %s 令牌已续期（新到期 %s）", key,
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(expires))
                    if expires else "未声明")
        return data


def token_for_call(db: Session, account: Account, *,
                   stale_token: str = "") -> str:
    """取一枚可用令牌：临期先续，过期不拿去除打接口。"""
    data = credentials_of(account)
    token = token_of(data)
    if not token:
        raise client.UpstreamError("账号没有令牌，请重新扫描或导入", kind="auth")
    expires = expires_of(account)
    if stale_token:
        return token_of(refresh_and_store(db, account, stale_token=stale_token))
    if not expires or _fresh_enough(expires):
        return token
    if paths.expired(token):
        raise client.UpstreamError(
            "令牌已过期：打开科搭客户端刷新登录后再导入", kind="auth",
            code="token_expired")
    try:
        return token_of(refresh_and_store(db, account))
    except client.UpstreamError as exc:
        # 续期失败但旧令牌还没到期：先用旧令牌，失败由上游回答。
        if exc.kind == "auth" and not paths.expired(token):
            logger.warning("科搭账号 %s 续期失败（沿用旧令牌）：%s",
                           getattr(account, "id", ""), exc.message)
            return token
        raise


# ---------------------------------------------------------------------------
# 点数
# ---------------------------------------------------------------------------
def _amount(value):
    """点数字段 → float；不是数字给 None（**不**把不可读当 0）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number == number else None


def _pick(body, names) -> float | None:
    if not isinstance(body, dict):
        return None
    for name in names:
        if name in body:
            number = _amount(body.get(name))
            if number is not None:
                return number
    return None


def read_points(token: str, username: str, *, account_id=None) -> dict:
    """读两个点数池：主读数 `gettoken`，副读数 `agent/balance` 补 agent。"""
    general = agent = None
    agent_source = ""
    errors: list[str] = []
    try:
        payload = client.request_json(
            "POST", endpoints.account_url(endpoints.POINTS_PATH), token=token,
            json_body={"username": username, "token": 0},
            account_id=account_id, context="点数")
        body = client.unwrapped(payload)
        general = _pick(body, GENERAL_NAMES)
        agent = _pick(body, AGENT_NAMES)
        if general is not None or agent is not None:
            agent_source = agent_source or ("gettoken" if agent is not None else "")
    except client.UpstreamError as exc:
        errors.append(exc.message)
        if exc.kind in ("auth", "account_restricted", "client_gated"):
            raise
    if agent is None:
        try:
            payload = client.request_json(
                "POST", endpoints.account_url(endpoints.AGENT_BALANCE_PATH),
                token=token, json_body={"username": username},
                account_id=account_id, context="agent 点数")
            body = client.unwrapped(payload)
            agent = _pick(body, AGENT_BALANCE_NAMES)
            if agent is not None:
                agent_source = "agent_balance"
        except client.UpstreamError as exc:
            errors.append(exc.message)
            if exc.kind in ("auth", "account_restricted", "client_gated"):
                raise
    if general is None and agent is None:
        raise client.UpstreamError(
            errors[0] if errors else "点数查询没有返回余额", kind="upstream",
            detail="; ".join(errors)[:300])
    return {"general": general, "agent": agent, "agent_source": agent_source,
            "errors": errors}


def sync_balances(db: Session, account: Account) -> dict:
    """刷点数 → 统一列 + 快照。**读不出来就抛错，不动已有数字。**"""
    token = token_for_call(db, account)
    username = username_of_account(account)
    if not username:
        raise client.UpstreamError("账号没有用户名，无法查询点数", kind="auth")
    points = read_points(token, username, account_id=int(account.id or 0))
    general = points["general"]
    agent = points["agent"]
    if general is not None:
        account.balance_remain = int(round(general))
    account.balance_total = int(round((general or 0) + (agent or 0)))
    account.balance_synced_at = datetime.utcnow()
    account.last_sync_at = datetime.utcnow()
    account.credits_snapshot = json.dumps({
        "unit": "points",
        "general": general,
        "agent": agent,
        "agent_source": points["agent_source"],
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "notes": points["errors"][:3],
    }, ensure_ascii=False)
    db.commit()
    return {"ok": True, "general": general, "agent": agent,
            "remain": account.balance_remain, "total": account.balance_total}


def summarize(db: Session, realm: str = "") -> dict:
    rows = accounts_for(db, realm)
    active = [row for row in rows if row.status == "active"]
    general = 0
    agent = 0
    for row in active:
        try:
            snapshot = json.loads(row.credits_snapshot or "{}")
        except (TypeError, ValueError):
            snapshot = {}
        if not isinstance(snapshot, dict):
            continue
        value = _amount(snapshot.get("general"))
        if value is not None:
            general += value
        value = _amount(snapshot.get("agent"))
        if value is not None:
            agent += value
    return {
        "platform": PLATFORM,
        "accounts": len(rows),
        "active": len(active),
        "remain_points": round(general, 2),
        "agent_points": round(agent, 2),
        "total_points": round(sum(row.balance_remain or 0 for row in active), 2),
        "quota_kind": "points",
    }


# ---------------------------------------------------------------------------
# 目录
# ---------------------------------------------------------------------------
def _catalog_account(db: Session, key=None):
    """挑一个能拉目录的号：优先 Key 绑定的号，其次最闲的 active 号。"""
    try:
        from admin.platform import shared

        account = shared.select_account(db, storage=PLATFORM, realm="cn",
                                        key=key, ignore_capacity=True)
        if account is not None:
            return account
    except Exception:  # noqa: BLE001 - 选号失败退回「第一个 active」
        logger.debug("科搭目录选号失败", exc_info=True)
    for row in accounts_for(db):
        if row.status == "active":
            return row
    return None


def catalog(db: Session, *, force: bool = False, key=None) -> dict:
    """模型目录：60 秒内存缓存；不可达时给内置表并标记 `fallback`。

    `fallback` 是**写给调用方看的**：客户端据此知道这份目录不是上游此刻的
    真实答案，而不是把一张旧表当成权威。空列表按「这次没用」处理 ——
    把空目录当成答案会让客户端一个模型都选不到。
    """
    if not force and client.catalog_fresh():
        cached = client.cached_catalog()
        return {"rows": cached["rows"], "fallback": cached["fallback"],
                "at": cached["at"],
                "vendor_concurrency": cached["vendor_concurrency"],
                "error": ""}
    account = _catalog_account(db, key)
    if account is None:
        rows = endpoints.fallback_catalog()
        client.remember_catalog(rows, fallback=True)
        return {"rows": rows, "fallback": True, "at": time.time(),
                "vendor_concurrency": 0,
                "error": "账号池没有启用中的科搭账号"}
    try:
        token = token_for_call(db, account)
        rows, concurrency = client.fetch_catalog(
            token, account_id=int(account.id or 0))
        if not rows:
            raise client.UpstreamError("目录为空（该账号未被投放任何模型）",
                                       kind="upstream")
        client.remember_catalog(rows, fallback=False,
                                vendor_concurrency=concurrency)
        return {"rows": rows, "fallback": False, "at": time.time(),
                "vendor_concurrency": concurrency, "error": ""}
    except client.UpstreamError as exc:
        rows = client.cached_catalog()["rows"] or endpoints.fallback_catalog()
        client.remember_catalog(rows, fallback=True)
        logger.warning("科搭目录拉取失败，使用兜底表：%s", exc.message)
        return {"rows": rows, "fallback": True, "at": time.time(),
                "vendor_concurrency": 0, "error": exc.message}


def protocol_for(db: Session | None, model: str = "") -> str:
    """这个模型该走哪套协议：目录优先，目录没拉过就按名字兜底。

    `db=None` 表示「不许发请求」（热路径在没有会话时调用它）：只用内存里
    已有的目录，没有就按模型名兜底 —— 热路径不能为了选协议多打一次上游。
    """
    if not client.cached_catalog()["rows"] and db is not None:
        try:
            catalog(db)
        except Exception:  # noqa: BLE001 - 目录拉不到不影响按名字选路
            logger.debug("科搭目录预取失败", exc_info=True)
    if client.cached_catalog()["rows"]:
        return client.protocol_of(model)
    return endpoints.protocol_by_name(model)
