"""WPS 灵犀管理接口：`/api/wps-lingxi/*`。

    GET  /api/wps-lingxi/meta               平台元信息
    GET  /api/wps-lingxi/scan               扫描本机灵犀客户端登录态（不含 Cookie）
    POST /api/wps-lingxi/import             导入单条（Cookie 或裸 sid）
    POST /api/wps-lingxi/import-batch       批量导入（数组 / JSON 文本 / JSONL）
    POST /api/wps-lingxi/import-local       导入本机客户端账号
    POST /api/wps-lingxi/keepalive          全量探活 + 同步智点
    GET  /api/wps-lingxi/accounts           账号列表
    POST /api/wps-lingxi/accounts/{id}/refresh  重新校验 Cookie + 刷余额
    POST /api/wps-lingxi/accounts/{id}/health   分段健康检查
    POST /api/wps-lingxi/checkin            签到
    GET  /api/wps-lingxi/export             导出（默认脱敏，`reveal=1` 给完整 Cookie）
    GET  /api/wps-lingxi/usage              用量
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.platform.wps_lingxi.core import endpoints
from admin.platform.wps_lingxi.services import accounts as lingxi_accounts
from admin.platform.wps_lingxi.services import activity as lingxi_activity
from admin.platform.wps_lingxi.services import checkin as lingxi_checkin
from admin.platform.wps_lingxi.services import usage as lingxi_usage
from admin.security import require_admin

logger = logging.getLogger("admin.platform.wps_lingxi.routers")
router = APIRouter(prefix="/api/wps-lingxi", tags=["wps-lingxi"])

REALM_KEYS = ("cn",)


def _realm_or_400(realm: str) -> str:
    value = (realm or "").strip().lower()
    if value and value not in REALM_KEYS:
        raise HTTPException(400, detail="realm 必须是 cn")
    return value


@router.get("/meta")
def meta(_: bool = Depends(require_admin)):
    # 能力清单从注册表读，不在这里再抄一份 —— 抄一份就会和
    # `PlatformSpec.extra` 漂移（新增 activity 后前端下拉里看不到）。
    from admin.platform import registry

    spec = registry.get(endpoints.PUBLIC)
    extra = (getattr(spec, "extra", None) or {}) if spec else {}
    actions = extra.get("actions") or ["scan", "import", "export", "checkin",
                                       "refresh-quota", "activity"]
    return {
        "platform": endpoints.PUBLIC,
        "label": "WPS 灵犀",
        "regions": [{"key": "cn", "label": "WPS 灵犀",
                     "base_url": endpoints.base_of()}],
        "auth": "Cookie: wps_sid=<sid>（可带 uid / copilot_server_branch）",
        "quota_kind": "balance",
        "quota_unit": "credits",
        "note": "WPS 灵犀桌面客户端通路：cowork 会话 → credits run → agent-runtime",
        "auth_mode": "cookie",
        "auth_explain": "粘贴 wps_sid 或整段 Cookie；也可导入本机灵犀客户端登录态",
        "actions": list(actions),
        "task_runners": list((extra.get("task_runners") or [])),
        "models_default": len(endpoints.DEFAULT_MODELS),
    }


class ImportIn(BaseModel):
    credential: dict | None = None
    cookie: str | None = None
    #: 导入后是否探活（打一次 profile + balance）。
    probe: bool = True


@router.post("/import")
def import_account(body: ImportIn, _: bool = Depends(require_admin),
                   db: Session = Depends(get_db)):
    record = body.credential if body.credential else body.cookie
    if not record:
        raise HTTPException(400, detail="需要 credential（Cookie 或 sid）")
    try:
        return lingxi_accounts.import_record(db, record, probe=body.probe)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


class BatchIn(BaseModel):
    accounts: list | None = None
    payload: str | None = None
    #: 批量导入默认**不探活**：几十个号逐个打上游会被限流。
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
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(item, dict) or isinstance(item, str):
                    records.append(item)
    if not records:
        raise HTTPException(400, detail="没有可导入的账号")

    ok = fail = 0
    results = []
    for record in records[:500]:
        try:
            result = lingxi_accounts.import_record(db, record,
                                                   probe=body.probe)
            if result.get("ok"):
                ok += 1
            else:
                fail += 1
            results.append({"ok": bool(result.get("ok")),
                            "account_id": result.get("account_id"),
                            "uid": result.get("uid"),
                            "error": (result.get("error") or {}).get("message")
                            if isinstance(result.get("error"), dict) else None})
        except Exception as exc:  # noqa: BLE001 - 单条失败不中断批量
            fail += 1
            results.append({"ok": False, "error": str(exc)[:200]})
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
    return {"ok": True, "imported": ok, "failed": fail,
            "total": len(records), "results": results[:60]}


@router.post("/auth/start")
def auth_start(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """授权登录：拉起一个独立 profile 的浏览器打开灵犀登录页。

    登录完成后由 `/auth/poll` 从该 profile 的 Cookies 库里读 `wps_sid` 并入库
    —— 不用手抄 Cookie，也不碰用户正在用的浏览器资料。
    """
    from admin.platform.wps_lingxi.services import auth as lingxi_auth

    return lingxi_auth.start(db)


@router.get("/auth/poll")
def auth_poll(auth_id: str = "", _: bool = Depends(require_admin),
              db: Session = Depends(get_db)):
    from admin.platform.wps_lingxi.services import auth as lingxi_auth

    if not auth_id:
        raise HTTPException(400, detail="缺少 auth_id")
    return lingxi_auth.poll(db, auth_id)


@router.delete("/auth/{auth_id}")
def auth_cancel(auth_id: str, _: bool = Depends(require_admin)):
    from admin.platform.wps_lingxi.services import auth as lingxi_auth

    return lingxi_auth.cancel(auth_id)


@router.get("/auth/url")
def auth_url(_: bool = Depends(require_admin)):
    """授权入口：给出网页版登录地址 + 操作指引（控制台「授权登录」按钮用）。

    灵犀没有设备码/扫码换 token 的公开协议（客户端走 WPS SSO，凭据是
    `wps_sid` Cookie），所以「授权」= 在浏览器登录网页版 → 本机扫描直接抓
    Cookie。这里把地址和步骤给前端，用户点一下就能开页面。
    """
    return {
        "platform": endpoints.PUBLIC,
        "mode": "browser-cookie",
        "login_url": endpoints.BASE,
        "alternate_urls": [endpoints.KDOCS_BASE],
        "steps": [
            "在 Chrome / Edge 打开上面的地址并登录（手机号 / 微信扫码都行）",
            "回到控制台点「扫描本机」",
            "扫描结果里勾选该账号，点「导入这些凭证」",
        ],
        "hint": "登录态在浏览器 Cookies 里，服务端会直接读取 wps_sid，无需手抄",
        "manual_import": "也可以在 DevTools → Application → Cookies 复制 wps_sid 粘贴导入",
    }


@router.get("/scan-local")
@router.get("/scan")
def scan(_: bool = Depends(require_admin)):
    """扫描本机灵犀客户端登录态。

    路径 `scan-local` 是控制台「扫描本机」按钮的通用约定
    （见 `platformScanLocal`），`scan` 是早期别名，两个都留着。
    """
    return lingxi_accounts.scan_local()


class ImportLocalIn(BaseModel):
    #: 只导入勾选的 uid；为空 + all=true 则全量（控制台「导入这些凭证」）。
    uids: list[str] | None = None
    all: bool = True


@router.post("/import-local")
def import_local(body: ImportLocalIn | None = None, probe: bool = False,
                 _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    payload = body or ImportLocalIn()
    return lingxi_accounts.import_local(
        db, probe=probe,
        uids=None if payload.all and not payload.uids else (payload.uids or []))


@router.post("/keepalive")
def keepalive(realm: str = "", _: bool = Depends(require_admin),
              db: Session = Depends(get_db)):
    return lingxi_accounts.keepalive(db, _realm_or_400(realm))


@router.get("/accounts")
def list_accounts(realm: str = "", _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    from admin.platform.wps_lingxi.platform_gateway import WpsLingxiAccountOps

    return WpsLingxiAccountOps(_realm_or_400(realm)).list(db)


@router.post("/accounts/{acc_id}/refresh")
def refresh(acc_id: int, _: bool = Depends(require_admin),
            db: Session = Depends(get_db)):
    from admin.platform.wps_lingxi.platform_gateway import WpsLingxiAccountOps

    try:
        return WpsLingxiAccountOps("").refresh(db, acc_id)
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.post("/accounts/{acc_id}/health")
def health(acc_id: int, _: bool = Depends(require_admin),
           db: Session = Depends(get_db)):
    from admin.platform.wps_lingxi.platform_gateway import WpsLingxiAccountOps

    return WpsLingxiAccountOps("").health(db, acc_id)


@router.post("/checkin")
def checkin(realm: str = "", _: bool = Depends(require_admin),
            db: Session = Depends(get_db)):
    return lingxi_checkin.run_daily_checkin(db, None, _realm_or_400(realm))


@router.post("/activity")
def activity(realm: str = "", limit: int = 0, _: bool = Depends(require_admin),
             db: Session = Depends(get_db)):
    """手动触发一次「每日活跃对话」（定时任务里也有，这里用于验证/补跑）。"""
    from admin.platform.wps_lingxi.platform_gateway import WpsLingxiAccountOps

    ops = WpsLingxiAccountOps(_realm_or_400(realm))
    # force=True：用户手动点了「活跃对话」，就不该被 85% 概率掷骰跳过
    return lingxi_activity.run_daily_activity(db, ops.realm,
                                              limit=int(limit) or None,
                                              force=True)


@router.post("/ban-recheck")
def ban_recheck(realm: str = "", _: bool = Depends(require_admin),
                db: Session = Depends(get_db)):
    """失效 / 禁用账号复检：Cookie 又有效就恢复。"""
    from admin.platform.wps_lingxi.platform_gateway import WpsLingxiAccountOps

    return WpsLingxiAccountOps(_realm_or_400(realm)).ban_recheck(db)


@router.get("/export")
def export(realm: str = "", reveal: int = 0, _: bool = Depends(require_admin),
           db: Session = Depends(get_db)):
    return lingxi_accounts.export_records(db, _realm_or_400(realm),
                                          reveal=bool(int(reveal or 0)))


@router.get("/usage")
def usage(realm: str = "", days: int = 30, _: bool = Depends(require_admin),
          db: Session = Depends(get_db)):
    from admin.platform.wps_lingxi.platform_gateway import WpsLingxiAccountOps

    return WpsLingxiAccountOps(_realm_or_400(realm)).usage(db, days=days)


@router.get("/usage/detail")
def usage_detail(account_id: int = 0, realm: str = "", days: int = 30,
                 _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """用量详情：上游智点余额 + 收支流水 + 本地按模型聚合。

    `account_id` 传了就只看这一个号（控制台「上游明细」用），
    不传则返回整平台的账号表 + 合计。
    """
    from admin.models import Account

    if account_id:
        account = db.query(Account).filter(
            Account.id == int(account_id),
            Account.platform == lingxi_accounts.PLATFORM).first()
        if account is None:
            raise HTTPException(404, detail="账号不存在")
        return lingxi_usage.account_detail(db, account, days=days)
    return lingxi_usage.realm_detail(db, _realm_or_400(realm), days=days)


@router.get("/usage/logs")
def usage_logs(days: int = 7, page: int = 1, page_size: int = 20,
               account_id: int = 0, source: str = "local",
               _: bool = Depends(require_admin),
               db: Session = Depends(get_db)):
    """使用日志：本地账本（实时）+ 可选上游 run 记录。

    `source=local`（默认）只读本站记账；`source=upstream` 读上游 run 记录
    （需要 `account_id`）；`source=both` 两份都返回，便于对照。
    """
    from admin.models import Account

    local = lingxi_usage.local_logs(db, days=days, page=page,
                                    page_size=page_size,
                                    account_id=account_id or None)
    if source == "local":
        return local

    account = None
    if account_id:
        account = db.query(Account).filter(
            Account.id == int(account_id),
            Account.platform == lingxi_accounts.PLATFORM).first()
        if account is None:
            raise HTTPException(404, detail="账号不存在")
    upstream = (lingxi_usage.upstream_logs(account, days=days, page_size=page_size,
                                          offset=(page - 1) * page_size)
                if account is not None
                else {"ok": False, "items": [],
                      "error": "上游日志需要指定 account_id"})
    if source == "upstream":
        return upstream
    return {"local": local, "upstream": upstream}


@router.get("/{acc_id}/request-usage")
def request_usage(acc_id: int, days: int = 1, page_size: int = 20,
                  offset: int = 0, _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    """控制台「用量明细 → 上游明细」：该账号最近的调用记录。"""
    from admin.models import Account

    account = db.query(Account).filter(
        Account.id == int(acc_id),
        Account.platform == lingxi_accounts.PLATFORM).first()
    if account is None:
        raise HTTPException(404, detail="账号不存在")
    return lingxi_usage.request_usage(account, days=days, page_size=page_size,
                                      offset=offset)


@router.get("")
def list_root(page_size: int = 200, realm: str = "",
              _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """控制台「用量明细」的入口：`/api/wps-lingxi?page_size=200`。

    与 `/accounts` 同源，只是路径对齐其它平台的 `GET /api/<storage>`；
    控制台按这层调用，缺了它这个平台的用量明细会直接空白。
    """
    from admin.platform.wps_lingxi.platform_gateway import WpsLingxiAccountOps

    payload = WpsLingxiAccountOps(_realm_or_400(realm)).list(db)
    items = payload.get("items") or []
    if page_size:
        items = items[:max(1, min(500, int(page_size)))]
    return {**payload, "items": items}