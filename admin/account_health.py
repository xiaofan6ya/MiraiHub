"""Authoritative, non-destructive account-pool health checks."""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta

import httpx

from admin import backend, maintenance
from admin.config import settings
from admin.models import Account, ModelConfig
from admin.pool import POOL

_logger = logging.getLogger("admin.account_health")

#: `note_auth_failure` 写进 `last_err_msg` 的前缀。`ban_recheck._recheck_platform_auth`
#: 只复核带这个前缀的停用号，人工停用 / 其它原因停用的号不会被自动恢复。
AUTH_FAIL_PREFIX = "登录态被上游拒绝"


def _env_number(name: str, default: float, low: float, high: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        value = default
    return max(low, min(high, value))


def note_auth_failure(db, account, message: str = "", *, now: datetime | None = None) -> str:
    """非 WorkBuddy 平台遇到 auth / 401 时的统一处置（修复批次 3 · D7，审查 L-3）。

    原来 Loomy / Qoder 的额度同步只要遇到**一次** `auth` 错误就把账号永久禁用，
    没有连续计数、没有二次确认、也没有任何自动复核路径 —— 上游一次 401 抖动
    就能把整个平台的号池打空。这里改成：

      * 连续失败 < 阈值：只冷却（`cool_until`），账号仍是 active，冷却到期自然回池；
      * 连续失败 ≥ 阈值：停用，`cool_kind=session_dead`，错误文案带 `AUTH_FAIL_PREFIX`，
        由 `ban_recheck` 定时用该平台自己的健康检查复核，通过即自动恢复；
      * 人工停用（`manual_disabled`）的号不动。

    阈值 / 冷却时长：`ADMIN_AUTH_FAIL_THRESHOLD`（默认 3）、
    `ADMIN_AUTH_FAIL_COOLDOWN_SECONDS`（默认 1800）。
    返回 `"cooldown"` / `"disabled"` / `"manual"`。**不提交**，由调用方 commit。
    成功调用上游后请调 `clear_auth_failure(account)` 清计数。
    """
    if (getattr(account, "cool_kind", "") or "") == "manual_disabled":
        return "manual"
    moment = now or datetime.utcnow()
    threshold = int(_env_number("ADMIN_AUTH_FAIL_THRESHOLD", 3, 1, 50))
    cooldown = _env_number("ADMIN_AUTH_FAIL_COOLDOWN_SECONDS", 1800, 60, 86400)
    fails = int(getattr(account, "session_dead_fails", 0) or 0) + 1
    account.session_dead_fails = fails
    account.last_err_at = moment
    account.last_err_msg = ("%s（%d/%d）：%s" % (AUTH_FAIL_PREFIX, fails, threshold,
                                               str(message or "")[:150]))[:255]
    account.cool_kind = "session_dead"
    if fails < threshold:
        account.cool_until = moment + timedelta(seconds=cooldown)
        return "cooldown"
    account.status = "disabled"
    account.cool_until = None
    return "disabled"


def clear_auth_failure(account) -> None:
    """上游调用成功后清掉 `note_auth_failure` 的连续计数与冷却（不动人工停用）。"""
    if (getattr(account, "cool_kind", "") or "") == "manual_disabled":
        return
    account.session_dead_fails = 0
    if (getattr(account, "cool_kind", "") or "") == "session_dead" and account.status == "active":
        account.cool_kind = ""
        account.cool_until = None


def _error_result(exc: Exception) -> dict:
    from admin.routers.proxy import _extract_http_status

    status = getattr(exc, "http_status", 0) or _extract_http_status(str(exc))
    from admin.upstream_errors import error_info
    info = error_info(getattr(exc, "raw_body", "") or str(exc), status)
    return {"ok": False, "status": status or info.get("http_status") or 0,
            "code": info.get("code"),
            "message": str(info.get("message") or "上游请求失败")[:180]}


def _check_call(fn, *, validator=None) -> dict:
    started = time.perf_counter()
    try:
        value = fn()
        if validator and not validator(value):
            raise ValueError("上游返回为空或结构不完整")
        return {"ok": True, "status": 200,
                "elapsed_ms": round((time.perf_counter() - started) * 1000)}
    except Exception as exc:
        result = _error_result(exc)
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return result


def _model_id(row) -> str:
    return str(row.get("id") or "").strip() if isinstance(row, dict) else str(row or "").strip()


def _select_chat_model(db, rows) -> tuple[str, bool]:
    """Prefer a current free text model; fall back to an enabled text model."""
    from model_metadata import is_media_model

    model_rows = [row for row in (rows or [])
                  if _model_id(row) and _model_id(row).lower() != "auto"
                  and not is_media_model(_model_id(row))]
    if not model_rows:
        return "", False

    # 只看 WorkBuddy 自己的模型配置（D3）：这条路径只服务默认平台（其它平台走
    # `_platform_health`），原来跨平台取免费 / 启用清单，别家启用的同名模型会被当成可用。
    from admin.platform.workbuddy import scoping as wb_scope

    free_ids = {row.model_id for row in db.query(ModelConfig).filter(
        ModelConfig.enabled == 1,
        (ModelConfig.credit_multiplier == 0) | (ModelConfig.credit_multiplier.is_(None)),
        wb_scope.workbuddy_cond(ModelConfig.platform),
    ).all()}
    for row in model_rows:
        model = _model_id(row)
        credits = str(row.get("credits") or "").lower() if isinstance(row, dict) else ""
        if model in free_ids or "x0.00" in credits or "free" in credits:
            return model, True

    enabled_ids = wb_scope.enabled_models(db, "workbuddy")
    for row in model_rows:
        model = _model_id(row)
        if model in enabled_ids:
            return model, False
    return _model_id(model_rows[0]), False


def _conversation_probe(sess, model: str) -> dict:
    """Send one minimal real upstream chat and require a normal completed stream."""
    from admin.routers.proxy import (
        _looks_like_inband_error, _sse_has_content, _sse_has_error_event,
        _sse_is_truncated,
    )

    body = {"model": model, "stream": True, "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}]}
    # 健康检查也是一次真实计费调用：带上归属头与独立 trace，否则上游用量表里
    # 会多出一行「使用端为空 + crb- 自造 id」记录，排查时与真实流量混在一起。
    from admin import upstream_identity as identity
    probe_extra = identity.identity_headers(identity.purpose_for("chat"))
    probe_extra.update(identity.trace_headers())
    started = time.perf_counter()
    chunks: list[str] = []
    try:
        with sess.http_client(timeout=45) as client:
            with client.stream(
                    "POST", f"{settings.BACKEND}/v2/chat/completions",
                    headers=sess.get_headers(extra=probe_extra), json=body) as response:
                if response.status_code >= 400:
                    detail = response.read()
                    text = detail.decode("utf-8", "replace") if isinstance(detail, bytes) else str(detail)
                    from admin.upstream_errors import error_info
                    info = error_info(text[:65536], response.status_code)
                    return {"ok": False, "status": response.status_code,
                            "code": info.get("code"),
                            "message": str(info.get("message") or "上游对话请求失败")[:180],
                            "model": model,
                            "elapsed_ms": round((time.perf_counter() - started) * 1000)}
                for chunk in response.iter_text():
                    chunks.append(chunk)
                    if sum(map(len, chunks)) > 1_000_000:
                        raise ValueError("上游对话返回超过健康检查读取上限")
        text = "".join(chunks)
        if _looks_like_inband_error(text) or _sse_has_error_event(text):
            info = _error_result(RuntimeError(text[:65536]))
            return {**info, "ok": False, "model": model,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000)}
        if not _sse_has_content(text) or _sse_is_truncated(text):
            return {"ok": False, "status": 200, "code": None,
                    "message": "上游未返回完整的对话正文或结束标志",
                    "model": model,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000)}
        return {"ok": True, "status": 200, "code": None, "model": model,
                "elapsed_ms": round((time.perf_counter() - started) * 1000)}
    except Exception as exc:
        result = _error_result(exc)
        result["model"] = model
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return result


