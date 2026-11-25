"""Persistent Loomy response-ownership regressions; synthetic HTTP objects only.

The client is imported as a private package with a mock upstream opener. Tests do
not load application configuration, databases, credentials, or network services.
"""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
import types
import urllib.error

import pytest


CORE = Path(__file__).resolve().parents[1] / "admin/platform/loomy/core"
CHUNK = {"model": "synthetic-model", "choices": [{"delta": {"content": "hello"}}]}


class Response:
    def __init__(self, lines=(), *, content_type="text/event-stream", body=b"",
                 read_error=None, close_error=None):
        self.lines = iter(lines)
        self.headers = {"content-type": content_type}
        self.body = body
        self.read_error = read_error
        self.close_error = close_error
        self.close_calls = 0

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self.lines)
        except StopIteration:
            if self.read_error is not None:
                raise self.read_error
            raise

    def read(self):
        if self.read_error is not None:
            raise self.read_error
        return self.body

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


def line(value):
    return ("data: " + json.dumps(value) + "\n").encode()


@pytest.fixture
def client(monkeypatch):
    name = "_loomy_response_ownership_test"
    package = types.ModuleType(name)
    package.__path__ = [str(CORE)]
    monkeypatch.setitem(sys.modules, name, package)
    spec = importlib.util.spec_from_file_location(name + ".client", CORE / "client.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.endpoints, "base_of", lambda realm: "https://synthetic.invalid")
    monkeypatch.setattr(module.endpoints, "chat_headers", lambda *args, **kwargs: {})
    monkeypatch.setattr(module, "_chat_session_id", lambda *args: "")
    monkeypatch.setattr(module, "note_sentinel", lambda *args, **kwargs: None)
    upstream = types.ModuleType("admin.upstream_http")
    pending = []

    def opened(*args, **kwargs):
        value = pending.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    upstream.urllib_opener = lambda: types.SimpleNamespace(open=opened)
    monkeypatch.setitem(sys.modules, "admin.upstream_http", upstream)
    yield module, pending
    sys.modules.pop(name + ".endpoints", None)


def test_never_started_close_releases_open_response_once(client):
    module, pending = client
    response = Response()
    pending.append(response)
    stats = {}
    stream = module.chat_stream("synthetic-token", "cn", {}, stats=stats)
    assert response.close_calls == 0
    stream.close()
    stream.close()
    assert response.close_calls == 1
    assert list(stream) == []
    assert stats == {}  # No bytes or timing observations were fabricated.


@pytest.mark.parametrize("mode", ["eof", "partial-close", "read-error"])
def test_started_stream_closes_once_and_preserves_observed_chunks_and_stats(client, mode):
    module, pending = client
    failure = OSError("synthetic read failure")
    response = Response([line(CHUNK)], read_error=failure if mode == "read-error" else None,
                        close_error=OSError("synthetic close failure"))
    pending.append(response)
    ticks = iter((10.0, 10.25))
    module.time = types.SimpleNamespace(perf_counter=lambda: next(ticks))
    stats = {}
    stream = module.chat_stream("synthetic-token", "cn", {}, stats=stats)
    assert next(stream) == CHUNK
    if mode == "read-error":
        with pytest.raises(OSError) as caught:
            next(stream)
        assert caught.value is failure
    elif mode == "eof":
        with pytest.raises(StopIteration):
            next(stream)
    else:
        stream.close()
    stream.close()
    assert response.close_calls == 1
    assert stats == {"ttfb_ms": 250, "total_ms": 250}
    assert module.LAST_TTFB_MS.get() == 250
    assert module.LAST_TOTAL_MS.get() == 250


@pytest.mark.parametrize("mode", ["business-json", "empty-json", "body-read-error", "empty-sse"])
def test_pre_chunk_failures_close_response_and_keep_existing_classification(client, mode):
    module, pending = client
    response = Response(
        content_type="text/event-stream" if mode == "empty-sse" else "application/json",
        body=b'{"code":"100002","desc":"synthetic expired"}' if mode == "business-json" else b"",
        read_error=OSError("synthetic read failure") if mode == "body-read-error" else None)
    pending.append(response)
    stats = {}
    stream = module.chat_stream("synthetic-token", "cn", {}, stats=stats)
    with pytest.raises(module.UpstreamError) as caught:
        next(stream)
    assert caught.value.kind == ("auth" if mode == "business-json" else "bad_response")
    assert response.close_calls == 1
    stream.close()
    assert response.close_calls == 1
    assert stats == {"ttfb_ms": 0, "total_ms": 0}


@pytest.mark.parametrize("mode", ["normal", "read-error", "close-error"])
def test_http_error_response_is_closed_before_original_classification_raises(client, mode):
    module, pending = client

    class ErrorBody(io.BytesIO):
        def __init__(self):
            super().__init__(b'{"error":{"message":"synthetic quota"}}')
            self.close_calls = 0

        def close(self):
            self.close_calls += 1
            super().close()
            if mode == "close-error":
                raise OSError("synthetic close failure")

        def read(self, *args):
            if mode == "read-error":
                raise OSError("synthetic error-body read failure")
            return super().read(*args)

    body = ErrorBody()
    error = urllib.error.HTTPError("https://synthetic.invalid", 402, "quota", {}, body)
    pending.append(error)
    with pytest.raises(module.UpstreamError) as caught:
        module.chat_stream("synthetic-token", "cn", {})
    assert caught.value.kind == "quota_exceeded"
    assert caught.value.status == 402
    assert body.close_calls == 1


def test_close_waits_for_active_read_before_releasing_response(client):
    module, pending = client
    reading, release, close_started, close_finished = (threading.Event() for _ in range(4))

    class BlockingResponse(Response):
        def __next__(self):
            reading.set()
            assert release.wait(2), "synthetic blocking read was not released"
            return super().__next__()

    response = BlockingResponse([line(CHUNK)])
    pending.append(response)
    stream = module.chat_stream("synthetic-token", "cn", {})
    values, failures = [], []

    def read():
        try:
            values.append(next(stream))
        except BaseException as exc:
            failures.append(exc)

    def close():
        close_started.set()
        try:
            stream.close()
        except BaseException as exc:
            failures.append(exc)
        finally:
            close_finished.set()

    reader = threading.Thread(target=read, daemon=True)
    closer = threading.Thread(target=close, daemon=True)
    try:
        reader.start()
        assert reading.wait(2)
        closer.start()
        assert close_started.wait(2)
        assert not close_finished.is_set()
        assert response.close_calls == 0
    finally:
        release.set()
        reader.join(2)
        if closer.ident is not None:
            closer.join(2)
    assert not reader.is_alive() and not closer.is_alive()
    assert values == [CHUNK]
    assert failures == []
    assert response.close_calls == 1
    stream.close()
    assert response.close_calls == 1
