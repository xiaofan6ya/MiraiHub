# PERSISTENT REGRESSION TESTS — keep this file in the repo.
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""平台作用域模型解析：WorkBuddy 入口收包回归（计划 2026-11-22 任务 1，先红）。

## 这份文件锁的是什么

截图里的故障是「客户端从 `/v1/models` 复制了一个 ID，网关却把另一个名字发给了
上游」。本文件把它固定成**入口回归**，全部走真实代码路径：

    GET  /v1/models            真实的 PlatformDispatchMiddleware / models_router
                               （build_payload + to_openai_strict 严格形状）
    POST /v1/chat/completions  真实的 PlatformDispatchMiddleware：
                               _should_peek -> _read_body -> _replay_receive
                               -> model_suffix.resolve -> 真实选路 -> 目标平台 body

WorkBuddy 是默认平台（`registry.DEFAULT_PLATFORM`），它的「主应用」在这里用
本地接收器替代：接收器内 `await Request(scope, receive).json()` 读**最终** model，
同时记录 `scope["_wb_body"]`（本次转发缓存的 body）、response start 的落点头
（`x-gateway-platform` / `x-gateway-model`）以及传进来的 `messages`。

三条入口（计划 §三）：
    a) 裸公开名 + `X-Platform: workbuddy` 头；
    b) 只带 `-workbuddy` 后缀（`deepseek-v4.1-flash-workbuddy`）；
    c) 绑定 workbuddy 的测试 Key（`api_keys.platform`）。

目录里同时存在 Marvis 的槽位行 `main-auto`（可读名同样是
`deepseek-v4.1-flash`）与 WorkBuddy 的原生行 `deepseek-v4.1-flash`，
并**参数化两种插入顺序**：SQL 的自然返回顺序不构成约定（计划 §一 第 3 行）。

## 断言（计划 §三 §44-48）

    assert published_id == "deepseek-v4.1-flash"
    assert observed["platform"] == "workbuddy"
    assert observed["received"]["model"] == "deepseek-v4.1-flash"
    assert observed["cached"]["model"] == observed["received"]["model"]
    assert observed["received"]["messages"] == original["messages"]

最后两条用**顺序确定的行迭代器**（不依赖数据库执行计划）再验一次同样的入口
不变式，避免某些库的执行计划恰好掩盖顺序依赖。

## §八 验收矩阵覆盖（本轮：行 1/2/3/5/6/7）

    行1 WorkBuddy 四条入口 × 两种插入顺序  A 段 + 前提用例：入口 model / cached==received /
                                           messages 原样 / 非 model 字段原样 / 回显头不泄漏内部名
    行2 Marvis 槽位版 + 可读名版目录        D 段：两组目录 × 三条入口，最终 _validate_model -> main-auto
    行3 Qoder / Loomy / Trae                E 段：dfmodel / dmodel / GLM-5.3-Flash（逐字节）/ trae-intl
    行5 错误分类                            F 段：11102 vs 无余额 vs 模型未知；冷却只落该账号×该模型
    行6 管理员与客户端视角                  G 段：group_models 字符串 key、聚合 ID + 旧别名、日志字段
    行7 协议与可回放性                      H 段：chat/responses/messages、流式、空体、chunked、
                                           413、预读期断开、/v1/models strict+detail、落点头
    行4 聚合仅在合格池内重试                I 段：A 503 -> B 200；显式目标（后缀/头/查询参数）
                                           只调 A；禁用 / 分组外 / 平台内部异常 / 流式断开
                                           都不重放；4 个声明者只尝试 3 个（按截断后的列表
                                           判「最后一次」）。
    分组 Key 的 HTTP 往返                   J 段：清单 -> 复制公开 ID -> POST 落到 qoder 且上游
                                           收到 dfmodel；组外模型 403 model_not_in_group
    行82 多成员组 / :intl 孪生扩权            K 段：组内两平台只发一条公开 ID；后缀各自命中本
                                           平台拼写；**组外后缀 / 组外区必须 403**（当前两条
                                           红，见文件末尾 F1/F2：分组授权没走「显式目标」路径）

平台侧一律用 `_RecordingGateway` 假网关（记录 platform/path/model/body），
所以断言直接落在「平台实际收到什么」上，而不是「我们以为它会收到什么」。

## 当前已知红（2026-11-22 工作树：dispatch 的任务2 修复已落地）

  * test_request_log_carries_no_payload_or_credentials —— dispatch 的
    `dispatch 收到 | ... body_head=%r` 把请求体头部写进 INFO 日志（含 messages 正文），
    属 §八 行6「勿输出请求正文」，待任务 6 收口。
  * test_request_log_identifies_names_platform_and_realm —— INFO 级只有
    `dispatch 分叉 | raw_model=... -> platform=...`（没有区）；「平台 + 区」只出现在
    DEBUG 级 `模型名按平台还原 ... (qoder/cn)`，生产级别下读不到，同属 §八 行6。

## 隔离

  * `ADMIN_DATABASE_URL` 在 **import admin.db 之前**指到本轮专属临时 SQLite 文件；
    结束时 dispose 引擎、清理临时目录（Windows 上先 dispose 再删）。
  * 不碰公网 / 真实 Key / 真实账号 / Redis：账号与 Key 都是临时库里的测试行，
    指标与实时流量模块用桩替换（dispatch 内部对它们本来就容错）。
  * `QIMEI_VALIDATE_ON_START=0`：跳过 Marvis 启动期的 qimei 池文件校验，
    测试不读写部署设备池（见 admin/platform/marvis/__init__.py）。

## 先红时的实际值（2026-11-22，分支 pr/qimei-guard-rotation 工作树）

插入顺序决定故障是否外显（计划 §一 第 3 行）：
  * marvis_first    三条入口的接收器都读到 main-auto（模型错发）；
  * workbuddy_first 裸名 / 绑定 Key 读到正确的 deepseek-v4.1-flash；后缀入口
                    模型虽然对了，但 scope["_wb_body"] 仍带后缀原名（缓存没跟着改）。

## 入库注意

