#!/usr/bin/env python
"""平台模块脚手架：一条命令生成符合契约的完整骨架。

## 为什么需要它

`ONBOARDING.md` 说要实现 10 个方法、11 项账号操作、4 个卡片 ——
手写容易漏（尤其 `platform_id` 属性和方法签名，漏了 `isinstance` 就 False）。

这个脚本生成的骨架**直接能过 `verify_platform.py` 的结构检查**，
你只需要填 3 个 TODO。

## 用法

    python scaffold.py --platform myapp --label "MyApp" --realm cn \
        --base-url https://api.myapp.com/v1

生成：

    admin/platform/<platform>/
        __init__.py            平台声明 + install()
        gateway.py             /v1/* 转发（含三协议）
        platform_gateway.py    Gateway 契约（11 项 + 任务 + 探测 + 卡片）
        core/endpoints.py      端点表
        core/client.py         HTTP 客户端
        services/accounts.py   凭证与账号池
        services/scan.py       本机扫描
        routers/accounts.py    /api/<platform>/*
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[4]  # 仓库根
PLATFORM_ROOT = ROOT / "admin" / "platform"


INIT_PY = '''"""{label} 平台网关。

## 目录职责

    admin/platform/{pid}/
      __init__.py            平台声明 + install()
      gateway.py             /v1/* 转发实现
      platform_gateway.py    Gateway 契约（账号池 / 任务 / 探测 / 卡片）
      core/endpoints.py      端点表
      core/client.py         HTTP 客户端
      services/accounts.py   凭证与账号池
      services/scan.py       本机扫描
      routers/accounts.py    /api/{pid}/*

## 接入来源

拆包侦察结论见 `docs/{pid}-recon.md`（如有）。
**实现前先跑 skill 里的脚本**：

    python admin/platform/skill/scripts/recon.py "<安装目录>"
    python admin/platform/skill/scripts/probe.py --recon recon-report.json
    python admin/platform/skill/scripts/creds.py --source "<unpacked 目录>"
"""
from __future__ import annotations

import logging

from admin.platform.gateway import AccountOps, ClientProbe, Gateway
from admin.platform.registry import PlatformSpec

logger = logging.getLogger("admin.platform.{pid}")

PLATFORM = "{pid}"
#: 落库区域（没有多区就留一个空串）
REALMS = {realms_src}
#: 区域 -> 对外平台 id
REALM_IDS = {{{realm_ids}}}


def _task_runners() -> dict:
    """本平台支持的定时任务。**不支持就返回空 dict**。"""
    return {{}}


def install() -> None:
    """登记 PlatformSpec（幂等）。"""
    from admin.platform import registry as _registry

    for realm in REALMS:
        platform_id = REALM_IDS[realm]
        if _registry.get(platform_id) is not None:
            continue
        _registry.register(PlatformSpec(
            id=platform_id,
            label="{label}",
            realms=(),
            enabled=True,
            supports_local_scan=True,
            # 转发入口（dispatch 调用）；不需要转发就留空
            gateway="admin.platform.{pid}.gateway",
            # 管理契约（账号池 / 任务 / 探测 / 卡片）
            platform_gateway="admin.platform.{pid}.platform_gateway:{cls}Gateway",
            storage_platform=PLATFORM,
            realm=realm,
            icon="{pid}",
            notes="{label}",
            extra={{"task_runners": list(_task_runners().keys())}},
        ))
        logger.debug("{label} 平台已登记：%s", platform_id)


install()
'''


GATEWAY_PY = '''"""{label} /v1/* 转发实现（供 dispatch.py 调用）。

## 三个协议都要实现

    /v1/chat/completions   OpenAI
    /v1/messages           Anthropic
    /v1/responses          Responses
    /v1/models
    /v1/balance

## dispatch 必须是 async

`PlatformDispatchMiddleware` 的调用是 `await dispatch(request, path)` ——
同步函数返回 Response 会报
「object JSONResponse can't be used in 'await' expression」。
"""
from __future__ import annotations

import json
import logging

from starlette.responses import JSONResponse

from admin.platform.{pid}.core import client
from admin.platform.{pid}.services import accounts as {mod}_accounts

logger = logging.getLogger("admin.platform.{pid}.gateway")

#: 拦截路径（dispatch 据此判断要不要转交）
PATHS = (
    "/v1/chat/completions",
    "/v1/messages",
    "/v1/messages/count_tokens",
    "/v1/responses",
    "/v1/models",
    "/v1/balance",
)


async def _body_of(request) -> dict:
    """读 JSON body（Starlette 的 Request.json() 是协程）。"""
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return {{}}
    return data if isinstance(data, dict) else {{}}


def _realm_of(request) -> str:
    """本次请求的目标区域（从 X-Platform 头或 query 判断）。"""
    header = (request.headers.get("x-platform") or "").lower()
    if "intl" in header:
        return "intl"
    query = (request.query_params.get("realm") or "").lower()
    return "intl" if query in ("intl", "global") else "{default_realm}"


def _fail(exc: client.UpstreamError) -> JSONResponse:
    status = exc.status or 502
    if status < 400 or status > 599:
        status = 502
    return JSONResponse(
        {{"error": {{"type": exc.kind, "code": exc.code or "upstream_error",
                    "message": exc.message}}}},
        status_code=status)


async def chat_completions(request):
    """OpenAI 兼容入口。

    TODO(1/3): 把 body 转成上游格式并转发。
    若上游就是 OpenAI 兼容，直接透传即可。
    """
    body = await _body_of(request)
    realm = _realm_of(request)
    from admin.db import SessionLocal

    with SessionLocal() as db:
        account = {mod}_accounts.select(db, realm)
        if account is None:
            return JSONResponse(
                {{"error": {{"message": "没有可用账号", "type": "no_account"}}}},
                status_code=503)
        try:
            data = {mod}_accounts.ensure_token(db, account)
            result = client.chat_completions(
                {mod}_accounts.token_of(data), realm, body)
        except client.UpstreamError as exc:
            return _fail(exc)
    return JSONResponse(result)


async def messages(request):
    """Anthropic 入口。

    上游若是 OpenAI，需要 event 名映射：
        content_block_delta ← choices[].delta.content
        message_delta       ← finish_reason
    """
    body = await _body_of(request)
    # TODO(2/3): Anthropic 请求体 → 上游格式
    normalized = dict(body)
    system = body.get("system")
    if system:
        normalized["messages"] = ([{{"role": "system", "content": system}}]
                                  + list(body.get("messages") or []))
    return await chat_completions(request)


async def count_tokens(request):
    """本地估算（上游通常没有这个接口）。

    口径：CJK 1 token / 其它 4 字符 1 token。保守近似即可。
    """
    body = await _body_of(request)
    parts = []
    if isinstance(body.get("system"), str):
        parts.append(body["system"])
    for message in body.get("messages") or []:
        if isinstance(message, dict):
            content = message.get("content")
            parts.append(content if isinstance(content, str) else str(content or ""))
    text = "\\n".join(parts)
    cjk = sum(1 for ch in text if "\\u4e00" <= ch <= "\\u9fff")
    return JSONResponse({{"input_tokens": cjk + max(1, (len(text) - cjk) // 4)}})


async def responses(request):
    """Responses 入口（`input` 可能是字符串或消息数组）。"""
    body = await _body_of(request)
    normalized = dict(body)
    raw_input = body.get("input")
    if isinstance(raw_input, str):
        normalized["messages"] = [{{"role": "user", "content": raw_input}}]
    elif isinstance(raw_input, list):
        normalized["messages"] = raw_input
    normalized.setdefault("max_tokens", body.get("max_output_tokens"))
    return await chat_completions(request)


async def list_models(request):
    """/v1/models —— **只返回能调的模型**。"""
    realm = _realm_of(request)
    from admin.db import SessionLocal

    with SessionLocal() as db:
        account = {mod}_accounts.select(db, realm)
        if account is None:
            return JSONResponse(
                {{"error": {{"message": "没有可用账号", "type": "no_account"}}}},
                status_code=503)
        try:
            data = {mod}_accounts.ensure_token(db, account)
            models = client.list_models({mod}_accounts.token_of(data), realm)
        except client.UpstreamError as exc:
            return _fail(exc)
    return JSONResponse({{"object": "list", "data": models}})


async def balance(request):
    realm = _realm_of(request)
    from admin.db import SessionLocal

    with SessionLocal() as db:
        account = {mod}_accounts.select(db, realm)
        if account is None:
            return JSONResponse(
                {{"error": {{"message": "没有可用账号", "type": "no_account"}}}},
                status_code=503)
        try:
            data = {mod}_accounts.ensure_token(db, account)
            quota = client.fetch_quota({mod}_accounts.token_of(data), realm)
        except client.UpstreamError as exc:
            return _fail(exc)
    return JSONResponse({{"platform": "{pid}", "realm": realm, **quota}})


_HANDLERS = {{
    "/v1/chat/completions": chat_completions,
    "/v1/messages": messages,
    "/v1/messages/count_tokens": count_tokens,
    "/v1/responses": responses,
    "/v1/models": list_models,
    "/v1/balance": balance,
}}


async def dispatch(request, path: str):
    """`PlatformDispatchMiddleware` 的唯一入口（**必须 async**）。"""
    handler = _HANDLERS.get(path)
    if handler is None:
        return JSONResponse(
            {{"error": {{"message": "{label} 不支持 %s" % path,
                        "type": "invalid_request_error"}}}},
            status_code=404)
    return await handler(request)
'''


PLATFORM_GATEWAY_PY = '''"""{label} Gateway 契约实现。

## 三个必须照做的点（漏了就过不了 verify_platform.py）

1. `platform_id` **类属性**必须声明 —— 它是 Protocol 的一部分，
   缺了 `isinstance(gw, Gateway)` 会判 False，而错误信息不提示缺属性
2. 方法签名**与 Protocol 完全一致**（不能有默认值差异）
3. `install()` 幂等（重复调用要安静返回）
"""
from __future__ import annotations

import json
import logging

from admin.platform.gateway import AccountOps, ClientProbe, Gateway
from admin.platform.{pid}.core import client
from admin.platform.{pid}.services import accounts as {mod}_accounts
from admin.platform.{pid}.services import scan as {mod}_scan

logger = logging.getLogger("admin.platform.{pid}.platform_gateway")


class {cls}AccountOps:
    """账号池 11 项操作。"""

    def __init__(self, realm: str = ""):
        self.realm = realm

    def list(self, db):
        """账号列表。

        ⚠️ 必须**写回 DB 列**（`balance_remain/total`）——
        admin 的通用 `/api/accounts` 读的是列，不调本方法。
        只在这里实时拉、不写回，前端账号池会一直显示旧余额。
        """
        views = []
        for account in {mod}_accounts.accounts_for(db, self.realm):
            try:
                data = {mod}_accounts.ensure_token(db, account)
                quota = client.fetch_quota({mod}_accounts.token_of(data),
                                           {mod}_accounts.realm_of(account))
                {mod}_accounts.apply_quota(account, quota)
            except Exception:  # noqa: BLE001 - 单账号失败不影响列表
                quota = {{}}
            views.append({mod}_accounts.public_view(account, quota=quota))
        db.commit()
        return {{"items": views, "summary": {mod}_accounts.summarize(views)}}

    def scan(self):
        """扫描本机（**不含 token**）。"""
        return {mod}_scan.discover()

    def import_local(self, db):
        """本机导入。

        读不出 token 时必须**显式返回 ok=False + reason**，
        不能假装导入成功 —— 那会让用户以为号已加进来。
        """
        found = {mod}_scan.discover()
        return {{"ok": False, "imported": 0,
                "scanned": len(found.get("items") or []),
                "reason": "no_plain_credential",
                "hint": found.get("hint") or ""}}

    def import_record(self, db, record):
        return {mod}_accounts.import_record(db, record)

    def refresh(self, db, account_id, *, force=False):
        from admin.models import Account

        account = db.query(Account).filter(
            Account.id == int(account_id), Account.platform == "{pid}").first()
        if account is None:
            raise ValueError("账号不存在")
        return {mod}_accounts.sync_quota(db, account)

    def keepalive(self, db):
        ok, failed = 0, []
        for account in {mod}_accounts.accounts_for(db, self.realm):
            try:
                {mod}_accounts.ensure_token(db, account)
                ok += 1
            except client.UpstreamError as exc:
                failed.append({{"account_id": account.id, "error": exc.message[:120]}})
        return {{"ok": not failed, "refreshed": ok, "failed": failed}}

    def checkin(self, db):
        """签到。**不支持就明确返回 supported=False。**"""
        return {{"supported": False,
                "summary": "{label} 暂无签到接口"}}

    def credits(self, db):
        data = self.list(db)
        return {{"items": data["items"], "summary": data["summary"]}}

    def usage(self, db, **filters):
        """用量。

        上游没有明细接口时，用本网关账本兜底（永远可查）。
        """
        days = int(filters.get("days") or 30)
        return {{"realm": self.realm, "days": days,
                "source": "local_only",
                "local": _local_ledger(db, self.realm, days),
                "note": "上游未提供用量明细，以下为本网关记录"}}

    def health(self, db, account_id):
        from admin.models import Account

        account = db.query(Account).filter(
            Account.id == int(account_id), Account.platform == "{pid}").first()
        if account is None:
            raise ValueError("账号不存在")
        stages = {{}}
        data = {mod}_accounts.credentials_of(account)
        token = {mod}_accounts.token_of(data)
        stages["token"] = {{"ok": bool(token), "detail": "len=%d" % len(token)}}
        try:
            {mod}_accounts.ensure_token(db, account)
            stages["refresh"] = {{"ok": True, "detail": "ok"}}
        except client.UpstreamError as exc:
            stages["refresh"] = {{"ok": False, "detail": exc.message[:100]}}
        return {{"account_id": account.id, "account": account.name,
                "ok": all(s["ok"] for s in stages.values()), "stages": stages}}

    def inject(self, db, account_id):
        return {{"supported": False, "summary": "{label} 不做客户端注入"}}

    def export(self, db, ids=None):
        rows = {mod}_accounts.accounts_for(db, self.realm)
        if ids:
            wanted = {{int(i) for i in ids}}
            rows = [r for r in rows if r.id in wanted]
        return {{"format": "backup", "platform": "{pid}",
                "accounts": [{{
                    "id": a.id, "name": a.name, "uid": a.uid,
                    "realm": {mod}_accounts.realm_of(a),
                    "token": {mod}_accounts.token_of({mod}_accounts.credentials_of(a)),
                }} for a in rows]}}


class {cls}ClientProbe:
    """本机探测。可改项很少时如实返回空列表。"""

    def __init__(self, realm: str = ""):
        self.realm = realm

    def dirs(self) -> list[str]:
        return list({mod}_scan.discover().get("data_dirs") or [])

    def probe(self) -> dict:
        return {mod}_scan.discover()

    def schema(self) -> list[dict]:
        return []

    def read(self, key: str):
        return None

    def write(self, key: str, value) -> dict:
        return {{"supported": False, "summary": "{label} 客户端参数只读"}}


class {cls}Gateway:
    """网关主体。"""

    #: ★ Protocol 要求 —— 缺了 isinstance 判 False
    platform_id = "{pid}"

    def __init__(self, realm: str = ""):
        self.realm = realm or "{default_realm}"

    def chat_completions(self, request, body):
        from admin.platform.{pid} import gateway as impl

        return impl.chat_completions(request)

    def responses(self, request, body):
        from admin.platform.{pid} import gateway as impl

        return impl.responses(request)

    def messages(self, request, body):
        from admin.platform.{pid} import gateway as impl

        return impl.messages(request)

    def count_tokens(self, request, body):
        from admin.platform.{pid} import gateway as impl

        return impl.count_tokens(request)

    def list_models(self, request):
        from admin.platform.{pid} import gateway as impl

        return impl.list_models(request)

    def balance(self, request):
        from admin.platform.{pid} import gateway as impl

        return impl.balance(request)

    def account_ops(self) -> AccountOps:
        return {cls}AccountOps(self.realm)

    def task_runners(self) -> dict:
        # TODO(3/3): 有签到/刷新任务就登记到这里
        return {{}}

    def client_probe(self) -> ClientProbe:
        return {cls}ClientProbe(self.realm)

    def overview_cards(self, db) -> list[dict]:
        rows = {mod}_accounts.accounts_for(db, self.realm)
        total = len(rows)
        active = sum(1 for r in rows
                     if (getattr(r, "status", "") or "") == "active")
        remain = sum(int(getattr(r, "balance_remain", 0) or 0) for r in rows)
        limit = sum(int(getattr(r, "balance_total", 0) or 0) for r in rows)
        return [
            {{"key": "accounts", "label": "账号总数", "value": total,
             "sub": "启用 %d" % active, "icon": "fa-users", "tone": ""}},
            {{"key": "credits", "label": "可用额度", "value": remain,
             "sub": "总额度 %d" % limit, "icon": "fa-wallet", "tone": "brand"}},
            {{"key": "mode", "label": "接入方式", "value": "反向代理",
             "sub": "{label}", "icon": "fa-network-wired", "tone": ""}},
        ]


def _local_ledger(db, realm: str, days: int) -> dict:
    """本网关账本（直接查 usage_logs，不依赖路由函数签名）。"""
    from datetime import datetime, timedelta

    from sqlalchemy import func

    from admin.models import Account, UsageLog

    since = datetime.utcnow() - timedelta(days=max(1, int(days)))
    query = db.query(
        UsageLog.model.label("model"),
        func.count(UsageLog.id).label("requests"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0).label("total"),
    ).filter(UsageLog.platform == "{pid}", UsageLog.created_at >= since)
    if realm:
        ids = [row[0] for row in db.query(Account.id).filter(
            Account.platform == "{pid}", Account.domain == realm).all()]
        if not ids:
            return {{"days": days, "items": [], "totals": {{}}}}
        query = query.filter(UsageLog.account_id.in_(ids))
    items = [{{"model": r.model or "-", "requests": int(r.requests or 0),
              "total_tokens": int(r.total or 0)}}
             for r in query.group_by(UsageLog.model).all()]
    return {{"days": days, "items": items,
            "totals": {{"requests": sum(i["requests"] for i in items),
                       "total_tokens": sum(i["total_tokens"] for i in items)}}}}


#: 无固定区域的实例
gateway = {cls}Gateway("")
'''


ENDPOINTS_PY = '''"""{label} 端点表。

按 skill 的 recon 结果填这里。**契约：一张表说清每个接口的 host + 鉴权**。
"""
from __future__ import annotations

PLATFORM = "{pid}"

#: 区域配置（单区就一个）
REGIONS = {{
    "{default_realm}": {{
        "label": "{label}",
        "base_url": "{base_url}",
    }},
}}

#: 落库区域 -> 对外平台 id
REALM_IDS = {{{realm_ids}}}

# ---- openapi 一族 ----
USER_INFO_PATH = "/user/info"        # TODO: 按实际接口改
QUOTA_PATH = "/quota"                # TODO
MODELS_PATH = "/models"              # TODO
CHAT_PATH = "/chat/completions"      # TODO

# ---- 鉴权 ----
#: 需要哪些头（按 recon / 源码结论填）
def auth_headers(token: str) -> dict:
    """鉴权头。

    ⚠️ 有些平台要**双头**（`Authorization` + 自定义 `token`）——
    只发一个会 401，且错误信息不一定提示少了哪个。
    """
    return {{
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
    }}


def base_of(realm: str) -> str:
    conf = REGIONS.get(realm or "{default_realm}") or REGIONS["{default_realm}"]
    return str(conf["base_url"]).rstrip("/")
'''


CLIENT_PY = '''"""{label} HTTP 客户端。

## 三件事必须做对

1. 错误分类（`kind` 决定上层是冷却、禁用还是换号）
2. 网络异常上报哨兵（连续失败 = 平台不可用）
3. 不要静默吞异常（否则「查不到」和「没有」分不清）
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from . import endpoints

logger = logging.getLogger("admin.platform.{pid}.client")
DEFAULT_TIMEOUT = 30.0

#: 哪些 kind 要上报哨兵（区分「账号问题」与「实现问题」）
SENTINEL_KINDS = frozenset({{"auth", "quota_exceeded", "network",
                             "signature_invalid", "upstream_business"}})


class UpstreamError(Exception):
    def __init__(self, message: str, *, status: int = 0, code: str = "",
                 kind: str = "http_error", detail: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.kind = kind
        self.detail = detail

    def retryable(self) -> bool:
        return self.status in (408, 429) or self.status >= 500

    def to_dict(self) -> dict:
        return {{"message": self.message, "status": self.status,
                "code": self.code, "kind": self.kind, "detail": self.detail[:300]}}


def note_sentinel(realm: str, account_id: int, kind: str, *,
                  code: str = "", status: int = 0, detail: str = "") -> None:
    """上报风控哨兵。**绝不抛异常**（哨兵坏了不能阻断转发）。"""
    if kind not in SENTINEL_KINDS:
        return
    try:
        from admin import sentinel

        sentinel.note_failure("{pid}", int(account_id or 0), kind,
                              code=str(code or ""), status=int(status or 0),
                              detail=str(detail or "")[:200])
    except Exception:  # noqa: BLE001
        logger.debug("哨兵上报失败 kind=%s", kind, exc_info=True)


def _request(url: str, *, method: str = "GET", body: dict | None = None,
             headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT,
             account_id: int = 0, realm: str = "") -> dict:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method=method.upper())
    for key, value in (headers or {{}}).items():
        req.add_header(str(key), str(value))
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
            return json.loads(text) if text.strip() else {{}}
    except urllib.error.HTTPError as exc:
        payload = ""
        try:
            payload = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        kind = ("auth" if exc.code in (401, 403)
                else "rate_limit" if exc.code == 429
                else "upstream_error" if exc.code >= 500 else "http_error")
        note_sentinel(realm, account_id, kind, code=str(exc.code),
                      status=exc.code, detail=payload)
        raise UpstreamError("%s失败（HTTP %d）" % (method, exc.code),
                            status=exc.code, code=str(exc.code), kind=kind,
                            detail=payload) from exc
    except Exception as exc:  # noqa: BLE001
        note_sentinel(realm, account_id, "network", detail=str(exc)[:200])
        raise UpstreamError("网络失败", status=0, kind="network",
                            detail=str(exc)[:200]) from exc


# ---------------------------------------------------------------------------
# 具体接口（TODO: 按实际响应结构调整）
# ---------------------------------------------------------------------------
def fetch_user(token: str, realm: str) -> dict:
    return _request(endpoints.base_of(realm) + endpoints.USER_INFO_PATH,
                    headers=endpoints.auth_headers(token), realm=realm)


def fetch_quota(token: str, realm: str) -> dict:
    """额度。

    TODO: 若上游有多个额度桶（套餐 / 资源包），两个都要返回。
    """
    return _request(endpoints.base_of(realm) + endpoints.QUOTA_PATH,
                    headers=endpoints.auth_headers(token), realm=realm)


def list_models(token: str, realm: str) -> list[dict]:
    data = _request(endpoints.base_of(realm) + endpoints.MODELS_PATH,
                    headers=endpoints.auth_headers(token), realm=realm)
    items = data.get("data") or data.get("models") or data.get("items") or []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append({{
            "id": str(item.get("id") or item.get("key") or ""),
            "object": "model",
            "created": 0,
            "owned_by": "{pid}",
            "name": str(item.get("name") or item.get("display_name") or ""),
        }})
    return out


def chat_completions(token: str, realm: str, body: dict) -> dict:
    """对话（非流式）。TODO: 流式需要单独实现 SSE 转发。"""
    return _request(endpoints.base_of(realm) + endpoints.CHAT_PATH,
                    method="POST", body=body,
                    headers=endpoints.auth_headers(token), realm=realm)


def refresh_token(refresh: str, realm: str) -> dict:
    """续期。**平台没有就 raise**，让上层走重新登录。"""
    raise UpstreamError("该平台没有 refresh 接口，需要重新登录",
                        status=401, kind="auth")
'''


ACCOUNTS_PY = '''"""{label} 凭证与账号池。"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from sqlalchemy.orm import Session

from admin.models import Account
from admin.platform.{pid}.core import client, endpoints

logger = logging.getLogger("admin.platform.{pid}.accounts")
PLATFORM = endpoints.PLATFORM

#: 额度快照新鲜窗口（秒）。TTL 内用缓存，避免每次读都打上游。
QUOTA_TTL = 60


def credentials_of(account: Account) -> dict:
    try:
        data = json.loads(account.auth_json or "{{}}")
    except ValueError:
        data = {{}}
    return data if isinstance(data, dict) else {{}}


def token_of(data: dict) -> str:
    return str(data.get("token") or data.get("session") or "")


def realm_of(account: Account) -> str:
    return str(getattr(account, "domain", "") or "{default_realm}")


def quota_is_fresh(account: Account) -> bool:
    synced = getattr(account, "credits_synced_at", None)
    if synced is None:
        return False
    return (datetime.utcnow() - synced).total_seconds() < QUOTA_TTL


def ensure_token(db: Session, account: Account) -> dict:
    """保证 token 可用。**没有 refresh 且已过期就 raise。**"""
    data = credentials_of(account)
    token = token_of(data)
    expires = int(data.get("expires_at") or 0)
    if token and (not expires or expires - int(time.time()) > 300):
        return data

    refresh = str(data.get("refresh_token") or "")
    if not refresh:
        raise client.UpstreamError("凭证已过期且无 refresh_token，需重新登录",
                                   status=401, kind="auth")
    fresh = client.refresh_token(refresh, realm_of(account))
    data["token"] = str(fresh.get("token") or "")
    data["refresh_token"] = str(fresh.get("refresh_token") or refresh)
    data["expires_at"] = int(fresh.get("expires_at") or 0)
    account.auth_json = json.dumps(data, ensure_ascii=False)
    db.commit()
    return data


def accounts_for(db: Session, realm: str = "") -> list[Account]:
    query = db.query(Account).filter(Account.platform == PLATFORM)
    if realm:
        query = query.filter(Account.domain == realm)
    return query.order_by(Account.id.asc()).all()


def select(db: Session, realm: str = "", mark_picked: bool = False) -> Account | None:
    now = datetime.utcnow()
    for account in accounts_for(db, realm):
        if (getattr(account, "status", "") or "") != "active":
            continue
        cool = getattr(account, "cool_until", None)
        if cool and cool > now:
            continue
        if mark_picked:
            account.last_used_at = now
            db.commit()
        return account
    return None


def apply_quota(account: Account, quota: dict) -> None:
    """把上游额度写回统一列。

    ⚠️ 必须写回 —— admin 的 `/api/accounts` 读的是列，
    只返回不写回会导致前端账号池显示旧余额。

    TODO: 多桶（套餐 / 资源包）请合并到 remain/total，明细存 snapshot。
    """
    remain = float(quota.get("remaining") or quota.get("remain") or 0)
    total = float(quota.get("total") or 0)
    account.balance_remain = int(round(remain))
    account.balance_total = int(round(total))
    account.credits_synced_at = datetime.utcnow()
    account.last_sync_at = datetime.utcnow()
    account.credits_snapshot = json.dumps(quota, ensure_ascii=False)[:20000]


def sync_quota(db: Session, account: Account) -> dict:
    data = ensure_token(db, account)
    realm = realm_of(account)
    try:
        quota = client.fetch_quota(token_of(data), realm)
    except client.UpstreamError as exc:
        return {{"ok": False, "error": exc.to_dict()}}
    apply_quota(account, quota)
    db.commit()
    return {{"ok": True, "quota": quota}}


def public_view(account: Account, *, quota: dict | None = None) -> dict:
    """给前端的视图（**不含 token**）。"""
    data = credentials_of(account)
    return {{
        "id": account.id,
        "name": account.name,
        "uid": account.uid,
        "realm": realm_of(account),
        "status": getattr(account, "status", "") or "",
        "balance_remain": int(getattr(account, "balance_remain", 0) or 0),
        "balance_total": int(getattr(account, "balance_total", 0) or 0),
        "quota_live": bool(quota),
        "token_type": token_of(data)[:3],
        "has_refresh": bool(data.get("refresh_token")),
        "last_sync_at": account.last_sync_at.isoformat()
        if account.last_sync_at else None,
    }}


def summarize(rows: list[dict]) -> dict:
    total = len(rows)
    active = sum(1 for r in rows if r.get("status") == "active")
    return {{"total": total, "active": active, "disabled": total - active,
            "balance_remain": sum(int(r.get("balance_remain") or 0) for r in rows),
            "balance_total": sum(int(r.get("balance_total") or 0) for r in rows)}}


def import_record(db: Session, record: dict) -> dict:
    """导入一条凭证（备份恢复 / 手工粘贴）。"""
    if not isinstance(record, dict):
        raise ValueError("凭证必须是对象")
    token = token_of(record)
    if not token:
        raise ValueError("凭证缺少 token")
    realm = str(record.get("realm") or "{default_realm}")
    uid = str(record.get("uid") or record.get("userid") or "")
    existing = None
    if uid:
        existing = db.query(Account).filter(Account.platform == PLATFORM,
                                            Account.uid == uid).first()
    if existing is None:
        existing = Account(platform=PLATFORM, name=str(record.get("name") or uid or "{label}"),
                           uid=uid, status="active")
        db.add(existing)
    existing.uid = uid or existing.uid
    existing.domain = realm
    existing.auth_json = json.dumps({{
        "token": token,
        "refresh_token": str(record.get("refresh_token") or ""),
        "expires_at": int(record.get("expires_at") or 0),
        "uid": uid,
        "realm": realm,
    }}, ensure_ascii=False)
    existing.status = "active"
    existing.last_sync_at = datetime.utcnow()
    db.commit()
    db.refresh(existing)
    return {{"ok": True, "account_id": existing.id, "uid": uid}}
'''


SCAN_PY = '''"""{label} 本机扫描。

## 找凭证的正确顺序（按可行性）

1. **读源码找存储名**：`new Store({{ name: 'auth-session' }})` → `<userData>/auth-session.json`
2. **找自定义 userDataPath**：很多产品不用 `%APPDATA%/<产品名>`
3. 找到明文 JSON → 直接读
4. 加密（DPAPI/v10）→ 记下位置，提示用户需要重新登录
5. 都没有 → 走设备流授权

## 实测教训

* 产品名 ≠ 数据目录名（Qoder：`com.qodercn.app.stable`）
* 自定义路径猜不到（Loomy：`C:/Users/Public/Loomy/<sha256(用户名)[:12]>`）
* 业务文件可能只有几百字节
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import sys

logger = logging.getLogger("admin.platform.{pid}.scan")
PLATFORM = "{pid}"

#: 候选数据目录名（**按 recon 结论改**）
DATA_DIR_CANDIDATES = (
    "{pid}",
    "{label}",
)

#: electron-store 的凭证文件名（源码里的 `name`）
CRED_FILE = "auth-session.json"


def mask(value: str, keep: int = 4) -> str:
    text = str(value or "")
    if len(text) <= keep * 2:
        return text[:keep] + "***" if text else ""
    return text[:keep] + "*" * min(8, len(text) - keep * 2) + text[-keep:]


def _appdata() -> pathlib.Path:
    return pathlib.Path(os.environ.get("APPDATA")
                        or pathlib.Path.home() / "AppData/Roaming")


def user_data_dirs() -> list[pathlib.Path]:
    """候选数据目录。

    TODO: 按 recon 结论补上自定义路径（例如
    `C:/Users/Public/<Product>/<sha256(username)[:12]>/userData`）。
    """
    out = []
    for name in DATA_DIR_CANDIDATES:
        path = _appdata() / name
        if path.is_dir():
            out.append(path)
    return out


def read_credentials(data_dir: pathlib.Path) -> dict | None:
    """读明文凭证文件。

    返回 {{session, userid, phone}} 或 None。
    **加密就返回 None** —— 不要尝试解密后再失败，那会浪费几分钟。
    """
    path = data_dir / CRED_FILE
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()[:4096]
    except OSError:
        return None
    if raw[:3] == b"v10" or raw[:1] not in (b"{{", b"["):
        logger.info("%s 存在但不是明文 JSON（可能是加密）", path.name)
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if not (data.get("session") or data.get("token")):
        return None
    return data


def discover() -> dict:
    """扫描本机（**结果不含完整 token**）。"""
    dirs = user_data_dirs()
    items: list[dict] = []
    hint = ""

    for data_dir in dirs:
        creds = read_credentials(data_dir)
        if creds:
            items.append({{
                "source": str(data_dir / CRED_FILE),
                "session": mask(str(creds.get("session") or creds.get("token") or "")),
                "userid": mask(str(creds.get("userid") or "")),
                "phone": mask(str(creds.get("phone") or "")),
                "has_plain_credential": True,
            }})
        else:
            items.append({{
                "source": str(data_dir),
                "has_plain_credential": False,
            }})

    if not items:
        hint = ("没找到 {label} 的数据目录。"
                "请先运行 skill 的 creds.py 定位真实路径"
                "（产品名常与目录名不同）。")
    elif not any(i.get("has_plain_credential") for i in items):
        hint = ("找到数据目录但没有可读凭证（可能加密）。"
                "请在客户端登录一次后重试；或走设备流授权。")

    return {{
        "platform": PLATFORM,
        "data_dirs": [str(d) for d in dirs],
        "items": items,
        "can_import": any(i.get("has_plain_credential") for i in items),
        "hint": hint,
        "errors": [],
    }}
'''


ROUTER_PY = '''"""{label} 管理接口：`/api/{pid}/*`。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.platform.{pid}.services import accounts as {mod}_accounts
from admin.platform.{pid}.services import scan as {mod}_scan
from admin.security import require_admin

logger = logging.getLogger("admin.platform.{pid}.routers")
router = APIRouter(prefix="/api/{pid}", tags=["{pid}"])


@router.get("/meta")
def meta(_: bool = Depends(require_admin)):
    from admin.platform.{pid}.core import endpoints

    return {{"platform": "{pid}",
            "regions": [{{"key": k, "label": v["label"],
                         "base_url": v["base_url"]}}
                        for k, v in endpoints.REGIONS.items()]}}


@router.get("/scan-local")
def scan_local(_: bool = Depends(require_admin)):
    return {mod}_scan.discover()


class ImportIn(BaseModel):
    credential: dict | None = None


@router.post("/import")
def import_account(body: ImportIn, _: bool = Depends(require_admin),
                   db: Session = Depends(get_db)):
    if not body.credential:
        raise HTTPException(400, detail="需要 credential")
    try:
        return {mod}_accounts.import_record(db, body.credential)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.get("/accounts")
def list_accounts(realm: str = "", _: bool = Depends(require_admin),
                  db: Session = Depends(get_db)):
    from admin.platform.{pid}.platform_gateway import {cls}AccountOps

    return {cls}AccountOps(realm).list(db)


@router.post("/accounts/{{acc_id}}/refresh")
def refresh(acc_id: int, _: bool = Depends(require_admin),
            db: Session = Depends(get_db)):
    from admin.platform.{pid}.platform_gateway import {cls}AccountOps

    try:
        return {cls}AccountOps("").refresh(db, acc_id, force=True)
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
'''


def write(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print("  写入:", path.relative_to(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description="生成平台模块骨架")
    ap.add_argument("--platform", required=True, help="平台 id（小写，如 myapp）")
    ap.add_argument("--label", required=True, help="展示名（如 MyApp）")
    ap.add_argument("--realm", default="", help="固定区域（多区留空）")
    ap.add_argument("--realms", default="", help="多区逗号分隔（如 cn,intl）")
    ap.add_argument("--base-url", default="https://api.example.com/v1")
    args = ap.parse_args()

    pid = args.platform.lower().strip()
    if not pid.isidentifier():
        print("平台 id 必须是合法标识符（小写字母/数字/下划线）")
        return 1

    realms = [r.strip() for r in args.realms.split(",") if r.strip()] or [args.realm or ""]
    # 单元素也要尾逗号 —— 否则 `("cn")` 是括号表达式而不是元组，
    # `for realm in REALMS` 会逐字符迭代（实测 KeyError: 'c'）
    realms_src = "(" + "".join('"%s", ' % r for r in realms) + ")"
    realm_ids = ", ".join('"%s": "%s"' % (r, pid if len(realms) == 1 else
                                          (pid if not r else pid + "-" + r))
                          for r in realms)
    default_realm = realms[0]
    cls = "".join(part.capitalize() for part in pid.split("_"))
    mod = pid

    target = PLATFORM_ROOT / pid
    if target.exists():
        print("目录已存在:", target)
        print("（先删掉或换一个 --platform）")
        return 1

    print("=== 生成", target, "===")
    fmt = dict(pid=pid, label=args.label, cls=cls, mod=mod,
               realm_ids=realm_ids, default_realm=default_realm,
               base_url=args.base_url,
               realms_src=realms_src)

    write(target / "__init__.py", INIT_PY.format(**fmt))
    write(target / "gateway.py", GATEWAY_PY.format(**fmt))
    write(target / "platform_gateway.py", PLATFORM_GATEWAY_PY.format(**fmt))
    write(target / "core" / "__init__.py", '"""协议层。"""\n')
    write(target / "core" / "endpoints.py", ENDPOINTS_PY.format(**fmt))
    write(target / "core" / "client.py", CLIENT_PY.format(**fmt))
    write(target / "services" / "__init__.py", '"""状态层。"""\n')
    write(target / "services" / "accounts.py", ACCOUNTS_PY.format(**fmt))
    write(target / "services" / "scan.py", SCAN_PY.format(**fmt))
    write(target / "routers" / "__init__.py",
          '"""管理接口。"""\nfrom admin.platform.%s.routers import accounts  # noqa: F401\n' % pid)
    write(target / "routers" / "accounts.py", ROUTER_PY.format(**fmt))

    print("""
=== 接下来（3 个 TODO + 3 条命令）===

TODO 1  core/endpoints.py   填真实路径与鉴权头（按 recon 结论）
TODO 2  core/client.py      按上游响应结构调整 fetch_quota / list_models
TODO 3  platform_gateway.py 有签到/任务时登记到 task_runners()

然后：

  1. 在 admin/platform/loader.py 的 BUILTIN 加一行
       "admin.platform.%s",

  2. 结构检查（不需要凭证，应全绿）
       python admin/platform/skill/scripts/verify_platform.py --platform %s

  3. 运行检查（服务在跑）
       python admin/platform/skill/scripts/verify_platform.py \\
           --platform %s --base http://127.0.0.1:8790 --browser
""" % (pid, pid, pid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
