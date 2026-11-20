"""Marvis 风控 / 额度 / 请求体积 / 出口代理的回归锁（2026-11-17）。

锁的都是「写错了不会报错、只会静默把整站拖挂」的地方：

  · 4100404（**出口 IP 级**风控）被当成账号级故障 → 3 个号一起 600s 冷却 →
    冷却到期立刻复发 → 503 accounts_cooling 死循环；
  · 风控还触发换号重试 → 同一份 body 1 秒内重放给 3 个号（风控最敏感的形状）；
  · 3000001（用户名下 token 不足）被归成 upstream_business → 空号永远排第一、
    每个请求先白撞一次 402；
  · 11MB 单条消息被送去撞上游，只换回一个看不懂的 400 3000101；
  · 出口 IP 硬禁代理 → 被风控之后代码层面没有任何换 IP 的手段。

2026-11-18 修订（本文件里跟着改了 5 处断言）：

  · 4100404 **不再重试**（client.CHAT_ATTEMPTS = 1）—— 18:19:11 撞 4100404 后
    整组轮换重发，18:23 同一个 token 就变成 401 4100403（登录态被打死）；
  · 平台级退避的阈值是**连续 5 次**（17:38 从 1 提到 5），单次命中只让本次失败。

⚠ 2026-11-20 口径更新 —— 本文件里**还有 4 条断言是红的，本批明确不修**：

  · `COOL_SECONDS_RISK` / `UpstreamError(cool_seconds=…)` /
    `UpstreamError(local=…)` 这三个名字在当前代码里都不存在。撞墙号的处置
    早已从「写库 300 秒冷却」改成「平台级退避 + **内存** 60 秒账号冷却」：

        MARVIS_RISK_ACCOUNT_COOLDOWN=60      冷却秒数
        MARVIS_RISK_COOLDOWN_ON_4100404=1    总开关（0 = 回到不冷却）
        实现见 gateway.py 的 _RISK_ACCOUNT_STATE（不写库、不跨进程）

    按新契约写的测试在 `tests/test_marvis_risk_no_retry_cooldown.py`。
    本文件那 4 条（test_cool_seconds_auth_and_risk /
    test_cool_seconds_prefers_client_value /
    test_risk_control_cools_only_the_hit_account /
    test_local_cooldown_block_does_not_count_as_risk_hit）留给单独的清理批次。
  · `CHAT_ATTEMPTS` 现在**真的是 1**（以前这里写 1、代码是 2 的那处漂移收口了）。
"""
import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

