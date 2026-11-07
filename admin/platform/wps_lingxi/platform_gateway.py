"""WPS 灵犀 Gateway 契约实现（账号池 / 任务 / 探测 / 卡片）。

## 与其它平台的差异

* **没有 token 刷新**：上游只认 `wps_sid`，过期只能重新粘 Cookie ——
  所以 `refresh()` 做的是「验一下还能不能用 + 同步余额」，不是换 token。
* **有签到**：任务中心的 `daily_check_in`，见 `services/checkin.py`。
* **额度是智点**：`credits/balance` 的 `total_balance`，赠送批次按到期时间
  分出「临时积分」（15 天内到期）。
* **有本机客户端**：Windows/macOS 的灵犀客户端 Cookie 库可扫描导入
  （DPAPI + AES-GCM），见 `services/accounts.py`。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from admin.platform.wps_lingxi.core import endpoints
from admin.platform.wps_lingxi.services import accounts as lingxi_accounts
from admin.platform.wps_lingxi.services import activity as lingxi_activity
from admin.platform.wps_lingxi.services import checkin as lingxi_checkin

logger = logging.getLogger("admin.platform.wps_lingxi.platform_gateway")


class WpsLingxiAccountOps:
    """账号池 11 项操作。"""

    def __init__(self, realm: str = ""):
        self.realm = realm

    def list(self, db):
        """账号列表 —— **只读库，不打上游**。

        批量探活在这里做的话，几十个号就是几十次上游请求，控制台会超时。
        余额走 `keepalive`（定时任务）或显式 `refresh`。

        `usage_today` 由本地账本聚合（一次分组查询），控制台的「用量明细」
        表格直接读这三个字段，不用再逐个账号打接口。
        """
        views = [lingxi_accounts.public_view(account)
                 for account in lingxi_accounts.accounts_for(db, self.realm)]
        today_map = self._usage_today(db, [row["id"] for row in views])
        for row in views:
            row["usage_today"] = today_map.get(row["id"],
                                              {"requests": 0, "tokens": 0, "credits": 0})
        return {"items": views, "summary": lingxi_accounts.summarize(views)}

    @staticmethod
    def _usage_today(db, account_ids: list[int]) -> dict:
        """今日每个账号的调用次数 / token / 智点（一次 GROUP BY，不 N+1）。"""
        from sqlalchemy import func

        from admin.models import UsageLog

        if not account_ids:
            return {}
        midnight = datetime.utcnow().replace(hour=0, minute=0, second=0,
                                            microsecond=0)
        rows = (db.query(UsageLog.account_id,
                         func.count(UsageLog.id).label("requests"),
                         func.sum(UsageLog.total_tokens).label("tokens"),
                         func.sum(UsageLog.credits).label("credits"))
                .filter(UsageLog.platform == lingxi_accounts.PLATFORM,
                        UsageLog.account_id.in_(account_ids),
                        UsageLog.created_at >= midnight)
                .group_by(UsageLog.account_id).all())
        return {row.account_id: {"requests": int(row.requests or 0),
                                 "tokens": int(row.tokens or 0),
                                 "credits": round(float(row.credits or 0), 2)}
                for row in rows}

    def scan(self):
        result = lingxi_accounts.scan_local()
        return {"ok": True, "items": result.get("items") or [],
                "errors": result.get("errors") or [],
                "hint": result.get("hint") or ""}

    def import_local(self, db):
        return lingxi_accounts.import_local(db)

    def import_record(self, db, record):
        return lingxi_accounts.import_record(db, record)

    def refresh(self, db, account_id, *, force=False):
        from admin.models import Account

        account = db.query(Account).filter(
            Account.id == int(account_id),
            Account.platform == lingxi_accounts.PLATFORM).first()
        if account is None:
            raise ValueError("账号不存在")
        # 灵犀没有 refresh token：重新验一次 Cookie 归属，顺带刷余额
        cookie = lingxi_accounts.cookie_of(account)
        if cookie:
            try:
                profile = lingxi_accounts.client.fetch_profile(cookie)
                data = lingxi_accounts.credentials_of(account)
                data["base"] = profile.get("base") or data.get("base") or \
                    endpoints.base_of()
                data["uid"] = data.get("uid") or profile.get("uid") or ""
                data["probed_at"] = int(datetime.utcnow().timestamp())
                if profile.get("name"):
                    account.name = profile["name"]
                account.auth_json = json.dumps(data, ensure_ascii=False)[:20000]
            except lingxi_accounts.client.UpstreamError as exc:
                return {"ok": False,
                        "error": exc.to_dict(),
                        "hint": "灵犀没有刷新接口，请重新粘贴 Cookie"}
        return lingxi_accounts.sync_quota(db, account)

    def keepalive(self, db):
        return lingxi_accounts.keepalive(db, self.realm)

    def checkin(self, db):
        return lingxi_checkin.run_daily_checkin(db, None, self.realm)

    def activity(self, db):
        """每日活跃对话（模拟真人使用）：**不能只签到 + 刷额度**。"""
        return lingxi_activity.run_daily_activity(db, self.realm)

    def ban_recheck(self, db):
        """失效账号复检：Cookie 又有效了就恢复，避免永久躺平。"""
        return lingxi_activity.recheck_disabled(db, self.realm)

    def credits(self, db):
        rows = lingxi_accounts.accounts_for(db, self.realm)
        remain = sum(int(getattr(account, "balance_remain", 0) or 0)
                     for account in rows)
        total = sum(int(getattr(account, "balance_total", 0) or 0)
                    for account in rows)
        expiring = sum(int(getattr(account, "credits_expiring_soon", 0) or 0)
                       for account in rows)
        return {"unit": "credits", "remain": remain, "total": total or remain,
                "expiring": expiring, "accounts": len(rows),
                "quota_kind": "balance"}

    def usage(self, db, **filters):
        from sqlalchemy import func

        from admin.models import Account, UsageLog

        days = int(filters.get("days") or 30)
        since = datetime.utcnow() - timedelta(days=days)
        query = db.query(
            UsageLog.model,
            func.count(UsageLog.id).label("requests"),
            func.sum(UsageLog.total_tokens).label("total"),
        ).filter(UsageLog.platform == lingxi_accounts.PLATFORM,
                 UsageLog.created_at >= since)
        if self.realm:
            ids = [row[0] for row in db.query(Account.id).filter(
                Account.platform == lingxi_accounts.PLATFORM,
                Account.domain == self.realm).all()]
            if not ids:
                return {"days": days, "items": [], "totals": {}}
            query = query.filter(UsageLog.account_id.in_(ids))
        items = [{"model": row.model or "-",
                  "requests": int(row.requests or 0),
                  "total_tokens": int(row.total or 0)}
                 for row in query.group_by(UsageLog.model).all()]
        items.sort(key=lambda item: item["total_tokens"], reverse=True)
        return {"days": days, "items": items,
                "totals": {"requests": sum(item["requests"] for item in items),
                           "total_tokens": sum(item["total_tokens"]
                                               for item in items)}}

    def health(self, db, account_id):
        from admin.models import Account

        account = db.query(Account).filter(
            Account.id == int(account_id),
            Account.platform == lingxi_accounts.PLATFORM).first()
        if account is None:
            return {"ok": False, "error": "账号不存在"}
        return lingxi_accounts.health(db, account)

    def inject(self, db, account_id):
        """灵犀客户端不支持注入账号：如实返回不支持。"""
        return {"ok": False, "reason": "not_supported",
                "hint": "灵犀客户端没有可写的账号槽位，请用「复制 Cookie」手动登录"}

    def export(self, db, ids=None):
        return lingxi_accounts.export_records(db, self.realm)


class WpsLingxiClientProbe:
    """本机灵犀客户端探测（目录 / Cookies 库）。"""

    def dirs(self):
        return lingxi_accounts.client_dirs()

    def probe(self):
        dirs = lingxi_accounts.client_dirs()
        dbs = lingxi_accounts.cookie_dbs()
        return {"ok": bool(dirs), "dirs": dirs, "cookie_dbs": dbs,
                "has_login": bool(dbs),
                "note": "灵犀客户端的登录态在 Cookies 库里，可「导入本机账号」"}

    def schema(self):
        return [{"key": "home", "label": "客户端数据目录",
                 "type": "string",
                 "default": (lingxi_accounts.client_dirs() or [""])[0]}]

    def read(self, key):
        dirs = lingxi_accounts.client_dirs()
        if key == "home":
            return dirs[0] if dirs else ""
        return None

    def write(self, key, value):
        return {"ok": False, "reason": "read_only",
                "hint": "灵犀客户端没有可修改的网关参数"}


class WpsLingxiGateway:
    platform_id = endpoints.PUBLIC

    def __init__(self, realm: str = ""):
        self.realm = realm
        self._ops = WpsLingxiAccountOps(realm)
        self._probe = WpsLingxiClientProbe()

    # ---- 转发 ----
    def chat_completions(self, request, body):
        from admin.platform.wps_lingxi import gateway as impl

        return impl.chat_completions(request)

    def responses(self, request, body):
        from admin.platform.wps_lingxi import gateway as impl

        return impl.responses(request)

    def messages(self, request, body):
        from admin.platform.wps_lingxi import gateway as impl

        return impl.messages(request)

    def count_tokens(self, request, body):
        from admin.platform.wps_lingxi import gateway as impl

        return impl.count_tokens(request)

    # ---- 目录 ----
    def list_models(self, request):
        from admin.platform.wps_lingxi import gateway as impl

        return impl.list_models(request)

    def balance(self, request):
        from admin.platform.wps_lingxi import gateway as impl

        return impl.balance(request)

    # ---- 账号池 ----
    def account_ops(self):
        return self._ops

    # ---- 定时任务 ----
    def task_runners(self):
        return {
            "wps_lingxi_keepalive": self._ops.keepalive,
            "wps_lingxi_checkin": self._ops.checkin,
            "wps_lingxi_activity": self._ops.activity,
            "wps_lingxi_ban_recheck": self._ops.ban_recheck,
        }

    # ---- 客户端探测 ----
    def client_probe(self):
        return self._probe

    # ---- 概览卡片 ----
    def overview_cards(self, db):
        rows = lingxi_accounts.accounts_for(db, self.realm)
        active = sum(1 for account in rows
                     if (getattr(account, "status", "") or "") == "active")
        points = sum(int(getattr(account, "balance_remain", 0) or 0)
                     for account in rows)
        expiring = sum(int(getattr(account, "credits_expiring_soon", 0) or 0)
                       for account in rows)
        return [
            {"key": "accounts", "label": "账号总数", "value": len(rows),
             "sub": "启用 %d · 已禁用 %d" % (active, len(rows) - active),
             "icon": "fa-users", "tone": ""},
            {"key": "credits", "label": "可用智点", "value": points,
             "sub": "临时积分 %d" % expiring if expiring else "灵犀智点",
             "icon": "fa-coins", "tone": "brand" if points else ""},
        ]


#: 无固定区域的实例
gateway = WpsLingxiGateway("")