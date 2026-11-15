"""Loomy 邀请码绑定 + 兑换码换积分。

## 接口来源（客户端 `electron/points-service.js`）

    邀请码绑定 / 激活
      GET  /points/activation                    查当前绑定状态
      POST /points/activation                    {inviteCode, deviceId?} 绑定
      POST /points/first-login                   {inviteCode?, ...}   首次登录也能绑
    兑换码换积分
      POST /points/redemption-codes/redeem       {code}
      GET  /points/invitation-codes              查自己的邀请码

鉴权：只要 `token` 头（points 族一致）。

## 「默认邀请码」为什么要存在

用户的原话：「网关出一个绑定功能，填写好自己的邀请码，之后入库的账号
自动填写绑定激活使用」。

也就是：把自己的邀请码存成**池级默认值**，此后每个新入库的号（扫码/短信/
导入）都自动绑一次。省得每加一个号都要手填一遍。

存在 `system_settings` 里（键 `loomy:invite_code`），不写进账号 auth_json ——
它是**池级配置**，不是某个号的凭证。
"""
from __future__ import annotations

import json
import logging
import time

from sqlalchemy.orm import Session

from admin.models import Account, SystemSetting
from admin.platform.loomy.core import client
from admin.platform.loomy.services import accounts as loomy_accounts

logger = logging.getLogger("admin.platform.loomy.invite")

#: 默认邀请码的 system_settings 键
DEFAULT_CODE_KEY = "loomy:invite_code"

#: 批量时的号间隔（秒）：绑定/兑换都是写操作，连发太像脚本
STEP_GAP = 0.5

#: 单次批量上限（防止一次点下去打穿号池）
MAX_BATCH = 500


# ---------------------------------------------------------------------------
# 默认邀请码（池级配置）
# ---------------------------------------------------------------------------
def read_default_code(db: Session) -> str:
    try:
        row = db.query(SystemSetting).filter(SystemSetting.key == DEFAULT_CODE_KEY).first()
        return str(getattr(row, "value", "") or "").strip()
    except Exception:  # noqa: BLE001 - 读不到就当没配置
        return ""


def write_default_code(db: Session, code: str) -> str:
    """保存默认邀请码（空串 = 清除）。"""
    code = str(code or "").strip()
    row = db.query(SystemSetting).filter(SystemSetting.key == DEFAULT_CODE_KEY).first()
    if row is None:
        row = SystemSetting(key=DEFAULT_CODE_KEY, value=code)
        db.add(row)
    else:
        row.value = code
    db.commit()
    return code


#: 上游「邀请码不可用」的业务码。
#:
#: 实测（2026-11-09，用户给的两条事实）：
#:   · `200003 邀请码不存在` 在**这个号已经兑换过邀请码**时也返回 ——
#:     码本身是好的（同一批里 74 个号能绑），只是"这个号不能再绑"；
#:   · 邀请人侧官方上限 **20 个**，超了之后绑定请求仍然回成功，
#:     但 `activated` 不会变 true —— 这就是"看起来成功其实没生效"。
#: 所以判定不能只看 HTTP/业务码，必须回头读 `activated` 才知道真相。
INVITE_UNAVAILABLE_CODES = frozenset({"200003"})

#: 邀请人单码上限（官方规则），用于汇总里提示"还能邀请几个"
INVITER_LIMIT = 20


def _snapshot_of(account: Account) -> dict:
    try:
        data = json.loads(account.credits_snapshot or "{}")
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_invite_state(account: Account, *, activated, code: str,
                        note: str = "") -> None:
    """把邀请状态写进 `credits_snapshot['invite']`（**不加表列**）。

    为什么不加列：这是展示型快照，随上游状态变；塞进既有的
    `credits_snapshot` JSON 里既不用迁移、也不会让账号表越来越宽。
    """
    snap = _snapshot_of(account)
    snap["invite"] = {
        "activated": bool(activated),
        "code": str(code or ""),
        "note": str(note or "")[:120],
        "at": int(time.time()),
    }
    account.credits_snapshot = json.dumps(snap, ensure_ascii=False)[:20000]


