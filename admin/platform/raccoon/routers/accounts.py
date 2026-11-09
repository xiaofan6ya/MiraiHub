"""小浣熊管理接口：`/api/raccoon/*`。

    GET  /api/raccoon/meta             平台元信息
    GET  /api/raccoon/accounts         账号列表（按平台隔离）
    POST /api/raccoon/import           导入（单条 / 批量 / 整段粘贴）
    POST /api/raccoon/scan-local       扫描本机（~/.office-raccoon/.../auth.json）
    POST /api/raccoon/import-local     本机凭证入库（扫描 → 勾选 → 确认）
    GET  /api/raccoon/export           导出成 agent2api 兼容格式（备份 / 脱敏）
    POST /api/raccoon/accounts/{id}/refresh   探活并刷新
    POST /api/raccoon/accounts/{id}/test      分段连通性检查
    GET  /api/raccoon/credits          积分总览
    GET  /api/raccoon/usage            用量（本网关账本）

说明：小浣熊的登录态是**明文** auth.json（逆向 `boxAgentAuthFile.js` 确认），
所以 scan-local / import-local 是真的能用的 —— PR #9 时代的 LevelDB
结论早已作废，别照着那句旧注释把这两个接口删掉。
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.platform.raccoon.core import client, endpoints
from admin.platform.raccoon.services import accounts as raccoon_accounts
from admin.security import require_admin

logger = logging.getLogger("admin.platform.raccoon.routers")
router = APIRouter(prefix="/api/raccoon", tags=["raccoon"])

REALM_KEYS = ("cn",)

#: 单次导入的条目上限（每条都会串行探活上游，见 import_accounts 的说明）
MAX_IMPORT_BATCH = 500


def _int_or(value, default: int, *, lo: int | None = None,
            hi: int | None = None) -> int:
    """把 query 参数转 int，并夹到合理区间。

    `isdigit()` 与 `int()` 的口径**不一致**：`"²".isdigit()` 是 True，
    但 `int("²")` 抛 ValueError → 500。所以统一用 try/except + 夹紧。
    """
    try:
        num = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if lo is not None and num < lo:
        return lo
    if hi is not None and num > hi:
        return hi
    return num


def _ids_of(raw: str) -> list[int] | None:
    """解析 `ids=1,2,3`。非法项直接跳过，不抛异常。"""
    out = []
    for chunk in str(raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.append(int(chunk))
        except (TypeError, ValueError):
            continue
    return out or None


def _realm_or_400(realm: str) -> str:
    value = (realm or "").strip().lower()
    if value and value not in REALM_KEYS:
        raise HTTPException(400, detail="realm 必须是 cn")
    return value


@router.get("/meta")
def meta(_: bool = Depends(require_admin)):
    return {
        "platform": endpoints.PLATFORM,
        "regions": [{"key": k, "label": v["label"],
                     "base_url": v["llm_base"]}
                    for k, v in endpoints.REGIONS.items()],
        "auth": "Bearer JWT（HS256，iss=xiaohuanxiong.com/iam）",
        "quota_kind": "balance",
        "note": "积分制；有 refresh 接口但 refreshToken 与 accessToken 同值",
        "auth_mode": "device-auth",
        "auth_explain": (
            "三种入库方式：①「扫描本机」读取客户端登录态"
            "（~/.office-raccoon/agent/config/auth.json，明文 IAM JWT）；"
            "②「导入文件」选本地 JSON / JSONL / TXT（agent2api 备份、裸 JWT 都吃，"
            "自动识别 UTF-8 / GBK）；③「授权登录」扫码免客户端入库——"
            "官方小程序扫码，手机确认，无需滑块与短信验证码。"
        ),
    }


@router.get("/accounts")
def list_accounts(realm: str = "", _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    from admin.platform.raccoon.platform_gateway import RaccoonAccountOps

    return RaccoonAccountOps(_realm_or_400(realm)).list(db)


class ImportIn(BaseModel):
    """导入请求。

    * `records`：批量（agent2api 导出的数组，或单个对象数组）
    * `credential`：单条
    * `text`：整段粘贴（JSON 数组 / JSONL / 裸 JWT 各占一行）
    """

    records: list[dict] | None = None
    credential: dict | str | None = None
    text: str | None = None


def _parse_text(text: str) -> list[dict]:
    """把粘贴的文本解析成记录列表。

    支持三种常见形态，逐层降级：
      1. 整个 JSON 数组 / 单个 JSON 对象
      2. JSONL（一行一个 JSON —— agent2api 导出的备份就是这种）
      3. 纯 JWT 列表（一行一个 `eyJ...`）
    """
    text = (text or "").lstrip("\ufeff").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [x for x in parsed if isinstance(x, (dict, str))]
        if isinstance(parsed, dict):
            # 兼容 {"accounts": [...]}
            if isinstance(parsed.get("accounts"), list):
                return [x for x in parsed["accounts"] if isinstance(x, (dict, str))]
            return [parsed]
    except ValueError:
        pass
    except RecursionError:
        # 深嵌套 JSON 抛的是 RecursionError（**不是** ValueError 的子类），
        # 不接住会直接 500。当成「不是结构化 JSON」，退回逐行解析。
        logger.warning("小浣熊导入文本嵌套过深，退回逐行解析")
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            item = line  # 裸 JWT 行
        except RecursionError:
            continue
        if isinstance(item, (dict, str)):
            out.append(item)
    return out


@router.post("/import")
def import_accounts(body: ImportIn, _: bool = Depends(require_admin),
                    db: Session = Depends(get_db)):
    records: list[dict] = []
    if body.records:
        records.extend(body.records)
    if body.credential:
        records.append(body.credential)
    if body.text:
        records.extend(_parse_text(body.text))
    if not records:
        raise HTTPException(400, detail="没有可导入的凭证")

    # 批次上限：每条都会串行打上游做一次探活（read timeout 180s），
    # 上万个条目会把请求挂住十几分钟，还可能让上游把这批账号连坐风控
    # —— 这正是风控哨兵要防的事。分批导入对用户也没有损失。
    if len(records) > MAX_IMPORT_BATCH:
        raise HTTPException(
            413,
            detail="单次最多导入 %d 条（本次 %d 条），请分批提交"
                   % (MAX_IMPORT_BATCH, len(records)))

    result = raccoon_accounts.import_many(db, records)
    return {"ok": not result["errors"], **result,
            "total": len(records)}


@router.get("/export")
def export_accounts(ids: str = "", mode: str = "backup",
                    _: bool = Depends(require_admin),
                    db: Session = Depends(get_db)):
    """导出账号。

    `mode`：
      * `backup`（默认）— 完整凭证，可回灌；**含明文 token**
      * `plain`         — 不含 token，只给账号清单，可安全外发

    为什么要有两种模式：`ONBOARDING.md` 要求「脱敏/备份」两类导出。
    只给备份模式的话，任何人想「导一份给别人看」都会连带把自己的
    全部 JWT 一起发出去。
    """
    wanted = _ids_of(ids)
    rows = raccoon_accounts.export_records(db, wanted)
    if str(mode).lower() == "plain":
        plain = []
        for row in rows:
            item = dict(row)
            token = str(item.pop("accessToken", "") or "")
            item.pop("refreshToken", None)
            # ⚠️ 不要用 `token[-6:]`：那是 JWT **签名**的末 6 位。
            # 签名虽然不能单独当凭证用，但它是凭证的一部分，
            # 而且注释写着「已脱敏」会让人放松警惕。
            # 取头部（恒为 `eyJhbG`，零信息量）只用于「这条有没有 token」的核对。
            item["token_prefix"] = token[:6] if token else ""
            item["has_token"] = bool(token)
            item["expiresAt"] = int(item.get("expiresAt") or 0)
            plain.append(item)
        return {"format": "plain", "mode": "plain", "count": len(plain),
                "accounts": plain,
                "note": "已移除 accessToken / refreshToken，仅用于清单核对"}
    return {"format": "agent2api-account", "mode": "backup", "count": len(rows),
            "accounts": rows,
            "note": "含明文凭证，请按机密文件保管"}


@router.post("/accounts/{acc_id}/refresh")
def refresh(acc_id: int, _: bool = Depends(require_admin),
            db: Session = Depends(get_db)):
    from admin.platform.raccoon.platform_gateway import RaccoonAccountOps

    try:
        return RaccoonAccountOps("").refresh(db, acc_id, force=True)
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.post("/accounts/{acc_id}/test")
def test(acc_id: int, _: bool = Depends(require_admin),
         db: Session = Depends(get_db)):
    from admin.platform.raccoon.platform_gateway import RaccoonAccountOps

    try:
        return RaccoonAccountOps("").health(db, acc_id)
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.get("/credits")
def credits(realm: str = "", _: bool = Depends(require_admin),
            db: Session = Depends(get_db)):
    from admin.platform.raccoon.platform_gateway import RaccoonAccountOps

    return RaccoonAccountOps(_realm_or_400(realm)).credits(db)


@router.get("/usage")
def usage(realm: str = "", days: int = 30, _: bool = Depends(require_admin),
          db: Session = Depends(get_db)):
    from admin.platform.raccoon.platform_gateway import RaccoonAccountOps

    # days 必须有上下界：无上界时极端值会在 timedelta 里抛 OverflowError → 500
    return RaccoonAccountOps(_realm_or_400(realm)).usage(
        db, days=_int_or(days, 30, lo=1, hi=3650))


@router.get("/scan-local")
def scan_local(_: bool = Depends(require_admin)):
    """扫描本机客户端登录态（auth.json，逆向 boxAgentAuthFile.js 确认）。"""
    from admin.platform.raccoon.platform_gateway import RaccoonAccountOps

    return RaccoonAccountOps("").scan()


class ImportLocalIn(BaseModel):
    uids: list[str] | None = None


class AuthCancelIn(BaseModel):
    auth_id: str = ""


@router.post("/import-local")
def import_local(body: ImportLocalIn | None = None,
                 _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """本机凭证入库（前端「扫描 → 勾选 → 确认」两步）。"""
    from admin.platform.raccoon.platform_gateway import RaccoonAccountOps

    return RaccoonAccountOps("").import_local(
        db, uids=body.uids if body else None)


# ---------------------------------------------------------------------------
# 扫码授权（免客户端）：官方 IAM 小程序扫码登录，成功即入库
# ---------------------------------------------------------------------------
@router.post("/auth/begin")
def auth_begin(_: bool = Depends(require_admin)):
    """创建扫码会话：返回 auth_id + 二维码内容（前端渲染二维码）。"""
    from admin.platform.raccoon.services import webauth

    try:
        return webauth.begin()
    except RuntimeError as exc:
        raise HTTPException(502, detail="官方授权服务不可用：%s" % exc) from exc


@router.get("/auth/poll")
def auth_poll(auth_id: str = "", _: bool = Depends(require_admin),
              db: Session = Depends(get_db)):
    """轮询扫码状态；success 时已入库。"""
    from admin.platform.raccoon.services import webauth

    if not auth_id.strip():
        raise HTTPException(400, detail="缺少 auth_id")
    return webauth.poll(db, auth_id.strip())


@router.get("/auth/qr.svg")
def auth_qr_svg(auth_id: str = "", _: bool = Depends(require_admin)):
    """二维码 SVG（后端渲染，控制台 CSP 只放行同源资源）。"""
    from admin.platform.raccoon.services import webauth

    with webauth._lock:
        sess = webauth._sessions.get(auth_id.strip())
    if sess is None:
        raise HTTPException(404, detail="授权会话不存在或已过期")
    from fastapi.responses import Response

    try:
        svg = webauth.qr_svg(sess["qr_value"])
    except RuntimeError as exc:
        # 缺 qrcode 库：回 503 + 明确处置办法，而不是 500 裸奔
        # （前端 <img> 的 onerror 会把这个 detail 展示给用户）。
        raise HTTPException(503, detail=str(exc))
    return Response(svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@router.post("/auth/cancel")
def auth_cancel(body: AuthCancelIn, _: bool = Depends(require_admin)):
    from admin.platform.raccoon.services import webauth

    return webauth.cancel(body.auth_id)


@router.get("/checkin")
def checkin(realm: str = "", _: bool = Depends(require_admin)):
    """小浣熊没有签到接口 —— 返回 `supported: False`（不是错误）。"""
    return {"supported": False,
            "summary": "小浣熊没有签到接口；额度按积分计量"}
