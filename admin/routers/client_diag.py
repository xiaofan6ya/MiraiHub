"""浏览器端自检上报：把控制台在真实浏览器里的运行状况写回本地文件。

为什么需要这个端点：控制台在开发机上用真实浏览器（含 Playwright）反复验证都
干净 —— 0 个 pageerror、26 个脚本全部加载成功、关键函数齐全；但使用者那边持续
报 `Uncaught SyntaxError: Unexpected end of input (at /admin?tab=xxx:1:19)`，
且按钮整片失灵。这个坐标（第 1 行第 19 列）说明出错的是一段**没有 src 的脚本**
在解析到 EOF 时失败，而控制台自身 0 个内联脚本 —— 那段脚本来自页面之外
（浏览器扩展 / 安全软件网页防护 / 加速器 / 桌面壳注入的 native bridge）。
这类注入走 MAIN world，不在 DOM 里留 <script> 节点，从服务端和页面内都看不见。

所以让浏览器把自己看到的东西（错误来源 filename/lineno/colno、脚本资源 HTTP
状态与字节数、后来出现的 <script>、新增的全局属性、UA、页面完整性）上报回来，
落到 `logs/client-diag.jsonl`。一次刷新就能定位，不用再让使用者截图对行号。

安全考虑：
  * 该端点**不要求登录** —— 页面可能在凭据失效时也要能上报；
  * 因此严格限长（96KB）、限条数、只追加写本地文件，不做任何解析执行；
  * 读取接口 `/last` 需要管理员会话。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from admin.security import require_admin

logger = logging.getLogger("admin.client_diag")

router = APIRouter(prefix="/api/client-diag", tags=["client-diag"])

_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "client-diag.jsonl"
_MAX_BODY = 96 * 1024          # 单条上报上限
_MAX_FILE = 4 * 1024 * 1024    # 文件超过 4MB 时轮转
_KEEP = 200                    # /last 默认最多返回多少条

#: 匿名/弱身份上报的频控：同一 IP 每 10 秒最多 20 条。
#: 没有这道闸，任何人都能以 96KB/条的速率把 logs/client-diag.jsonl 刷爆，
#: 甚至伪造「页面被注入」的诊断记录误导排障。E 盘曾被写满过一次，不能再来。
_RATE_WINDOW = 10.0
_RATE_MAX = 20
_rate: dict[str, list[float]] = {}

_lock = threading.Lock()


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    hits = _rate.setdefault(ip, [])
    while hits and now - hits[0] > _RATE_WINDOW:
        hits.pop(0)
    if len(hits) >= _RATE_MAX:
        return True
    hits.append(now)
    return False


def _rotate_if_needed() -> None:
    try:
        if _LOG_PATH.exists() and _LOG_PATH.stat().st_size > _MAX_FILE:
            _LOG_PATH.replace(_LOG_PATH.with_suffix(".jsonl.1"))
    except OSError:
        pass


@router.post("/report")
async def report(request: Request, _: bool = Depends(require_admin)):
    """接收浏览器自检报告（需登录态、限长、限频、只落盘）。

    控制台页面本来就只对已登录会话下发，这里没必要留成匿名写接口 ——
    匿名 = 任何人都能往服务器磁盘写任意 JSON（每条 96KB、无次数限制）。
    """
    ip = request.client.host if request.client else "?"
    if _rate_limited(ip):
        return JSONResponse({"ok": False, "detail": "too many reports"}, status_code=429)
    raw = await request.body()
    if not raw:
        return JSONResponse({"ok": False, "detail": "empty"}, status_code=400)
    if len(raw) > _MAX_BODY:
        return JSONResponse({"ok": False, "detail": "too large"}, status_code=413)
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return JSONResponse({"ok": False, "detail": "bad json"}, status_code=400)

    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ip": ip,
        "data": data if isinstance(data, dict) else {"raw": str(data)[:2000]},
    }
    line = json.dumps(entry, ensure_ascii=False)
    with _lock:
        try:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            _rotate_if_needed()
            with _LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as ex:  # 落盘失败不影响页面
            logger.warning("client-diag 写入失败: %s", ex)
            return JSONResponse({"ok": False, "detail": "write failed"}, status_code=500)

    # 摘要进服务端日志，方便 tail admin.log 也能看到
    d = entry["data"]
    errs = d.get("errors") or []
    inj = d.get("injected") or []
    logger.warning("client-diag 报告: build=%s errors=%d injected=%d ua=%s",
                   d.get("build"), len(errs), len(inj), (d.get("ua") or "")[:80])
    for e in errs[:5]:
        logger.warning("  err %s %s:%s:%s %s",
                       e.get("k"), e.get("file"), e.get("line"), e.get("col"), e.get("msg"))
    return {"ok": True}


@router.get("/last")
def last(limit: int = Query(_KEEP, ge=1, le=500),
         _: bool = Depends(require_admin)):
    """读取最近若干条自检报告（管理员）。"""
    if not _LOG_PATH.is_file():
        return {"count": 0, "items": []}
    rows = []
    try:
        with _LOG_PATH.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except OSError as ex:
        return JSONResponse({"detail": str(ex)}, status_code=500)
    return {"count": len(rows), "items": rows[-limit:]}
