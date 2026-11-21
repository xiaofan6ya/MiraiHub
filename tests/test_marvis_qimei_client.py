"""Persistent experimental QIMEI client regressions; synthetic/mock traffic only.

Private RSA keys remain in memory. No sockets, real SDK service, deployment
configuration, metadata collectors, or private-key files are used by these tests.
Async tests use asyncio.run and HTTPX MockTransport with observable raw streams.
"""
from __future__ import annotations

import asyncio
import base64
import builtins
from dataclasses import FrozenInstanceError
from functools import lru_cache
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import time
import types

import httpx
import pytest
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


CORE = Path(__file__).resolve().parents[1] / "admin/platform/marvis/core"
KEY = bytes(range(16))
IV = bytes(range(16, 32))
INNER_KEY = bytes(range(32, 48))
INNER_IV = bytes.fromhex("B8221969C27A8019D533B139ADE71BCC")
QIMEI = "0123456789abcdef01234567300089abcdef"
ENDPOINT = "https://qimei.example.invalid/experimental"


@lru_cache(maxsize=1)
def _client_module():
    source = CORE / "qimei_client.py"
    assert source.is_file(), "experimental QIMEI client is not implemented"
    name = "qimei_client_regression"
    package = types.ModuleType(name)
    package.__path__ = [str(CORE)]
    package.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
    sys.modules[name] = package
    spec = importlib.util.spec_from_file_location(name + ".qimei_client", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def _private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _header():
    fields = {number: b"" for number in range(1, 14)}
    fields.update({1: 1, 3: 3, 4: b"public-app", 5: b"5.1.4.2", 13: 0})
    return fields


def _arguments(**changes):
    module = _client_module()
    return {
        "inner_version": 123, "inner_key": INNER_KEY,
        "rsa_public_key_pem": _private_key().public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo),
        "key": KEY, "iv": IV, "nonce": bytes.fromhex("0011223380aafffe"),
        "timestamp_ms": 1700000000123, "ticks_100ns": 17000000001234567,
        "signing": module.QimeiSigningConfig(version=0, seed8=bytes(range(8)), tail4=bytes(4)),
        "rsa_padding": bytes(range(1, 222)),
        **changes,
    }


def _prepare():
    return _client_module().prepare_request(_header(), {2: b"synthetic-device", 49: 3},
                                             **_arguments())


def _decrypt(ciphertext, key, iv):
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def _wire_fields(data):
    offset = 0
    fields = {}

    def varint():
        nonlocal offset
        value = shift = 0
        while True:
            byte = data[offset]
            offset += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
            shift += 7

    while offset < len(data):
        tag = varint()
        number, wire_type = tag >> 3, tag & 7
        if wire_type == 0:
            fields[number] = varint()
        else:
            assert wire_type == 2
            length = varint()
            fields[number] = data[offset:offset + length]
            offset += length
    return fields


def _decode_request(body):
    packet = json.loads(body)
    secrets = _private_key().decrypt(base64.b64decode(packet["ky"]), rsa_padding.PKCS1v15())
    key, iv = secrets[:16], secrets[16:]
    inner = _decrypt(base64.b64decode(packet["pms"]), key, iv)
    assert inner[:5] == b"\x03\x00\x00\x00{"
    frame_fields = _wire_fields(_decrypt(inner[5:], INNER_KEY, INNER_IV))
    return packet, key, iv, _wire_fields(frame_fields[1]), _wire_fields(frame_fields[2])


def _response_body(key=KEY, iv=IV):
    plain = b"\x0a\x24" + QIMEI.encode("ascii") + b"\x12\x02\x00\xff"
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    data = base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode("ascii")
    return json.dumps({"code": 0, "data": data}, separators=(",", ":")).encode("ascii")


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, error=None, close_error=None):
        self.chunks = chunks
        self.error = error
        self.close_error = close_error
        self.closed = False
        self.close_count = 0
        self.yielded = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk
        if self.error is not None:
            raise self.error

    async def aclose(self):
        self.close_count += 1
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class HoldingStream(TrackingStream):
    def __init__(self, **kwargs):
        super().__init__([], **kwargs)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __aiter__(self):
        self.started.set()
        await self.release.wait()
        yield _response_body()


