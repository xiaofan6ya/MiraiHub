#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Marvis 上游业务码归类 + 错误面（PR-01）的回归。

## 锁的是什么

1. `client._classify()` 不再只看 HTTP 状态：`3000001` / `1000007` /
   `3000100` / `5100007` 四种业务码各自归到独立 kind，且**判定位置在
   auth 之前** —— 以前它们全落进 `upstream_business`，网关「不重试、
   不冷却、原样抛」，用户拿到的是一句看不出原因的「模型网关失败
   （HTTP 402 / 500）」，于是去改一个根本没写错的请求体。
2. `gateway._error_payload()` 产出的错误体带 `family`（额度族）与
   `upstream_code`（保留上游码供排障），状态码按族分开：
   `3000100` → 400、`1000007` → 400、`3000001` → 402、`5100007` → 502。
3. `family` **只在额度族出现**，其它 kind 的错误体形状与 origin/main 一致。
4. 池子里 active 号的余额**全为 0** 时报 402 `quota_exhausted`，而不是
   503 `accounts_cooling` —— 「等配额重置」和「等冷却」是两种完全不同的
   结局。余额为 NULL（从没同步过）仍按冷却处理：未知不等于耗尽。

## 不动的是什么（回归护栏）

`4100404` / `4100406` / `4100403` / `signer_unavailable` 的归类与
错误体形状在本 PR 里一个字都没改，下面用对照组锁住。

运行：
  python tests/test_marvis_error_surface.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from admin.platform import shared  # noqa: E402
from admin.platform.marvis import gateway  # noqa: E402
from admin.platform.marvis.core import client  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [ok] {name}")
    else:
        FAIL += 1
        print(f"  [!!] {name}  {detail}")


def _err(message: str = "模型网关失败", *, status: int = 0, code: str = "",
         kind: str = "http_error", detail: str = "") -> client.UpstreamError:
    return client.UpstreamError(message, status=status, code=code, kind=kind,
                               detail=detail)


# ----------------------------------------------------------------------
# 1. _classify：业务码族
# ----------------------------------------------------------------------
def test_classify_business_codes() -> None:
    print("\n== 业务码归类（不再只看 HTTP 状态） ==")
    cases = [
        (402, '{"code":3000001,"msg":"用户名下token不足"}', "quota_exceeded",
         "3000001 → quota_exceeded"),
        (400, '{"code":1000007,"msg":"积分余额不足"}', "quota_exceeded",
         "1000007 → quota_exceeded"),
        (400, '{"code":3000100,"msg":"max input token error"}',
         "max_input_tokens", "3000100 → max_input_tokens"),
        (500, '{"code":5100007,"msg":"服务端通用错误:服务端严重报错"}',
         "upstream_internal", "5100007 → upstream_internal"),
        # 业务码写成字符串（上游两种写法都出现过）
        (402, '{"code":"3000001"}', "quota_exceeded",
         "业务码为字符串时同样命中"),
        # 被包一层信封也要能抠出来
        (500, '{"error":{"code":5100007}}', "upstream_internal",
         "嵌套信封也命中"),
    ]
    for status, body, want, label in cases:
        got = client._classify(status, body)
        check(label, got == want, f"got={got} want={want}")


def test_classify_regressions() -> None:
    print("\n== 既有分类不变（回归护栏） ==")
    cases = [
        (403, '{"code":4100404,"msg":"客户端通用错误:请求被风控拦截"}',
         "risk_control", "4100404 仍是 risk_control"),
        (403, '{"code":4100406,"msg":"非法请求"}', "content_block",
         "4100406 仍是 content_block"),
        (401, "{}", "auth", "401 仍是 auth"),
        (200, '{"code":4100403,"msg":"登录态过期"}', "auth",
         "200+4100403 仍是 auth"),
        (400, '{"code":3000101,"msg":"invalid parameter"}', "http_error",
         "3000101 参数错仍落 http_error（本 PR 不接管）"),
        (503, "", "upstream_error", "无业务码的 5xx 仍是 upstream_error"),
    ]
    for status, body, want, label in cases:
        got = client._classify(status, body)
        check(label, got == want, f"got={got} want={want}")


