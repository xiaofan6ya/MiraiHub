"""Freebuff 管理接口：`/api/freebuff/*`。

    GET  /api/freebuff/meta               平台元信息
    POST /api/freebuff/import             导入单条（API key）
    POST /api/freebuff/import-batch       批量导入（数组 / JSON 文本 / JSONL）
    GET  /api/freebuff/accounts           账号列表
    POST /api/freebuff/accounts/{id}/refresh   探活（目录可达 + key 状态）
    POST /api/freebuff/accounts/{id}/health    分段健康检查
    POST /api/freebuff/keepalive          全量探活
    POST /api/freebuff/catalog/sync       强制刷新上游模型目录
    GET  /api/freebuff/export             导出（默认脱敏，`reveal=1` 给完整 key）
    GET  /api/freebuff/usage              本地用量
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.platform.freebuff.core import client, endpoints
from admin.platform.freebuff.services import accounts as fb_accounts
from admin.security import require_admin

logger = logging.getLogger("admin.platform.freebuff.routers")
router = APIRouter(prefix="/api/freebuff", tags=["freebuff"])

REALM_KEYS = ("cn",)


def _realm_or_400(realm: str) -> str:
    value = (realm or "").strip().lower()
    if value and value not in REALM_KEYS:
        raise HTTPException(400, detail="realm 必须是 cn")
    return value or "cn"


@router.get("/meta")
def meta(_: bool = Depends(require_admin)):
    return {
        "platform": endpoints.PUBLIC,
        "label": "Freebuff",
        "regions": [{"key": "cn", "label": "Freebuff",
                     "base_url": endpoints.base_url()}],
        "auth": "Authorization: Bearer <Developer API key>（fb_...）",
        "quota_kind": "unknown",
        "quota_note": "上游没有余额接口：/usage /credits /me /balance 均 404",
        "note": "Freebuff Developer API；免费额度只给官方客户端，需 API key",
        "auth_mode": "api-key",
        "auth_explain": "到 https://freebuff.com/account/api 创建 Developer API key "
                        "后粘贴导入；CLI/Desktop 的会话 token（UUID）用不了",
        "actions": ["import", "export", "refresh-quota", "usage"],
        "models_default": len(endpoints.DEFAULT_MODELS),
    }


class ImportIn(BaseModel):
    credential: dict | None = None
    key: str | None = None
    label: str = ""
    #: 导入后是否探活（只校验目录可达，不花额度）。
    probe: bool = True


@router.post("/import")
def import_account(body: ImportIn, _: bool = Depends(require_admin),
                   db: Session = Depends(get_db)):
    record = body.credential if body.credential else body.key
    if record is None:
        raise HTTPException(400, detail="需要 credential 或 key")
    if body.label:
        if isinstance(record, dict):
            record = {**record, "label": body.label}
        else:
            record = {"key": record, "label": body.label}
    try:
        return fb_accounts.import_record(db, record, probe_upstream=body.probe)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


class BatchIn(BaseModel):
    accounts: list | None = None
    payload: str | None = None
    probe: bool = False


@router.post("/import-batch")
def import_batch(body: BatchIn, _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    records: list = list(body.accounts or [])
    if body.payload:
        text = body.payload.strip()
        try:
            data = json.loads(text)
        except Exception:  # noqa: BLE001 - 不是整段 JSON 就按 JSONL 处理
            data = None
        if isinstance(data, dict):
            inner = data.get("accounts")
            if isinstance(inner, list):
                records.extend(inner)
            else:
                records.append(data)
        elif isinstance(data, list):
            records.extend(data)
        elif data is None:
            for line in text.splitlines():
                line = line.strip()
                if line:
                    records.append(line)
    if not records:
        raise HTTPException(400, detail="没有可导入的账号")

    ok = fail = 0
    results = []
    for record in records[:500]:
        try:
            result = fb_accounts.import_record(db, record,
                                               probe_upstream=body.probe)
            ok += 1
            results.append({"ok": True, "account_id": result.get("account_id"),
                            "key_tail": result.get("key_tail")})
        except Exception as exc:  # noqa: BLE001 - 单条失败不中断批量
            fail += 1
            results.append({"ok": False, "error": str(exc)[:200]})
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
    return {"ok": True, "imported": ok, "failed": fail,
            "total": len(records), "results": results[:60]}


@router.get("/accounts")
def list_accounts(realm: str = "", _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    from admin.platform.freebuff.platform_gateway import FreebuffAccountOps

    return FreebuffAccountOps(_realm_or_400(realm)).list(db)


@router.post("/accounts/{acc_id}/refresh")
def refresh(acc_id: int, _: bool = Depends(require_admin),
            db: Session = Depends(get_db)):
    from admin.platform.freebuff.platform_gateway import FreebuffAccountOps

    try:
        return FreebuffAccountOps("cn").refresh(db, acc_id)
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.post("/accounts/{acc_id}/health")
def health(acc_id: int, _: bool = Depends(require_admin),
           db: Session = Depends(get_db)):
    from admin.platform.freebuff.platform_gateway import FreebuffAccountOps

    return FreebuffAccountOps("cn").health(db, acc_id)


@router.post("/keepalive")
def keepalive(realm: str = "", _: bool = Depends(require_admin),
              db: Session = Depends(get_db)):
    return fb_accounts.keepalive(db, _realm_or_400(realm))


@router.post("/catalog/sync")
def catalog_sync(_: bool = Depends(require_admin)):
    """强制刷新上游模型目录缓存，返回模型数与几个样例。"""
    client.clear_catalog_cache()
    rows, note = fb_accounts.catalog_rows()
    payload = {"ok": not bool(note), "models": len(rows),
               "sample": [row["id"] for row in rows[:10]]}
    if note:
        payload["note"] = note
    return payload


@router.get("/export")
def export(realm: str = "", reveal: int = 0, _: bool = Depends(require_admin),
           db: Session = Depends(get_db)):
    return fb_accounts.export_records(db, _realm_or_400(realm),
                                      reveal=bool(int(reveal or 0)))


@router.get("/usage")
def usage(realm: str = "", days: int = 30, _: bool = Depends(require_admin),
          db: Session = Depends(get_db)):
    return fb_accounts.usage(db, _realm_or_400(realm), days)


# ---------------------------------------------------------------- 授权登录
@router.get("/auth/url")
def auth_url(_: bool = Depends(require_admin)):
    """授权页地址 + 本机浏览器可用性（前端据此决定要不要显示「授权登录」）。"""
    from admin.platform.freebuff.services import auth as fb_auth

    info = fb_auth.browser_available()
    return {"url": fb_auth.url(), "browser": info.get("candidates") or [],
            "available": bool(info.get("ok"))}


@router.post("/auth/start")
def auth_start(browser: str = "", _: bool = Depends(require_admin),
               db: Session = Depends(get_db)):
    """拉起独立浏览器的 Freebuff 账户页，返回 `auth_id` 供轮询。"""
    from admin.platform.freebuff.services import auth as fb_auth

    return fb_auth.start(db, browser=browser)


@router.get("/auth/poll")
def auth_poll(auth_id: str = "", _: bool = Depends(require_admin),
              db: Session = Depends(get_db)):
    """轮询授权结果：拿到完整 API key 就自动入库。"""
    from admin.platform.freebuff.services import auth as fb_auth

    if not auth_id:
        raise HTTPException(400, detail="需要 auth_id")
    return fb_auth.poll(db, auth_id)


@router.post("/auth/cancel/{auth_id}")
def auth_cancel(auth_id: str, _: bool = Depends(require_admin)):
    from admin.platform.freebuff.services import auth as fb_auth

    return fb_auth.cancel(auth_id)


# ---------------------------------------------------------------- 活跃 / 复检
@router.post("/activity")
def activity(realm: str = "", limit: int = 0,
             _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """手动跑一次每日活跃（默认还要 `ADMIN_FREEBUFF_ACTIVITY=1` 才真跑）。"""
    from admin.platform.freebuff.services import activity as fb_activity

    return fb_activity.run_daily_activity(db, _realm_or_400(realm),
                                          limit or None)


@router.post("/ban-recheck")
def ban_recheck(realm: str = "", _: bool = Depends(require_admin),
                db: Session = Depends(get_db)):
    """失效账号复检：被 401 禁用的 key 又活了就恢复。"""
    from admin.platform.freebuff.services import activity as fb_activity

    return fb_activity.recheck_disabled(db, _realm_or_400(realm))


# ---------------------------------------------------------------- 用量详情
@router.get("/usage/detail")
def usage_detail(account_id: int = 0, realm: str = "", days: int = 30,
                 _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """单账号详情（给了 account_id）或平台视角汇总。"""
    from admin.models import Account
    from admin.platform.freebuff.services import usage as fb_usage

    realm = _realm_or_400(realm)
    if account_id:
        account = db.query(Account).filter(
            Account.id == int(account_id),
            Account.platform == fb_accounts.PLATFORM).first()
        if account is None:
            raise HTTPException(404, detail="账号不存在")
        return fb_usage.account_detail(db, account, days=days)
    return fb_usage.realm_detail(db, realm, days=days)


@router.get("/usage/logs")
def usage_logs(days: int = 7, page: int = 1, page_size: int = 20,
               account_id: int = 0, model: str = "", errors_only: int = 0,
               _: bool = Depends(require_admin),
               db: Session = Depends(get_db)):
    """使用日志（分页）。只读本地账本 —— 上游没有用量接口。"""
    from admin.platform.freebuff.services import usage as fb_usage

    return fb_usage.local_logs(db, days=days, page=page, page_size=page_size,
                               account_id=account_id, model=model,
                               errors_only=bool(errors_only))


@router.get("/accounts/{acc_id}/usage")
def account_usage(acc_id: int, days: int = 1, page_size: int = 20,
                  _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    from admin.models import Account
    from admin.platform.freebuff.services import usage as fb_usage

    account = db.query(Account).filter(
        Account.id == int(acc_id),
        Account.platform == fb_accounts.PLATFORM).first()
    if account is None:
        raise HTTPException(404, detail="账号不存在")
    return fb_usage.request_usage(db, account, days=days, page_size=page_size)