"""Qoder 国际版「model server 主机已下线」的两个回归点。

背景（2026-11-28 实测）：

* 国际版的 model server 基址 `https://api2-v2.qoder.sh` 已经**不存在** ——
  阿里 / DNSPod / Google 三路公共 DNS 全部 NXDOMAIN，请求在解析阶段就失败；
* `_plan_iterator()` 原来只在 `kind == "endpoint_gone"` 时回退旧链路，
  于是 DNS 失败被原样抛给用户，旧链路一次都没试过 —— 国际版整平台表现为
  「对话请求网络失败」，账号看着正常（余额能同步）却一条对话都发不出去。

这里锁住两条边界：

1. `_host_unreachable()` 只认「请求没到上游」的失败，超时不能算；
2. 主机不可达时必须回退旧链路，超时必须原样抛出（否则会二次记账）。
"""
from __future__ import annotations

import pytest

from admin.platform.qoder.core import client as qclient
from admin.platform.qoder.services import chat as qchat


@pytest.fixture(autouse=True)
def _reset_model_server_state():
    """每个用例都从「新链路可用」开始，避免 10 分钟 TTL 串味。"""
    qchat._MODEL_SERVER_STATE.update({"down_at": 0.0, "reason": ""})
    yield
    qchat._MODEL_SERVER_STATE.update({"down_at": 0.0, "reason": ""})


def test_dns_and_refused_count_as_unreachable():
    for detail in (
        "<urlopen error [Errno 11001] getaddrinfo failed>",
        "[Errno -2] Name or service not known",
        "Temporary failure in name resolution",
        "<urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>",
        "Connection refused",
        "Network is unreachable",
    ):
        assert qchat._host_unreachable(detail), detail


def test_timeouts_are_not_treated_as_unreachable():
    """超时可能已经到过上游，换链路重发 = 二次记账，必须原样抛。"""
    for detail in (
        "timed out",
        "<urlopen error [WinError 10060] 由于连接方在一段时间后没有正确答复",
        "Connection reset by peer",
        "SSL: UNEXPECTED_EOF_WHILE_READING",
        "",
        None,
    ):
        assert not qchat._host_unreachable(detail), detail


def test_plan_iterator_falls_back_when_model_host_is_dead(monkeypatch):
    calls = {"model_server": 0, "legacy": 0}

    def _dead_host(*a, **kw):
        calls["model_server"] += 1
        raise qclient.UpstreamError(
            "对话请求网络失败", status=0, kind="network",
            detail="<urlopen error [Errno 11001] getaddrinfo failed>")
        yield  # pragma: no cover - 让函数成为生成器

    def _legacy(*a, **kw):
        calls["legacy"] += 1
        yield {"choices": [{"delta": {"content": "ok"}}]}

    monkeypatch.setattr(qclient, "iter_model_chat_stream", _dead_host)
    monkeypatch.setattr(qclient, "iter_chat_stream", _legacy)

    plan = {"mode": "model_server", "token": "dt-x", "realm": "intl",
            "uid": "u", "name": "", "email": "", "machine_id": "",
            "upstream": {}, "upstream_openai": {}, "account_id": 1,
            "model_name": "auto"}
    frames = list(qchat._plan_iterator(plan))

    assert calls == {"model_server": 1, "legacy": 1}
    assert frames[0]["choices"][0]["delta"]["content"] == "ok"
    # 回退后标记下线，后续请求直接走旧链路（不再每次白撞一次 DNS）
    assert qchat.model_server_state()["usable"] is False


def test_plan_iterator_does_not_fall_back_on_timeout(monkeypatch):
    calls = {"legacy": 0}

    def _timeout(*a, **kw):
        raise qclient.UpstreamError(
            "对话请求网络失败", status=0, kind="network",
            detail="<urlopen error timed out>")
        yield  # pragma: no cover

    def _legacy(*a, **kw):
        calls["legacy"] += 1
        yield {}

    monkeypatch.setattr(qclient, "iter_model_chat_stream", _timeout)
    monkeypatch.setattr(qclient, "iter_chat_stream", _legacy)

    plan = {"mode": "model_server", "token": "dt-x", "realm": "intl",
            "uid": "u", "name": "", "email": "", "machine_id": "",
            "upstream": {}, "upstream_openai": {}, "account_id": 1,
            "model_name": "auto"}
    with pytest.raises(qclient.UpstreamError):
        list(qchat._plan_iterator(plan))
    assert calls["legacy"] == 0


def test_activation_message_is_explained():
    """`企业尚未激活，请重新购买` 要给出「找谁、怎么做」，而不是原样抛。"""
    hint = qclient._activation_hint("企业尚未激活，请重新购买")
    assert "未激活" in hint
    assert "无法绕过" in hint
    assert "Qoder 控制台" in hint
    # 普通错误不能被误伤
    assert qclient._activation_hint("model not found") == ""
    assert qclient._activation_hint("") == ""
