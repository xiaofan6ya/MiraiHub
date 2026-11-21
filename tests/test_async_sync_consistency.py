# -*- coding: utf-8 -*-
"""async / sync 调用不匹配的回归测试（758ab76 marvis 全量 502）。

## 事故

2026-11-17 部署 758ab76 后，marvis 的每个请求都 502：

    HTTP 502 {"error":{"message":"上游平台异常：cannot unpack non-iterable
              coroutine object","type":"server_error"}}

    File ".../marvis/gateway.py", line 1031, in chat_completions
        key, resp, account, started = await asyncio.to_thread(...)
    File ".../marvis/gateway.py", line 985, in _acquire
        resp, account, started = _attempt(db, request, key, body, realm, ...)
    TypeError: cannot unpack non-iterable coroutine object

29f245f 同步上游 2742025 时，_attempt / _call_upstream 被改成了 async def
（本地旧的「每步 to_thread」写法），但 _acquire 用的是上游的**同步** def ——
它在工作线程里同步调用 async 函数，拿到的是 coroutine 对象。

## 本文件钉死的三件事

1. **全量静态扫描** admin/platform/**/*.py：同步上下文调用 async def（含
   "a, b = f()" 元组解包）、asyncio.to_thread(f) 里 f 是 coroutine function、
   await f() 里 f 是普通 def —— 三类都不许出现。
2. **marvis 的形状**：_attempt / _call_upstream / _acquire 同步，
   chat_completions 用 asyncio.to_thread(_acquire, ...) 把它们挪出事件循环。
3. **真跑一遍**：_acquire 与 chat_completions 主路径端到端跑通。758ab76 上
   这两个用例会直接抛 TypeError。
"""
import ast
import inspect
import os
import pathlib
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

# admin/db.py 在模块级按 settings 建引擎（默认 MySQL）；先固定成 SQLite 文件库。
os.environ.setdefault(
    "ADMIN_DATABASE_URL",
    "sqlite:///" + str(pathlib.Path(os.environ.get("TEMP", "/tmp"))
                       / "async_sync_test.db"),
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PLATFORM = ROOT / "admin" / "platform"

#: 这些名字的调用结果是「可等待对象」，传给它们不算漏 await。
PASS_THROUGH = {"create_task", "gather", "shield", "ensure_future", "wait_for",
                "run_coroutine_threadsafe", "to_thread", "StreamingResponse"}


# ---------------------------------------------------------------------------
# 静态扫描（AST）
# ---------------------------------------------------------------------------
def _has_yield(node) -> bool:
    """只数**本函数自己的** yield（不递归进嵌套函数）。"""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        cur = stack.pop()
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(cur, (ast.Yield, ast.YieldFrom)):
            return True
        stack.extend(ast.iter_child_nodes(cur))
    return False


def _defs(tree) -> dict:
    """名字 -> async / asyncgen / sync。async generator 不算 coroutine。"""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            out.setdefault(node.name, "asyncgen" if _has_yield(node) else "async")
        elif isinstance(node, ast.FunctionDef):
            out.setdefault(node.name, "sync")
    return out


def _module_of(path: pathlib.Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _index() -> dict:
    """admin.* 模块名 -> {函数名: kind}（跨模块解析用）。"""
    index = {}
    for path in (ROOT / "admin").rglob("*.py"):
        index[_module_of(path)] = _defs(
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path)))
    return index


def _parents(tree) -> dict:
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return list(reversed(parts))
    return None


def _aliases(tree) -> dict:
    alias = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.asname:
                    alias[item.asname] = item.name
                else:
                    head = item.name.split(".")[0]
                    alias[head] = head
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for item in node.names:
                alias[item.asname or item.name] = node.module + "." + item.name
    return alias


def _resolve(call_func, local, alias, index):
    chain = _dotted(call_func)
    if not chain:
        return None
    head, rest = chain[0], chain[1:]
    if head in alias:
        target = alias[head] + ("." + ".".join(rest) if rest else "")
        if "." in target:
            mod, _, name = target.rpartition(".")
            if mod in index and name in index[mod]:
                return index[mod][name], target
        return None
    if not rest and head in local:
        return local[head], head
    return None


