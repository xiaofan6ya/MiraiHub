"""Freebuff Gateway 契约实现（账号池 / 任务 / 探测 / 卡片）。

## 与其它平台的差异

* **凭证只有 API key**：没有 OAuth、没有本机客户端可扫、没有 refresh。
  `scan()` / `import_local()` / `inject()` 一律如实返回「不支持」。
* **没有余额接口**：上游只有 `/models` 与 `/chat/completions`。
  `credits()` 报本地账本与 key 校验状态，不编一个假的余额数字。
* **没有签到**：`checkin()` 明确 `supported=False`。
* **探活不等于验证 key**：`/models` 免鉴权，拉得到目录证明不了 key 有效；
  `verify_state` 只在真实对话成功（`ok`）或被 401 拒绝（`rejected`）时翻转。
"""
from __future__ import annotations

import logging

from admin.models import Account
from admin.platform.freebuff.core import client, endpoints
from admin.platform.freebuff.services import accounts as fb_accounts

logger = logging.getLogger("admin.platform.freebuff.platform_gateway")


class FreebuffAccountOps:
    """账号池 11 项操作。"""

    def __init__(self, realm: str = "cn"):
        self.realm = realm

    def list(self, db):
        """账号列表 —— 只读库。探活打的是免鉴权目录，没必要放列表里。

        `usage_today` 由本地账本一次 GROUP BY 算出来，控制台的「用量明细」
        直接读它，不用逐账号打接口（上游也没有用量接口可打）。
        """
        views = [fb_accounts.public_view(account)
                 for account in fb_accounts.accounts_for(db, self.realm)]
        today_map = self._usage_today(db, [row["id"] for row in views])
        for row in views:
            row["usage_today"] = today_map.get(
                row["id"], {"requests": 0, "tokens": 0, "credits": 0})
        return {"items": views, "summary": fb_accounts.summarize(views)}

    @staticmethod
    def _usage_today(db, account_ids: list[int]) -> dict:
        """今日每个账号的调用次数 / token / 记帐 credits（一次查询）。"""
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
                .filter(UsageLog.platform == fb_accounts.PLATFORM,
                        UsageLog.account_id.in_(account_ids),
                        UsageLog.created_at >= midnight)
                .group_by(UsageLog.account_id).all())
        return {row.account_id: {"requests": int(row.requests or 0),
                                 "tokens": int(row.tokens or 0),
                                 "credits": round(float(row.credits or 0), 2)}
                for row in rows}

    def scan(self):
        return {"ok": False, "reason": "no_local_client",
                "hint": "Freebuff 没有可扫描的本机登录态；"
                        "请到 https://freebuff.com/account/api 创建 "
                        "Developer API key 后导入"}

    def import_local(self, db):
        return {"ok": False, "imported": 0, "reason": "no_local_client"}

    def import_record(self, db, record):
        return fb_accounts.import_record(db, record)

    def refresh(self, db, account_id, *, force=False):
        account = db.query(Account).filter(
            Account.id == int(account_id),
            Account.platform == fb_accounts.PLATFORM).first()
        if account is None:
            raise ValueError("账号不存在")
        return fb_accounts.probe(db, account)

    def keepalive(self, db):
        return fb_accounts.keepalive(db, self.realm)

    def checkin(self, db):
        return {"supported": False,
                "reason": "Freebuff 没有签到接口（上游只有 /models 与 "
                          "/chat/completions）"}

    def activity(self, db):
        """每日活跃对话：发一次极小请求，保活 + **顺手验证 key**。

        Freebuff 没有校验 key 的轻量端点，`unverified` 的账号只能靠真实
        对话翻状态 —— 这个任务同时解决「保活」与「验证」两件事。
        """
        from admin.platform.freebuff.services import activity as fb_activity

        return fb_activity.run_daily_activity(db, self.realm)

    def ban_recheck(self, db):
        """失效账号复检：被 401 禁用的 key 又活了就恢复。"""
        from admin.platform.freebuff.services import activity as fb_activity

        return fb_activity.recheck_disabled(db, self.realm)

    def credits(self, db):
        rows = fb_accounts.accounts_for(db, self.realm)
        verified = sum(1 for account in rows
                       if fb_accounts.credentials_of(account).get("verify_state")
                       == "ok")
        return {"unit": "credits", "quota_kind": "unknown",
                "remain": 0, "total": 0, "accounts": len(rows),
                "verified": verified,
                "note": "上游没有余额接口，额度以 freebuff.com 控制台为准"}

    def usage(self, db, **filters):
        return fb_accounts.usage(db, self.realm,
                                 int(filters.get("days") or 30))

    def health(self, db, account_id):
        account = db.query(Account).filter(
            Account.id == int(account_id),
            Account.platform == fb_accounts.PLATFORM).first()
        if account is None:
            return {"ok": False, "error": "账号不存在"}
        return fb_accounts.health(db, account)

    def inject(self, db, account_id):
        return {"ok": False, "reason": "not_supported",
                "hint": "Freebuff 没有可写账号槽位的本地客户端"}

    def export(self, db, ids=None):
        return fb_accounts.export_records(db, self.realm)


