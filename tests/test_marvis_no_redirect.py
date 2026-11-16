"""验证 _NoRedirect 是否**真的**拦住了 302（而不是被默认 handler 抢先）。

关键区别：
  · `build_opener()` 后再 `add_handler(_NoRedirect())` —— 追加，默认
    HTTPRedirectHandler 仍在链上，302 照样被跟随（旧实现，坏的）；
  · `build_opener(_NoRedirect())` —— 子类替换父类，302 真的停下（新实现）。

用本地 302 + Set-Cookie 桩验证两者差异，再验证生产代码走的是后者。
"""
import http.server
import io
import socketserver
import sys
import threading
import urllib.error
import urllib.request

sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("PASS  " + name)
    else:
        fail += 1
        print("FAIL  " + name + ("  " + str(extra) if extra else ""))


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/oauth"):
            self.send_response(302)
            self.send_header("Set-Cookie", "accesstoken=mv_REAL; Path=/")
            self.send_header("Set-Cookie", "openid=oREAL; Path=/")
            self.send_header("Location", "/landing")
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                "<!DOCTYPE html><html><head><title>登录成功</title></head></html>"
                .encode())


class ND(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_a, **_k):
        return None


with socketserver.TCPServer(("127.0.0.1", 0), H) as srv:
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/oauth" % port

    def run(opener, label):
        try:
            r = opener.open(url, timeout=10)
            return r.status, r.headers, r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, b""

    print("=== A) 旧写法：build_opener() 后 add_handler（坏的）===")
    old = urllib.request.build_opener()
    old.add_handler(ND())
    st, hd, body = run(old, "old")
    sc = hd.get_all("Set-Cookie") or []
    print("  status =", st, "| Set-Cookie =", sc)
    print("  body   =", body[:60])
    check("旧写法会被跟随（状态变 200）", st == 200, st)
    check("旧写法拿不到 Set-Cookie（这就是 bug）", not sc, sc)
    check("旧写法 body 是 HTML",
          b"<!DOCTYPE" in body or b"<html" in body, body[:40])

    print()
    print("=== B) 新写法：build_opener(ND())（子类替换，对的）===")
    new = urllib.request.build_opener(ND())
    st2, hd2, body2 = run(new, "new")
    sc2 = hd2.get_all("Set-Cookie") or []
    print("  status =", st2, "| Set-Cookie =", sc2)
    check("新写法停在 302", st2 == 302, st2)
    check("新写法拿到 Set-Cookie", bool(sc2), sc2)
    joined = " ".join(sc2)
    check("有 accesstoken", "accesstoken=mv_REAL" in joined, joined)
    check("有 openid", "openid=oREAL" in joined, joined)

    srv.shutdown()

print()
print("=== C) 生产代码是否走新写法 ===")
src = open("admin/platform/marvis/core/client.py", encoding="utf-8").read()
start = src.index("def _no_redirect_opener")
body = src[start:start + 1200]
# `_NoRedirect` 必须以**位置参数**交给某个 build_opener（直接或经
# upstream_http 转发），不能被 add_handler 追加。
import ast as _ast

_pos = False
for _n in _ast.walk(_ast.parse(src)):
    if (isinstance(_n, _ast.Call) and isinstance(_n.func, _ast.Name)
            and _n.func.id in ("build_opener", "fn")):
        for _a in _n.args:
            if isinstance(_a, _ast.Call) and isinstance(_a.func, _ast.Name) \
                    and _a.func.id == "_NoRedirect":
                _pos = True
            if isinstance(_a, _ast.Name) and _a.id == "_NoRedirect":
                _pos = True
check("_NoRedirect 作为 build_opener 位置参数", _pos)
check("有兼容降级（老版 upstream_http 也能用）",
      "accepts" in src and "inspect.signature" in src)

up = open("admin/upstream_http.py", encoding="utf-8").read()
check("build_opener 支持 extra_handlers",
      "extra_handlers" in up and "for handler in extra_handlers" in up)

print()
print("%d/%d passed" % (ok, ok + fail))
sys.exit(1 if fail else 0)