def invite_state_of(account: Account) -> dict:
    """读本地缓存里的邀请状态（列表渲染用，**不发请求**）。"""
    state = _snapshot_of(account).get("invite")
    if not isinstance(state, dict):
        return {"activated": False, "code": "", "known": False}
    return {"activated": bool(state.get("activated")),
            "code": str(state.get("code") or ""),
            "note": str(state.get("note") or ""),
            "at": int(state.get("at") or 0),
            "known": True}


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------
def status_of(account: Account, *, persist: bool = True) -> dict:
    """单个账号的邀请码绑定状态（读上游，并回写快照）。"""
    data = loomy_accounts.credentials_of(account)
    token = loomy_accounts.token_of(data)
    realm = loomy_accounts.realm_of(account)
    if not token:
        return {"account_id": account.id, "ok": False, "reason": "账号缺少 session token"}
    try:
        got = client.fetch_activation(token, realm, account.id)
    except client.UpstreamError as exc:
        return {"account_id": account.id, "ok": False,
                "kind": exc.kind, "reason": str(exc.message)[:160]}
    raw = got.get("raw") or {}
    activated = bool(raw.get("activated"))
    code = str(raw.get("appliedInvitationCode") or "")
    if persist:
        _write_invite_state(account, activated=activated, code=code)
    return {"account_id": account.id, "account": account.name or str(account.id),
            "ok": True, "activated": activated, "code": code, "raw": raw}


def sync_all(db: Session, realm: str = "", *, limit: int | None = None) -> dict:
    """把整个号池的邀请状态同步一遍（写进快照，之后列表直接读缓存）。"""
    accounts = [a for a in loomy_accounts.accounts_for(db, realm)
                if (getattr(a, "status", "") or "") == "active"]
    if limit:
        accounts = accounts[:min(int(limit), MAX_BATCH)]
    started = time.perf_counter()
    activated = pending = failed = 0
    used_codes: dict[str, int] = {}
    items: list[dict] = []
    for index, account in enumerate(accounts):
        result = status_of(account)
        if result.get("ok"):
            if result.get("activated"):
                activated += 1
                code = str(result.get("code") or "")
                if code:
                    used_codes[code] = used_codes.get(code, 0) + 1
            else:
                pending += 1
        else:
            failed += 1
        items.append(result)
        if index < len(accounts) - 1:
            time.sleep(STEP_GAP * 0.6)  # 读操作可以快一点
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    elapsed = round(time.perf_counter() - started, 1)
    summary = ("邀请状态同步：已激活 %d 个，未激活 %d 个，读取失败 %d 个（用时 %ss）"
               % (activated, pending, failed, elapsed))
    logger.info("%s", summary)
    return {"ok": failed == 0, "accounts": len(accounts),
            "activated": activated, "pending": pending, "failed": failed,
            "codes": used_codes, "elapsed_s": elapsed,
            "summary": summary, "items": items[:80]}


