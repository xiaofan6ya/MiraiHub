"""KroWork 管理契约：账号池 11 项 / 定时任务 / 客户端探测 / 概览卡片。

冷路径。转发热路径在 `gateway.py`，这里只回答「这个平台怎么管理自己」。
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.krowork.core import client, endpoints
from admin.platform.krowork.core import token as token_mod
from admin.platform.krowork.services import accounts as ka

logger = logging.getLogger("admin.platform.krowork.platform_gateway")

PUBLIC = ka.PLATFORM


def _first_number(data, keys) -> float:
    """从上游返回里挑第一个能转成数字的字段（字段名没有文档，做容错）。"""
    if not isinstance(data, dict):
        return 0.0
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


# ---------------------------------------------------------------------------
# 定时任务
# ---------------------------------------------------------------------------
def _run_keepalive(db: Session, schedule=None) -> dict:
    """批量续期：临期（<20 分钟）的号换新票，**不依赖客户端**。

    ## 时间窗为什么是 20 分钟

    `id_token` 只有 30 分钟寿命。窗口太小（比如 5 分钟）会赶不上——
    定时任务本身可能间隔 10~15 分钟，落在窗口外就漏掉一整轮；
    20 分钟窗口 + 每 10 分钟跑一次，任何一枚票都会被覆盖两次。

    ## 续得上续不上，看 refresh_token 的类型

    | refresh 类型 | 结果 |
    |---|---|
    | 离线型（授权时带 `offline_access`） | 换到新票，**refresh 轮换后写回库**，可一直续 |
    | 会话型（旧流程，scope 只有 `openid`） | `invalid_grant` → 记为需要重新授权，**不再每请求重试** |

    所以本任务同时承担「告诉用户哪几个号必须重新授权」的职责。
    """
    rows = [row for row in ka.accounts_for(db) if (row.status or "active") == "active"]
    if not rows:
        return {"ok": True, "checked": 0, "refreshed": 0,
                "summary": "没有 active 的 KroWork 账号"}

    refreshed = 0
    reused = 0
    need_reauth: list[str] = []
    for row in rows:
        data = ka.credentials_of(row)
        exp = int(data.get("expires_at") or 0)
        # 还早得很就跳过（留 20 分钟窗口）
        if exp and exp - 1200 > time.time():
            continue
        try:
            ka.ensure_token(db, row, force=True)
            refreshed += 1
        except Exception as exc:  # noqa: BLE001
            need_reauth.append("#%s %s" % (row.id, str(exc)[:100]))
            continue
        # ensure_token 失败但没抛（用旧票兜底）→ 也要标出来
        blocked = float(ka.credentials_of(row).get("refresh_blocked_until") or 0)
        if blocked > time.time():
            reused += 1
            need_reauth.append("#%s 需重新授权：%s"
                               % (row.id,
                                  str(ka.credentials_of(row).get("refresh_blocked_reason")
                                      or "")[:90]))
    ok = not need_reauth
    summary = "续期 %d" % refreshed
    if reused:
        summary += " · 需重新授权 %d" % reused
    return {"ok": ok, "checked": len(rows), "refreshed": refreshed,
            "need_reauth": need_reauth[:8], "summary": summary}


def _run_quota(db: Session, schedule=None) -> dict:
    """定时刷积分并写回 `balance_*` 列 + 逐包到期快照。

    这两列/两张表分工不同：
      * `balance_total` / `balance_remain` → 账号池「余额」列 + 选号闸门
      * `credits_snapshot` / `credits_*`   → 账号池「到期」列（「未采集」就是它没写）
    """
    from datetime import datetime

    from admin import credits as credit_rules

    rows = [row for row in ka.accounts_for(db) if (row.status or "active") == "active"]
    if not rows:
        return {"ok": True, "checked": 0, "summary": "没有 active 的 KroWork 账号"}
    ok_count = 0
    failed: list[str] = []
    remain_total = 0.0
    for row in rows:
        try:
            tok = ka.ensure_token(db, row)
            info = client.fetch_credits(ka.region_of(row), tok=tok, force=True)
            if not info.get("ok"):
                failed.append("#%s %s" % (row.id, str(info.get("detail"))[:70]))
                continue
            remain = float(info.get("remain_credits") or 0)
            granted = float(info.get("total_credits") or 0)
            row.balance_remain = int(remain)
            row.balance_total = int(granted)
            row.balance_synced_at = datetime.utcnow()
            row.last_sync_at = datetime.utcnow()
            normalized = ka.credit_packages(info.get("items") or [],
                                            credit_rules.now_ms())
            summary = credit_rules.summarize_account(normalized)
            row.credits_expiring_soon = int(summary["expiring_soon_remaining"])
            row.credits_evergreen = int(sum(
                r["remaining"] for r in normalized
                if r["remaining"] > 0 and not r["expire_at"]))
            row.credits_expired = int(summary["expired_remaining"])
            row.credits_soonest_expire_at = credit_rules.as_datetime(
                summary["soonest_expire_at"])
            row.credits_snapshot = json.dumps(
                {**summary, "resources": normalized}, ensure_ascii=False)
            row.credits_synced_at = datetime.utcnow()
            db.commit()
            ok_count += 1
            remain_total += remain
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            failed.append("#%s %s" % (row.id, str(exc)[:70]))
    return {"ok": not failed, "checked": len(rows), "refreshed": ok_count,
            "remain_total": remain_total, "errors": failed[:5],
            "summary": "积分刷新 %d/%d · 合计 %s"
                       % (ok_count, len(rows), "{:,.0f}".format(remain_total))}


def _run_refresh_catalog(db: Session, schedule=None) -> dict:
    """刷新模型表（上游 `model_map_version` 变了才有意义）。"""
    model_map = client.fetch_model_map(force=True)
    return {"ok": bool(model_map.models),
            "version": model_map.version,
            "models": len(model_map.models),
            "slots": len(model_map.slots),
            "summary": "模型表 v%s：%d 个后端 / %d 个槽位"
                       % (model_map.version, len(model_map.models),
                          len(model_map.slots))}


def _run_probe(db: Session, schedule=None) -> dict:
    """上游连通性探针（免鉴权 `/health`），用于健康度曲线。"""
    result = client.probe("cn")
    return {"ok": bool(result.get("ok")), "detail": result,
            "summary": "上游 %s" % ("正常" if result.get("ok") else "不可达")}


# ---------------------------------------------------------------------------
# AccountOps — 11 项
# ---------------------------------------------------------------------------
class KroworkAccountOps:
    """账号池操作。凭证 = 一份 Keycloak id_token（+ refresh_token）。"""

    number_of_ops = 11

    # ---- 读 -------------------------------------------------------------
    def list(self, db: Session) -> list[dict]:
        return [ka.public_view(row) for row in ka.accounts_for(db)]

    def scan(self) -> dict:
        return ka.scan()

    # ---- 写 -------------------------------------------------------------
    def import_local(self, db: Session) -> dict:
        return ka.import_local(db)

    def import_record(self, db: Session, record: dict):
        return ka.import_record(db, record)

    # ---- 维护 -----------------------------------------------------------
    def refresh(self, db: Session, account_id: int, *, force: bool = False) -> dict:
        """刷新凭证 + **把积分写回 `balance_*` 列**。

        ## 为什么必须写回列（2026-11-20 修）

        控制台账号池读的是 `accounts.balance_total` / `balance_remain`，
        不写回就是「状态正常 · 0 / 0 · 未采集」——
        用户看到的是「账号导进来了但额度是 0」，而实际上游有 3000 积分。

        上游口径：`totalCredits` 是**当前可用**总额（各包之和），
        `sum(originCredits)` 才是发放总额（含已消耗）。所以：
          · `balance_remain` ← `totalCredits`（可用）
          · `balance_total`  ← `sum(originCredits)`（发放）
        """
        from datetime import datetime

        row = db.get(Account, int(account_id))
        if row is None or row.platform != ka.STORAGE:
            return {"ok": False, "error": "账号不存在"}

        # 1) 凭证：能续就续（30 分钟寿命，刷新任务顺带保活）
        token_error = ""
        tok = ""
        try:
            tok = ka.ensure_token(db, row, force=True)
        except Exception as exc:  # noqa: BLE001
            token_error = str(exc)
            tok = ka.token_of(ka.credentials_of(row))

        if not tok:
            return {"ok": False, "error": token_error or "账号没有可用凭证"}

        region = ka.region_of(row)

        # 2) 模型表验证（顺手校验凭证有效性）
        check = ka.verify_token(tok, region)

        # 3) 积分：写回余额列 + 逐包到期快照（控制台「未采集」的修复点）
        credits_ok = False
        remain = 0.0
        granted = 0.0
        info: dict = {}
        try:
            info = client.fetch_credits(region, tok=tok, force=True)
            if info.get("ok"):
                credits_ok = True
                remain = float(info.get("remain_credits") or 0)
                granted = float(info.get("total_credits") or 0)
                for item in (info.get("items") or []):
                    try:
                        granted = max(granted, float(item.get("origin") or 0))
                    except (TypeError, ValueError):
                        continue
                row.balance_total = int(granted)
                row.balance_remain = int(remain)
                row.balance_synced_at = datetime.utcnow()
                row.last_sync_at = datetime.utcnow()
                # 逐包快照：界面「到期」列读的是 credits_snapshot。
                # 不写它，账号池永远显示「未采集 · 点重新采集」。
                try:
                    from admin import credits as credit_rules

                    # 直接产出**已归一化**的包（不走 normalize_packages：
                    # 那套认的是 WorkBuddy 字段名，喂 KroWork 的字段会全变 0）
                    normalized = ka.credit_packages(info.get("items") or [],
                                                    credit_rules.now_ms())
                    summary = credit_rules.summarize_account(normalized)
                    row.credits_expiring_soon = int(
                        summary["expiring_soon_remaining"])
                    row.credits_evergreen = int(sum(
                        r["remaining"] for r in normalized
                        if r["remaining"] > 0 and not r["expire_at"]))
                    row.credits_expired = int(summary["expired_remaining"])
                    row.credits_soonest_expire_at = credit_rules.as_datetime(
                        summary["soonest_expire_at"])
                    row.credits_snapshot = json.dumps(
                        {**summary, "resources": normalized}, ensure_ascii=False)
                    row.credits_synced_at = datetime.utcnow()
                except Exception as exc:  # noqa: BLE001 - 快照失败不影响余额
                    logger.warning("KroWork 账号 %s 积分快照失败：%s", account_id, exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("KroWork 账号 %s 积分刷新失败：%s", account_id, exc)

        if check.get("ok"):
            row.status = "active"
            row.err_count = 0
            row.cool_until = None
            row.cool_kind = ""
            row.last_err_msg = ""
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

        result = {
            "ok": bool(check.get("ok")),
            "verify": check,
            "account": ka.public_view(row),
            "credits": {"ok": credits_ok, "remain": remain, "total": granted,
                        "items": info.get("items") or [],
                        "detail": info.get("detail") or ""},
            "message": ("余额已刷新：%s / %s"
                        % ("{:,.0f}".format(remain), "{:,.0f}".format(granted)))
            if credits_ok else ("凭证有效，但积分读不到：%s"
                                % (info.get("detail") or "")[:80]),
        }
        if token_error:
            result["token_error"] = token_error
        return result

    def keepalive(self, db: Session) -> dict:
        return _run_keepalive(db)

    def checkin(self, db: Session) -> dict:
        """每日签到（`/krow/commerce/checkin/claim`）。

        上游强制要求 `X-User-Timezone` 头，缺了回 `status:500 Missing request
        header`（HTTP 仍是 200，所以要看信封里的 status）。
        """
        rows = [r for r in ka.accounts_for(db) if (r.status or "active") == "active"]
        if not rows:
            return {"supported": True, "ok": True, "checked": 0,
                    "summary": "没有 active 的 KroWork 账号"}
        claimed = 0
        already = 0
        failed: list[str] = []
        for row in rows:
            region = ka.region_of(row)
            try:
                tok = ka.ensure_token(db, row)
            except Exception as exc:  # noqa: BLE001
                failed.append("#%s 取票失败：%s" % (row.id, str(exc)[:60]))
                continue
            status = client.fetch_checkin_status(region, tok=tok)
            if status.get("supported") is False:
                failed.append("#%s %s" % (row.id, status.get("detail") or "查不到签到状态"))
                continue
            # 上游真实字段（实测）：`canClaim` / `claimedToday` /
            # `credits`（本次可得）/ `consecutiveDays` / `lastClaimTime`
            claimed_today = status.get("claimedToday")
            can_claim = status.get("canClaim")
            if claimed_today is True or can_claim is False:
                already += 1
                continue
            try:
                client.claim_checkin(region, tok=tok)
                claimed += 1
            except Exception as exc:  # noqa: BLE001
                failed.append("#%s 签到失败：%s" % (row.id, str(exc)[:80]))
        return {
            "supported": True,
            "ok": not failed,
            "checked": len(rows),
            "claimed": claimed,
            "already": already,
            "errors": failed[:5],
            "summary": "签到 %d · 已签 %d · 失败 %d"
                       % (claimed, already, len(failed)),
        }

    def credits(self, db: Session) -> dict:
        """积分 / 订阅。

        真实路径（从 web dashboard 打包产物扒出来的，不是猜的）：

            GET https://api.krowork.com/krow/commerce/credit/balance
            Authorization: Bearer <同一个 Keycloak id_token>

        返回 `{"totalCredits":2961,"items":[{creditType:"免费试用",
        credits:2961, originCredits:3000, expireTime:"2026-12-19 …"}]}`。

        读不到时明确 `supported=False`，**不伪造数字**（skill 铁律 7）。
        """
        rows = ka.accounts_for(db)
        realm = ka.region_of(rows[0]) if rows else "cn"
        tok = ""
        if rows:
            try:
                tok = ka.ensure_token(db, rows[0])
            except Exception:  # noqa: BLE001 - 取票失败就让 fetch_credits 自己报
                tok = ""
        result = client.fetch_credits(realm, tok=tok, force=True)
        summary = ka.summarize(db)
        if not result.get("ok"):
            return {
                "supported": False,
                "summary": "读不到 KroWork 积分接口：%s" % (result.get("detail") or ""),
                "attempts": result.get("detail") or "",
                "accounts": summary,
                "remain_credits": 0.0,
                "total_credits": 0.0,
                "items": [],
            }
        return {
            "supported": True,
            "summary": "积分 %s（来源 credit/balance）"
                       % "{:,.0f}".format(float(result.get("remain_credits") or 0)),
            "path": result.get("path"),
            "remain_credits": float(result.get("remain_credits") or 0),
            "total_credits": float(result.get("total_credits") or 0),
            "items": result.get("items") or [],
            "plan": result.get("plan") or {},
            "accounts": summary,
        }

    def usage(self, db: Session, **filters) -> dict:
        days = int(filters.get("days") or 30)
        limit = int(filters.get("limit") or 50)
        return ka.usage_stats(db, days=days, limit=limit)

    def health(self, db: Session, account_id: int) -> dict:
        """分段检查：凭证 → 模型表 → 积分 → 上游探针 → 本地账本。"""
        stages: dict[str, dict] = {}
        row = db.get(Account, int(account_id))
        data = ka.credentials_of(row) if row is not None else {}
        tok = ka.token_of(data)
        exp = int(data.get("expires_at") or 0)
        region = ka.region_of(row) if row is not None else "cn"
        stages["token"] = {
            "ok": bool(tok) and (not exp or exp > time.time()),
            "detail": ("有效期至 %s"
                       % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(exp)))
            if exp else ("有票但无 exp" if tok else "没有凭证"),
        }
        if tok:
            check = ka.verify_token(tok, region)
            stages["models"] = {"ok": bool(check.get("ok")),
                                "detail": ("模型 %s 个 / 表版本 %s"
                                           % (check.get("models"),
                                              check.get("version")))
                                if check.get("ok")
                                else str(check.get("detail"))[:160]}
            # 积分（真实路径：/krow/commerce/credit/balance）
            try:
                info = client.fetch_credits(region, tok=tok, force=True)
                stages["credits"] = {
                    "ok": bool(info.get("ok")),
                    "detail": ("%s 积分（%s）"
                               % ("{:,.0f}".format(float(info.get("remain_credits") or 0)),
                                  info.get("path") or ""))
                    if info.get("ok") else str(info.get("detail") or "")[:160],
                }
            except Exception as exc:  # noqa: BLE001
                stages["credits"] = {"ok": False, "detail": str(exc)[:160]}
        else:
            stages["models"] = {"ok": False, "detail": "缺少凭证，无法拉模型表"}
            stages["credits"] = {"ok": False, "detail": "缺少凭证，无法读积分"}
        probe = client.probe(region)
        stages["upstream"] = {"ok": bool(probe.get("ok")),
                              "detail": "%s %s" % (probe.get("status"),
                                                   str(probe.get("body"))[:60])}
        stages["local"] = {"ok": True, "detail": "本地账本可用"}
        return {"ok": all(s.get("ok") for s in stages.values()), "stages": stages}

    def inject(self, db: Session, account_id: int) -> dict:
        """把这号的票落到 `id_token.txt`，让网关进程自己也能用（不写客户端）。"""
        row = db.get(Account, int(account_id))
        if row is None:
            return {"ok": False, "error": "账号不存在"}
        try:
            tok = ka.ensure_token(db, row)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        path = token_mod.data_dir() / "id_token.txt"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(tok, encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "error": "写入失败：%s" % exc}
        token_mod.STORE.invalidate()
        return {"ok": True, "path": str(path),
                "summary": "已写入网关凭证文件（30 分钟后需重新注入）"}

    def export(self, db: Session, ids=None) -> dict:
        """导出：默认脱敏；`mode="backup"` 才带 token（用于迁移）。"""
        rows = ka.accounts_for(db)
        if ids:
            wanted = {int(i) for i in ids}
            rows = [row for row in rows if int(row.id) in wanted]
        items = []
        for row in rows:
            data = ka.credentials_of(row)
            item = ka.public_view(row)
            if (getattr(self, "mode", "") or "") == "backup":
                item["credential"] = {k: data.get(k) for k in
                                      ("id_token", "refresh_token", "region", "sub")}
            items.append(item)
        return {"platform": ka.PLATFORM, "count": len(items),
                "mode": getattr(self, "mode", "") or "safe", "items": items}


# ---------------------------------------------------------------------------
# ClientProbe
# ---------------------------------------------------------------------------
class KroworkClientProbe:
    """KroWork 客户端探测：只暴露「区域」一个可改项，其余只读。"""

    def dirs(self) -> list[str]:
        out: list[str] = []
        for path in (token_mod.data_dir(), Path.home() / "Krow",
                     Path.home() / "Krow" / ".krow"):
            if path.is_dir():
                out.append(str(path))
        return out

    def probe(self) -> dict:
        folder = token_mod.data_dir()
        cfg = {}
        try:
            path = folder / "config.json"
            if path.is_file():
                cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cfg = {}
        running = False
        try:
            import subprocess

            res = subprocess.run(["tasklist", "/FI", "IMAGENAME eq krow-app.exe"],
                                 capture_output=True, text=True, timeout=8)
            running = "krow-app.exe" in (res.stdout or "")
        except Exception:  # noqa: BLE001
            running = False
        snap = token_mod.current()
        return {
            "installed": folder.is_dir(),
            "data_dir": str(folder),
            "projects_dir": str(cfg.get("projectsDir") or ""),
            "region": str(cfg.get("region") or "cn"),
            "running": running,
            "token": snap.public(),
            "sidecar_python": token_mod.STORE._sidecar_python or "",
            "local_hosts": [endpoints.LOCAL_HOST_APP, endpoints.LOCAL_HOST_SERVER],
        }

    def schema(self) -> list[dict]:
        return [{
            "key": "region",
            "label": "区域",
            "type": "select",
            "default": "cn",
            "options": [
                {"value": "cn", "label": "中国区（llm.krowork.com）"},
                {"value": "global", "label": "国际区（llm.kro.work）"},
            ],
            "help": "决定模型表来源与上游主机；改动后需要重新拉模型表。",
        }]

    def read(self, key: str):
        path = token_mod.data_dir() / "config.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except Exception:  # noqa: BLE001
            data = {}
        return data.get(key) or ("cn" if key == "region" else None)

    def write(self, key: str, value) -> dict:
        path = token_mod.data_dir() / "config.json"
        if not path.parent.is_dir():
            return {"ok": False, "error": "KroWork 数据目录不存在：%s" % path.parent}
        backup = ""
        try:
            if path.is_file():
                backup = str(path.with_suffix(".json.bak-%d" % int(time.time())))
                shutil.copy2(path, backup)
            data = {}
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
            data[key] = value
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "backup": backup}
        client.fetch_model_map(force=True)
        return {"ok": True, "backup": backup, "key": key, "value": value}


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------
class KroworkGateway:
    """Gateway 契约实现（转发方法委托 `gateway.py`）。"""

    platform_id = PUBLIC

    def __init__(self, realm: str = "") -> None:
        self.realm = realm

    # ---- 转发（热路径委托）----
    def chat_completions(self, request, body):
        from admin.platform.krowork import gateway

        return gateway.chat_completions(request)

    def responses(self, request, body):
        from admin.platform.krowork import gateway

        return gateway.responses(request)

    def messages(self, request, body):
        from admin.platform.krowork import gateway

        return gateway.messages(request)

    def count_tokens(self, request, body):
        from admin.platform.krowork import gateway

        return gateway.count_tokens(request)

    def list_models(self, request):
        from admin.platform.krowork import gateway

        return gateway.list_models(request)

    def balance(self, request):
        from admin.platform.krowork import gateway

        return gateway.balance(request)

    # ---- 管理契约 ----
    def account_ops(self) -> KroworkAccountOps:
        return KroworkAccountOps()

    def task_runners(self) -> dict:
        """**这里是生效的那一份**（调度器加载的是本类的实例）。

        `__init__._task_runners()` 只用于 `PlatformSpec.extra["task_runners"]`
        的声明展示，两处必须保持同名同义 —— 改一处必须改另一处。
        """
        return {
            "krowork_keepalive": _run_keepalive,
            "krowork_checkin": KroworkAccountOps().checkin,
            "krowork_refresh_quota": _run_quota,
            "krowork_refresh_catalog": _run_refresh_catalog,
            "krowork_probe": _run_probe,
        }

    def client_probe(self) -> KroworkClientProbe:
        return KroworkClientProbe()

    def overview_cards(self, db: Session) -> list[dict]:
        summary = ka.summarize(db)
        snap = token_mod.current()
        try:
            model_map = client.fetch_model_map()
            model_count = len(model_map.slots) or len(model_map.models)
            version = model_map.version
        except Exception:  # noqa: BLE001
            model_count = 0
            version = 0
        stats = ka.usage_stats(db, days=30, limit=0)
        token_tone = "brand" if snap.live else ("warn" if snap.ok else "danger")

        # 积分卡片：从号池里取第一个能用的号去读上游（只发 GET，不扣额度）
        credits_tone = ""
        credits_value = "—"
        credits_sub = "未采集"
        try:
            rows = ka.accounts_for(db)
            if rows:
                tok = ""
                try:
                    tok = ka.ensure_token(db, rows[0])
                except Exception:  # noqa: BLE001
                    tok = ""
                info = client.fetch_credits(ka.region_of(rows[0]), tok=tok)
                if info.get("ok"):
                    credits_value = "{:,.0f}".format(
                        float(info.get("remain_credits") or 0))
                    origin = float(info.get("total_credits") or 0)
                    expiring = ""
                    for item in (info.get("items") or []):
                        if item.get("expire_time"):
                            expiring = " · 到期 %s" % str(item["expire_time"])[:10]
                            break
                    credits_sub = "总额 %s%s" % ("{:,.0f}".format(origin), expiring)
                    credits_tone = "brand"
                else:
                    credits_sub = "读不到：%s" % str(info.get("detail") or "")[:28]
                    credits_tone = "warn"
            else:
                credits_sub = "号池为空"
        except Exception:  # noqa: BLE001
            credits_sub = "读取异常"
            credits_tone = "warn"

        return [
            {"key": "accounts", "label": "账号总数",
             "value": summary["total"],
             "sub": "启用 %d · 可用 %d" % (summary["active"], summary["live"]),
             "icon": "fa-users", "tone": ""},
            {"key": "credits", "label": "积分余额",
             "value": credits_value, "sub": credits_sub,
             "icon": "fa-wallet", "tone": credits_tone},
            {"key": "token", "label": "登录态",
             "value": "有效" if snap.live else ("过期" if snap.ok else "缺失"),
             "sub": ("剩余 %d 分钟 · %s"
                     % (max(0, int((snap.expires_at - time.time()) // 60)),
                        snap.source or "unknown"))
             if snap.live else (snap.detail or "请用「授权登录」入库")[:48],
             "icon": "fa-key", "tone": token_tone},
            {"key": "models", "label": "可用模型",
             "value": model_count,
             "sub": "模型表 v%s" % version if version else "未拉取",
             "icon": "fa-cube", "tone": ""},
            {"key": "tokens", "label": "近 30 天 tokens",
             "value": "{:,}".format(int(stats.get("total_tokens") or 0)),
             "sub": "请求 %d 次" % int(stats.get("total") or 0),
             "icon": "fa-chart-simple", "tone": ""},
        ]


gateway = KroworkGateway("")