class StallingCloseStream(HoldingStream):
    """Cooperative close waits until cancelled or released by test cleanup."""

    def __init__(self, *, hold_body=False, read_error=None):
        super().__init__()
        self.hold_body = hold_body
        self.read_error = read_error
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_finished = asyncio.Event()
        self.close_cancelled = False

    async def __aiter__(self):
        self.started.set()
        if self.hold_body:
            await self.release.wait()
        if self.read_error is not None:
            raise self.read_error
        yield _response_body()

    async def aclose(self):
        self.close_count += 1
        self.closed = True
        self.close_started.set()
        try:
            await self.close_release.wait()
        except asyncio.CancelledError:
            self.close_cancelled = True
            raise
        finally:
            self.close_finished.set()


async def _send_with_stream(stream, *, status=200, response_headers=None,
                            prepared=None, send_changes=None, client_changes=None):
    module = _client_module()
    observed = []

    async def handler(request):
        observed.append(request)
        return httpx.Response(status, headers=response_headers, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 trust_env=False, **(client_changes or {})) as client:
        result = await module.send_request(
            prepared or _prepare(), endpoint=ENDPOINT,
            headers={"Content-Type": "application/example"}, client=client,
            **(send_changes or {}))
    return result, observed


def _assert_error(call):
    with pytest.raises(_client_module().QimeiClientError) as caught:
        call()
    assert "sensitive" not in str(caught.value)
    assert ENDPOINT not in str(caught.value)
    assert caught.value.__cause__ is None


def test_prepare_joins_wire_layers_and_binds_ext_to_the_copied_header_app_key():
    module = _client_module()
    header, device = _header(), {2: b"synthetic-device", 49: 3}
    original_header, original_device = header.copy(), device.copy()
    prepared = module.prepare_request(header, device, **_arguments())
    packet, key, iv, wire_header, wire_device = _decode_request(prepared.body)
    assert key == prepared.key == KEY
    assert iv == prepared.iv == IV
    assert wire_header == original_header
    assert wire_device == original_device
    assert json.loads(packet["ext"])["app_key"].encode() == wire_header[4] == b"public-app"
    assert header == original_header
    assert device == original_device
    header[4] = b"later-mutation"
    device[2] = b"later-mutation"
    assert _decode_request(prepared.body)[3:] == (original_header, original_device)


def test_prepare_copies_both_input_dicts_before_any_encoder_runs(monkeypatch):
    module = _client_module()
    header, device = _header(), {2: b"synthetic-device"}
    encode_header = module.encode_header

    def mutate_originals(copied_header):
        header[4] = b"changed-app"
        device[2] = b"changed-device"
        return encode_header(copied_header)

    monkeypatch.setattr(module, "encode_header", mutate_originals)
    prepared = module.prepare_request(header, device, **_arguments())
    packet, _, _, wire_header, wire_device = _decode_request(prepared.body)
    assert wire_header[4] == b"public-app"
    assert wire_device[2] == b"synthetic-device"
    assert json.loads(packet["ext"]) == {"app_key": "public-app"}


def test_prepared_context_is_frozen_and_excludes_body_key_and_iv_from_repr():
    prepared = _prepare()
    text = repr(prepared)
    for value in (prepared.body, prepared.key, prepared.iv):
        assert repr(value) not in text
        assert value.hex() not in text
    with pytest.raises(FrozenInstanceError):
        prepared.key = bytes(16)


class DictSubclass(dict):
    pass


class BytesSubclass(bytes):
    pass


class IntSubclass(int):
    pass


class FloatSubclass(float):
    pass


class StringSubclass(str):
    pass


