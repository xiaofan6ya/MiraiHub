"""Loomy Gateway 契约实现。

## 三个必须照做的点

1. `platform_id` **类属性**必须声明（Protocol 的一部分，
   缺了 `isinstance` 判 False 且错误信息不提示）
2. 方法签名与 Protocol 完全一致（不能有默认值差异）
3. `install()` 幂等

## Loomy 的特殊性

* **没有额度接口**：计费在每轮 `usage.points_consumed` → 计量制
* **没有 refresh**：session 失效只能重新登录
* **没有签到**：`checkin` 明确返回 `supported: False`
* 凭证是明文 JSON → `import_local` 能真正工作（用户什么都不用做）
"""
from __future__ import annotations

import json
import logging

from admin.platform.gateway import AccountOps, ClientProbe, Gateway
from admin.platform.loomy.core import client, endpoints
from admin.platform.loomy.services import accounts as loomy_accounts
from admin.platform.loomy.services import scan as loomy_scan

logger = logging.getLogger("admin.platform.loomy.platform_gateway")


#: 一次**后台**刷新最多同步几个「积分快照已过期」的号。
#:
#: ⚠️ 这不再是「列表请求里同步几个」—— 那版实测把 `/accounts` 拖到 **2.8 秒**
#: （50 个号里 8 个各要一次上游 RTT，串行）。现在列表只读快照、立刻返回，
#: 刷新交给 `shared.kick_background_refresh()`（单飞 + 限频 + 限量）。
QUOTA_SYNC_PER_LIST = 8

#: 后台刷新的最小间隔（秒）。翻页很频繁，不能每页都排一批上游请求。
QUOTA_REFRESH_MIN_GAP = 10.0


def _kick_quota_refresh(account_ids: list[int]) -> bool:
    """把「刷新过期余额」丢到后台，**不阻塞列表响应**（见 shared 里的说明）。"""
    from admin.platform import shared

    return shared.kick_background_refresh(
        "loomy", account_ids,
        lambda session, account: loomy_accounts.sync_quota(
            session, account, with_models=False),
        max_items=QUOTA_SYNC_PER_LIST, min_gap=QUOTA_REFRESH_MIN_GAP)


def _snapshot_of(account) -> dict:
    """账号上一次的积分快照（打上游失败时的兜底显示）。"""
    try:
        snap = json.loads(account.credits_snapshot or "{}")
    except (ValueError, TypeError):
        snap = {}
    return snap if isinstance(snap, dict) else {}


