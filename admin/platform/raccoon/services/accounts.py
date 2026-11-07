"""小浣熊凭证与账号池。

## 凭证形态

本项目接受三种输入，统一归一成 `auth_json`：

**① agent2api 导出（主路径）** —— `agent2api-raccoon-packNN` 里的单账号 JSON：

```json
{"provider": "raccoon", "format": "agent2api-account",
 "uid": "pu-01a1...", "accessToken": "eyJ...", "refreshToken": "eyJ...",
 "expiresAt": 1795980176000.0, "name": "账号 pu-01a1...", ...}
```

**② 裸 JWT** —— 用户直接贴一串 `eyJ...`。

**③ 小浣熊原生字段** —— `{"token": ..., "uid": ...}` / `{"accessToken": ...}`。

落库后 `auth_json` 固定为：

```json
{"realm": "cn", "token": "<accessToken>", "refresh_token": "<refreshToken>",
 "uid": "pu-01a1...", "phone": "", "expires_at": 1795980176000,
 "provider": "raccoon", "agent2api": {...}}
```

## 幂等

按 `uid` upsert；`uid` 缺失时退化为「JWT 的 sub」——小浣熊 JWT 的 `sub`
就是 `pu-...`，所以即使导入的是裸 JWT 也能正确去重。

## 额度口径

`balance_remain = available_points`（可用总额）
`credits_expiring_soon = daily_points`（当日额度，每日重置会过期）
`balance_total` 存累计消耗（本网关记账），**不是**剩余额度。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from admin.models import Account
from admin.security import clean_display_name
from admin.platform.raccoon.core import client, endpoints

logger = logging.getLogger("admin.platform.raccoon.accounts")
PLATFORM = endpoints.PLATFORM
QUOTA_TTL = 60

#: 这些键被认为是「这条记录本身就是凭证」
_TOKEN_KEYS = ("accessToken", "access_token", "token", "session")
_REFRESH_KEYS = ("refreshToken", "refresh_token")


# ---------------------------------------------------------------------------
# 时间归一
# ---------------------------------------------------------------------------
def now_ms_utc() -> int:
    """当前 UTC 时间的毫秒时间戳。

    ⚠️ **不要写 `datetime.utcnow().timestamp()`** —— `utcnow()` 返回的是
    「naive 的 UTC 时间」，而 `.timestamp()` 会把它当**本地时间**解释。
    在东八区，这个写法会凭空多出 8 小时：

        now_ms_utc()                -> 1793636539000
        datetime.utcnow().timestamp() -> 1793665339000   # 差 +8h

    后果：`ensure_token` 的「距到期 < 6h 才续」被推迟 8 小时（该续不续，
    吃 401）；`prune_expired` 同理偏松，过期号不会被清扫。
    用 `time.time()` 才是干净的 UTC 秒。
    """
    return int(time.time() * 1000)


def to_ms(value, *, relative_ok: bool = False) -> int:
    """把各种形态的时间归一成**毫秒**时间戳；无法识别返回 0。

    实测量级：
        < 1e11      秒级绝对时间戳（如 1795980176）
        < 1e14      毫秒级绝对时间戳（如 1795980176000）
        relative_ok 时更小的值当**相对秒数**（如 `expires_in=3600`）

    统一归一是必须的：`expiresAt` 有的导出是秒、有的是毫秒；
    `expires_in` 是相对秒。混着用会把账号判成 1970 年过期。
    """
    if value is None or value == "":
        return 0
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0
    if num <= 0:
        return 0
    if num < 1e11:
        if relative_ok and num < 1e8:
            # 相对秒数（expires_in）
            return now_ms_utc() + int(num * 1000)
        # 秒级绝对时间戳
        return int(num * 1000)
    if num < 1e14:
        return int(num)
    return int(num / 1000)  # 微秒级兜底


def exp_ms_of(token: str) -> int:
    """JWT 的 `exp` → 毫秒时间戳（数据库与前端都用毫秒）。"""
    exp = jwt_claims(token).get("exp")
    try:
        return int(float(exp) * 1000)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# JWT 解析（只为拿 sub / exp，不做验签 —— 验签是上游的事）
# ---------------------------------------------------------------------------
def _b64url(chunk: str) -> bytes:
    pad = "=" * (-len(chunk) % 4)
    return base64.urlsafe_b64decode(chunk + pad)


def jwt_claims(token: str) -> dict:
    """尽力解析 JWT payload；解析不了返回空 dict（不抛异常）。"""
    token = str(token or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        claims = json.loads(_b64url(parts[1]))
    except Exception:  # noqa: BLE001 - 纯尽力而为
        return {}
    return claims if isinstance(claims, dict) else {}


def sub_of(token: str) -> str:
    """JWT 的 `sub`（小浣熊就是 `pu-...` 账号 id）。"""
    return str(jwt_claims(token).get("sub") or "")


# ---------------------------------------------------------------------------
# 凭证归一
# ---------------------------------------------------------------------------
def credentials_of(account: Account) -> dict:
    try:
        data = json.loads(account.auth_json or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def token_of(data: dict) -> str:
    for key in _TOKEN_KEYS:
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def refresh_token_of(data: dict) -> str:
    for key in _REFRESH_KEYS:
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def normalize_record(record: dict) -> dict:
    """把任意输入归一成 `auth_json` 的形状。抛 `ValueError` 表示不可用。"""
    if isinstance(record, str):
        record = {"accessToken": record}
    if not isinstance(record, dict):
        raise ValueError("凭证必须是对象")

    # agent2api 会把密文塞在 pluginSecret 里；两处取其一即可
    secret = record.get("pluginSecret") if isinstance(record.get("pluginSecret"), dict) else {}
    merged = dict(secret)
    merged.update({k: v for k, v in record.items() if v not in (None, "")})

    token = token_of(merged)
    if not token:
        raise ValueError("凭证缺少 accessToken/token")
    if len(token) < 20:
        raise ValueError("token 长度异常，疑似占位符而不是真实凭证")

    uid = str(record.get("uid") or record.get("userId") or merged.get("userId") or "")
    if not uid:
        uid = sub_of(token)
    refresh = refresh_token_of(merged) or token

    expires = record.get("expiresAt") or merged.get("expiresAt")
    # 秒/毫秒必须归一：同一批导出有的是 `1795980176`（秒），有的是
    # `1795980176000.0`（毫秒）。不归一的话秒级会被当成 1970 年，
    # 账号刚落库就会被判过期、或每次请求都触发续期。
    expires_at = to_ms(expires) or exp_ms_of(token)

    # 只保留 agent2api 的轻量元信息，**不要把整份记录再塞一遍**（里面全是重复 JWT）
    meta = {k: record.get(k) for k in
            ("provider", "format", "source", "priority", "maxConcurrent", "addedAt")
            if record.get(k) is not None}

    name = str(record.get("name") or "").strip()
    if name.startswith("账号 "):
        name = name[3:].strip()
    if not name:
        name = "小浣熊 " + (uid[-4:] if len(uid) >= 4 else uid or "account")

    # 长度护栏：库列是 VARCHAR(120)/(8)，超长在 MySQL 严格模式会写失败
    # （异常字符串还会带出绑定参数，形成凭证泄漏面）。这里提前截断/规范化。
    uid = uid.strip()[:120]
    realm = str(record.get("realm") or "cn").strip().lower()[:8] or "cn"
    if realm not in ("cn",):
        realm = "cn"  # 目前只有国内站，未知区域回落而不是写脏值

    return {
        "realm": realm,
        "token": token,
        "refresh_token": refresh,
        "uid": uid,
        "phone": str(record.get("phone") or "")[:32],
        "expires_at": expires_at,
        "provider": "raccoon",
        "agent2api": meta,
        "_name": name[:120],
    }


# ---------------------------------------------------------------------------
# 账号查询
# ---------------------------------------------------------------------------
def realm_of(account: Account) -> str:
    return str(getattr(account, "domain", "") or "cn")


def accounts_for(db: Session, realm: str = "") -> list[Account]:
    query = db.query(Account).filter(Account.platform == PLATFORM)
    if realm:
        query = query.filter(Account.domain == realm)
    return query.order_by(Account.id.asc()).all()


# 注：这里**故意不提供本地 `select()`**。
# 选号必须走框架的 `shared.select_account()` —— 它带在途租约（POOL）、
# Key 绑定、模型可用性、冷却与降级等一整套闸门。本地再实现一个
# 「遍历取第一个 active」的版本，一旦被误用就等于绕过号池闸门，
# 会把同一账号并发打爆。早期版本有过这样一个零调用点的 `select()`，
# 已删除以免误用。


def ensure_token(db: Session, account: Account) -> dict:
    """确保账号有可用 token；临期时尝试自续一次。

    小浣熊有 `POST /auth/v1/refresh`，但实测这批票据 refreshToken 与
    accessToken 同值，所以续期只是「同一张票换一张新票」——能延 exp，
    不能救一个被吊销的会话。失败时**不抛异常**，交给上游 401 去判定，
    避免本地时钟误差把好号误杀。
    """
    data = credentials_of(account)
    token = token_of(data)
    if not token:
        raise client.UpstreamError(
            "账号缺少 token，请重新导入凭证", status=401, kind="auth")

    expires_at = to_ms(data.get("expires_at")) or exp_ms_of(token)
    now_ms = now_ms_utc()
    # 距到期不足 6 小时才续，避免无谓请求
    if expires_at and expires_at - now_ms < 6 * 3600 * 1000:
        refresh = refresh_token_of(data)
        if refresh:
            try:
                fresh = client.refresh_credentials(refresh, realm_of(account), account.id)
                if fresh.get("access_token"):
                    data["token"] = fresh["access_token"]
                    data["refresh_token"] = fresh.get("refresh_token") or refresh
                    # `expires` / `expires_in` 可能是**相对秒数**，也可能是绝对
                    # 时间戳。统一交给 to_ms 归一，否则 `expires_in=3600` 会被
                    # 存成 1970 年，导致每次请求都续期。
                    fresh_exp = to_ms(fresh.get("expires"),
                                      relative_ok=True) or exp_ms_of(data["token"])
                    data["expires_at"] = fresh_exp or expires_at
                    account.auth_json = json.dumps(data, ensure_ascii=False)
                    db.commit()
                    logger.info("小浣熊账号 %s 凭证已续期", account.id)
            except client.UpstreamError as exc:
                logger.debug("小浣熊账号 %s 续期失败：%s", account.id, exc.message)
    return data


# ---------------------------------------------------------------------------
# 额度写回
# ---------------------------------------------------------------------------
def apply_quota(account: Account, quota: dict) -> None:
    """把额度写回统一列。

    ## 口径（实测 2026-11-03）

        GET /api/web/points/v1/balance
        → {"available_points": 4112, "daily_points": 836,
           "reward_points": 3276, "monthly_points": 0, ...}

    * `balance_remain = available_points`（可用总额，含当日额度）
    * `credits_expiring_soon = daily_points`（每日重置，属会过期额度）
    * `balance_total` 存**可用总额快照**（与 remain 同源）

    `balance_total` 与 `balance_remain` 在此同值不是笔误：上游只给一个
    「可用」数，没有「总额」概念（不像 WorkBuddy 有配额上限）。
    区分它们需要另外的口径，硬编一个假的上限更糟。
    `summarize()` 因此也只把它们各自求和，不宣称「总额 - 剩余」。
    """
    available = int(quota.get("available") or 0)
    daily = int(quota.get("daily_balance") or 0)
    account.balance_remain = available
    account.balance_total = available
    account.credits_expiring_soon = daily
    account.credits_synced_at = datetime.utcnow()
    account.last_sync_at = datetime.utcnow()
    account.credits_snapshot = json.dumps({
        "unit": "points",
        "quota_kind": "balance",
        "available": available,
        "daily_balance": daily,
        "monthly_balance": int(quota.get("monthly_balance") or 0),
        "reward": int(quota.get("reward") or 0),
        "models": int(quota.get("models") or 0),
    }, ensure_ascii=False)[:20000]


def quota_is_fresh(account: Account) -> bool:
    synced = getattr(account, "credits_synced_at", None)
    if synced is None:
        return False
    return (datetime.utcnow() - synced).total_seconds() < QUOTA_TTL


def sync_quota(db: Session, account: Account, *, mark_disabled: bool = True) -> dict:
    """探活 + 刷新额度。返回 `{"ok":..., "quota":...}`。

    `mark_disabled=False` 时**不**把鉴权失败的账号标成 disabled。
    为什么要这个开关：账号池列表页会对每个缓存过期的账号调一次
    `sync_quota`，那是**被动刷新**；一次网络抖动或上游临时 403
    就把整页账号标成永久禁用，属于过度反应。
    被动刷新只上报失败，禁用留给主动的「刷新额度 / 保活」任务。

    ## 「计费接口 403」不等于「凭证失效」

    实测（2026-11-03，50 个真实账号全部如此）：

        GET /api/web/llm/v2/model_catalog   -> 200  （能列出模型）
        GET /api/web/points/v1/balance      -> 403 + 空 body

    也就是说 **token 完全有效**，只是积分接口对该账号/该时段不开放
    （子账号权限、风控限流、或上游灰度）。早期实现把它当 `kind=auth`
    直接 `status="disabled"`，会把整批好号一次性打进禁用池 —— 账号
    还能调模型，却再也选不中。

    所以这里的判定是：**先用目录接口确认凭证是否真的失效**，
    只有目录也拿不到时才禁用。
    """
    try:
        data = ensure_token(db, account)
    except client.UpstreamError as exc:
        return {"ok": False, "error": exc.to_dict()}
    token = token_of(data)
    realm = realm_of(account)
    quota: dict = {}

    # 先探目录：它是「凭证是否有效」的权威判据
    models_count = 0
    try:
        models_count = len(client.fetch_models(token, realm, account.id))
    except client.UpstreamError as exc:
        if exc.kind == "auth" and mark_disabled:
            account.status = "disabled"
            account.last_err_msg = "凭证已失效（目录接口拒绝）：%s" % exc.message[:120]
            account.last_err_at = datetime.utcnow()
            db.commit()
        return {"ok": False, "error": exc.to_dict()}

    # ⚠️ 目录「200 但空」不能当作凭证有效。
    # 实测：死 token 也可能拿到一个 200 + 空 categories 的目录响应
    # （上游对失效会话退回公共目录）。若只看「没抛异常」，
    # 死号会被判成有效、停在 active，然后被反复选中：
    # chat 的 401 只冷却 600 秒、并不置 disabled，于是它会一直转世。
    # 所以「目录为空」按「判据不可用」处理：不禁用、也不宣称有效。
    if not models_count:
        logger.info("小浣熊账号 %s 目录为空，凭证有效性无法判定", account.id)
        return {"ok": False, "partial": True, "models": 0,
                "note": "目录返回空清单，无法判定凭证有效性（既不禁用也不标有效）"}
    quota["models"] = models_count

    # 再探积分：失败只降级，不判死
    try:
        quota.update(client.fetch_points(token, realm, account.id))
    except client.UpstreamError as exc:
        # 凭证已确认有效，这里只是额度读不到。保留上一次快照，不覆盖成 0。
        logger.info("小浣熊账号 %s 积分接口不可用（%s），保留上次快照",
                    account.id, exc.kind)
        account.last_sync_at = datetime.utcnow()
        account.credits_synced_at = datetime.utcnow()
        db.commit()
        return {"ok": False, "partial": True, "models": models_count,
                "error": exc.to_dict(),
                "note": "凭证有效（目录可读），但积分接口不可用；余额沿用上次快照"}

    apply_quota(account, quota)
    # 成功一次就把上一次的错误清掉。
    # 否则账号池会一直挂着一条早已过期的 `last_err_msg`
    # （实测：换回可用凭证后，行上仍显示「账号凭证缺少 auth」），
    # 让人以为号还是坏的。
    account.last_err_msg = ""
    account.last_err_at = None
    db.commit()
    return {"ok": True, "quota": quota}


# ---------------------------------------------------------------------------
# 视图
# ---------------------------------------------------------------------------
def public_view(account: Account, *, quota: dict | None = None) -> dict:
    """给前端的视图（**不含 token**）。"""
    data = credentials_of(account)
    snapshot = {}
    try:
        snapshot = json.loads(account.credits_snapshot or "{}") or {}
    except ValueError:
        snapshot = {}
    token = token_of(data)
    claims = jwt_claims(token)
    return {
        "id": account.id,
        "name": account.name,
        "uid": account.uid,
        "realm": realm_of(account),
        "status": getattr(account, "status", "") or "",
        "balance_remain": int(getattr(account, "balance_remain", 0) or 0),
        "balance_total": int(getattr(account, "balance_total", 0) or 0),
        "daily_balance": int(getattr(account, "credits_expiring_soon", 0) or 0),
        "quota_kind": "balance",
        "quota_live": bool(quota) or bool(snapshot),
        "models": int((quota or {}).get("models") or snapshot.get("models") or 0),
        "phone": str(data.get("phone") or ""),
        "has_refresh": bool(refresh_token_of(data)),
        "expires_at": int(data.get("expires_at") or 0),
        "jwt_sid": str(claims.get("sid") or ""),
        "last_sync_at": account.last_sync_at.isoformat()
        if account.last_sync_at else None,
    }


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    active = sum(1 for r in rows if r.get("status") == "active")
    return {
        "total": total,
        "active": active,
        "disabled": total - active,
        "balance_remain": sum(int(r.get("balance_remain") or 0) for r in rows),
        "balance_total": sum(int(r.get("balance_total") or 0) for r in rows),
        "quota_kind": "metered",
    }


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------
def import_record(db: Session, record: dict) -> tuple[Account, bool]:
    """导入一条凭证（按 uid 幂等）。返回 `(账号, 是否新建)`。

    ## uid 与 JWT `sub` 必须一致

    这条校验不是形式主义：uid 是 upsert 的唯一键，如果允许「uid 写 A、
    token 属于 B」，一次误粘贴就会**把别人的账号凭证覆盖到 A 上**，
    而且把原本被禁用的 A 无条件改回 `active`。管理员误操作、
    批量粘贴串行，都是真实场景。

    ## 去重键不能用 `token[:16]`

    任何 HS256 JWT 的前 16 个字符恒为 `eyJhbGciOiJIUzI1`
    （header 是固定的 `{"alg":"HS256","typ":"JWT"}`），
    所以拿前 16 位当指纹等于**所有账号都撞同一个键**。
    改用整串 sha256。
    """
    norm = normalize_record(record)
    uid = norm.get("uid") or ""
    token = norm.get("token") or ""
    sub = sub_of(token)
    if uid and sub and uid != sub:
        raise ValueError(
            "uid 与凭证 sub 不一致（uid=%s sub=%s），疑似粘贴串号"
            % (uid[:12], sub[:12]))
    # token 里有 sub 就以它为准（比手填的 uid 更可信）
    if not uid and sub:
        uid = sub
        norm["uid"] = sub

    name = norm.pop("_name", "") or uid or "小浣熊"
    realm = norm.get("realm") or "cn"

    existing = None
    if uid:
        existing = db.query(Account).filter(Account.platform == PLATFORM,
                                            Account.uid == uid).first()
    if existing is None:
        # 没有 uid 时用**整串 sha256** 做去重键（不是前 16 位）
        probe = hashlib.sha256(token.encode("utf-8")).hexdigest()
        for row in accounts_for(db, realm):
            other = token_of(credentials_of(row))
            if other and hashlib.sha256(other.encode("utf-8")).hexdigest() == probe:
                existing = row
                break

    created = existing is None
    if created:
        existing = Account(platform=PLATFORM, uid=uid, status="active",
                           domain=realm)
        db.add(existing)

    existing.name = clean_display_name(str(name or existing.name or uid or "小浣熊"))
    existing.uid = uid or existing.uid or ""
    existing.domain = realm
    # 合并旧值：agent2api 元信息与手动补的字段不因重导而丢
    old = credentials_of(existing) if not created else {}
    merged = dict(old)
    merged.update({k: v for k, v in norm.items() if v not in ("", None)})
    existing.auth_json = json.dumps(merged, ensure_ascii=False)
    # 只在「凭证确实换了」时才把禁用改回启用，避免一次重导就洗掉禁用状态
    if created or (token and token != token_of(old)):
        existing.status = "active"
    existing.last_sync_at = datetime.utcnow()
    db.commit()
    db.refresh(existing)
    # 入库后自动补刷积分（后台线程）：扫码/导入落库只写凭证，余额列
    # 原来保持 0，用户进面板还得手动点一次「刷新余额」。与 WorkBuddy
    # 导入补刷同语义；重复导入（凭证未变）也排一次，成本可忽略。
    try:
        from admin.platform import shared as _shared
        _shared.schedule_import_refresh(PLATFORM, [existing.id])
    except Exception:  # noqa: BLE001 - 补刷排队失败不影响导入结果
        pass
    return existing, created


def _prefetch_probes(accounts: list[Account], *, workers: int = 16) -> dict[int, dict]:
    """并发预取探活的**纯网络段**（目录 + 积分），不碰数据库 Session。

    为什么：`import_many` 逐条串行 `sync_quota`，每号要等两次上游往返
    （目录 + 积分），几百个号就是几分钟 —— 与 workbuddy 批量导入同款病根。
    网络段并发化（默认 16 路）后，判定与落库仍串行走 sync_quota 同款语义。

    返回（按 account.id）：
      {"kind": "ok", "models": N, "quota": {...}}        # 目录有货 + 积分到手
      {"kind": "partial", "models": N, "error": {...}}   # 目录 ok、积分挂
      {"kind": "empty"}                                  # 目录 200 但空
      {"kind": "other", "error": {...}}                  # 目录非鉴权失败
    拿不到 token / 意外异常的账号**不进表** —— apply 阶段回退串行
    `sync_quota`（它会 ensure_token 刷新过期凭证后再探，语义最全）。
    """
    from concurrent.futures import ThreadPoolExecutor

    def _one(account: Account):
        aid = int(account.id or 0)
        try:
            data = credentials_of(account)
            token = token_of(data)
            if not token:
                return aid, None
            realm = realm_of(account)
            models_count = len(client.fetch_models(token, realm, aid))
        except client.UpstreamError as exc:
            if exc.kind == "auth":
                # 不能凭这个就禁用：token 可能只是过期了，串行兜底会先刷新。
                return aid, {"kind": "refresh"}
            return aid, {"kind": "other", "error": exc.to_dict()}
        except Exception:
            return aid, None
        if not models_count:
            return aid, {"kind": "empty"}
        try:
            quota = client.fetch_points(token, realm, aid)
            return aid, {"kind": "ok", "models": models_count, "quota": quota}
        except client.UpstreamError as exc:
            # 凭证已确认有效，这里只是额度读不到：保留上次快照（partial 语义）。
            return aid, {"kind": "partial", "models": models_count,
                         "error": exc.to_dict()}
        except Exception:
            return aid, {"kind": "partial", "models": models_count,
                         "error": {"kind": "unknown", "message": "积分接口异常"}}

    out: dict[int, dict] = {}
    if not accounts:
        return out
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(accounts)))) as pool:
        for aid, result in pool.map(_one, accounts):
            if result is not None:
                out[aid] = result
    return out


def import_many(db: Session, records: list[dict]) -> dict:
    """批量导入。逐条独立，一条失败不影响其它。

    节奏改成了两段（2026-11-06，用户反馈「其它平台也一样速度优化一下」）：

      1. **落库段串行**：`import_record` 逐条幂等入库（它自己 commit）；
      2. **探活段并发**：`_prefetch_probes` 16 路预取目录 + 积分（纯网络，
         不碰 Session），判定与落库仍按 `sync_quota` 的原语义串行收尾；
         预取拿不准的（token 过期 / 意外异常）回退串行 `sync_quota`，
         它会先刷新凭证再探，不会把「只是过期」的号误判成失效。

    导入完成后会**同步模型目录**：`/v1/models` 聚合走的是 `model_configs`
    表（`models_router.build_payload`），不同步的话客户端会看到一个空清单，
    而实际调用却通 —— 这种「能调但列不出来」最容易被误判成接入失败。

    ## 「探活失败」不等于「导入失败」

    探活分两段：目录（判凭证）与积分（判余额）。实测上游的积分接口
    对整批账号返回 403，而目录接口正常 —— 这种账号**完全可以调模型**。
    所以 `partial=True` 只记提示，不算导入失败，更不能回退已入库的记录。
    """
    added = updated = 0
    errors: list[str] = []
    notes: list[str] = []

    # ---- 1) 落库（串行；一条失败不影响其它） ----
    touched: list[Account] = []
    for idx, rec in enumerate(records, 1):
        try:
            account, created = import_record(db, rec)
            if created:
                added += 1
            else:
                updated += 1
            touched.append(account)
        except ValueError as exc:
            errors.append("第 %d 条：%s" % (idx, exc))
        except client.UpstreamError as exc:
            errors.append("第 %d 条：%s" % (idx, exc.message))
        except Exception as exc:  # noqa: BLE001 - 单条异常不许打断整批
            # ⚠️ 两个坑一起修：
            #   ① **必须 rollback**：DB 级错误后 Session 被毒化，后续每条都会
            #      抛 `PendingRollbackError`，「单条失败不影响其它条」就不成立，
            #      而且真实原因被这个连带错误掩盖。
            #   ② **不能把 str(exc) 回给前端**：SQLAlchemy 的异常字符串带完整
            #      绑定参数（`[parameters: (..., '{"token": "eyJ...真实签名"}')]`），
            #      实测会在响应体里泄漏真实 JWT。这里只回异常类型，
            #      详情留在服务端日志（日志侧同样做了脱敏）。
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.warning("小浣熊导入第 %d 条失败：%s", idx, type(exc).__name__)
            errors.append("第 %d 条：导入失败（%s），详情见服务端日志"
                          % (idx, type(exc).__name__))

    # ---- 2) 探活：网络段并发预取，判定/落库串行收尾 ----
    probed = _prefetch_probes(touched)
    for account in touched:
        result = probed.get(int(account.id or 0))
        if result is None or result.get("kind") == "refresh":
            # 拿不到 token / 意外异常 / 目录鉴权拒绝：走原串行路径 ——
            # sync_quota 会 ensure_token 刷新过期凭证后再探，不会误判。
            result = sync_quota(db, account)
            if result.get("ok"):
                pass
            elif result.get("partial"):
                if len(notes) < 3:
                    notes.append("账号 %s：%s" % (account.id, result.get("note") or "额度暂不可读"))
            else:
                err = (result.get("error") or {}).get("message") or "探活失败"
                errors.append("账号 %s：%s" % (account.id, err))
            continue
        kind = result.get("kind")
        if kind == "ok":
            apply_quota(account, result.get("quota") or {})
            # 成功一次就把上一次的错误清掉，否则账号池会一直挂着
            # 早已过期的 last_err_msg 让人以为号还是坏的。
            account.last_err_msg = ""
            account.last_err_at = None
            db.commit()
        elif kind == "partial":
            # 凭证有效（目录可读），积分读不到：保留上次快照，不覆盖成 0。
            account.last_sync_at = datetime.utcnow()
            account.credits_synced_at = datetime.utcnow()
            db.commit()
            if len(notes) < 3:
                notes.append("账号 %s：凭证有效（目录可读），积分接口暂不可用；余额沿用上次快照"
                             % account.id)
        elif kind == "empty":
            # 目录「200 但空」不能当作凭证有效，也不禁用（判据不可用）。
            logger.info("小浣熊账号 %s 目录为空，凭证有效性无法判定", account.id)
            if len(notes) < 3:
                notes.append("账号 %s：目录返回空清单，无法判定凭证有效性" % account.id)
        else:  # "other"：目录非鉴权失败，不动状态，只记错误
            err = (result.get("error") or {}).get("message") or "探活失败"
            errors.append("账号 %s：%s" % (account.id, err))

    result = {"added": added, "skipped": updated, "errors": errors,
              "notes": notes}
    try:
        result["model_sync"] = sync_catalog(db)
    except Exception as exc:  # noqa: BLE001 - 目录同步失败不影响入库事实
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning("小浣熊模型目录同步失败：%s", type(exc).__name__)
        result["model_sync"] = {"ok": False,
                                "summary": "目录同步失败（%s）" % type(exc).__name__}
    return result


def sync_catalog(db: Session) -> dict:
    """把上游 `/model_catalog` 同步进 `model_configs`，让 `/v1/models` 能看到。

    复用框架的 `_do_sync_models`（它按平台分派到 `Gateway.list_models()`），
    这样 admin 侧一行不用改 —— 平台知识仍然只在本模块里。
    """
    from admin.routers.models import _do_sync_models, _invalidate_routing_cache

    if not accounts_for(db):
        return {"attempted": False, "ok": False, "summary": "没有账号"}
    db.commit()  # _do_sync_models 会另开查询，未提交的账号它看不见
    try:
        detail = _do_sync_models(db, None, PLATFORM) or {}
        _invalidate_routing_cache()
    except Exception as exc:  # noqa: BLE001
        # `str(exc)` 会带出 SQLAlchemy 的绑定参数（内含明文 JWT），
        # 而这个 summary 会直接回给 `/api/raccoon/import` 的调用方。
        # 只回异常类型，详情留在服务端日志（日志里同样只记类型）。
        logger.warning("小浣熊模型同步异常：%s", type(exc).__name__)
        return {"attempted": True, "ok": False,
                "summary": "模型同步失败（%s）" % type(exc).__name__}
    added = int(detail.get("added") or 0)
    total = int(detail.get("total_fetched") or 0)
    return {
        "attempted": True,
        "ok": total > 0,
        "added": added,
        "total_fetched": total,
        "summary": "同步 %d 个模型（新增 %d）" % (total, added),
        "detail": detail,
    }


def export_records(db: Session, ids: list[int] | None = None) -> list[dict]:
    """导出成 agent2api 兼容格式（可回灌、可备份）。"""
    query = db.query(Account).filter(Account.platform == PLATFORM)
    if ids:
        query = query.filter(Account.id.in_(ids))
    out = []
    for account in query.order_by(Account.id.asc()).all():
        data = credentials_of(account)
        token = token_of(data)
        if not token:
            continue
        out.append({
            "format": "agent2api-account",
            "provider": "raccoon",
            "source": "export",
            "uid": account.uid or sub_of(token),
            "name": account.name,
            "accessToken": token,
            "refreshToken": refresh_token_of(data) or token,
            "expiresAt": int(data.get("expires_at") or exp_ms_of(token) or 0),
            "priority": (data.get("agent2api") or {}).get("priority"),
        })
    return out


def prune_expired(db: Session, *, days: int = 0) -> dict:
    """把已过期的账号标成 disabled（不删除，留证据）。"""
    # 用 now_ms_utc()，不要用 `datetime.utcnow().timestamp()`
    # （后者在东八区会偏 +8h，导致过期号永远清不掉）
    now_ms = now_ms_utc() + days * 86400 * 1000
    marked = 0
    for account in accounts_for(db):
        data = credentials_of(account)
        expires_at = to_ms(data.get("expires_at")) or exp_ms_of(token_of(data))
        if expires_at and expires_at < now_ms and account.status == "active":
            account.status = "disabled"
            account.last_err_msg = "JWT 已过期，需回官方客户端重新登录"
            account.last_err_at = datetime.utcnow()
            marked += 1
    if marked:
        db.commit()
    return {"marked": marked}


def import_batch(db: Session, text: str) -> dict:
    """从备份文本（JSONL / JSON 数组）批量导入小浣熊账号。

    与 `import_many` 的区别只在**入口形态**：
    控制台「导入账号」把文件原文整段交过来（不预先解析），
    这里负责拆记录、逐条入库，并返回与其它平台一致的统计字段
    （`added / updated / failed / total / errors`），
    这样 `admin/routers/accounts.py::import_accounts_async` 不用为
    每个平台写一套汇总逻辑。

    认的形状：
      * 本网关导出的 `agent2api-account`（含 accessToken / uid）
      * 手机版备份 `{"realm","token","uid","phone","expires_at"}`
      * 裸 JWT（一行一个）
    """
    import json as _json

    from admin.platform.raccoon.services import scan as _scan

    raw = (text or "").strip().lstrip("\ufeff")
    if not raw:
        return {"ok": False, "added": 0, "updated": 0, "failed": 0,
                "total": 0, "errors": ["没有提供任何凭证"]}

    records: list = []
    try:
        parsed = _json.loads(raw)
    except ValueError:
        parsed = None
    if isinstance(parsed, list):
        records = [x for x in parsed if isinstance(x, (dict, str))]
    elif isinstance(parsed, dict):
        inner = parsed.get("accounts")
        if isinstance(inner, list):
            records = [x for x in inner if isinstance(x, (dict, str))]
        else:
            records = [parsed]
    if not records:
        for line in raw.splitlines():
            line = line.strip().rstrip(",")
            if not line:
                continue
            try:
                item = _json.loads(line)
            except ValueError:
                item = line  # 裸 JWT
            if isinstance(item, (dict, str)):
                records.append(item)

    if not records:
        return {"ok": False, "added": 0, "updated": 0, "failed": 0,
                "total": 0, "errors": ["没有解析出任何凭证（支持 JSON / JSONL / 裸 JWT）"]}

    result = import_many(db, records)
    added = int(result.get("added") or 0)
    updated = int(result.get("skipped") or 0)
    errors = list(result.get("errors") or [])
    return {
        "ok": not errors,
        "added": added,
        "updated": updated,
        "failed": len(errors),
        "total": len(records),
        "errors": errors[:50],
        # 保留原字段，前端/日志仍能读到
        "notes": result.get("notes") or [],
        "model_sync": result.get("model_sync"),
        "_scan_hint": ([str(p) for p in _scan.auth_file_candidates()]
                       if not added and not updated else None),
    }