@pytest.mark.parametrize("container", [None, [], (), DictSubclass({})])
@pytest.mark.parametrize("field", ["header", "device"])
def test_prepare_requires_exact_dict_inputs(container, field):
    header, device = _header(), {}
    if field == "header":
        header = container
    else:
        device = container
    _assert_error(lambda: _client_module().prepare_request(header, device, **_arguments()))


@pytest.mark.parametrize("request_type", [0, 2, True, 1.0, "1", IntSubclass(1), None])
def test_prepare_accepts_only_the_normal_exact_integer_request_type(request_type):
    header = _header()
    header[1] = request_type
    _assert_error(lambda: _client_module().prepare_request(header, {}, **_arguments()))


@pytest.mark.parametrize("changes", [{"inner_key": b"sensitive"}, {"inner_version": True},
                                    {"rsa_public_key_pem": b"sensitive invalid key"},
                                    {"signing": None}, {"timestamp_ms": -1}])
def test_prepare_normalizes_wire_packet_and_signing_errors(changes):
    _assert_error(lambda: _client_module().prepare_request(_header(), {}, **_arguments(**changes)))


def test_prepare_rejects_an_app_key_that_cannot_use_the_verified_printable_subset():
    header = _header()
    header[4] = b"sensitive\x00app"
    _assert_error(lambda: _client_module().prepare_request(header, {}, **_arguments()))


def test_preparation_uses_only_explicit_inputs_without_io_environment_entropy_or_clocks(monkeypatch):
    module = _client_module()
    arguments = _arguments()
    expected = module.prepare_request(_header(), {2: b"synthetic-device"}, **arguments)

    def forbidden(*args, **kwargs):
        raise AssertionError("preparation must consume only explicit inputs")

    class ForbiddenEnvironment(dict):
        __getitem__ = get = __iter__ = forbidden

    with monkeypatch.context() as context:
        for target, names in ((builtins, ("open",)), (io, ("open",)),
                              (os, ("open", "read", "write", "stat", "lstat", "listdir",
                                    "scandir", "urandom", "getenv")),
                              (time, ("time", "time_ns", "monotonic", "monotonic_ns")),
                              (socket, ("socket", "create_connection"))):
            for name in names:
                context.setattr(target, name, forbidden)
        context.setattr(os, "environ", ForbiddenEnvironment())
        assert module.prepare_request(_header(), {2: b"synthetic-device"}, **arguments) == expected


def test_mock_server_decrypts_request_and_encrypts_response_with_the_same_key_and_iv():
    module = _client_module()
    prepared = _prepare()
    streams = []
    requests = []

    async def handler(request):
        requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == ENDPOINT
        assert request.content == prepared.body
        assert request.headers["accept-encoding"] == "identity"
        assert request.headers["content-type"] == "application/example"
        assert request.extensions["timeout"] == {name: 3.0 for name in ("connect", "read", "write", "pool")}
        _, key, iv, _, _ = _decode_request(request.content)
        response = _response_body(key, iv)
        stream = TrackingStream([response[:17], response[17:]])
        streams.append(stream)
        return httpx.Response(200, headers={"Content-Length": str(len(response))}, stream=stream)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            return await module.send_request(prepared, endpoint=ENDPOINT,
                                             headers={"Content-Type": "application/example"}, client=client)

    result = asyncio.run(scenario())
    assert result.qimei36 == QIMEI
    assert result.fields == {1: QIMEI.encode(), 2: b"\x00\xff"}
    assert len(requests) == 1
    assert streams[0].closed and streams[0].close_count == 1


def test_injected_client_defaults_auth_cookies_headers_and_base_url_do_not_enter_request():
    module = _client_module()
    observed = []

    async def handler(request):
        observed.append(request)
        return httpx.Response(200, stream=TrackingStream([_response_body()]))

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False,
            base_url="https://unrelated.example.invalid/base/", timeout=99,
            auth=("sensitive-user", "sensitive-password"),
            cookies={"sensitive-cookie": "sensitive-value"},
            headers={"Authorization": "sensitive auth", "Cookie": "sensitive cookie",
                     "X-Unrelated": "sensitive default", "Accept-Encoding": "gzip"},
        ) as client:
            return await module.send_request(_prepare(), endpoint=ENDPOINT, headers={}, client=client)

    assert asyncio.run(scenario()).qimei36 == QIMEI
    assert len(observed) == 1
    request = observed[0]
    assert str(request.url) == ENDPOINT
    assert set(request.headers) == {"host", "content-length", "accept-encoding"}
    assert request.headers["accept-encoding"] == "identity"
    assert request.extensions["timeout"]["read"] == 3.0