# 必须早于 admin.* 导入：admin/db.py 在模块级按 settings 建引擎（默认 MySQL）。
os.environ.setdefault(
    "ADMIN_DATABASE_URL",
    "sqlite:///" + str(Path(os.environ.get("TEMP", "/tmp")) / "marvis_risk_test.db"),
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from starlette.requests import Request  # noqa: E402

from admin import upstream_http  # noqa: E402
from admin.platform import shared  # noqa: E402
from admin.platform.marvis import gateway  # noqa: E402
from admin.platform.marvis.core import client as marvis_client  # noqa: E402


class _FakeAccount:
    """够 shared.note_failure / note_success 用的最小账号。"""

    def __init__(self, account_id: int):
        self.id = account_id
        self.status = "active"
        self.balance_remain = 510504
        self.balance_total = 10000000
        self.cool_until = None
        self.cool_kind = ""
        self.degrade_until = None
        self.err_count = 0
        self.last_err_at = None
        self.last_err_msg = ""


class _FakeDB:
    def commit(self):
        pass

    def rollback(self):
        pass


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """每个用例都从「没有退避、没有 opener 缓存、没有代理 env」开始。"""
    gateway.reset_risk_backoff()
    gateway.reset_risk_account_cooldown()
    upstream_http.reset_platform_openers()
    monkeypatch.delenv("MIRAIHUB_MARVIS_PROXY", raising=False)
    monkeypatch.delenv("MIRAIHUB_MARVIS_MAX_BODY_BYTES", raising=False)
    monkeypatch.delenv(gateway.RISK_BACKOFF_ENV, raising=False)
    monkeypatch.delenv(gateway.RISK_ACCOUNT_COOLDOWN_ENV, raising=False)
    monkeypatch.delenv(gateway.RISK_ACCOUNT_COOLDOWN_SWITCH_ENV, raising=False)
    yield
    gateway.reset_risk_backoff()
    gateway.reset_risk_account_cooldown()
    upstream_http.reset_platform_openers()


@pytest.fixture
def note_calls(monkeypatch):
    """记录 note_failure 的入参，同时**真的**写回 cool_until / degrade_until。

    把 public_platform 摘掉只是为了让哨兵不碰数据库 —— 冷却 / 降级这两条
    真正的写回逻辑仍然走 shared.note_failure 的**真实现**。
    """
    calls = []
    real = shared.note_failure

    def fake(db, account, **kwargs):
        calls.append(dict(kwargs))
        kwargs.pop("public_platform", None)
        return real(db, account, public_platform="", **kwargs)

    monkeypatch.setattr(gateway.shared, "note_failure", fake)
    return calls


def _install_pool(monkeypatch, accounts):
    """把选号 / 租约 / 取 token 换成本地假实现，返回「每次选号」的调用记录。"""
    selected = []

    def select_account(db, **kwargs):
        excluded = set(kwargs.get("exclude_ids") or ())
        selected.append(frozenset(excluded))
        for account in accounts:
            if account.id not in excluded:
                return account
        return None

    monkeypatch.setattr(gateway.shared, "select_account", select_account)
    monkeypatch.setattr(gateway.shared, "lease", lambda account: True)
    monkeypatch.setattr(gateway.shared, "release", lambda account: None)
    monkeypatch.setattr(gateway.shared, "note_success", lambda account: None)
    monkeypatch.setattr(gateway.marvis_accounts, "ensure_token",
                        lambda db, account: {"openid": "openid-%s" % account.id})
    return selected


def _run_attempt(monkeypatch, exc=None, result=None):
    def fake_call(data, realm, body, account_id, *, stream, profile=None):
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(gateway, "_call_upstream", fake_call)


def _risk_error():
    return marvis_client.UpstreamError(
        "模型网关失败（HTTP 403）", status=403, code="4100404",
        kind="risk_control",
        detail='{"code":4100404,"msg":"客户端通用错误:请求被风控拦截"}')


def _quota_error():
    return marvis_client.UpstreamError(
        "模型网关失败（HTTP 402）", status=402, code="3000001",
        kind="quota_exceeded",
        detail='{"code":3000001,"msg":"用户名下token不足"}')


# ---------------------------------------------------------------------------
# ① risk_control 不再走账号级冷却
# ---------------------------------------------------------------------------


def test_cool_seconds_auth_and_risk():
    """auth 600s；risk_control 用 300s（2026-11-18 起，不再是 0）。"""
    assert gateway._cool_seconds("auth") == 600
    assert gateway._cool_seconds("risk_control") == gateway.COOL_SECONDS_RISK
    assert gateway.COOL_SECONDS_RISK == 300
    assert gateway._cool_seconds("quota_exceeded") == 0
    assert gateway._cool_seconds("upstream_business") == 0


def test_cool_seconds_prefers_client_value():
    """client 在撞墙处已经算好秒数（env 可调），gateway 不许覆盖它。"""
    exc = marvis_client.UpstreamError(
        "x", status=403, code="4100404", kind="risk_control", cool_seconds=420)
    assert gateway._cool_seconds("risk_control", exc) == 420


def test_risk_control_cools_only_the_hit_account(monkeypatch, note_calls):
    """风控命中后：**只**给撞墙的号打 300 秒冷却，另一个号一点都不许动。

    旧断言是「cool_until 必须保持 None」。改判的不是「要不要保护号」，
    而是保护方式：18:19 的现场证明重试会把 4100404 推成 4100403（不可逆），
    所以必须让这个号歇一会儿；而「换号重放」已经被 risk_control 分支的
    raise（不 exclude、不换号）挡住了，冷却不会自放大。
    """
    account = _FakeAccount(189)
    other = _FakeAccount(190)
    _install_pool(monkeypatch, [account, other])
    _run_attempt(monkeypatch, exc=_risk_error())

    with pytest.raises(marvis_client.UpstreamError):
        gateway._attempt(_FakeDB(), None, None, {"model": "deepseek-v4.1-flash"},
                         "cn", stream=False)

    assert note_calls, '失败必须留痕（note_failure 没被调用）'
    assert note_calls[0]["cool_seconds"] == 300
    assert note_calls[0]["kind"] == "risk_control"
    assert account.cool_until is not None, '撞墙的号没被打冷却'
    assert account.cool_kind == "risk_control"
    left = (account.cool_until - datetime.utcnow()).total_seconds()
    assert 250 <= left <= 310, left
    assert other.cool_until is None, '别的号被连带冷却了（自放大）'


# ---------------------------------------------------------------------------
# ② risk_control 不换号 + 平台级退避
# ---------------------------------------------------------------------------


def test_risk_control_does_not_switch_account(monkeypatch, note_calls):
    """不 exclude、不换号：选号只发生一次（3 个号在池里也不许重放）。"""
    accounts = [_FakeAccount(189), _FakeAccount(190), _FakeAccount(191)]
    selected = _install_pool(monkeypatch, accounts)
    _run_attempt(monkeypatch, exc=_risk_error())

    with pytest.raises(marvis_client.UpstreamError):
        gateway._attempt(_FakeDB(), None, None, {"model": "deepseek-v4.1-flash"},
                         "cn", stream=False)

    assert len(selected) == 1, '风控触发了换号（同一 body 被重放）'
    assert selected[0] == frozenset(), '失败号被加进了 exclude 集合'


def test_local_cooldown_block_does_not_count_as_risk_hit(monkeypatch, note_calls):
    """本端冷却拦下的请求（local=True）不算命中：没打上游就不可能被风控。"""
    account = _FakeAccount(189)
    _install_pool(monkeypatch, [account])
    local_exc = marvis_client.UpstreamError(
        "Marvis 风控冷却中：本次不上上游", status=429, code="4100404",
        kind="risk_control", cool_seconds=120, local=True)
    _run_attempt(monkeypatch, exc=local_exc)

    with pytest.raises(marvis_client.UpstreamError):
        gateway._attempt(_FakeDB(), None, None, {"model": "m"}, "cn", stream=False)

    assert gateway.risk_hits() == 0, "本地拦截被算成上游命中（会自己推进退避）"
    assert gateway.risk_backoff_remaining() == 0


def test_platform_backoff_blocks_second_request(monkeypatch, note_calls):
    """退避窗口内第二次请求直接 503 + Retry-After，且**不再选号 / 不打上游**。

    阈值在 2026-11-18 17:38 从 1 提到 5（单次 4100404 只让本次失败），
    所以这里把阈值压到 1 才能测「退避已打开」这个状态本身。
    """
    monkeypatch.setenv(gateway.RISK_BACKOFF_THRESHOLD_ENV, "1")
    accounts = [_FakeAccount(189), _FakeAccount(190)]
    selected = _install_pool(monkeypatch, accounts)
    _run_attempt(monkeypatch, exc=_risk_error())

    with pytest.raises(marvis_client.UpstreamError):
        gateway._attempt(_FakeDB(), None, None, {"model": "m"}, "cn", stream=False)

    assert gateway.risk_backoff_remaining() > 0
    assert gateway.risk_backoff_remaining() <= gateway.RISK_BACKOFF_DEFAULT_SECONDS

    with pytest.raises(shared.PlatformRejected) as caught:
        gateway._attempt(_FakeDB(), None, None, {"model": "m"}, "cn", stream=False)

    exc = caught.value
    assert exc.status == 503
    assert exc.code == "risk_control_backoff"
    assert int(exc.headers["Retry-After"]) >= 1
    assert "风控" in exc.message
    assert len(selected) == 1, '退避期间仍然去选了号（会打上游）'


def test_backoff_window_is_env_tunable(monkeypatch):
    monkeypatch.setenv(gateway.RISK_BACKOFF_ENV, "5")
    monkeypatch.setenv(gateway.RISK_BACKOFF_THRESHOLD_ENV, "1")
    gateway.reset_risk_backoff()
    gateway.note_risk_control()
    remaining = gateway.risk_backoff_remaining()
    assert 0 < remaining <= 5


def test_risk_control_maps_to_503_for_client(monkeypatch):
    """给客户端的响应必须是可重试的 503，而不是看着像 Key 错的 403。"""
    response = gateway._fail(_risk_error())
    assert response.status_code == 503
    assert int(response.headers["retry-after"]) >= 1
    payload = json.loads(response.body)["error"]
    assert payload["type"] == "risk_control"
    assert "4100404" in payload["message"]


# ---------------------------------------------------------------------------
# ③ 3000001 → quota_exceeded
# ---------------------------------------------------------------------------


def test_biz_code_3000001_parsed():
    assert marvis_client._biz_code_of(
        '{"code":3000001,"msg":"用户名下token不足"}') == "3000001"


def test_3000001_classified_as_quota_exceeded():
    text = '{"code":3000001,"msg":"用户名下token不足"}'
    assert marvis_client._classify(402, text) == "quota_exceeded"
    # 别的业务码不受影响（仍然 upstream_business）
    assert marvis_client._classify(403, '{"code":4100406,"msg":"非法请求"}') \
        == "upstream_business"
    # 风控优先级不变
    assert marvis_client._classify(403, '{"code":4100404,"msg":"风控"}') \
        == "risk_control"


def test_quota_exceeded_goes_to_degrade_window_and_switches(monkeypatch, note_calls):
    """额度耗尽：走 degrade_until（30 分钟）+ 换号，而不是写 0 或永久冷却。"""
    accounts = [_FakeAccount(189), _FakeAccount(190)]
    selected = _install_pool(monkeypatch, accounts)

    state = {"calls": 0}

    def fake_call(data, realm, body, account_id, *, stream, profile=None):
        state["calls"] += 1
        if state["calls"] == 1:
            raise _quota_error()
        return {"choices": []}

    monkeypatch.setattr(gateway, "_call_upstream", fake_call)

    result, account, _started = gateway._attempt(
        _FakeDB(), None, None, {"model": "deepseek-v4.1-flash"}, "cn",
        stream=False)

    assert account.id == 190, '额度号没被换掉'
    assert note_calls[0]["zero_balance"] is True
    assert note_calls[0]["cool_seconds"] == 0
    assert accounts[0].degrade_until is not None, '没进 degrade_until 通道'
    assert accounts[0].cool_until is None
    assert accounts[0].balance_remain > 0, '余额是事实数据，不该被写成 0'
    assert 189 in selected[1], '额度号没被排除出本次请求'


# ---------------------------------------------------------------------------
# ④ 超大 body 入口直接拒（413）
# ---------------------------------------------------------------------------


def _request(content_length=None, body=b"", path="/v1/chat/completions"):
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    headers.append((b"content-type", b"application/json"))
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers,
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def test_oversized_body_rejected_locally(monkeypatch):
    calls = []
    monkeypatch.setattr(gateway, "_acquire", lambda *a, **k: calls.append(1))

    # 超过 256MB 兜底才拒（原来 11MB 就拒，长对话发不出去）
    request = _request(content_length=300 * 1024 * 1024)
    response = asyncio.run(gateway.chat_completions(request))

    assert response.status_code == 413
    payload = json.loads(response.body)["error"]
    assert payload["code"] == "body_too_large"
    # 文案要点：说清是网关兜底、给出解法，不再教训用户去拆消息
    assert "本网关的体积兜底" in payload["message"]
    assert "不是模型" in payload["message"]
    assert "MIRAIHUB_MARVIS_MAX_BODY_BYTES" in payload["message"]
    assert "拆成几条" not in payload["message"]
    assert not calls, '超大请求被送去打上游了'


def test_normal_large_body_still_passes(monkeypatch):
    """用户真实场景：6.5MB 单条消息必须放行；3.7MB 老先例当然也要放行。"""
    for size in (int(3.7 * 1024 * 1024), 6500000):
        request = _request(content_length=size)
        assert asyncio.run(gateway._reject_oversized(request)) is None, size


def test_body_limit_env_override(monkeypatch):
    monkeypatch.setenv("MIRAIHUB_MARVIS_MAX_BODY_BYTES", "1024")
    assert gateway._max_body_bytes() == 1024
    request = _request(content_length=2048)
    response = asyncio.run(gateway._reject_oversized(request))
    assert response is not None and response.status_code == 413


def test_body_limit_default_is_256mb():
    assert gateway._max_body_bytes() == 256 * 1024 * 1024


def test_body_limit_zero_disables_check(monkeypatch):
    """MIRAIHUB_MARVIS_MAX_BODY_BYTES=0（或负数）= 完全关闭体积检查。"""
    for raw in ("0", "-1"):
        monkeypatch.setenv("MIRAIHUB_MARVIS_MAX_BODY_BYTES", raw)
        assert gateway._max_body_bytes() <= 0
        # 连 300MB 的声明长度也不该被拦
        request = _request(content_length=300 * 1024 * 1024)
        assert asyncio.run(gateway._reject_oversized(request)) is None, raw
        # 没有 content-length 时读到 body 再量，同样放行
        request = _request(body=b"x" * 4096)
        assert asyncio.run(gateway._reject_oversized(request)) is None, raw


# ---------------------------------------------------------------------------
# ⑤ 出口代理：按平台可配，没配时行为与改动前完全一致
# ---------------------------------------------------------------------------


def _proxy_handler(opener):
    """取 opener 里的 ProxyHandler（没有 = 直连）。

    ⚠ 关键事实：urllib 的 build_opener 会把**空代理**的 ProxyHandler 丢掉 ——
    `ProxyHandler({})` 在 __init__ 里不给实例装 http_open/https_open，
    于是 OpenerDirector.add_handler 认为它没有可注册的方法、不加入 handlers，
    同时默认的 ProxyHandler() 又因为「同类型已被替换」被跳过。
    结果就是**整个 opener 里没有任何代理处理器** = 直连。
    这正是改动前的行为，回归锁要锁的就是「没配代理时仍然是这个形状」。
    """
    for handler in opener.handlers:
        if isinstance(handler, urllib.request.ProxyHandler):
            return handler
    return None


def test_no_proxy_env_keeps_direct_connection():
    """回归锁：没配 env 时必须还是「硬禁代理」的老行为（opener 里没有代理处理器）。"""
    assert upstream_http.platform_proxy_url("marvis") == ""
    opener = upstream_http.platform_opener("marvis")
    assert _proxy_handler(opener) is None, '出现代理处理器 = 开始读系统代理了'
    assert "ProxyHandler" not in [type(h).__name__ for h in opener.handlers]
    # 全局 opener 也没变
    assert _proxy_handler(upstream_http.urllib_opener()) is None


def test_platform_proxy_env_only_affects_marvis(monkeypatch):
    monkeypatch.setenv("MIRAIHUB_MARVIS_PROXY", "http://user:pass@127.0.0.1:7890")
    upstream_http.reset_platform_openers()

    assert upstream_http.platform_proxy_url("marvis") == \
        "http://user:pass@127.0.0.1:7890"
    marvis_handler = _proxy_handler(upstream_http.platform_opener("marvis"))
    assert marvis_handler.proxies == {
        "http": "http://user:pass@127.0.0.1:7890",
        "https": "http://user:pass@127.0.0.1:7890",
    }

    # 其它平台 / 全局 opener 一律不受影响（仍然没有代理处理器 = 直连）
    assert upstream_http.platform_proxy_url("loomy") == ""
    assert _proxy_handler(upstream_http.platform_opener("loomy")) is None
    assert _proxy_handler(upstream_http.urllib_opener()) is None


def test_marvis_client_opener_uses_platform_proxy(monkeypatch):
    monkeypatch.setenv("MIRAIHUB_MARVIS_PROXY", "http://127.0.0.1:7890")
    upstream_http.reset_platform_openers()
    handler = _proxy_handler(marvis_client._opener())
    assert handler.proxies == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }
    # 换登录态那条链路也必须走同一个出口
    no_redirect = marvis_client._no_redirect_opener()
    assert _proxy_handler(no_redirect).proxies == handler.proxies


def test_build_opener_without_proxy_matches_history():
    """build_opener() 的默认行为锁住：无代理 env 时就是直连（回归锁）。"""
    opener = upstream_http.build_opener()
    assert _proxy_handler(opener) is None
    assert not upstream_http.platform_proxy_url("anything")
    # 显式给 proxy 时才会装上代理处理器
    proxied = upstream_http.build_opener(proxy="http://127.0.0.1:7890")
    assert _proxy_handler(proxied).proxies == {"http": "http://127.0.0.1:7890",
                                             "https": "http://127.0.0.1:7890"}
