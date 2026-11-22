# PERSISTENT REGRESSION TESTS — keep this file in the repo.
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§五 第 73 行：软健康降级只在硬限制集合内 + 「仅历史失败」的独立分类（t15）。

A. 软降级只影响**顺序**，不影响资格
   1) 健康的候选在前，degraded 追加在后（不是被提到前面）；
   2) 健康候选明确失败后，degraded 仍可作为兜底被调用；
   3) 唯一声明者是 degraded 时仍然尝试它 —— 不得直接回 no_platform。

B. 硬限制的集合不会被降级/排序加回来
   1) 被管理员禁用的平台：即使它健康，degraded 候选失败后也不许重试它；
   2) 分组不允许的平台：同上（分组 Key 的候选池只到组内）。

C. 四类路由结论互不相同（计划 §五 第 73 行）
   - 无人登记              -> 404 model_not_found
   - 声明者全被禁用        -> 403 platform_disabled
   - 存在但当前无可用账号  -> 平台自己的可用性错误（本文件用 503 no_account）
   - 所有候选只有历史失败  -> 503 candidates_degraded（dispatch 侧判定）

隔离：本轮专属临时 SQLite（import admin.db 之前直接赋值，不用 setdefault），
真实表（model_configs / accounts / usage_logs / api_keys / model_groups），
平台侧由本地假网关记录收包。每个用例清表，并清掉 dispatch 与 models_router
的 TTL 缓存（健康度、目录、原生名、平台顺序），避免上一轮污染下一轮。

运行：python -B -m pytest -p no:cacheprovider -q tests/test_dispatch_retry_degraded.py
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_TMP_DIR = Path(tempfile.mkdtemp(prefix="dispatch_degraded_"))
os.environ["ADMIN_DATABASE_URL"] = "sqlite:///" + (_TMP_DIR / "degraded.db").as_posix()
os.environ["QIMEI_VALIDATE_ON_START"] = "0"

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from starlette.responses import JSONResponse  # noqa: E402

from admin import db as admin_db  # noqa: E402
from admin.models import (Account, ApiKey, ModelConfig, ModelGroup,  # noqa: E402
                          ModelGroupItem, UsageLog)
from admin.platform import dispatch, gateway_router, registry  # noqa: E402
from admin.platform import model_suffix, models_router  # noqa: E402
from admin.security import hash_key  # noqa: E402

ALPHA = "alpha"
BETA = "beta"
MODEL = "demo-model"            # alpha 的库内写法（客户端公开名）
BETA_MODEL = "DEMO-MODEL"       # 同一个模型在 beta 的写法（大小写不同）
GHOST = "ghost-model"           # 谁都没登记

KEY_PLAIN = "sk-degraded-plain-0001"
KEY_GROUP = "sk-degraded-group-0001"

SCHEMA = {"done": False}


def _ensure_schema() -> None:
    if SCHEMA["done"]:
        return
    admin_db.Base.metadata.create_all(bind=admin_db.engine)
    SCHEMA["done"] = True


def _reset_caches() -> None:
    models_router.invalidate()
    models_router._native_cache.update({"at": 0.0, "ids": {}})
    dispatch.invalidate_model_cache()
    dispatch.invalidate_platform_cache()
    dispatch._health_cache.clear()
    gateway_router.invalidate()
    model_suffix.invalidate()
    model_suffix._cache.pop("storage", None)


def _reset_world() -> None:
    from sqlalchemy import text

    _ensure_schema()
    with admin_db.engine.begin() as conn:
        for table in ("api_keys", "accounts", "model_configs", "usage_logs",
                      "account_model_cool", "system_settings",
                      "model_group_items", "model_groups"):
            conn.execute(text('DELETE FROM "%s"' % table))
    _reset_caches()