def test_context_bytes_are_snapshotted_before_awaiting_the_transport():
    module = _client_module()
    prepared = _prepare()
    body = prepared.body

    async def handler(request):
        assert request.content == body
        object.__setattr__(prepared, "key", bytes(16))
        object.__setattr__(prepared, "iv", bytes(16))
        object.__setattr__(prepared, "body", b"changed after dispatch")
        return httpx.Response(200, stream=TrackingStream([_response_body()]))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            return await module.send_request(prepared, endpoint=ENDPOINT, headers={}, client=client)

    assert asyncio.run(scenario()).qimei36 == QIMEI


def test_redirect_is_not_followed_even_if_the_injected_client_enables_redirects():
    module = _client_module()
    requests = []
    streams = []

    async def handler(request):
        requests.append(request)
        stream = TrackingStream([_response_body()])
        streams.append(stream)
        return httpx.Response(302 if len(requests) == 1 else 200,
                              headers={"Location": "https://other.example.invalid/"}, stream=stream)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                     trust_env=False, follow_redirects=True) as client:
            return await module.send_request(_prepare(), endpoint=ENDPOINT, headers={}, client=client)

    _assert_error(lambda: asyncio.run(scenario()))
    assert len(requests) == 1
    assert streams[0].closed and streams[0].yielded == 0


@pytest.mark.parametrize("status", [199, 201, 204, 301, 400, 401, 429, 500])
def test_actual_http_status_must_be_exactly_200_and_response_is_closed(status):
    stream = TrackingStream([_response_body()])
    _assert_error(lambda: asyncio.run(_send_with_stream(stream, status=status)))
    assert stream.closed and stream.yielded == 0


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "br", "identity, gzip", "", "identity, identity"])
def test_compressed_or_ambiguous_content_encoding_is_rejected_before_reading(encoding):
    stream = TrackingStream([_response_body()])
    _assert_error(lambda: asyncio.run(_send_with_stream(
        stream, response_headers={"Content-Encoding": encoding})))
    assert stream.closed and stream.yielded == 0


@pytest.mark.parametrize("response_headers", [None, {"Content-Encoding": "identity"},
                                           {"Content-Encoding": "Identity"},
                                           {"Transfer-Encoding": "chunked"}])
def test_identity_or_chunked_raw_streams_decode_without_decompression(response_headers):
    response = _response_body()
    stream = TrackingStream([response[:3], response[3:31], response[31:]])
    result, _ = asyncio.run(_send_with_stream(stream, response_headers=response_headers))
    assert result.qimei36 == QIMEI
    assert stream.closed and stream.close_count == 1


@pytest.mark.parametrize("value", ["-1", "+1", "1.0", "", "sensitive", "1, 1", "999999999999999999999999999"])
def test_invalid_or_oversized_content_length_is_rejected_before_reading(value):
    stream = TrackingStream([_response_body()])
    _assert_error(lambda: asyncio.run(_send_with_stream(
        stream, response_headers={"Content-Length": value})))
    assert stream.closed and stream.yielded == 0


def test_duplicate_content_length_is_rejected_before_reading():
    stream = TrackingStream([_response_body()])
    _assert_error(lambda: asyncio.run(_send_with_stream(
        stream, response_headers=[("Content-Length", "1"), ("Content-Length", "1")])))
    assert stream.closed and stream.yielded == 0


@pytest.mark.parametrize("delta", [-1, 1])
def test_declared_content_length_must_match_the_complete_raw_body(delta):
    response = _response_body()
    stream = TrackingStream([response])
    _assert_error(lambda: asyncio.run(_send_with_stream(
        stream, response_headers={"Content-Length": str(len(response) + delta)})))
    assert stream.closed


