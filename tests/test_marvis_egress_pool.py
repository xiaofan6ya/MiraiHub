"""出口池泛化后的行为验证（**不带任何真实出口数据**）。

判据（「空池也能独立跑通」这件事必须被证明，不是靠嘴说）：

  1. 仓库里**只有** `egress_pool.example.json`，没有 `egress_pool.json`；
  2. 空池 → `pick()` 返回 ""，`_opener()` 仍是平台级 opener，不抛异常；
  3. 同一账号永远同一出口（跨调用、跨 reload 都稳定）；
  4. `MARVIS_EGRESS_POOL`（内联 JSON / 逗号串）与
     `MARVIS_EGRESS_POOL_FILE`（文件）都能生效；
  5. 配了出口 → opener 装的是 `ProxyHandler(真实代理)`；
     没配 → 与改动前一致（`ProxyHandler({})`，硬禁代理）；
  6. `open_chat_stream()` 空池走直连、不绑 contextvar；有池就绑；
     传输层失败回退平台出口，业务错误**不回退**（不二次记账）。

跑法：`python tests/test_marvis_egress_pool.py`（仓库现有测试都是这个形态）。
"""
import io
import os
import sys
import tempfile
import urllib.request

sys.path.insert(0, ".")
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace")

from admin import upstream_http
from admin.platform.marvis.core import client, egress

ok = fail = 0
_ENV_KEYS = ("MARVIS_EGRESS_POOL", "MARVIS_EGRESS_POOL_FILE",
             "MIRAIHUB_MARVIS_PROXY")


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS  " + name)
    else:
        fail += 1
        print("FAIL  " + name + ("  " + str(extra) if extra else ""))


def env(**kw):
    """改环境变量（None = 删掉），返回恢复函数。"""
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    for key, value in kw.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    egress.reload()

    def restore():
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        egress.reload()

    return restore


def proxy_handler(opener):
    for handler in opener.handlers:
        if isinstance(handler, urllib.request.ProxyHandler):
            return handler
    return None


