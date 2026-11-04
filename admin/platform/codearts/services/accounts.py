"""CodeArts 凭证与账号池。

## 凭证形态（`Account.auth_json`）

```json
{
  "realm": "cn",
  "region": "cn-north-4",
  "access_key": "HSTAT...",        // 临时 AK（STS 签发）
  "secret_key": "...",             // 临时 SK
  "security_token": "gQpjbi1...",  // STS security token（很长）
  "refresh_token": "eyJ0eXAi...",  // 续期用（JWT）
  "expires_at": 1793775876627,     // 毫秒；AK/SK 的过期时刻
  "uid": "493d0b03...",
  "user_name": "hid_hqng...",
  "dpop_private_key": "{JWK}",     // 续期必需
  "dpop_public_key": "{JWK}",
  "pkce_code_verifier": "...",
  "source": "local" | "import"
}
```

## 三条必须守住的口径

1. **额度单位是「积分」不是 token**：`balance_remain = 可用积分`（不是 0~100 的比例）。
2. **过期要主动续**：临时 AK/SK 只有 6 小时左右，靠 `expires_at` 提前续；
   续不上（refresh_token 失效）就标 `disabled`，不要让它继续被选中。
3. **零积分的号不要在选号队列里**：调用必被 402 打回，白白失败一次
   （`shared.select_account(min_balance=0)` + 探活后主动冷却）。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.codearts.core import client, endpoints, session
from admin.security import clean_display_name

logger = logging.getLogger("admin.platform.codearts.accounts")

PLATFORM = endpoints.PLATFORM

#: 额度快照多久算「新鲜」。超过就走后台刷新（列表接口只读快照）。
QUOTA_TTL = 60

#: 零积分账号冷却时长。积分不是每天重置（是买断/赠送的包），
#: 所以只需避免「每轮都被选中」，不必频繁重试。
QUOTA_COOL_SECONDS = 1800

#: 提前多少秒续期（留出时钟偏差与一次网络往返）
REFRESH_AHEAD_SECONDS = 300


# ---------------------------------------------------------------------------
# 凭证读写
# ---------------------------------------------------------------------------
def credentials_of(account: Account) -> dict:
    try:
        data = json.loads(account.auth_json or "{}")
    except (ValueError, TypeError):
        data = {}
    return data if isinstance(data, dict) else {}


def realm_of(account: Account) -> str:
    return str(credentials_of(account).get("realm") or "cn")


def uid_of(data: dict) -> str:
    return str(data.get("uid") or data.get("user_id") or "")


def name_of(data: dict) -> str:
    return str(data.get("user_name") or data.get("name") or "")


def _store(account: Account, data: dict) -> None:
    account.auth_json = json.dumps(data, ensure_ascii=False)
    account.uid = str(data.get("uid") or account.uid or "")
    account.platform = PLATFORM
    account.domain = str(data.get("realm") or "cn")
    if not account.name:
        account.name = clean_display_name(name_of(data)) or account.uid or "CodeArts"


def expires_at_ms(data: dict) -> int:
    try:
        return int(data.get("expires_at") or 0)
    except (TypeError, ValueError):
        return 0


def needs_refresh(data: dict) -> bool:
    """临时凭证是否该续期了。

    ⚠️ 用 `time.time()` 而不是 `datetime.utcnow().timestamp()`：
    后者把**naive UTC** 当成**本地时间**再转 epoch，在东八区会凭空多出
    8 小时 —— 于是「还有 1 小时到期」被算成「早就过期」，每个请求都去
    续一次，既慢又可能把 refresh_token 打废。
    """
    expires = expires_at_ms(data)
    if not expires:
        return False          # 没有到期信息就不猜，交给上游报错
    return expires / 1000 - time.time() < REFRESH_AHEAD_SECONDS


def _local_credential(uid: str = "") -> dict | None:
    """读本机客户端当前登录态，转成凭证字典（读不到返回 None）。

    ⚠️ 为什么必须先读它：**refresh_token 是一次性的**。客户端每小时自己
    续一次并轮转 token，我们库里那份随即作废。此时如果还拿库里的
    refresh_token 去续，上游直接回 `STS5.1806 invalid refresh token:
    'the refresh token has been used'` —— 于是一个**客户端活得好好的**账号
    被我们标成 disabled（实测 2026-11-04 就这样废掉一个号）。

    客户端把最新一份写在 `secretStorage` 里，所以「先读盘、后续期」才是
    正确顺序：盘上那份永远是当下有效的，只有它自己也快过期时才谈续期。
    """
    try:
        local = session.load_session()
    except session.SessionError as exc:
        logger.debug("读取本机 CodeArts 登录态失败 kind=%s: %s", exc.kind, exc.detail)
        return None
    except Exception:  # noqa: BLE001 - 客户端目录结构异常绝不能挡住调用
        logger.debug("读取本机 CodeArts 登录态异常", exc_info=True)
        return None
    if not local.access_key or not local.secret_key:
        return None
    if uid and local.uid and local.uid != uid:
        # 客户端已换号登录：盘上那份不属于这个账号，不能用
        return None
    credential = local.to_credential()
    credential["source"] = "local"
    return credential


def _adopt(account: Account, data: dict, db: Session) -> dict:
    """把凭证写回账号；若此前是被「续期失败」自动禁用的，顺手恢复。"""
    _store(account, data)
    if account.status == "disabled" and str(account.last_err_msg or "").startswith(
            ("续期失败", "凭证已过期")):
        # 只有「我们自己判死」的才恢复；人工禁用的保持原样
        account.status = "active"
        account.last_err_msg = ""
        account.last_upstream_error = None
        account.err_count = 0
        logger.info("CodeArts 账号已用本机登录态恢复 acc=%s", account.id)
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return data


def ensure_credential(db: Session, account: Account, *, force: bool = False) -> dict:
    """拿到一份**可用的** AK/SK/STS。

    顺序（这个顺序本身就是结论，别调）：

    1. 库里那份还有效、且不比盘上的旧 → 直接用；
    2. 库里那份要过期了（或 force）→ **先读盘**：客户端刚续过的话，
       盘上那份就是最新的，直接采纳，一次网络都不用发；
    3. 盘上也没有更新 → 才拿库里那份 refresh_token 去续；
    4. 续期被拒 → 再读一次盘（可能刚好续上了）；还是不行才标 disabled。
    """
    data = credentials_of(account)
    if not data.get("access_key") or not data.get("secret_key"):
        # 库里没有：本机客户端可能刚登录过，给一次机会再说「缺凭证」
        local = _local_credential()
        if local:
            return _adopt(account, local, db)
        account.status = "disabled"
        account.last_err_msg = "凭证缺少 AK/SK，需要重新导入"
        db.commit()
        raise client.UpstreamError("凭证缺少 AK/SK", kind="auth",
                                   status=401, code="no_credential")

    if not (force or needs_refresh(data)):
        return data

    uid = uid_of(data)
    local = _local_credential(uid)
    if local and expires_at_ms(local) > expires_at_ms(data):
        # 客户端比我们新：直接采纳（AK/SK/STS/refresh_token 整组替换）
        merged = dict(data)
        merged.update(local)
        return _adopt(account, merged, db)

    try:
        data = session.refresh_session(data)
    except session.SessionError as exc:
        # 网络问题不算「号坏了」：下次还能再试
        if exc.kind == "network":
            logger.warning("CodeArts 续期网络失败 acc=%s: %s", account.id, exc.detail)
            return credentials_of(account)
        # 续期被拒：再看一眼盘上（客户端可能刚刚自己续成功并轮转）
        local = _local_credential(uid)
        if local and not needs_refresh(local):
            logger.info("CodeArts 续期被拒，但本机登录态可用，改用它 acc=%s", account.id)
            merged = dict(data)
            merged.update(local)
            return _adopt(account, merged, db)
        account.status = "disabled"
        account.last_err_msg = ("续期失败：%s" % exc.message)[:250]
        account.last_upstream_error = str(exc.detail or "")[:2000]
        db.commit()
        raise client.UpstreamError(
            "凭证已过期且无法续期（%s），请重新登录客户端后再导入" % exc.message,
            kind="auth", status=401, code="refresh_dead",
            detail=str(exc.detail or "")) from exc
    return _adopt(account, data, db)


# ---------------------------------------------------------------------------
# 额度
# ---------------------------------------------------------------------------
def apply_quota(account: Account, quota: dict) -> None:
    """把额度写回统一列（列表 / 概览 / 选号都读这些列）。"""
    remain = int(quota.get("remain") or 0)
    total = int(quota.get("total") or 0)
    account.balance_remain = remain
    account.balance_total = total or remain
    account.credits_expiring_soon = int(quota.get("expiring") or 0)
    account.credits_synced_at = datetime.utcnow()
    account.last_sync_at = datetime.utcnow()
    snapshot = {
        "unit": "credits",
        "quota_kind": "balance",
        "remain": remain,
        "total": total,
        "used": int(quota.get("used") or 0),
        "expiring": int(quota.get("expiring") or 0),
        "basic_remain": int(quota.get("basic_remain") or 0),
        "basic_total": int(quota.get("basic_total") or 0),
        "bonus_remain": int(quota.get("bonus_remain") or 0),
        "tokens": int(quota.get("tokens") or 0),
        "requests": int(quota.get("requests") or 0),
        "package_name": str(quota.get("package_name") or ""),
        "spec_code": str(quota.get("spec_code") or ""),
        "start_date": str(quota.get("start_date") or ""),
        "end_date": str(quota.get("end_date") or ""),
    }
    # 模型数不在额度接口里：调用方探过就写它，没探就**沿用旧值**。
    # 漏了前半句 → 导入时明明拉到了 13 个模型，列表却显示 0。
    models = quota.get("models")
    if models is None:
        models = snapshot_of(account).get("models")
    if models is not None:
        snapshot["models"] = int(models or 0)
    account.credits_snapshot = json.dumps(snapshot, ensure_ascii=False)[:20000]


def snapshot_of(account: Account) -> dict:
    try:
        value = json.loads(account.credits_snapshot or "{}")
    except (ValueError, TypeError):
        value = {}
    return value if isinstance(value, dict) else {}


def quota_is_fresh(account: Account) -> bool:
    synced = getattr(account, "credits_synced_at", None)
    if synced is None:
        return False
    return (datetime.utcnow() - synced).total_seconds() < QUOTA_TTL


def sync_quota(db: Session, account: Account, *, with_models: bool = False) -> dict:
    """同步额度（可选带一次模型目录计数）。失败不抛，返回 `{ok: False}`。

    `with_models=False` 是**列表/批量**的默认：模型目录是池级的，
    每个号都拉一遍纯属浪费（这个坑 Loomy 那边实测把导入拖到 24 秒）。
    """
    try:
        data = ensure_credential(db, account)
    except client.UpstreamError as exc:
        return {"ok": False, "error": exc.to_dict()}
    try:
        quota = client.fetch_quota(data, account_id=int(account.id or 0))
    except client.UpstreamError as exc:
        account.last_err_msg = str(exc.message or "")[:250]
        account.last_upstream_error = json.dumps(exc.to_dict(), ensure_ascii=False)[:2000]
        if exc.kind in ("auth", "signature_invalid"):
            account.status = "disabled"
        elif exc.kind == "quota_exceeded":
            account.cool_until = datetime.utcnow() + timedelta(seconds=QUOTA_COOL_SECONDS)
            account.cool_kind = "quota"
        db.commit()
        return {"ok": False, "error": exc.to_dict()}

    if with_models:
        try:
            quota["models"] = len(client.fetch_models(data, account_id=int(account.id or 0)))
        except client.UpstreamError:
            quota.setdefault("models", 0)
    apply_quota(account, quota)
    # 0 积分的号：凭证是好的，但发对话必然 402。主动冷却，别让它排在选号队列前面。
    if int(account.balance_remain or 0) <= 0:
        account.cool_until = datetime.utcnow() + timedelta(seconds=QUOTA_COOL_SECONDS)
        account.cool_kind = "quota"
    else:
        # 有额度了就把「额度冷却」解除（回充/续包后立刻可用）
        if str(getattr(account, "cool_kind", "") or "") == "quota":
            account.cool_until = None
            account.cool_kind = ""
        if str(account.status or "") == "disabled":
            account.status = "active"
    db.commit()
    return {"ok": True, "quota": quota}


# ---------------------------------------------------------------------------
# 账号池
# ---------------------------------------------------------------------------
def accounts_for(db: Session, realm: str = "") -> list[Account]:
    query = db.query(Account).filter(Account.platform == PLATFORM)
    if realm:
        query = query.filter(Account.domain == realm)
    return query.order_by(Account.id.asc()).all()


def select(db: Session, realm: str = "", mark_picked: bool = False) -> Account | None:
    from admin.platform import shared

    account = shared.select_account(db, storage=PLATFORM, realm=realm,
                                    min_balance=0)
    if account is not None and mark_picked:
        account.last_picked_at = datetime.utcnow()
        db.commit()
    return account


def _upsert(db: Session, data: dict, *, name: str = "") -> tuple[Account, bool]:
    """按 uid 落库（存在则更新凭证，不存在则新建）。"""
    uid = uid_of(data)
    account = None
    if uid:
        account = (db.query(Account)
                   .filter(Account.platform == PLATFORM, Account.uid == uid)
                   .first())
    created = account is None
    if account is None:
        account = Account(platform=PLATFORM)
        if name:
            account.name = clean_display_name(name)
    elif name:
        account.name = clean_display_name(name)
    _store(account, data)
    if created:
        db.add(account)
        db.flush()
    return account, created


def import_record(db: Session, record: dict) -> dict:
    """导入一条记录（导出文件的单条 / 手工粘贴的对象）。"""
    if not isinstance(record, dict):
        raise ValueError("记录必须是对象")
    payload = record.get("credential") if isinstance(record.get("credential"), dict) else record
    # 兼容网关导出格式（字段用下划线，值在 credential 里）
    data = {
        "realm": str(payload.get("realm") or "cn"),
        "region": str(payload.get("region") or endpoints.REGION),
        "access_key": str(payload.get("access_key") or payload.get("accessKey") or ""),
        "secret_key": str(payload.get("secret_key") or payload.get("secretKey") or ""),
        "security_token": str(payload.get("security_token")
                              or payload.get("securityToken") or ""),
        "refresh_token": str(payload.get("refresh_token")
                             or payload.get("refreshToken") or ""),
        "expires_at": int(payload.get("expires_at") or payload.get("expiresAt") or 0),
        "uid": str(payload.get("uid") or ""),
        "user_name": str(payload.get("user_name") or payload.get("userName") or ""),
        "domain_id": str(payload.get("domain_id") or payload.get("domainId") or ""),
        "client_id": str(payload.get("client_id") or session.CLIENT_ID),
        "dpop_private_key": str(payload.get("dpop_private_key")
                                or payload.get("dpopPrivateKey") or ""),
        "dpop_public_key": str(payload.get("dpop_public_key")
                               or payload.get("dpopPublicKey") or ""),
        "pkce_code_verifier": str(payload.get("pkce_code_verifier")
                                  or payload.get("pkceCodeVerifier") or ""),
        "source": "import",
    }
    if not data["access_key"] or not data["secret_key"]:
        raise ValueError("缺少 access_key / secret_key（CodeArts 使用 AK/SK 签名）")

    account, created = _upsert(db, data, name=str(record.get("name") or ""))
    result = {"account_id": account.id, "uid": account.uid,
              "name": account.name, "created": created}
    try:
        sync = sync_quota(db, account)
        result["quota"] = sync.get("quota")
        result["quota_ok"] = bool(sync.get("ok"))
        if not sync.get("ok"):
            result["error"] = (sync.get("error") or {}).get("message")
    except Exception as exc:  # noqa: BLE001 - 额度失败不影响导入
        result["error"] = str(exc)[:160]
        db.rollback()
    return result


def import_local(db: Session) -> dict:
    """从本机客户端登录态导入（**主路径**：不需要用户粘贴任何东西）。"""
    try:
        local = session.load_session()
    except session.SessionError as exc:
        return {"ok": False, "imported": 0, "error": exc.message,
                "kind": exc.kind, "detail": exc.detail}
    data = local.to_credential()
    account, created = _upsert(db, data, name=local.user_name)
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        raise
    # 导入时**顺便拉一次模型目录计数**：模型目录是池级信息，
    # 只在导入/手动刷新时拉，列表接口不碰（否则 100 个号就是 100 次大 JSON）。
    sync = sync_quota(db, account, with_models=True)
    return {"ok": True, "imported": 1, "created": created,
            "account": {"id": account.id, "name": account.name,
                        "uid": account.uid,
                        "expires_at": local.expires_at,
                        "expires_in": local.expires_in},
            "quota": sync.get("quota"),
            "quota_ok": bool(sync.get("ok")),
            "error": (sync.get("error") or {}).get("message") if not sync.get("ok") else ""}


def import_batch(db: Session, text: str, *, probe: bool = True,
                 workers: int = 8, on_progress=None) -> dict:
    """批量导入（网关导出文件 / 手工粘贴的 JSON 数组 / JSONL）。

    只做**结构校验 + 落库**，探活交给调用方决定（`probe=False` 时更快）：
    每个号都要一次签名请求，几千个号排在请求线程里没意义，
    控制台本来就是「提交后台任务」。
    """
    import re

    items: list[dict] = []
    text = (text or "").strip()
    if not text:
        return {"ok": False, "added": 0, "updated": 0, "errors": ["内容为空"]}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            if isinstance(parsed.get("accounts"), list):
                items = [row for row in parsed["accounts"] if isinstance(row, dict)]
            elif isinstance(parsed.get("items"), list):
                items = [row for row in parsed["items"] if isinstance(row, dict)]
            else:
                items = [parsed]
        elif isinstance(parsed, list):
            items = [row for row in parsed if isinstance(row, dict)]
    except ValueError:
        for line in re.split(r"[\r\n]+", text):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                items.append(row)

    added = updated = 0
    errors: list[str] = []
    total = len(items)
    for index, item in enumerate(items, start=1):
        try:
            payload = item.get("credential") if isinstance(item.get("credential"), dict) else item
            data = {
                "realm": str(payload.get("realm") or "cn"),
                "region": str(payload.get("region") or endpoints.REGION),
                "access_key": str(payload.get("access_key") or payload.get("accessKey") or ""),
                "secret_key": str(payload.get("secret_key") or payload.get("secretKey") or ""),
                "security_token": str(payload.get("security_token")
                                      or payload.get("securityToken") or ""),
                "refresh_token": str(payload.get("refresh_token")
                                     or payload.get("refreshToken") or ""),
                "expires_at": int(payload.get("expires_at") or payload.get("expiresAt") or 0),
                "uid": str(payload.get("uid") or ""),
                "user_name": str(payload.get("user_name") or payload.get("userName") or ""),
                "domain_id": str(payload.get("domain_id") or ""),
                "client_id": str(payload.get("client_id") or session.CLIENT_ID),
                "dpop_private_key": str(payload.get("dpop_private_key") or ""),
                "dpop_public_key": str(payload.get("dpop_public_key") or ""),
                "pkce_code_verifier": str(payload.get("pkce_code_verifier") or ""),
                "source": "import",
            }
            if not data["access_key"] or not data["secret_key"]:
                errors.append("第 %d 条缺少 AK/SK" % index)
                continue
            _account, created = _upsert(db, data, name=str(item.get("name") or ""))
            if created:
                added += 1
            else:
                updated += 1
        except Exception as exc:  # noqa: BLE001 - 单条失败不影响整批
            errors.append("第 %d 条：%s" % (index, str(exc)[:120]))
            db.rollback()
        if on_progress and total:
            try:
                on_progress(index, total)
            except Exception:  # noqa: BLE001
                pass
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        errors.append("收尾提交失败：%s" % str(exc)[:160])
    return {"ok": True, "added": added, "updated": updated,
            "total": total, "errors": errors[:50]}


def export_accounts(db: Session, ids=None) -> dict:
    """导出（**含完整凭证**，可再次导入；请按密钥对待）。"""
    rows = accounts_for(db)
    if ids:
        wanted = {int(i) for i in ids}
        rows = [row for row in rows if int(row.id) in wanted]
    out = []
    for account in rows:
        data = credentials_of(account)
        out.append({
            "name": account.name,
            "uid": account.uid,
            "realm": data.get("realm") or "cn",
            "credential": data,
            "status": account.status,
            "balance_remain": int(account.balance_remain or 0),
            "balance_total": int(account.balance_total or 0),
        })
    return {"format": "mirarihub.codearts.v1", "platform": PLATFORM,
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "accounts": out}


def public_view(account: Account, *, quota: dict | None = None) -> dict:
    """给前端的视图（**不含 AK/SK**）。"""
    data = credentials_of(account)
    snapshot = snapshot_of(account)
    models = (quota or {}).get("models")
    if models is None:
        models = snapshot.get("models")
    expires = expires_at_ms(data)
    return {
        "id": account.id,
        "name": account.name,
        "uid": account.uid,
        "realm": data.get("realm") or "cn",
        "status": getattr(account, "status", "") or "",
        "balance_remain": int(getattr(account, "balance_remain", 0) or 0),
        "balance_total": int(getattr(account, "balance_total", 0) or 0),
        "expiring": int(getattr(account, "credits_expiring_soon", 0) or 0),
        "quota_kind": "credits",
        "quota_live": bool(quota),
        "models": int(models or 0),
        "package_name": str(snapshot.get("package_name") or ""),
        "spec_code": str(snapshot.get("spec_code") or ""),
        "period_end": str(snapshot.get("end_date") or ""),
        # 凭证到期（临时 AK/SK）：过期前会由 ensure_credential 自动续
        "credential_expires_at": expires or None,
        "has_refresh": bool(data.get("refresh_token")),
        "user_name": name_of(data),
        "created_at": account.created_at.isoformat() if account.created_at else None,
        "last_used_at": account.last_used_at.isoformat() if account.last_used_at else None,
        "last_sync_at": account.last_sync_at.isoformat() if account.last_sync_at else None,
        "cool_until": account.cool_until.isoformat()
        if getattr(account, "cool_until", None) else None,
        "cool_kind": str(getattr(account, "cool_kind", "") or ""),
        "last_err_msg": str(getattr(account, "last_err_msg", "") or ""),
    }


def summarize(rows: list[dict]) -> dict:
    active = sum(1 for row in rows if row.get("status") == "active")
    return {
        "total": len(rows),
        "active": active,
        "disabled": len(rows) - active,
        "balance_remain": sum(int(row.get("balance_remain") or 0) for row in rows),
        "balance_total": sum(int(row.get("balance_total") or 0) for row in rows),
        "expiring": sum(int(row.get("expiring") or 0) for row in rows),
    }
