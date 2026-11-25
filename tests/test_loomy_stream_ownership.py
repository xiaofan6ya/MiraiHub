"""Persistent synthetic regressions for Loomy stream/lease ownership."""
import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from admin.platform import protocol_bridge as bridge
from admin.platform.loomy import gateway


def _wait_for_flag(flag, timeout=2.0):
    """3.10 兼容：等 Event 置位，超时抛 AssertionError（替掉 3.11 才有的 asyncio.timeout）。"""
    deadline = time.monotonic() + timeout
    while not flag.is_set():
        if time.monotonic() >= deadline:
            raise AssertionError("等待事件置位超时")
        time.sleep(0.005)


class Database:
    def __init__(self):
        self.closed = 0
        self.threads = [threading.get_ident()]

    def commit(self):
        self.threads.append(threading.get_ident())

    def rollback(self):
        self.threads.append(threading.get_ident())

    def close(self):
        self.threads.append(threading.get_ident())
        self.closed += 1


class Chunks:
    def __init__(self, values=(), failure=None):
        self.values = iter(values)
        self.failure = failure
        self.closed = 0
        self.reads = 0
        self.threads = []

    def __iter__(self):
        return self

    def __next__(self):
        self.reads += 1
        self.threads.append(threading.get_ident())
        try:
            return next(self.values)
        except StopIteration:
            if self.failure is not None:
                raise self.failure
            raise

    def close(self):
        self.closed += 1
        self.threads.append(threading.get_ident())


def request():
    return Request({"type": "http", "method": "POST", "path": "/v1/chat/completions",
                    "headers": [], "query_string": b""})


@pytest.fixture
def setup(monkeypatch):
    import admin.db
    state = SimpleNamespace(db=None, success=[], release=[], bills=[], chunks=Chunks())
    state.account = SimpleNamespace(id=7)
    state.open_stream = gateway._open_stream

    def database():
        state.db = Database()
        return state.db

    def opened(db, *args, **kwargs):
        db.threads.append(threading.get_ident())
        return state.chunks, state.account, None, time.perf_counter(), "m"

    def bill(*args, **kwargs):
        state.bills.append((args, kwargs))

    monkeypatch.setattr(admin.db, "SessionLocal", database)
    monkeypatch.setattr(gateway.shared, "authenticate", lambda *a, **k: SimpleNamespace(id=2))
    monkeypatch.setattr(gateway, "_open_stream", opened)
    monkeypatch.setattr(gateway.shared, "release", state.release.append)
    monkeypatch.setattr(gateway.shared, "note_success", state.success.append)
    monkeypatch.setattr(gateway, "_bill", bill)
    monkeypatch.setattr(gateway, "_usage_with_credit", lambda usage, model: dict(usage))
    return state


@pytest.mark.parametrize("relay", [None, "anthropic", "responses"])
def test_closing_unstarted_response_releases_owned_chunks_and_session(setup, relay):
    async def run():
        result = await gateway._forward_stream({"model": "m", "stream": True}, request())
        if relay:
            factory = bridge.anthropic_stream_relay if relay == "anthropic" else bridge.responses_stream_relay
            result = factory(result, "m")
            await result.body_iterator.__anext__()
        await result.body_iterator.aclose()
        await result.body_iterator.aclose()

    asyncio.run(run())
    assert setup.chunks.closed == 1 and setup.db.closed == 1
    assert len(setup.release) == 1 and not setup.success
    assert setup.bills[0][1]["error"]


def test_normal_eof_owns_reads_close_and_db_on_one_worker(setup):
    setup.chunks = Chunks([{"choices": [{"delta": {"content": "hi"}}]},
                            {"usage": {"total_tokens": 7}}])

    async def run():
        result = await gateway._forward_stream({"model": "m", "stream": True}, request())
        return b"".join([piece async for piece in result.body_iterator])

    body = asyncio.run(run())
    assert b'"hi"' in body and body.endswith(b"data: [DONE]\n\n")
    assert setup.chunks.closed == setup.db.closed == 1
    assert len(setup.success) == len(setup.release) == len(setup.bills) == 1
    assert setup.bills[0][0][3]["usage"]["total_tokens"] == 7
    assert not setup.bills[0][1]["error"]
    assert len(set(setup.db.threads + setup.chunks.threads)) == 1


@pytest.mark.parametrize("mode", ["inband", "upstream", "transport"])
def test_failures_preserve_observed_usage_and_never_mark_success(setup, mode):
    values = [{"usage": {"total_tokens": 5}, "choices": [{"delta": {"content": "hi"}}]}]
    if mode == "inband":
        values.append({"error": {"message": "synthetic rejection", "type": "upstream"}})
        failure = None
    elif mode == "upstream":
        failure = gateway.client.UpstreamError("synthetic rejection", kind="network")
    else:
        failure = OSError("synthetic reset")
    setup.chunks = Chunks(values, failure=failure)

    async def run():
        result = await gateway._forward_stream({"model": "m", "stream": True}, request())
        try:
            return b"".join([piece async for piece in result.body_iterator])
        except OSError:
            assert mode == "transport"
            return b""

    asyncio.run(run())
    assert setup.chunks.closed == setup.db.closed == 1
    assert not setup.success and len(setup.release) == 1
    assert setup.bills[0][1]["error"]
    assert setup.bills[0][0][3]["usage"]["total_tokens"] == 5


def test_auth_failure_closes_session(setup, monkeypatch):
    def rejected(*args, **kwargs):
        raise gateway.shared.PlatformRejected(401, "synthetic invalid key")

    monkeypatch.setattr(gateway.shared, "authenticate", rejected)
    with pytest.raises(gateway.shared.PlatformRejected):
        asyncio.run(gateway._forward_stream({"model": "m", "stream": True}, request()))
    assert setup.db.closed == 1
    assert not setup.release and not setup.bills