def _seed(*, group_models=None, alpha_account="active") -> None:
    with admin_db.SessionLocal() as db:
        db.add(ModelConfig(platform=ALPHA, model_id=MODEL, enabled=1,
                           credit_multiplier=1.0, note="Demo Model",
                           upstream_metadata=""))
        db.add(ModelConfig(platform=BETA, model_id=BETA_MODEL, enabled=1,
                           credit_multiplier=2.0, note="",
                           upstream_metadata=""))
        db.add_all([
            Account(platform=ALPHA, name="alpha-1", uid="alpha-1",
                    auth_json="{}", status=alpha_account, domain="",
                    balance_total=100, balance_remain=100),
            Account(platform=BETA, name="beta-1", uid="beta-1",
                    auth_json="{}", status="active", domain="",
                    balance_total=100, balance_remain=100),
        ])
        group_id = 0
        if group_models is not None:
            group = ModelGroup(name="degraded-group")
            db.add(group)
            db.flush()
            group_id = int(group.id)
            for item in group_models:
                db.add(ModelGroupItem(group_id=group_id, model_id=item))
        db.add_all([
            ApiKey(name="degraded-plain", key_hash=hash_key(KEY_PLAIN),
                   key_prefix="sk-deg", unlimited=1, status="active",
                   aggregate=True, platform="", group_id=0),
            ApiKey(name="degraded-group", key_hash=hash_key(KEY_GROUP),
                   key_prefix="sk-deg", unlimited=1, status="active",
                   aggregate=True, platform="", group_id=group_id),
        ])
        db.commit()


def _mark_degraded(platform: str, model: str, *, failures: int = 2) -> None:
    # 0 成功 + >=2 失败 => _model_health_ok() 判为「只有历史失败记录」
    with admin_db.SessionLocal() as db:
        for _index in range(failures):
            db.add(UsageLog(api_key_id=0, account_id=0, model=model,
                            platform=platform, error_kind="server",
                            client_ip="127.0.0.1"))
        db.commit()
    dispatch._health_cache.clear()


class _Recorder:
    # 本地假平台网关：记录每次调用的 model，回固定状态码与错误码。

    def __init__(self, platform_id: str, status: int = 200,
                 code: str = "upstream_unavailable"):
        self.platform_id = platform_id
        self.status = status
        self.code = code
        self.calls: list[dict] = []

    async def dispatch(self, request, path):
        raw = await request.body()
        try:
            payload = json.loads(raw) if raw else {}
        except (ValueError, UnicodeDecodeError):
            payload = {}
        self.calls.append({"platform": self.platform_id,
                           "model": str(payload.get("model") or "")})
        if self.status == 200:
            return JSONResponse({"object": "chat.completion",
                                 "platform": self.platform_id,
                                 "model": payload.get("model")})
        return JSONResponse({"error": {"message": "%s failed"
                                            % self.platform_id,
                                       "code": self.code}},
                            status_code=self.status)

    @property
    def models(self) -> list[str]:
        return [call["model"] for call in self.calls]


@contextlib.contextmanager
def _fake_platforms(gateways):
    spec_map = {ALPHA: _FakeSpec(ALPHA), BETA: _FakeSpec(BETA)}
    real = {"get": registry.get, "ids": registry.ids,
            "all": registry.all_platforms, "load": registry.load_gateway}

    def _get(pid):
        key = str(pid or "").strip().lower()
        return spec_map.get(key) or real["get"](pid)

    def _ids(*, enabled_only: bool = True):
        out = list(real["ids"](enabled_only=enabled_only))
        for pid in spec_map:
            if pid not in out:
                out.append(pid)
        return out

    def _all(*args, **kwargs):
        out = list(real["all"](*args, **kwargs))
        seen = {str(getattr(spec, "id", "") or "") for spec in out}
        for pid, spec in spec_map.items():
            if pid not in seen:
                out.append(spec)
        return out

    def _load(spec):
        pid = str(getattr(spec, "id", "") or "").strip().lower()
        if pid in gateways:
            return SimpleNamespace(dispatch=gateways[pid].dispatch)
        return real["load"](spec)

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(registry, "get", _get))
        stack.enter_context(mock.patch.object(registry, "ids", _ids))
        stack.enter_context(mock.patch.object(registry, "all_platforms", _all))
        stack.enter_context(mock.patch.object(registry, "load_gateway", _load))
        yield