def test_raw_streaming_limit_is_authoritative_and_checked_before_append(monkeypatch):
    module = _client_module()
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 10)

    class UnsafeAllocation(BaseException):
        pass

    class BoundedBuffer(bytearray):
        def extend(self, value):
            if len(self) + len(value) > 10:
                raise UnsafeAllocation("oversized raw chunk reached buffer append")
            super().extend(value)

    def forbidden_decode(*args, **kwargs):
        raise UnsafeAllocation("oversized response reached protocol decoding")

    monkeypatch.setattr(module, "bytearray", BoundedBuffer, raising=False)
    monkeypatch.setattr(module, "decode_response", forbidden_decode)
    stream = TrackingStream([b"123456", b"78901", b"not-read"])
    _assert_error(lambda: asyncio.run(_send_with_stream(stream)))
    assert stream.closed and stream.yielded == 2


def test_exact_response_limit_is_accepted_and_declared_oversize_never_reads(monkeypatch):
    module = _client_module()
    response = _response_body()
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", len(response))
    stream = TrackingStream([response])
    assert asyncio.run(_send_with_stream(stream))[0].qimei36 == QIMEI
    assert stream.closed
    oversized = TrackingStream([response])
    _assert_error(lambda: asyncio.run(_send_with_stream(
        oversized, response_headers={"Content-Length": str(len(response) + 1)})))
    assert oversized.closed and oversized.yielded == 0


@pytest.mark.parametrize("error", [httpx.ReadTimeout("sensitive timeout"),
                                  httpx.RemoteProtocolError("sensitive truncation"),
                                  ValueError("sensitive stream failure")])
def test_stream_errors_are_normalized_and_close_the_response_without_retry(error):
    stream = TrackingStream([b"{"], error=error)
    _assert_error(lambda: asyncio.run(_send_with_stream(stream)))
    assert stream.closed and stream.close_count == 1 and stream.yielded == 1


def test_close_failure_is_normalized_without_exposing_remote_details():
    stream = TrackingStream([_response_body()], close_error=RuntimeError("sensitive close detail"))
    _assert_error(lambda: asyncio.run(_send_with_stream(stream)))
    assert stream.closed and stream.close_count == 1


def test_transport_failure_is_normalized_without_retry_or_remote_details():
    module = _client_module()
    requests = []

    async def handler(request):
        requests.append(request)
        raise httpx.ConnectTimeout("sensitive endpoint and transport error")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            await module.send_request(_prepare(), endpoint=ENDPOINT, headers={}, client=client)

    _assert_error(lambda: asyncio.run(scenario()))
    assert len(requests) == 1


def test_total_deadline_covers_a_stalled_stream_and_closes_the_response():
    stream = HoldingStream()
    _assert_error(lambda: asyncio.run(_send_with_stream(
        stream, send_changes={"timeout_seconds": 0.01})))
    assert stream.closed and stream.close_count == 1


async def _watch_stalled_cleanup(stream, *, timeout, status=200, cancel=False):
    """Watch without injecting a second cancellation that could mask the bug."""
    module = _client_module()

    async def observed_send():
        try:
            await _send_with_stream(stream, status=status,
                                    send_changes={"timeout_seconds": timeout})
        except asyncio.CancelledError:
            return "cancelled", ""
        except KeyboardInterrupt:
            return "interrupted", ""
        except module.QimeiClientError as error:
            return "error", str(error)
        return "success", ""

    task = asyncio.create_task(observed_send())
    try:
        if cancel:
            await asyncio.wait_for(stream.started.wait(), timeout=1.0)
            task.cancel()
        await asyncio.wait_for(stream.close_started.wait(), timeout=1.0)
        done, _ = await asyncio.wait({task}, timeout=0.4)
        assert task in done, "response cleanup exceeded its independent bounded grace"
        return task.result()
    finally:
        # Also release the body if a setup failure prevented cleanup entry. No
        # task or stalled test stream survives even when the RED assertion fails.
        stream.release.set()
        stream.close_release.set()
        await asyncio.wait_for(task, timeout=1.0)


