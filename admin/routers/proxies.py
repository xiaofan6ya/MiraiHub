"""代理节点管理接口：`/api/proxies/*`。

用户实测痛点：**一个出口 IP 请求多个账号，IP 会被上游拉黑**。
而池子几百上千时，全从本机一个 IP 出去等于自曝。

这里只做「节点增删改查 + 状态」，实际分配逻辑在 `admin/proxy_pool.py`，
消费方是批量任务（余额刷新 / 签到 / 保活）。

    GET    /api/proxies                列出所有节点（含可用状态 + 当前出口）
    POST   /api/proxies                新增
    PATCH  /api/proxies/{id}           修改（含启停）
    DELETE /api/proxies/{id}           删除
    POST   /api/proxies/{id}/unblock   手动解除静默
    POST   /api/proxies/{id}/test      真实拨一次，回延迟 / 出口 IP
    GET    /api/proxies/status         汇总：可用槽位 / 总并发
    GET    /api/proxies/egress         全局出口：配置 + 当前节点 + 计数
    PUT    /api/proxies/egress         全局出口：开关 / 自动轮换 / 锁定节点
    POST   /api/proxies/egress/rotate  手动切换（缺省 = 切到下一个）
    POST   /api/proxies/test           测试一个还没落库的地址

issue #39 之后这里多了「全局出口」：`admin/proxy_egress.py` 负责对话主链路的
出口选择与轮换，本路由只做配置读写、手动切换和连通性体检的翻译。

安全（用户明确强调「这个很危险」）：

  · **落库加密**：`url` 存 `enc:v1:...` 密文（`admin/secret_box.py`），
    key 由 `ADMIN_JWT_SECRET` 派生；换 secret = 旧密文失效（预期行为）。
  · **出口不落库的选项**：填 `env:PROXY_MAIN` 时直接从环境变量读，
    库里只有一个引用名，连密文都没有。
  · **API 永不返回明文**：列表/详情一律打码成 `user:***@host`，
    没有「查看密码」的接口。要改就整个重填。
  · **日志脱敏**：`last_error` 写库前过 `secret_box.scrub()`，
    因为上游错误体经常原样回显含账密的 URL。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from admin import proxy_egress, proxy_pool, secret_box
from admin.db import get_db
from admin.models import ProxyEndpoint
from admin.security import require_admin

router = APIRouter(prefix="/api/proxies", tags=["proxies"])


def _mask(url: str) -> str:
    """展示用：**先解密再打码**，`scheme://user:pass@host` → `scheme://user:***@host`。

    ## 为什么必须先解密（2026-11-06 踩过）

    库里存的是 `enc:v1:...` 密文。如果直接对密文调 `mask()`，正则匹配不到
    `user:pass@`，于是整串密文原样吐给前端 —— 面板上显示一串 base64，
    既看不出是哪个节点，又等于把密文外泄。

    正确顺序是：**解密 → 打码 → 返回**。这样：
      · 用户能看到 `host:port`（能认出是哪个出口）；
      · 密码永远是 `***`（面板被截图也不泄）；
      · 明文只在内存里存在一瞬间，不进响应体。

    对环境变量引用（`env:NAME`）原样显示引用名 —— 名字本身不是秘密。
    """
    text = str(url or "")
    if not text:
        return ""
    if text.startswith(secret_box.ENV_PREFIX):
        return text
    plain = secret_box.decrypt(text)
    if not plain:
        # 解密失败（密钥轮换 / 数据损坏）：**不要回退显示密文**
        return "（无法解密，请重新填写）"
    return secret_box.mask(plain)


def _view(row: ProxyEndpoint, now, current_id: int = 0) -> dict:
    blocked = bool(row.blocked_until and row.blocked_until > now)
    probe = proxy_egress.probe_cache().get(int(row.id))
    return {
        "id": row.id,
        "label": row.label or "",
        "url": _mask(row.url),
        "enabled": bool(row.enabled),
        "max_concurrency": int(row.max_concurrency or 1),
        "pool": row.pool or "",
        "blocked": blocked,
        "blocked_until": row.blocked_until.isoformat() if row.blocked_until else None,
        "last_ok_at": row.last_ok_at.isoformat() if row.last_ok_at else None,
        "last_error": row.last_error or "",
        "state": ("disabled" if not row.enabled
                  else "blocked" if blocked else "ready"),
        # 全局出口的「当前节点」标记 + 最近一次连通性体检结果（issue #39）。
        "current": bool(current_id and int(row.id) == int(current_id)),
        "probe": probe,
    }


class ProxyIn(BaseModel):
    label: str = ""
    url: str = Field(min_length=8)
    enabled: int = 1
    max_concurrency: int = Field(default=1, ge=1, le=32)
    pool: str = ""


class ProxyPatch(BaseModel):
    label: str | None = None
    url: str | None = None
    enabled: int | None = None
    max_concurrency: int | None = Field(default=None, ge=1, le=32)
    pool: str | None = None


class EgressIn(BaseModel):
    """全局出口配置（issue #39）。只传要改的键。"""

    enabled: int | None = None
    mode: str | None = None
    endpoint_id: int | None = None
    pool: str | None = None
    rotate_seconds: int | None = None
    rotate_per_request: int | None = None