class FreebuffClientProbe:
    """没有本机客户端可探测（API key 是唯一入口）。"""

    def dirs(self):
        return []

    def probe(self):
        return {"ok": False, "reason": "no_local_client",
                "note": "Freebuff 走 Developer API key，没有本机客户端登录态"}

    def schema(self):
        return []

    def read(self, key):
        return None

    def write(self, key, value):
        return {"ok": False, "reason": "read_only"}


class FreebuffGateway:
    platform_id = endpoints.PUBLIC

    def __init__(self, realm: str = "cn"):
        self.realm = realm
        self._ops = FreebuffAccountOps(realm)
        self._probe = FreebuffClientProbe()

    # ---- 转发 ----
    def chat_completions(self, request, body):
        from admin.platform.freebuff import gateway as impl

        return impl.chat_completions(request)

    def responses(self, request, body):
        from admin.platform.freebuff import gateway as impl

        return impl.responses(request)

    def messages(self, request, body):
        from admin.platform.freebuff import gateway as impl

        return impl.messages(request)

    def count_tokens(self, request, body):
        from admin.platform.freebuff import gateway as impl

        return impl.count_tokens(request)

    # ---- 目录 ----
    def list_models(self, request):
        from admin.platform.freebuff import gateway as impl

        return impl.list_models(request)

    def balance(self, request):
        from admin.platform.freebuff import gateway as impl

        return impl.balance(request)

    # ---- 账号池 ----
    def account_ops(self):
        return self._ops

    # ---- 定时任务 ----
    def task_runners(self):
        return {
            "freebuff_keepalive": self._ops.keepalive,
            "freebuff_activity": self._ops.activity,
            "freebuff_ban_recheck": self._ops.ban_recheck,
        }

    # ---- 客户端探测 ----
    def client_probe(self):
        return self._probe

    # ---- 概览卡片 ----
    def overview_cards(self, db):
        rows = fb_accounts.accounts_for(db, self.realm)
        active = sum(1 for account in rows
                     if (getattr(account, "status", "") or "") == "active")
        verified = sum(1 for account in rows
                       if fb_accounts.credentials_of(account).get("verify_state")
                       == "ok")
        try:
            models, _note = fb_accounts.catalog_rows()
            model_count = len(models)
        except Exception:  # noqa: BLE001 - 卡片不能把概览页打挂
            model_count = 0
        return [
            {"key": "accounts", "label": "账号总数", "value": len(rows),
             "sub": "启用 %d · 已禁用 %d" % (active, len(rows) - active),
             "icon": "fa-users", "tone": ""},
            {"key": "verified", "label": "已验证 Key", "value": verified,
             "sub": "其余待首次对话确认" if verified < len(rows) else "全部可用",
             "icon": "fa-key",
             "tone": "" if verified else ("warn" if rows else "")},
            {"key": "models", "label": "可用模型", "value": model_count,
             "sub": "上游 /models 目录", "icon": "fa-cube", "tone": ""},
        ]


#: 无多区概念的实例
gateway = FreebuffGateway("cn")