def test_body_deadline_has_a_fresh_bounded_cleanup_grace_after_its_cancellation():
    stream = StallingCloseStream(hold_body=True)
    kind, message = asyncio.run(_watch_stalled_cleanup(stream, timeout=0.005))
    assert kind == "error" and "timed out" in message
    assert "sensitive" not in message
    assert stream.close_count == 1 and stream.close_cancelled and stream.close_finished.is_set()


def test_stalled_cleanup_after_an_http_error_preserves_the_error_within_the_grace_cap():
    stream = StallingCloseStream()
    kind, message = asyncio.run(_watch_stalled_cleanup(stream, timeout=2.0, status=500))
    assert kind == "error" and "HTTP 200" in message
    assert stream.close_count == 1 and stream.close_cancelled and stream.close_finished.is_set()


def test_external_cancellation_survives_stalled_cleanup_with_a_bounded_close_attempt():
    stream = StallingCloseStream(hold_body=True)
    kind, _ = asyncio.run(_watch_stalled_cleanup(stream, timeout=2.0, cancel=True))
    assert kind == "cancelled"
    assert stream.close_count == 1 and stream.close_cancelled and stream.close_finished.is_set()


@pytest.mark.parametrize("timeout", [0.005, 2.0])
def test_keyboard_interrupt_survives_stalled_cleanup_with_a_bounded_close_attempt(timeout):
    stream = StallingCloseStream(read_error=KeyboardInterrupt())
    kind, _ = asyncio.run(_watch_stalled_cleanup(stream, timeout=timeout))
    assert kind == "interrupted"
    assert stream.close_count == 1 and stream.close_cancelled and stream.close_finished.is_set()


def test_httpx_implicit_success_close_is_bounded_by_the_active_io_deadline():
    stream = StallingCloseStream()
    kind, message = asyncio.run(_watch_stalled_cleanup(stream, timeout=0.005))
    assert kind == "error" and "timed out" in message
    assert stream.close_count == 1 and stream.close_cancelled and stream.close_finished.is_set()


