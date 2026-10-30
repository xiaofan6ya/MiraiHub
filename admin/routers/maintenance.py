"""Authenticated upgrade guidance and online SQLite backup; no host execution."""
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy.orm import Session

from admin.db import engine, get_db
from admin.models import Account, CreditEvent
from admin.security import require_admin
from admin.routers.releases import service
from desktop.common import VERSION, RELEASES_URL

logger = logging.getLogger("admin.maintenance")

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"], dependencies=[Depends(require_admin)])


@router.get("/update")
def update_info():
    from packaging.version import Version, InvalidVersion
    snapshot = service().check()
    candidates = []
    for release in snapshot.get("releases", []):
        try:
            version = Version(release["tag"].lstrip("vV"))
            if not version.is_prerelease and re.fullmatch(r"[vV]?\d+\.\d+\.\d+", release["tag"]):
                candidates.append((version, release))
        except (InvalidVersion, KeyError, TypeError):
            continue
    latest = max(candidates, key=lambda item: item[0], default=(None, None))[1]
    tag = latest["tag"] if latest else None
    commands = None
    commands_cn = None
    if tag:
        commands = {
            "docker": f"git status --short\ngit fetch origin --tags\ngit checkout {tag}\ndocker compose --env-file .env.docker up -d --build admin\ndocker compose --env-file .env.docker logs --tail=100 admin",
            "linux": f"git status --short\ngit fetch origin --tags\ngit checkout {tag}\npython -m pip install -r requirements.txt\n# 使用当前部署对应的 systemd / supervisor / 启动脚本重启服务",
        }
        # 国内网络：pip 走清华镜像；基础镜像与 git 可按需再套代理。
        commands_cn = {
            "docker": f"git status --short\ngit fetch origin --tags\ngit checkout {tag}\ndocker compose --env-file .env.docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple admin\ndocker compose --env-file .env.docker up -d admin\ndocker compose --env-file .env.docker logs --tail=100 admin\n# 拉基础镜像慢时可改用镜像仓库前缀，例如：\n# docker pull docker.m.daocloud.io/library/python:3.12-slim && docker tag docker.m.daocloud.io/library/python:3.12-slim python:3.12-slim",
            "linux": f"git status --short\ngit fetch origin --tags\ngit checkout {tag}\npython -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple\n# 使用当前部署对应的 systemd / supervisor / 启动脚本重启服务",
        }
    return {"version": VERSION, "latest": {k: latest.get(k) for k in ("tag", "name", "url", "date")} if latest else None,
            "available": bool(latest and Version(tag.lstrip("vV")) > Version(VERSION)),
            "error": snapshot.get("error", ""), "releases_url": RELEASES_URL,
            "database": engine.dialect.name, "backup_supported": engine.dialect.name == "sqlite",
            "commands": commands, "commands_cn": commands_cn,
            "backup_note": "备份含账号凭据和 API Key，请私密保存。升级前先备份；确认 git status 无本地改动后，在项目目录执行命令。NAS 可使用容器管理器重新构建并重启，保留 /data 数据卷。",
            "mysql_backup_command": "mysqldump --single-transaction --routines --triggers -h <数据库主机> -u <数据库用户> -p <数据库名> > workbuddy-backup.sql" if engine.dialect.name != "sqlite" else None}


@router.post("/backup")
def backup():
    """在线备份当前 SQLite 库，以文件响应返回。

    ## 为什么每一步都记日志

    桌面版（EXE）点「下载 SQLite 备份」报 `Failed to fetch` ——
    那是 **fetch 层的网络失败**，不是 HTTP 错误码，浏览器不会给出任何细节。
    没有服务端日志就完全无法区分三种情况：

        a) 请求根本没到（前端 URL / 鉴权 / 代理问题）
        b) 请求到了但备份抛异常，连接被关（前端看到 fetch 失败）
        c) 备份成功但传输中断（大文件 / 客户端超时）

    现在开始、结束、异常三处都落日志，下次一看便知。
    """
    started = time.perf_counter()
    logger.info("备份开始 数据库=%s", engine.dialect.name)
    if engine.dialect.name != "sqlite":
        logger.warning("备份被拒 数据库=%s 只有 sqlite 支持在线备份", engine.dialect.name)
        raise HTTPException(409, "当前数据库不支持页面在线备份，请使用数据库管理工具或 mysqldump 完成一致性备份")
    fd, name = tempfile.mkstemp(prefix="workbuddy-backup-", suffix=".db")
    os.close(fd)
    path = Path(name)
    try:
        with engine.connect() as source:
            raw = source.connection.driver_connection
            with closing(sqlite3.connect(name)) as target:
                raw.backup(target, pages=256, sleep=0.01)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("backup integrity check failed")
        size = path.stat().st_size
        filename = "workbuddy-backup-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".db"
        logger.info("备份完成 文件=%s 大小=%.1fKB 耗时=%dms",
                    filename, size / 1024.0,
                    int((time.perf_counter() - started) * 1000))
        return FileResponse(path, media_type="application/octet-stream", filename=filename,
                            headers={"Cache-Control": "no-store"}, background=BackgroundTask(path.unlink, missing_ok=True))
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    except Exception:
        path.unlink(missing_ok=True)
        logger.exception("备份失败 耗时=%dms", int((time.perf_counter() - started) * 1000))
        raise


