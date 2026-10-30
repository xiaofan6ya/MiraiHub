"""Trae 平台的 Gateway 实现（符合 `admin.platform.gateway.Gateway` 契约）。

## 为什么需要这一层

`main gateway.py` 是**转发实现**（`chat_completions` / `responses` / … 的
具体处理），本模块是**契约适配器**：把转发能力和平台元数据
（账号池操作、任务清单、客户端探测）打包成 admin 认识的标准形状。

分成两个文件而不是合并：
  * `gateway.py` 只关心「怎么把请求转到上游」，被 `dispatch.py` 直接调用，
    是热路径；
  * `platform_gateway.py` 只关心「这个平台对外长什么样」，被 admin 的
    管理接口调用，是冷路径。
混在一起会让热路径 import 一堆管理相关的模块。

## 国内版 / 国际版

两者共用本类：`realm` 由请求上下文决定（`PlatformSpec.realm` 固定），
所以任务清单按 realm 过滤（国际版没有签到）。
"""
from __future__ import annotations

import json
import logging

from admin.platform.gateway import AccountOps, ClientProbe, Gateway

logger = logging.getLogger("admin.platform.trae.platform_gateway")

PLATFORM = "trae"


def _runners_for(realm: str) -> dict:
    """该区的任务清单。

    国际版**没有签到**（上游 `/trae/api/v2/ug/*` 只在 api.trae.cn 注册），
    因此不列 `trae_checkin` —— 否则调度器会按时去打一个必然 404 的接口。
    """
    out = {}
    try:
        from admin.scheduler import run_trae_checkin

        if realm != "intl":
            out["trae_checkin"] = run_trae_checkin
    except Exception:  # noqa: BLE001 - 任务缺失不该让网关起不来
        logger.debug("trae_checkin 装配失败", exc_info=True)
    # 额度刷新：两个区都有
    try:
        from admin.platform.trae.services import pool as trae_pool

        def refresh_quota(db, schedule=None):
            rows = trae_pool.accounts_for(db, realm or None)
            ok = fail = 0
            for account in rows:
                try:
                    trae_pool.refresh_quota(db, account)
                    ok += 1
                except Exception:  # noqa: BLE001 - 单号失败不影响其它号
                    fail += 1
            return {"task": "trae_refresh_quota", "ok": fail == 0,
                    "refreshed": ok, "failed": fail,
                    "summary": "刷新 %d 个账号额度%s" % (
                        ok, "，%d 个失败" % fail if fail else "")}

        out["refresh_quota"] = refresh_quota
    except Exception:  # noqa: BLE001
        logger.debug("refresh_quota 装配失败", exc_info=True)
    return out


def _local_ledger(db, realm: str, days: int) -> dict:
    """本网关自己记录的用量汇总（按模型聚合）。

    这是**经过本系统**的请求，与上游账单口径不同：
      · 上游按 token 折算成美元/积分
      · 本地是实际调用的 token 数与请求数
    两者不可互相校对，但本地数据永远在（不依赖上游凭证），
    排障时更有用。

    直接查 `usage_logs`，不走 `admin.routers.stats` ——
    后者靠 request 头判断平台，从内部调用拿不到正确的 scope。
    """
    from datetime import datetime, timedelta

    from sqlalchemy import func

    from admin.models import UsageLog

    since = datetime.utcnow() - timedelta(days=max(1, int(days)))
    # 落库平台：Trae 两个区共用 `trae`
    platform_name = "trae"

    query = db.query(
        UsageLog.model.label("model"),
        func.count(UsageLog.id).label("requests"),
        func.coalesce(func.sum(UsageLog.prompt_tokens), 0).label("prompt"),
        func.coalesce(func.sum(UsageLog.completion_tokens), 0).label("completion"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0).label("total"),
        func.coalesce(func.sum(UsageLog.credits), 0).label("credits"),
    ).filter(
        UsageLog.platform == platform_name,
        UsageLog.created_at >= since,
    )
    if realm:
        # realm 无法从 usage_logs 直接判断（它只存落库平台名），
        # 这里用账号归属过滤：先取该区的账号 id
        from admin.models import Account

        ids = [row[0] for row in db.query(Account.id).filter(
            Account.platform == platform_name,
            Account.domain == realm).all()]
        if not ids:
            return {"days": days, "realm": realm, "items": [],
                    "totals": {"requests": 0, "total_tokens": 0}}
        query = query.filter(UsageLog.account_id.in_(ids))

    rows = query.group_by(UsageLog.model).all()
    items = [
        {"model": r.model or "—", "requests": int(r.requests or 0),
         "prompt_tokens": int(r.prompt or 0),
         "completion_tokens": int(r.completion or 0),
         "total_tokens": int(r.total or 0),
         "credits": float(r.credits or 0)}
        for r in rows
    ]
    items.sort(key=lambda i: i["total_tokens"], reverse=True)
    return {
        "days": days,
        "realm": realm,
        "items": items,
        "totals": {
            "requests": sum(i["requests"] for i in items),
            "total_tokens": sum(i["total_tokens"] for i in items),
            "credits": round(sum(i["credits"] for i in items), 4),
        },
    }


