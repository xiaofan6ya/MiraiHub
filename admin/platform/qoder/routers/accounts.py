"""Qoder 管理接口：`/api/qoder/*`。"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.platform.qoder.services import accounts as qoder_accounts
from admin.platform.qoder.services import checkin as qoder_checkin
from admin.platform.qoder.services import scan as qoder_scan
from admin.security import require_admin

logger = logging.getLogger("admin.platform.qoder.routers")

router = APIRouter(prefix="/api/qoder", tags=["qoder"])

REALM_KEYS = ("cn", "intl")


def _realm_or_400(realm: str) -> str:
    value = (realm or "").strip().lower()
    if value and value not in REALM_KEYS:
        raise HTTPException(400, detail="realm 必须是 cn 或 intl")
    return value


@router.get("/meta")
def meta(_: bool = Depends(require_admin)):
    """平台元信息。"""
    from admin.platform.qoder.core import endpoints

    return {"platform": "qoder",
            "realms": [{"key": k, "label": v["label"],
                        "open_api": v["open_api"], "gateway": v["gateway"]}
                       for k, v in endpoints.REGIONS.items()],
            "auth": "COSY 自签名（/algo 族） + Bearer（openapi / sash 族）",
            # 设备码授权：凭证由**服务端轮询**取回（deviceToken 端点），
            # 浏览器在哪台设备上都行，所以服务器部署无需任何回调配置。
            "auth_mode": "device-poll",
            "auth_explain": ("Qoder 使用设备码授权：点开链接在任意设备上确认即可，"
                             "凭证由服务端轮询取回，与浏览器所在机器无关。")}


@router.get("/scan-local")
def scan_local(_: bool = Depends(require_admin)):
    """扫描本机 Qoder 登录态（**不含 token**）。"""
    return qoder_scan.discover()


class ImportIn(BaseModel):
    #: PAT（pt- 开头），或完整凭证对象
    pat: str = ""
    realm: str = ""
    name: str = ""
    credential: dict | None = None


@router.post("/import")
def import_account(body: ImportIn, _: bool = Depends(require_admin),
                   db: Session = Depends(get_db)):
    """导入账号。

    两条路：
      * `pat`        —— PAT 换令牌（最快，但需用户生成）
      * `credential` —— 已有凭证对象（备份恢复）
    """
    realm = _realm_or_400(body.realm) or "cn"
    try:
        if body.pat:
            return qoder_accounts.import_pat(db, body.pat, realm, body.name)
        if body.credential:
            record = dict(body.credential)
            record.setdefault("realm", realm)
            return qoder_accounts.import_record(db, record)
    except (ValueError, Exception) as exc:  # noqa: BLE001
        raise HTTPException(400, detail=str(exc)[:300]) from exc
    raise HTTPException(400, detail="需要 pat 或 credential")


@router.get("/export")
def export(realm: str = "", mode: str = "", reveal: int = 0,
           _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """导出号池（issue #25：Qoder 国内/国际的导入导出入口都没了）。

    `mode=plain`（默认）→ **脱敏清单**：uid / 昵称 / 邮箱打码 / 余额 / 到期。
    `mode=backup`       → **完整凭证**：含 token / refresh_token / machine_id，
                          可直接喂回 `/api/qoder/import-batch` 换机器还原。

    `reveal=1` 是老参数，等同 `mode=backup`。

    返回体同时给 `accounts` 数组（控制台通用导出按钮读它）与 `text`
    （JSONL，与 `import_batch` 严格对称）—— 这是「导出→导入」能闭环的前提。
    """
    realm = _realm_or_400(realm) or "cn"
    full = bool(reveal) or str(mode or "").strip().lower() == "backup"
    rows = qoder_accounts.accounts_for(db, realm)
    records: list[dict] = []
    for account in rows:
        record = qoder_accounts.export_record(account, reveal=full)
        if record:
            records.append(record)
    return {"realm": realm, "count": len(records),
            "mode": "backup" if full else "plain", "reveal": full,
            "format": "jsonl", "accounts": records,
            "text": "\n".join(json.dumps(item, ensure_ascii=False) for item in records),
            "note": ("完整凭证含登录态与设备标识，请私密保存"
                     if full else
                     "已脱敏：不含 token，不能直接导入还原同一账号")}


class ImportBatchIn(BaseModel):
    text: str = ""


@router.post("/import-batch")
def import_batch(body: ImportBatchIn, _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """从手机版备份批量导入 Qoder 账号（issue #8：除 workbuddy 外都能导入）。"""
    return qoder_accounts.import_batch(db, body.text)


