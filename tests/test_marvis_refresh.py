#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Marvis 续签契约测试（不联网、不连库）。

锁的是 2026-11-16 打通的这条链：

    POST /marvis_client_login/marvis_refresh_token
    body {"userInfo":{"openId","refreshToken","accessToken","loginType":"Marvis"}}

以前一直回 `-109 RefreshToken missing required param (openid, guid, token)`，
原因是上游从 `body.userInfo.{openId,refreshToken,accessToken}` 里取参数，
而本仓发的是**扁平的** `{openid,guid,token,...}` —— 少一层嵌套、字段名大小写
也不对，还多了 guid。错误文案又只说「缺参数」，所以从文案反推永远推不出来。

运行：
  python tests/test_marvis_refresh.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from admin.platform.marvis.core import client, endpoints  # noqa: E402
from admin.platform.marvis.services import accounts as macc  # noqa: E402

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


class _FakeAccount:
    def __init__(self, account_id: int = 1, auth: dict | None = None):
        self.id = account_id
        self.auth_json = json.dumps(auth or {}, ensure_ascii=False)
        self.domain = "cn"
        self.status = "active"
        self.last_err_msg = ""
        self.last_err_at = None
        self.cool_until = None
        self.cool_kind = ""


class _FakeDB:
    def __init__(self) -> None:
        self.commits = 0

    def refresh(self, _obj) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1


def _capture_request(result: dict | Exception):
    """临时替换 `client._request`，返回 (captured, restore)。"""
    captured: dict = {}

    def fake(url, *, method="GET", body=None, raw_body=None, headers=None,
             timeout=None, account_id=0, realm=""):
        captured.update({"url": url, "method": method, "raw_body": raw_body,
                         "headers": dict(headers or {})})
        if isinstance(result, Exception):
            raise result
        return result

    original = client._request
    client._request = fake

    def restore():
        client._request = original

    return captured, restore


def test_refresh_body_shape() -> None:
    print("\n== 续签 body 形状（打通的唯一关键）==")
    body = endpoints.refresh_body({
        "openid": "oAN1i2QoNnAee", "access_token": "mv_abc",
        "refresh_token": "rt_xyz", "guid": "d34db33f", "login_type": "2",
    })
    payload = json.loads(body)
    check("最外层是 userInfo 一层嵌套", list(payload.keys()) == ["userInfo"], payload)
    inner = payload.get("userInfo") or {}
    check("字段名是驼峰 openId", "openId" in inner and "openid" not in inner, inner)
    check("带 refreshToken", inner.get("refreshToken") == "rt_xyz", inner)
    check("带 accessToken", inner.get("accessToken") == "mv_abc", inner)
    check("loginType 是字面量 Marvis（不是 1/2）",
          inner.get("loginType") == "Marvis", inner)
    check("body 里不带 guid（它只走请求头）", "guid" not in inner, inner)
    check("紧凑 JSON（签名对象不能带空格）", " " not in body, body)
    check("loginType 常量与 SDK 一致", endpoints.REFRESH_LOGIN_TYPE == "Marvis")


def test_refresh_success() -> None:
    print("\n== 续签成功：解析 + 回写字段 ==")
    now = int(time.time())
    captured, restore = _capture_request({
        "code": 0, "msg": "RC_SUCCESS",
        "user_info": {"access_token": "mv_new", "refresh_token": "",
                      "expires_in": "7200", "openid": "oAN1i2QoNnAee"},
    })
    try:
        grant = client.refresh_credentials(
            "", "cn", account_id=7,
            credential={"openid": "oAN1i2QoNnAee", "access_token": "mv_old",
                        "refresh_token": "rt_old", "guid": "d34db33f",
                        "login_type": "2"})
    finally:
        restore()

    check("发的是 POST", captured.get("method") == "POST", captured.get("method"))
    check("打的是 marvis_refresh_token",
          captured.get("url", "").endswith(endpoints.REFRESH_PATH),
          captured.get("url"))
    sent = json.loads(captured.get("raw_body") or "{}")
    check("实发 body 也是 userInfo 嵌套",
          (sent.get("userInfo") or {}).get("openId") == "oAN1i2QoNnAee", sent)
    check("签名头存在",
          bool((captured.get("headers") or {}).get("Ual-Access-Signature")))
    check("Guid 头存在",
          (captured.get("headers") or {}).get("Ual-Access-Guid") == "d34db33f",
          captured.get("headers"))

    check("expires_in 字符串被解析成秒", grant.get("expires_in") == 7200, grant)
    check("expires_at 约为 now+7200",
          abs(int(grant.get("expires_at") or 0) - (now + 7200)) <= 5, grant)
    check("refresh_token 空串 → 回落旧值（不清票）",
          grant.get("refresh_token") == "rt_old", grant)
    check("access_token 取响应里的新值", grant.get("access_token") == "mv_new",
          grant)