# ---------------------------------------------------------------------------
# 绑定
# ---------------------------------------------------------------------------
def bind_one(db: Session, account: Account, code: str) -> dict:
    """给一个账号绑邀请码。

    ## 判定必须回读 `activated`，不能只看返回码

    实测（用户给的事实 + 复现）：
      · 号**已经兑换过**邀请码 → 上游回 `200003 邀请码不存在`（码本身没问题）；
      · 邀请人**已达 20 人上限** → 绑定请求回成功，但 `activated` 不会变 true。
    所以这里绑完立刻回读一次状态，只有 `activated=true` 且
    `appliedInvitationCode` 对得上才判真成功，否则把上游码与真实状态一起说清楚。
    """
    code = str(code or "").strip()
    if not code:
        return {"account_id": account.id, "ok": False, "reason": "邀请码为空"}
    data = loomy_accounts.credentials_of(account)
    token = loomy_accounts.token_of(data)
    realm = loomy_accounts.realm_of(account)
    device_id = str(data.get("devid") or "")
    if not token:
        return {"account_id": account.id, "ok": False, "reason": "账号缺少 session token"}
    try:
        result = client.bind_invite_code(token, realm, code,
                                         device_id=device_id, account_id=account.id)
    except client.UpstreamError as exc:
        upstream_code = str(exc.code or "")
        # 200003 的真实含义：这个号已兑换过 / 邀请人已满 20，不是"码不存在"
        reason = str(exc.message)[:200]
        if upstream_code in INVITE_UNAVAILABLE_CODES:
            state = status_of(account, persist=True)
            already = bool(state.get("activated"))
            reason = ("该账号已经兑换过邀请码（%s）" % (state.get("code") or "未知码")
                      if already else
                      "上游拒绝绑定：账号已兑换过，或邀请人已达 %d 人上限"
                      % INVITER_LIMIT)
            try:
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
            return {"account_id": account.id,
                    "account": account.name or str(account.id),
                    "ok": already, "already": already, "code": code,
                    "applied_code": state.get("code") or "",
                    "kind": exc.kind, "upstream_code": upstream_code,
                    "reason": reason}
        return {"account_id": account.id,
                "account": account.name or str(account.id),
                "ok": False, "code": code,
                "kind": exc.kind, "upstream_code": upstream_code,
                "reason": reason}

    # 回读，确认真的生效（超限时上游会"假成功"）
    state = status_of(account, persist=True)
    applied = str(state.get("code") or "")
    activated = bool(state.get("activated"))
    ok = activated and (applied == code or not applied)
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return {
        "account_id": account.id, "account": account.name or str(account.id),
        "ok": ok, "already": activated and applied not in ("", code),
        "code": code, "applied_code": applied,
        "reason": ("" if ok else
                   ("已绑过其它码（%s），未改动" % applied if activated and applied != code
                    else "上游受理但未生效：邀请人可能已达 %d 人上限" % INVITER_LIMIT)),
        "raw": result.get("raw") or {},
    }


def bind_all(db: Session, code: str, realm: str = "", *,
             limit: int | None = None, skip_activated: bool = True) -> dict:
    """把邀请码绑到该平台所有启用账号（串行 + 间隔）。

    `skip_activated=True`（默认）会**跳过本地快照里已激活的号**：
    一个号只能兑换一次邀请码，对已激活的号再发一次请求纯属浪费
    —— 而且会拿回 `200003`，把"已兑换过"误报成失败。
    """
    code = str(code or "").strip() or read_default_code(db)
    if not code:
        return {"ok": False, "reason": "没有邀请码，也没有配置默认邀请码"}
    all_accounts = [a for a in loomy_accounts.accounts_for(db, realm)
                    if (getattr(a, "status", "") or "") == "active"]
    accounts = all_accounts
    skipped = 0
    if skip_activated:
        accounts = []
        for account in all_accounts:
            state = invite_state_of(account)
            if state.get("known") and state.get("activated"):
                skipped += 1
                continue
            accounts.append(account)
    if limit:
        accounts = accounts[:min(int(limit), MAX_BATCH)]
    started = time.perf_counter()
    items: list[dict] = []
    bound = already = failed = 0
    for index, account in enumerate(accounts):
        item = bind_one(db, account, code)
        items.append(item)
        if item.get("ok"):
            bound += 1
        elif item.get("already"):
            already += 1
        else:
            failed += 1
        if index < len(accounts) - 1:
            time.sleep(STEP_GAP)
    elapsed = round(time.perf_counter() - started, 1)
    summary = ("邀请码 %s：真生效 %d 个，已绑过 %d 个，失败 %d 个，"
               "跳过已激活 %d 个（邀请人上限 %d，用时 %ss）"
               % (code, bound, already, failed, skipped, INVITER_LIMIT, elapsed))
    logger.info("%s", summary)
    return {"ok": failed == 0, "code": code, "accounts": len(accounts),
            "skipped_activated": skipped,
            "bound": bound, "already": already, "failed": failed,
            "inviter_limit": INVITER_LIMIT,
            "elapsed_s": elapsed, "summary": summary, "items": items[:80]}


