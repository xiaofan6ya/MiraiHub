"""Read-only release information for the browser UI; installing is native-only."""
from functools import lru_cache
import tempfile
import os
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from admin.security import require_admin
from desktop.common import ROOT, VERSION, RELEASES_URL

router = APIRouter(prefix="/api/app", tags=["releases"])


@router.get("/version")
def version():
    return {"version": VERSION, "releases_url": RELEASES_URL}


@router.get("/network", dependencies=[Depends(require_admin)])
def network(request: Request):
    from admin.network import lan_ipv4
    server = request.scope.get("server") or ("", 8790)
    return {"lan_ips": lan_ipv4(), "port": server[1]}


@router.get("/request-timings", dependencies=[Depends(require_admin)])
def request_timings():
    if os.getenv("WORKBUDDY_ANDROID") != "1":
        raise HTTPException(status_code=404)
    from admin.request_metrics import snapshot
    return snapshot()


@router.get("/icon")
def icon():
    return FileResponse(ROOT / "desktop/assets/app.svg", media_type="image/svg+xml")


@router.get("/desktop-ready")
def desktop_ready(x_desktop_boot: str = Header(default="")):
    expected = os.getenv("WB_DESKTOP_BOOT", "")
    if not expected or not secrets.compare_digest(expected, x_desktop_boot):
        raise HTTPException(status_code=404)
    return {"ready": True}


@lru_cache(maxsize=1)
def service():
    # The server version never downloads or installs executables.
    # Existing source environments may not yet have update-only dependencies.
    # A missing version parser must not prevent the API gateway from starting.
    try:
        from desktop.updates import UpdateService
    except ModuleNotFoundError as exc:
        if exc.name != "packaging" and not (exc.name or "").startswith("packaging."):
            raise
        raise HTTPException(
            status_code=503,
            detail="检查更新不可用：缺少 packaging，请用运行服务的 Python 执行 python -m pip install -r requirements.txt",
        ) from exc
    base = Path(tempfile.gettempdir()) / "workbuddy2api-releases"
    base.mkdir(exist_ok=True)
    return UpdateService(base)


@router.get("/releases", dependencies=[Depends(require_admin)])
def releases():
    return service().check()