@router.get("/backup/info")
def backup_info():
    """备份前置探测：前端点按钮**先查这个**，再决定要不要发那次大请求。

    桌面版报 `Failed to fetch` 时，这个轻量 GET 能立刻区分
    「后端不可达」和「备份本身有问题」—— 前者这里就会失败，
    后者这里返回 ok 而下载失败。
    """
    db_path = engine.url.database or ""
    exists = bool(db_path) and Path(db_path).is_file()
    size = Path(db_path).stat().st_size if exists else 0
    return {"ok": True, "database": engine.dialect.name,
            "backup_supported": engine.dialect.name == "sqlite",
            "db_exists": exists, "db_size": size}


#: 恢复校验要求的最小表集合 —— 上传文件必须长得像本系统的库，
#: 防止把任意 sqlite 文件灌进生产。
_REQUIRED_TABLES = {"accounts", "api_keys", "usage_logs", "model_configs"}


def _restore_sqlite_bytes(data: bytes) -> dict:
    """把上传的备份字节恢复为当前 sqlite 库（就地、带安全副本）。

    流程：临时落盘 → integrity_check + 表结构校验 → 复制当前库为
    ``<db>.pre-restore-<ts>`` 安全副本 → 用 sqlite backup API 把上传库
    整体写进当前库文件（对同文件的其他连接是原子切换页级内容）。
    """
    if engine.dialect.name != "sqlite":
        raise HTTPException(409, "当前数据库不是 sqlite，请在数据库侧完成恢复")
    db_path = engine.url.database
    if not db_path or db_path.startswith(":memory:") or not Path(db_path).exists():
        raise HTTPException(409, "找不到当前数据库文件，无法在线恢复")
    if not data:
        raise HTTPException(400, "上传内容为空")

    fd, name = tempfile.mkstemp(prefix="workbuddy-restore-", suffix=".db")
    os.close(fd)
    up = Path(name)
    try:
        up.write_bytes(data)
        try:
            with closing(sqlite3.connect(name)) as check:
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise HTTPException(400, "上传文件不是有效的 SQLite 数据库（完整性校验失败）")
                tables = {r[0] for r in check.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                missing = _REQUIRED_TABLES - tables
                if missing:
                    raise HTTPException(400, "上传文件缺少本系统数据表（%s），不是有效的 MiraiHub 备份"
                                         % ", ".join(sorted(missing)))
                stats = {t: check.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                         for t in sorted(_REQUIRED_TABLES)}
        except sqlite3.DatabaseError as exc:
            raise HTTPException(400, f"上传文件不是有效的 SQLite 数据库：{exc}")

        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        safety = Path(db_path).with_name(Path(db_path).name + f".pre-restore-{ts}")
        shutil.copy2(db_path, safety)
        try:
            with closing(sqlite3.connect(str(up), timeout=30)) as source, \
                    closing(sqlite3.connect(db_path, timeout=30)) as target:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise HTTPException(500, "恢复后完整性校验失败，已保留恢复前安全副本")
        except HTTPException:
            shutil.copy2(safety, db_path)
            raise
        except Exception as exc:
            shutil.copy2(safety, db_path)
            raise HTTPException(500, f"恢复失败已回滚到原库：{exc}")
        return {"ok": True, "tables": stats, "safety_copy": safety.name,
                "note": "恢复完成。账号凭据与配置已替换，建议重启服务让内存态（账号池/缓存）与磁盘一致。"}
    finally:
        up.unlink(missing_ok=True)


@router.post("/restore")
async def restore(request: Request):
    data = await request.body()
    size_limit = 512 * 1024 * 1024
    if len(data) > size_limit:
        raise HTTPException(413, "备份文件超过 512MB 上限")
    return _restore_sqlite_bytes(data)


#: Web 端重启：退出 worker，由 main.py 监督器自动重生（exe/apk 没有
#: systemd，之前子进程退出=整个服务死；2026-10-24 起监督器会重新拉起）。
#: 鉴权与防滥用：require_admin（路由级）+ 显式确认体 + 60 秒频控 + 审计日志。
_restart_lock = threading.Lock()
_restart_last = 0.0


@router.post("/restart")
async def restart_service(request: Request):
    global _restart_last
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if payload.get("confirm") is not True:
        raise HTTPException(400, "缺少确认字段：{\"confirm\": true}")
    with _restart_lock:
        now = time.monotonic()
        if now - _restart_last < 60:
            raise HTTPException(429, f"重启太频繁，请 {int(60 - (now - _restart_last))} 秒后再试")
        _restart_last = now
    ip = request.client.host if request.client else "?"
    logging.getLogger("admin.maintenance").warning(
        "管理员请求重启服务（ip=%s ua=%s），1.5 秒后退出 worker 交由监督器重生", ip,
        request.headers.get("user-agent", ""))

    def _exit_soon():
        time.sleep(1.5)
        os._exit(0)

    threading.Thread(target=_exit_soon, daemon=True).start()
    return {"ok": True, "message": "服务将在约 2 秒后重启（监督器自动拉起新实例），期间请求会短暂中断"}


@router.get("/credit-events")
def credit_events(account_id: int | None = None, source: str = "", kind: str = "",
                  start: datetime | None = None, end: datetime | None = None,
                  page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                  request: Request = None,
                  db: Session = Depends(get_db)):
    """积分流水。

    **平台作用域**：`credit_events` 只有 `account_id`，没有平台列，
    因此按「该平台下的账号 id 集合」收窄 —— 与用量日志同一套做法，
    不需要给历史数据补字段。
    """
    from admin.credit_journal import backfill
    from admin.platform import scope as platform_scope

    importing = backfill(db)
    platform, realm = platform_scope.from_request(request)
    scope_ids = platform_scope.account_ids(db, platform, realm)

    query = db.query(CreditEvent)
    if scope_ids is not None:
        if not scope_ids:
            # 该平台没有账号 -> 结果必须为空，不能退化成全量
            return {"items": [], "total": 0, "page": page,
                    "page_size": page_size, "importing": importing,
                    "sources": [], "accounts": []}
        query = query.filter(CreditEvent.account_id.in_(scope_ids))
    if account_id is not None:
        query = query.filter(CreditEvent.account_id == account_id)
    if source:
        query = query.filter(CreditEvent.source == source)
    if kind:
        if kind not in ("confirmed", "observed"):
            raise HTTPException(400, "记录类型无效")
        query = query.filter(CreditEvent.kind == kind)
    def utc(value):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if start:
        query = query.filter(CreditEvent.created_at >= utc(start))
    if end:
        query = query.filter(CreditEvent.created_at <= utc(end))
    if start and end and utc(start) > utc(end):
        raise HTTPException(400, "开始时间不能晚于结束时间")
    total = query.count()
    rows = query.order_by(CreditEvent.created_at.desc(), CreditEvent.id.desc()).offset((page-1)*page_size).limit(page_size).all()
    # 账号下拉也必须跟随平台作用域：流水本身已经收窄了，但下拉若列全平台账号，
    # 用户会以为「这个平台下还有别的号」，选了一个又查不到记录。
    if scope_ids is not None:
        account_names = {}
        if scope_ids:
            account_names = {aid: name for aid, name in db.query(
                Account.id, Account.name).filter(Account.id.in_(scope_ids))}
    else:
        historical = db.query(CreditEvent.account_id, CreditEvent.account_name).distinct().all()
        account_names = {aid: name for aid, name in historical}
        account_names.update({aid: name for aid, name in db.query(Account.id, Account.name)})
    # 来源同样按作用域统计，避免下拉里出现「当前平台根本没有的来源」
    if scope_ids is not None:
        source_rows = []
        if scope_ids:
            source_rows = [s for (s,) in db.query(CreditEvent.source).filter(
                CreditEvent.account_id.in_(scope_ids)).distinct().order_by(CreditEvent.source)]
    else:
        source_rows = [s for (s,) in db.query(CreditEvent.source).distinct().order_by(CreditEvent.source)]
    return {"items": [{"id": r.id, "account_id": r.account_id, "account_name": r.account_name,
                        "kind": r.kind, "source": r.source, "delta": r.delta, "balance_after": r.balance_after,
                        "created_at": r.created_at.isoformat() + "Z"} for r in rows],
            "total": total, "page": page, "page_size": page_size, "importing": importing,
            "sources": source_rows,
            "accounts": [{"id": aid, "name": name} for aid, name in sorted(account_names.items())]}