@router.get("/accounts")
def list_accounts(realm: str = "", _: bool = Depends(require_admin),
                  db: Session = Depends(get_db), request: Request = None):
    from admin.platform.qoder.platform_gateway import QoderAccountOps
    from admin.platform.shared import list_page_params

    lp = list_page_params(request)
    return QoderAccountOps(_realm_or_400(realm)).list(
        db, page=lp["page"] if lp["paginated"] else None,
        page_size=lp["page_size"], search=lp["search"], status=lp["status"],
        sort=lp["sort"], min_credits=lp["min_credits"],
        max_credits=lp["max_credits"])


@router.post("/accounts/{acc_id}/refresh")
def refresh(acc_id: int, _: bool = Depends(require_admin),
            db: Session = Depends(get_db)):
    from admin.platform.qoder.platform_gateway import QoderAccountOps

    try:
        return QoderAccountOps("").refresh(db, acc_id, force=True)
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.post("/accounts/{acc_id}/test")
def test(acc_id: int, _: bool = Depends(require_admin),
         db: Session = Depends(get_db)):
    from admin.platform.qoder.platform_gateway import QoderAccountOps

    try:
        return QoderAccountOps("").health(db, acc_id)
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.get("/models")
def models(realm: str = "", scene: str = "chat", _: bool = Depends(require_admin),
           db: Session = Depends(get_db)):
    """模型目录（按场景分组）。"""
    from admin.platform.qoder.core import client
    from admin.platform.qoder.services import chat as qoder_chat

    target = _realm_or_400(realm) or "cn"
    account = qoder_accounts.select(db, target)
    if account is None:
        raise HTTPException(404, detail="该区没有可用账号")
    data = qoder_accounts.ensure_token(db, account)
    try:
        catalog = client.fetch_model_catalog(
            qoder_accounts.token_of(data), target,
            uid=str(data.get("uid") or ""),
            name=str(data.get("name") or ""),
            email=str(data.get("email") or ""),
            machine_id=qoder_accounts.machine_id_of(data))
    except client.UpstreamError as exc:
        raise HTTPException(exc.status or 502, detail=exc.message) from exc
    return {"items": qoder_chat.model_list(catalog, scene),
            "scenes": {k: len(v) for k, v in catalog.items()
                       if isinstance(v, list)}}


@router.get("/usage")
def usage(realm: str = "", days: int = 30, _: bool = Depends(require_admin),
          db: Session = Depends(get_db)):
    from admin.platform.qoder.platform_gateway import QoderAccountOps

    return QoderAccountOps(_realm_or_400(realm)).usage(db, days=days)


@router.get("/campaigns")
def campaigns(realm: str = "", _: bool = Depends(require_admin),
              db: Session = Depends(get_db)):
    """活动列表（签到入口）。"""
    from admin.platform.qoder.core import client

    target = _realm_or_400(realm) or "cn"
    account = qoder_accounts.select(db, target)
    if account is None:
        raise HTTPException(404, detail="该区没有可用账号")
    data = qoder_accounts.ensure_token(db, account)
    try:
        return client.fetch_campaigns(qoder_accounts.token_of(data), target)
    except client.UpstreamError as exc:
        raise HTTPException(exc.status or 502, detail=exc.message) from exc


@router.post("/checkin")
def checkin(realm: str = "", _: bool = Depends(require_admin),
            db: Session = Depends(get_db)):
    """执行签到（领每日权益）。"""
    return qoder_checkin.run_daily_checkin(db, None, _realm_or_400(realm))


@router.get("/overview")
def overview(realm: str = "", _: bool = Depends(require_admin),
             db: Session = Depends(get_db)):
    from admin.platform.qoder.platform_gateway import QoderGateway

    target = _realm_or_400(realm) or "cn"
    gateway = QoderGateway(target)
    ops = gateway.account_ops()
    data = ops.list(db)
    return {"platform": "qoder", "realm": target,
            "summary": data.get("summary"), "items": data.get("items"),
            "cards": gateway.overview_cards(db)}