# ---------------------------------------------------------------------------
# 兑换
# ---------------------------------------------------------------------------
def redeem_one(db: Session, account: Account, code: str) -> dict:
    """给一个账号兑换码换积分。"""
    code = str(code or "").strip()
    if not code:
        return {"account_id": account.id, "ok": False, "reason": "兑换码为空"}
    data = loomy_accounts.credentials_of(account)
    token = loomy_accounts.token_of(data)
    realm = loomy_accounts.realm_of(account)
    if not token:
        return {"account_id": account.id, "ok": False, "reason": "账号缺少 session token"}
    try:
        result = client.redeem_code(token, realm, code, account_id=account.id)
    except client.UpstreamError as exc:
        return {"account_id": account.id,
                "account": account.name or str(account.id),
                "ok": False, "code": code,
                "kind": exc.kind, "upstream_code": str(exc.code or ""),
                "reason": str(exc.message)[:200]}
    raw = result.get("raw") or {}
    # 兑换成功后顺手刷一次余额，用户能立刻看到到账
    quota = {}
    try:
        quota = loomy_accounts.sync_quota(db, account, with_models=False)
    except Exception:  # noqa: BLE001 - 刷余额失败不影响兑换结论
        logger.debug("兑换后刷余额失败 acc=%s", account.id, exc_info=True)
    return {"account_id": account.id, "account": account.name or str(account.id),
            "ok": True, "code": code, "raw": raw,
            "balance": int(getattr(account, "balance_remain", 0) or 0),
            "quota_ok": bool(quota.get("ok"))}


def redeem_all(db: Session, code: str, realm: str = "", *,
               limit: int | None = None) -> dict:
    """给所有启用账号兑换同一个码（串行 + 间隔）。"""
    code = str(code or "").strip()
    if not code:
        return {"ok": False, "reason": "没有兑换码"}
    accounts = [a for a in loomy_accounts.accounts_for(db, realm)
                if (getattr(a, "status", "") or "") == "active"]
    if limit:
        accounts = accounts[:min(int(limit), MAX_BATCH)]
    started = time.perf_counter()
    items: list[dict] = []
    ok_count = failed = 0
    for index, account in enumerate(accounts):
        item = redeem_one(db, account, code)
        items.append(item)
        if item.get("ok"):
            ok_count += 1
        else:
            failed += 1
        if index < len(accounts) - 1:
            time.sleep(STEP_GAP)
    elapsed = round(time.perf_counter() - started, 1)
    summary = ("兑换码 %s：成功 %d 个，失败 %d 个（用时 %ss）"
               % (code, ok_count, failed, elapsed))
    logger.info("%s", summary)
    return {"ok": failed == 0, "code": code, "accounts": len(accounts),
            "redeemed": ok_count, "failed": failed,
            "elapsed_s": elapsed, "summary": summary, "items": items[:80]}


# ---------------------------------------------------------------------------
# 账号池互邀（破 20 上限）
# ---------------------------------------------------------------------------
#: 互邀的**硬终止条件**。任何一条命中就停，绝不靠"看起来没了"来判断。
#: 这些值同时出现在接口参数里，调用方可调小，不能调大超过硬顶。
MUTUAL_MAX_REDEEMS = 200      # 一次最多产生多少次"兑换"写操作
MUTUAL_MAX_SECONDS = 900      # 墙钟预算（秒）：超了立刻停
MUTUAL_MAX_INVITERS = 40      # 最多用多少个号当邀请人