def test_refresh_failures() -> None:
    print("\n== 续签失败：致命 / 非致命要分开 ==")
    captured, restore = _capture_request({"code": -109,
                                          "msg": "RefreshToken missing required param"})
    try:
        client.refresh_credentials("", "cn", credential={
            "openid": "o1", "access_token": "mv", "refresh_token": "rt",
            "guid": "g"})
        check("-109 应抛异常", False)
    except client.UpstreamError as exc:
        check("-109 不算登录态死（kind 不是 auth）", exc.kind != "auth", exc.kind)
        check("-109 带上游 code", exc.code == "-109", exc.code)
        check("-109 文案说明是本端问题", "本端" in exc.message, exc.message)
    finally:
        restore()

    captured, restore = _capture_request({"code": -107, "msg": "RC_NO_AUTH_ERR"})
    try:
        client.refresh_credentials("", "cn", credential={
            "openid": "o1", "access_token": "mv", "refresh_token": "rt",
            "guid": "g"})
        check("-107 应抛异常", False)
    except client.UpstreamError as exc:
        check("-107 判为登录态死（kind=auth）", exc.kind == "auth", exc.kind)
    finally:
        restore()

    # -114：参数形状对、但这张 refresh_token 已经不能用（2026-11-16 真机撞到）
    captured, restore = _capture_request({
        "code": -114, "user_info": None,
        "msg": 'type:business, code:-114, msg:callback( {"error":100036,'
               '"error_description":"invalid refresh token"} );'})
    try:
        client.refresh_credentials("", "cn", credential={
            "openid": "o1", "access_token": "mv", "refresh_token": "rt",
            "guid": "g"})
        check("-114 应抛异常", False)
    except client.UpstreamError as exc:
        check("-114 判为登录态死（refresh_token 失效）", exc.kind == "auth",
              exc.kind)
        check("-114 文案引导重新扫码", "重新扫码" in exc.message, exc.message)
    finally:
        restore()

    try:
        client.refresh_credentials("", "cn", credential={
            "openid": "o1", "access_token": "mv"})
        check("缺 refresh_token 应抛", False)
    except client.UpstreamError as exc:
        check("缺 refresh_token 报 credential_incomplete",
              exc.code == "credential_incomplete", exc.code)

    check("致命码含 -107/-101/-114",
          client.FATAL_REFRESH_CODES == frozenset({-107, -101, -114}),
          client.FATAL_REFRESH_CODES)


def test_needs_refresh() -> None:
    print("\n== 何时该续签 ==")
    now = int(time.time())
    check("没有 refresh_token 一律不续",
          macc.needs_refresh({"access_token": "mv"}, now=now) is False)
    check("还有 1 小时 → 不续",
          macc.needs_refresh({"refresh_token": "rt", "expires_at": now + 3600},
                             now=now) is False)
    check("只剩 5 分钟 → 续",
          macc.needs_refresh({"refresh_token": "rt", "expires_at": now + 300},
                             now=now) is True)
    check("已过期 → 续",
          macc.needs_refresh({"refresh_token": "rt", "expires_at": now - 60},
                             now=now) is True)
    check("毫秒级 expires_at 自动降级",
          macc.needs_refresh({"refresh_token": "rt",
                              "expires_at": (now + 300) * 1000}, now=now) is True)
    # 没有 expires_at 时按登录方式推断
    check("微信签发 2 小时 → 续",
          macc.needs_refresh({"refresh_token": "rt", "login_type": "WX",
                              "issued_at": now - 7200}, now=now) is True)
    check("QQ 签发 3 小时 → 不续",
          macc.needs_refresh({"refresh_token": "rt", "login_type": "QC",
                              "issued_at": now - 3 * 3600}, now=now) is False)
    check("老凭证没 issued_at → 先续一次",
          macc.needs_refresh({"refresh_token": "rt"}, now=now) is True)
    check("续签失败退避期内不再重试",
          macc.needs_refresh({"refresh_token": "rt",
                              "refresh_failed_at": now - 60}, now=now) is True)
    check("被判死（refresh_dead）的号不再续签",
          macc.needs_refresh({"refresh_token": "rt", "refresh_dead": 1,
                              "expires_at": now - 10}, now=now) is False)
    check("hint 标出 needs_relogin",
          macc.token_expiry_hint({"refresh_token": "rt", "refresh_dead": 1,
                                  "expires_at": now - 10}
                                 ).get("needs_relogin") is True)