# ----------------------------------------------------------------------
# 2. _error_payload：(status, code, family, upstream_code)
# ----------------------------------------------------------------------
def test_error_payload_quota_family() -> None:
    print("\n== _error_payload：额度族 ==")
    exc = _err(status=402, code="402", kind="quota_exceeded",
               detail='{"code":3000001,"msg":"用户名下token不足"}')
    payload = gateway._error_payload(exc)
    check("3000001 有 payload", payload is not None, repr(payload))
    status, obj, headers = payload
    check("3000001 → 402", status == 402, status)
    check("3000001 → code=quota_exhausted", obj.get("code") == "quota_exhausted",
          obj.get("code"))
    check("3000001 → family=quota", obj.get("family") == "quota",
          obj.get("family"))
    check("3000001 → upstream_code", obj.get("upstream_code") == "3000001",
          obj.get("upstream_code"))
    check("3000001 → Retry-After 给到配额重置尺度",
          headers.get("Retry-After") == "3600", headers)
    check("3000001 → upstream_account=False",
          obj.get("upstream_account") is False, obj.get("upstream_account"))

    exc = _err(status=400, code="400", kind="max_input_tokens",
               detail='{"code":3000100,"msg":"max input token error"}')
    status, obj, _ = gateway._error_payload(exc)
    check("3000100 → 400", status == 400, status)
    check("3000100 → code=max_input_tokens_exceeded",
          obj.get("code") == "max_input_tokens_exceeded", obj.get("code"))
    check("3000100 → family=quota", obj.get("family") == "quota")
    check("3000100 → upstream_code=3000100",
          obj.get("upstream_code") == "3000100", obj.get("upstream_code"))
    check("3000100 文案否掉「请求太大」",
          "不是请求太大" in str(obj.get("message")), obj.get("message"))

    exc = _err(status=400, code="400", kind="quota_exceeded",
               detail='{"code":1000007,"msg":"积分余额不足"}')
    status, obj, _ = gateway._error_payload(exc)
    check("1000007 → 400", status == 400, status)
    check("1000007 → code=insufficient_quota",
          obj.get("code") == "insufficient_quota", obj.get("code"))
    check("1000007 → family=quota", obj.get("family") == "quota")
    check("1000007 → upstream_code=1000007",
          obj.get("upstream_code") == "1000007", obj.get("upstream_code"))


def test_error_payload_upstream_internal() -> None:
    print("\n== _error_payload：上游内部错误 ==")
    exc = _err(status=500, code="500", kind="upstream_internal",
               detail='{"code":5100007,"msg":"服务端通用错误:服务端严重报错"}')
    status, obj, headers = gateway._error_payload(exc)
    check("5100007 → 502", status == 502, status)
    check("5100007 → code=upstream_internal_error",
          obj.get("code") == "upstream_internal_error", obj.get("code"))
    check("5100007 → 不带 family", "family" not in obj, obj.get("family"))
    check("5100007 → upstream_code=5100007",
          obj.get("upstream_code") == "5100007")
    check("5100007 → upstream_account=False",
          obj.get("upstream_account") is False)


def test_error_payload_untouched_kinds() -> None:
    print("\n== _error_payload 不接管既有分类 ==")
    for kind, extra in (("risk_control", {}),
                        ("content_block", {"status": 403}),
                        ("signer_unavailable", {"status": 503}),
                        ("auth", {"status": 401}),
                        ("http_error", {"status": 400})):
        payload = gateway._error_payload(_err(kind=kind, **extra))
        check(f"{kind} 返回 None（交给 _fail 既有分支）",
              payload is None, repr(payload))


# ----------------------------------------------------------------------
# 3. _fail：端到端错误体
# ----------------------------------------------------------------------
def test_fail_end_to_end() -> None:
    print("\n== _fail 端到端（真 JSONResponse） ==")
    exc = _err(status=402, code="402", kind="quota_exceeded",
               detail='{"code":3000001,"msg":"用户名下token不足"}')
    resp = gateway._fail(exc)
    body = json.loads(resp.body)
    check("402 quota_exhausted 的状态码", resp.status_code == 402,
          resp.status_code)
    check("错误体带 family", body["error"].get("family") == "quota", body)
    check("错误体带 upstream_code",
          body["error"].get("upstream_code") == "3000001", body)
    check("Retry-After 到响应头",
          resp.headers.get("retry-after") == "3600",
          dict(resp.headers))

    exc = _err(status=500, code="500", kind="upstream_internal",
               detail='{"code":5100007,"msg":"服务端通用错误"}')
    resp = gateway._fail(exc)
    body = json.loads(resp.body)
    check("5100007 → 502", resp.status_code == 502, resp.status_code)
    check("5100007 错误体不带 family", "family" not in body["error"], body)

    # 4100404 的错误面 —— **PR #67 起变了**（2026-11-20）
    #
    #   旧：429 + code=risk_control + Retry-After: 600
    #       （对应账号级冷却时代：整池都在风控里、等十分钟）
    #
    #   新：503 + code=risk_control_backoff / risk_control_request
    #       + Retry-After: 1~10
    #       （请求级失败，平台级退避窗口只有 10 秒）
    #
    # 为什么改成 503：403/429 在多数 OpenAI 兼容客户端里会被渲染成
    # 「API 密钥无效」或「被限流」，用户于是去翻一个没问题的 Key。
    # 503 才是「上游暂时不可用，稍后重试」的中性表达。
    resp = gateway._fail(_err(status=403, code="403", kind="risk_control",
                              detail='{"code":4100404}'))
    body = json.loads(resp.body)
    check("4100404 是 503（可重试）", resp.status_code == 503, resp.status_code)
    check("4100404 的 code 分两档",
          body["error"].get("code") in ("risk_control_backoff",
                                        "risk_control_request"), body)
    check("4100404 不带 family", "family" not in body["error"], body)
    check("4100404 的 Retry-After 是退避窗口（1~11s）",
          resp.headers.get("retry-after") in ("1", "10", "11"),
          dict(resp.headers))
    check("4100404 不引导换号",
          body["error"].get("upstream_account") is False, body)


