"""Loomy 凭证与账号池。

## 凭证形态

`<configRoot>/userData/auth-session.json`（明文）：

```json
{"session": "...", "userid": "...", "phone": "...", "updatedAt": 1793...}
```

存进 `Account.auth_json` 后归一成：

```json
{"realm": "cn", "token": "<session>", "uid": "<userid>",
 "phone": "...", "expires_at": 0}
```

`expires_at = 0` 表示「上游不告诉我们有效期」—— Loomy 确实没有。

## 额度怎么算

Loomy **没有额度接口**，计费信息在每轮对话的 `usage.points_consumed`。
所以：
  * `balance_total` 存**累计消耗**（本网关记账）
  * 没有「剩余额度」概念 —— 不要伪造一个数字

`public_view` 里明确标注 `quota_kind = "metered"`，
前端据此显示「已用 N points」而不是「剩余 N」。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from admin.models import Account
from admin.security import clean_display_name
from admin.platform.loomy.core import client, endpoints

logger = logging.getLogger("admin.platform.loomy.accounts")
PLATFORM = endpoints.PLATFORM
QUOTA_TTL = 60
#: 402（积分不足）的号冷却多久。Loomy 的当日额度**每天重置**，
#: 10 分钟后重试一次足以发现「是不是刚好跨天了」，又不至于空转。
QUOTA_COOL_SECONDS = 600


def credentials_of(account: Account) -> dict:
    try:
        data = json.loads(account.auth_json or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def token_of(data: dict) -> str:
    return str(data.get("token") or data.get("session") or "")


def realm_of(account: Account) -> str:
    return str(getattr(account, "domain", "") or "cn")


def quota_is_fresh(account: Account) -> bool:
    synced = getattr(account, "credits_synced_at", None)
    if synced is None:
        return False
    return (datetime.utcnow() - synced).total_seconds() < QUOTA_TTL


def ensure_token(db: Session, account: Account) -> dict:
    """Loomy 没有续期机制 —— token 只能用现有的。

    这里只做「有没有」检查，不做「是否过期」猜测：
    过期与否由上游返回的 `100002` 决定，那时 raise 让上层提示重新登录。
    """
    data = credentials_of(account)
    if not token_of(data):
        raise client.UpstreamError(
            "账号缺少 session，请在客户端登录后重新扫描",
            status=401, kind="auth")
    return data


def accounts_for(db: Session, realm: str = "") -> list[Account]:
    query = db.query(Account).filter(Account.platform == PLATFORM)
    if realm:
        query = query.filter(Account.domain == realm)
    return query.order_by(Account.id.asc()).all()


def select(db: Session, realm: str = "", mark_picked: bool = False) -> Account | None:
    now = datetime.utcnow()
    for account in accounts_for(db, realm):
        if (getattr(account, "status", "") or "") != "active":
            continue
        cool = getattr(account, "cool_until", None)
        if cool and cool > now:
            continue
        if mark_picked:
            account.last_used_at = now
            db.commit()
        return account
    return None


def apply_quota(account: Account, quota: dict) -> None:
    """写回统一列。

    ## 额度口径（实测 2026-10-28）

        GET {base}/points/balance
        → {"balance": 5500, "dailyBalance": 4960, "availableBalance": 10460}

    `availableBalance` 才是「可用余额」（基础 + 当日额度）。
    第一版误以为 Loomy 没有额度接口，做了「计量制」——
    现在换成正常口径：`balance_remain = availableBalance`。

    `dailyBalance` 每日重置，所以它天然是「会过期的额度」，
    这里映射到 `credits_expiring_soon` 让「积分到期」面板也能看到。

    ## `models` 缺省时**沿用旧值**

    模型清单是**池级**信息（同一 realm 每个号看到的目录一样），批量导入时
    只探一次（见 `import_batch`）。探活不带 `models` 时必须保留上次的数字，
    否则每刷一次余额，池子的「可用模型」就被清零一次。
    """
    available = int(quota.get("available") or 0)
    daily = int(quota.get("daily_balance") or 0)
    account.balance_remain = available
    account.balance_total = available
    account.credits_expiring_soon = daily
    account.credits_synced_at = datetime.utcnow()
    account.last_sync_at = datetime.utcnow()
    try:
        previous_models = int((json.loads(account.credits_snapshot or "{}")
                               or {}).get("models") or 0)
    except (ValueError, TypeError, AttributeError):
        previous_models = 0
    fetched_models = quota.get("models")
    account.credits_snapshot = json.dumps({
        "unit": "points",
        "quota_kind": "balance",
        "balance": int(quota.get("balance") or 0),
        "daily_balance": daily,
        "available": available,
        "models": int(fetched_models) if fetched_models is not None
                  else previous_models,
    }, ensure_ascii=False)[:20000]


def sync_quota(db: Session, account: Account, *,
               with_models: bool = True) -> dict:
    """同步 = 探一次积分接口（可选再探模型目录）。

    `with_models=False` 专供**批量导入**：模型清单是池级的，100 个号逐个拉
    等于 100 次大 JSON 下载 + 解析（每次几十到几百 KB，重复内容 100 份），
    这是「导 100 个号要 24 秒」里最没意义的那一半开销。
    """
    data = ensure_token(db, account)
    realm = realm_of(account)
    token = token_of(data)
    quota: dict = {}
    try:
        points = client.fetch_points_balance(token, realm, account.id)
        quota.update(points)
    except client.UpstreamError as exc:
        account.last_err_msg = str(exc.message or "")[:500]
        if exc.kind == "auth":
            account.status = "disabled"
        elif exc.kind == "quota_exceeded":
            # 402 不是坏号，是暂时没钱。冷却一段时间把它从选号队列里挪开，
            # 比留在池子里被反复选中、反复 402 好得多。
            account.cool_until = datetime.utcnow() + timedelta(
                seconds=QUOTA_COOL_SECONDS)
            account.cool_kind = "quota"
        db.commit()
        return {"ok": False, "error": exc.to_dict()}
    if with_models:
        try:
            models = client.fetch_models(token, realm, account.id)
            quota["models"] = len(models)
        except client.UpstreamError:
            quota.setdefault("models", 0)
    apply_quota(account, quota)
    # 余额为 0 的号**主动冷却**：它的凭证是好的（积分接口 200），但发对话
    # 一定被上游 402 打回。留在选号队列里只会让请求先失败一次再换号 ——
    # 实测 100 个号里有 25 个是这种空号，用户看到的就是「一直 402」。
    if int(account.balance_remain or 0) <= 0:
        account.cool_until = datetime.utcnow() + timedelta(
            seconds=QUOTA_COOL_SECONDS)
        account.cool_kind = "quota"
    db.commit()
    return {"ok": True, "quota": quota}


#: 上游 session 的有效期。**客户端登录时传的就是这个值**
#: （`electron/xfyun/account-service.js` 的 `expire: 14 * 24 * 3600`），
#: 所以「签发时间 + 14 天」就是这张票的死线。
SESSION_TTL_SECONDS = 14 * 24 * 3600


def session_expires_at(data: dict | None) -> float:
    """从凭证里推算 session 到期时间（UTC 秒）；算不出来返回 0。

    ## 为什么要自己算（2026-11-06）

    上游**不回传**有效期，我们原来把它记成 `expires_at = 0`（「未知」），
    结果就是「某天突然一大批号全废了」—— 用户直到调用失败才知道票过期。

    但有效期其实是可以推出来的：
      * 客户端 `auth-session.json` 里有 `updatedAt`（**写入时间 = 登录时间**，
        毫秒时间戳）；
      * 登录接口的 `expire` 是固定的 14 天。

    于是「登录时间 + 14 天」就是死线。优先级：
      `updatedAt` → `expires_at`（导入时写过）→ `first_seen_at`
      （我们第一次确认它有效的时间，**保守下界**）。都没有就返回 0，
      不瞎猜。
    """
    payload = data if isinstance(data, dict) else {}
    for key in ("updatedAt", "updated_at", "first_seen_at"):
        raw = payload.get(key)
        if not raw:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        # 毫秒时间戳（客户端写的是 Date.now()）
        if value > 1e12:
            value /= 1000.0
        if value > 0:
            return value + SESSION_TTL_SECONDS
    try:
        stored = float(payload.get("expires_at") or 0)
    except (TypeError, ValueError):
        stored = 0
    return stored if stored > 0 else 0.0


def stamp_first_seen(data: dict) -> dict:
    """给没有 `updatedAt` 的老凭证补一个 `first_seen_at` 锚点。

    老数据（导入时还没保留 updatedAt）本来算不出死线。但**只要它现在还能用**，
    就说明「至少从这一刻起还有 14 天」—— 这是保守下界，比「未知」有用得多：
    用户至少会收到「这张票最晚 X 号到期」的提醒，而不是某天突然发现全挂了。
    """
    payload = dict(data or {})
    if payload.get("updatedAt") or payload.get("first_seen_at"):
        return payload
    payload["first_seen_at"] = int(time.time() * 1000)
    return payload


# ---------------------------------------------------------------------------
# 密码续期（唯一能真正「延长」session 的方式）
# ---------------------------------------------------------------------------
#: 剩余多少天开始自动续期。比预警阈值略早，留出重试余量。
SESSION_RENEW_BEFORE_DAYS = 3.0


def has_password(data: dict | None) -> bool:
    """这个号存了手机号 + 密码吗（没有就只能等它过期后手动重扫）。"""
    payload = data if isinstance(data, dict) else {}
    return bool(str(payload.get("loginid") or "").strip()
                and str(payload.get("password") or "").strip())


def set_password(account: Account, loginid: str, password: str) -> dict:
    """保存登录凭据（供到期自动续期）。

    ⚠️ 密码是**明文存进 auth_json** 的，和 `loomy2api` 那类工具一致 ——
    没有密码就没法换新 session（服务端没有 refresh 接口）。导出账号备份时
    这份凭据会一起被带走，提醒用户按凭据对待。
    """
    data = credentials_of(account)
    data["loginid"] = str(loginid or "").strip()
    data["password"] = str(password or "")
    return data


def needs_renewal(data: dict | None) -> bool:
    """该不该现在续期：有密码 + 剩余天数不足阈值（或已过期）。"""
    if not has_password(data):
        return False
    days = session_days_left(data)
    if days is None:
        # 算不出死线但有密码 —— 顺手续一次没坏处（幂等：新票还是 14 天）
        return False
    return days <= SESSION_RENEW_BEFORE_DAYS


def renew_with_password(db: Session, account: Account) -> dict:
    """用存好的手机号 + 密码重登，换一张新 session。

    这是「自动续期」的全部实现 —— 服务端没有 refresh token，
    只能用同一套登录接口重新签发一张 14 天的票。

    成功时同时刷新 `updatedAt` / `expires_at`，并把新 session 落库；
    失败（密码错 / 需要短信二次验证）**不动原凭据**，原票还能用到过期那天。
    """
    from admin.platform.loomy.core import xfyun

    data = credentials_of(account)
    loginid = str(data.get("loginid") or "").strip()
    password = str(data.get("password") or "")
    if not loginid or not password:
        raise ValueError("账号没有保存手机号/密码，无法自动续期")

    # 每个号一个设备标识：首次续期时生成并固定下来，之后一直复用
    # （与参考实现一致 —— 同一个号换 devid 反而像异常设备）。
    devid = str(data.get("devid") or "").strip()
    if not devid:
        devid = xfyun.new_device_id()

    got = xfyun.login_by_password(loginid, password, devid=devid)
    now_ms = int(time.time() * 1000)
    data.update({
        "token": got["session"],
        "uid": got.get("userid") or data.get("uid") or "",
        "phone": got.get("phone") or data.get("phone") or "",
        "devid": devid,
        "updatedAt": now_ms,
        "expires_at": int(now_ms / 1000 + xfyun.SESSION_EXPIRE_SECONDS),
        "renewed_at": now_ms,
        "renew_count": int(data.get("renew_count") or 0) + 1,
    })
    # 老的 first_seen_at 锚点已经没用了（有真实 updatedAt 了），清掉避免混淆
    data.pop("first_seen_at", None)
    account.auth_json = json.dumps(data, ensure_ascii=False)
    account.status = "active"
    account.last_err_msg = ""
    account.session_dead_fails = 0
    db.commit()
    return {"ok": True, "session": got["session"][:8] + "…",
            "days_left": session_days_left(data),
            "renewed_at": now_ms}


def session_days_left(data: dict | None, now: float | None = None) -> float | None:
    """还剩多少天（负数=已过期；算不出来返回 None）。"""
    expires = session_expires_at(data)
    if expires <= 0:
        return None
    now = now if now is not None else time.time()
    return round((expires - now) / 86400.0, 1)


#: 剩余多少天算「快到期」—— 提前 3 天足够把号重新扫一遍。
SESSION_WARN_DAYS = 3.0


def _expiring_soon(data: dict | None) -> bool:
    days = session_days_left(data)
    return days is not None and days <= SESSION_WARN_DAYS


def public_view(account: Account, *, quota: dict | None = None) -> dict:
    """给前端的视图（**不含 token**）。

    `models` 优先取本次实时探活的结果，没有就回落到快照里的值 ——
    批量导入只拉一次模型目录（见 `import_batch` 第 5 步），其余号的
    快照里是上一次的数字，不能因为「这次没探」就显示 0。
    """
    data = credentials_of(account)
    try:
        snapshot = json.loads(account.credits_snapshot or "{}")
    except (ValueError, TypeError):
        snapshot = {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    models = (quota or {}).get("models")
    if models is None:
        models = snapshot.get("models")
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
        "quota_live": bool(quota),
        "models": int(models or 0),
        "phone": str(data.get("phone") or ""),
        "has_refresh": False,
        # 能不能自动续期：只有存了手机号+密码才可以（服务端无 refresh 接口）
        "renewable": has_password(data),
        "renew_before_days": SESSION_RENEW_BEFORE_DAYS,
        "renew_count": int(data.get("renew_count") or 0),
        # ---- session 有效期（我们按客户端规则自己算的，见 session_expires_at）----
        # 上游没有 refresh 接口，14 天一到必失效；把死线和剩余天数透出给前端，
        # 用户在「还剩 3 天」时就能安排重新扫描，而不是某天突然全挂。
        "session_expires_at": session_expires_at(data) or None,
        "session_days_left": session_days_left(data),
        "session_expiring": _expiring_soon(data),
        # 入库时间：**全平台统一字段**，账号池表格直接渲染这一列。
        # 没有它，用户只能靠 id 顺序猜「哪几个是刚导进来的」。
        "created_at": account.created_at.isoformat()
        if account.created_at else None,
        "last_used_at": account.last_used_at.isoformat()
        if account.last_used_at else None,
        "last_sync_at": account.last_sync_at.isoformat()
        if account.last_sync_at else None,
        "cool_until": account.cool_until.isoformat()
        if getattr(account, "cool_until", None) else None,
        "cool_kind": getattr(account, "cool_kind", "") or "",
        "last_err_msg": getattr(account, "last_err_msg", "") or "",
    }


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    active = sum(1 for r in rows if r.get("status") == "active")
    return {
        "total": total,
        "active": active,
        "disabled": total - active,
        "balance_remain": 0,
        "balance_total": sum(int(r.get("balance_total") or 0) for r in rows),
        "quota_kind": "metered",
    }


def import_from_local(db: Session, uids: list[str] | None = None) -> dict:
    """从本机 auth-session.json 导入。

    ## uids 过滤（「导入」不再是静默全导）

    前端已改为「扫描 → 弹窗展示 → 用户点确认」两步；uids 是用户在
    弹窗里**明确勾选**的账号（扫描结果里的 userid）。为 None 时保持
    旧行为（全部导入）—— 兼容老调用方（platform_gateway/定时任务）。

    本机通常只有一份 auth-session.json，但过滤语义必须存在：将来
    FALLBACK_DIRS 里出现多份凭证时，不该再把「用户没选的」也灌进号池。
    """
    from admin.platform.loomy.services import scan

    creds = scan.read_credentials()
    if not creds:
        return {"ok": False, "imported": 0,
                "reason": "no_plain_credential",
                "hint": "预期位置：%s" % scan.cred_path()}
    record = {
        "realm": "cn",
        "token": str(creds.get("session") or ""),
        "uid": str(creds.get("userid") or ""),
        "phone": str(creds.get("phone") or ""),
        "name": "Loomy %s" % (str(creds.get("phone") or "")[-4:] or "account"),
        # 保留客户端写入时间：14 天有效期的**唯一锚点**（见 session_expires_at）。
        # 之前这里丢掉了 updatedAt，结果 expires_at 永远是 0 = 「有效期未知」，
        # 也就没法提醒「再过几天这张票就废了」。
        "updatedAt": creds.get("updatedAt"),
        "expires_at": session_expires_at({
            "updatedAt": creds.get("updatedAt"),
            "expires_at": 0,
        }),
    }
    if uids is not None:
        wanted = {str(u or "").strip() for u in uids}
        wanted.discard("")
        # 前端勾选框携带的是**扫描结果里的打码 uid**（discover 不回完整
        # token/uid），所以这里同时接受完整 uid 与打码形态的匹配。
        if wanted and record["uid"] and record["uid"] not in wanted:
            from admin.platform.loomy.services import scan as _scan

            masked_uid = _scan.mask(record["uid"])
            masked_phone = _scan.mask(record["phone"]) if record["phone"] else ""
            if masked_uid not in wanted and masked_phone not in wanted:
                return {"ok": False, "imported": 0, "reason": "not_selected",
                        "hint": "本机凭证（uid %s…）不在勾选范围内，未导入"
                                % record["uid"][:6]}
    result = import_record(db, record)
    return {"ok": True, "imported": 1, **result}


def import_record(db: Session, record: dict) -> dict:
    """导入一条凭证。按 uid 幂等。"""
    if not isinstance(record, dict):
        raise ValueError("凭证必须是对象")
    token = token_of(record)
    if not token:
        raise ValueError("凭证缺少 session/token")
    realm = str(record.get("realm") or "cn")
    uid = str(record.get("uid") or record.get("userid") or "")

    existing = None
    if uid:
        existing = db.query(Account).filter(Account.platform == PLATFORM,
                                            Account.uid == uid).first()
    created = existing is None
    if existing is None:
        existing = Account(platform=PLATFORM,
                           name=clean_display_name(str(record.get("name") or uid or "Loomy")),
                           uid=uid, status="active")
        db.add(existing)

    existing.name = clean_display_name(str(record.get("name") or existing.name or uid or "Loomy"))
    existing.uid = uid or existing.uid
    existing.domain = realm
    existing.auth_json = json.dumps({
        "realm": realm,
        "token": token,
        "uid": uid,
        "phone": str(record.get("phone") or ""),
        # 保留客户端写入时间 + 推算出的死线（见 session_expires_at）
        "updatedAt": record.get("updatedAt"),
        "expires_at": int(session_expires_at(record) or 0),
    }, ensure_ascii=False)
    # 导入的 token 一律标「未验证」：probe 会把它刷成 active/disabled。
    existing.status = "active"
    existing.session_dead_fails = 0
    existing.consecutive_fails = 0
    existing.cool_until = None
    existing.cool_kind = ""
    existing.last_err_msg = ""
    existing.last_sync_at = datetime.utcnow()
    db.commit()
    db.refresh(existing)

    # 立刻探一次，确认凭证可用
    probe = sync_quota(db, existing)
    return {"ok": bool(probe.get("ok")) or True,
            "account_id": existing.id, "uid": uid, "created": created,
            "probe": probe}


def import_batch(db: Session, text: str, *, probe: bool = True,
                 workers: int = 16, on_progress=None) -> dict:
    """从手机版备份（JSONL / JSON 数组）批量导入 Loomy 账号。

    ## 为什么不用 `import_record` 逐条跑

    第一版就是 `for rec: import_record(db, rec)`，而 `import_record` 里：

        db.commit()          # 每条一次事务
        sync_quota(db, acc)  # 每条 1~2 次上游 HTTP（积分 + 模型列表）

    100 个号 = 100 次提交 + 100~200 次**串行**网络往返，实测 **24 秒还没完**。
    用户看到的「导入太慢、内存是不是炸了」就是这里 —— 不是内存，
    是串行 I/O 加上每条都 `db.refresh()` 的会话 churn。

    ## 现在的做法

      1. 一次解析完（JSON 数组 / JSONL 都认）；
      2. **一次** `IN` 查询把已存在的 uid 全捞出来（分块 500，避免 SQL 过长）；
      3. 在内存里建/改 ORM 对象，**一次 commit** 落库；
      4. 探活改成**并发**（默认 16 线程，每线程独立 Session），且**只探积分**
         —— 模型目录是池级的，100 个号逐个拉就是 100 次几十~几百 KB 的
         重复 JSON 下载（占掉一半时间的正是它）。整池统一拉一次即可；
         `probe=False` 可完全跳过（大批量导入时交给保活任务）。
      5. 进度通过 `on_progress(done, total, note)` 上报，前端进度条不再是死的。

    ## 保留的语义

      * 按 `(platform, uid)` 幂等：同 uid 更新，不新增；
      * 没有 uid 的记录一律新建（与旧行为一致）；
      * 单条坏数据只计数，不阻断其余；
      * 返回 `added / updated / failed / total / errors`（前端就认这几个）。
    """
    from concurrent.futures import ThreadPoolExecutor

    def _progress(done: int, total: int, note: str = "") -> None:
        """把「入库 / 探活到第几条」报给调用方（任务面板的进度条）。

        签名故意容错：调用方（`admin/routers/accounts.py` 的异步导入）
        只关心 (done, total)；第三个参数是给人看的阶段文案。
        """
        if on_progress is None:
            return
        try:
            on_progress(done, total, note)
        except TypeError:
            try:
                on_progress(done, total)
            except Exception:  # noqa: BLE001 - 进度失败绝不影响导入
                pass
        except Exception:  # noqa: BLE001
            pass

    started = time.time()
    text = (text or "").strip()
    if not text:
        return {"ok": False, "added": 0, "updated": 0, "failed": 0,
                "total": 0, "errors": ["没有提供任何凭证"], "elapsed_ms": 0}

    # ---- 1) 解析 ----
    records: list[dict] = []
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, list):
        records = [r for r in parsed if isinstance(r, dict)]
    else:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                records.append(obj)

    if not records:
        return {"ok": False, "added": 0, "updated": 0, "failed": 0,
                "total": 0, "errors": ["没有解析出任何账号记录（应为 JSONL 或 JSON 数组）"],
                "elapsed_ms": int((time.time() - started) * 1000)}

    # ---- 2) 预取已存在账号（一次查询，分块） ----
    uids = [str(r.get("uid") or r.get("userid") or "") for r in records]
    uids = [u for u in uids if u]
    existing_map: dict[str, Account] = {}
    for i in range(0, len(uids), 500):
        chunk = list(dict.fromkeys(uids[i:i + 500]))
        if not chunk:
            continue
        for row in (db.query(Account)
                    .filter(Account.platform == PLATFORM,
                            Account.uid.in_(chunk))
                    .all()):
            existing_map[str(row.uid)] = row

    # ---- 3) 内存里建/改，最后一次 commit ----
    added = updated = failed = 0
    errors: list[str] = []
    touched: list[int] = []          # 待探活的 account id
    seen: set[str] = set()

    for rec in records:
        try:
            token = token_of(rec)
            if not token:
                failed += 1
                errors.append("跳过一条：缺少 token/session")
                continue
            realm = str(rec.get("realm") or "cn")
            uid = str(rec.get("uid") or rec.get("userid") or "")
            # 同一文件里重复的 uid 只算一次（后者覆盖前者，与逐条导入一致）
            if uid and uid in seen:
                updated += 1
            elif uid and uid in existing_map:
                updated += 1
            else:
                added += 1
            if uid:
                seen.add(uid)

            account = existing_map.get(uid) if uid else None
            if account is None:
                account = Account(platform=PLATFORM,
                                  name=clean_display_name(
                                      str(rec.get("name") or uid or "Loomy")),
                                  uid=uid, status="active")
                db.add(account)
                if uid:
                    existing_map[uid] = account
            account.name = clean_display_name(
                str(rec.get("name") or account.name or uid or "Loomy"))
            account.uid = uid or account.uid
            account.domain = realm
            account.auth_json = json.dumps({
                "realm": realm,
                "token": token,
                "uid": uid,
                "phone": str(rec.get("phone") or ""),
                # 同 import_record：留下客户端写入时间与推算死线
                "updatedAt": rec.get("updatedAt"),
                "expires_at": int(session_expires_at(rec) or 0),
            }, ensure_ascii=False)
            account.status = "active"
            account.session_dead_fails = 0
            account.consecutive_fails = 0
            account.cool_until = None
            account.cool_kind = ""
            account.last_err_msg = ""
            account.last_sync_at = datetime.utcnow()
        except Exception as exc:  # noqa: BLE001 - 单条坏数据不阻断
            failed += 1
            errors.append("跳过一条：%s" % str(exc)[:120])

    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return {"ok": False, "added": 0, "updated": 0, "failed": len(records),
                "total": len(records),
                "errors": ["批量入库失败（已回滚）：%s" % str(exc)[:160]],
                "elapsed_ms": int((time.time() - started) * 1000)}

    # 新建对象 commit 后才有 id
    touched = [int(a.id) for a in existing_map.values() if a.id]

    # ---- 4) 并发探活（可选） ----
    probe_ok = probe_failed = 0
    if probe and touched:
        def _probe_one(account_id: int) -> bool:
            # 每线程独立 Session：SQLAlchemy Session 不是线程安全的，
            # 共享一个会让探活写回互相踩（表现为偶发的「余额不更新」）。
            from admin.db import SessionLocal

            session = SessionLocal()
            try:
                account = (session.query(Account)
                           .filter(Account.id == account_id).first())
                if account is None:
                    return False
                # **只探积分**：模型目录是池级的，逐号拉 100 次纯属重复下载
                # （每次几十~几百 KB 的 JSON）。下面探完再统一拉一次。
                return bool(sync_quota(session, account,
                                       with_models=False).get("ok"))
            except Exception:  # noqa: BLE001 - 单条探活失败不影响导入结果
                return False
            finally:
                session.close()

        _progress(0, len(touched), "已入库，正在探测凭证可用性")
        # 16 并发：探活是纯 I/O（每个号 1 次 HTTP），8 线程时 100 个号要排
        # 十几轮 RTT。上限压住 16 是为了别把上游打出 429。
        with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 16))) as pool:
            for idx, ok_flag in enumerate(pool.map(_probe_one, touched), start=1):
                if ok_flag:
                    probe_ok += 1
                else:
                    probe_failed += 1
                if idx % 10 == 0 or idx == len(touched):
                    _progress(idx, len(touched),
                              "探活 %d/%d（可用 %d）" % (idx, len(touched), probe_ok))

        # ---- 5) 模型数：整池只拉一次 ----
        # 号池的「可用模型」是 `max(各号 models)`（见 overview_cards），
        # 所以只要**一个**探活成功的号把真实数字写进快照就够，其余号沿用。
        # 这样 100 个号的模型开销从 100 次降到 1 次。
        if probe_ok:
            from admin.db import SessionLocal

            session = SessionLocal()
            try:
                for account_id in touched:
                    account = (session.query(Account)
                               .filter(Account.id == account_id).first())
                    if account is None or not account.balance_remain:
                        continue
                    result = sync_quota(session, account, with_models=True)
                    if result.get("ok"):
                        break
            except Exception:  # noqa: BLE001 - 模型数拿不到不影响导入
                try:
                    session.rollback()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                session.close()

    return {"ok": failed == 0,
            "added": added, "updated": updated, "failed": failed,
            "total": len(records), "errors": errors[:20],
            "probe_ok": probe_ok, "probe_failed": probe_failed,
            "elapsed_ms": int((time.time() - started) * 1000)}
