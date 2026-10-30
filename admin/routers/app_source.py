#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逆向产物（客户端源码）管理接口：探测、指定安装目录、一键拆包、检索。

定位
----
这是**开发/测试环境**的便利工具，生产环境不需要（生产既没有客户端安装包，
也不该在服务器上放 225MB 的源码）。因此整个路由器由
`ADMIN_DEV_TOOLS`（默认开）控制，关掉后所有接口返回 404。

为什么需要它
------------
逆向产物（`app_source/`）本来要用户自己跑 `asar` 解包，门槛不低：
装 Node、装 npm、跑命令、等 14 秒。而 `wb_asar.py` 已经能用**纯标准库**
读 asar，于是这里把它收进后台 —— 点一个按钮就拆开，还能手动指定
客户端安装目录（用户不一定装在默认盘符）。

接口一览::

    GET    /api/app-source/status          路径 / 是否有安装包 / 产物统计
    POST   /api/app-source/detect          重新扫描本机安装目录
    POST   /api/app-source/locate          校验并保存安装目录（自动补齐层级）
    POST   /api/app-source/reset-paths     清空路径覆盖
    POST   /api/app-source/unpack          一键拆包（异步，返回 job_id）
    GET    /api/app-source/unpack/progress 拆包进度
    POST   /api/app-source/unpack/report   同步预检：这次要写多少文件/字节
    POST   /api/app-source/remove          删除逆向产物
    GET    /api/app-source/search          在产物里检索关键字

安全：全部需要管理员鉴权。`remove` 只删「我们自己产出的那个目录」
（要求含 `.extracted` 标记，或目录名就是 app_source），避免误删用户数据。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from admin.config import settings
from admin.db import get_db
from admin.jobrunner import RUNNER
from admin.security import require_admin
from admin import wb_paths

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/app-source", tags=["app-source"])

#: 拆包任务的固定 job key（同 key 同时只允许一个，重复点击不会叠加）
_JOB_KEY = "unpack_source"


def _guard() -> None:
    """开发工具开关：关掉时整个路由器不可用。"""
    if not getattr(settings, "DEV_TOOLS", True):
        raise HTTPException(status_code=404, detail="逆向产物工具已在配置中关闭")