def test_expiry_hint_prefers_upstream() -> None:
    print("\n== 时效提示优先用上游 expires_at ==")
    now = int(time.time())
    soon = macc.token_expiry_hint({"refresh_token": "rt",
                                   "expires_at": now + 300, "login_type": "WX"})
    check("剩 5 分钟 → soon", soon["state"] == "soon", soon)
    check("source 标 upstream", soon.get("source") == "upstream", soon)
    check("带 refreshable 标记", soon.get("refreshable") is True, soon)

    expired = macc.token_expiry_hint({"expires_at": now - 10})
    check("已过期 → expired", expired["state"] == "expired", expired)

    # 有真值时不再被推断 TTL 带偏：QQ 号签发 30 天但上游说还有 10 分钟
    mixed = macc.token_expiry_hint({"expires_at": now + 600, "login_type": "QC",
                                    "issued_at": now - 30 * 24 * 3600})
    check("真值优先于 issued_at 推断", mixed["state"] == "soon", mixed)


def test_refresh_account_writes_back() -> None:
    print("\n== refresh_account 写回 auth_json ==")
    now = int(time.time())
    account = _FakeAccount(11, {"openid": "o1", "access_token": "mv_old",
                                "refresh_token": "rt_old", "guid": "g",
                                "login_type": "2", "issued_at": now - 7300})
    db = _FakeDB()
    captured, restore = _capture_request({
        "code": 0, "msg": "RC_SUCCESS",
        "user_info": {"access_token": "mv_old", "refresh_token": "rt_new",
                      "expires_in": "7200"},
    })
    try:
        result = macc.refresh_account(db, account)
    finally:
        restore()

    data = json.loads(account.auth_json)
    check("返回 ok", result.get("ok") is True, result)
    check("写回 refresh_token", data.get("refresh_token") == "rt_new", data)
    check("写回 expires_at",
          abs(int(data.get("expires_at") or 0) - (now + 7200)) <= 5, data)
    check("issued_at 重新起算",
          abs(int(data.get("issued_at") or 0) - now) <= 5, data)
    check("清空错误信息", account.last_err_msg == "", account.last_err_msg)
    check("提交了一次", db.commits >= 1, db.commits)

    # 失败路径：记下 refresh_failed_at + 错误，不抛
    account2 = _FakeAccount(12, {"openid": "o2", "access_token": "mv",
                                 "refresh_token": "rt", "guid": "g"})
    db2 = _FakeDB()
    captured, restore = _capture_request({"code": -109, "msg": "missing param"})
    try:
        result2 = macc.refresh_account(db2, account2)
    finally:
        restore()
    data2 = json.loads(account2.auth_json)
    check("失败不抛、返回 ok=False", result2.get("ok") is False, result2)
    check("-109 不标 fatal", result2.get("fatal") is False, result2)
    check("记下失败时间戳（退避用）", bool(data2.get("refresh_failed_at")), data2)
    check("失败也写 last_err_msg", bool(account2.last_err_msg), account2.last_err_msg)

    # 致命失败（-114 refresh_token 失效）→ 标 refresh_dead，别再打接口
    account3 = _FakeAccount(13, {"openid": "o3", "access_token": "mv",
                                 "refresh_token": "rt", "guid": "g"})
    captured, restore = _capture_request({
        "code": -114, "user_info": None,
        "msg": 'callback( {"error":100036,"error_description":'
               '"invalid refresh token"} );'})
    try:
        result3 = macc.refresh_account(_FakeDB(), account3)
    finally:
        restore()
    data3 = json.loads(account3.auth_json)
    check("-114 标 fatal", result3.get("fatal") is True, result3)
    check("-114 写 refresh_dead", data3.get("refresh_dead") == 1, data3)
    check("标死后 needs_refresh 返回 False",
          macc.needs_refresh(data3) is False, data3)