def _enclosing_async(node, parents) -> bool:
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, ast.AsyncFunctionDef):
            return True
        if isinstance(cur, (ast.FunctionDef, ast.Lambda)):
            return False
        cur = parents.get(cur)
    return False


def _awaited(call, parents) -> bool:
    cur = parents.get(call)
    while isinstance(cur, ast.Call):
        cur = parents.get(cur)
    return isinstance(cur, ast.Await)


def _handed_to(call, parents, alias) -> bool:
    cur = parents.get(call)
    if isinstance(cur, ast.Call):
        name = getattr(cur.func, "attr", None) or getattr(cur.func, "id", None)
        if name in PASS_THROUGH:
            return True
        if cur.args and cur.args[0] is call:
            chain = _dotted(cur.func)
            if chain and chain[0] in alias:
                target = ".".join([alias[chain[0]]] + chain[1:])
                return target == "asyncio.run"
    return False


def _is_method_return(node, parents) -> bool:
    """同步**方法**里的 return <coroutine> = 协议约定的「返回可等待对象」。

    admin/platform/gateway.py 的 Gateway 协议把转发入口声明成
    "def chat_completions(...) -> Any"：实现方返回 coroutine，由调用方 await。
    各平台 platform_gateway.py 全是这一行，不算漏 await。
    """
    parent = parents.get(node)
    if not isinstance(parent, ast.Return):
        return False
    cur = parents.get(parent)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.Lambda)):
            args = getattr(cur, "args", None)
            names = [a.arg for a in (args.posonlyargs + args.args)] if args else []
            return bool(names) and names[0] == "self"
        if isinstance(cur, ast.AsyncFunctionDef):
            return False
        cur = parents.get(cur)
    return False


def scan_file(path: pathlib.Path, index: dict) -> list:
    """返回 [(kind, lineno, message)]；kind ∈ unawaited / to_thread / await-sync。"""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    local = _defs(tree)
    alias = _aliases(tree)
    parents = _parents(tree)
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        hit = _resolve(node.func, local, alias, index)
        if hit is None:
            continue
        kind, label = hit
        parent = parents.get(node)
        if isinstance(parent, ast.Call):
            pname = getattr(parent.func, "attr", None) or getattr(
                parent.func, "id", None)
            if pname == "to_thread" and parent.args and parent.args[0] is node:
                if kind in ("async", "asyncgen"):
                    findings.append((
                        "to_thread", node.lineno,
                        "to_thread(%s) 里是 %s —— 工作线程只会拿到 "
                        "coroutine/agen 对象" % (label, kind)))
                continue
        if kind == "asyncgen":
            continue
        if kind == "async":
            if _awaited(node, parents) or _handed_to(node, parents, alias):
                continue
            if _is_method_return(node, parents):
                continue
            findings.append((
                "unawaited", node.lineno,
                "同步上下文调用 async %s —— %s" % (
                    label,
                    "漏了 await" if _enclosing_async(node, parents)
                    else "协程从未 await，返回 coroutine 对象")))
        elif kind == "sync" and _awaited(node, parents):
            findings.append((
                "await-sync", node.lineno,
                "await 了普通 def %s —— 不是 coroutine" % label))
    return findings


def scan_platform() -> list:
    index = _index()
    problems = []
    for path in sorted(PLATFORM.rglob("*.py")):
        for kind, line, message in scan_file(path, index):
            problems.append("%s:%d [%s] %s" % (
                path.relative_to(ROOT).as_posix(), line, kind, message))
    return problems