def test_close_during_blocked_next_waits_then_stops_without_more_reads(setup):
    entered, unblock = threading.Event(), threading.Event()

    class Blocking(Chunks):
        def __next__(self):
            self.reads += 1
            self.threads.append(threading.get_ident())
            entered.set()
            assert unblock.wait(3)
            return {"choices": [{"delta": {"content": "late"}}]}

    setup.chunks = Blocking()

    async def run():
        result = await gateway._forward_stream({"model": "m", "stream": True}, request())
        reader = asyncio.create_task(result.body_iterator.__anext__())
        try:
            await asyncio.to_thread(_wait_for_flag, entered)
            closing = asyncio.create_task(result.body_iterator.aclose())
            await asyncio.sleep(0.02)
            assert not closing.done()
            assert not setup.db.closed and not setup.release
            unblock.set()
            await closing
            await asyncio.gather(reader, return_exceptions=True)
        finally:
            unblock.set()
            if not reader.done():
                reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)

    asyncio.run(run())
    assert setup.chunks.reads == 1
    assert setup.chunks.closed == setup.db.closed == 1
    assert not setup.success and len(setup.release) == 1


def test_cancel_during_open_reclaims_late_result_on_same_worker(setup, monkeypatch):
    entered, unblock = threading.Event(), threading.Event()

    def opened(db, *args, **kwargs):
        entered.set()
        assert unblock.wait(3)
        return setup.chunks, setup.account, None, time.perf_counter(), "m"

    monkeypatch.setattr(gateway, "_open_stream", opened)

    async def run():
        task = asyncio.create_task(gateway._forward_stream({"model": "m", "stream": True}, request()))
        try:
            await asyncio.to_thread(_wait_for_flag, entered)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not setup.db.closed
            unblock.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            unblock.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert setup.chunks.closed == setup.db.closed == 1
    assert len(setup.release) == 1 and not setup.success


@pytest.mark.parametrize("failed", [False, True])
def test_peek_replay_preserves_close_and_failed_peek_is_not_a_success(setup, monkeypatch, failed):
    monkeypatch.setattr(gateway, "_open_stream", setup.open_stream)
    monkeypatch.setattr(gateway.shared, "select_account", lambda *a, **k: setup.account)
    monkeypatch.setattr(gateway.shared, "lease", lambda account: True)
    monkeypatch.setattr(gateway.shared, "exclude_identity", lambda *a, **k: None)
    monkeypatch.setattr(gateway.shared, "note_failure", lambda *a, **k: None)
    monkeypatch.setattr(gateway.loomy_accounts, "ensure_token", lambda *a, **k: {})
    monkeypatch.setattr(gateway.loomy_accounts, "token_of", lambda data: "synthetic")
    monkeypatch.setattr(gateway.client, "chat_stream", lambda *a, **k: setup.chunks)
    monkeypatch.setattr(gateway, "MAX_ATTEMPTS", 1)
    setup.chunks = Chunks([{"model": "m", "choices": [{"delta": {"content": "peeked"}}]}])
    if failed:
        setup.chunks = Chunks([{}], failure=gateway.client.UpstreamError(
            "synthetic parameter rejection", status=400, kind="invalid_request"))

    async def close():
        result = await gateway._forward_stream({"model": "m", "stream": True}, request())
        if not failed:
            await result.body_iterator.aclose()

    asyncio.run(close())
    assert setup.chunks.closed == setup.db.closed == 1
    assert len(setup.release) == 1 and not setup.success
    if failed:
        assert not setup.bills


def test_heartbeat_does_not_start_a_second_read(setup, monkeypatch):
    entered, unblock = threading.Event(), threading.Event()

    class Blocking(Chunks):
        def __next__(self):
            self.reads += 1
            entered.set()
            assert unblock.wait(3)
            raise StopIteration

    setup.chunks = Blocking()
    monkeypatch.setattr(bridge, "PING_INTERVAL_S", 0.01)

    async def run():
        result = await gateway._forward_stream({"model": "m", "stream": True}, request())
        try:
            for _ in range(3):
                piece = await result.body_iterator.__anext__()
                assert piece == bridge.CHAT_PING
                assert setup.chunks.reads == 1
            unblock.set()
            await result.body_iterator.aclose()
        finally:
            unblock.set()

    asyncio.run(run())
    assert setup.chunks.closed == setup.db.closed == 1


def test_response_construction_failure_reclaims_opened_resources(setup, monkeypatch):
    original = RuntimeError("synthetic header encoding failure")

    def fail(*args, **kwargs):
        raise original

    monkeypatch.setattr(bridge, "owned_stream_response", fail)
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(gateway._forward_stream({"model": "m", "stream": True}, request()))
    assert caught.value is original
    assert setup.chunks.closed == setup.db.closed == 1
    assert len(setup.release) == 1 and not setup.success


def test_auth_error_remains_primary_when_database_cleanup_also_fails(setup, monkeypatch):
    original = gateway.shared.PlatformRejected(401, "synthetic invalid key")
    cleanup = RuntimeError("synthetic database close failure")

    def rejected(*args, **kwargs):
        raise original

    def close(db):
        db.closed += 1
        raise cleanup

    monkeypatch.setattr(gateway.shared, "authenticate", rejected)
    monkeypatch.setattr(Database, "close", close)
    with pytest.raises(gateway.shared.PlatformRejected) as caught:
        asyncio.run(gateway._forward_stream({"model": "m", "stream": True}, request()))
    assert caught.value is original and caught.value.__cause__ is cleanup
    assert setup.db.closed == 1