class LoomyAccountOps:
    """账号池 11 项操作。"""

    def __init__(self, realm: str = ""):
        self.realm = realm

    def list(self, db, *, page: int | None = None, page_size: int = 50,
             search: str = "", status: str = "", sort: str = "id_desc",
             min_credits=None, max_credits=None):
        """账号列表（支持服务端分页 / 搜索 / 筛选）。

        ## 两种模式

        * `page=None`（默认）：旧行为，全量返回 —— 兼容旧调用方。
        * `page` 传入：服务端分页。summary 走 SQL 聚合
          （口径与全量版一致：积分类只算 active）。

        ## 列表**不碰上游**

        余额是展示数据，不该挡住整页渲染。这里只读 `credits_snapshot`，
        过期账号丢给后台线程刷新（`_kick_quota_refresh`，单飞 + 限频）。
        实测把同步探活从请求路径里拿掉后：**2.82s → 0.05s**。

        搜索/筛选/排序复用 `admin.platform.shared` 的通用助手，
        与通用 `/api/accounts` 同一套语义。
        """
        from sqlalchemy import func as _func

        from admin.models import Account
        from admin.platform.shared import (apply_account_filters,
                                           account_order,
                                           summarize_accounts_sql)

        query = db.query(Account).filter(Account.platform == "loomy")
        if self.realm:
            query = query.filter(Account.domain == self.realm)
        query = apply_account_filters(
            query, Account, search=search, status=status,
            min_credits=min_credits, max_credits=max_credits)
        query = account_order(query, Account, sort)

        if page is None:
            views, stale = self._views_of(db, query.all())
            return {"items": views,
                    "summary": loomy_accounts.summarize(views)}

        total = int(query.with_entities(_func.count(Account.id)).scalar() or 0)
        rows = (query.offset((max(1, page) - 1) * page_size)
                .limit(page_size).all())
        views, stale = self._views_of(db, rows)

        # summary 不含 status 筛选（筛选 tab 显示全池分布）
        agg_q = db.query(Account).filter(Account.platform == "loomy")
        if self.realm:
            agg_q = agg_q.filter(Account.domain == self.realm)
        agg_q = apply_account_filters(
            agg_q, Account, search=search, status="",
            min_credits=min_credits, max_credits=max_credits)
        return {"items": views,
                "summary": summarize_accounts_sql(agg_q, Account),
                "paginated": True, "total": total,
                "page": max(1, page), "page_size": page_size}

    def _views_of(self, db, accounts) -> tuple[list[dict], list[int]]:
        """把账号列表转成视图，**只读快照、绝不打上游**。

        返回 `(views, stale_ids)`；`stale_ids` 交给 `_kick_quota_refresh()`
        在后台刷新。这里一个 HTTP 都不发 —— 实测原来同步跑 8 个号要 2.8 秒，
        现在整个列表 0.05 秒。

        ⚠️ `credits_snapshot` 解析失败必须兜住：一个坏行不能让整页 500。
        """
        views: list[dict] = []
        stale: list[int] = []
        for account in accounts:
            if not loomy_accounts.quota_is_fresh(account):
                stale.append(int(account.id or 0))
            views.append(loomy_accounts.public_view(
                account, quota=_snapshot_of(account)))
        # 过期余额交给后台刷（单飞 + 限频），页面不等它。
        try:
            _kick_quota_refresh(stale)
        except Exception:  # noqa: BLE001 - 后台调度失败不影响列表
            logger.debug("后台刷新调度失败", exc_info=True)
        return views, stale

    def scan(self):
        return loomy_scan.discover()

    def import_local(self, db, uids: list[str] | None = None):
        """本机导入 —— **Loomy 的主路径**。

        uids：前端「扫描 → 勾选 → 确认」里用户明确选中的账号；
        None = 全部导入（兼容定时任务等老调用方）。
        """
        return loomy_accounts.import_from_local(db, uids=uids)

    def import_record(self, db, record):
        return loomy_accounts.import_record(db, record)

    def refresh(self, db, account_id, *, force=False):
        from admin.models import Account

        account = db.query(Account).filter(
            Account.id == int(account_id), Account.platform == "loomy").first()
        if account is None:
            raise ValueError("账号不存在")
        return loomy_accounts.sync_quota(db, account)

    def keepalive(self, db):
        """保活：真探测每张 session 还活着没 + 同步余额 + 报告剩余天数。

        ## 先说结论：Loomy 没有可自动化的续期

        我们已经把能试的都试过了（2026-11-06，用真实 session + 正确的
        讯飞 HMAC-SHA1 签名逐个探测）：

            /session/refresh /login/session/refresh /login/account/refresh
            /login/account/renew /login/account/keepalive /login/session/keepalive
            /userinfo/session/refresh /userinfo/session/renew
            /login/account/expire /login/session/expire ...

        **全部 404**（服务端根本没这些路由），客户端代码里也没有任何
        refresh 调用点。session 只在登录时签发，有效期写死
        `expire: 14 * 24 * 3600`。想延长只能在登录那一次传更大的 expire，
        而那需要手机验证码 —— 无法自动化。

        所以这里做**能做的那部分**，而且做扎实：

          1. **真探测**：用 `/userinfo/query/baseInfo`（客户端自己用的那个
             接口）确认 session 还在，比原来探积分接口更准，也不消耗额度；
          2. **算死线**：`updatedAt + 14 天`，把「还剩几天」算出来写回；
          3. **提前预警**：剩余 ≤3 天在返回里单独列出，前端可以直接提示
             「这些号 3 天内要重新登录」；
          4. **失效即禁用**：`100002`/`020002` 是终态，标 disabled 让选号跳过，
             不再每轮白撞。
        """
        import time as _time

        from admin.models import Account

        from admin.platform import shared as platform_shared

        ok, refreshed = 0, 0
        failed: list[dict] = []
        expiring: list[dict] = []
        refresh_anchor = 0
        renewed_count = 0
        renewable_count = 0

        accounts = [int(row[0]) for row in
                    (db.query(Account.id)
                     .filter(Account.platform == loomy_accounts.PLATFORM,
                             Account.status == "active")
                     .order_by(Account.id.asc()).all())]
        if not accounts:
            return {"ok": True, "probed": 0, "refreshed": 0, "failed": [],
                    "expiring": [],
                    "note": "没有 active 的 Loomy 账号"}

        def worker(session, account):
            realm = loomy_accounts.realm_of(account)
            data = loomy_accounts.ensure_token(session, account)
            token = loomy_accounts.token_of(data)
            renewed = False
            # 必须是**这个任务自己的**变量：并发跑时共享一个外部变量会串号。
            renew_error = ""

            # ⓞ **到期前自动续期**：有手机号+密码的号，在剩余不足 3 天时
            #    用 byPwd 重登换一张新 14 天的票。这是唯一能让号池「无人值守
            #    长期活着」的机制（服务端没有 refresh 接口，实测 404）。
            try:
                if loomy_accounts.needs_renewal(data):
                    loomy_accounts.renew_with_password(session, account)
                    data = loomy_accounts.credentials_of(account)
                    token = loomy_accounts.token_of(data)
                    renewed = True
            except Exception as exc:  # noqa: BLE001 - 续期失败不影响本次探活
                renew_error = ("%s: %s" % (type(exc).__name__,
                                           getattr(exc, "message", exc)))[:160]

            try:
                # ① 真探测：积分接口必须接受 session，能真实回答
                #    「这张票上游还认不认」。401/100002 才是失效，
                #    网络抖动不算（否则一次抖动会把好号全禁掉）。
                points = client.fetch_points_balance(token, realm, account.id)
            except client.UpstreamError as exc:
                if exc.kind == "auth":
                    # session 真的废了：禁用，别再让它进候选
                    account.status = "disabled"
                    account.last_err_msg = (exc.message or "")[:255]
                    session.commit()
                return {"account_id": account.id, "account": account.name,
                        "ok": False, "auth_dead": exc.kind == "auth",
                        "renewed": renewed,
                        "error": exc.message[:120]}
            # ② 活着：同步余额（保活跑完前端数字就是新的）
            quota_ok = False
            try:
                loomy_accounts.apply_quota(account, points)
                session.commit()
                quota_ok = True
            except Exception:  # noqa: BLE001 - 余额失败不影响「登录态还活着」
                session.rollback()
            # ③ 给老凭证补「首次确认有效」的时间锚点：没有它就永远算不出死线。
            data = loomy_accounts.credentials_of(account)
            if not (data.get("updatedAt") or data.get("first_seen_at")):
                data = loomy_accounts.stamp_first_seen(data)
                try:
                    account.auth_json = json.dumps(data, ensure_ascii=False)
                    session.commit()
                    refresh_anchor += 1
                except Exception:  # noqa: BLE001
                    session.rollback()
            days = loomy_accounts.session_days_left(data)
            return {"account_id": account.id, "account": account.name,
                    "ok": True, "quota": quota_ok, "days_left": days,
                    "renewed": renewed, "renewable": loomy_accounts.has_password(data),
                    "renew_error": renew_error if not renewed else ""}

        rows = platform_shared.run_account_batch(
            accounts, worker, label="loomy-keepalive")

        now = _time.time()
        for row in rows:
            if row.get("ok"):
                ok += 1
                if row.get("quota"):
                    refreshed += 1
                if row.get("renewed"):
                    renewed_count += 1
                if row.get("renewable"):
                    renewable_count += 1
                days = row.get("days_left")
                if days is not None and days <= loomy_accounts.SESSION_WARN_DAYS:
                    expiring.append({"account_id": row.get("account_id"),
                                     "account": row.get("account"),
                                     "days_left": days,
                                     "renewable": row.get("renewable"),
                                     "renew_error": row.get("renew_error") or ""})
            else:
                failed.append({"account_id": row.get("account_id"),
                               "account": row.get("account"),
                               "error": row.get("error") or "未知错误"})

        note = ("Loomy 无 refresh 接口（已实测 404）：session 自登录起 14 天有效。"
                "有手机号+密码的号会在剩余不足 %.0f 天时自动重登续期。"
                % loomy_accounts.SESSION_RENEW_BEFORE_DAYS)
        if renewed_count:
            note += "；本次自动续期 %d 个" % renewed_count
        if renewable_count:
            note += "（%d/%d 个号已存密码，可自动续期）" % (renewable_count, ok)
        elif ok:
            note += "（当前没有号存密码 —— 到期只能手动重扫）"
        if refresh_anchor:
            note += "；为 %d 个老凭证补了有效期锚点" % refresh_anchor
        if expiring:
            note += "；有 %d 个号 %.0f 天内到期" % (
                len(expiring), loomy_accounts.SESSION_WARN_DAYS)
        return {"ok": not failed, "probed": ok, "refreshed": refreshed,
                "failed": failed,
                "expiring": expiring,
                "expiring_count": len(expiring),
                "anchored": refresh_anchor,
                "renewed": renewed_count,
                "renewable": renewable_count,
                "checked_at": now,
                "note": note}

    def checkin(self, db):
        """每日签到：领取当日赠送额度（5000 积分）。

        实测 `POST /points/first-login` 幂等 —— 已领过时返回
        `alreadyProcessed: true`，不是错误。
        """
        from admin.platform.loomy.services import checkin as checkin_service

        result = checkin_service.run_daily_checkin(db, realm=self.realm or "cn")
        return {"supported": True, **result}

    def credits(self, db):
        rows = loomy_accounts.accounts_for(db, self.realm)
        items = []
        for account in rows:
            try:
                snap = json.loads(account.credits_snapshot or "{}")
            except ValueError:
                snap = {}
            items.append({
                "id": account.id,
                "name": account.name,
                "available": int(getattr(account, "balance_remain", 0) or 0),
                "balance": int((snap or {}).get("balance") or 0),
                "daily_balance": int((snap or {}).get("daily_balance") or 0),
                "models": int((snap or {}).get("models") or 0),
                "unit": "points",
            })
        total = sum(i["available"] for i in items)
        return {"items": items, "unit": "points",
                "summary": {"available": total,
                            "accounts": len(items)},
                "note": "availableBalance = 基础余额 + 当日额度"}

    def usage(self, db, **filters):
        """用量：上游无明细接口，给本网关账本。"""
        days = int(filters.get("days") or 30)
        return {"realm": self.realm, "days": days,
                "source": "local_only",
                "local": _local_ledger(db, self.realm, days),
                "note": "上游不提供用量明细；以下为本网关记录（含 points_consumed 累计）"}

    def health(self, db, account_id):
        from admin.models import Account

        account = db.query(Account).filter(
            Account.id == int(account_id), Account.platform == "loomy").first()
        if account is None:
            raise ValueError("账号不存在")
        stages = {}
        data = loomy_accounts.credentials_of(account)
        token = loomy_accounts.token_of(data)
        stages["session"] = {"ok": bool(token), "detail": "len=%d" % len(token)}
        try:
            models = client.fetch_models(token, loomy_accounts.realm_of(account),
                                         account.id)
            stages["models"] = {"ok": bool(models), "detail": "%d 个模型" % len(models)}
        except client.UpstreamError as exc:
            stages["models"] = {"ok": False, "detail": exc.message[:100]}
        return {"account_id": account.id, "account": account.name,
                "ok": all(s["ok"] for s in stages.values()), "stages": stages}

    def inject(self, db, account_id):
        return {"supported": False,
                "summary": "Loomy 的 session 由客户端自管，本网关不注入"}

    def export(self, db, ids=None):
        rows = loomy_accounts.accounts_for(db, self.realm)
        if ids:
            wanted = {int(i) for i in ids}
            rows = [r for r in rows if r.id in wanted]
        out = []
        for account in rows:
            data = loomy_accounts.credentials_of(account)
            out.append({
                "id": account.id, "name": account.name, "uid": account.uid,
                "realm": loomy_accounts.realm_of(account),
                "token": loomy_accounts.token_of(data),
                "phone": str(data.get("phone") or ""),
            })
        return {"format": "backup", "platform": "loomy", "accounts": out}