def main():
    # ---- 1. 仓库里不带真值文件 ----
    check("仓库只带 egress_pool.example.json（不带 egress_pool.json）",
          not os.path.exists(egress.DEFAULT_POOL_FILE),
          egress.DEFAULT_POOL_FILE)
    example = os.path.join(os.path.dirname(egress.DEFAULT_POOL_FILE),
                           "egress_pool.example.json")
    check("egress_pool.example.json 存在", os.path.exists(example))

    # ---- 2. 空池：安全 ----
    restore = env(MARVIS_EGRESS_POOL=None, MARVIS_EGRESS_POOL_FILE=None)
    try:
        picked = [egress.pick(i) for i in (0, 1, "acct-x", None)]
        check("空池 pick() 全返回空串", picked == ["", "", "", ""], picked)
        check("空池 exits() 为空列表", egress.exits() == [])
        opener = client._opener()
        check("空池 _opener() 可用（不抛异常）", opener is not None)
        check("空池 _opener() 走平台级 opener（不是按出口缓存）",
              client._opener_for("") is not None)
        direct = client._opener_for("")
        check("空池 _opener_for(\"\") == 平台级 opener",
              direct is opener, (id(direct), id(opener)))
    finally:
        restore()

    # ---- 3. / 4. 池来源 ----
    pool = ["http://egress-a.example:8080", "http://egress-b.example:8080"]
    restore = env(MARVIS_EGRESS_POOL='["%s", "%s"]' % tuple(pool))
    try:
        check("内联 JSON 数组被解析", egress.exits() == pool, egress.exits())
        first = egress.pick(189)
        again = [egress.pick(189) for _ in range(20)]
        check("同一账号恒定同一出口", set(again) == {first}, first)
        egress.reload()
        check("reload 后仍稳定", egress.pick(189) == first)
        spread = {egress.pick(i) for i in range(64)}
        check("不同账号会分散到多个出口", spread == set(pool), spread)
        check("pick 只会返回池内出口",
              all(egress.pick(i) in pool for i in range(200)))
    finally:
        restore()

    restore = env(MARVIS_EGRESS_POOL="http://a.example:1 , http://b.example:2 ,")
    try:
        check("逗号串被解析（含尾逗号）",
              egress.exits() == ["http://a.example:1", "http://b.example:2"],
              egress.exits())
    finally:
        restore()

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "pool.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"exits": ["http://c.example:3"]}')
        restore = env(MARVIS_EGRESS_POOL=None, MARVIS_EGRESS_POOL_FILE=path)
        try:
            check("MARVIS_EGRESS_POOL_FILE 生效",
                  egress.exits() == ["http://c.example:3"], egress.exits())
        finally:
            restore()
        # 早期用法：环境变量直接给文件路径
        restore = env(MARVIS_EGRESS_POOL=path, MARVIS_EGRESS_POOL_FILE=None)
        try:
            check("MARVIS_EGRESS_POOL 给文件路径也能读（兼容旧用法）",
                  egress.exits() == ["http://c.example:3"], egress.exits())
        finally:
            restore()
        restore = env(MARVIS_EGRESS_POOL=None,
                      MARVIS_EGRESS_POOL_FILE=os.path.join(tmp, "nope.json"))
        try:
            check("文件不存在 → 空池，不抛异常", egress.exits() == [])
        finally:
            restore()

    # ---- 5. opener 上的 ProxyHandler ----
    opener = upstream_http.build_opener(None, proxy="http://p.example:9000")
    handler = proxy_handler(opener)
    check("build_opener(proxy=) 装 ProxyHandler",
          handler is not None and handler.proxies == {
              "http": "http://p.example:9000",
              "https": "http://p.example:9000"}, handler and handler.proxies)
    # 不传 proxy 时必须与改动前**完全等价**：handler 链一模一样，
    # 且链里没有任何带代理的 ProxyHandler（= 不读系统代理 / HTTP_PROXY）。
    plain_new = upstream_http.build_opener(None, proxy=None)
    plain_old = upstream_http.build_opener(None)
    names = lambda opener: [type(h).__name__ for h in opener.handlers]
    check("不传 proxy 时 handler 链与改动前一致",
          names(plain_new) == names(plain_old), names(plain_new))
    check("不传 proxy 时不装任何代理",
          proxy_handler(plain_new) is None,
          [getattr(h, "proxies", None) for h in plain_new.handlers])

    restore = env(MIRAIHUB_MARVIS_PROXY="http://platform.example:3128")
    try:
        upstream_http.reset_platform_openers()
        check("platform_proxy_url 读 MIRAIHUB_<平台>_PROXY",
              upstream_http.platform_proxy_url("marvis")
              == "http://platform.example:3128")
        handler = proxy_handler(upstream_http.platform_opener("marvis"))
        check("platform_opener 装上该代理",
              handler is not None
              and handler.proxies.get("https") == "http://platform.example:3128")
        opener_a = upstream_http.platform_opener("marvis")
        opener_b = upstream_http.platform_opener("marvis")
        check("platform_opener 有缓存（同一实例）", opener_a is opener_b)
        upstream_http.reset_platform_openers()
        check("reset_platform_openers 后重建新实例",
              upstream_http.platform_opener("marvis") is not opener_a)
        check("其它平台不受影响",
              upstream_http.platform_proxy_url("loomy") == "")
    finally:
        restore()
        upstream_http.reset_platform_openers()

    # ---- 6. open_chat_stream 的出口绑定 / 回退 ----
    real_direct = client._open_chat_stream_direct
    seen = []
    behaviour = {"raise": None}

    def fake_direct(credential, realm, body, *, account_id=0):
        proxy = client._EGRESS_VAR.get()
        seen.append(proxy)
        if behaviour["raise"] and proxy:
            raise behaviour["raise"]
        return "resp"

    client._open_chat_stream_direct = fake_direct
    try:
        restore = env(MARVIS_EGRESS_POOL=None, MARVIS_EGRESS_POOL_FILE=None)
        try:
            seen.clear()
            check("空池：open_chat_stream 返回正常",
                  client.open_chat_stream({}, "r", {}) == "resp")
            check("空池：不绑出口（contextvar 为空）", seen == [""], seen)
        finally:
            restore()

        restore = env(MARVIS_EGRESS_POOL='["http://egress-a.example:8080"]')
        try:
            seen.clear()
            client.open_chat_stream({}, "r", {}, account_id=7)
            check("有池：请求绑到该账号出口",
                  seen == ["http://egress-a.example:8080"], seen)
            check("退出后 contextvar 已还原",
                  client._EGRESS_VAR.get() == "", client._EGRESS_VAR.get())

            seen.clear()
            behaviour["raise"] = client.UpstreamError("boom", kind="network")
            check("传输层失败 → 回退平台出口",
                  client.open_chat_stream({}, "r", {}) == "resp")
            check("回退那次确实没绑出口", seen[-1] == "", seen)

            seen.clear()
            behaviour["raise"] = client.UpstreamError("biz",
                                                      kind="upstream_business")
            raised = None
            try:
                client.open_chat_stream({}, "r", {})
            except client.UpstreamError as exc:
                raised = exc
            check("业务错误不重发（原样抛出）", raised is not None, raised)
            check("业务错误只发了一次", len(seen) == 1, seen)
        finally:
            behaviour["raise"] = None
            restore()

        seen.clear()
        opener_a = client._opener_for("http://egress-a.example:8080")
        opener_b = client._opener_for("http://egress-a.example:8080")
        check("按出口缓存 opener", opener_a is opener_b)
        handler = proxy_handler(opener_a)
        check("按出口的 opener 里是这条代理",
              handler is not None
              and handler.proxies.get("https") == "http://egress-a.example:8080",
              handler and handler.proxies)
        client.reset_egress_openers()
        check("reset_egress_openers 后重建",
              client._opener_for("http://egress-a.example:8080") is not opener_a)
    finally:
        client._open_chat_stream_direct = real_direct
        client.reset_egress_openers()

    print()
    print("%d/%d passed" % (ok, ok + fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