class TraeAccountOps:
    """Trae 账号池的 11 项操作。

    迁移期实现委托给 `admin/trae/services/` 与 `admin/trae/routers/`。
    这些实现本来就在 Trae 自己的目录里（不像 WorkBuddy 散在 admin 各处），
    所以这里只是把已有函数收拢成契约形状。
    """

    def __init__(self, realm: str = "") -> None:
        self.realm = realm

    def _db(self):
        from admin.db import SessionLocal

        return SessionLocal()

    def list(self, db):
        from admin.platform.trae.services import pool as trae_pool
        from admin.platform.trae.services import accounts as trae_accounts

        rows = trae_pool.accounts_for(db, self.realm or None)
        return [trae_accounts.public_view(r) for r in rows]

    def scan(self):
        from admin.platform.trae.services import accounts as trae_accounts

        return trae_accounts.discover()

    def import_local(self, db):
        from admin.platform.trae.services import accounts as trae_accounts

        return trae_accounts.import_local(db)

    def import_record(self, db, record):
        from admin.platform.trae.services import accounts as trae_accounts

        return trae_accounts.import_record(db, record)

    def refresh(self, db, account_id, *, force=False):
        from admin.models import Account
        from admin.platform.trae.services import pool as trae_pool

        account = db.query(Account).filter(Account.id == account_id).first()
        if account is None:
            return {"ok": False, "message": "账号不存在"}
        return trae_pool.refresh_quota(db, account, force=force)

    def keepalive(self, db):
        from admin.platform.trae.core import client as trae_client
        from admin.platform.trae.services import pool as trae_pool

        rows = trae_pool.accounts_for(db, self.realm or None)
        ok = fail = 0
        for account in rows:
            try:
                creds = trae_pool.credentials(db, account)
                trae_client.refresh_account(creds)
                ok += 1
            except Exception:  # noqa: BLE001
                fail += 1
        return {"ok": fail == 0, "refreshed": ok, "failed": fail,
                "summary": "保活 %d 个账号%s" % (ok, "，%d 失败" % fail if fail else "")}

    def checkin(self, db):
        from admin.scheduler import run_trae_checkin

        if self.realm == "intl":
            # 明确「不支持」而不是伪造成功
            return {"supported": False,
                    "summary": "Trae 国际版没有签到接口"}
        return run_trae_checkin(db, None)

    def credits(self, db):
        from admin.platform.trae.services import pool as trae_pool

        rows = trae_pool.accounts_for(db, self.realm or None)
        summary = trae_pool.summarize(rows)
        return {"summary": summary, "realm": self.realm,
                "items": [trae_pool.quota_detail(r) for r in rows]}

    def usage(self, db, **filters):
        """用量明细。

        ## 数据来源与「为什么是 0」

        上游 `query_user_usage_group_by_session` **只认网页会话 cookie**，
        IDE token 调它永远返回 `total=0`（实测：6 个时间窗 × 8 组参数，
        传错参数会报 9004，证明鉴权通过、接口只是不返数据）。

        所以这里的返回必须能区分三种情况，否则用户会以为「没用过」：

          need_web_cookie=True  -> 缺网页 Cookie，去补
          source=upstream 且 total=0 -> 有 Cookie，真的没有记录
          errors 非空           -> 上游/网络出错

        本地 `usage_logs` 始终作为对照返回：那是**经过本网关**的请求，
        与上游账单口径不同，但对排障更有用。
        """
        from admin.platform.trae.core import client as trae_client
        from admin.platform.trae.services import pool as trae_pool

        days = int(filters.get("days") or 30)
        rows = trae_pool.accounts_for(db, self.realm or None)
        sessions: list[dict] = []
        errors: list[dict] = []
        missing_cookie: list[int] = []
        upstream_ok = 0

        for account in rows:
            try:
                creds = trae_pool.credentials(db, account)
                data = trae_client.fetch_usage_by_session(
                    creds, self.realm or "cn", days=days)
            except Exception as exc:  # noqa: BLE001 - 单账号失败不影响其它号
                errors.append({"account_id": account.id,
                               "account": account.name,
                               "error": str(exc)[:160]})
                continue

            if data.get("need_web_cookie"):
                missing_cookie.append(account.id)
                continue

            upstream_ok += 1
            for item in data.get("user_usage_group_by_sessions") or []:
                sessions.append({"account_id": account.id,
                                 "account": account.name, **item})

        # 本地账本：始终返回，作为「经过本网关的请求」的对照口径。
        #
        # ⚠️ 不要调 `admin.routers.stats.usage` —— 那个函数不存在
        # （真名 `usage_stats`，且它靠 request 头判断平台，不吃 platform 参数）。
        # 之前这里被 try/except 吞掉，导致「本网关记录」永远为空，
        # 让用户误以为一条请求都没走过。直接查 `usage_logs` 最可靠。
        local = _local_ledger(db, self.realm, days)

        need_cookie = bool(missing_cookie) and not sessions
        return {
            "realm": self.realm,
            "days": days,
            "source": "upstream" if upstream_ok else ("need_cookie" if need_cookie else "local"),
            "total": len(sessions),
            "sessions": sessions[:200],
            "local": local,
            "errors": errors,
            #: 缺网页 Cookie 的账号（前端据此显示「去补充 Cookie」按钮）
            "need_web_cookie": need_cookie,
            "accounts_missing_cookie": missing_cookie,
            "accounts_queried": upstream_ok,
            "hint": ("查上游用量明细需要网页会话 cookie："
                     "登录对应站点后从浏览器开发者工具复制 Cookie 请求头，"
                     "在账号里填入「网页 Cookie」。IDE 凭证调这个接口恒为空。"
                     if need_cookie else ""),
        }

    def health(self, db, account_id):
        from admin.platform.trae.routers import accounts as trae_router

        return trae_router.test_account(account_id, _=True, db=db)

    def inject(self, db, account_id):
        """把凭证写回客户端 —— Trae 的注入是「写 storage.json」。

        当前实现返回明确的不支持：写客户端存储属于高风险操作，
        需要先确认备份/回滚路径，见 docs/GATEWAY_ARCHITECTURE.md §8。
        """
        return {"supported": False,
                "message": "Trae 客户端注入尚未实现（需先有备份/回滚）"}

    def export(self, db, ids=None):
        from admin.platform.trae.services import pool as trae_pool

        rows = trae_pool.accounts_for(db, self.realm or None)
        if ids:
            rows = [r for r in rows if r.id in set(ids)]
        from admin.platform.trae.services.accounts import public_view

        return {"format": "trae-backup", "realm": self.realm,
                "accounts": [public_view(r) for r in rows]}