def _platform_health(db, account: Account) -> dict | None:
    """非默认平台的健康检查：走各自网关的 `account_ops().health()`。

    之前一律用默认平台的 `backend.AccountSession` 去探，结果「扫描 /
    健康检查」永远打默认平台上游，别的平台账号要么 404、要么被误判失效
    （issue #2：账号池的扫描没跟平台走）。

    返回 None 表示「该账号仍走默认平台原有检查逻辑」
    （默认平台 / 未登记平台 / 网关未实现 health）。

    ## 两个坑（issue #129）

    1. **平台注册表是懒加载的**：`load_platforms()` 原来只在平台页 / 网关
       接口被访问时顺手调用。后台巡检、脚本自检这类没打开过平台页的进程里
       `_REGISTRY` 还是空的，`spec_for_storage()` 恒返回 None → 所有非默认
       平台账号被判成「该平台未提供三项健康检查」。这里显式加载一次（幂等）。
    2. **阶段名不能硬绑默认平台那三格**：各平台上报的 stage 名不一样
       （qoder: token/profile/models/quota/chat_link；marvis: token/refresh；
       codearts: token/models/quota/chat_link），原来只认 `session`/`balance`，
       于是 Qoder/Marvis 的余额行恒为 `ok=False` 且 `detail` 是空串 ——
       前端画一条没有原因的红条。
    """
    from admin.platform import registry

    # `_account_label` 只在 `check_account` 里局部导入过，这个函数原来直接用
    # 模块级名字 → 平台 health() 没回 `account` 时 NameError（整条检测 500）。
    from admin.scheduler import _account_label

    try:
        from admin.platform.loader import load_platforms

        load_platforms()
    except Exception as exc:  # noqa: BLE001 - 加载失败按「未登记平台」继续
        _logger.warning("平台注册表加载失败，按未登记平台处理：%s", exc)

    storage = (getattr(account, "platform", "") or "").lower()
    if not storage or storage == registry.DEFAULT_PLATFORM:
        return None
    spec = registry.spec_for_storage(storage)
    if spec is None:
        return None
    gw = registry.load_platform_gateway(spec, spec.realm)
    if gw is None:
        return None
    ops = getattr(gw, "account_ops", None)
    if ops is None:
        return None
    ops = ops()
    health = getattr(ops, "health", None)
    if not callable(health):
        return None
    try:
        res = health(db, account.id)
    except Exception as exc:  # noqa: BLE001 - 单平台失败不影响排查
        _logger.warning("平台 %s 健康检查失败 id=%s: %s", storage, account.id, exc)
        return None
    if not isinstance(res, dict):
        return None
    stages = res.get("stages") or {}

    def _stage(*names: str) -> tuple[str, dict]:
        """按语义取第一个存在的阶段（返回 `(阶段名, 阶段字典)`）。

        各平台的阶段名不统一，硬绑单个名字等于让别的平台永远红条：
        qoder 用 `token`/`profile`/`quota`，marvis 用 `token`/`refresh`，
        codearts 用 `token`/`models`/`quota`。这里按「谁能代表登录态 /
        可用额度 / 对话能力」依次找，找不到就回 `("", {})`。
        """
        for name in names:
            value = stages.get(name)
            if isinstance(value, dict):
                return name, value
        return "", {}

    sess_name, sess_stage = _stage("session", "balance", "token", "profile", "quota")
    model_name, model_stage = _stage("models", "profile")
    chat_name, chat_stage = _stage("conversation", "chat", "chat_link")
    healthy = bool(res.get("ok"))

    def _detail(stage: dict, fallback: str) -> str:
        """阶段原因：先看统一字段，再看各平台的历史写法，最后给一句兜底。

        `detail` 不是唯一写法 —— trae 用 `error`/`message`，codearts 用
        `error`。以前直接取 `detail`，取不到就是空串，前端只能画一条
        **没有原因**的红条。绝不返回空串。
        """
        for key in ("detail", "message", "error", "reason"):
            value = stage.get(key)
            if value:
                return str(value)
        return fallback

    def _row(found: bool, stage: dict, fallback: str) -> dict:
        # 平台没有这个阶段时按整体健康度回落，并写明「本平台无此阶段」
        return {"ok": bool(stage.get("ok")) if found else healthy,
                "detail": _detail(stage, fallback) if found else fallback}

    reasons = []
    for name, stage in stages.items():
        if isinstance(stage, dict) and not stage.get("ok"):
            reasons.append("%s: %s" % (name, _detail(stage, "未通过")))
    return {
        "account_id": account.id,
        "account": res.get("account") or _account_label(account),
        "ok": healthy,
        "healthy": healthy,
        "status": "healthy" if healthy else "failed",
        "restored": False,
        "stages": {
            "balance": _row(bool(sess_name), sess_stage,
                            "本平台无此阶段（%s）" % (storage or "?")),
            "models": _row(bool(model_name), model_stage,
                           "本平台无此阶段（%s）" % (storage or "?")),
            "conversation": _row(bool(chat_name), chat_stage,
                                 "本平台以 session+models 表征健康"),
        },
        "reason": ("平台级健康检查通过" if healthy
                   else "；".join(reasons) or "平台级健康检查未通过（上游未给出具体阶段）"),
    }