class _FakeSpec:
    enabled = True
    realm = ""
    realms = ()

    def __init__(self, pid, realm=""):
        self.id = pid
        self.realm = realm
        self.storage_platform = pid

    def storage(self):
        return self.id

    def default_realm(self):
        return self.realm


def _receive(body: bytes):
    state = {"sent": False}

    async def receive():
        if not state["sent"]:
            state["sent"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return receive


def _request(model: str, key: str):
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": "ping"}],
                       "stream": False}).encode()
    scope = {
        "type": "http", "http_version": "1.1", "method": "POST",
        "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions",
        "query_string": b"", "scheme": "http", "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "headers": [(b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"authorization", ("Bearer " + key).encode())],
    }
    return scope, body


def _run(scope, body: bytes, gateways, *, disabled=None, main_calls=None,
         capture_logs: bool = False):
    main_calls = main_calls if main_calls is not None else []

    async def app(scope_, receive, send):
        main_calls.append("main")
        await JSONResponse({"app": True})(scope_, receive, send)

    messages = []

    async def send(message):
        messages.append(message)

    middleware = dispatch.PlatformDispatchMiddleware(app)
    traffic = SimpleNamespace(annotate=lambda *a, **k: None,
                              mark_stage=lambda *a, **k: None,
                              start=lambda *a, **k: "rid")
    metrics = SimpleNamespace(current=SimpleNamespace(get=lambda: None))
    logs: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record):
            logs.append(record.getMessage())

    target_logger = logging.getLogger("admin.platform.dispatch")
    handler = _Handler(level=logging.INFO)
    old_level = target_logger.level

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.dict(
            sys.modules, {"admin.traffic_stats": traffic,
                          "admin.request_metrics": metrics}))
        stack.enter_context(_fake_platforms(gateways))
        if disabled is not None:
            stack.enter_context(mock.patch.object(
                dispatch, "_disabled_platforms", lambda: set(disabled)))
        if capture_logs:
            target_logger.addHandler(handler)
            target_logger.setLevel(logging.INFO)
            stack.callback(target_logger.removeHandler, handler)
            stack.callback(target_logger.setLevel, old_level)
        asyncio.run(middleware(scope, _receive(body), send))
    return messages, main_calls, logs


def _status(messages) -> int:
    for message in messages:
        if message.get("type") == "http.response.start":
            return int(message.get("status") or 0)
    return 0


def _payload(messages) -> dict:
    raw = b"".join(message.get("body", b"") for message in messages
                   if message.get("type") == "http.response.body")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return {}


def _code(messages) -> str:
    return str((_payload(messages).get("error") or {}).get("code") or "")


def _header(messages, name: str) -> str:
    for message in messages:
        if message.get("type") == "http.response.start":
            for key, value in message.get("headers") or []:
                if key.decode("latin-1").lower() == name:
                    return value.decode("latin-1")
    return ""