class LoomyClientProbe:
    """本机探测。可改项：无（客户端配置由它自己管）。"""

    def __init__(self, realm: str = ""):
        self.realm = realm

    def dirs(self) -> list[str]:
        return list(loomy_scan.discover().get("data_dirs") or [])

    def probe(self) -> dict:
        result = loomy_scan.discover()
        # 补 app_path / app_version / ua（路径表在本平台 core/paths.py，
        # 不放总网关 —— 加平台不用改共享文件）
        from admin.platform.probe import enrich
        from admin.platform.loomy.core import paths as _paths

        return enrich(result,
                      exe_candidates=_paths.app_exe_candidates(),
                      data_dirs=_paths.data_dir_candidates(),
                      product=_paths.UA_PRODUCT)


    def schema(self) -> list[dict]:
        return []

    def read(self, key: str):
        return None

    def write(self, key: str, value) -> dict:
        return {"supported": False, "summary": "Loomy 客户端参数只读"}


class LoomyGateway:
    """网关主体。"""

    #: ★ Protocol 要求 —— 缺了 isinstance 判 False
    platform_id = "loomy"

    def __init__(self, realm: str = ""):
        self.realm = realm or "cn"

    # ---- 转发（实现都在 gateway.py）----
    def chat_completions(self, request, body):
        from admin.platform.loomy import gateway as impl

        return impl.chat_completions(request)

    def responses(self, request, body):
        from admin.platform.loomy import gateway as impl

        return impl.responses(request)

    def messages(self, request, body):
        from admin.platform.loomy import gateway as impl

        return impl.messages(request)

    def count_tokens(self, request, body):
        from admin.platform.loomy import gateway as impl

        return impl.count_tokens(request)

    def list_models(self, request):
        from admin.platform.loomy import gateway as impl

        return impl.list_models(request)

    def balance(self, request):
        from admin.platform.loomy import gateway as impl

        return impl.balance(request)

    # ---- 契约 ----
    def account_ops(self) -> AccountOps:
        return LoomyAccountOps(self.realm)

    # ---- 凭证有效期 ----
    def credential_expiry(self, auth_json) -> dict:
        """Loomy 的 session 死线 —— **这是唯一不能靠 refresh 救的平台**。

        服务端没有 refresh token（12 个候选端点实测全 404），session 只在
        登录时签发、固定 14 天。所以：
          · `renewable` 只有在**存了手机号+密码**时才为 True；
          · 没有密码的号到期后只能重新扫描/短信重登。
        """
        from admin.platform import shared

        data = shared.parse_auth_json(auth_json)
        if not data:
            return shared.credential_window()
        expires = loomy_accounts.session_expires_at(data)
        return shared.credential_window(
            expires_at=expires or None,
            # 续期窗口 = 同一个死线：到点前必须用密码重登，没有别的办法
            refresh_expires_at=expires or None,
            # 「能续」= 有密码（有 refresh token 这个概念在 Loomy 不存在）
            has_refresh=loomy_accounts.has_password(data))

    def renewal_guard_days(self) -> int:
        return int(loomy_accounts.SESSION_WARN_DAYS)

    def task_runners(self) -> dict:
        """本平台的定时任务。

        `loomy_checkin` —— 每日签到，领取当日赠送额度（5000 积分）。

        之前这里返回 `{}`（当时误判「Loomy 没有签到接口」）。
        实测 `POST /points/first-login` 就是签到入口，且**幂等** ——
        当天第一次调触发发放，之后返回 `alreadyProcessed: true`。
        """
        from admin.platform.loomy import _task_runners as declared

        runners = dict(declared())
        # 保活：Loomy 没有 refresh 接口，但 ensure_token 会在本地判断
        # 过期并复用刚签发的 session；定时跑一次能把死号尽早标出来。
        runners["loomy_keepalive"] = self.account_ops().keepalive
        return runners

    def client_probe(self) -> ClientProbe:
        return LoomyClientProbe(self.realm)

    def overview_cards(self, db) -> list[dict]:
        rows = loomy_accounts.accounts_for(db, self.realm)
        total = len(rows)
        active = sum(1 for r in rows
                     if (getattr(r, "status", "") or "") == "active")
        available = sum(int(getattr(r, "balance_remain", 0) or 0) for r in rows)
        daily = sum(int(getattr(r, "credits_expiring_soon", 0) or 0) for r in rows)
        models = 0
        for account in rows:
            try:
                snap = json.loads(account.credits_snapshot or "{}")
            except ValueError:
                snap = {}
            models = max(models, int((snap or {}).get("models") or 0))

        return [
            {"key": "accounts", "label": "账号总数", "value": total,
             "sub": "启用 %d" % active, "icon": "fa-users", "tone": ""},
            {"key": "credits", "label": "可用积分",
             "value": "{:,}".format(available),
             "sub": "其中当日额度 %s" % "{:,}".format(daily),
             "icon": "fa-wallet", "tone": "brand"},
            {"key": "models", "label": "可用模型", "value": models,
             "sub": "来自上游 /models（已过滤图像模型）",
             "icon": "fa-cube", "tone": ""},
            {"key": "mode", "label": "接入方式", "value": "会话鉴权",
             "sub": "双 token 头 + traceparent", "icon": "fa-key", "tone": ""},
        ]


def _local_ledger(db, realm: str, days: int) -> dict:
    """本网关账本（直接查 usage_logs）。"""
    from datetime import datetime, timedelta

    from sqlalchemy import func

    from admin.models import Account, UsageLog

    since = datetime.utcnow() - timedelta(days=max(1, int(days)))
    query = db.query(
        UsageLog.model.label("model"),
        func.count(UsageLog.id).label("requests"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0).label("total"),
    ).filter(UsageLog.platform == "loomy", UsageLog.created_at >= since)
    if realm:
        ids = [row[0] for row in db.query(Account.id).filter(
            Account.platform == "loomy", Account.domain == realm).all()]
        if not ids:
            return {"days": days, "items": [], "totals": {}}
        query = query.filter(UsageLog.account_id.in_(ids))
    items = [{"model": r.model or "-", "requests": int(r.requests or 0),
              "total_tokens": int(r.total or 0)}
             for r in query.group_by(UsageLog.model).all()]
    items.sort(key=lambda i: i["total_tokens"], reverse=True)
    return {"days": days, "items": items,
            "totals": {"requests": sum(i["requests"] for i in items),
                       "total_tokens": sum(i["total_tokens"] for i in items)}}


#: 无固定区域的实例
gateway = LoomyGateway("")