class AsyncSyncConsistencyTests(unittest.TestCase):
    """全量静态扫描：admin/platform 下不许再有 async/sync 混用。"""

    @classmethod
    def setUpClass(cls):
        cls.problems = scan_platform()

    def test_no_async_call_in_sync_context(self):
        hits = [p for p in self.problems if "[unawaited]" in p]
        self.assertEqual(
            [], hits,
            "同步上下文调用了 async 函数（758ab76 的 502 就是这个形状）：\n"
            + "\n".join(hits))

    def test_no_coroutine_function_passed_to_to_thread(self):
        hits = [p for p in self.problems if "[to_thread]" in p]
        self.assertEqual(
            [], hits,
            "asyncio.to_thread 收到 coroutine function —— 线程里拿到的是对象：\n"
            + "\n".join(hits))

    def test_no_await_on_plain_function(self):
        hits = [p for p in self.problems if "[await-sync]" in p]
        self.assertEqual(
            [], hits,
            "await 了普通 def —— TypeError: object ... can't be used in await：\n"
            + "\n".join(hits))

    def test_scanner_would_catch_the_original_bug(self):
        """自检：把 758ab76 的写法喂给扫描器，必须报出来。

        没有这条自检，扫描器静默失效（比如名字解析错）时上面三条会永远绿。
        """
        source = (
            "async def _attempt(db):\n"
            "    return 1\n"
            "\n"
            "def _acquire():\n"
            "    resp, account = _attempt(None)\n"
            "    return resp\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "broken.py"
            path.write_text(source, encoding="utf-8")
            findings = scan_file(path, {})
        self.assertEqual(["unawaited"], [f[0] for f in findings],
                         "扫描器没抓到 a, b = async_fn()：%r" % (findings,))


class ScannerCoroutineRunnerRegressionTests(unittest.TestCase):
    """持久回归测试：只认可导入的 asyncio.run 的首个位置参数。"""

    def _scan(self, imports, invocation):
        source = (imports + "\n\nasync def task():\n    return 1\n\n"
                  "def main():\n    " + invocation + "\n")
        with tempfile.TemporaryDirectory(prefix="async-scanner-regression-") as tmp:
            path = pathlib.Path(tmp) / "runner.py"
            path.write_text(source, encoding="utf-8")
            return [kind for kind, _line, _message in scan_file(path, {})]

    def test_asyncio_run_consumes_its_first_positional_coroutine(self):
        cases = (
            ("import asyncio", "asyncio.run(task())"),
            ("import asyncio as aio", "aio.run(task())"),
            ("from asyncio import run", "run(task())"),
            ("from asyncio import run as drive", "drive(task())"),
        )
        for imports, invocation in cases:
            with self.subTest(imports=imports, invocation=invocation):
                self.assertEqual(self._scan(imports, invocation), [])

    def test_custom_run_does_not_hide_unawaited_coroutines(self):
        cases = (
            ("", "runner.run(task())"),
            ("def run(value):\n    return value", "run(task())"),
            ("import custom_runner as aio", "aio.run(task())"),
        )
        for imports, invocation in cases:
            with self.subTest(imports=imports, invocation=invocation):
                self.assertEqual(self._scan(imports, invocation), ["unawaited"])

    def test_asyncio_run_does_not_consume_other_arguments(self):
        for invocation in ("asyncio.run(None, task())",
                           "asyncio.run(None, debug=task())",
                           "asyncio.run(main=task())"):
            with self.subTest(invocation=invocation):
                self.assertEqual(self._scan("import asyncio", invocation),
                                 ["unawaited"])

    def test_asyncio_run_does_not_hide_to_thread_misuse(self):
        self.assertEqual(
            self._scan("import asyncio", "asyncio.run(asyncio.to_thread(task()))"),
            ["to_thread"])


class MarvisShapeTests(unittest.TestCase):
    """marvis 的正确形状：同步的 _attempt 被同步的 _acquire 在工作线程里调。"""

    def setUp(self):
        from admin.platform.marvis import gateway

        self.gateway = gateway

    def test_attempt_is_sync(self):
        self.assertFalse(
            inspect.iscoroutinefunction(self.gateway._attempt),
            "_attempt 必须是同步 def：它整段是阻塞调用，只能跑在工作线程里")

    def test_call_upstream_is_sync(self):
        """真正发上游 HTTP 的函数必须是普通 def。

        `_acquire` 整条跑在工作线程里：`client.open_chat_stream` /
        `client.chat_completions` 内部是 `urllib.request.urlopen`（阻塞到
        上游吐出状态行 + 响应头）。它们若是 async —— 线程里拿到 coroutine，
        解包即 `cannot unpack non-iterable coroutine object`；在已经在线程里的
        上下文再套一层 `to_thread` 则只是多一次切换。
        """
        for fn in (self.gateway.client.open_chat_stream,
                   self.gateway.client.chat_completions):
            self.assertFalse(
                inspect.iscoroutinefunction(fn),
                "%s 必须是同步 def：阻塞调用只由工作线程承载" % fn.__name__)
            # 只看代码，不看 docstring（docstring 里提到 to_thread 是说明文字）。
            node = ast.parse(textwrap.dedent(
                inspect.getsource(fn))).body[0]
            if (isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)):
                node.body = node.body[1:]
            self.assertNotIn("asyncio.to_thread", ast.unparse(node),
                             "工作线程里再套 to_thread 只会多一次上下文切换")

    def test_acquire_call_style_matches_attempt(self):
        """_acquire 里的调用方式必须与 _attempt 的 async 属性一致。

        这是 758ab76 的直接判据：async _attempt + 同步 _acquire = 解包崩溃。
        """
        source = inspect.getsource(self.gateway._acquire)
        awaited = "await _attempt(" in source
        self.assertEqual(
            awaited, inspect.iscoroutinefunction(self.gateway._attempt),
            "_acquire 用 %s 调 _attempt，但 _attempt 是 %s" % (
                "await" if awaited else "同步",
                "async" if inspect.iscoroutinefunction(self.gateway._attempt)
                else "同步"))

    def test_chat_completions_offloads_acquire(self):
        source = inspect.getsource(self.gateway.chat_completions)
        self.assertIn("asyncio.to_thread(", source)
        self.assertIn("_acquire", source)
        self.assertNotIn("await _acquire(", source,
                         "直接 await _acquire 会把 urlopen 拉回事件循环")