def test_ensure_token_flow() -> None:
    print("\n== ensure_token：临期才续、失败不阻断 ==")
    now = int(time.time())

    # 不临期 → 不该发续签请求
    account = _FakeAccount(21, {"openid": "o1", "access_token": "mv",
                                "refresh_token": "rt", "guid": "g",
                                "expires_at": now + 3600, "login_type": "2"})
    called = {"n": 0}
    original = client.refresh_credentials

    def fake_refresh(*_a, **_k):
        called["n"] += 1
        return {"access_token": "mv", "refresh_token": "rt",
                "expires_in": 7200, "expires_at": now + 7200}

    client.refresh_credentials = fake_refresh
    try:
        data = macc.ensure_token(_FakeDB(), account)
        check("不临期 → 不发续签", called["n"] == 0, called)
        check("返回原有凭证", data.get("access_token") == "mv", data)

        # 临期 → 续
        account2 = _FakeAccount(22, {"openid": "o1", "access_token": "mv",
                                     "refresh_token": "rt", "guid": "g",
                                     "expires_at": now + 60, "login_type": "2"})
        macc.ensure_token(_FakeDB(), account2)
        check("临期 → 续签一次", called["n"] == 1, called)
        check("续完 expires_at 推进",
              int(json.loads(account2.auth_json).get("expires_at") or 0) > now + 3600,
              account2.auth_json)

        # 续签失败（非 force）→ 仍返回旧票，不抛
        def boom(*_a, **_k):
            raise client.UpstreamError("续签被拒", status=401, code="-109",
                                       kind="refresh_rejected")

        client.refresh_credentials = boom
        account3 = _FakeAccount(23, {"openid": "o1", "access_token": "mv",
                                     "refresh_token": "rt", "guid": "g",
                                     "expires_at": now + 60, "login_type": "2"})
        data3 = macc.ensure_token(_FakeDB(), account3)
        check("非 force 下续签失败仍返回旧票",
              data3.get("access_token") == "mv", data3)

        # force → 抛
        try:
            macc.ensure_token(_FakeDB(), _FakeAccount(24, {
                "openid": "o1", "access_token": "mv", "refresh_token": "rt",
                "guid": "g", "login_type": "2"}), force=True)
            check("force 下续签失败应抛", False)
        except client.UpstreamError:
            check("force 下续签失败抛出给调用方", True)
    finally:
        client.refresh_credentials = original


def test_gateway_wiring() -> None:
    print("\n== Gateway 接线（静态检查）==")
    gw_src = (ROOT / "admin" / "platform" / "marvis" / "platform_gateway.py"
              ).read_text(encoding="utf-8")
    init_src = (ROOT / "admin" / "platform" / "marvis" / "__init__.py"
                ).read_text(encoding="utf-8")
    sched_src = (ROOT / "admin" / "scheduler.py").read_text(encoding="utf-8")

    check("task_runners 声明 marvis_keepalive",
          '"marvis_keepalive": _run_keepalive' in gw_src)
    check("__init__ 也登记 marvis_keepalive",
          '"marvis_keepalive": _pg._run_keepalive' in init_src)
    check("keepalive 走 _run_keepalive",
          "def keepalive(self, db):\n        return _run_keepalive" in gw_src)
    check("手动刷新会先续签",
          "ensure_token(db, account, force=True)" in gw_src)
    check("调度默认周期 30 分钟（微信 2 小时档接得上）",
          '"marvis_keepalive": ("续签保活", 30)' in sched_src)


if __name__ == "__main__":
    test_refresh_body_shape()
    test_refresh_success()
    test_refresh_failures()
    test_needs_refresh()
    test_expiry_hint_prefers_upstream()
    test_refresh_account_writes_back()
    test_ensure_token_flow()
    test_gateway_wiring()
    print()
    print(f"通过 {PASS} / {PASS + FAIL}")
    sys.exit(1 if FAIL else 0)