class EgressRotateIn(BaseModel):
    """手动切换：`endpoint_id=0` / 缺省 = 切到下一个可用节点。"""

    endpoint_id: int = 0


class ProbeIn(BaseModel):
    """测试一个还没落库的地址（新节点保存前先试连通）。"""

    url: str = Field(min_length=8)


@router.get("")
def list_proxies(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """列出全部节点 + 汇总 + 当前出口标记。

    汇总里的 `slots` 才是**真正生效的并发上限**：Σ(可用节点 max_concurrency)。
    """
    from datetime import datetime

    now = datetime.utcnow()
    rows = db.query(ProxyEndpoint).order_by(ProxyEndpoint.id.asc()).all()
    current_id = proxy_egress.active_endpoint_id(db)
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row.pool or "", []).append(row)
    views = [_view(row, now, current_id) for row in rows]
    return {
        "items": views,
        "summary": {
            "total": len(rows),
            "ready": sum(1 for item in views if item["state"] == "ready"),
            "blocked": sum(1 for item in views if item["state"] == "blocked"),
            "disabled": sum(1 for item in rows if not row.enabled),
            "slots": proxy_pool.total_slots(db, ""),
            "current_id": current_id,
        },
        "pools": sorted(groups.keys()),
        "note": ("并发上限 = 各可用节点 max_concurrency 之和。"
                 "住宅代理建议 1~2，机房可以 5~10；"
                 "单节点并发越高，被上游盯上的概率越大。"),
    }


@router.get("/status")
def status(pool: str = "", _: bool = Depends(require_admin),
           db: Session = Depends(get_db)):
    """某个代理组当前可用多少槽（前端配任务时用来显示「自动并发」是多少）。"""
    slots = proxy_pool.slots(db, pool)
    return {"pool": pool or "（全部）", "slots": len(slots),
            "urls": [slot["label"] for slot in slots]}


