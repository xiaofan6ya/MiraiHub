# -*- coding: utf-8 -*-
"""AStudio 协议契约测试（模块内，离线跑）。

    ../venv/bin/python -m pytest admin/platform/astudio/tests -q -p no:cacheprovider

全部用例不触网：HTTP 层用 httpx.MockTransport 假上游（断言**实际发出的
请求形状**与**响应解析**），凭证路径用临时目录造假文件，数据库用假对象。

覆盖：
  1. 凭证导入（幂等 / 只有模型凭据 / 粘贴形状）
  2. 本机扫描（session.json / config.toml / 无网页会话标注）
  3. 模型目录解析（补位 / 下架不复活 / 兜底静态表 / TTL 缓存）
  4. 额度与用量字段映射（Responses → chat 口径）
  5. 错误分类（模型网关 401/403/429；账号面信封 80000/业务码）
  6. 签到判定（已领零写 / 可领 / 无弹窗 / 无会话）
  7. 网关转发（三入口 × 非流式 + 流式帧解析）
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent.parent.parent))

import httpx  # noqa: E402
import pytest  # noqa: E402

import admin.db as admin_db  # noqa: E402
from admin.platform import shared  # noqa: E402
from admin.platform.astudio import gateway as astudio_gateway  # noqa: E402
from admin.platform.astudio.core import client, paths  # noqa: E402
from admin.platform.astudio.services import accounts, checkin  # noqa: E402

MODEL = "spark-x2.6-4b"


# ---------------------------------------------------------------------------
# 通用假件与 patch 工具
# ---------------------------------------------------------------------------
class _Missing:
    pass


_MISSING = _Missing()


@contextlib.contextmanager
def _patched():
    saved = []

    def setp(obj, name, value):
        try:
            prev = getattr(obj, name)
        except AttributeError:
            prev = _MISSING
        saved.append((obj, name, prev))
        setattr(obj, name, value)

    try:
        yield setp
    finally:
        for obj, name, prev in reversed(saved):
            if prev is _MISSING:
                try:
                    delattr(obj, name)
                except AttributeError:
                    pass
            else:
                setattr(obj, name, prev)


class FakeKey:
    id = 1
    name = "test-key"


class FakeAccount:
    id = 7
    uid = "18800000000"
    name = "astudio-test"
    auth_json = json.dumps({"key": "fake-jwt", "uid": "18800000000",
                            "session": {"token": "sess", "account_id": "7",
                                        "sso_session_id": "sso7"}},
                           ensure_ascii=False)


class FakeDB:
    def close(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def patch_db(setp):
    setp(admin_db, "SessionLocal", lambda: FakeDB())


def patch_shared(setp):
    setp(shared, "authenticate", lambda db, request, platform="": FakeKey())
    setp(shared, "locate", lambda request, fallback: (fallback, "cn", None))
    setp(shared, "select_account", lambda db, *a, **k: FakeAccount())
    setp(shared, "lease", lambda account: True)
    setp(shared, "release", lambda account: None)
    setp(shared, "note_success", lambda account: None)
    setp(shared, "note_failure", lambda *a, **k: None)
    setp(shared, "key_row", lambda db, request: FakeKey())
    setp(shared, "record_usage", lambda *a, **k: None)


def make_request(body: bytes, path: str = "/v1/chat/completions"):
    from starlette.requests import Request

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {"type": "http", "method": "POST", "path": path,
             "headers": [(b"content-type", b"application/json"),
                         (b"authorization", b"Bearer sk-test")],
             "query_string": b"", "server": ("127.0.0.1", 80)}
    return Request(scope, receive)


async def drain(response) -> str:
    out = []
    async for piece in response.body_iterator:
        out.append(piece.decode("utf-8", "replace")
                   if isinstance(piece, (bytes, bytearray)) else str(piece))
    return "".join(out)


def parse_sse_events(text: str) -> list:
    events = []
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            events.append(json.loads(payload))
        except ValueError:
            pass
    return events


#: 捕获到的上游请求 / 可编程回放数据（每用例清空）。
UPSTREAM: dict = {}


def mock_transport_client_factory(handler):
    # 注意：`client.httpx` 就是 httpx 模块本体 —— patch 发生前先捕获
    # 原 Client 类，否则工厂里 `httpx.Client(...)` 会递归到自己。
    original = httpx.Client

    def _factory(*args, **kwargs):
        kwargs.pop("transport", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)
    return _factory


def _sse(events: list) -> bytes:
    lines = []
    for ev in events:
        lines.append("event: %s" % ev["type"])
        lines.append("data: %s" % json.dumps(ev, ensure_ascii=False))
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


def _grant_row(amount=100):
    return {"direction": "CREDIT", "operation": "GRANT",
            "reason": "每日登录赠送", "amount": amount,
            "expireTime": "2026-12-31 00:00:00"}


def _stream_events() -> list:
    return [
        {"type": "response.output_text.delta", "delta": "你好"},
        {"type": "response.reasoning_summary_text.delta", "delta": "先想想"},
        {"type": "response.output_item.added", "output_index": 1,
         "item": {"type": "function_call", "id": "fc_1", "call_id": "call_9",
                  "name": "read", "arguments": ""}},
        {"type": "response.function_call_arguments.delta", "output_index": 1,
         "delta": '{"path": '},
        {"type": "response.function_call_arguments.delta", "output_index": 1,
         "delta": '"/x"}'},
        {"type": "response.completed",
         "response": {"id": "resp_1", "object": "response",
                      "status": "completed", "output": [],
                      "usage": {"input_tokens": 10, "output_tokens": 4,
                                "total_tokens": 14}}},
    ]


def gateway_handler(request: httpx.Request) -> httpx.Response:
    """假上游：模型网关 / 目录 / 账号面 按 URL 分发。"""
    url = str(request.url)
    UPSTREAM["method"] = request.method
    UPSTREAM["url"] = url
    UPSTREAM["headers"] = dict(request.headers)
    if request.content:
        try:
            UPSTREAM["body"] = json.loads(request.content)
        except ValueError:
            UPSTREAM["body"] = request.content.decode("utf-8", "replace")
    if "/model-manager/models" in url:
        return httpx.Response(200, json={"code": 0, "data": {"models": [
            {"slug": "spark-x2.6-4b", "display_name": "Spark-X2.6-Lite",
             "context_window": 1048576, "max_output_tokens": 110000,
             "input_modalities": ["text"], "visibility": "list",
             "supported_in_api": True,
             "badge": {"text": {"en": "Free"}},
             "supported_reasoning_levels": [
                 {"effort": "none"}, {"effort": "high"}]}]}})
    if "/model-gateway/v1/responses" in url:
        body = UPSTREAM.get("body") or {}
        if body.get("stream"):
            return httpx.Response(200, content=_sse(_stream_events()),
                                  headers={"Content-Type": "text/event-stream"})
        return httpx.Response(200, json={
            "id": "resp_1", "object": "response", "created_at": 1700000000,
            "status": "completed", "model": MODEL,
            "output": [
                {"type": "reasoning", "id": "rs_1",
                 "summary": [{"type": "summary_text", "text": "先想想"}]},
                {"type": "message", "id": "msg_1", "role": "assistant",
                 "content": [{"type": "output_text", "text": "完成"}]},
                {"type": "function_call", "id": "fc_1", "call_id": "call_9",
                 "name": "read", "arguments": "{\"path\": \"/x\"}"},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 4,
                      "total_tokens": 14}})
    if "xingchen-studio" in url:
        path = url.split("xingchen-studio/", 1)[-1].split("?", 1)[0]
        if path == "points/details":
            items = list(UPSTREAM.get("ledger", []))
            if UPSTREAM.get("completed"):
                # complete 之后复读：入账 GRANT 行出现
                items = items + [_grant_row()]
            return httpx.Response(200, json={
                "flag": True, "code": 0, "desc": "ok",
                "data": {"items": items}})
        if path == "client-popups/pending":
            return httpx.Response(200, json={
                "flag": True, "code": 0, "desc": "ok",
                "data": {"popups": UPSTREAM.get("popups", [])}})
        if path == "client-popups/complete":
            UPSTREAM["completed"] = UPSTREAM.get("body")
            return httpx.Response(200, json={
                "flag": True, "code": 0, "desc": "ok", "data": {}})
        if path == "membership/plan-config":
            return httpx.Response(200, json={
                "flag": True, "code": 0, "desc": "ok",
                "data": {"dailyLoginEnabled": True}})
        if path == "points/balance":
            return httpx.Response(200, json={
                "flag": True, "code": 0, "desc": "ok",
                "data": {"totalBalance": 8000, "sparkTotalBalance": 1200,
                         "activityBalance": 300,
                         "activityNextExpireTime": "2026-12-31 00:00:00"}})
        if path == "membership/me":
            return httpx.Response(200, json={
                "flag": True, "code": 0, "desc": "ok",
                "data": {"planName": "体验版", "uid": "18800000000"}})
        return httpx.Response(200, json={"flag": True, "code": 0,
                                         "desc": "ok", "data": {}})
    return httpx.Response(404, json={"error": {"message": "not found"}})


@pytest.fixture()
def fake_upstream(monkeypatch):
    """把 client 模块的 httpx.Client 换成 MockTransport 假上游。"""
    monkeypatch.setattr(client.httpx, "Client",
                        mock_transport_client_factory(gateway_handler))
    UPSTREAM.clear()
    return UPSTREAM


def _session() -> dict:
    return {"token": "web-token", "account_id": "18800000000",
            "sso_session_id": "sso-1"}


# ---------------------------------------------------------------------------
# 1. 凭证导入
# ---------------------------------------------------------------------------
class _FakeRow:
    """最小 Account 双胞胎（不引 ORM）。"""

    def __init__(self):
        self.id = 1
        self.platform = "astudio"
        self.uid = ""
        self.name = ""
        self.domain = ""
        self.auth_json = ""
        self.status = "active"
        self.balance_total = 0
        self.balance_remain = 0
        self.credits_snapshot = ""
        self.credits_expiring = 0
        self.last_sync_at = None
        self.balance_synced_at = None
        self.created_at = None
        self.last_err_msg = ""


class _FakeQuery:
    def __init__(self, db, model):
        self.db = db
        self.model = model
        self._preds = []

    def filter(self, *a, **k):
        self._preds.extend(a)
        return self

    def order_by(self, *a):
        return self

    def _match(self, row) -> bool:
        for pred in self._preds:
            col = getattr(getattr(pred, "left", None), "key", None)
            value = getattr(getattr(pred, "right", None), "value", None)
            if col == "platform" and row.platform != value:
                return False
            if col == "uid" and row.uid != value:
                return False
            if col == "id" and row.id != value:
                return False
        return True

    def first(self):
        rows = [r for r in self.db.rows if self._match(r)]
        return rows[0] if rows else None

    def all(self):
        return [r for r in self.db.rows if self._match(r)]


class _FakeDB:
    """记录 upsert 行为的假会话。"""

    def __init__(self):
        self.rows: list[_FakeRow] = []
        self.commits = 0

    def add(self, row):
        self.rows.append(row)

    def delete(self, row):
        if row in self.rows:
            self.rows.remove(row)

    def commit(self):
        self.commits += 1

    def refresh(self, row):
        return row

    def rollback(self):
        pass

    def close(self):
        pass

    def query(self, model):
        return _FakeQuery(self, model)


def test_import_record_with_session(fake_upstream):
    account, created = accounts.import_record(
        _FakeDB(), {"key": "jwt-1", "session_token": "t1",
                    "account_id": "10001", "uid": "18810000001",
                    "source": "import"})
    assert created is True
    data = accounts.credentials_of(account)
    assert data["key"] == "jwt-1"
    assert accounts.session_of(data)["account_id"] == "10001"


def test_import_record_model_token_only(fake_upstream):
    account, created = accounts.import_record(
        _FakeDB(), {"modelBearerToken": "jwt-2", "source": "paste"})
    assert created is True
    data = accounts.credentials_of(account)
    assert data["key"] == "jwt-2"
    assert accounts.session_of(data) is None


def test_import_record_bare_token_is_model_key(fake_upstream):
    account, _created = accounts.import_record(
        _FakeDB(), {"token": "jwt-3", "uid": "18810000003"})
    data = accounts.credentials_of(account)
    assert data["key"] == "jwt-3"
    assert accounts.session_of(data) is None


def test_import_record_missing_key_rejected():
    with pytest.raises(ValueError) as caught:
        accounts.import_record(_FakeDB(),
                               {"token": "t1", "account_id": "10001"})
    assert "缺少模型凭据" in str(caught.value)


def test_import_record_same_uid_is_upsert(fake_upstream):
    db = _FakeDB()
    _first, created = accounts.import_record(
        db, {"key": "jwt-a", "session_token": "t1", "account_id": "1",
             "uid": "same-uid"})
    second, created2 = accounts.import_record(
        db, {"key": "jwt-b", "session_token": "t2", "account_id": "1",
             "uid": "same-uid"})
    assert created is True and created2 is False
    assert accounts.token_of(accounts.credentials_of(second)) == "jwt-b"


def test_import_batch_jsonl(fake_upstream):
    text = "\n".join([
        json.dumps({"key": "jwt-x", "uid": "u-x"}),
        json.dumps({"modelBearerToken": "jwt-y", "token": "ty",
                    "accountId": "9", "uid": "u-y"}),
    ])
    result = accounts.import_batch(_FakeDB(), text)
    assert result["ok"] is True
    assert result["added"] == 2


# ---------------------------------------------------------------------------
# 2. 本机扫描（临时目录造假文件）
# ---------------------------------------------------------------------------
def _scan_env(tmp_path, monkeypatch):
    root = tmp_path / "astudio-data"
    (root / "userdata").mkdir(parents=True)
    (root / "runtime" / "acode-home").mkdir(parents=True)
    monkeypatch.setenv("ASTUDIO_HOME", str(root))
    monkeypatch.delenv("SYNARA_HOME", raising=False)
    # 隔离兜底目录：这台机器可能真有 ~/.acode/acode 登录态，测试不能被污染
    monkeypatch.setattr(paths, "FALLBACK_DATA_ROOT",
                        str(tmp_path / "fallback-home"))
    return root


def test_scan_session_json(tmp_path, monkeypatch):
    root = _scan_env(tmp_path, monkeypatch)
    (root / "userdata" / "astron-session.json").write_text(json.dumps({
        "modelBearerToken": "jwt-scan", "token": "web-t",
        "ssoSessionId": "sso-s", "accountId": "18800000000",
        "uid": "18800000000",
    }), encoding="utf-8")
    items = paths.discover()
    assert len(items) == 1
    item = items[0]
    assert item["has_plain_credential"] is True
    assert item["has_session"] is True
    assert item["uid_full"] == "18800000000"
    record = paths.read_session(item["path"])
    assert record["key"] == "jwt-scan"
    assert record["session"]["account_id"] == "18800000000"


def test_scan_config_toml_only_token(tmp_path, monkeypatch):
    root = _scan_env(tmp_path, monkeypatch)
    (root / "runtime" / "acode-home" / "config.toml").write_text(
        "[model_providers.astron-spark]\n"
        'experimental_bearer_token = "jwt-cfg"\n'
        'uid = "18800000001"\n', encoding="utf-8")
    items = paths.discover()
    assert len(items) == 1
    item = items[0]
    assert item["has_session"] is False
    assert "无网页会话" in (item.get("note") or "")
    record = paths.read_config_token(item["path"])
    assert record["key"] == "jwt-cfg"
    assert record["session"] is None


def test_scan_missing_web_session_marked(tmp_path, monkeypatch):
    root = _scan_env(tmp_path, monkeypatch)
    (root / "userdata" / "astron-session.json").write_text(json.dumps({
        "modelBearerToken": "jwt-only",
    }), encoding="utf-8")
    items = paths.discover()
    assert len(items) == 1
    assert items[0]["has_session"] is False
    assert "只有模型凭据" in (items[0].get("note") or "")


def test_scan_broken_json_reported_not_fatal(tmp_path, monkeypatch):
    root = _scan_env(tmp_path, monkeypatch)
    (root / "userdata" / "astron-session.json").write_text("{not-json",
                                                           encoding="utf-8")
    items = paths.discover()
    assert len(items) == 1
    assert "不是有效 JSON" in (items[0].get("problem") or "")


def test_scan_env_root_takes_precedence(tmp_path, monkeypatch):
    root = _scan_env(tmp_path, monkeypatch)
    (root / "userdata" / "astron-session.json").write_text(json.dumps({
        "modelBearerToken": "jwt-env"}), encoding="utf-8")
    assert paths.data_roots()[0] == str(root)
    items = paths.discover()
    found = [it for it in items if it["file"].startswith(str(root))]
    assert found, "env 目录的登录态没有被扫到"


def test_scan_import_local(tmp_path, monkeypatch, fake_upstream):
    root = _scan_env(tmp_path, monkeypatch)
    (root / "userdata" / "astron-session.json").write_text(json.dumps({
        "modelBearerToken": "jwt-local", "token": "web-t",
        "accountId": "18800000002", "uid": "18800000002",
    }), encoding="utf-8")
    result = accounts.import_local(_FakeDB())
    assert result["imported"] == 1
    assert result["added"] == 1


# ---------------------------------------------------------------------------
# 3. 模型目录解析（补位 / 兜底 / TTL）
# ---------------------------------------------------------------------------
def test_catalog_row_maps_live_shape():
    row = astudio_gateway._catalog_row({
        "slug": "spark-x2.6-4b", "display_name": "Spark-X2.6-Lite",
        "context_window": 1048576, "max_output_tokens": 110000,
        "input_modalities": ["text"], "visibility": "list",
        "supported_in_api": True,
        "badge": {"text": {"en": "Free"}},
        "supported_reasoning_levels": [{"effort": "none"}, {"effort": "high"}],
    })
    assert row["id"] == "spark-x2.6-4b"
    assert row["contextWindow"] == 1048576
    assert row["maxOutputTokens"] == 110000
    assert row["free"] is True
    assert row["reasoning"] is True
    assert row["input"] == ["text"]


def test_catalog_extra_merged_when_slug_missing(monkeypatch):
    entries = [{"slug": "astronclaw-auto", "display_name": "Auto",
                "context_window": 262144, "max_output_tokens": 32768,
                "visibility": "list", "supported_in_api": True,
                "supported_reasoning_levels": [{"effort": "high"}]}]
    monkeypatch.setattr(client, "fetch_catalog", lambda token, **k: entries)
    rows = astudio_gateway._catalog_cached(41, "jwt")
    slugs = {row["id"] for row in rows}
    assert "astronclaw-auto" in slugs
    assert "xdeepseek-flash-in" in slugs, "补位表没有补上目录缺失的模型"


def test_catalog_directory_wins_over_extra(monkeypatch):
    """目录一旦列出同名（哪怕被 hidden 过滤）就以上游为准。"""
    entries = [
        {"slug": "xdeepseek-flash-in", "display_name": "目录版",
         "context_window": 4096, "max_output_tokens": 512,
         "visibility": "hidden", "supported_in_api": True,
         "supported_reasoning_levels": [{"effort": "none"}]},
        {"slug": "spark-x2.6", "display_name": "Spark-X2.6",
         "context_window": 262144, "max_output_tokens": 32768,
         "visibility": "list", "supported_in_api": True,
         "supported_reasoning_levels": [{"effort": "high"}]},
    ]
    monkeypatch.setattr(client, "fetch_catalog", lambda token, **k: entries)
    rows = astudio_gateway._catalog_cached(42, "jwt")
    slugs = {row["id"] for row in rows}
    assert "xdeepseek-flash-in" not in slugs, "hidden 下架的模型被复活了"
    assert "spark-x2.6" in slugs


def test_catalog_error_falls_back_to_static(monkeypatch):
    def _boom(token, **k):
        raise client.UpstreamError("目录读失败", kind="upstream")

    monkeypatch.setattr(client, "fetch_catalog", _boom)
    rows = astudio_gateway._catalog_cached(43, "jwt")
    slugs = {row["id"] for row in rows}
    assert "xdeepseek-flash-in" in slugs
    assert "astronclaw-auto" in slugs


def test_catalog_ttl_cache(monkeypatch):
    calls = {"n": 0}

    def _fetch(token, **k):
        calls["n"] += 1
        return [{"slug": "spark-x2.6", "display_name": "S",
                 "context_window": 1, "max_output_tokens": 2,
                 "visibility": "list", "supported_in_api": True,
                 "supported_reasoning_levels": []}]

    monkeypatch.setattr(client, "fetch_catalog", _fetch)
    astudio_gateway._catalog_cached(44, "jwt")
    astudio_gateway._catalog_cached(44, "jwt")
    assert calls["n"] == 1, "TTL 窗口内重复读目录了"


# ---------------------------------------------------------------------------
# 4. 额度与用量字段映射
# ---------------------------------------------------------------------------
def test_usage_responses_to_chat_maps_cached_and_reasoning():
    usage = astudio_gateway._usage_to_chat({
        "input_tokens": 100, "output_tokens": 30, "total_tokens": 130,
        "input_tokens_details": {"cached_tokens": 40},
        "output_tokens_details": {"reasoning_tokens": 12},
    })
    assert usage["prompt_tokens"] == 100
    assert usage["completion_tokens"] == 30
    assert usage["prompt_tokens_details"]["cached_tokens"] == 40
    assert usage["completion_tokens_details"]["reasoning_tokens"] == 12


def test_responses_to_chat_full_mapping():
    chat = astudio_gateway._responses_to_chat({
        "id": "resp_9", "created_at": 1700000000, "status": "completed",
        "output": [
            {"type": "reasoning", "summary": [
                {"type": "summary_text", "text": "先想想"}]},
            {"type": "message", "content": [
                {"type": "output_text", "text": "完成"}]},
            {"type": "function_call", "call_id": "call_9", "name": "read",
             "arguments": "{\"path\": \"/x\"}"},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 4},
    }, MODEL)
    msg = chat["choices"][0]["message"]
    assert msg["content"] == "完成"
    assert msg["reasoning_content"] == "先想想"
    assert msg["tool_calls"][0]["id"] == "call_9"
    assert chat["choices"][0]["finish_reason"] == "tool_calls"
    assert chat["usage"]["prompt_tokens"] == 10


def test_responses_to_chat_incomplete_maps_length():
    chat = astudio_gateway._responses_to_chat({
        "id": "resp_10", "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "到截断"}]}],
        "usage": {"input_tokens": 1, "output_tokens": 2},
    }, MODEL)
    assert chat["choices"][0]["finish_reason"] == "length"


def test_responses_to_chat_failed_raises():
    with pytest.raises(client.UpstreamError) as caught:
        astudio_gateway._responses_to_chat({
            "id": "resp_11", "status": "failed",
            "error": {"message": "额度不足"}, "output": []}, MODEL)
    assert "额度不足" in caught.value.message


# ---------------------------------------------------------------------------
# 5. 错误分类
# ---------------------------------------------------------------------------
def test_gateway_401_is_auth():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "unauthorized"}})

    with _patched() as setp:
        setp(client.httpx, "Client",
             mock_transport_client_factory(handler))
        with pytest.raises(client.UpstreamError) as caught:
            client.request_responses("jwt", {"model": MODEL})
    assert caught.value.kind == "auth"
    assert caught.value.status == 401


def test_gateway_429_is_rate():
    def handler(request):
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    with _patched() as setp:
        setp(client.httpx, "Client",
             mock_transport_client_factory(handler))
        with pytest.raises(client.UpstreamError) as caught:
            client.request_responses("jwt", {"model": MODEL})
    assert caught.value.kind == "rate"


def test_gateway_403_quota_hint_is_quota():
    def handler(request):
        return httpx.Response(403, json={"error": {
            "message": "insufficient credits"}})

    with _patched() as setp:
        setp(client.httpx, "Client",
             mock_transport_client_factory(handler))
        with pytest.raises(client.UpstreamError) as caught:
            client.request_responses("jwt", {"model": MODEL})
    assert caught.value.kind == "quota"


def test_studio_http_401_is_auth():
    def handler(request):
        return httpx.Response(401, json={"flag": False, "code": 80000,
                                         "desc": "重新登录", "data": None})

    with _patched() as setp:
        setp(client.httpx, "Client",
             mock_transport_client_factory(handler))
        with pytest.raises(client.UpstreamError) as caught:
            client.fetch_balance(_session())
    assert caught.value.kind == "auth"


def test_studio_envelope_code_80000_is_auth():
    def handler(request):
        return httpx.Response(200, json={"flag": True, "code": 80000,
                                         "desc": "登录状态异常，请重新登录",
                                         "data": None})

    with _patched() as setp:
        setp(client.httpx, "Client",
             mock_transport_client_factory(handler))
        with pytest.raises(client.UpstreamError) as caught:
            client.fetch_balance(_session())
    assert caught.value.kind == "auth"


def test_studio_envelope_biz_error():
    def handler(request):
        return httpx.Response(200, json={"flag": False, "code": 50001,
                                         "desc": "业务拒绝", "data": None})

    with _patched() as setp:
        setp(client.httpx, "Client",
             mock_transport_client_factory(handler))
        with pytest.raises(client.UpstreamError) as caught:
            client.fetch_balance(_session())
    assert caught.value.kind == "upstream"
    assert "业务拒绝" in caught.value.message


def test_studio_request_carries_identity_headers(fake_upstream):
    """clientType / studioVersion / Cookie（ssoSessionId 双份）都要带上。"""
    client.fetch_balance(_session())
    headers = UPSTREAM["headers"]
    assert headers.get("clienttype") in ("21", "22"), headers
    assert headers.get("studioversion"), headers
    cookie = headers.get("cookie", "")
    assert "ssoSessionId=sso-1" in cookie
    assert "sso_sessionid=sso-1" in cookie
    assert "account_id=18800000000" in cookie
    assert "token=web-token" in cookie


# ---------------------------------------------------------------------------
# 6. 签到判定（已领 / 可领 / 无弹窗 / 无会话）
# ---------------------------------------------------------------------------
def test_checkin_already_granted_zero_write(fake_upstream):
    """流水已有 GRANT → done，且一个写请求都不发。"""
    fake_upstream["ledger"] = [_grant_row()]
    result = checkin.checkin_one(_FakeDB(), FakeAccount())
    assert result["outcome"] == "done"
    assert result["credit"] == 100
    assert "completed" not in fake_upstream, "已发放还发了写请求"


def test_checkin_claimable_flow(fake_upstream, monkeypatch):
    """流水为空 + pending 有每日奖励 → complete 一次 → 复读流水认定。"""
    monkeypatch.setattr(checkin.time, "sleep", lambda s: None)
    fake_upstream["ledger"] = []
    fake_upstream["popups"] = [
        {"componentType": "DOWNLOAD_REWARD_DIALOG", "popupId": 3,
         "instanceKey": "dl-1"},
        {"componentType": "DAILY_REWARD_DIALOG", "popupId": 7,
         "instanceKey": "daily-2026-11-23"},
    ]
    result = checkin.checkin_one(_FakeDB(), FakeAccount())
    assert result["outcome"] == "claimed", result
    assert result["credit"] == 100
    completed = fake_upstream.get("completed")
    assert completed == {"popupId": 7, "instanceKey": "daily-2026-11-23"}, \
        "complete 的不是每日奖励那条弹窗"


def test_checkin_no_popup_is_inactive(fake_upstream):
    fake_upstream["ledger"] = []
    fake_upstream["popups"] = [
        {"componentType": "DOWNLOAD_REWARD_DIALOG", "popupId": 3,
         "instanceKey": "dl-1"},
    ]
    result = checkin.checkin_one(_FakeDB(), FakeAccount())
    assert result["outcome"] == "inactive"
    assert "completed" not in fake_upstream, "非每日弹窗被 complete 了"


def test_checkin_no_session_ineligible(fake_upstream):
    account = _FakeRow()
    account.auth_json = json.dumps({"key": "jwt-only"})
    result = checkin.checkin_one(_FakeDB(), account)
    assert result["outcome"] == "ineligible"


def test_checkin_popup_filter_requires_positive_int_and_key(fake_upstream):
    fake_upstream["ledger"] = []
    fake_upstream["popups"] = [
        {"componentType": "DAILY_REWARD_DIALOG", "popupId": "7",
         "instanceKey": "k"},          # popupId 不是整数
        {"componentType": "DAILY_REWARD_DIALOG", "popupId": 7,
         "instanceKey": "  "},          # instanceKey 空白
    ]
    result = checkin.checkin_one(_FakeDB(), FakeAccount())
    assert result["outcome"] == "inactive"


# ---------------------------------------------------------------------------
# 7. 网关转发：请求体转换 + 流式帧解析
# ---------------------------------------------------------------------------
def test_chat_completions_nonstream_request_and_response(fake_upstream):
    """chat 请求 → 上游收到 Responses 形状；响应翻回 chat.completion。"""
    body = {
        "model": MODEL, "stream": False,
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
            {"role": "assistant",
             "tool_calls": [{"id": "call_1", "type": "function",
                             "function": {"name": "read",
                                          "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "data"},
        ],
        "tools": [{"type": "function",
                   "function": {"name": "read",
                                "parameters": {"type": "object"}}}],
        "max_tokens": 64,
    }
    req = make_request(json.dumps(body).encode(), path="/v1/chat/completions")
    with _patched() as setp:
        patch_db(setp)
        patch_shared(setp)
        setp(astudio_gateway, "_bill", lambda *a, **k: None)
        resp = asyncio.run(astudio_gateway.chat_completions(req))
        assert resp.status_code == 200, getattr(resp, "status_code", None)
        out = json.loads(resp.body)
        msg = out["choices"][0]["message"]
        assert msg["content"] == "完成"
        assert msg["reasoning_content"] == "先想想"
        assert msg["tool_calls"][0]["function"]["name"] == "read"
        assert out["choices"][0]["finish_reason"] == "tool_calls"
        assert out["usage"]["prompt_tokens"] == 10
        # 请求方向：上游收到的是 Responses 形状
        up = fake_upstream["body"]
        assert up["model"] == MODEL
        assert up["instructions"] == "be brief"
        assert up["max_output_tokens"] == 64
        blob = json.dumps(up["input"], ensure_ascii=False)
        assert '"type": "function_call_output"' in blob, "tool 消息没转输出项"
        assert '"type": "function_call"' in blob, "assistant 历史没转函数调用项"
        assert up["tools"][0]["name"] == "read", "tools 没转 Responses 形状"


def test_responses_nonstream_passthrough(fake_upstream):
    """/v1/responses 原生透传：上游对象原样回。"""
    body = {"model": MODEL, "stream": False,
            "input": [{"role": "user", "content": "hi"}],
            "instructions": "sys"}
    req = make_request(json.dumps(body).encode(), path="/v1/responses")
    with _patched() as setp:
        patch_db(setp)
        patch_shared(setp)
        setp(astudio_gateway, "_bill", lambda *a, **k: None)
        resp = asyncio.run(astudio_gateway.responses(req))
        assert resp.status_code == 200, getattr(resp, "status_code", None)
        out = json.loads(resp.body)
        assert out["object"] == "response"
        assert out["status"] == "completed"
        fcs = [o for o in out["output"] if o.get("type") == "function_call"]
        assert fcs and fcs[0]["call_id"] == "call_9"
        assert out["usage"]["input_tokens"] == 10


def test_messages_nonstream_anthropic_shape(fake_upstream):
    """/v1/messages：anthropic 请求 → Responses 上游 → anthropic 响应。"""
    body = {
        "model": MODEL, "max_tokens": 100, "stream": False,
        "system": "be brief",
        "tools": [{"name": "read", "description": "read",
                   "input_schema": {"type": "object",
                                    "properties": {"path": {"type": "string"}}}}],
        "messages": [
            {"role": "user", "content": "读一下 /x"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "read",
                 "input": {"path": "/x"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "file content"}]},
        ],
    }
    req = make_request(json.dumps(body).encode(), path="/v1/messages")
    with _patched() as setp:
        patch_db(setp)
        patch_shared(setp)
        setp(astudio_gateway, "_bill", lambda *a, **k: None)
        resp = asyncio.run(astudio_gateway.messages(req))
        assert resp.status_code == 200, getattr(resp, "status_code", None)
        out = json.loads(resp.body)
        assert out["type"] == "message"
        assert out["role"] == "assistant"
        thinking = [b for b in out.get("content") or []
                    if b.get("type") == "thinking"]
        assert thinking, "思考链丢失"
        texts = [b.get("text") for b in out.get("content") or []
                 if b.get("type") == "text"]
        assert "完成" in "".join(texts)
        calls = [b for b in out.get("content") or []
                 if b.get("type") == "tool_use"]
        assert calls and calls[0]["name"] == "read"
        assert out["stop_reason"] == "tool_use"
        # 请求方向：工具调用历史完整到达上游
        up = fake_upstream["body"]
        blob = json.dumps(up["input"], ensure_ascii=False)
        assert "read" in blob, "工具定义/调用历史丢失"
        assert "file content" in blob, "tool_result 内容丢失"


def test_chat_stream_frames(fake_upstream):
    """chat 流式：Responses 事件 → OpenAI chunk（正文/工具/usage/[DONE]）。"""
    body = {"model": MODEL, "stream": True,
            "messages": [{"role": "user", "content": "你好"}]}
    req = make_request(json.dumps(body).encode(), path="/v1/chat/completions")
    with _patched() as setp:
        patch_db(setp)
        patch_shared(setp)
        setp(astudio_gateway, "_bill", lambda *a, **k: None)
        resp = asyncio.run(astudio_gateway.chat_completions(req))
        assert resp.status_code == 200, getattr(resp, "status_code", None)
        text = asyncio.run(drain(resp))
        events = parse_sse_events(text)
        assert events, "没有可解析的 chunk"
        assert events[-1]["choices"][0]["finish_reason"] == "stop"
        deltas = "".join(e["choices"][0]["delta"].get("content") or ""
                         for e in events if e.get("choices"))
        assert "你好" in deltas
        calls = [e for e in events
                 if e.get("choices")
                 and e["choices"][0].get("delta", {}).get("tool_calls")]
        assert calls, "function_call 没进 chunk 流"
        assert calls[0]["choices"][0]["delta"]["tool_calls"][0]["id"] == "call_9"
        args = "".join(
            c["choices"][0]["delta"]["tool_calls"][0]["function"]
            .get("arguments") or ""
            for c in calls)
        assert json.loads(args) == {"path": "/x"}, args
        usage_frames = [e for e in events if e.get("usage")]
        assert usage_frames and usage_frames[-1]["usage"]["prompt_tokens"] == 10
        assert text.rstrip().endswith("data: [DONE]")
        # 请求方向：chat 消息转成 Responses input
        up = fake_upstream["body"]
        assert up["stream"] is True
        assert up["input"][0]["content"] == "你好"


def test_responses_stream_passthrough_keeps_events(fake_upstream):
    """/v1/responses 流式：事件原样透传（含 event: 行）。"""
    body = {"model": MODEL, "stream": True,
            "input": [{"role": "user", "content": "hi"}]}
    req = make_request(json.dumps(body).encode(), path="/v1/responses")
    with _patched() as setp:
        patch_db(setp)
        patch_shared(setp)
        setp(astudio_gateway, "_bill", lambda *a, **k: None)
        resp = asyncio.run(astudio_gateway.responses(req))
        assert resp.status_code == 200, getattr(resp, "status_code", None)
        text = asyncio.run(drain(resp))
        assert "event: response.output_text.delta" in text
        assert "event: response.completed" in text
        events = parse_sse_events(text)
        types = [e.get("type") for e in events if isinstance(e, dict)]
        assert "response.output_text.delta" in types
        assert "response.completed" in types


def test_messages_stream_anthropic_events(fake_upstream):
    """/v1/messages 流式：chat chunk 中继 → anthropic 事件（工具全量）。"""
    body = {"model": MODEL, "max_tokens": 100, "stream": True,
            "messages": [{"role": "user", "content": "读一下"}]}
    req = make_request(json.dumps(body).encode(), path="/v1/messages")
    with _patched() as setp:
        patch_db(setp)
        patch_shared(setp)
        setp(astudio_gateway, "_bill", lambda *a, **k: None)
        resp = asyncio.run(astudio_gateway.messages(req))
        assert resp.status_code == 200, getattr(resp, "status_code", None)
        text = asyncio.run(drain(resp))
        events = parse_sse_events(text)
        types = [e.get("type") for e in events if isinstance(e, dict)]
        assert types and types[0] == "message_start", types[:3]
        th = [e for e in events if e.get("type") == "content_block_delta"
              and e.get("delta", {}).get("type") == "thinking_delta"]
        assert th, "思考链丢失"
        starts = [e for e in events
                  if e.get("type") == "content_block_start"
                  and e.get("content_block", {}).get("type") == "tool_use"]
        assert starts, "tool_use 块丢失"
        assert starts[0]["content_block"]["name"] == "read"
        assert starts[0]["content_block"]["id"] == "call_9"
        idx = starts[0]["index"]
        args = "".join(e["delta"].get("partial_json") or "" for e in events
                       if e.get("type") == "content_block_delta"
                       and e.get("index") == idx
                       and e.get("delta", {}).get("type") == "input_json_delta")
        assert json.loads(args) == {"path": "/x"}, args
        md = [e for e in events if e.get("type") == "message_delta"]
        assert md and md[-1]["delta"]["stop_reason"] == "tool_use", md
        assert types[-1] == "message_stop", types[-3:]


def test_unknown_model_rejected():
    body = {"model": "not-a-real-model",
            "messages": [{"role": "user", "content": "hi"}]}
    req = make_request(json.dumps(body).encode())
    with _patched() as setp:
        patch_db(setp)
        patch_shared(setp)
        resp = asyncio.run(
            astudio_gateway.dispatch(req, "/v1/chat/completions"))
        assert resp.status_code == 400
        assert "model_not_found" in resp.body.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# 授权登录（引导本机登录 + 自动导入；离线）
# ---------------------------------------------------------------------------
def test_auth_begin_reports_installed(monkeypatch):
    from admin.platform.astudio.services import webauth

    monkeypatch.setattr(webauth, "_snapshot", lambda: {"/x/userdata/astron-session.json": (1, 2)})
    begun = webauth.begin()
    assert begun["installed"] is True
    assert begun["url"].startswith("https://")
    assert begun["expires_in"] == webauth.TTL_S


def test_auth_begin_when_not_installed(monkeypatch):
    from admin.platform.astudio.services import webauth

    monkeypatch.setattr(webauth, "_snapshot", lambda: {})
    begun = webauth.begin()
    assert begun["installed"] is False
    assert "装" in begun["hint"]


def test_auth_poll_imports_changed_file(monkeypatch):
    from admin.platform.astudio.services import webauth

    base = {"/p/userdata/astron-session.json": (100, 10)}
    monkeypatch.setattr(webauth, "_snapshot", lambda: base)
    begun = webauth.begin()

    # 没变化 → 还是 pending
    assert webauth.poll(None, begun["auth_id"])["status"] == "pending"

    # 文件变了 → 读记录 + 入库 → done
    changed = {"/p/userdata/astron-session.json": (200, 12)}
    monkeypatch.setattr(webauth, "_snapshot", lambda: changed)
    monkeypatch.setattr(webauth.paths, "read_session", lambda path: {"key": "k", "uid": "u1"})

    class FakeAccount:
        id = 11
        name = "u1"

    calls = []
    monkeypatch.setattr(webauth.astudio_accounts, "import_record",
                        lambda db, record: (calls.append(record), (FakeAccount(), True))[1])
    state = webauth.poll(None, begun["auth_id"])
    assert state["status"] == "done" and state["account_id"] == 11
    assert calls and calls[0]["key"] == "k"


def test_auth_poll_unknown_session():
    from admin.platform.astudio.services import webauth

    assert webauth.poll(None, "nope")["status"] == "expired"
    assert webauth.cancel("nope")["status"] == "expired"