class SoftDegradationOrderTests(unittest.TestCase):
    # A. 软降级只影响顺序，不影响资格。

    def test_degraded_candidate_follows_healthy_one_and_is_not_called_first(self):
        _reset_world()
        _seed()
        _mark_degraded(ALPHA, MODEL)
        alpha = _Recorder(ALPHA)
        beta = _Recorder(BETA)
        scope, body = _request(MODEL, KEY_PLAIN)
        messages, main_calls, logs = _run(
            scope, body, {ALPHA: alpha, BETA: beta}, capture_logs=True)

        self.assertEqual(_status(messages), 200)
        self.assertEqual(beta.models, [BETA_MODEL], "健康候选先发")
        self.assertEqual(alpha.calls, [], "degraded 不该被提到前面先发")
        self.assertEqual(main_calls, [])
        attempts = [line for line in logs if "dispatch 尝试 |" in line]
        self.assertEqual(len(attempts), 1, attempts)
        # 1/2 说明候选池里有两个目标，且第一个就是健康的 beta
        self.assertIn("attempt=1/2", attempts[0])
        self.assertIn("public_platform='beta'", attempts[0])
        self.assertEqual(_header(messages, "x-gateway-platform"), BETA)

    def test_degraded_candidate_still_serves_as_fallback(self):
        _reset_world()
        _seed()
        _mark_degraded(ALPHA, MODEL)
        alpha = _Recorder(ALPHA)
        beta = _Recorder(BETA, status=503)
        scope, body = _request(MODEL, KEY_PLAIN)
        messages, main_calls, _logs = _run(scope, body, {ALPHA: alpha, BETA: beta})

        # 健康候选明确 503 之后，degraded 仍有一次机会（不是直接放弃）
        self.assertEqual([call["platform"] for call in beta.calls], [BETA])
        self.assertEqual([call["platform"] for call in alpha.calls], [ALPHA])
        self.assertEqual(alpha.models, [MODEL], "alpha 收到自己的写法")
        self.assertEqual(_status(messages), 200)
        self.assertEqual(_header(messages, "x-gateway-platform"), ALPHA)
        self.assertEqual(main_calls, [])

    def test_sole_degraded_candidate_is_still_attempted(self):
        _reset_world()
        _seed()
        # 只留 alpha 声明这个模型（beta 的目录行不动，但它不认这个名字）
        with admin_db.SessionLocal() as db:
            db.query(ModelConfig).filter(ModelConfig.platform == BETA).delete()
            db.commit()
        _reset_caches()
        _mark_degraded(ALPHA, MODEL)
        alpha = _Recorder(ALPHA)
        scope, body = _request(MODEL, KEY_PLAIN)
        messages, main_calls, _logs = _run(scope, body, {ALPHA: alpha})

        self.assertEqual(alpha.models, [MODEL], "唯一候选即使 degraded 也要试")
        self.assertEqual(_status(messages), 200, _payload(messages))
        self.assertNotEqual(_code(messages), "no_platform")
        self.assertEqual(main_calls, [])


class HardLimitNotRestoredTests(unittest.TestCase):
    # B. 硬限制的集合不会被降级/排序加回来。

    def test_disabled_platform_is_not_retried_even_when_degraded_fails(self):
        _reset_world()
        _seed()
        _mark_degraded(ALPHA, MODEL)
        alpha = _Recorder(ALPHA, status=503)
        beta = _Recorder(BETA)
        scope, body = _request(MODEL, KEY_PLAIN)
        messages, _main, _logs = _run(scope, body, {ALPHA: alpha, BETA: beta},
                                      disabled={BETA})

        self.assertEqual(alpha.models, [MODEL])
        self.assertEqual(beta.calls, [], "被禁用的平台不许被排序加回来")
        self.assertEqual(_status(messages), 503)
        self.assertEqual(_code(messages), "candidates_degraded", _payload(messages))

    def test_platform_outside_group_is_not_retried(self):
        _reset_world()
        _seed(group_models=[MODEL])   # 组里只有 alpha 的行
        _mark_degraded(ALPHA, MODEL)
        alpha = _Recorder(ALPHA, status=503)
        beta = _Recorder(BETA)
        scope, body = _request(MODEL, KEY_GROUP)
        messages, main_calls, _logs = _run(scope, body, {ALPHA: alpha, BETA: beta})

        self.assertEqual(alpha.models, [MODEL])
        self.assertEqual(beta.calls, [], "组外平台不许承接，也不许被降级带回来")
        self.assertEqual(main_calls, [], "主应用不该被调用")
        self.assertEqual(_status(messages), 503)