@pytest.mark.parametrize("close_error", [None, RuntimeError("sensitive close error")])
def test_async_cancellation_is_preserved_and_closes_the_response(close_error):
    _client_module()
    stream = HoldingStream(close_error=close_error)

    async def scenario():
        task = asyncio.create_task(_send_with_stream(stream))
        await asyncio.wait_for(stream.started.wait(), timeout=1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert stream.closed and stream.close_count == 1


def test_keyboard_interrupt_is_preserved_and_closes_the_response():
    stream = TrackingStream([], error=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(_send_with_stream(stream))
    assert stream.closed and stream.close_count == 1


@pytest.mark.parametrize("raw", [b"{", b"sensitive invalid body", b'{"code":1,"data":"sensitive"}'])
def test_protocol_failures_are_normalized_and_closed_without_response_body_details(raw):
    stream = TrackingStream([raw])
    _assert_error(lambda: asyncio.run(_send_with_stream(stream)))
    assert stream.closed and stream.close_count == 1


@pytest.mark.parametrize("endpoint", [None, b"https://example.invalid", "http://example.invalid", "/relative",
                                     "https:///missing-host", "https://user:password@example.invalid/",
                                     "https://@example.invalid/", "https://example.invalid/#",
                                     "https://example.invalid/#sensitive", "https://example.invalid:invalid/",
                                     "https://example.invalid/\nsensitive", StringSubclass(ENDPOINT)])
def test_invalid_endpoints_are_rejected_before_io(endpoint):
    _invalid_send(endpoint=endpoint)


def _invalid_send(**changes):
    module = _client_module()
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=TrackingStream([_response_body()]))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            arguments = {"prepared": _prepare(), "endpoint": ENDPOINT, "headers": {},
                         "client": client, **changes}
            await module.send_request(**arguments)

    _assert_error(lambda: asyncio.run(scenario()))
    assert requests == []


@pytest.mark.parametrize("timeout", [None, True, False, 0, -1, 120.01, float("nan"),
                                    float("inf"), "3", IntSubclass(3), FloatSubclass(3.0)])
def test_timeout_requires_a_finite_positive_exact_number_at_most_120_seconds(timeout):
    _invalid_send(timeout_seconds=timeout)


@pytest.mark.parametrize("timeout", [1, 0.5, 120])
def test_supported_timeout_values_are_applied_explicitly(timeout):
    stream = TrackingStream([_response_body()])
    result, observed = asyncio.run(_send_with_stream(stream, send_changes={"timeout_seconds": timeout}))
    assert result.qimei36 == QIMEI
    assert observed[0].extensions["timeout"] == {
        name: float(timeout) for name in ("connect", "read", "write", "pool")}


@pytest.mark.parametrize("headers", [None, [], DictSubclass({}), {1: "x"}, {"X": 1},
                                    {"X\rBad": "x"}, {"X": "sensitive\n"}, {"X": "\x00"},
                                    {"Accept-Encoding": "gzip"}, {"accept-encoding": ""},
                                    {"Accept-Encoding": "identity", "accept-encoding": "gzip"},
                                    {StringSubclass("X"): "x"}, {"X": StringSubclass("x")},
                                    {"X Bad": "x"}, {"X": "nonascii-\xff"}])
def test_headers_require_an_exact_string_dict_without_injection_or_compression(headers):
    _invalid_send(headers=headers)


def test_explicit_headers_are_copied_and_identity_encoding_is_canonicalized():
    module = _client_module()
    headers = {"accept-encoding": "Identity", "Content-Type": "application/example", "X-Public": "x"}
    original = headers.copy()
    observed = []

    async def handler(request):
        observed.append(request)
        headers["X-Public"] = "changed after dispatch"
        return httpx.Response(200, stream=TrackingStream([_response_body()]))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            result = await module.send_request(_prepare(), endpoint=ENDPOINT, headers=headers, client=client)
            return result

    assert asyncio.run(scenario()).qimei36 == QIMEI
    assert observed[0].headers["X-Public"] == original["X-Public"]
    assert observed[0].headers.get_list("accept-encoding") == ["identity"]
    assert headers == {**original, "X-Public": "changed after dispatch"}


@pytest.mark.parametrize("prepared", [None, {}, object()])
def test_prepared_request_type_is_revalidated_before_io(prepared):
    _invalid_send(prepared=prepared)


@pytest.mark.parametrize("field,value", [("body", "sensitive"), ("body", BytesSubclass(b"x")),
                                       ("body", b"x" * ((1 << 20) + 1)),
                                       ("key", b"sensitive"), ("iv", bytearray(16))])
def test_tampered_prepared_requests_are_revalidated_before_io(field, value):
    prepared = _prepare()
    object.__setattr__(prepared, field, value)
    _invalid_send(prepared=prepared)


def test_incomplete_prepared_context_errors_are_normalized_before_io():
    _invalid_send(prepared=object.__new__(_client_module().PreparedQimeiRequest))


def test_prepared_subclasses_are_rejected_before_io():
    module = _client_module()

    class PreparedSubclass(module.PreparedQimeiRequest):
        pass

    _invalid_send(prepared=PreparedSubclass(body=b"x", key=KEY, iv=IV))


def test_exact_request_body_budget_is_accepted_without_copying_client_defaults():
    module = _client_module()
    prepared = module.PreparedQimeiRequest(body=b"x" * module.MAX_PACKET_BYTES, key=KEY, iv=IV)
    stream = TrackingStream([_response_body()])
    result, observed = asyncio.run(_send_with_stream(stream, prepared=prepared))
    assert result.qimei36 == QIMEI
    assert len(observed[0].content) == module.MAX_PACKET_BYTES


def test_async_client_must_be_explicitly_injected():
    _invalid_send(client=None)