仓库 .gitignore 默认忽略 tests/*，需要一行白名单才不会被漏掉：
!tests/test_platform_model_routing.py（本文件不改 .gitignore）。

运行：
  python -B -m pytest -p no:cacheprovider -q tests/test_platform_model_routing.py
"""
from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from sqlalchemy import text
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

# ---------------------------------------------------------------------------
# 1) 本轮专属临时 SQLite —— 必须在 import admin.db **之前**生效
# ---------------------------------------------------------------------------
_TMP_DIR = Path(tempfile.mkdtemp(prefix="platform_model_routing_"))
_DB_FILE = _TMP_DIR / "routing.db"
# 直接赋值（不是 setdefault）：旧脚本的 setdefault 会继承外部配置，跑到生产库上。
os.environ["ADMIN_DATABASE_URL"] = "sqlite:///" + _DB_FILE.as_posix()
# Marvis 的 install() 会校验 qimei 池文件；测试不读写部署设备池。
os.environ["QIMEI_VALIDATE_ON_START"] = "0"

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from admin import db as admin_db  # noqa: E402
from admin.models import (  # noqa: E402
    Account,
    AccountModelCooldown,
    ApiKey,
    ModelConfig,
    ModelGroup,
    ModelGroupItem,
    SystemSetting,
    UsageLog,
)
from admin.platform import dispatch, gateway_router  # noqa: E402
from admin.platform import model_suffix  # noqa: E402
from admin.platform import models_router  # noqa: E402
from admin.platform import registry  # noqa: E402
from admin.platform.marvis import gateway as marvis_gateway  # noqa: E402
from admin.model_alias import group_models  # noqa: E402
from admin.routers import proxy as proxy_router  # noqa: E402
from admin.security import hash_key  # noqa: E402

#: 对外公开 ID（两个平台的可读名收敛成同一个）。
PUBLIC_ID = "deepseek-v4.1-flash"
#: Marvis 的落库槽位名（上游只认它，网关自己换算）。
MARVIS_SLOT = "main-auto"
WB_PLATFORM = "workbuddy"
MARVIS_PLATFORM = "marvis"

#: 测试 Key：一个不绑平台（开聚合），一个绑定 workbuddy。
AGG_KEY = "sk-regression-aggregate-0001"
BOUND_KEY = "sk-regression-workbuddy-0001"

#: 请求里的 messages —— 必须原样到达接收端。
MESSAGES = [
    {"role": "system", "content": "platform-scoped model resolution regression"},
    {"role": "user", "content": "ping 中文 \u2705"},
]

#: 两种插入顺序（SQL 的自然返回顺序不是契约）。
ORDERS = (
    ("marvis_first", (MARVIS_PLATFORM, WB_PLATFORM)),
    ("workbuddy_first", (WB_PLATFORM, MARVIS_PLATFORM)),
)
ORDER_IDS = [name for name, _storages in ORDERS]

_SCHEMA_READY = {"done": False}


def _close_orphan_sessions() -> None:
    """关掉没被上下文管理器收尾的 Session。

    dispatch 里存在 `_model_id_for(_db(), ...)` 这种**没有 with** 的取用
    （计划 §四 要求改成上下文管理器）。它会留下一个带连接的对象，
    `engine.dispose()` 管不到 —— Windows 上 sqlite 文件因此删不掉。
    这里按对象类型收尾，让临时目录能真正清理干净。
    """
    import gc

    from sqlalchemy.orm import Session as _OrmSession

    gc.collect()
    for obj in gc.get_objects():
        if isinstance(obj, _OrmSession):
            with contextlib.suppress(Exception):
                obj.close()


def teardown_module():  # noqa: N802 - pytest 的模块级清理
    """关闭会话、释放引擎后删临时目录（Windows 上 sqlite 文件被持有时删不掉）。"""
    _close_orphan_sessions()
    with contextlib.suppress(Exception):
        admin_db.engine.dispose()
    # 连接池 drain 之后 OS 侧句柄还要一会儿才真正释放：失败就重试，别留垃圾目录。
    for _attempt in range(10):
        shutil.rmtree(_TMP_DIR, ignore_errors=True)
        if not _TMP_DIR.exists():
            return
        time.sleep(0.1)
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


# unittest 风格 runner（python -B tests/xxx.py）也认这个名字。
tearDownModule = teardown_module
# `--collect-only` 只 import 不跑用例，模块级 teardown 不会触发：
# 兜底注册一次，保证任何模式下都不留临时目录。
atexit.register(teardown_module)


# ---------------------------------------------------------------------------
# 临时库：建表 / 清表 / 种子
# ---------------------------------------------------------------------------
def _ensure_schema() -> None:
    if _SCHEMA_READY["done"]:
        return
    admin_db.Base.metadata.create_all(bind=admin_db.engine)
    _SCHEMA_READY["done"] = True


def _clear_tables() -> None:
    with admin_db.engine.begin() as conn:
        for table in ("api_keys", "accounts", "model_configs", "usage_logs",
                      "account_model_cool", "system_settings",
                      "model_group_items", "model_groups"):
            conn.execute(text('DELETE FROM "%s"' % table))


def _reset_caches() -> None:
    """清空目录缓存、原生名缓存、调度缓存 —— 避免上一轮污染下一轮。"""
    models_router.invalidate()
    dispatch.invalidate_model_cache()
    models_router._native_cache.update({"at": 0.0, "ids": {}})
    dispatch.invalidate_platform_cache()
    dispatch._health_cache.clear()
    gateway_router.invalidate()
    model_suffix.invalidate()
    model_suffix._cache.pop("storage", None)


#: 两条模型配置：Marvis 存槽位名，WorkBuddy 存公开原生名。
_MODEL_ROWS = {
    MARVIS_PLATFORM: dict(
        platform=MARVIS_PLATFORM,
        model_id=MARVIS_SLOT,
        enabled=1,
        note="DeepSeek-V4.1-Flash",
        credit_multiplier=-1.0,
        upstream_metadata='{"name": "DeepSeek-V4.1-Flash"}',
    ),
    WB_PLATFORM: dict(
        platform=WB_PLATFORM,
        model_id=PUBLIC_ID,
        enabled=1,
        note="DeepSeek-V4.1-Flash",
        credit_multiplier=1.0,
        upstream_metadata="",
    ),
}


#: 默认测试账号：每个落库平台一行健康号（(storage, domain)）。
_DEFAULT_ACCOUNTS = (
    (WB_PLATFORM, ""),
    (MARVIS_PLATFORM, "cn"),
)


def _row(platform: str, model_id: str, *, multiplier=1.0, metadata="", note="",
         enabled: int = 1) -> dict:
    """一条 model_configs 行的字面量（供各行测试按需拼装目录）。"""
    return dict(platform=platform, model_id=model_id, enabled=enabled,
                note=note or model_id, credit_multiplier=multiplier,
                upstream_metadata=metadata)


def _seed(rows, accounts=None) -> None:
    """按给定顺序插入模型配置（`rows` 为 dict 列表）+ 测试账号 + 测试 Key。"""
    _ensure_schema()
    _clear_tables()
    with admin_db.SessionLocal() as db:
        for row in rows:
            db.add(ModelConfig(**row))
        for index, (platform, realm) in enumerate(
                tuple(accounts) if accounts else _DEFAULT_ACCOUNTS):
            db.add(Account(platform=platform,
                           name="%s-test-%d" % (platform, index),
                           uid="%s-test-%d" % (platform, index),
                           auth_json="{}", status="active", domain=realm,
                           balance_total=100, balance_remain=100))
        db.add_all([
            ApiKey(name="regression-agg", key_hash=hash_key(AGG_KEY),
                   key_prefix="sk-reg", unlimited=1, status="active",
                   aggregate=True, platform="", group_id=0),
            ApiKey(name="regression-bound", key_hash=hash_key(BOUND_KEY),
                   key_prefix="sk-reg", unlimited=1, status="active",
                   aggregate=True, platform=WB_PLATFORM, group_id=0),
        ])
        db.commit()


def _prepare(rows, accounts=None) -> None:
    _reset_caches()
    _seed(rows, accounts)
    # 种子写完再清一次：本轮只允许看到本轮的行。
    _reset_caches()


def _add_group(model_ids, *, name: str = "regression-group") -> int:
    """建一个模型分组（成员是 `model_configs.model_id` 原生 ID），返回组 id。"""
    with admin_db.SessionLocal() as db:
        group = ModelGroup(name=name)
        db.add(group)
        db.flush()
        for model_id in model_ids:
            db.add(ModelGroupItem(group_id=group.id, model_id=str(model_id)))
        db.commit()
        return int(group.id)


def _add_api_key(token: str, *, name: str = "regression-extra",
                 platform: str = "", group_id: int = 0,
                 aggregate: bool = True) -> str:
    """插一条测试 Key，返回它的明文（请求里用 Bearer）。"""
    with admin_db.SessionLocal() as db:
        db.add(ApiKey(name=name, key_hash=hash_key(token), key_prefix="sk-reg",
                      unlimited=1, status="active", aggregate=aggregate,
                      platform=platform, group_id=group_id))
        db.commit()
    return token


# ---------------------------------------------------------------------------
# ASGI 工具
# ---------------------------------------------------------------------------
def _headers(pairs) -> list:
    return [(str(name).lower().encode("latin-1"), str(value).encode("latin-1"))
            for name, value in pairs]


def _scope(path: str, headers, method: str = "POST", query: bytes = b"") -> dict:
    return {
        "type": "http", "http_version": "1.1", "method": method,
        "path": path, "raw_path": path.encode(), "query_string": query,
        "scheme": "http", "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "headers": _headers(headers),
    }


def _receive(body: bytes):
    """合成 ASGI receive：一次性给出完整 body，之后按真实语义给断连。"""
    state = {"sent": False}

    async def receive():
        if not state["sent"]:
            state["sent"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return receive


def _receive_chunks(chunks):
    """按块回放的合成 receive（`more_body` 只在最后一块为 False）。"""
    state = {"index": 0}

    async def receive():
        index = state["index"]
        state["index"] = index + 1
        if index < len(chunks):
            return {"type": "http.request", "body": chunks[index],
                    "more_body": index < len(chunks) - 1}
        return {"type": "http.disconnect"}

    return receive


def _collect():
    messages = []

    async def send(message):
        messages.append(message)

    return messages, send


def _status(messages):
    for message in messages:
        if message.get("type") == "http.response.start":
            return int(message.get("status") or 0)
    return None


def _response_headers(messages) -> dict:
    for message in messages:
        if message.get("type") == "http.response.start":
            return {key.decode("latin-1").lower(): value.decode("latin-1")
                    for key, value in (message.get("headers") or [])}
    return {}


def _response_json(messages) -> dict:
    raw = b"".join(message.get("body", b"") for message in messages
                   if message.get("type") == "http.response.body")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return {}


@contextlib.contextmanager
def _isolated_runtime():
    """隔离外部网关指标 / 实时流量（dispatch 对它们的导入本来就容错）。"""
    traffic = SimpleNamespace(annotate=lambda *a, **k: None,
                              mark_stage=lambda *a, **k: None,
                              start=lambda *a, **k: "rid-regression")
    metrics = SimpleNamespace(current=SimpleNamespace(get=lambda: None))
    with mock.patch.dict(sys.modules, {"admin.traffic_stats": traffic,
                                       "admin.request_metrics": metrics}):
        yield


def _workbuddy_receiver(observed: dict):
    """WorkBuddy 主应用替身：读**最终** JSON，并记录缓存 body 与 messages。"""

    async def app(scope, receive, send):
        request = Request(scope, receive)
        try:
            payload = await request.json()
            error = ""
        except Exception as exc:  # noqa: BLE001 - 记录读取失败，断言会看到空 model
            payload = {}
            error = repr(exc)
        cached = scope.get("_wb_body") or b""
        observed["received"] = {
            "model": str(payload.get("model") or ""),
            "messages": payload.get("messages"),
            "payload": payload,
            "error": error,
        }
        observed["cached"] = {
            # 用生产代码自己的读取器，确保与下一个中间件/hop 的读法一致。
            "model": dispatch._model_of_body(cached) if cached else "",
            "raw": cached,
        }
        await JSONResponse(
            {"object": "chat.completion", "model": payload.get("model")},
        )(scope, receive, send)

    return app


@contextlib.contextmanager
def _no_external_gateway():
    """只让测试进程认识 WorkBuddy（默认平台，本身没有独立网关实现）。

    本文件只验「WorkBuddy 主应用到底收到什么」。其它平台在测试进程里既没有
    真实账号也没有网络：一旦被调用，只会把「模型名错发」伪装成一个 503
    （例如 Marvis 的「登录态已过期」），把模型断言淹掉。计划要求隔离外部网关，
    因此这里把非默认平台的 _spec_with_gateway() 解析成 None：
      * _reroute_target() 找不到可改道目标 -> 保持当前落点；
      * 候选循环里也没有任何可调用的外部平台。
    """
    real = dispatch._spec_with_gateway

    def _only_default(platform_id, realm):
        if str(platform_id or "").strip().lower() == registry.DEFAULT_PLATFORM:
            return real(platform_id, realm)
        return None

    with mock.patch.object(dispatch, "_spec_with_gateway", _only_default):
        yield


class _RecordingGateway:
    """本地假平台网关：记录每次调用，默认回 200 JSON。

    本文件全程离线：平台侧一律由它替代，只读请求体、记录
    (platform, path, model, body)，再回一个可配置响应。
    """

    def __init__(self, platform_id: str, responder=None):
        self.platform_id = platform_id
        self.calls = []
        self._responder = responder

    async def dispatch(self, request, path):
        raw = await request.body()
        body = {}
        if raw:
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                body = {}
        call = {"platform": self.platform_id, "path": path,
                "model": str(body.get("model") or ""), "body": body}
        self.calls.append(call)
        if self._responder is not None:
            response = self._responder(self, path, body)
            if response is not None:
                return response
        return JSONResponse({"object": "chat.completion",
                             "platform": self.platform_id,
                             "model": call["model"]})

    @property
    def models(self):
        return [call["model"] for call in self.calls]


@contextlib.contextmanager
def _fake_gateways(gateways):
    """把 registry.load_gateway 换成本地假网关（spec.id -> _RecordingGateway）。

    非默认平台的真网关会选号、记账、打上游；测试里必须隔离，所以只让
    `spec.id` 命中的平台拿到假模块，默认平台（本身没有独立网关）保持原样，
    其余平台解析成 None（等价于「没装配」）。
    """
    modules = {str(pid).lower(): SimpleNamespace(dispatch=gw.dispatch)
               for pid, gw in (gateways or {}).items()}
    real = registry.load_gateway

    def _load(spec):
        pid = str(getattr(spec, "id", "") or "").lower()
        if pid in modules:
            return modules[pid]
        if pid == registry.DEFAULT_PLATFORM:
            return real(spec)
        return None

    with mock.patch.object(registry, "load_gateway", _load):
        yield


@contextlib.contextmanager
def _gateway_env(gateways=None):
    """请求期间的外部平台隔离：给了假网关就只装它们，否则只留默认平台。"""
    if gateways:
        with _fake_gateways(gateways):
            yield
    else:
        with _no_external_gateway():
            yield


def _dispatch_request(scope, body: bytes, app, gateways=None) -> list:
    messages, send = _collect()
    middleware = dispatch.PlatformDispatchMiddleware(app)
    with _isolated_runtime(), _gateway_env(gateways):
        asyncio.run(middleware(scope, _receive(body), send))
    return messages


def _run_middleware(scope, body: bytes, app) -> list:
    """默认平台用例：只隔离外部网关，不装任何假平台。"""
    return _dispatch_request(scope, body, app)


def _recording_receiver(observed: dict):
    """记录型下游：读最终 body / payload，并回 200 JSON。"""

    async def app(scope, receive, send):
        request = Request(scope, receive)
        raw = await request.body()
        payload = {}
        if raw:
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                payload = {}
        cached = scope.get("_wb_body") or b""
        observed["received"] = {"raw": raw, "payload": payload,
                                "model": str((payload or {}).get("model") or "")}
        observed["cached"] = {"raw": cached,
                              "present": "_wb_body" in scope,
                              "model": dispatch._model_of_body(cached) if cached else ""}
        await JSONResponse({"ok": True,
                            "model": (payload or {}).get("model")})(scope, receive, send)

    return app


def _call(scope, body: bytes, observed: dict, *, gateways=None, app=None) -> dict:
    """发一次请求并把状态码 / 落点头 / 响应体记进 observed。"""
    receiver = app if app is not None else _recording_receiver(observed)
    messages = _dispatch_request(scope, body, receiver, gateways)
    observed["status"] = _status(messages)
    observed["headers"] = _response_headers(messages)
    observed["platform"] = str(observed["headers"].get("x-gateway-platform") or "")
    observed["gateway_model"] = str(observed["headers"].get("x-gateway-model") or "")
    observed["response"] = _response_json(messages)
    observed["asgi_messages"] = messages
    return observed


def _json_scope(path: str, payload, *, key=AGG_KEY, extra_headers=(), method="POST",
                query: bytes = b"", declared_length=True):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [("content-type", "application/json")]
    if declared_length:
        headers.append(("content-length", str(len(body))))
    if key:
        headers.append(("authorization", "Bearer " + key))
    headers += list(extra_headers)
    return _scope(path, headers, method=method, query=query), body


@contextlib.contextmanager
def _capture_dispatch_logs(level=logging.INFO):
    """收集本次请求期间 dispatch 写下的日志文本（供「日志可辨认/不含正文」用）。

    默认按 **INFO**（生产默认级别）采集：只有 DEBUG 才出现的行在生产日志里看不到，
    不能拿它当「能辨认」的证据。
    """
    records = []

    class _Handler(logging.Handler):
        def emit(self, record):
            try:
                records.append(record.getMessage())
            except Exception:  # noqa: BLE001 - 记录失败不影响用例
                records.append("")

    handler = _Handler(level=level)
    target = logging.getLogger("admin.platform.dispatch")
    old_level = target.level
    target.addHandler(handler)
    target.setLevel(level)
    try:
        yield records
    finally:
        target.removeHandler(handler)
        target.setLevel(old_level)


# ---------------------------------------------------------------------------
# 入口：GET /v1/models -> 公开 ID；POST /v1/chat/completions -> 收包
# ---------------------------------------------------------------------------
def _published_id(key: str = AGG_KEY):
    """走真实的聚合 /v1/models（strict 形状），取客户端会复制的公开 ID。"""

    async def catalog_app(scope_, receive, send):  # 不该被调用
        raise AssertionError("GET /v1/models 落到主应用：聚合清单没有生效")

    scope = _scope("/v1/models", [("authorization", "Bearer " + key)], method="GET")
    messages = _run_middleware(scope, b"", catalog_app)
    status = _status(messages)
    payload = _response_json(messages)
    assert status == 200, "/v1/models 返回 %r：%r" % (status, payload)
    data = payload.get("data") or []
    # 严格形状：不许带 platform / platforms 这些扩展字段（Rust/Go 客户端会炸）。
    for item in data:
        assert "platform" not in item and "platforms" not in item, item
        assert item.get("object") == "model", item
    return [str(item.get("id") or "") for item in data], payload


def _post_chat(model: str, key: str, extra_headers=()) -> dict:
    original = {"model": model, "messages": MESSAGES, "stream": False}
    body = json.dumps(original, ensure_ascii=False).encode("utf-8")
    headers = [("content-type", "application/json"),
               ("content-length", str(len(body))),
               ("authorization", "Bearer " + key)] + list(extra_headers)
    scope = _scope("/v1/chat/completions", headers)
    observed = {"original": original, "scope": scope, "request_model": model}
    with _no_external_gateway():
        messages = _run_middleware(scope, body, _workbuddy_receiver(observed))
    observed["status"] = _status(messages)
    observed["headers"] = _response_headers(messages)
    observed["platform"] = str(observed["headers"].get("x-gateway-platform") or "")
    observed["gateway_model"] = str(observed["headers"].get("x-gateway-model") or "")
    observed["response"] = _response_json(messages)
    return observed


def _run_entry(entry: str, storages, db_patch=None) -> dict:
    """跑一遍入口。

    `db_patch` 非空时，只把 **POST 阶段** 的 `dispatch._db` 换掉（GET /v1/models
    仍走真实临时库，否则聚合清单读不到行）。
    """
    _prepare([_MODEL_ROWS[storage] for storage in storages])
    ids, catalog = _published_id()
    assert ids == [PUBLIC_ID], (
        "聚合 /v1/models 应只发布一条公开 ID %r，实际 %r" % (PUBLIC_ID, ids))
    published_id = ids[0]

    if entry == "bare_with_header":
        model, key, extra = published_id, AGG_KEY, [("x-platform", WB_PLATFORM)]
    elif entry == "suffix_only":
        model, key, extra = published_id + "-" + WB_PLATFORM, AGG_KEY, []
    elif entry == "bound_key":
        model, key, extra = published_id, BOUND_KEY, []
    else:  # pragma: no cover - 参数写错要立刻炸
        raise AssertionError("未知入口 %r" % (entry,))

    if db_patch is None:
        observed = _post_chat(model, key, extra)
    else:
        with mock.patch.object(dispatch, "_db", db_patch):
            observed = _post_chat(model, key, extra)
    observed["published_id"] = published_id
    observed["catalog"] = catalog
    return observed


def _assert_workbuddy_entry(observed: dict) -> None:
    """计划 §三 §44-48 的断言；失败信息带上实际收包，便于直接定位。"""
    published_id = observed["published_id"]
    assert published_id == PUBLIC_ID, (
        "公开 ID：期望 %r 实际 %r" % (PUBLIC_ID, published_id))
    assert observed["status"] == 200, (
        "入口 %r 返回 %r：%r"
        % (observed["request_model"], observed["status"], observed["response"]))
    assert observed["platform"] == WB_PLATFORM, (
        "落点头 x-gateway-platform：期望 %r 实际 %r（headers=%r）"
        % (WB_PLATFORM, observed["platform"], observed["headers"]))
    assert observed["received"]["model"] == PUBLIC_ID, (
        "WorkBuddy 接收器读到的 model：期望 %r 实际 %r"
        "（请求 model=%r，缓存 body=%r，gateway_model=%r）"
        % (PUBLIC_ID, observed["received"]["model"], observed["request_model"],
           observed["cached"]["raw"], observed["gateway_model"]))
    assert observed["cached"]["model"] == observed["received"]["model"], (
        "scope['_wb_body'] 缓存 body 里的 model（%r）与实际转发给接收端的 model"
        "（%r）不一致：本次转发的 body 与缓存必须是同一份字节"
        % (observed["cached"]["model"], observed["received"]["model"]))
    assert observed["received"]["messages"] == observed["original"]["messages"], (
        "messages 被改写：%r != %r"
        % (observed["received"]["messages"], observed["original"]["messages"]))
    # 计划 §四：**只改顶层 model**，其余字段（含 stream 开关）必须原样。
    received_rest = {key: value for key, value in
                     (observed["received"].get("payload") or {}).items()
                     if key != "model"}
    original_rest = {key: value for key, value in observed["original"].items()
                     if key != "model"}
    assert received_rest == original_rest, (
        "请求体除 model 之外的字段被改写：%r != %r"
        % (received_rest, original_rest))
    # 回显头只能回**用户请求的那个名字**，绝不能泄漏内部换算结果（例如 main-auto）。
    if observed["gateway_model"]:
        assert observed["gateway_model"] == observed["request_model"], (
            "x-gateway-model 回显的不是原始请求名：期望 %r 实际 %r"
            % (observed["request_model"], observed["gateway_model"]))


# ---------------------------------------------------------------------------
# A. 真实 SQLite 入口回归：三条入口 × 两种插入顺序
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("order_name,storages", ORDERS, ids=ORDER_IDS)
def test_bare_model_with_platform_header(order_name, storages):
    """a) 裸公开名 + X-Platform: workbuddy。"""
    _assert_workbuddy_entry(_run_entry("bare_with_header", storages))


@pytest.mark.parametrize("order_name,storages", ORDERS, ids=ORDER_IDS)
def test_model_with_workbuddy_suffix(order_name, storages):
    """b) 只带 -workbuddy 后缀。"""
    _assert_workbuddy_entry(_run_entry("suffix_only", storages))


@pytest.mark.parametrize("order_name,storages", ORDERS, ids=ORDER_IDS)
def test_key_bound_to_workbuddy(order_name, storages):
    """c) 绑定 workbuddy 的测试 Key。"""
    _assert_workbuddy_entry(_run_entry("bound_key", storages))


# ---------------------------------------------------------------------------
# A2. 用例自身的前提：别让「静默 False」把入口断言变成碰巧通过
# ---------------------------------------------------------------------------
def test_fixture_premises_native_id_and_alias_paths():
    """钉住两条容易被误读的前提（都是实测踩过的坑）。

    1) is_native_model_id(storage, name) **不传 db** 时会自开
       admin.db.SessionLocal()，指向 ADMIN_DATABASE_URL 的引擎。本文件正是把
       那张临时库建了表、也种了行，所以它必须真的命中 True —— 一旦有人换成
       自定义引擎（只在旁路 create_all），这里会**静默返回 False**，后缀入口
       就会因为「不是原生 id」走到别处，断言变成碰巧通过。
    2) alias_to_upstream() 的 ("", "") 有**两种**含义：「原生 id 优先、不改写」
       （workbuddy）与「根本不是别名」。真别名返回平台的槽位名
       （marvis: deepseek-v4.1-flash -> main-auto）。断言空元组时必须把另一条
       分支也钉住，否则分不清是哪一种。
    """
    for order_name, storages in ORDERS:
        _prepare([_MODEL_ROWS[storage] for storage in storages])
        assert models_router.is_native_model_id(WB_PLATFORM, PUBLIC_ID) is True, (
            "行顺序 %s：workbuddy 的原生 id 判定没读到行（静默 False），检查是否只在旁路引擎上 create_all" % (order_name,))
        assert models_router.is_native_model_id(
            MARVIS_PLATFORM, PUBLIC_ID) is False, (
            "行顺序 %s：marvis 存槽位名，公开名不该算原生 id" % (order_name,))
        assert models_router.is_native_model_id(
            MARVIS_PLATFORM, MARVIS_SLOT) is True, (
            "行顺序 %s：marvis 的槽位名才是原生 id" % (order_name,))
        # 原生 id 优先 -> 不改写；真别名 -> 换成该平台的槽位名。
        assert model_suffix.alias_to_upstream(
            PUBLIC_ID + "-" + WB_PLATFORM) == ("", ""), (
            "workbuddy 是原生 id，不该被别名改写")
        assert model_suffix.alias_to_upstream(
            PUBLIC_ID + "-" + MARVIS_PLATFORM) == (
                MARVIS_PLATFORM, MARVIS_SLOT), (
            "marvis 的可读名必须还原成槽位 %r" % (MARVIS_SLOT,))


# ---------------------------------------------------------------------------
# B. 顺序确定的行迭代器：不依赖数据库执行计划
# ---------------------------------------------------------------------------
def _platform_filter(criteria) -> str:
    """从 filter() 条件里认出 lower(model_configs.platform) == :x。"""
    for criterion in criteria or ():
        text_ = str(criterion)
        if "lower(" not in text_ or "platform" not in text_:
            continue
        value = getattr(getattr(criterion, "right", None), "value", None)
        if isinstance(value, str) and value:
            return value.strip().lower()
    return ""


class _FixedRowsQuery:
    """按给定顺序返回行的假查询对象（只理解「平台」这一条过滤）。"""

    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *criteria):
        platform = _platform_filter(criteria)
        if not platform:
            return self
        return _FixedRowsQuery(
            [row for row in self._rows
             if str(getattr(row, "platform", "")).lower() == platform])

    def order_by(self, *args, **kwargs):
        # 故意**不**排序：顺序由构造这个迭代器的人决定。
        return self

    def group_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FixedRowIteratorDB:
    """顺序确定的行迭代器：只支持 model_configs，其余查询一律抛错。"""

    def __init__(self, rows):
        self._rows = list(rows)

    def query(self, *entities):
        if len(entities) == 1 and entities[0] is ModelConfig:
            return _FixedRowsQuery(self._rows)
        keys = [getattr(entity, "key", None) for entity in entities]
        if not keys or any(key is None for key in keys):
            raise RuntimeError("测试替身不支持的查询：%r" % (entities,))
        return _FixedRowsQuery(
            [tuple(getattr(row, key) for key in keys) for row in self._rows])

    def close(self):
        return None


def _fixed_rows(storages) -> list:
    return [SimpleNamespace(**_MODEL_ROWS[storage]) for storage in storages]


def _run_entry_with_row_iterator(entry: str, storages) -> dict:
    """同一入口，但模型表行顺序由本地迭代器决定（不看 SQLite 执行计划）。"""
    db = _FixedRowIteratorDB(_fixed_rows(storages))

    def _fake_db():
        return contextlib.nullcontext(db)

    return _run_entry(entry, storages, db_patch=_fake_db)


@pytest.mark.parametrize("entry", ["bare_with_header", "suffix_only", "bound_key"])
def test_entry_result_ignores_row_iteration_order(entry):
    """两种行顺序的收包结果必须完全一致，且都是目录原拼写。"""
    models = {}
    for name, storages in ORDERS:
        _reset_caches()
        observed = _run_entry_with_row_iterator(entry, storages)
        models[name] = observed["received"]["model"]
        assert observed["received"]["model"] == PUBLIC_ID, (
            "行顺序 %s 的入口 %s：WorkBuddy 接收器读到 %r，期望 %r"
            "（请求 model=%r，缓存 body=%r）"
            % (name, entry, observed["received"]["model"], PUBLIC_ID,
               observed["request_model"], observed["cached"]["raw"]))
    assert models["marvis_first"] == models["workbuddy_first"], (
        "行迭代顺序改变了收包结果：%r" % (models,))


# ---------------------------------------------------------------------------
# C. 平台作用域映射：同一公开 ID 在两条目录里各归各家
# ---------------------------------------------------------------------------
def test_platform_scoped_mapping_is_row_order_independent():
    """按平台收窄的映射不能随行顺序变化（任务 2 的口径）。"""
    for name, storages in ORDERS:
        db = _FixedRowIteratorDB(_fixed_rows(storages))
        _reset_caches()
        workbuddy = dispatch._model_id_for(db, WB_PLATFORM, PUBLIC_ID)
        marvis = dispatch._model_id_for(db, MARVIS_PLATFORM, PUBLIC_ID)
        assert workbuddy == PUBLIC_ID, (
            "行顺序 %s：workbuddy 的 model_id 期望 %r 实际 %r"
            % (name, PUBLIC_ID, workbuddy))
        assert marvis == MARVIS_SLOT, (
            "行顺序 %s：marvis 的 model_id 期望槽位 %r 实际 %r"
            % (name, MARVIS_SLOT, marvis))


# ---------------------------------------------------------------------------
# D. §八 行2：Marvis 槽位版 / 可读名版目录都要命中 Marvis
# ---------------------------------------------------------------------------
#: 两套目录：一套存上游槽位名（线上现状），一套存可读名（清单同步后的形态）。
MARVIS_SLOT_CATALOG = [_MODEL_ROWS[MARVIS_PLATFORM], _MODEL_ROWS[WB_PLATFORM]]
MARVIS_READABLE_CATALOG = [
    _row(MARVIS_PLATFORM, PUBLIC_ID, multiplier=-1.0,
         metadata='{"name": "DeepSeek-V4.1-Flash"}'),
    _MODEL_ROWS[WB_PLATFORM],
]
MARVIS_CATALOGS = {"slot": MARVIS_SLOT_CATALOG,
                   "readable": MARVIS_READABLE_CATALOG}

#: 三条入口：可读名后缀 / 历史槽位后缀 / 裸槽位（按归属识别）。
MARVIS_ENTRIES = (
    ("readable_suffix", PUBLIC_ID + "-" + MARVIS_PLATFORM),
    ("slot_suffix", MARVIS_SLOT + "-" + MARVIS_PLATFORM),
    ("bare_slot", MARVIS_SLOT),
)
MARVIS_ENTRY_IDS = [name for name, _model in MARVIS_ENTRIES]


@pytest.mark.parametrize("catalog_name", list(MARVIS_CATALOGS))
@pytest.mark.parametrize("entry_name,request_model", MARVIS_ENTRIES,
                         ids=MARVIS_ENTRY_IDS)
def test_marvis_catalog_variants_reach_marvis(entry_name, request_model,
                                              catalog_name):
    """可读名后缀 / 历史槽位后缀 / 裸槽位都必须落到 Marvis，且协议边界拿到合法槽位。

    「目录存槽位」与「目录存可读名」是线上同时存在的两种形态（计划 §八 行2）。
    """
    marvis = _RecordingGateway(MARVIS_PLATFORM)
    observed = {"request_model": request_model}
    scope, body = _json_scope(
        "/v1/chat/completions",
        {"model": request_model, "messages": MESSAGES, "stream": False})
    _prepare(MARVIS_CATALOGS[catalog_name])
    _call(scope, body, observed, gateways={MARVIS_PLATFORM: marvis})

    assert observed["status"] == 200, (entry_name, catalog_name, observed["response"])
    assert observed["platform"] == MARVIS_PLATFORM, (entry_name, observed["headers"])
    assert [call["platform"] for call in marvis.calls] == [MARVIS_PLATFORM], (
        "%s/%s 没有落到 Marvis：%r" % (catalog_name, entry_name, marvis.calls))
    received = marvis.calls[-1]["model"]
    expected = MARVIS_SLOT if catalog_name == "slot" else PUBLIC_ID
    assert received == expected, (catalog_name, entry_name, received)
    # Marvis 协议边界：无论上面收到哪种写法，_validate_model 都必须得到槽位。
    slot = marvis_gateway._validate_model(
        {"model": received, "messages": MESSAGES})
    assert slot == MARVIS_SLOT, (catalog_name, entry_name, received, slot)
    # messages 原样到达平台网关
    assert marvis.calls[-1]["body"]["messages"] == MESSAGES


# ---------------------------------------------------------------------------
# E. §八 行3：Qoder / Loomy / Trae 的平台专名与区域
# ---------------------------------------------------------------------------
QODER_ROWS = [
    _row("qoder", "dfmodel", metadata='{"name": "DeepSeek-Flash"}'),
    _row("qoder", "dmodel", metadata='{"name": "DeepSeek-V4-Pro"}'),
]
LOOMY_ROWS = [_row("loomy", "GLM-5.3-Flash", metadata='{"name": "GLM-5.3-Flash"}')]
TRAE_ROWS = [
    _row("trae", PUBLIC_ID + ":cn"),
    _row("trae", PUBLIC_ID + ":intl"),
]


@pytest.mark.parametrize("requested,expected", [
    ("deepseek-flash-qoder", "dfmodel"),
    ("deepseek-v4-pro-qoder", "dmodel"),
])
def test_qoder_readable_suffix_forwards_platform_id(requested, expected):
    """可读名 + qoder 后缀 -> 上游自己的缩写 id（计划 §六 行3）。"""
    qoder = _RecordingGateway("qoder")
    observed = {"request_model": requested}
    scope, body = _json_scope(
        "/v1/chat/completions",
        {"model": requested, "messages": MESSAGES, "stream": False})
    _prepare(QODER_ROWS + [_MODEL_ROWS[WB_PLATFORM]])
    _call(scope, body, observed, gateways={"qoder": qoder})

    assert observed["status"] == 200, observed["response"]
    assert observed["platform"] == "qoder", observed["headers"]
    assert qoder.models == [expected], (requested, qoder.models)
    assert qoder.calls[-1]["body"]["messages"] == MESSAGES


def test_loomy_keeps_catalog_case_byte_for_byte():
    """Loomy 目录里是 `GLM-5.3-Flash`：上游大小写敏感，必须逐字节还原。"""
    loomy = _RecordingGateway("loomy")
    requested = "glm-5.3-flash-loomy"
    observed = {"request_model": requested}
    scope, body = _json_scope(
        "/v1/chat/completions",
        {"model": requested, "messages": MESSAGES, "stream": False})
    _prepare(LOOMY_ROWS + [_MODEL_ROWS[WB_PLATFORM]])
    _call(scope, body, observed, gateways={"loomy": loomy})

    assert observed["status"] == 200, observed["response"]
    assert observed["platform"] == "loomy", observed["headers"]
    assert loomy.models == ["GLM-5.3-Flash"], loomy.models
    assert loomy.models[0].encode("utf-8") == b"GLM-5.3-Flash"


def test_trae_intl_model_only_hits_intl_realm():
    """只登记国际区的模型：国内区一次都不许被调用，上游串去掉区域标记。"""
    cn_gateway = _RecordingGateway("trae-cn")
    intl_gateway = _RecordingGateway("trae-intl")
    requested = PUBLIC_ID + "-trae-intl"
    observed = {"request_model": requested}
    scope, body = _json_scope(
        "/v1/chat/completions",
        {"model": requested, "messages": MESSAGES, "stream": False})
    _prepare(TRAE_ROWS + [_MODEL_ROWS[WB_PLATFORM]])
    _call(scope, body, observed,
          gateways={"trae-cn": cn_gateway, "trae-intl": intl_gateway})

    assert observed["status"] == 200, observed["response"]
    assert cn_gateway.calls == [], ("国内区被调用了", cn_gateway.calls)
    assert [call["platform"] for call in intl_gateway.calls] == ["trae-intl"]
    assert intl_gateway.models == [PUBLIC_ID], intl_gateway.models
    assert ":" not in intl_gateway.models[0], (
        "上游模型串还带着区域标记：%r" % (intl_gateway.models[0],))


# ---------------------------------------------------------------------------
# F. §八 行5：错误分类与「只冷该账号×该模型」
# ---------------------------------------------------------------------------
#: 真实上游 11102 的响应体（本地上游响应样本，非公网）。
_UPSTREAM_11102_BODY = json.dumps(
    {"code": 11102,
     "message": "model [deepseek-v4.1-flash] service info not found"},
    ensure_ascii=False)
_NO_BALANCE_BODY = json.dumps(
    {"code": 11119, "message": "您的积分不足，请充值后再试"}, ensure_ascii=False)
_UNKNOWN_MODEL_BODY = json.dumps(
    {"error": {"code": "model_not_found", "message": "模型不存在"}},
    ensure_ascii=False)


def test_error_kinds_distinguish_11102_no_balance_and_unknown_model():
    """三类失败必须分成三种 error_kind，日志/统计才分得清（计划 §八 行5）。"""
    kinds = {
        "model_block": proxy_router._classify_error(400, _UPSTREAM_11102_BODY),
        "hard_credit": proxy_router._classify_error(400, _NO_BALANCE_BODY),
        "unknown_model": proxy_router._classify_error(400, _UNKNOWN_MODEL_BODY),
    }
    assert kinds["model_block"] == "model_block", kinds
    assert kinds["hard_credit"] == "hard_credit", kinds
    # 模型未知 = 客户端问题：不能冷却账号，也不能留下模型级负缓存。
    assert kinds["unknown_model"] == "client", kinds
    assert len(set(kinds.values())) == 3, ("三类错误没有分开", kinds)


def test_11102_model_block_only_cools_that_account_and_model():
    """11102 的负缓存只能落在 (该账号, 该模型)：别的账号 / 别的模型必须原样不变。"""
    _prepare([_row(WB_PLATFORM, PUBLIC_ID),
              _row(WB_PLATFORM, "deepseek-v4-pro")])
    kind = proxy_router._classify_error(400, _UPSTREAM_11102_BODY)
    assert kind == "model_block", kind
    future = datetime.utcnow() + timedelta(hours=1)
    with admin_db.SessionLocal() as db:
        accounts = db.query(Account).order_by(Account.id).all()
        assert len(accounts) >= 2, accounts
        target, other = accounts[0], accounts[1]
        # 既存的两条冷却：另一个账号×同模型、同账号×另一个模型。
        db.add(AccountModelCooldown(account_id=other.id, model=PUBLIC_ID,
                                    kind="model_rate", until=future,
                                    reason="pre-existing"))
        db.add(AccountModelCooldown(account_id=target.id,
                                    model="deepseek-v4-pro",
                                    kind="model_rate", until=future,
                                    reason="pre-existing"))
        db.commit()
        before = {(row.account_id, row.model): (row.kind, row.until)
                  for row in db.query(AccountModelCooldown).all()}
        proxy_router._set_model_cooldown(
            db, target, PUBLIC_ID, kind, _UPSTREAM_11102_BODY,
            reset_at=None, fallback_seconds=600, status=400)
        db.commit()
        after = {(row.account_id, row.model): (row.kind, row.until)
                 for row in db.query(AccountModelCooldown).all()}
    hit = after.get((target.id, PUBLIC_ID))
    assert hit is not None, ("11102 没有落到 (账号, 模型)", sorted(after))
    assert hit[0] == "model_block", hit
    for key, value in before.items():
        assert after[key] == value, ("别的账号/模型被牵连", key, after[key], value)
    assert len(after) == len(before) + 1, sorted(after)


# ---------------------------------------------------------------------------
# G. §八 行6：管理员与客户端视角（聚合 ID / 旧别名 / 分组 key / 日志）
# ---------------------------------------------------------------------------
def test_group_models_returns_distinct_string_keys():
    """同一平台的两个模型必须有各自稳定的**字符串**组 key（不是 (平台,区) 数组）。"""
    groups = group_models([
        {"model_id": "deepseek-v4-pro", "platform": WB_PLATFORM,
         "credit_multiplier": 1.0, "enabled": 1, "display_name": ""},
        {"model_id": PUBLIC_ID, "platform": WB_PLATFORM,
         "credit_multiplier": 1.0, "enabled": 1, "display_name": ""},
    ])
    assert len(groups) == 2, groups
    keys = [group["key"] for group in groups]
    for key in keys:
        assert isinstance(key, str) and key, ("组 key 不是字符串", key)
    assert len(set(keys)) == 2, ("两个不同模型拿到了同一个组 key", keys)


def test_aggregate_id_and_legacy_alias_both_reach_workbuddy():
    """聚合 ID 与历史别名都必须能回到同一个模型（计划 §八 行6）。"""
    for request_model in (PUBLIC_ID, "deepseek-flash"):
        observed = {"request_model": request_model}
        scope, body = _json_scope(
            "/v1/chat/completions",
            {"model": request_model, "messages": MESSAGES, "stream": False},
            extra_headers=[("x-platform", WB_PLATFORM)])
        _prepare(MARVIS_READABLE_CATALOG)
        _call(scope, body, observed)
        assert observed["status"] == 200, (request_model, observed["response"])
        assert observed["platform"] == WB_PLATFORM, observed["headers"]
        assert observed["received"]["model"] == PUBLIC_ID, (
            "入口 %r 没有回到 %r：接收器读到 %r（缓存 body=%r）"
            % (request_model, PUBLIC_ID, observed["received"]["model"],
               observed["cached"]["raw"]))


def test_request_log_identifies_names_platform_and_realm():
    """同一请求的日志要能读出原始名 / 目标名 / 平台 / 区（计划 §八 行6）。"""
    requested = PUBLIC_ID + "-qoder"
    qoder = _RecordingGateway("qoder")
    observed = {"request_model": requested}
    scope, body = _json_scope(
        "/v1/chat/completions",
        {"model": requested, "messages": MESSAGES, "stream": False})
    _prepare(QODER_ROWS + [_MODEL_ROWS[WB_PLATFORM]])
    with _capture_dispatch_logs() as records:
        _call(scope, body, observed, gateways={"qoder": qoder})
    assert observed["platform"] == "qoder", observed["headers"]
    text = "\n".join(records)
    assert records, "dispatch 一条日志都没写，排障无从下手"
    assert any(requested in line for line in records), (
        "日志里读不到原始请求名 %r：%r" % (requested, records))
    assert any("dfmodel" in line for line in records), (
        "日志里读不到目标模型名 dfmodel：%r" % (records,))
    assert any("qoder" in line for line in records), (
        "日志里读不到平台 qoder：%r" % (records,))
    assert any("qoder" in line and "cn" in line for line in records), (
        "同一行日志里读不到「平台 + 区」：%r" % (records,))
    assert text  # 保持 text 被使用，便于失败时人工查看


def test_request_log_carries_no_payload_or_credentials():
    """日志只许带路由字段：正文内容与 Key 明文都不许出现（计划 §八 行6）。"""
    marker = "SECRET-PAYLOAD-MARKER-9c1f"
    messages = [{"role": "user", "content": marker}]
    observed = {"request_model": PUBLIC_ID}
    scope, body = _json_scope(
        "/v1/chat/completions",
        {"model": PUBLIC_ID, "messages": messages, "stream": False},
        extra_headers=[("x-platform", WB_PLATFORM)])
    _prepare(MARVIS_READABLE_CATALOG)
    with _capture_dispatch_logs() as records:
        _call(scope, body, observed)
    assert observed["status"] == 200, observed["response"]
    joined = "\n".join(records)
    assert marker not in joined, ("日志泄漏了请求正文", [r for r in records if marker in r])
    assert AGG_KEY not in joined, ("日志泄漏了 Key 明文", [r for r in records if AGG_KEY in r])


# ---------------------------------------------------------------------------
# H. §八 行7：协议与可回放性
# ---------------------------------------------------------------------------
_PROTOCOL_PATHS = (
    ("chat", "/v1/chat/completions",
     {"model": PUBLIC_ID, "messages": MESSAGES, "stream": False}),
    ("responses", "/v1/responses",
     {"model": PUBLIC_ID, "input": MESSAGES, "stream": False}),
    ("messages", "/v1/messages",
     {"model": PUBLIC_ID, "messages": MESSAGES, "max_tokens": 16}),
)


@pytest.mark.parametrize("name,path,payload", _PROTOCOL_PATHS,
                         ids=[case[0] for case in _PROTOCOL_PATHS])
def test_protocol_paths_preserve_body_and_headers(name, path, payload):
    """三条协议入口的请求体必须原样到达下游，落点头契约不变。"""
    observed = {"request_model": PUBLIC_ID}
    scope, body = _json_scope(path, payload,
                              extra_headers=[("x-platform", WB_PLATFORM)])
    _prepare(MARVIS_READABLE_CATALOG)
    _call(scope, body, observed)

    assert observed["status"] == 200, (name, observed["response"])
    assert observed["platform"] == WB_PLATFORM, (name, observed["headers"])
    received = observed["received"]["payload"]
    assert {key: value for key, value in received.items() if key != "model"} == {
        key: value for key, value in payload.items() if key != "model"}, (name, received)
    headers = observed["headers"]
    assert headers.get("x-gateway-platform") == WB_PLATFORM, headers
    # 回显头只回**原始请求名**：没改名时允许缺省，给了就必须是原名。
    if "x-gateway-model" in headers:
        assert headers["x-gateway-model"] == PUBLIC_ID, headers


def test_streaming_response_chunks_reach_the_client():
    """流式（SSE）响应必须逐块回到客户端，且落点头仍在 response start 上。"""
    _prepare(MARVIS_READABLE_CATALOG)
    scope, body = _json_scope(
        "/v1/chat/completions",
        {"model": PUBLIC_ID, "messages": MESSAGES, "stream": True},
        extra_headers=[("x-platform", WB_PLATFORM)])

    async def stream_app(scope_, receive, send):
        await Request(scope_, receive).body()

        async def gen():
            yield b': "a"}\n\n'
            yield b"data: [DONE]\n\n"

        await StreamingResponse(gen(), media_type="text/event-stream")(scope_, receive, send)

    state = {"sent": False}

    async def receive():
        if not state["sent"]:
            state["sent"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        # 真实服务端语义：body 读完后**等断连**，不能立刻回 disconnect，
        # 否则 StreamingResponse 会在发出第一块之前就被取消。
        await asyncio.sleep(5)
        return {"type": "http.disconnect"}

    messages, send = _collect()
    middleware = dispatch.PlatformDispatchMiddleware(stream_app)
    with _isolated_runtime(), _no_external_gateway():
        asyncio.run(middleware(scope, receive, send))

    raw = b"".join(message.get("body", b"") for message in messages
                   if message.get("type") == "http.response.body")
    assert b': "a"}' in raw, raw
    assert b"[DONE]" in raw, raw
    assert _response_headers(messages).get("x-gateway-platform") == WB_PLATFORM


def test_empty_post_body_is_forwarded_without_pre_read():
    """声明长度 0 的空 POST 不预读，但必须原样交给下游。"""
    observed = {"request_model": ""}
    scope = _scope("/v1/chat/completions",
                   [("content-type", "application/json"),
                    ("content-length", "0"),
                    ("authorization", "Bearer " + AGG_KEY)])
    _prepare(MARVIS_READABLE_CATALOG)
    _call(scope, b"", observed)

    assert observed["status"] == 200, observed["response"]
    assert observed["received"]["raw"] == b"", observed["received"]["raw"]
    assert observed["cached"]["present"] is False, ("空 body 不该走预读", observed["cached"])


def test_chunked_body_is_reassembled_before_forwarding():
    """无 Content-Length 的分块请求必须整段读全再回放给下游。"""
    payload = {"model": PUBLIC_ID, "messages": MESSAGES, "stream": False}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    chunks = [body[:10], body[10:30], body[30:]]
    scope = _scope("/v1/chat/completions",
                   [("content-type", "application/json"),
                    ("authorization", "Bearer " + AGG_KEY)])
    state = {"index": 0}

    async def receive():
        index = state["index"]
        state["index"] = index + 1
        if index < len(chunks):
            return {"type": "http.request", "body": chunks[index],
                    "more_body": index < len(chunks) - 1}
        return {"type": "http.disconnect"}

    observed = {"request_model": PUBLIC_ID}
    _prepare(MARVIS_READABLE_CATALOG)
    messages, send = _collect()
    middleware = dispatch.PlatformDispatchMiddleware(_recording_receiver(observed))
    with _isolated_runtime(), _no_external_gateway():
        asyncio.run(middleware(scope, receive, send))

    assert observed["received"]["raw"] == body, observed["received"]["raw"]
    assert observed["received"]["payload"] == payload
    assert _status(messages) == 200


def test_oversized_chunked_body_returns_413():
    """超过预读上限的 chunked body 必须直接 413，不再继续读。"""
    chunks = [b"x" * 20 for _ in range(4)]
    scope = _scope("/v1/chat/completions",
                   [("content-type", "application/json"),
                    ("authorization", "Bearer " + AGG_KEY)])
    observed = {"request_model": ""}
    _prepare(MARVIS_READABLE_CATALOG)
    with mock.patch.object(dispatch, "_BODY_HARD_CAP", 32):
        messages, send = _collect()
        middleware = dispatch.PlatformDispatchMiddleware(_recording_receiver(observed))
        with _isolated_runtime(), _no_external_gateway():
            asyncio.run(middleware(scope, _receive_chunks(chunks), send))

    assert _status(messages) == 413, _response_json(messages)
    assert _response_json(messages)["error"]["code"] == "body_too_large"
    assert observed == {"request_model": ""}, ("413 之后不该再转发", observed)


def test_client_disconnect_before_body_returns_400():
    """预读期客户端断开：400 client_disconnected，且不转发半截请求。"""
    observed = {"request_model": ""}
    scope = _scope("/v1/chat/completions",
                   [("content-type", "application/json"),
                    ("authorization", "Bearer " + AGG_KEY)])

    async def receive():
        return {"type": "http.disconnect"}

    _prepare(MARVIS_READABLE_CATALOG)
    messages, send = _collect()
    middleware = dispatch.PlatformDispatchMiddleware(_recording_receiver(observed))
    with _isolated_runtime(), _no_external_gateway():
        asyncio.run(middleware(scope, receive, send))

    assert _status(messages) == 400, _response_json(messages)
    assert _response_json(messages)["error"]["code"] == "client_disconnected"
    assert observed == {"request_model": ""}, ("断开后不该转发", observed)


@pytest.mark.parametrize("query,strict", [(b"", True), (b"detail=1", False)],
                         ids=["strict", "detail"])
def test_models_view_strict_and_detail(query, strict):
    """GET /v1/models：默认严格形状，?detail=1 才带平台扩展字段。"""

    async def catalog_app(scope_, receive, send):  # 不该被调用
        raise AssertionError("GET /v1/models 落到主应用：聚合清单没有生效")

    scope = _scope("/v1/models", [("authorization", "Bearer " + AGG_KEY)],
                   method="GET", query=query)
    _prepare(MARVIS_READABLE_CATALOG)
    messages = _run_middleware(scope, b"", catalog_app)
    payload = _response_json(messages)
    entries = payload.get("data") or []
    assert _status(messages) == 200, payload
    assert entries, payload
    item = entries[0]
    if strict:
        assert "platform" not in item and "platforms" not in item, item
        allowed = {"id", "object", "created", "name", "contextWindow", "maxTokens",
                   "inputModalities", "reasoningEfforts", "defaultReasoningEffort",
                   "canDisableThinking"}
        assert set(item) <= allowed, ("严格形状出现白名单外的键", sorted(item))
    else:
        assert "platforms" in item or "platform" in item, item


# ---------------------------------------------------------------------------
# I. §八 行4：聚合只在合格池内重试
# ---------------------------------------------------------------------------
#: 两个平台都声明同一个公开模型，各自有自己的拼写。
RETRY_ROWS = [
    _row("loomy", "DeepSeek-V4.1-Flash",
         metadata='{"name": "DeepSeek-V4.1-Flash"}'),
    _row("qoder", "dfmodel", metadata='{"name": "DeepSeek-Flash"}'),
]
RETRY_ACCOUNTS = (("loomy", "cn"), ("qoder", "cn"))


def _unavailable_responder(platform_id: str):
    """明确 503：平台自己有把握没做成，允许换平台。"""

    def _respond(gateway, path, body):
        return JSONResponse(
            {"error": {"message": "平台 %s 做不了这个模型" % platform_id,
                       "code": "platform_unavailable",
                       "platform": platform_id}},
            status_code=503)

    return _respond


def _boom_responder(gateway, path, body):
    """平台内部异常：副作用可能已产生，结果不确定。"""
    raise RuntimeError("platform exploded")


async def _forbidden_default_app(scope, receive, send):
    raise AssertionError("默认平台（WorkBuddy 主应用）不该被调用")


def _repeat(name: str, model: str):
    """按 `_json_scope` 的签名发一次 POST，返回 (scope, body)。"""
    return _json_scope("/v1/chat/completions",
                       {"model": model, "messages": MESSAGES, "stream": False})


def test_aggregate_retries_next_qualified_platform_on_explicit_503():
    """裸名 + 无绑定 + 开聚合：A 明确 503 -> 换 B，终态与落点头都来自 B。"""
    loomy = _RecordingGateway("loomy", _unavailable_responder("loomy"))
    qoder = _RecordingGateway("qoder")
    observed = {"request_model": PUBLIC_ID}
    scope, body = _repeat("retry", PUBLIC_ID)
    _prepare(RETRY_ROWS, RETRY_ACCOUNTS)
    _call(scope, body, observed,
          gateways={"loomy": loomy, "qoder": qoder},
          app=_forbidden_default_app)

    assert [call["platform"] for call in loomy.calls] == ["loomy"], loomy.calls
    assert [call["platform"] for call in qoder.calls] == ["qoder"], (
        "第二个候选没有被调用", qoder.calls)
    # 两个接收器各拿**自己的** model ID
    assert loomy.models == ["DeepSeek-V4.1-Flash"], loomy.models
    assert qoder.models == ["dfmodel"], qoder.models
    assert observed["status"] == 200, observed["response"]
    assert observed["platform"] == "qoder", (
        "落点头不是最终平台的 public id", observed["headers"])
    assert observed["response"].get("platform") == "qoder", observed["response"]


@pytest.mark.parametrize("selector", ["suffix", "header", "query"])
def test_explicit_target_keeps_its_503_and_never_tries_others(selector):
    """仅显式指定 A：只调 A，A 的 503 原样回客户端，B 一次都不许被调。"""
    loomy = _RecordingGateway("loomy", _unavailable_responder("loomy"))
    qoder = _RecordingGateway("qoder")
    observed = {"request_model": PUBLIC_ID}
    if selector == "suffix":
        model, extra, query = PUBLIC_ID + "-loomy", (), b""
    elif selector == "header":
        model, extra, query = PUBLIC_ID, (("x-platform", "loomy"),), b""
    else:
        model, extra, query = PUBLIC_ID, (), b"platform=loomy"
    scope, body = _json_scope(
        "/v1/chat/completions",
        {"model": model, "messages": MESSAGES, "stream": False},
        extra_headers=extra, query=query)
    _prepare(RETRY_ROWS, RETRY_ACCOUNTS)
    _call(scope, body, observed,
          gateways={"loomy": loomy, "qoder": qoder},
          app=_forbidden_default_app)

    assert len(loomy.calls) == 1, (selector, loomy.calls)
    assert qoder.calls == [], ("显式指定 A 却调用了 B", selector, qoder.calls)
    assert observed["status"] == 503, (selector, observed["response"])
    assert observed["platform"] == "loomy", (selector, observed["headers"])


def test_disabled_platform_is_not_retried():
    """B 被管理员禁用：允许重试的裸名请求也只能停在 A 的 503 上。"""
    loomy = _RecordingGateway("loomy", _unavailable_responder("loomy"))
    qoder = _RecordingGateway("qoder")
    observed = {"request_model": PUBLIC_ID}
    scope, body = _repeat("retry", PUBLIC_ID)
    _prepare(RETRY_ROWS, RETRY_ACCOUNTS)
    with admin_db.SessionLocal() as db:
        db.add(SystemSetting(key="gateway:disabled",
                             value=json.dumps({"qoder": {"at": 1,
                                               "reason": "regression"}})))
        db.commit()
    dispatch.invalidate_platform_cache()
    _call(scope, body, observed,
          gateways={"loomy": loomy, "qoder": qoder},
          app=_forbidden_default_app)

    assert len(loomy.calls) == 1, loomy.calls
    assert qoder.calls == [], ("被禁用的平台仍被调用", qoder.calls)
    assert observed["status"] == 503, observed["response"]


def test_group_denied_platform_is_not_retried():
    """分组不允许 B：候选池里就没有 B，B 的调用次数必须是 0。"""
    loomy = _RecordingGateway("loomy", _unavailable_responder("loomy"))
    qoder = _RecordingGateway("qoder")
    observed = {"request_model": PUBLIC_ID}
    scope, body = _repeat("retry", PUBLIC_ID)
    _prepare(RETRY_ROWS, RETRY_ACCOUNTS)
    group_id = _add_group(["DeepSeek-V4.1-Flash"])
    token = _add_api_key("sk-regression-group-retry", group_id=group_id)
    scope, body = _json_scope(
        "/v1/chat/completions",
        {"model": PUBLIC_ID, "messages": MESSAGES, "stream": False}, key=token)
    _call(scope, body, observed,
          gateways={"loomy": loomy, "qoder": qoder},
          app=_forbidden_default_app)

    assert len(loomy.calls) == 1, loomy.calls
    assert qoder.calls == [], ("分组外的平台仍被调用", qoder.calls)
    assert observed["status"] == 503, observed["response"]


def test_uncertain_platform_error_is_not_replayed():
    """平台内部异常 = 结果不确定：只调一次，绝不换平台重放。"""
    loomy = _RecordingGateway("loomy", _boom_responder)
    qoder = _RecordingGateway("qoder")
    observed = {"request_model": PUBLIC_ID}
    scope, body = _repeat("retry", PUBLIC_ID)
    _prepare(RETRY_ROWS, RETRY_ACCOUNTS)
    _call(scope, body, observed,
          gateways={"loomy": loomy, "qoder": qoder},
          app=_forbidden_default_app)

    assert len(loomy.calls) == 1, loomy.calls
    assert qoder.calls == [], ("异常后仍换平台重放", qoder.calls)
    assert observed["status"] == 502, observed["response"]
    assert observed["response"]["error"]["code"] == "platform_error", observed["response"]


def test_client_disconnect_during_stream_does_not_replay():
    """流已经开始发送后客户端断开：不再换平台重放。"""
    chunks_seen = []

    def _stream_responder(gateway, path, body):
        async def gen():
            chunks_seen.append(1)
            yield b'data: {"delta": 1}\n\n'
            await asyncio.sleep(0.3)
            chunks_seen.append(2)
            yield b'data: {"delta": 2}\n\n'

        return StreamingResponse(gen(), media_type="text/event-stream")

    loomy = _RecordingGateway("loomy", _stream_responder)
    qoder = _RecordingGateway("qoder")
    observed = {"request_model": PUBLIC_ID}
    scope, body = _repeat("retry", PUBLIC_ID)
    state = {"sent": False}

    async def receive():
        if not state["sent"]:
            state["sent"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        # 第一块发出后（约 0.05s）断连，让 StreamingResponse 走 listen_for_disconnect。
        await asyncio.sleep(0.05)
        return {"type": "http.disconnect"}

    _prepare(RETRY_ROWS, RETRY_ACCOUNTS)
    messages, send = _collect()
    middleware = dispatch.PlatformDispatchMiddleware(_forbidden_default_app)
    with _isolated_runtime(), _fake_gateways({"loomy": loomy, "qoder": qoder}):
        asyncio.run(middleware(scope, receive, send))

    assert len(loomy.calls) == 1, loomy.calls
    assert qoder.calls == [], ("流式断开后换平台重放", qoder.calls)
    assert _status(messages) == 200, _response_json(messages)
    assert chunks_seen, "流一个分块都没发出去"
    raw = b"".join(message.get("body", b"") for message in messages
                   if message.get("type") == "http.response.body")
    assert b'"delta": 1' in raw, raw


CAP_ROWS = RETRY_ROWS + [
    _row("marvis", "main-auto", metadata='{"name": "DeepSeek-V4.1-Flash"}'),
    _row("raccoon", PUBLIC_ID),
]
CAP_ACCOUNTS = (("loomy", "cn"), ("marvis", "cn"),
                ("qoder", "cn"), ("raccoon", "cn"))


def test_retry_attempts_are_capped_at_three_usable_targets():
    """四个可用声明者：最多 3 次尝试，「最后一次」按截断后的列表判定。"""
    gateways = {
        "loomy": _RecordingGateway("loomy", _unavailable_responder("loomy")),
        "marvis": _RecordingGateway("marvis", _unavailable_responder("marvis")),
        "qoder": _RecordingGateway("qoder", _unavailable_responder("qoder")),
        "raccoon": _RecordingGateway("raccoon"),
    }
    observed = {"request_model": PUBLIC_ID}
    scope, body = _repeat("retry", PUBLIC_ID)
    _prepare(CAP_ROWS, CAP_ACCOUNTS)
    _call(scope, body, observed, gateways=gateways,
          app=_forbidden_default_app)

    attempted = [pid for pid, gateway in gateways.items() if gateway.calls]
    total_calls = sum(len(gateway.calls) for gateway in gateways.values())
    assert total_calls == 3, ("尝试次数不是 3", total_calls, attempted)
    assert gateways["raccoon"].calls == [], (
        "第 4 个候选不该被调用", gateways["raccoon"].calls)
    # 最后一次尝试（截断后的第 3 个）的 503 原样回客户端，而不是 no_platform
    assert observed["status"] == 503, observed["response"]
    assert observed["response"]["error"]["platform"] == "qoder", (
        "终态不是最后一次尝试的响应", observed["response"])
    assert observed["platform"] == "qoder", observed["headers"]


# ---------------------------------------------------------------------------
# J. 分组 Key 的 HTTP 层端到端往返（t8 汇报告白的风险①）
# ---------------------------------------------------------------------------
GROUP_ROWS = [
    _row("qoder", "dfmodel", metadata='{"name": "DeepSeek-Flash"}'),
    _row("qoder", "dmodel", metadata='{"name": "DeepSeek-V4-Pro"}'),
]
GROUP_ACCOUNTS = (("qoder", "cn"),)


def test_group_key_roundtrip_routes_to_platform_native_id():
    """分组 Key：从 /v1/models 复制公开 ID -> 调用 -> 上游收到该平台原生 ID。

    t8 只做了函数级（`_group_allows` / `effective_group_models`）；这里是
    **HTTP 层往返**：清单 -> 复制 -> POST，且必须落在 qoder（WorkBuddy 主应用
    一次都不许被调用，否则又会把别家的原生 ID 发到默认平台换 400）。
    """
    qoder = _RecordingGateway("qoder")
    _prepare(GROUP_ROWS, GROUP_ACCOUNTS)
    group_id = _add_group(["dfmodel"])
    token = _add_api_key("sk-regression-group-rt", group_id=group_id)

    ids, catalog = _published_id(token)
    assert ids == [PUBLIC_ID], ("分组清单应只发布公开 ID", ids, catalog)
    public_id = ids[0]

    observed = {"request_model": public_id}
    scope, body = _json_scope(
        "/v1/chat/completions",
        {"model": public_id, "messages": MESSAGES, "stream": False}, key=token)
    _call(scope, body, observed, gateways={"qoder": qoder},
          app=_forbidden_default_app)

    assert observed["status"] == 200, observed["response"]
    assert "model_not_in_group" not in json.dumps(observed["response"]), (
        "组内模型仍被判 403", observed["response"])
    assert observed["platform"] == "qoder", observed["headers"]
    assert qoder.models == ["dfmodel"], (
        "上游没有收到 qoder 的原生 ID", qoder.models)


def test_group_key_rejects_model_outside_group():
    """负例：同一分组下 deepseek-v4-pro 仍必须 403 model_not_in_group。"""
    qoder = _RecordingGateway("qoder")
    _prepare(GROUP_ROWS, GROUP_ACCOUNTS)
    group_id = _add_group(["dfmodel"])
    token = _add_api_key("sk-regression-group-deny", group_id=group_id)

    requested = "deepseek-v4-pro"
    observed = {"request_model": requested}
    scope, body = _json_scope(
        "/v1/chat/completions",
        {"model": requested, "messages": MESSAGES, "stream": False}, key=token)
    _call(scope, body, observed, gateways={"qoder": qoder},
          app=_forbidden_default_app)

    assert observed["status"] == 403, observed["response"]
    assert observed["response"]["error"]["code"] == "model_not_in_group", observed["response"]
    assert qoder.calls == [], ("组外模型仍被转发", qoder.calls)


# ---------------------------------------------------------------------------
# K. §六 第 82 行补：多成员组往返 + trae :intl 孪生成员不得扩权
# ---------------------------------------------------------------------------
#: 两个平台、同一逻辑模型的成员组（loomy + qoder 各自的原生 ID）。
MULTI_GROUP_ROWS = [
    _row("loomy", "DeepSeek-V4.1-Flash",
         metadata='{"name": "DeepSeek-V4.1-Flash"}'),
    _row("qoder", "dfmodel", metadata='{"name": "DeepSeek-Flash"}'),
]
MULTI_GROUP_ACCOUNTS = (("loomy", "cn"), ("qoder", "cn"))
MULTI_GROUP_MEMBERS = ["DeepSeek-V4.1-Flash", "dfmodel"]

#: Trae 两区共享一份 storage，目录里用 `:intl` 区分国际区。
TRAE_GROUP_ROWS = [
    _row("trae", "deepseek-v4.1-flash"),
    _row("trae", "deepseek-v4.1-flash:intl"),
]
TRAE_GROUP_ACCOUNTS = (("trae", "cn"), ("trae", "intl"))


def _forbidden_gateways(*platform_ids):
    """这些平台一次都不许被调用（调用即失败，便于直接看出越权）。"""
    return {pid: _RecordingGateway(pid) for pid in platform_ids}


def test_multi_member_group_publishes_single_public_id():
    """组内两个平台、同一逻辑模型：聚合清单只发一条公开 ID（不是两条）。"""
    _prepare(MULTI_GROUP_ROWS, MULTI_GROUP_ACCOUNTS)
    group_id = _add_group(MULTI_GROUP_MEMBERS)
    token = _add_api_key("sk-regression-multi-group", group_id=group_id)

    ids, catalog = _published_id(token)
    assert ids == [PUBLIC_ID], ("多成员组发布了多条 ID", ids, catalog)
    assert catalog["data"][0]["object"] == "model", catalog["data"][0]


def test_multi_member_group_roundtrip_stays_inside_group():
    """复制公开 ID 调用：必须落在组内平台，且用它自己的原生拼写。"""
    _prepare(MULTI_GROUP_ROWS, MULTI_GROUP_ACCOUNTS)
    group_id = _add_group(MULTI_GROUP_MEMBERS)
    token = _add_api_key("sk-regression-multi-group-rt", group_id=group_id)
    ids, _catalog = _published_id(token)
    public_id = ids[0]

    loomy = _RecordingGateway("loomy")
    qoder = _RecordingGateway("qoder")
    observed = {"request_model": public_id}
    scope, body = _json_scope(
        "/v1/chat/completions",
        {"model": public_id, "messages": MESSAGES, "stream": False}, key=token)
    _call(scope, body, observed, gateways={"loomy": loomy, "qoder": qoder},
          app=_forbidden_default_app)

    assert observed["status"] == 200, observed["response"]
    assert observed["platform"] in {"loomy", "qoder"}, (
        "落点不在组内", observed["platform"], observed["headers"])
    called = [gw for gw in (loomy, qoder) if gw.calls]
    assert len(called) == 1, ("组内平台调用数不对", loomy.calls, qoder.calls)
    native = called[0].models[0]
    assert native in MULTI_GROUP_MEMBERS, ("上游收到组外的原生 ID", native)


@pytest.mark.parametrize("model,expected_platform,expected_native", [
    (PUBLIC_ID + "-loomy", "loomy", "DeepSeek-V4.1-Flash"),
    ("deepseek-flash-qoder", "qoder", "dfmodel"),
])
def test_multi_member_group_suffix_pins_each_member(model, expected_platform,
                                                     expected_native):
    """后缀固定到组内成员：各自命中自己的目录拼写，另一个成员 0 次。"""
    _prepare(MULTI_GROUP_ROWS, MULTI_GROUP_ACCOUNTS)
    group_id = _add_group(MULTI_GROUP_MEMBERS)
    token = _add_api_key("sk-regression-multi-suffix", group_id=group_id)

    loomy = _RecordingGateway("loomy")
    qoder = _RecordingGateway("qoder")
    observed = {"request_model": model}
    scope, body = _json_scope(
        "/v1/chat/completions",
        {"model": model, "messages": MESSAGES, "stream": False}, key=token)
    _call(scope, body, observed, gateways={"loomy": loomy, "qoder": qoder},
          app=_forbidden_default_app)

    assert observed["status"] == 200, (model, observed["response"])
    assert observed["platform"] == expected_platform, (model, observed["headers"])
    other = qoder if expected_platform == "loomy" else loomy
    assert other.calls == [], ("另一个成员被调用了", model, other.calls)
    hit = loomy if expected_platform == "loomy" else qoder
    assert hit.models == [expected_native], (model, hit.models)


def test_group_suffix_outside_group_is_rejected():
    """组外平台后缀必须被拒：分组授权不能被手输后缀绕过（§六 第 82 行）。"""
    _prepare(MULTI_GROUP_ROWS, MULTI_GROUP_ACCOUNTS)
    group_id = _add_group(MULTI_GROUP_MEMBERS)
    token = _add_api_key("sk-regression-outside-suffix", group_id=group_id)

    loomy = _RecordingGateway("loomy")
    qoder = _RecordingGateway("qoder")
    forwarded: list = []

    async def default_app(scope_, receive, send):
        # 默认平台（WorkBuddy 主应用）代表「组外平台」：一旦被调用就是越权放行。
        forwarded.append(scope_.get("path"))
        await JSONResponse({"ok": True, "where": "workbuddy"})(scope_, receive, send)

    requested = PUBLIC_ID + "-" + WB_PLATFORM
    observed = {"request_model": requested}
    scope, body = _json_scope(
        "/v1/chat/completions",
        {"model": requested, "messages": MESSAGES, "stream": False}, key=token)
    _call(scope, body, observed, gateways={"loomy": loomy, "qoder": qoder},
          app=default_app)

    assert forwarded == [], ("组外后缀被转交给了默认平台", forwarded)
    assert loomy.calls == [] and qoder.calls == [], (
        "组外后缀仍调用了组内平台", loomy.calls, qoder.calls)
    assert observed["status"] == 403, (
        "组外后缀没有被拒", observed["status"], observed["response"])
    assert observed["response"]["error"]["code"] == "model_not_in_group", (
        observed["response"])


def test_trae_intl_twin_cannot_widen_group():
    """组里只登记国内行时，国际区不能借后缀扩权（`:intl` 孪生）。"""
    _prepare(TRAE_GROUP_ROWS, TRAE_GROUP_ACCOUNTS)
    group_id = _add_group(["deepseek-v4.1-flash"])
    token = _add_api_key("sk-regression-trae-twin", group_id=group_id)

    cn = _RecordingGateway("trae-cn")
    intl = _RecordingGateway("trae-intl")
    forwarded: list = []

    async def default_app(scope_, receive, send):
        forwarded.append(scope_.get("path"))
        await JSONResponse({"ok": True, "where": "workbuddy"})(scope_, receive, send)

    requested = "deepseek-v4.1-flash-trae-intl"
    observed = {"request_model": requested}
    scope, body = _json_scope(
        "/v1/chat/completions",
        {"model": requested, "messages": MESSAGES, "stream": False}, key=token)
    _call(scope, body, observed,
          gateways={"trae-cn": cn, "trae-intl": intl}, app=default_app)

    assert intl.calls == [], ("国际区被扩权调用了", intl.calls)
    assert cn.calls == [], ("国内区也不该代接国际区请求", cn.calls)
    assert forwarded == [], ("请求被交给默认平台", forwarded)
    assert observed["status"] == 403, (
        "国际区后缀没有被拒", observed["status"], observed["response"])
    assert observed["response"]["error"]["code"] == "model_not_in_group", (
        observed["response"])


@pytest.mark.parametrize("suffix,expected_platform,expected_native", [
    ("-trae-cn", "trae-cn", "deepseek-v4.1-flash"),
    ("-trae-intl", "trae-intl", "deepseek-v4.1-flash"),
])
def test_trae_twin_group_members_hit_their_own_realm(suffix, expected_platform,
                                                     expected_native):
    """组内同时登记两区：各自命中自己的区，上游串按协议去掉区域标记。"""
    _prepare(TRAE_GROUP_ROWS, TRAE_GROUP_ACCOUNTS)
    group_id = _add_group(["deepseek-v4.1-flash", "deepseek-v4.1-flash:intl"])
    token = _add_api_key("sk-regression-trae-both", group_id=group_id)

    cn = _RecordingGateway("trae-cn")
    intl = _RecordingGateway("trae-intl")
    requested = "deepseek-v4.1-flash" + suffix
    observed = {"request_model": requested}
    scope, body = _json_scope(
        "/v1/chat/completions",
        {"model": requested, "messages": MESSAGES, "stream": False}, key=token)
    _call(scope, body, observed,
          gateways={"trae-cn": cn, "trae-intl": intl})

    assert observed["status"] == 200, (suffix, observed["response"])
    assert observed["platform"] == expected_platform, (suffix, observed["headers"])
    other = intl if expected_platform == "trae-cn" else cn
    assert other.calls == [], ("另一个区被调用了", suffix, other.calls)
    hit = cn if expected_platform == "trae-cn" else intl
    assert hit.models == [expected_native], (suffix, hit.models)
    assert ":" not in hit.models[0], ("上游串仍带区域标记", hit.models)


if __name__ == "__main__":  # 允许 python -B tests/test_platform_model_routing.py
    _exit_code = pytest.main([__file__, "-q"])
    # pytest 会把这个文件**再导入一次**跑用例；__main__ 这一份自己的临时库要单独清。
    teardown_module()
    raise SystemExit(_exit_code)