# ----------------------------------------------------------------------
# 4. 池子余额全 0 → 402 quota_exhausted（不是「都在冷却」）
# ----------------------------------------------------------------------
def _row(acct_id: int, balance, *, status: str = "active"):
    return SimpleNamespace(id=acct_id, status=status, last_err_msg="",
                           cool_kind="", balance_remain=balance)


def _run_attempt(rows) -> Exception | None:
    """`一个号都选不到` 的路径：select_account 返回 None。"""
    with mock.patch.object(shared, "select_account", return_value=None), \
            mock.patch.object(gateway.marvis_accounts, "accounts_for",
                              return_value=rows):
        try:
            gateway._attempt(None, None, None, {"model": "main-auto"}, "cn",
                             stream=False)
        except Exception as exc:  # noqa: BLE001 - 就是要拿这个异常
            return exc
    return None


def test_pool_exhausted_is_402() -> None:
    print("\n== 余额全 0 → 402 quota_exhausted ==")
    exc = _run_attempt([_row(1, 0), _row(2, 0), _row(3, 0)])
    check("抛的是 UpstreamError", isinstance(exc, client.UpstreamError),
          type(exc).__name__)
    check("status=402", getattr(exc, "status", None) == 402,
          getattr(exc, "status", None))
    check("code=quota_exhausted", getattr(exc, "code", "") == "quota_exhausted",
          getattr(exc, "code", ""))
    check("kind=quota_exceeded", getattr(exc, "kind", "") == "quota_exceeded",
          getattr(exc, "kind", ""))
    # 同一条异常经过 _fail() 之后，对外就是 402 + family
    resp = gateway._fail(exc)
    body = json.loads(resp.body)
    check("对外 402", resp.status_code == 402, resp.status_code)
    check("对外 family=quota", body["error"].get("family") == "quota", body)
    check("对外 code=quota_exhausted",
          body["error"].get("code") == "quota_exhausted", body)
    check("对外 upstream_code=3000001",
          body["error"].get("upstream_code") == "3000001", body)


def test_pool_still_cooling_control() -> None:
    print("\n== 对照组：还有余额 / 余额未知 → 仍是 503 accounts_cooling ==")
    exc = _run_attempt([_row(1, 100), _row(2, 5)])
    check("有余额 → PlatformRejected", isinstance(exc, shared.PlatformRejected),
          type(exc).__name__)
    check("有余额 → 503", getattr(exc, "status", None) == 503,
          getattr(exc, "status", None))
    check("有余额 → accounts_cooling", getattr(exc, "code", "") ==
          "accounts_cooling", getattr(exc, "code", ""))
    body = json.loads(exc.openai().body)
    check("有余额 → 错误体不带 family", "family" not in body["error"], body)

    exc = _run_attempt([_row(1, None), _row(2, None)])
    check("余额未知(None) → 仍是 503 accounts_cooling",
          isinstance(exc, shared.PlatformRejected)
          and getattr(exc, "code", "") == "accounts_cooling",
          f"{type(exc).__name__} {getattr(exc, 'code', '')}")

    exc = _run_attempt([_row(1, 0), _row(2, 100)])
    check("部分号有余额 → 仍是 503 accounts_cooling",
          isinstance(exc, shared.PlatformRejected)
          and getattr(exc, "code", "") == "accounts_cooling",
          f"{type(exc).__name__} {getattr(exc, 'code', '')}")


if __name__ == "__main__":
    test_classify_business_codes()
    test_classify_regressions()
    test_error_payload_quota_family()
    test_error_payload_upstream_internal()
    test_error_payload_untouched_kinds()
    test_fail_end_to_end()
    test_pool_exhausted_is_402()
    test_pool_still_cooling_control()
    print()
    print(f"通过 {PASS} / {PASS + FAIL}")
    sys.exit(1 if FAIL else 0)