class _FakeDb:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _Account:
    id = 7


class _FakeRequest:
    """_acquire / chat_completions 需要的最小表面。"""

    def __init__(self, payload=None):
        self.headers = {}
        self.query_params = {}
        self.client = type("C", (), {"host": "127.0.0.1"})()
        self._payload = payload or {}

    async def json(self):
        return self._payload


def _patch_acquire_internals(gateway, result):
    """把 _acquire 内部的阻塞实现全换成假件（不碰 async/sync 形状）。"""
    import admin.db

    account = _Account()
    stack = [
        mock.patch.object(admin.db, "SessionLocal", _FakeDb),
        mock.patch.object(gateway.shared, "authenticate",
                          lambda db, request, platform=None: "KEY"),
        mock.patch.object(gateway.shared, "select_account",
                          lambda db, **kw: account),
        mock.patch.object(gateway.shared, "lease", lambda acc: True),
        mock.patch.object(gateway.shared, "release", lambda acc: None),
        mock.patch.object(gateway.shared, "note_success", lambda acc: None),
        mock.patch.object(gateway.marvis_accounts, "ensure_token",
                          lambda db, acc: {"t": "x"}),
        mock.patch.object(gateway.client, "open_chat_stream",
                          lambda data, realm, body, *, account_id=0: result),
        mock.patch.object(gateway.client, "chat_completions",
                          lambda data, realm, body, *, account_id=0: result),
    ]
    return stack, account