def invitation_codes_of(account: Account) -> dict:
    """读某个号自己的邀请码（`GET /invitation-codes`）。"""
    data = loomy_accounts.credentials_of(account)
    token = loomy_accounts.token_of(data)
    realm = loomy_accounts.realm_of(account)
    if not token:
        return {"ok": False, "reason": "账号缺少 session token", "codes": []}
    try:
        got = client.fetch_invitation_codes(token, realm, account.id)
    except client.UpstreamError as exc:
        return {"ok": False, "kind": exc.kind,
                "reason": str(exc.message)[:160], "codes": []}
    raw = got.get("raw") or {}
    rows = raw.get("list") if isinstance(raw.get("list"), list) else []
    codes = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("inviteCode") or "").strip()
        if not code:
            continue
        try:
            used = int(row.get("usedCount") or 0)
            max_uses = int(row.get("maxUses") or 0)
        except (TypeError, ValueError):
            used, max_uses = 0, 0
        codes.append({
            "code": code,
            "used": used,
            "max_uses": max_uses,
            "left": max(0, max_uses - used),
            "status": str(row.get("status") or ""),
        })
    # 剩余次数多的排前面（20 次的多用码优先，避免几十个 1 次码把节奏打碎）
    codes.sort(key=lambda c: (-c["left"], -c["max_uses"]))
    return {"ok": True, "codes": [c for c in codes if c["left"] > 0]}


