# -*- coding: utf-8 -*-
"""科搭 Codar 模块的离线契约用例（不触网、不写库、不需要真令牌）。

跑法：

    ../venv/bin/python -m pytest admin/platform/codar/tests -q -p no:cacheprovider

## 覆盖面（对应简报里的必测项）

1. 本机登录态扫描：带 BOM 的配置能读出来、坏文件/过期令牌各给各的文案；
2. 凭证导入：过期令牌在入库前就被拒，账号记录归一；
3. 模型目录：目录字段映射、未知协议不上架、不可达时回兜底表并标记；
4. 点数映射：主/副两个读数、一条失败另一条仍出数、都没有才报错；
5. 错误分类：401 / account_restricted / client_not_supported / 429 四种
   语义**分开**（文案不同是这四条断言的重点）；
6. 签到：已领（带 until）/ 本次领到 / 400+retry_after 判已领 / 登录失效；
7. 转发：OpenAI 入口走 Anthropic 上游（请求体转换 + 响应翻回来）、
   Anthropic 入口走 OpenAI 上游、两条流式中继的帧解析；
8. 并发闸门：每账号 3 个名额、超限排队、排队超时回 429。
"""
from __future__ import annotations

import asyncio
import base64
import json
import time

import httpx
import pytest
from starlette.requests import Request

from admin.platform.codar import gateway as hot
from admin.platform.codar.core import client, endpoints, paths
from admin.platform.codar.services import accounts as codar_accounts
from admin.platform.codar.services import checkin as codar_checkin