def _wb():
    """取 wb_install 单例（延迟导入，便于测试替换）。"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    from wb_install import WB
    return WB


class LocateIn(BaseModel):
    """手动指定安装目录。

    接受三种输入（会自动向上补齐到安装基目录）：
    `D:\\WorkBuddy` / `D:\\WorkBuddy\\resources` / `...\\resources\\app.asar`
    """

    install_dir: str = Field(default="", description="安装目录 / resources / app.asar")
    source_dir: str = Field(default="", description="逆向产物输出目录（可留空）")
    save: bool = Field(default=True, description="是否保存到后台设置并立即生效")


class UnpackIn(BaseModel):
    force: bool = Field(default=False, description="强制重抽（覆盖已有文件）")
    dest: str = Field(default="", description="输出目录；留空用当前配置")
    prefixes: list[str] | None = Field(default=None, description="只抽这些顶层目录")
    extensions: list[str] | None = Field(default=None, description="只抽这些后缀")


def _status_payload() -> dict:
    """组装状态：路径、安装包、产物统计、开发工具开关。"""
    WB = _wb()
    paths = WB.describe_paths()
    stat = WB.source_stat()
    return {
        "dev_tools": bool(getattr(settings, "DEV_TOOLS", True)),
        **paths,
        "source_stat": stat,
        # 能不能拆包：必须有 app.asar
        "can_unpack": bool(paths.get("asar")),
        "python": sys.version.split()[0],
        "node": _node_version(),
        "paths_config": wb_paths.current(),
    }


def _node_version() -> str:
    """Node 版本（仅作展示：本工具不需要 Node，但用户常以为需要）。"""
    import subprocess
    from admin.turing_token import _node_bin
    exe = _node_bin()
    if not exe:
        return ""
    try:
        cached = getattr(_node_version, "_cached", None)
        if cached and cached[0] == exe:
            return cached[1]
        r = subprocess.run([exe, "--version"], capture_output=True,
                           text=True, timeout=5,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        version = (r.stdout or "").strip() if r.returncode == 0 else ""
        if version:
            _node_version._cached = (exe, version)
        return version
    except Exception:
        return ""


@router.get("/status")
def status(_: bool = Depends(require_admin)):
    _guard()
    return _status_payload()


@router.post("/detect")
def detect(_: bool = Depends(require_admin)):
    """丢弃缓存重新扫描本机安装目录（用户刚装了客户端时用）。"""
    _guard()
    WB = _wb()
    WB.refresh()
    p = _status_payload()
    p["ok"] = bool(p.get("install_dir"))
    if not p.get("install_dir"):
        p["message"] = ("未扫描到 WorkBuddy 安装目录。可在下方手动指定安装路径"
                        "（例如 D:\\WorkBuddy），或把安装盘符加进 WORKBUDDY_DRIVES。")
    return p


@router.post("/locate")
def locate(body: LocateIn, _: bool = Depends(require_admin),
           db: Session = Depends(get_db)):
    """校验并保存安装目录 / 产物目录。

    用户只需选一个目录，**不用关心该选到哪一层** —— 传上来的
    `resources/` 或 `app.asar` 都会被自动补齐成安装基目录。
    """
    _guard()
    WB = _wb()
    out: dict = {"ok": True}

    if (body.install_dir or "").strip():
        v = WB.validate_install(body.install_dir)
        if not v.get("ok"):
            raise HTTPException(status_code=400,
                                detail=v.get("reason") or "安装目录无效")
        # 保存**规范化后**的基目录，而不是用户原始输入：下次启动直接命中，
        # 不必再走一遍「向上找 resources/app.asar」的推断。
        out["validated"] = v
        paths = {"install_dir": v["install_dir"]}
        # 显式给了 asar 就一并记下（更精确，跳过扫描）
        if (body.install_dir or "").endswith("app.asar"):
            paths["asar_path"] = v["asar"]
    else:
        paths = {}

    if (body.source_dir or "").strip():
        sd = Path(body.source_dir.strip().strip('"')).expanduser()
        try:
            sd.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(status_code=400,
                                detail=f"产物目录不可用：{e}")
        if not os.access(sd, os.W_OK):
            raise HTTPException(status_code=400,
                                detail=f"产物目录不可写：{sd}")
        paths["source_dir"] = str(sd)

    if not paths:
        raise HTTPException(status_code=400, detail="请至少提供安装目录或产物目录")

    if body.save:
        wb_paths.save(paths, db)
        WB.refresh()
        out["saved"] = paths
    out.update(_status_payload())
    return out


@router.post("/reset-paths")
def reset_paths(_: bool = Depends(require_admin),
                db: Session = Depends(get_db)):
    """清空后台保存的路径覆盖，回到「环境变量 + 自动扫描」。"""
    _guard()
    wb_paths.clear(db)
    _wb().refresh()
    return {"ok": True, **_status_payload()}


@router.post("/unpack/plan")
def unpack_plan(_: bool = Depends(require_admin)):
    """预检：这次拆包要写多少文件 / 多少字节。**不写盘**。

    前端据此提示「将写入 225MB，约需 15 秒」，而不是让用户面对一个
    没有任何预期的按钮。
    """
    _guard()
    WB = _wb()
    asar = WB.asar_path()
    if asar is None:
        raise HTTPException(status_code=400, detail="找不到 app.asar，请先指定安装目录")
    try:
        from wb_asar import Asar
        s = Asar(asar).survey()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取 asar 失败：{e}")
    dest = WB.source_dir()
    existing = WB.source_stat()
    return {
        "ok": True,
        "asar": str(asar),
        "dest": str(dest),
        "plan": s,
        "existing": existing,
        # 已有产物时提示「增量补齐」而不是重写
        "already": bool(existing.get("files")),
    }


@router.post("/unpack")
def unpack(body: UnpackIn, _: bool = Depends(require_admin)):
    """一键拆包（**异步**）。

    为什么异步：全量拆包实测 2242 个文件 / 225MB / 约 14 秒。同步跑会顶到
    nginx 的 proxy_read_timeout（默认 60s）边缘，界面也是「点了没反应」。
    这里立即返回 job_id，前端轮询 `/unpack/progress`。
    """
    _guard()
    WB = _wb()
    if WB.asar_path() is None:
        raise HTTPException(status_code=400, detail="找不到 app.asar，请先指定安装目录")

    if RUNNER.is_running(_JOB_KEY):
        job = RUNNER.get(_JOB_KEY)
        return {"ok": True, "job_id": job.id if job else None,
                "already_running": True,
                "message": "拆包任务正在运行中"}

    dest = body.dest.strip() if (body.dest or "").strip() else None
    # 指定了输出目录就一并记成配置值：否则「拆包到 X」之后，
    # 检索/状态仍然看旧的 source_dir，用户会以为拆包失败（实测踩过）。
    if dest:
        try:
            wb_paths.save({"source_dir": dest})
            WB.refresh()
        except Exception as e:
            _logger.warning("保存产物目录失败（仍按本次 dest 拆包）：%s", e)

    def worker(job) -> dict:
        job.set_phase("读取 asar 目录")
        res = WB.ensure_source(prefixes=body.prefixes,
                               extensions=body.extensions,
                               force=body.force, dest=dest,
                               progress=lambda d, t, cur: _on_progress(
                                   job, d, t, cur))
        job.set_phase("完成" if res.get("ok") else "失败")
        # 展平统计字段：ensure_source 把计数放在 res["stat"] 里，
        # 前端只想直接读 written/bytes。在 API 边界统一形状，
        # 免得每个调用方都记着「要先解一层 stat」。
        return _flatten(res)

    job = RUNNER.start(_JOB_KEY, total=100, worker=worker, title="拆包逆向产物")
    return {"ok": True, "job_id": job.id, "message": "已开始拆包"}


def _flatten(res: dict) -> dict:
    """把 ensure_source 的结果展平成前端友好的形状。

    ensure_source 返回 {ok, dir, extracted, reason, stat:{written,bytes,...}}，
    前端要的是「写了多少文件、多少字节、输出到哪」。这里统一摊平，
    并保留原始 reason/stat 供排障。
    """
    out = {
        "ok": bool(res.get("ok")),
        "dest": res.get("dir") or (res.get("stat") or {}).get("dest"),
        "already": not res.get("extracted", True),
        "reason": res.get("reason") or "",
    }
    stat = res.get("stat") or {}
    for k in ("written", "bytes", "skipped_exists", "skipped_missing",
              "total", "missing_samples"):
        if k in stat:
            out[k] = stat[k]
    return out


def _on_progress(job, done: int, total: int, current: str) -> None:
    """把 Asar.extract 的进度映射到 Job（供前端轮询）。

    Job.total 语义是「条目数」，这里直接用文件总数，percent 才有意义。
    """
    if total and job.total != total:
        job.total = total
    job.done = done
    if current:
        job.beat(current)


@router.get("/unpack/progress")
def unpack_progress(_: bool = Depends(require_admin)):
    """拆包进度（前端轮询）。没有任务时返回 running=false。"""
    _guard()
    job = RUNNER.get(_JOB_KEY)
    if job is None:
        return {"running": False, "exists": False}
    snap = job.snapshot()
    snap["running"] = job.status == "running"
    snap["exists"] = True
    return snap


@router.post("/remove")
def remove(_: bool = Depends(require_admin)):
    """删除逆向产物目录。

    只删 `source_dir()` 指向的目录，且要求它带 `.extracted` 标记或名为
    app_source —— 防止用户把 `WORKBUDDY_SOURCE_DIR` 指到重要目录后被误删。
    """
    _guard()
    res = _wb().remove_source()
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("reason") or "删除失败")
    return {"ok": True, **res, **_status_payload()}


@router.get("/search")
def search(q: str, limit: int = 20, _: bool = Depends(require_admin)):
    """在**已产出的源码目录**里检索关键字（等价于对 app_source 做 grep）。

    与 `wb_asar.search` 的区别：那个搜 asar 数据区（unpacked 文件的正文
    不在这里），这个搜落盘后的完整目录，因此更全。产物不存在时明确提示，
    而不是返回空结果让人以为「代码里没有」。
    """
    _guard()
    if not (q or "").strip():
        raise HTTPException(status_code=400, detail="请提供检索关键字")
    WB = _wb()
    src = WB.source_dir()
    if not src.is_dir():
        return {"ok": False, "files": [], "dir": str(src),
                "reason": "逆向产物尚未产出，请先点「一键拆包」"}

    needle = q.strip().lower()
    limit = max(1, min(int(limit or 20), 200))
    hits: list[dict] = []
    scanned = 0
    # 只扫文本类文件；二进制里找字符串没有意义且很慢
    exts = (".js", ".cjs", ".mjs", ".ts", ".json", ".html", ".css",
            ".md", ".txt", ".yml", ".yaml", ".map")
    try:
        for p in src.rglob("*"):
            if len(hits) >= limit:
                break
            if not p.is_file() or p.suffix.lower() not in exts:
                continue
            scanned += 1
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        if needle in line.lower():
                            hits.append({
                                "file": str(p.relative_to(src)).replace("\\", "/"),
                                "line": i,
                                "text": line.strip()[:300],
                            })
                            break        # 每个文件只报第一处，避免结果被单文件刷屏
            except OSError:
                continue
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"检索失败：{e}")
    return {"ok": True, "dir": str(src), "query": q, "scanned": scanned,
            "hits": hits, "truncated": len(hits) >= limit}