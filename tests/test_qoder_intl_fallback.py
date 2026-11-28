"""Qoder 国际版「model server 主机已下线」的回归点。

背景（2026-11-28 实测）：

* 国际版的 model server 基址 `https://api2-v2.qoder.sh` 已经**不存在** ——
  阿里 / DNSPod / Google 三路公共 DNS 全部 NXDOMAIN，请求在解析阶段就失败；
* `_plan_iterator()` 原来只在 `kind == "endpoint_gone"` 时回退旧链路，
  于是 DNS 失败被原样抛给用户，旧链路一次都没试过 —— 国际版整平台表现为
  「对话请求网络失败」，账号看着正常（余额能同步）却一条对话都发不出去。

锁三条边界：

1. 「主机不可达」只认请求**没到上游**的失败（DNS 解析失败 / 连接被拒）；
2. 超时 / 连接被 reset / SSL 中断**不算** —— 那几种请求可能已经送达上游，
   换链路重发会二次记账；
3. 主机不可达时 `_plan_iterator()` 必须回退旧链路并标记下线，
   超时必须原样抛出。

用例骨架来自 PR #130（@star211199），实现改成 main 上的 `_is_link_gone()`。
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


def _network(detail: str | None) -> qclient.UpstreamError:
    return qclient.UpstreamError("对话请求网络失败", status=0, kind="network",
                                 detail=detail or "")


def _plan() -> dict:
    return {"mode": "model_server", "token": "dt-x", "realm": "intl",
            "uid": "u", "name": "", "email": "", "machine_id": "",
            "upstream": {}, "upstream_openai": {}, "account_id": 1,
            "model_name": "auto"}


def test_dns_and_refused_count_as_unreachable():
    for detail in (
        "<urlopen error [Errno 11001] getaddrinfo failed>",
        "[Errno -2] Name or service not known",
        "Temporary failure in name resolution",
        "<urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>",
        "Connection refused",
        "Network is unreachable",
        "No route to host",
    ):
        assert qchat._is_link_gone(_network(detail)), detail


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
        assert not qchat._is_link_gone(_network(detail)), detail


def test_endpoint_gone_still_falls_back():
    """11-26 那条老路径不能被这次放宽改坏：空 404 仍要回退。"""
    gone = qclient.UpstreamError("404", status=404, kind="endpoint_gone",
                                 detail="该端点已被上游撤掉")
    assert qchat._is_link_gone(gone)


def test_business_errors_never_fall_back():
    """额度 / 模型类错误与链路无关，换链路只会掩盖问题。"""
    for kind in ("quota_exceeded", "model_unknown", "upstream_business"):
        assert not qchat._is_link_gone(
            qclient.UpstreamError("x", status=400, kind=kind, detail="y")), kind


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

    frames = list(qchat._plan_iterator(_plan()))

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

    with pytest.raises(qclient.UpstreamError):
        list(qchat._plan_iterator(_plan()))
    assert calls["legacy"] == 0


def test_intl_model_base_is_not_a_dead_domain():
    """`api2-v2.qoder.sh` 已全球 NXDOMAIN，默认不能再指向它。"""
    from admin.platform.qoder.core import endpoints

    assert endpoints.model_base("intl") == ""
    assert endpoints.model_base("cn") == "https://gateway.qoder.com.cn"