def check_account(db, account: Account) -> dict:
    """Check balance, current model catalog, and one real conversation.

    This operation reports evidence but never disables an account because a
    single check failed. A successful three-part check restores an account
    previously disabled by session-dead automation, except for manual stops.
    """
    from admin.scheduler import _account_label

    label = _account_label(account)
    if account.status == "disabled" and (account.cool_kind or "") == "manual_disabled":
        return {"account_id": account.id, "account": label, "ok": False,
                "healthy": False, "status": "skipped", "reason": "账号为手动停用状态，未发起上游请求"}
    if POOL.waf.active():
        return {"account_id": account.id, "account": label, "ok": False,
                "healthy": False, "status": "skipped", "reason": "出口保护期间暂停健康检查"}

    # 键必须与会话租约（POOL.acquire_account）一致：空 UID 账号是 "id:<id>"，
    # 否则 acquire_maintenance 看到的在途数是另一个键，永远「看不见」真实请求。
    uid = POOL.key_for(account)
    lock = maintenance._batch_lock
    if not lock.acquire(blocking=False):
        return {"account_id": account.id, "account": label, "ok": False,
                "healthy": False, "status": "skipped", "reason": "另一个后台维护任务正在运行"}
    reserved = False
    sess = None
    original = account.auth_json
    try:
        reserved = POOL.inflight.acquire_maintenance(uid)
        if not reserved:
            return {"account_id": account.id, "account": label, "ok": False,
                    "healthy": False, "status": "skipped", "reason": "账号当前有在途请求或维护任务"}

        # 非默认平台：走各自网关健康检查，避免用默认平台后端去探
        # （issue #2：账号池扫描/健康检查一直打默认平台上游）。
        plat = _platform_health(db, account)
        if plat is not None:
            return plat

        sess = backend.AccountSession(
            original, enterprise_id=(getattr(account, "enterprise_id", "") or ""))
        balance_value: dict = {}

        def fetch_balance():
            value = sess.fetch_balance()
            balance_value["value"] = value
            return value

        balance = _check_call(fetch_balance,
                              validator=lambda value: isinstance(value, dict)
                              and "total" in value and "remain" in value)
        if balance["ok"]:
            balance["total"] = int(balance_value["value"].get("total") or 0)
            balance["remain"] = int(balance_value["value"].get("remain") or 0)
        model_value: dict = {}

        def fetch_models():
            value = sess.fetch_models()
            model_value["value"] = value
            return value

        models = _check_call(fetch_models,
                             validator=lambda value: isinstance(value, list) and bool(value))
        model_rows = model_value.get("value") or []
        if models["ok"]:
            models["count"] = len(model_rows)
        model, free = _select_chat_model(db, model_rows) if models["ok"] else ("", False)
        if model:
            conversation = _conversation_probe(sess, model)
            conversation["free_model"] = free
        else:
            conversation = {"ok": False, "status": 0, "code": None,
                            "message": "无法取得可用于文本对话的模型目录",
                            "model": "", "free_model": False}

        healthy = bool(balance["ok"] and models["ok"] and conversation["ok"])
        account.auth_json = sess.updated_json()
        was_disabled = account.status == "disabled"
        if healthy:
            balance_data = balance_value["value"]
            account.balance_total = int(balance_data.get("total") or 0)
            account.balance_remain = int(balance_data.get("remain") or 0)
            account.last_sync_at = datetime.utcnow()
            account.session_dead_fails = 0
            if account.status == "disabled" and (account.cool_kind or "") == "session_dead":
                account.status = "active"
                account.cool_until = None
                account.cool_kind = ""
                account.err_count = 0
                account.breaker_fails = 0
                account.breaker_until = None
                account.degrade_until = None
                account.consecutive_fails = 0
                account.last_err_at = None
                account.last_err_msg = ""
                account.last_upstream_error = ""
            elif (account.last_err_msg or "").startswith(("登录态被上游拒绝", "上游认证接口异常")):
                account.last_err_at = None
                account.last_err_msg = ""
                account.last_upstream_error = ""
            db.commit()
        else:
            # Persist rotated credentials only. The health probe is diagnostic,
            # so an endpoint error cannot mutate account status/cooldowns.
            db.query(Account).filter(Account.id == account.id,
                                     Account.auth_json == original).update(
                {Account.auth_json: account.auth_json}, synchronize_session=False)
            db.commit()
        _logger.info("health_check id=%s uid=%s healthy=%s balance=%s models=%s chat=%s",
                     account.id, (account.uid or "")[:12], healthy,
                     balance["ok"], models["ok"], conversation["ok"])
        stages = {"balance": balance, "models": models, "conversation": conversation}
        reasons = [f"{name}: {result.get('message')}" for name, result in stages.items()
                   if not result["ok"]]
        return {"account_id": account.id, "account": label, "ok": healthy,
                "healthy": healthy, "status": "healthy" if healthy else "failed",
                "restored": bool(was_disabled and account.status == "active"), "stages": stages,
                "reason": "三项检查全部通过" if healthy else "；".join(reasons)}
    except Exception as exc:
        db.rollback()
        return {"account_id": account.id, "account": label, "ok": False,
                "healthy": False, "status": "error",
                "reason": str(_error_result(exc).get("message") or "健康检查失败")[:180]}
    finally:
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass
        if reserved:
            POOL.inflight.release_maintenance(uid)
        lock.release()


def run_health_batch(ids: list[int], job) -> dict:
    from admin.db import SessionLocal

    detail = []
    with SessionLocal() as db:
        for index, account_id in enumerate(ids):
            account = db.get(Account, account_id)
            if account is None:
                item = {"account_id": account_id, "account": str(account_id),
                        "ok": False, "healthy": False, "status": "error",
                        "reason": "账号不存在"}
            else:
                job.beat(account.name or account.uid or str(account.id))
                item = check_account(db, account)
            detail.append(item)
            job.add_item(item)
            if index + 1 < len(ids):
                time.sleep(min(60.0, max(0.0, settings.KEEPALIVE_ACCOUNT_GAP)))
    healthy = sum(bool(item.get("healthy")) for item in detail)
    skipped = sum(item.get("status") == "skipped" for item in detail)
    failed = len(detail) - healthy - skipped
    return {"ok": failed == 0 and skipped == 0, "total": len(detail),
            "healthy": healthy, "failed": failed, "skipped": skipped,
            "detail": detail,
            "summary": f"健康检查完成：健康 {healthy}/{len(detail)}，失败 {failed}，跳过 {skipped}"}