def mutual_invite(db: Session, realm: str = "", *,
                  max_redeems: int = 60, max_seconds: int = 300,
                  max_inviters: int = 20, dry_run: bool = False) -> dict:
    """**账号池互邀**：用池里每个号自己的邀请码，去给还没绑定过的号兑换积分。

    ## 为什么要有这个

    官方规则：**一个邀请人最多被 20 个号兑换**（`maxUses=20`，满了
    `status=exhausted`）。所以单个邀请码最多只能带来 20 次奖励 ——
    想给整池刷，就得**轮换邀请人**：A 的码用完 20 次，换 B 的码，再换 C。

    这就是用户说的「账号池互邀，为了破 20 限制」。

    ## 终止条件（六条，缺一不可）

    1. **没有可兑换的号**了（所有启用号都已激活）
    2. **没有可用邀请码**了（每个号的码都 exhausted）
    3. 写操作达到 `max_redeems`（默认 60，硬顶 200）
    4. 墙钟超过 `max_seconds`（默认 300s，硬顶 900s）
    5. 邀请人数量达到 `max_inviters`（默认 20，硬顶 40）
    6. **`(邀请人, 被邀请号)` 组合去重** —— 同一个组合绝不尝试第二次

    第 6 条是关键：没有它，一旦某个号"兑换失败但状态没变"，循环会永远
    拿同一个组合重试。这里既有内存去重集，也要求每个被邀请号**成功一次
    就立刻从候选里移除**。

    ## 安全边界

    · **不给自己兑换自己的码**（上游会拒，且徒增风控特征）；
    · 只对 `status=active` 的号操作；
    · `dry_run=True` 只出计划、不发请求（先看会跑多少步）。
    """
    started = time.perf_counter()
    redeems_cap = max(1, min(int(max_redeems or 60), MUTUAL_MAX_REDEEMS))
    seconds_cap = max(10, min(int(max_seconds or 300), MUTUAL_MAX_SECONDS))
    inviters_cap = max(1, min(int(max_inviters or 20), MUTUAL_MAX_INVITERS))

    accounts = [a for a in loomy_accounts.accounts_for(db, realm)
                if (getattr(a, "status", "") or "") == "active"]

    # 候选被邀请号：快照里 known 且 activated 的直接排除（省一次请求）
    invitees: list[Account] = []
    for account in accounts:
        state = invite_state_of(account)
        if state.get("known") and state.get("activated"):
            continue
        invitees.append(account)

    seen_pairs: set[tuple[int, str]] = set()
    steps: list[dict] = []
    redeemed = failed = inviters_used = 0
    stop_reason = ""

    for inviter in accounts[:inviters_cap]:
        if redeemed >= redeems_cap:
            stop_reason = "达到本次兑换上限（%d 次）" % redeems_cap
            break
        if time.perf_counter() - started > seconds_cap:
            stop_reason = "达到时间预算（%ds）" % seconds_cap
            break
        if not invitees:
            stop_reason = "号池里已经没有「未激活」的号可兑换"
            break

        got = invitation_codes_of(inviter)
        if not got.get("ok"):
            steps.append({"inviter": inviter.id, "ok": False,
                          "reason": "读邀请码失败：%s" % (got.get("reason") or "")})
            continue
        codes = got.get("codes") or []
        if not codes:
            continue
        inviters_used += 1

        for entry in codes:
            left = int(entry.get("left") or 0)
            code = entry.get("code") or ""
            while left > 0 and invitees and redeemed < redeems_cap:
                if time.perf_counter() - started > seconds_cap:
                    stop_reason = "达到时间预算（%ds）" % seconds_cap
                    break
                invitee = invitees[0]
                # 不能自己邀请自己
                if invitee.id == inviter.id:
                    invitees.pop(0)
                    continue
                pair = (invitee.id, code)
                if pair in seen_pairs:          # 终止条件 6：组合去重
                    invitees.pop(0)
                    continue
                seen_pairs.add(pair)

                if dry_run:
                    steps.append({"inviter": inviter.id, "invitee": invitee.id,
                                  "code": code, "dry_run": True})
                    invitees.pop(0)
                    left -= 1
                    continue

                result = bind_one(db, invitee, code)
                steps.append({"inviter": inviter.id, "invitee": invitee.id,
                              "code": code, "ok": bool(result.get("ok")),
                              "applied": result.get("applied_code") or "",
                              "reason": (result.get("reason") or "")[:140]})
                # 无论成败都从候选里移除：失败重试同一个组合就是死循环
                invitees.pop(0)
                if result.get("ok"):
                    redeemed += 1
                    left -= 1
                else:
                    failed += 1
                time.sleep(STEP_GAP)
            if stop_reason:
                break
        if stop_reason:
            break

    if not stop_reason:
        stop_reason = ("邀请码都已用满" if not invitees else
                       "已处理完本批邀请人（%d 个）" % inviters_used)

    elapsed = round(time.perf_counter() - started, 1)
    summary = ("账号池互邀%s：兑换成功 %d 次，失败 %d 次，用了 %d 个邀请人，"
               "剩余未激活 %d 个（用时 %ss · 停止原因：%s）"
               % ("（演练）" if dry_run else "", redeemed, failed,
                  inviters_used, len(invitees), elapsed, stop_reason))
    logger.info("%s", summary)
    return {"ok": failed == 0, "dry_run": dry_run, "summary": summary,
            "redeemed": redeemed, "failed": failed,
            "inviters": inviters_used, "invitees_left": len(invitees),
            "elapsed_s": elapsed, "stop_reason": stop_reason,
            "caps": {"max_redeems": redeems_cap, "max_seconds": seconds_cap,
                     "max_inviters": inviters_cap},
            "steps": steps[:100]}


# ---------------------------------------------------------------------------
# 入库自动绑定
# ---------------------------------------------------------------------------
def auto_bind_on_import(db: Session, account: Account | None) -> None:
    """新号入库后自动绑默认邀请码（**失败不抛**）。

    用户要的就是「填写好自己的邀请码，之后入库的账号自动填写绑定激活」。
    只在配了默认邀请码时才动；失败只记 debug —— 不能因为绑邀请码失败
    就把「账号已入库」这件事回滚掉。
    """
    if account is None:
        return
    try:
        code = read_default_code(db)
        if not code:
            return
        result = bind_one(db, account, code)
        if result.get("ok"):
            logger.info("入库自动绑定邀请码成功 acc=%s code=%s", account.id, code)
        else:
            logger.debug("入库自动绑定邀请码失败 acc=%s：%s",
                         account.id, result.get("reason"))
    except Exception:  # noqa: BLE001
        logger.debug("入库自动绑定邀请码异常 acc=%s", getattr(account, "id", "?"),
                     exc_info=True)