class AcquireRuntimeTests(unittest.TestCase):
    """真跑 _acquire：758ab76 上这里就是 cannot unpack non-iterable coroutine。"""

    def test_acquire_returns_four_tuple(self):
        from admin.platform.marvis import gateway

        stack, account = _patch_acquire_internals(gateway, "RESP")
        for patcher in stack:
            patcher.start()
            self.addCleanup(patcher.stop)

        key, resp, got_account, started = gateway._acquire(
            _FakeRequest(), {"model": "main-auto"}, "cn", stream=True)

        self.assertEqual("KEY", key)
        self.assertEqual("RESP", resp)
        self.assertIs(account, got_account)
        self.assertGreater(started, 0, "started 必须是 time.perf_counter()")

    def test_acquire_closes_session(self):
        from admin.platform.marvis import gateway

        sessions = []

        def factory():
            sessions.append(_FakeDb())
            return sessions[-1]

        stack, _ = _patch_acquire_internals(gateway, "RESP")
        stack[0] = mock.patch("admin.db.SessionLocal", factory)
        for patcher in stack:
            patcher.start()
            self.addCleanup(patcher.stop)

        gateway._acquire(_FakeRequest(), {"model": "main-auto"}, "cn",
                         stream=True)
        self.assertEqual(1, len(sessions))
        self.assertTrue(sessions[0].closed, "会话必须在本线程 finally 里关掉")


class ChatCompletionsSmokeTests(unittest.IsolatedAsyncioTestCase):
    """主路径 smoke：chat_completions 端到端不许抛 TypeError。"""

    async def test_non_stream_returns_json_response(self):
        from admin.platform.marvis import gateway

        result = {"choices": [{"message": {"content": "ok"}}],
                  "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        stack, _ = _patch_acquire_internals(gateway, result)
        billed = []

        async def fake_bill(*args, **kwargs):
            billed.append((args, kwargs))

        stack.extend([
            mock.patch.object(gateway, "_validate_model",
                              lambda body: "main-auto"),
            mock.patch.object(gateway, "_prepare_body",
                              lambda body, slot: dict(body)),
            mock.patch.object(gateway, "_bill_async", fake_bill),
        ])
        for patcher in stack:
            patcher.start()
            self.addCleanup(patcher.stop)

        response = await gateway.chat_completions(
            _FakeRequest({"model": "deepseek-v4.1-flash",
                          "messages": [{"role": "user", "content": "hi"}]}))

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(billed), "非流式必须记一笔账")
        body = response.body.decode("utf-8")
        self.assertIn("ok", body)

    async def test_stream_returns_streaming_response(self):
        from admin.platform.marvis import gateway

        class _Resp:
            def read(self, _size=-1):
                return b""

            def close(self):
                return None

        stack, _ = _patch_acquire_internals(gateway, _Resp())
        stack.extend([
            mock.patch.object(gateway, "_validate_model",
                              lambda body: "main-auto"),
            mock.patch.object(gateway, "_prepare_body",
                              lambda body, slot: dict(body)),
            mock.patch.object(gateway.client, "close_stream",
                              lambda resp: None),
        ])
        for patcher in stack:
            patcher.start()
            self.addCleanup(patcher.stop)

        response = await gateway.chat_completions(
            _FakeRequest({"model": "deepseek-v4.1-flash", "stream": True,
                          "messages": [{"role": "user", "content": "hi"}]}))

        self.assertEqual(200, response.status_code)
        self.assertIsNotNone(getattr(response, "body_iterator", None),
                             "流式分支必须返回可迭代的 StreamingResponse")
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        # 这条只钉「流式主路径跑得通、吐出来的是合法 SSE」：
        # 758ab76 上 `await asyncio.to_thread(_acquire, ...)` 在解包 coroutine
        # 时直接 TypeError，连 200 都拿不到。收尾帧由转发器自己决定补不补，
        # 不在这里规定（上游与本地在这一点的策略不同，不该互相钉死）。
        self.assertTrue(
            all(isinstance(c, (bytes, bytearray)) for c in chunks),
            "流式体必须原样是 bytes：%r" % ([type(c) for c in chunks],))
        self.assertEqual([], [c for c in chunks if not c.endswith(b"\n\n")],
                         "每个非空块都必须是完整 SSE 事件（以空行收尾）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