class TraeClientProbe:
    """Trae 客户端探测（storage.json / state.vscdb）。"""

    def __init__(self, realm: str = "") -> None:
        self.realm = realm or "cn"

    def dirs(self) -> list[str]:
        from admin.platform.trae.services.accounts import storage_paths

        out = []
        for entry in storage_paths():
            if not self.realm or entry.get("realm") == self.realm:
                path = entry.get("path") or ""
                if path:
                    out.append(path)
        return out

    def probe(self) -> dict:
        from admin.platform.trae.services import desktop_catalog

        realm = self.realm
        info = {"realm": realm, "storage": self.dirs(),
                "desktop_installed": False, "model_count": 0}
        try:
            data = desktop_catalog.read_models(realm)
            info["model_count"] = len(data.get("models") or [])
            info["desktop_installed"] = bool(data.get("models"))
            if data.get("error"):
                info["error"] = data["error"]
        except Exception as exc:  # noqa: BLE001
            info["error"] = str(exc)[:200]
        result = info
        # 补 app_path / app_version / ua（路径表在本平台 core/paths.py）
        from admin.platform.probe import enrich
        from admin.platform.trae.core import paths as _paths
        return enrich(result,
                      exe_candidates=_paths.APP_EXE_CANDIDATES,
                      data_dirs=_paths.DATA_DIR_CANDIDATES,
                      product=_paths.UA_PRODUCT)

    def schema(self) -> list[dict]:
        """可修改项：目前只暴露「模型目录来源」这类只读信息。

        Trae 的客户端参数大多由客户端自己管理，改它们容易让客户端
        校验失败，所以这里**不提供写入项**，只如实返回空列表 ——
        管理界面会显示「该平台没有可修改项」，而不是给一堆假开关。
        """
        return []

    def read(self, key: str):
        return None

    def write(self, key: str, value) -> dict:
        return {"ok": False, "message": "Trae 客户端参数不支持在线修改"}