@router.post("")
def create_proxy(body: ProxyIn, _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    url = str(body.url or "").strip()
    # 环境变量引用不校验协议（值在环境里，库里只放引用名）
    if not url.startswith(secret_box.ENV_PREFIX):
        if not re.match(r"^(https?|socks5h?)://", url):
            raise HTTPException(
                400, detail="代理地址必须以 http:// / https:// / socks5:// 开头，"
                            "或写成 env:变量名 从环境变量读取")
    try:
        stored = (url if url.startswith(secret_box.ENV_PREFIX)
                  else secret_box.encrypt(url))
    except Exception as exc:  # noqa: BLE001 - 加密不可用时拒绝存明文
        raise HTTPException(500, detail="代理地址加密失败，已拒绝以明文保存：%s" % exc) from exc
    row = ProxyEndpoint(
        label=str(body.label or "").strip()[:64],
        url=stored[:1024],
        enabled=1 if body.enabled else 0,
        max_concurrency=max(1, min(32, int(body.max_concurrency or 1))),
        pool=str(body.pool or "").strip()[:32],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    from datetime import datetime

    return {"ok": True, "item": _view(row, datetime.utcnow())}


@router.patch("/{pid}")
def patch_proxy(pid: int, body: ProxyPatch, _: bool = Depends(require_admin),
                db: Session = Depends(get_db)):
    row = db.get(ProxyEndpoint, pid)
    if row is None:
        raise HTTPException(404, detail="节点不存在")
    if body.label is not None:
        row.label = str(body.label or "").strip()[:64]
    if body.url is not None:
        url = str(body.url or "").strip()
        if url and not url.startswith(secret_box.ENV_PREFIX) \
                and not re.match(r"^(https?|socks5h?)://", url):
            raise HTTPException(400, detail="代理地址协议不合法")
        if url:
            # 打码值（含 `***`）不写回：那是前端把展示值当输入提交了，
            # 直接落库会把真密码覆盖成 `***`，节点立刻不可用。
            if "***" in url:
                raise HTTPException(
                    400, detail="检测到打码后的地址（含 ***）。修改代理请重新填写完整地址。")
            try:
                row.url = (url if url.startswith(secret_box.ENV_PREFIX)
                           else secret_box.encrypt(url))[:1024]
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(500, detail="加密失败，已拒绝明文保存：%s" % exc) from exc
    if body.enabled is not None:
        row.enabled = 1 if body.enabled else 0
    if body.max_concurrency is not None:
        row.max_concurrency = max(1, min(32, int(body.max_concurrency)))
    if body.pool is not None:
        row.pool = str(body.pool or "").strip()[:32]
    db.commit()
    from datetime import datetime

    return {"ok": True, "item": _view(row, datetime.utcnow())}


@router.delete("/{pid}")
def delete_proxy(pid: int, _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    row = db.get(ProxyEndpoint, pid)
    if row is None:
        raise HTTPException(404, detail="节点不存在")
    db.delete(row)
    db.commit()
    return {"ok": True, "id": pid}


@router.post("/{pid}/unblock")
def unblock(pid: int, _: bool = Depends(require_admin),
            db: Session = Depends(get_db)):
    """手动解除静默（确认节点换过 IP / 问题已修之后用）。"""
    row = db.get(ProxyEndpoint, pid)
    if row is None:
        raise HTTPException(404, detail="节点不存在")
    row.blocked_until = None
    row.last_error = ""
    db.commit()
    return {"ok": True, "id": pid}


# --------------------------------------------------------------------------- #
# 全局出口（issue #39）：配置 / 状态 / 手动切换 / 连通性体检
# --------------------------------------------------------------------------- #

@router.get("/egress")
def get_egress(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """全局出口现状：开关、模式、当前节点、轮换倒计时、计数与告警。"""
    return proxy_egress.status(db)


@router.put("/egress")
def put_egress(body: EgressIn, _: bool = Depends(require_admin),
               db: Session = Depends(get_db)):
    """保存全局出口配置（合并式：只改传进来的键）。

    `mode=fixed` 且 `endpoint_id` 指向不存在 / 已停用的节点时直接 400 ——
    让运营在保存那一刻就知道锁错了，而不是等对话请求静默退回直连。
    """
    patch = {key: value for key, value in body.model_dump().items() if value is not None}
    mode = str(patch.get("mode") or "").strip().lower()
    if mode and mode not in proxy_egress.MODES:
        raise HTTPException(400, detail="mode 只能是 auto（自动轮换）或 fixed（锁定节点）")
    endpoint_id = int(patch.get("endpoint_id") or 0)
    if endpoint_id:
        row = db.get(ProxyEndpoint, endpoint_id)
        if row is None:
            raise HTTPException(404, detail="要锁定的节点不存在")
        if not row.enabled:
            raise HTTPException(400, detail="要锁定的节点已停用，先启用再锁定")
    proxy_egress.save_settings(db, patch)
    # 开启的那一刻就把「当前节点」定下来：面板不该等到第一条对话请求才有值。
    proxy_egress.ensure_current(db)
    return proxy_egress.status(db)


@router.post("/egress/rotate")
def rotate_egress(body: EgressRotateIn | None = None,
                  _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    """手动切换当前出口：不传 id 就按 id 顺序切下一个。"""
    target = int((body.endpoint_id if body else 0) or 0)
    result = proxy_egress.rotate(db, endpoint_id=target, reason="manual")
    if not result.get("ok"):
        raise HTTPException(400, detail=result.get("error") or "切换失败")
    return {"ok": True, "current": result.get("current"), "status": proxy_egress.status(db)}


@router.post("/test")
def test_proxy_url(body: ProbeIn, _: bool = Depends(require_admin)):
    """测试一个还没落库的地址：真发一次请求，回延迟与出口 IP。"""
    url = str(body.url or "").strip()
    if not url.startswith(secret_box.ENV_PREFIX) \
            and not re.match(r"^(https?|socks5h?)://", url):
        raise HTTPException(400, detail="代理地址协议不合法")
    return proxy_egress.test_url(url)


@router.post("/{pid}/test")
def test_proxy(pid: int, _: bool = Depends(require_admin),
               db: Session = Depends(get_db)):
    """测试一个已保存的节点。

    结果会写回 `last_ok_at` / `last_error`，并缓存给列表页展示
    （延迟、出口 IP、失败原因）。面板上的绿灯只能来自这次真实拨号。
    """
    result = proxy_egress.test_endpoint(db, pid)
    if result.get("endpoint_id") and result.get("probe") is not None:
        from datetime import datetime

        row = db.get(ProxyEndpoint, pid)
        if row is not None:
            result["item"] = _view(row, datetime.utcnow(),
                                   proxy_egress.active_endpoint_id(db))
    return result