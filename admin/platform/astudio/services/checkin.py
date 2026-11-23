"""AStudio 每日签到（每日登录积分）。

## 判定链（写请求越少越好）

    1. 只读**当日 CREDIT 流水**（points/details，北京时间当天窗口，
       与网页后台「获得明细」同源）：出现 `operation=GRANT` 且
       `reason=「每日登录赠送」` 的行 = 已发放 —— **零写**，连弹窗都不读；
    2. 流水为空才去 client-popups/pending 精确筛选：
       `componentType === "DAILY_REWARD_DIALOG"`、`popupId` 为正整数、
       `instanceKey` 非空 —— 只对这一条 POST complete（桌面端「知道了」
       同款），再以流水认定结果；
    3. 已 complete 但流水还没出现 → 短延迟复读一次，仍没有记 failed。
       **不拿余额差、不拿弹窗文案当证据**；没有待领取弹窗记 inactive。
       下载奖励 / 新人礼包等其它弹窗一律不碰。

只有模型凭据（无网页会话）的账号直接记 ineligible。
"""
from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.astudio.core import client, endpoints
from admin.platform.astudio.services import accounts

logger = logging.getLogger("admin.platform.astudio.services.checkin")

#: complete 后等入账的复读间隔（只复读一次，不重复 complete）。
LEDGER_RETRY_S = 1.5


def ledger_grant(session: dict, account_id: int) -> dict | None:
    """当日获得流水里的「每日登录赠送」GRANT 行（没有返回 None）。"""
    start, end = endpoints.cst_day_window()
    items = client.fetch_ledger(session, start, end, account_id=account_id)
    for item in items:
        if not isinstance(item, dict):
            continue
        if (item.get("direction") == "CREDIT"
                and item.get("operation") == endpoints.GRANT_OPERATION
                and item.get("reason") == endpoints.GRANT_REASON):
            return item
    return None


def pick_daily_popup(popups: list) -> dict | None:
    """只认桌面端 complete 契约能接受的每日奖励弹窗。

    其它弹窗（下载奖励、新人礼包、引导）一律不碰。
    """
    for item in popups or []:
        if not isinstance(item, dict):
            continue
        popup_id = item.get("popupId")
        instance_key = item.get("instanceKey")
        if (item.get("componentType") == endpoints.DAILY_POPUP_COMPONENT
                and isinstance(popup_id, int) and popup_id > 0
                and isinstance(instance_key, str) and instance_key.strip()):
            return item
    return None


def _credit_of(grant: dict) -> int:
    try:
        amount = int(float(grant.get("amount") or 0))
    except (TypeError, ValueError):
        amount = 0
    return max(amount, 0)


def checkin_one(db: Session, account: Account) -> dict:
    """单账号签到。返回 {ok, outcome, message, credit?, error?}。"""
    data = accounts.credentials_of(account)
    session = accounts.session_of(data)
    account_id = int(account.id or 0)
    if session is None:
        return {"ok": True, "outcome": "ineligible",
                "message": "该账号只有模型凭据（无网页会话），积分/签到不可用"}

    def read_grant():
        try:
            return ledger_grant(session, account_id), None
        except client.UpstreamError as exc:
            return None, exc

    # 1) 流水优先：当日已有「每日登录赠送」GRANT 行 = 已发放。只读，零写。
    grant, err = read_grant()
    if err is not None:
        _note_if_auth(db, account, err)
        return {"ok": False, "outcome": "failed", "error": err.message,
                "message": "当日获得流水读取失败：%s" % err.message}
    if grant is not None:
        return {"ok": True, "outcome": "done",
                "message": "今日已发放：每日登录赠送 +%s（流水实证）"
                          % _credit_of(grant),
                "credit": _credit_of(grant)}

    # 2) 未发放：取 pending 里的每日奖励弹窗，只 complete 这一条。
    try:
        popups = client.fetch_popups(session, account_id=account_id)
    except client.UpstreamError as exc:
        _note_if_auth(db, account, exc)
        return {"ok": False, "outcome": "failed", "error": exc.message,
                "message": "每日奖励弹窗读取失败：%s" % exc.message}
    daily = pick_daily_popup(popups)
    if daily is None:
        return {"ok": True, "outcome": "inactive",
                "message": "当日没有待领取的每日登录积分"}
    try:
        client.complete_popup(session, daily, account_id=account_id)
    except client.UpstreamError as exc:
        _note_if_auth(db, account, exc)
        return {"ok": False, "outcome": "failed", "error": exc.message,
                "message": "每日登录积分领取失败：%s" % exc.message}

    # 3) 以流水认定：短延迟复读一次，不重复 complete。
    grant, err = read_grant()
    if err is None and grant is None:
        time.sleep(LEDGER_RETRY_S)
        grant, err = read_grant()
    if err is not None:
        _note_if_auth(db, account, err)
        return {"ok": False, "outcome": "failed", "error": err.message,
                "message": "已提交领取，但当日获得流水读取失败：%s" % err.message}
    if grant is not None:
        logger.info("AStudio 账号 %s 签到成功 +%s 积分", account_id,
                    _credit_of(grant))
        return {"ok": True, "outcome": "claimed",
                "message": "每日登录积分 +%s（本次领取，流水实证）"
                          % _credit_of(grant),
                "credit": _credit_of(grant)}
    return {"ok": False, "outcome": "failed",
            "message": "已提交领取，但当日获得流水未出现（不猜结果，请稍后刷新确认）"}


def _note_if_auth(db: Session, account: Account, exc: client.UpstreamError) -> None:
    """会话失效的错误记到账号上（冷却 + 提示重登），其余不误伤。"""
    if exc.kind != "auth":
        return
    try:
        from admin.platform import shared as _shared
        _shared.note_failure(
            db, account, kind="auth", detail=exc.message,
            status=exc.status, model="", public_platform=endpoints.PUBLIC,
            cool_seconds=600)
        db.commit()
    except Exception:  # noqa: BLE001 - 状态写回失败不影响签到结果
        pass


def run_checkin(db: Session) -> dict:
    """批量签到（全部 active 账号；定时任务与「签到」按钮共用）。"""
    from admin.platform.shared import run_account_batch

    rows = accounts.accounts_for(db)
    ids = [int(row.id or 0) for row in rows if row.status == "active"]
    if not ids:
        return {"ok": True, "checked": 0, "claimed": 0,
                "summary": "没有 active 的 AStudio 账号"}

    def worker(session: Session, account: Account) -> dict:
        result = checkin_one(session, account)
        return {"account_id": int(account.id or 0),
                "ok": bool(result.get("ok")),
                "outcome": result.get("outcome"),
                "message": result.get("message") or "",
                "credit": result.get("credit")}

    results = run_account_batch(ids, worker, label="astudio-checkin")
    outcomes = {}
    for row in results:
        outcome = str(row.get("outcome") or "failed")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    failed = sum(1 for row in results if not row.get("ok"))
    claimed = outcomes.get("claimed", 0)
    done = outcomes.get("done", 0)
    inactive = outcomes.get("inactive", 0) + outcomes.get("ineligible", 0)
    summary = "签到 %d · 已发 %d · 跳过 %d%s" % (
        claimed, done, inactive,
        " · 失败 %d" % failed if failed else "")
    return {"ok": failed == 0, "checked": len(ids), "claimed": claimed,
            "already": done, "skipped": inactive, "failed": failed,
            "summary": summary, "items": results}