class TraeGateway:
    """Trae 的 Gateway 实现。

    `realm` 参数区分国内版与国际版；两者共用本类。
    """

    platform_id = PLATFORM

    def __init__(self, realm: str = "") -> None:
        self.realm = realm

    # ---- 转发：委托给 main gateway.py（热路径保持原样）----
    def chat_completions(self, request, body):
        from admin.platform.trae import gateway

        return gateway.chat_completions(request, body)

    def responses(self, request, body):
        from admin.platform.trae import gateway

        return gateway.responses(request, body)

    def messages(self, request, body):
        from admin.platform.trae import gateway

        return gateway.messages(request, body)

    def count_tokens(self, request, body):
        from admin.platform.trae import gateway

        return gateway.count_tokens(request, body)

    # ---- 目录 ----
    def list_models(self, request):
        from admin.platform.trae import gateway

        return gateway.list_models(request)

    def balance(self, request):
        from admin.platform.trae import gateway

        return gateway.balance(request)

    # ---- 账号池 ----
    def account_ops(self) -> AccountOps:
        return TraeAccountOps(self.realm)

    # ---- 定时任务 ----
    def task_runners(self) -> dict:
        runners = _runners_for(self.realm)
        # 保活：Trae 的 IDE token 会被上游按会话回收，定时续期一次。
        runners["trae_keepalive"] = self.account_ops().keepalive
        return runners

    # ---- 客户端探测 ----
    def client_probe(self) -> ClientProbe:
        return TraeClientProbe(self.realm)

    # ---- 概览卡片 ----
    def overview_cards(self, db) -> list[dict]:
        """Trae 的统计卡片。

        ## 两区口径完全不同（这是必须分开显示的原因）

        | 区域 | 计费模式 | 单位 | 含义 |
        |---|---|---|---|
        | 国内版 | `is_credits_billing` | `credit` | 积分 + 倍率，用完为止 |
        | 国际版（付费） | `is_dollar_usage_billing` | `usd` | 按实际 token 折算美元余额 |
        | 国际版（免费） | 请求次数限额 | `percent` | 额度水位（0–100），不是钱 |

        所以标签与后缀都从 `unit` 推导，**不硬编码「积分」二字**。
        免费版没有美元余额（`charge_amount: 0`），显示「请求额度」才准确。
        """
        from admin.platform.trae.services import pool as trae_pool

        try:
            rows = trae_pool.accounts_for(db, self.realm or None)
            summary = trae_pool.summarize(rows)
        except Exception:  # noqa: BLE001 - 统计失败不该让概览打不开
            rows, summary = [], {}

        total = int(summary.get("total") or len(rows))
        active = int(summary.get("active") or 0)
        cooling = int(summary.get("cooling") or 0)

        # 额度口径：优先用快照里的 unit，没有就按区域推断
        unit = ""
        for row in rows:
            try:
                snap = json.loads(getattr(row, "credits_snapshot", "") or "{}")
            except (ValueError, TypeError):
                snap = {}
            if isinstance(snap, dict) and snap.get("unit"):
                unit = str(snap["unit"])
                break
        if not unit:
            unit = "percent" if (self.realm or "") == "intl" else "credit"

        remain = summary.get("balance_remain")
        if remain is None:
            remain = sum(int(getattr(r, "balance_remain", 0) or 0) for r in rows)
        limit = summary.get("balance_total")
        if limit is None:
            limit = sum(int(getattr(r, "balance_total", 0) or 0) for r in rows)

        if unit == "usd":
            # 美元口径。
            #
            # ⚠️ **不能把多个账号的额度相加**：每个账号各有独立的 $1.00
            # 免费额度，两个账号相加显示 "$0.66 / $2.00" 会让人以为
            # 这是一个共享的 $2 池子 —— 实际是两份互不通用的额度。
            # 所以显示**当前账号的真实数值**（每个号都一样上限时无歧义），
            # 并在副标题里说明账号数与合计消耗。
            label = "美元额度"
            per_account = []
            for row in rows:
                try:
                    snap = json.loads(getattr(row, "credits_snapshot", "") or "{}")
                except (ValueError, TypeError):
                    snap = {}
                if not isinstance(snap, dict):
                    continue
                total_usd = snap.get("usd_total")
                if total_usd:
                    per_account.append({
                        "id": getattr(row, "id", 0),
                        "used": float(snap.get("usd_used") or 0),
                        "total": float(total_usd),
                    })

            def _usd(amount_value: float) -> str:
                """美元金额格式化：**小金额保留有效小数位**。

                `{:.2f}` 会把 $0.0007 显示成 $0.00 —— 会看到
                「已用 0」会以为统计坏了。按量级自适应：
                  · >= $0.01    -> 两位小数（$0.66）
                  · >= $0.0001  -> 四位小数（$0.0007）
                  · 更小         -> 六位小数（$0.000660）
                """
                amount = float(amount_value or 0)
                if amount == 0:
                    return "$0.00"
                if amount >= 0.01:
                    return "${:,.2f}".format(amount)
                if amount >= 0.0001:
                    return "${:,.4f}".format(amount)
                return "${:,.6f}".format(amount)

            if per_account:
                # 上限一致（Free plan 都是 $1）时展示单个账号的口径；
                # 上限不一致时按「合计已用 / 合计上限」并注明是合计。
                totals = {round(item["total"], 6) for item in per_account}
                sum_used = sum(item["used"] for item in per_account)
                if len(totals) == 1:
                    # 取消耗最多的那个号作为代表（用户最关心「快用完了吗」）
                    peak = max(per_account, key=lambda i: i["used"])
                    value = "%s / %s" % (_usd(peak["used"]), _usd(peak["total"]))
                    if len(per_account) > 1:
                        sub = ("单账号上限 %s · 共 %d 个账号，合计已用 %s"
                               % (_usd(peak["total"]), len(per_account), _usd(sum_used)))
                    else:
                        sub = "按 token 折算的美元额度（已用 / 上限）"
                else:
                    total_all = sum(item["total"] for item in per_account)
                    value = "%s / %s" % (_usd(sum_used), _usd(total_all))
                    sub = "多账号合计（各号上限不同）"
            else:
                value = "${:,.2f}".format(float(remain or 0) / 100.0)
                sub = "按 token 折算的美元额度"
        elif unit == "percent":
            # ⚠️ 百分比**不能求和**：两个账号各 100% 相加会变成 200%，
            # 那是个不存在的数。多账号时给**平均值**（整体水位），
            # 并在副标题里说明样本数。
            percents = []
            for row in rows:
                try:
                    snap = json.loads(getattr(row, "credits_snapshot", "") or "{}")
                except (ValueError, TypeError):
                    snap = {}
                val = snap.get("remain") if isinstance(snap, dict) else None
                if isinstance(val, (int, float)):
                    percents.append(float(val))
            if percents:
                avg = sum(percents) / len(percents)
            else:
                avg = float(remain or 0) / max(1, total)
            label = "请求额度"
            value = "%.0f%%" % avg
            sub = ("免费版按请求次数限额，非金额（%d 个账号均值）" % len(percents)
                   if len(percents) > 1 else "免费版按请求次数限额，非金额")
        else:
            label = "可用积分"
            value = "%d" % int(remain or 0)
            sub = "总额度 %d · 积分+倍率计费" % int(limit or 0)

        # 区域分布：本平台只统计自己那个区，另一区显示 0
        other = "国际版" if self.realm != "intl" else "国内版"
        mine = "国内版" if self.realm != "intl" else "国际版"

        return [
            {"key": "accounts", "label": "账号总数", "value": total,
             "sub": "启用 %d · 冷却 %d" % (active, cooling),
             "icon": "fa-users", "tone": ""},
            {"key": "credits", "label": label, "value": value,
             "sub": sub, "icon": "fa-wallet",
             "tone": "brand" if unit != "percent" else ""},
            {"key": "realm", "label": "区域分布",
             "value": "%s %d · %s 0" % (mine, total, other),
             "sub": "两个区独立凭证与模型目录", "icon": "fa-globe", "tone": ""},
            {"key": "mode", "label": "接入方式", "value": "反向代理",
             "sub": "复用系统 Key / 号池 / 用量 / 积分",
             "icon": "fa-network-wired", "tone": ""},
        ]


#: 供 `PlatformSpec.gateway = "admin.platform.trae.platform_gateway:gateway"` 使用。
#: 返回一个**无固定区域**的实例：具体区域由调用方（dispatch / 管理接口）
#: 按请求上下文决定。
gateway = TraeGateway("")