class ErrorClassificationTests(unittest.TestCase):
    # C. 四类路由结论的 (status, code) 必须互不相同。

    def _observe(self, messages) -> tuple[int, str]:
        return _status(messages), _code(messages)

    def test_four_routing_outcomes_are_pairwise_distinct(self):
        observed: dict[str, tuple[int, str]] = {}

        # 1) 无人登记：任何平台都没有这个模型
        _reset_world()
        _seed()
        alpha = _Recorder(ALPHA)
        beta = _Recorder(BETA)
        scope, body = _request(GHOST, KEY_PLAIN)
        messages, _main, _logs = _run(scope, body, {ALPHA: alpha, BETA: beta})
        observed["none_declared"] = self._observe(messages)

        # 2) 声明者全被管理员禁用
        _reset_world()
        _seed()
        alpha = _Recorder(ALPHA)
        beta = _Recorder(BETA)
        scope, body = _request(MODEL, KEY_PLAIN)
        messages, _main, _logs = _run(scope, body, {ALPHA: alpha, BETA: beta},
                                      disabled={ALPHA, BETA})
        observed["all_disabled"] = self._observe(messages)
        self.assertEqual(alpha.calls + beta.calls, [])

        # 3) 存在但当前无可用账号：账号被停用，平台自己回可用性错误
        _reset_world()
        _seed(alpha_account="disabled")
        with admin_db.SessionLocal() as db:
            db.query(ModelConfig).filter(ModelConfig.platform == BETA).delete()
            db.commit()
        _reset_caches()
        alpha = _Recorder(ALPHA, status=503, code="no_account")
        scope, body = _request(MODEL, KEY_PLAIN)
        messages, _main, _logs = _run(scope, body, {ALPHA: alpha})
        observed["no_usable_account"] = self._observe(messages)
        # 「没有可用账号」仍会把请求交给该平台，由它回自己的可用性错误
        # （计划 §五 第 73 行的括注：账号池/平台的可用性错误）。
        self.assertEqual(len(alpha.calls), 1, "可用性错误由平台给出")

        # 4) 所有候选只有历史失败记录：全部 degraded 且都明确失败
        _reset_world()
        _seed()
        _mark_degraded(ALPHA, MODEL)
        _mark_degraded(BETA, BETA_MODEL)
        alpha = _Recorder(ALPHA, status=503)
        beta = _Recorder(BETA, status=503)
        scope, body = _request(MODEL, KEY_PLAIN)
        messages, _main, logs = _run(scope, body, {ALPHA: alpha, BETA: beta},
                                     capture_logs=True)
        observed["only_degraded"] = self._observe(messages)

        tuples = set(observed.values())
        self.assertEqual(len(tuples), 4,
                         "四类结论塌成了同一组 (status, code)：%r" % (observed,))
        self.assertEqual(observed["none_declared"], (404, "model_not_found"))
        self.assertEqual(observed["all_disabled"], (403, "platform_disabled"))
        self.assertEqual(observed["no_usable_account"], (503, "no_account"))
        self.assertEqual(observed["only_degraded"],
                         (503, "candidates_degraded"))
        # 分类还要能被日志读出：两个 degraded 都试过，attempts 顺序与状态留痕
        self.assertEqual(len(alpha.calls), 1)
        self.assertEqual(len(beta.calls), 1)
        degraded_logs = [line for line in logs
                         if "候选平台都只有历史失败记录" in line]
        self.assertEqual(len(degraded_logs), 1, logs)
        body_json = _payload(messages)["error"]
        # 两个候选都是 degraded：首选来自 degraded 池自身的策略顺序
        # （倍率升序 -> alpha 先），attempts 如实记录这个顺序。
        self.assertEqual([item["platform"] for item in body_json["attempts"]],
                         [ALPHA, BETA], "attempts 要按实际尝试顺序记录")
        self.assertEqual({item["status"] for item in body_json["attempts"]},
                         {503})


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))