# ---------------------------------------------------------------------------
# 造件
# ---------------------------------------------------------------------------
def make_token(username: str = "demo-user", *, exp_offset: int = 7 * 86400,
               exp_missing: bool = False) -> str:
    """造一枚 JWT（不验签，够本模块读账号名与 exp）。"""
    def seg(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    claims = {"username": username}
    if not exp_missing:
        claims["exp"] = int(time.time()) + exp_offset
    return ".".join([seg({"alg": "HS256", "typ": "JWT"}), seg(claims),
                     "c2lnbmF0dXJlc2lnbmF0dXJl"])


class FakeAccount:
    def __init__(self, token: str = "", *, username: str = "demo-user",
                 account_id: int = 1, status: str = "active",
                 name: str = "demo"):
        self.id = account_id
        self.platform = endpoints.PLATFORM
        self.uid = username
        self.name = name
        self.status = status
        self.domain = "cn"
        self.balance_remain = 0
        self.balance_total = 0
        self.credits_expiring = 0
        self.credits_snapshot = ""
        self.balance_synced_at = None
        self.created_at = None
        self.last_err_msg = ""
        self.last_used_at = None
        self.auth_json = json.dumps({
            "token": token or make_token(username),
            "username": username,
            "expires_at": int(time.time()) + 7 * 86400,
            "source": "import",
        })

    def attr(self, name, default=None):
        return getattr(self, name, default)


class FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class FakeDB:
    """够 `_upsert` / `token_for_call` 用的最小会话。"""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.added = []
        self.commits = 0

    def query(self, *args, **kwargs):
        return FakeQuery(self.rows)

    def add(self, obj):
        self.added.append(obj)
        self.rows.append(obj)

    def delete(self, obj):
        if obj in self.rows:
            self.rows.remove(obj)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def refresh(self, obj):
        pass

    def close(self):
        pass


class FakeKey:
    id = 7


class FakeStream:
    """假上游 SSE：`iter_lines()` 逐行给，`close()` 记账。"""

    def __init__(self, lines):
        self._lines = list(lines)
        self.closed = False
        self.status_code = 200

    def iter_lines(self):
        for line in self._lines:
            yield line.encode() if isinstance(line, str) else line

    def close(self):
        self.closed = True


def sse(lines) -> list[str]:
    """把若干 `data:` 负载拼成 SSE 行。"""
    out = []
    for line in lines:
        if line in ("[DONE]",) or line.startswith("event:"):
            out.append(line)
        else:
            out.append("data: " + line)
        out.append("")
    return out


@pytest.fixture()
def wire(monkeypatch):
    """把 `client._new_client` 换成走 MockTransport 的客户端。"""
    holder = {"handler": None}

    def factory(timeout):
        handler = holder["handler"]
        assert handler is not None, "用例必须先设置 holder['handler']"
        return httpx.Client(timeout=timeout,
                            transport=httpx.MockTransport(handler))

    monkeypatch.setattr(client, "_new_client", factory)

    def use(handler):
        holder["handler"] = handler

    return use


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """每个用例从干净的内存状态开始（目录缓存 / 领取记录 / 闸门）。"""
    client.remember_catalog([], fallback=True)
    codar_checkin._CLAIMED_ACTIVITIES.clear()
    client._GATES.clear()
    yield
    client.remember_catalog([], fallback=True)
    codar_checkin._CLAIMED_ACTIVITIES.clear()
    client._GATES.clear()


def json_resp(payload, status: int = 200, headers=None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, headers=headers or {})
    return handler


# ---------------------------------------------------------------------------
# 1. 本机登录态扫描
# ---------------------------------------------------------------------------
def test_scan_reads_config_with_bom(tmp_path, monkeypatch):
    """客户端在 Windows 写过的配置带 BOM：必须能读出来（否则「明明登录了却扫不到」）。"""
    token = make_token("alice")
    config = tmp_path / "config.json"
    config.write_bytes(b"\xef\xbb\xbf" + json.dumps(
        {"provider": {"speedai": {"token": token}}}, ensure_ascii=False).encode())
    monkeypatch.setenv("CODAR_HOME", str(tmp_path))

    found = paths.app_token()
    assert found.get("error") in (None, ""), found
    assert found["token"] == token
    assert found["username"] == "alice"

    items = paths.discover()
    assert len(items) == 1
    assert items[0]["uid_full"] == "alice"
    assert items[0]["valid"] is True
    # 扫描结果不能含令牌本体
    assert token not in json.dumps(items, ensure_ascii=False)


def test_scan_reports_expired_token(tmp_path, monkeypatch):
    token = make_token("bob", exp_offset=-3600)
    (tmp_path / "config.json").write_text(
        json.dumps({"provider": {"speedai": {"token": token}}}), encoding="utf-8")
    monkeypatch.setenv("CODAR_HOME", str(tmp_path))

    found = paths.app_token()
    assert "过期" in found["error"]
    assert "刷新" in found["error"], "过期要给可执行的下一步，而不是只说不行"


def test_scan_missing_and_broken_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CODAR_HOME", str(tmp_path / "nope"))
    assert "没有找到" in paths.app_token()["error"]
    assert paths.discover() == []

    (tmp_path / "config.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("CODAR_HOME", str(tmp_path))
    assert "JSON" in paths.app_token()["error"]


def test_verify_token_requires_username_and_expiry():
    ok = paths.verify_token(make_token("carol"))
    assert ok["username"] == "carol" and ok["expires_at"] > 0

    with pytest.raises(paths.ScanError) as exc:
        paths.verify_token(make_token("dave", exp_missing=False,
                                      exp_offset=-1))
    assert "过期" in str(exc.value)

    with pytest.raises(paths.ScanError):
        paths.verify_token("a b c")  # 含空格：放不进 HTTP 头


# ---------------------------------------------------------------------------
# 2. 导入
# ---------------------------------------------------------------------------
def test_record_of_rejects_expired_token():
    with pytest.raises(ValueError) as exc:
        codar_accounts.record_of({"token": make_token("erin", exp_offset=-60)})
    assert "过期" in str(exc.value)


def test_import_batch_upserts_with_username(tmp_path, monkeypatch):
    monkeypatch.setattr("admin.platform.shared.schedule_import_refresh",
                        lambda *a, **k: None, raising=False)
    db = FakeDB()
    payload = json.dumps([{"token": make_token("frank")}])
    result = codar_accounts.import_batch(db, payload)
    assert result["ok"] and result["added"] == 1
    assert result["failed"] == 0
    account = db.added[0]
    assert account.uid == "frank"
    assert account.platform == "codar"
    assert account.status == "active"
    assert json.loads(account.auth_json)["username"] == "frank"


def test_import_batch_accepts_bare_token_lines(tmp_path, monkeypatch):
    monkeypatch.setattr("admin.platform.shared.schedule_import_refresh",
                        lambda *a, **k: None, raising=False)
    db = FakeDB()
    result = codar_accounts.import_batch(db, make_token("gina") + "\n")
    assert result["added"] == 1 and db.added[0].uid == "gina"


def test_import_local_uses_scanned_record(tmp_path, monkeypatch):
    monkeypatch.setenv("CODAR_HOME", str(tmp_path))
    monkeypatch.setattr("admin.platform.shared.schedule_import_refresh",
                        lambda *a, **k: None, raising=False)
    (tmp_path / "config.json").write_text(json.dumps(
        {"provider": {"speedai": {"token": make_token("henry")}}}),
        encoding="utf-8")
    db = FakeDB()
    result = codar_accounts.import_local(db)
    assert result["imported"] == 1 and result["added"] == 1
    assert db.added[0].uid == "henry"


def test_router_meta_scan_and_import_local(tmp_path, monkeypatch):
    """路由层也要通：`/meta` 的字段是控制台判定入口的依据，扫描不回传令牌。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from admin.db import get_db
    from admin.platform.codar.routers import accounts as routes
    from admin.security import require_admin

    monkeypatch.setattr("admin.platform.shared.schedule_import_refresh",
                        lambda *a, **k: None, raising=False)
    monkeypatch.setenv("CODAR_HOME", str(tmp_path))
    token = make_token("zoe")
    (tmp_path / "config.json").write_text(
        json.dumps({"provider": {"speedai": {"token": token}}}),
        encoding="utf-8")

    db = FakeDB()
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_admin] = lambda: True
    app.dependency_overrides[get_db] = lambda: db
    http = TestClient(app)

    meta = http.get("/api/codar/meta").json()
    for key in ("platform", "regions", "auth", "quota_kind", "note",
                "auth_mode", "auth_explain"):
        assert key in meta, "控制台按这些小标题渲染入口：" + key
    assert meta["auth_mode"] == "local-scan"

    scan = http.get("/api/codar/scan-local").json()
    assert scan["can_import"] is True
    assert scan["items"][0]["uid_full"] == "zoe"
    assert token not in json.dumps(scan, ensure_ascii=False)

    imported = http.post("/api/codar/import-local",
                         json={"all": True}).json()
    assert imported["imported"] == 1 and imported["added"] == 1
    assert db.added[0].uid == "zoe"


# ---------------------------------------------------------------------------
# 3. 模型目录
# ---------------------------------------------------------------------------
CATALOG_BODY = {
    "code": 200,
    "catalog": {
        "data": [
            {
                "id": "claude-sonnet-5-5",
                "name": "Claude Sonnet 5.5",
                "context_window": 1000000,
                "protocol": {"type": "anthropic_messages"},
                "input_modalities": ["text", "image"],
                "output_modalities": ["text"],
                "reasoning": True,
                "rate_badge": "0.93x",
                "request_schema": {"properties": {"max_tokens": {"maximum": 128000}}},
                "max_concurrency": 3,
            },
            {
                "id": "glm-5.3",
                "name": "GLM-5.3",
                "context_window": 1000000,
                "protocol": "openai_chat_completions",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
                "request_schema": {"properties": {"max_tokens": {"maximum": 131072}}},
            },
            {
                # 本网关没有 responses 这条线：不上架（否则每个请求必失败）
                "id": "some-responses-model",
                "protocol": {"type": "openai_responses"},
            },
            {"name": "没有 id 的记录"},
        ],
        "max_concurrency": 2,
    },
}


def test_catalog_records_maps_vendor_fields():
    rows = client.catalog_records(CATALOG_BODY)
    assert rows is not None
    by_id = {row["id"]: row for row in rows}
    assert set(by_id) == {"claude-sonnet-5-5", "glm-5.3"}
    claude = by_id["claude-sonnet-5-5"]
    assert claude["protocol"] == "anthropic"
    assert claude["context"] == 1000000
    assert claude["max_output"] == 128000
    assert claude["input"] == ["text", "image"]
    assert claude["rate"] == pytest.approx(0.93)
    assert claude["reasoning"] is True
    assert client.vendor_concurrency(CATALOG_BODY) == 2


def test_catalog_records_returns_none_when_shape_unknown():
    assert client.catalog_records({"code": 200, "detail": "boom"}) is None
    assert client.catalog_records(None) is None


def test_catalog_refusal_is_not_read_as_a_catalog():
    """拒绝体里残留的列表不能当目录用（否则账号的模型会被整体换掉）。"""
    body = {"catalog": {"code": 500, "data": [{"id": "bad", "protocol": "openai_chat_completions"}]}}
    assert client.catalog_records(body) is None
    assert client.catalog_records({"code": 200, "data": [
        {"id": "ok", "protocol": "openai_chat_completions"}]}) is not None
    assert client.catalog_records({"success": False, "data": [
        {"id": "ok", "protocol": "openai_chat_completions"}]}) is None


def test_catalog_fallback_marks_itself(monkeypatch):
    monkeypatch.setattr(codar_accounts, "_catalog_account",
                        lambda db, key=None: None)
    info = codar_accounts.catalog(FakeDB())
    assert info["fallback"] is True
    assert len(info["rows"]) == len(endpoints.fallback_catalog())
    assert "没有" in info["error"]


def test_catalog_fetch_uses_bearer_and_caches(wire):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=CATALOG_BODY)

    wire(handler)
    rows, concurrency = client.fetch_catalog(make_token("ivy"), account_id=1)
    assert captured["url"].endswith("/v1/codar/models")
    assert captured["auth"].startswith("Bearer ")
    assert len(rows) == 2 and concurrency == 2

    client.remember_catalog(rows, vendor_concurrency=concurrency)
    assert client.protocol_of("claude-sonnet-5-5") == "anthropic"
    assert client.protocol_of("glm-5.3") == "chat"
    # 目录里没有的模型按名字兜底
    assert client.protocol_of("claude-opus-9") == "anthropic"
    assert client.protocol_of("unknown-model") == "chat"


# ---------------------------------------------------------------------------
# 4. 点数 / 额度
# ---------------------------------------------------------------------------
def test_points_reads_general_and_agent(wire):
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/v1/gettoken")
        body = json.loads(request.content)
        assert body["username"] == "jack" and body["token"] == 0
        return httpx.Response(200, json={"code": 200,
                                         "data": {"token": 1200,
                                                  "agent_token": 340}})

    wire(handler)
    points = codar_accounts.read_points(make_token("jack"), "jack")
    assert points["general"] == 1200 and points["agent"] == 340
    assert points["agent_source"] == "gettoken"


def test_points_falls_back_to_agent_balance(wire):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/v1/gettoken"):
            return httpx.Response(200, json={"code": 200, "token": 88})
        assert url.endswith("/v1/agent/balance")
        return httpx.Response(200, json={"code": 200,
                                         "data": {"username": "kate",
                                                  "agent_balance": 42}})

    wire(handler)
    points = codar_accounts.read_points(make_token("kate"), "kate")
    assert points["general"] == 88
    assert points["agent"] == 42 and points["agent_source"] == "agent_balance"


def test_points_raise_when_both_reads_fail(wire):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "boom"})

    wire(handler)
    with pytest.raises(client.UpstreamError):
        codar_accounts.read_points(make_token("lee"), "lee")


def test_sync_balances_writes_unified_columns(wire):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 200,
                                         "data": {"token": 500,
                                                  "agent_token": 25}})

    wire(handler)
    account = FakeAccount(make_token("mona"), username="mona")
    result = codar_accounts.sync_balances(FakeDB([account]), account)
    assert result["general"] == 500 and result["agent"] == 25
    assert account.balance_remain == 500
    assert account.balance_total == 525
    snapshot = json.loads(account.credits_snapshot)
    assert snapshot["unit"] == "points" and snapshot["agent"] == 25


# ---------------------------------------------------------------------------
# 5. 错误分类（四种语义必须分开）
# ---------------------------------------------------------------------------
def test_classify_401_is_auth():
    error = client.classify(401, {"detail": "unauthorized"}, "")
    assert error.kind == "auth"
    assert "401" in error.message


def test_classify_account_restricted_is_not_login_failure():
    error = client.classify(403, {"detail": {"code": "account_restricted"}}, "")
    assert error.kind == "account_restricted"
    assert "不是登录失效" in error.message or "重新登录也不会恢复" in error.message


def test_classify_client_not_supported_names_the_fix():
    error = client.classify(403, {"detail": {
        "code": "client_not_supported",
        "message": "This login credential is restricted to Codar. "
                   "Use an API key for other clients."}}, "")
    assert error.kind == "client_gated"
    assert "API key" in error.message
    assert "client_not_supported" in error.message


def test_classify_429_keeps_retry_after():
    error = client.classify(429, {"detail": {"code": "concurrent_limit_exceeded"}},
                            "", retry_after=7)
    assert error.kind == "concurrent"
    assert error.retry_after == 7
    assert "并发" in error.message


def test_request_json_treats_200_with_business_401_as_auth(wire):
    wire(json_resp({"code": 401, "message": "Token has expired"}))
    with pytest.raises(client.UpstreamError) as exc:
        client.request_json("POST", endpoints.account_url(endpoints.POINTS_PATH),
                            token="tok", json_body={"username": "x"})
    assert exc.value.kind == "auth"


def test_request_json_anthropic_uses_api_key_header(wire):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["api_key"] = request.headers.get("x-api-key")
        seen["version"] = request.headers.get("anthropic-version")
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"type": "message"})

    wire(handler)
    client.request_json("POST", endpoints.gateway_url(endpoints.MESSAGES_PATH),
                        token="secret-token", protocol="anthropic",
                        json_body={"model": "claude-x", "messages": []})
    assert seen["api_key"] == "secret-token"
    assert seen["version"] == endpoints.ANTHROPIC_VERSION
    assert seen["auth"] is None, "Anthropic 分支不该同时发 Bearer"


# ---------------------------------------------------------------------------
# 6. 签到
# ---------------------------------------------------------------------------
def _sign_router(monkeypatch, *, status_payload, sign_payload=None,
                 sign_error=None):
    calls = {"status": 0, "sign": 0, "agent": 0}

    def fake(method, url, **kwargs):
        if url.endswith(endpoints.SIGN_STATUS_PATH):
            calls["status"] += 1
            return status_payload
        if url.endswith(endpoints.SIGN_PATH):
            calls["sign"] += 1
            if sign_error is not None:
                raise sign_error
            return sign_payload
        if url.endswith(endpoints.AGENT_BALANCE_PATH):
            calls["agent"] += 1
            return {"code": 200, "data": {"agent_balance": 0}}
        raise AssertionError("意外的请求：" + url)

    monkeypatch.setattr(client, "request_json", fake)
    return calls


def test_checkin_already_from_status(monkeypatch):
    calls = _sign_router(monkeypatch, status_payload={
        "code": 200, "data": {"isSigned": True, "retry_after": 80000}})
    account = FakeAccount(make_token("nick"), username="nick")
    result = codar_checkin.sign_in(FakeDB([account]), account)
    assert result["outcome"] == "already"
    assert result["wait_seconds"] == 80000
    assert result["until"] and result["until"] > time.time()
    assert calls["sign"] == 0, "已签过就不该再发一次写请求"


def test_checkin_claimed(monkeypatch):
    monkeypatch.setattr(codar_checkin, "_campaign", lambda db, account: None)
    calls = _sign_router(
        monkeypatch,
        status_payload={"code": 200, "data": {"signed": False}},
        sign_payload={"code": 200, "message": "签到成功",
                      "data": {"retry_after": 86399, "points": 15}})
    account = FakeAccount(make_token("olive"), username="olive")
    result = codar_checkin.sign_in(FakeDB([account]), account)
    assert result["outcome"] == "claimed"
    assert result["points"] == 15
    assert calls["sign"] == 1


def test_checkin_already_from_400_with_retry_after(monkeypatch):
    body = {"code": 400, "message": "24小时内仅可领取一次",
            "data": {"retry_after": 82800}}
    _sign_router(
        monkeypatch,
        status_payload={"code": 200, "data": {"signed": False}},
        sign_error=client.UpstreamError("24小时内仅可领取一次", status=400,
                                        kind="request", body=body))
    account = FakeAccount(make_token("paul"), username="paul")
    result = codar_checkin.sign_in(FakeDB([account]), account)
    assert result["outcome"] == "already"
    assert result["wait_seconds"] == 82800


def test_checkin_rate_limit_with_wait_is_not_already(monkeypatch):
    body = {"code": 400, "message": "请求过于频繁，风控拦截",
            "data": {"retry_after": 30}}
    _sign_router(
        monkeypatch,
        status_payload={"code": 200, "data": {"signed": False}},
        sign_error=client.UpstreamError("请求过于频繁，风控拦截", status=400,
                                        kind="request", body=body))
    account = FakeAccount(make_token("quinn"), username="quinn")
    result = codar_checkin.sign_in(FakeDB([account]), account)
    assert result["outcome"] == "failed", "风控/限流的 400 不能算「今日已领」"


def test_checkin_expired_token(monkeypatch):
    _sign_router(monkeypatch, status_payload=None)
    monkeypatch.setattr(client, "request_json",
                        lambda *a, **k: (_ for _ in ()).throw(
                            client.UpstreamError("登录态已失效", status=401,
                                                 kind="auth")))
    account = FakeAccount(make_token("rita"), username="rita")
    result = codar_checkin.sign_in(FakeDB([account]), account)
    assert result["outcome"] == "failed"
    assert result["kind"] == "auth"


def test_running_activities_filter():
    now = time.time()
    payload = {"data": [
        {"name": "活动A", "activity_id": "a", "free_until": now + 3600,
         "remaining_quota": 5},
        {"name": "过期", "activity_id": "b", "free_until": now - 10,
         "remaining_quota": 5},
        {"name": "用完", "activity_id": "c", "free_until": now + 3600,
         "remaining_quota": 0},
        {"name": "不可读额度", "activity_id": "d", "free_until": now + 3600,
         "remaining_quota": "oops"},
    ]}
    rows = codar_checkin.running_activities(payload)
    assert [row["activity_id"] for row in rows] == ["a"]


def test_free_model_claimed_at_most_once(monkeypatch):
    calls = {"claim": 0}

    def fake(method, url, **kwargs):
        if url.endswith(endpoints.FREE_MODEL_PATH):
            return {"code": 200, "data": {"status": "available",
                                          "activity_id": "camp-1",
                                          "free_until": time.time() + 3600}}
        if url.endswith(endpoints.FREE_MODEL_CLAIM_PATH):
            calls["claim"] += 1
            return {"code": 200, "success": True}
        raise AssertionError("意外的请求：" + url)

    monkeypatch.setattr(client, "request_json", fake)
    account = FakeAccount(make_token("sam"), username="sam")
    first = codar_checkin.claim_free_model(FakeDB([account]), account)
    second = codar_checkin.claim_free_model(FakeDB([account]), account)
    assert first and first["activity_id"] == "camp-1"
    assert second is None and calls["claim"] == 1


# ---------------------------------------------------------------------------
# 7. 转发
# ---------------------------------------------------------------------------
ANTHROPIC_MESSAGE = {
    "id": "msg_1", "type": "message", "role": "assistant",
    "model": "claude-sonnet-5-5",
    "content": [{"type": "text", "text": "hello"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 9, "output_tokens": 2},
}

OPENAI_COMPLETION = {
    "id": "chatcmpl-1", "object": "chat.completion", "model": "glm-5.3",
    "choices": [{"index": 0, "finish_reason": "stop",
                 "message": {"role": "assistant", "content": "hey"}}],
    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
}


def _request(path: str = "/v1/chat/completions") -> Request:
    return Request({
        "type": "http", "method": "POST", "path": path,
        "headers": [(b"content-type", b"application/json")],
        "query_string": b"", "scheme": "http", "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
    })


@pytest.fixture()
def hot_env(monkeypatch):
    """让转发热路径可跑：假的鉴权、假的会话、不记账。"""
    import admin.db as admin_db

    monkeypatch.setattr(admin_db, "SessionLocal", lambda: FakeDB())
    monkeypatch.setattr(hot.shared, "authenticate",
                        lambda *a, **k: FakeKey())
    monkeypatch.setattr(hot, "_bill", lambda *a, **k: None)
    client.remember_catalog([
        {"id": "claude-sonnet-5-5", "protocol": "anthropic",
         "context": 1000000, "max_output": 128000},
        {"id": "glm-5.3", "protocol": "chat",
         "context": 1000000, "max_output": 131072},
    ])
    return monkeypatch


def test_chat_entry_routes_claude_to_anthropic(hot_env):
    captured = {}

    def fake_attempt(db, request, key, body, protocol):
        captured["protocol"] = protocol
        captured["body"] = body
        return ANTHROPIC_MESSAGE, FakeAccount(), time.perf_counter()

    hot_env.setattr(hot, "_attempt_json", fake_attempt)
    body = {"model": "claude-sonnet-5-5",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 64, "temperature": 0.3}
    response = asyncio.run(hot.chat_completions(_wrap(body)))
    assert captured["protocol"] == "anthropic"
    # 请求体已转成 Anthropic 形态：max_tokens 保留、messages 不带 role=tool
    assert captured["body"]["max_tokens"] == 64
    assert captured["body"]["messages"][0]["role"] == "user"
    assert "stream_options" not in captured["body"]
    payload = json.loads(response.body)
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"]["content"] == "hello"


def test_messages_entry_routes_chat_model_to_openai(hot_env):
    captured = {}

    def fake_attempt(db, request, key, body, protocol):
        captured["protocol"] = protocol
        captured["body"] = body
        return OPENAI_COMPLETION, FakeAccount(), time.perf_counter()

    hot_env.setattr(hot, "_attempt_json", fake_attempt)
    body = {"model": "glm-5.3", "max_tokens": 32,
            "messages": [{"role": "user", "content": "yo"}],
            "system": "be nice"}
    response = asyncio.run(hot.messages(_wrap(body, "/v1/messages")))
    assert captured["protocol"] == "chat"
    # Anthropic 的 system 字段被搬进了 messages
    assert captured["body"]["messages"][0]["role"] == "system"
    payload = json.loads(response.body)
    assert payload["type"] == "message"
    assert payload["content"][0]["text"] == "hey"
    assert payload["stop_reason"] == "end_turn"


class _FakeReq:
    """把 body 直接喂给 handler（跳过 Starlette 的流式读体）。"""

    def __init__(self, body, path="/v1/chat/completions"):
        self._body = body
        self.scope = {"method": "POST", "path": path,
                      "_wb_body": json.dumps(body).encode()}
        self.headers = {"x-platform": "codar"}
        self.state = type("S", (), {"platform": "codar", "realm": "cn"})()
        self.client = None

    async def json(self):
        return self._body


def _wrap(body, path="/v1/chat/completions"):
    return _FakeReq(body, path)


def _collect(response) -> bytes:
    async def run():
        chunks = []
        async for piece in response.body_iterator:
            chunks.append(piece if isinstance(piece, bytes) else str(piece).encode())
        return b"".join(chunks)

    return asyncio.run(run())


ANTHROPIC_STREAM_LINES = [
    "event: message_start",
    'data: {"type":"message_start","message":{"id":"msg_1","type":"message",'
    '"role":"assistant","model":"claude-sonnet-5-5","content":[],'
    '"usage":{"input_tokens":5,"output_tokens":0}}}',
    "",
    "event: content_block_start",
    'data: {"type":"content_block_start","index":0,'
    '"content_block":{"type":"text","text":""}}',
    "",
    "event: content_block_delta",
    'data: {"type":"content_block_delta","index":0,'
    '"delta":{"type":"text_delta","text":"hi"}}',
    "",
    "event: content_block_stop",
    'data: {"type":"content_block_stop","index":0}',
    "",
    "event: message_delta",
    'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    '"usage":{"output_tokens":2}}',
    "",
    "event: message_stop",
    'data: {"type":"message_stop"}',
    "",
]

OPENAI_STREAM_LINES = [
    'data: {"id":"chatcmpl-1","object":"chat.completion.chunk",'
    '"model":"glm-5.3","choices":[{"index":0,"delta":{"content":"hey"},'
    '"finish_reason":null}]}',
    "",
    'data: {"id":"chatcmpl-1","object":"chat.completion.chunk",'
    '"model":"glm-5.3","choices":[{"index":0,"delta":{},'
    '"finish_reason":"stop"}],"usage":{"prompt_tokens":4,'
    '"completion_tokens":2}}',
    "",
    "data: [DONE]",
    "",
]


def test_anthropic_upstream_stream_relayed_as_openai(hot_env):
    stream = FakeStream(ANTHROPIC_STREAM_LINES)

    def fake_open(db, request, key, body, protocol, stats):
        assert protocol == "anthropic"
        return stream, FakeAccount(), "", time.perf_counter()

    hot_env.setattr(hot, "_open_stream_attempt", fake_open)
    body = {"model": "claude-sonnet-5-5", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}
    response = asyncio.run(hot.chat_completions(_wrap(body)))
    raw = _collect(response).decode()
    assert "chat.completion.chunk" in raw
    assert '"content":"hi"' in raw.replace(" ", "")
    assert "[DONE]" in raw
    assert stream.closed, "流结束必须关掉上游连接"


def test_chat_upstream_stream_relayed_as_anthropic(hot_env):
    stream = FakeStream(OPENAI_STREAM_LINES)

    def fake_open(db, request, key, body, protocol, stats):
        assert protocol == "chat"
        # 流式要补 include_usage，否则账本只有 0 token
        assert body["stream_options"]["include_usage"] is True
        return stream, FakeAccount(), "", time.perf_counter()

    hot_env.setattr(hot, "_open_stream_attempt", fake_open)
    body = {"model": "glm-5.3", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}
    response = asyncio.run(hot.messages(_wrap(body, "/v1/messages")))
    raw = _collect(response).decode()
    assert "event: message_start" in raw
    assert "event: content_block_delta" in raw
    assert '"text":"hey"' in raw.replace(" ", "")
    assert "event: message_stop" in raw


def test_passthrough_chat_stream_is_byte_for_byte(hot_env):
    stream = FakeStream(OPENAI_STREAM_LINES)

    def fake_open(db, request, key, body, protocol, stats):
        return stream, FakeAccount(), "", time.perf_counter()

    hot_env.setattr(hot, "_open_stream_attempt", fake_open)
    body = {"model": "glm-5.3", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}
    response = asyncio.run(hot.chat_completions(_wrap(body)))
    raw = _collect(response).decode()
    assert "chat.completion.chunk" in raw
    assert raw.count("data: [DONE]") == 1


def test_stream_body_injects_include_usage_only_for_chat():
    body = {"model": "glm-5.3", "stream": True,
            "stream_options": {"custom": 1}}
    wired, injected = hot._stream_body(body, "chat")
    assert injected is True
    assert wired["stream_options"] == {"custom": 1, "include_usage": True}
    same, injected2 = hot._stream_body(body, "anthropic")
    assert same is body and injected2 is False


def test_count_tokens_estimates_without_upstream(hot_env):
    response = asyncio.run(hot.count_tokens(_wrap({
        "model": "glm-5.3",
        "messages": [{"role": "user", "content": "你好 hello"}]})))
    payload = json.loads(response.body)
    assert payload["input_tokens"] >= 2


def test_usage_mapping_to_openai_columns():
    """两种协议的 usage 都要落到 OpenAI 字段上，否则账本只有 0 token。"""
    mapped = hot._openai_usage({"input_tokens": 10, "output_tokens": 4,
                                "cache_read_input_tokens": 6})
    assert mapped["prompt_tokens"] == 16
    assert mapped["completion_tokens"] == 4
    passthrough = {"prompt_tokens": 3, "completion_tokens": 1}
    assert hot._openai_usage(passthrough) is passthrough


def test_responses_entry_returns_responses_shape(hot_env):
    captured = {}

    def fake_attempt(db, request, key, body, protocol):
        captured["protocol"] = protocol
        captured["body"] = body
        return OPENAI_COMPLETION, FakeAccount(), time.perf_counter()

    hot_env.setattr(hot, "_attempt_json", fake_attempt)
    body = {"model": "glm-5.3", "input": "hi", "max_output_tokens": 32}
    response = asyncio.run(hot.responses(_wrap(body, "/v1/responses")))
    assert captured["protocol"] == "chat"
    assert captured["body"]["messages"][0]["content"] == "hi"
    payload = json.loads(response.body)
    assert payload["object"] == "response"
    assert payload["output"][0]["content"][0]["text"] == "hey"


def test_stream_billing_keeps_upstream_usage(hot_env):
    """翻译中继会吞掉事件流 —— usage 必须在我们的生成器里先记下来。"""
    billed = {}
    hot_env.setattr(hot, "_bill",
                    lambda key, account, model, usage, **kw: billed.update(usage or {}))
    stream = FakeStream(ANTHROPIC_STREAM_LINES)

    hot_env.setattr(hot, "_open_stream_attempt",
                    lambda db, request, key, body, protocol, stats:
                    (stream, FakeAccount(), "", time.perf_counter()))
    body = {"model": "claude-sonnet-5-5", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}
    response = asyncio.run(hot.chat_completions(_wrap(body)))
    _collect(response)
    assert billed.get("input_tokens") == 5
    assert billed.get("output_tokens") == 2


def test_fail_passes_retry_after_header():
    response = hot._fail(client.UpstreamError("busy", status=429,
                                              kind="concurrent", retry_after=9))
    assert response.status_code == 429
    assert response.headers.get("retry-after") == "9"
    payload = json.loads(response.body)
    assert payload["error"]["type"] == "rate_limit_error"


def test_dispatch_unknown_path_returns_404():
    response = asyncio.run(hot.dispatch(_request(), "/v1/unknown"))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 8. 并发闸门
# ---------------------------------------------------------------------------
def test_slot_limit_is_three_per_account():
    for _ in range(endpoints.max_concurrency()):
        assert client.acquire_slot("uid-a") is True
    assert client.acquire_slot("uid-a", timeout=0.05) is False
    # 另一个账号不受影响：上限是「每账号」的，不是全局的
    assert client.acquire_slot("uid-b") is True
    client.release_slot("uid-a")
    assert client.acquire_slot("uid-a", timeout=0.05) is True


def test_queue_timeout_raises_retryable_429():
    for _ in range(endpoints.max_concurrency()):
        client.acquire_slot("uid-c")
    with pytest.raises(client.UpstreamError) as exc:
        with client.slot("uid-c", timeout=0.05):
            pass
    assert exc.value.status == 429
    assert exc.value.kind == "concurrent"
    assert exc.value.retry_after > 0
    assert exc.value.retryable is True


# ---------------------------------------------------------------------------
# 微信扫码授权（离线：上游与入库都打桩）
# ---------------------------------------------------------------------------
def _qr_session(monkeypatch, status_answer, token="eyJ0eXAiOiJKV1QifQ.eyJleHAiOjQwNzA5MDg4MDAsInVzZXJuYW1lIjoidTEifQ.sig"):
    from admin.platform.codar.services import webauth

    monkeypatch.setattr(webauth, "_post", lambda path, body: status_answer)
    sessions = {}
    with webauth._LOCK:
        webauth._SESSIONS.clear()
    begun = webauth.begin() if False else None
    return webauth


def test_qr_url_allowlist():
    from admin.platform.codar.services import webauth

    assert webauth.qr_url_allowed("https://mp.weixin.qq.com/cgi-bin/showqrcode?ticket=x")
    assert webauth.qr_url_allowed("https://codar-web.speedai.com/a.png")
    # 回环、明文、带端口、带 userinfo 一律拒绝
    for bad in ("http://mp.weixin.qq.com/x", "https://127.0.0.1/x.png",
                "https://mp.weixin.qq.com:8443/x", "https://u:p@mp.weixin.qq.com/x",
                "https://evil.example.com/x.png"):
        assert not webauth.qr_url_allowed(bad), bad


def test_qr_begin_and_poll_done(monkeypatch):
    from admin.platform.codar.services import webauth

    answers = {
        webauth.QR_PATH: {"code": 200, "data": {"scene": "s-1",
                                                "qr_url": "https://mp.weixin.qq.com/cgi-bin/showqrcode?ticket=t"}},
        webauth.STATUS_PATH: {"code": 200, "data": {"status": "SUCCESS",
                                                    "token": "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjQwNzA5MDg4MDAsInVzZXJuYW1lIjoidTEifQ.x",
                                                    "username": "u1"}},
    }
    monkeypatch.setattr(webauth, "_post", lambda path, body=None, **kw: answers[path])
    imported = []

    class FakeAccount:
        id = 7
        name = "u1"

    monkeypatch.setattr(webauth.codar_accounts, "import_record",
                        lambda db, record: (imported.append(record), (FakeAccount(), True))[1])
    monkeypatch.setattr(webauth.paths, "verify_token",
                        lambda token: {"token": token, "username": "u1", "expires_at": 4070908800})

    begun = webauth.begin()
    assert begun["scene"] == "s-1" and begun["expires_in"] == webauth.TTL_S
    state = webauth.poll(None, begun["auth_id"])
    assert state["status"] == "done" and state["account_id"] == 7
    assert imported and imported[0]["source"] == "qr"
    # 二次轮询不该重复入库
    again = webauth.poll(None, begun["auth_id"])
    assert again["status"] == "done" and len(imported) == 1


def test_qr_status_mapping(monkeypatch):
    from admin.platform.codar.services import webauth

    cases = [
        ({"code": 200, "data": {"status": "EXPIRED"}}, "expired"),
        ({"code": 404, "data": {}}, "expired"),
        ({"code": 200, "data": {"status": "REQUIRED_BIND_PHONE"}}, "failed"),
        ({"code": 200, "data": {"status": "REQUIRED_IP_VERIFY"}}, "failed"),
        ({"code": 200, "data": {"status": "FAILED"}}, "failed"),
    ]
    for answer, expected in cases:
        monkeypatch.setattr(webauth, "_post",
                            lambda path, body=None, a=answer, **kw: a if path == webauth.STATUS_PATH else
                            {"code": 200, "data": {"scene": "s", "qr_url": "https://mp.weixin.qq.com/x"}})
        begun = webauth.begin()
        state = webauth.poll(None, begun["auth_id"])
        assert state["status"] == expected, (answer, state)


def test_qr_pending_and_scanned(monkeypatch):
    from admin.platform.codar.services import webauth

    for said, expected in (("WAITING", "pending"), ("SCANNED", "scanned")):
        monkeypatch.setattr(webauth, "_post",
                            lambda path, body=None, s=said, **kw: {"code": 200, "data": {"scene": "s", "qr_url": "https://mp.weixin.qq.com/x"}}
                            if path == webauth.QR_PATH else {"code": 200, "data": {"status": s}})
        begun = webauth.begin()
        assert webauth.poll(None, begun["auth_id"])["status"] == expected
