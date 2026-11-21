"""Persistent opt-in standalone generator regressions; synthetic mock traffic only.

RSA keys are generated in memory. Temporary configuration files use pytest's
external temporary directory. No SDK installation, app parameter, hardware,
account state or live endpoint is read or contacted.
"""
from __future__ import annotations

import asyncio
import base64
import builtins
from functools import lru_cache
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import types

import httpx
import pytest
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


SOURCE = Path(__file__).resolve().parents[1] / "admin/platform/marvis/core/qimei_generate.py"
MAC = "02:11:22:33:44:55"
OTHER_MAC = "02:11:22:33:44:66"
QIMEI = "0123456789abcdef01234567300089abcdef"
INNER_IV = bytes.fromhex("B8221969C27A8019D533B139ADE71BCC")


@pytest.fixture
def tmp_path():
    """Own and remove synthetic configs without retaining pytest artifacts."""
    with tempfile.TemporaryDirectory(prefix="qimei-generator-regression-") as directory:
        yield Path(directory)


@lru_cache(maxsize=1)
def _generator():
    assert SOURCE.is_file(), "experimental standalone generator is not implemented"
    spec = importlib.util.spec_from_file_location("qimei_generate_regression", SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def _private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _config():
    return {
        "schema": 1, "app_key": "synthetic-public-app", "app_version": "1.0-test",
        "endpoint": "https://qimei.example.invalid/experimental",
        "headers": {"Content-Type": "application/json", "X-Synthetic": "regression"},
        "header": {"7": "Synthetic OS", "10": "0" * 32,
                   "11": "Synthetic.exe", "12": "net_0"},
        "device": {"24": "Synthetic CPU", "25": "SYNTHETIC-CPU",
                   "49": 0, "53": 0, "54": '{"C:":"0_1234abcd_0"}',
                   "55": '{"0":"Synthetic SSD"}', "56": "{synthetic}",
                   "57": "C:#{synthetic}#0"},
        "inner_version": 1, "inner_key_hex": bytes(range(16)).hex(),
        "signing": {"version": 0, "seed8_hex": "00" * 8,
                    "tail4_hex": "00" * 4, "enabled": True},
        "rsa_public_key_pem": _private_key().public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii"),
        "timeout_seconds": 3.0,
    }


def _loaded(tmp_path, config=None):
    path = tmp_path / "synthetic-config.json"
    path.write_text(json.dumps(_config() if config is None else config), encoding="utf-8")
    return _generator().load_config(str(path))


def _decrypt(ciphertext, key, iv):
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def _wire_fields(data):
    offset, fields = 0, {}

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
        if tag & 7 == 0:
            fields[tag >> 3] = varint()
        else:
            assert tag & 7 == 2
            length = varint()
            fields[tag >> 3] = data[offset:offset + length]
            offset += length
    return fields


def _decode_request(request):
    packet = json.loads(request.content)
    raw = _private_key().decrypt(base64.b64decode(packet["ky"]), rsa_padding.PKCS1v15())
    key, iv = raw[:16], raw[16:]
    inner = _decrypt(base64.b64decode(packet["pms"]), key, iv)
    assert inner[:5] == b"\x03\x00\x00\x00\x01"
    envelope = _wire_fields(_decrypt(inner[5:], bytes(range(16)), INNER_IV))
    return packet, key, iv, _wire_fields(envelope[1]), _wire_fields(envelope[2])


def _response(key, iv, qimei=QIMEI.upper()):
    raw = b"\x0a\x24" + qimei.encode("ascii")
    padder = padding.PKCS7(128).padder()
    padded = padder.update(raw) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    encoded = base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode("ascii")
    return json.dumps({"code": 0, "data": encoded}).encode("ascii")


class RawStream(httpx.AsyncByteStream):
    def __init__(self, value):
        self.value, self.closed = value, False

    async def __aiter__(self):
        yield self.value

    async def aclose(self):
        self.closed = True


def test_generator_profile_varies_by_mac_but_server_issues_the_qimei(tmp_path):
    generator = _generator()
    config = _loaded(tmp_path)
    requests, streams = [], []

    async def handler(request):
        decoded = _decode_request(request)
        requests.append(decoded)
        assert request.headers["accept-encoding"] == "identity"
        assert str(request.url) == config["endpoint"]
        stream = RawStream(_response(decoded[1], decoded[2]))
        streams.append(stream)
        return httpx.Response(200, stream=stream)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            return [await generator.generate(mac, config, client=client)
                    for mac in (MAC, MAC, OTHER_MAC)]

    assert asyncio.run(scenario()) == [QIMEI] * 3
    assert all(stream.closed for stream in streams)
    first, repeat, other = requests
    assert first[0] != repeat[0]
    assert first[1:3] != repeat[1:3]
    for packet, key, iv, header, device in requests:
        assert len(key) == len(iv) == 16
        assert header[1] == 1 and header[3] == 3 and header[13] == 1
        assert header[4] == b"synthetic-public-app"
        assert header[5] == b"5.1.4.2" and header[6] == b"1.0-test"
        assert header[9] == b""
        assert json.loads(packet["ext"]) == {"app_key": "synthetic-public-app"}
        assert header[8] == device[32]
        assert len(device[32]) == 32 and device[32].decode().isupper()
        assert device[54] == b'{"C:":"0_1234abcd_0"}'
        assert device[55] == b'{"0":"Synthetic SSD"}'
    assert first[4] == repeat[4]
    assert first[3][8] == repeat[3][8] != other[3][8]
    assert first[4][20] == b"021122334455"
    assert other[4][20] == b"021122334466"
    assert first[4][42] == b"Synthetic Ethernet Adapter,6,021122334455;"
    assert first[3][2] != repeat[3][2]


def test_generate_samples_fresh_timestamp_and_distinct_signing_clock(tmp_path, monkeypatch):
    generator = _generator()
    config = _loaded(tmp_path)
    client_module = generator._load_client()
    original_prepare = client_module.prepare_request
    observed = []
    readings = iter((1700000000123456700, 1700000000987654300))
    monkeypatch.setattr(generator.time, "time_ns", lambda: next(readings))

    def prepare(*args, **kwargs):
        observed.append((args, kwargs))
        return original_prepare(*args, **kwargs)

    monkeypatch.setattr(client_module, "prepare_request", prepare)

    async def handler(request):
        decoded = _decode_request(request)
        return httpx.Response(200, stream=RawStream(_response(decoded[1], decoded[2])))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            return await generator.generate(MAC, config, client=client)

    assert asyncio.run(scenario()) == QIMEI
    header, kwargs = observed[0][0][0], observed[0][1]
    assert header[2].startswith(b"1700000000123") and len(header[2]) == 29
    assert kwargs["timestamp_ms"] == 1700000000123
    assert kwargs["ticks_100ns"] == 17000000009876543
    assert len(kwargs["rsa_padding"]) == 221 and 0 not in kwargs["rsa_padding"]


@pytest.mark.parametrize("enabled", [None, "", "0", "true", "01", " 1"])
def test_disabled_cli_fails_before_config_read_import_or_network(enabled, monkeypatch, capsys):
    generator = _generator()
    if enabled is None:
        monkeypatch.delenv("QIMEI_PYTHON_GENERATOR_ENABLED", raising=False)
    else:
        monkeypatch.setenv("QIMEI_PYTHON_GENERATOR_ENABLED", enabled)
    monkeypatch.setenv("QIMEI_PYTHON_GENERATOR_CONFIG", "sensitive-config-path")

    def forbidden(*args, **kwargs):
        raise AssertionError("disabled generator reached configuration or transport")

    monkeypatch.setattr(generator, "load_config", forbidden)
    monkeypatch.setattr(generator, "_load_client", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    assert generator.main([MAC, "4455", "8801", "60"]) != 0
    captured = capsys.readouterr()
    assert captured.out == "" and "disabled" in captured.err.lower()
    assert "sensitive" not in captured.err


def test_enabled_cli_requires_explicit_config_path(monkeypatch, capsys):
    generator = _generator()
    monkeypatch.setenv("QIMEI_PYTHON_GENERATOR_ENABLED", "1")
    monkeypatch.delenv("QIMEI_PYTHON_GENERATOR_CONFIG", raising=False)
    assert generator.main([MAC, "4455", "8801", "60"]) != 0
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err


@pytest.mark.parametrize("argv", [[], [MAC], [MAC, "4455", "8801"],
                                 [MAC, "4455", "8801", "60", "sensitive"],
                                 [MAC, "wrong", "8801", "60"],
                                 [MAC, "4455", "8800", "60"],
                                 [MAC, "4455", "8900", "60"],
                                 [MAC, "4455", "8801", "59"],
                                 [MAC, "4455", "8801", "120"],
                                 [MAC, "4455", "8e3", "60"],
                                 [MAC, "4455", "8801", "060"]])
def test_legacy_arguments_are_exact_and_invalid_values_fail_before_config(argv, monkeypatch, capsys):
    generator = _generator()
    monkeypatch.setenv("QIMEI_PYTHON_GENERATOR_ENABLED", "1")
    monkeypatch.setenv("QIMEI_PYTHON_GENERATOR_CONFIG", "sensitive-path")
    monkeypatch.setattr(generator, "load_config", lambda *_: pytest.fail("invalid argv read config"))
    assert generator.main(argv) != 0
    output = capsys.readouterr()
    assert output.out == "" and "sensitive" not in output.err


@pytest.mark.parametrize("mac", ["", "021122334455", "02:11-22:33:44:55", "02:11:22:33:44",
                                "03:11:22:33:44:55", "ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00",
                                "sensitive", b"02:11:22:33:44:55", None])
def test_mac_validation_rejects_malformed_multicast_broadcast_and_zero_before_transport(mac):
    async def scenario():
        with pytest.raises(_generator().QimeiGeneratorError):
            await _generator().generate(mac, _config(), client=None)
    asyncio.run(scenario())


@pytest.mark.parametrize("mac", ["B0:11:22:33:AA:BB", "b0-11-22-33-aa-bb", "02:11:22:33:AA:BB"])
def test_standard_unicast_mac_formats_are_supported(mac):
    generator = _generator()
    normalized = generator.normalize_mac(mac)
    assert normalized in ("b0112233aabb", "02112233aabb")


@pytest.mark.parametrize("raw", [b"", b"[]", b"null", b"{", b"\xff",
                               b'{"schema":1,"schema":1}', b'{"schema":NaN}',
                               b'{"nested":{"x":1,"x":2}}', b" " * (65536 + 1)])
def test_strict_config_json_rejects_invalid_duplicate_and_oversized_input(raw, tmp_path):
    path = tmp_path / "sensitive-config.json"
    path.write_bytes(raw)
    with pytest.raises(_generator().QimeiGeneratorError) as caught:
        _generator().load_config(str(path))
    assert "sensitive" not in str(caught.value) and caught.value.__cause__ is None


@pytest.mark.parametrize("field", ["schema", "app_key", "app_version", "endpoint", "headers", "header", "device",
                                   "inner_version", "inner_key_hex", "signing", "rsa_public_key_pem", "timeout_seconds"])
def test_config_requires_every_explicit_field(field, tmp_path):
    config = _config()
    del config[field]
    with pytest.raises(_generator().QimeiGeneratorError):
        _loaded(tmp_path, config)


@pytest.mark.parametrize("field,value", [
    ("schema", True), ("schema", 2), ("app_key", ""), ("app_key", "x" * 33),
    ("app_key", "sensitive\x00"), ("app_key", "\xff"), ("app_version", ""),
    ("app_version", "\n"), ("endpoint", "http://example.invalid"),
    ("endpoint", "https://user:password@example.invalid"), ("endpoint", "https://example.invalid/#"),
    ("headers", {"X": "sensitive\r\n"}), ("headers", {"Accept-Encoding": "gzip"}),
    ("inner_version", True), ("inner_version", -1), ("inner_version", 1 << 32),
    ("inner_key_hex", "00" * 15), ("inner_key_hex", "gg" * 16),
    ("rsa_public_key_pem", "sensitive-invalid-key"),
    ("timeout_seconds", True), ("timeout_seconds", 0), ("timeout_seconds", 121),
])
def test_invalid_config_fails_before_transport(field, value, tmp_path):
    config = _config()
    config[field] = value
    with pytest.raises(_generator().QimeiGeneratorError) as caught:
        _loaded(tmp_path, config)
    assert "sensitive" not in str(caught.value) and caught.value.__cause__ is None


@pytest.mark.parametrize("area", ["top", "header", "device", "signing"])
def test_unknown_or_reserved_config_fields_fail_closed(area, tmp_path):
    config = _config()
    if area == "top":
        config["sensitive-extra"] = "x"
    elif area == "header":
        config["header"]["8"] = "sensitive-override"
    elif area == "device":
        config["device"]["20"] = "sensitive-override"
    else:
        config["signing"]["sensitive-extra"] = "x"
    with pytest.raises(_generator().QimeiGeneratorError):
        _loaded(tmp_path, config)


@pytest.mark.parametrize("field,value", [("31", "x"), ("52", "x"), ("58", "x"),
                                       ("02", "x"), ("49", True), ("53", -1),
                                       ("24", 1), ("24", "sensitive\x00"), ("24", "\xff")])
def test_config_device_fields_require_verified_slots_and_exact_ascii_or_uint32(field, value, tmp_path):
    config = _config()
    config["device"][field] = value
    with pytest.raises(_generator().QimeiGeneratorError):
        _loaded(tmp_path, config)


def test_explicit_pid_and_serialized_extra_fields_use_verified_device_slots(tmp_path):
    generator = _generator()
    config = _config()
    config["device"].update({"1": "1234", "50": '{"synthetic":true}'})
    loaded = _loaded(tmp_path, config)
    observed = []

    async def handler(request):
        decoded = _decode_request(request)
        observed.append(decoded[4])
        return httpx.Response(200, stream=RawStream(_response(decoded[1], decoded[2])))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            return await generator.generate(MAC, loaded, client=client)

    assert asyncio.run(scenario()) == QIMEI
    assert observed[0][1] == b"1234"
    assert observed[0][50] == b'{"synthetic":true}'


@pytest.mark.parametrize("field,value", [("version", True), ("version", 1 << 32),
                                       ("seed8_hex", "00" * 7), ("tail4_hex", "gg" * 4),
                                       ("enabled", 1)])
def test_config_signing_fields_are_explicit_and_strict(field, value, tmp_path):
    config = _config()
    config["signing"][field] = value
    with pytest.raises(_generator().QimeiGeneratorError):
        _loaded(tmp_path, config)


def test_generate_invalid_config_fails_without_transport_or_state(tmp_path, monkeypatch):
    generator = _generator()
    config = _loaded(tmp_path)
    config["headers"]["X"] = "sensitive\r\n"

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid configuration reached a socket")

    async def scenario():
        with monkeypatch.context() as context:
            context.setattr(socket, "socket", forbidden)
            with pytest.raises(generator.QimeiGeneratorError):
                await generator.generate(MAC, config, client=None)

    asyncio.run(scenario())


def test_mutated_config_aggregate_budget_is_checked_before_crypto_or_transport(tmp_path, monkeypatch):
    generator = _generator()
    config = _loaded(tmp_path)
    config["header"]["7"] = "x" * 40000
    config["device"]["24"] = "y" * 40000
    monkeypatch.setattr(generator, "_load_client", lambda: pytest.fail("oversized config loaded components"))

    async def scenario():
        with pytest.raises(generator.QimeiGeneratorError):
            await generator.generate(MAC, config, client=None)

    asyncio.run(scenario())


def test_cli_prints_only_lowercase_server_identifier_and_ignores_legacy_context(tmp_path, monkeypatch, capsys):
    generator = _generator()
    path = tmp_path / "synthetic-config.json"
    path.write_text(json.dumps(_config()), encoding="utf-8")
    monkeypatch.setenv("QIMEI_PYTHON_GENERATOR_ENABLED", "1")
    monkeypatch.setenv("QIMEI_PYTHON_GENERATOR_CONFIG", str(path))
    received = []

    async def handler(request):
        decoded = _decode_request(request)
        received.append(decoded)
        return httpx.Response(200, stream=RawStream(_response(decoded[1], decoded[2])))

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        assert kwargs == {"trust_env": False}
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(generator, "httpx", types.SimpleNamespace(
        AsyncClient=client_factory, URL=httpx.URL, InvalidURL=httpx.InvalidURL))
    assert generator.main(["02-11-22-33-AA-BB", "AABB", "8899", "119"]) == 0
    captured = capsys.readouterr()
    assert captured.out == QIMEI + "\n" and captured.err == ""
    assert received[0][4][20] == b"02112233aabb"
    assert all(value not in received[0][4].values() for value in (b"8899", b"119", b"aabb"))


def test_cli_transport_failure_is_sanitized_without_hash_fallback(tmp_path, monkeypatch, capsys):
    generator = _generator()
    path = tmp_path / "synthetic-config.json"
    path.write_text(json.dumps(_config()), encoding="utf-8")
    monkeypatch.setenv("QIMEI_PYTHON_GENERATOR_ENABLED", "1")
    monkeypatch.setenv("QIMEI_PYTHON_GENERATOR_CONFIG", str(path))

    async def failed(*args, **kwargs):
        raise RuntimeError("sensitive endpoint/app/remote detail")

    monkeypatch.setattr(generator, "generate", failed)
    assert generator.main([MAC, "4455", "8801", "60"]) != 0
    captured = capsys.readouterr()
    assert captured.out == "" and "sensitive" not in captured.err and captured.err


def test_direct_script_disabled_subprocess_has_no_admin_imports_or_state_writes(tmp_path):
    assert SOURCE.is_file(), "experimental standalone generator is not implemented"
    environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH", "PATHEXT")
                   if key in os.environ}
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "QIMEI_PYTHON_GENERATOR_ENABLED": "0",
                        "QIMEI_PYTHON_GENERATOR_CONFIG": str(tmp_path / "must-not-read.json")})
    before = {path.name for path in tmp_path.iterdir()}
    result = subprocess.run([sys.executable, "-B", str(SOURCE), MAC, "4455", "8801", "60"],
                            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=5)
    assert result.returncode != 0 and result.stdout == ""
    assert "disabled" in result.stderr.lower() and "Traceback" not in result.stderr
    assert {path.name for path in tmp_path.iterdir()} == before


def test_import_and_disabled_entry_do_not_import_the_application_packages(monkeypatch, capsys):
    before = {name for name in sys.modules if name == "admin" or name.startswith("admin.")}
    generator = _generator()
    monkeypatch.setenv("QIMEI_PYTHON_GENERATOR_ENABLED", "0")
    assert generator.main([MAC, "4455", "8801", "60"]) != 0
    after = {name for name in sys.modules if name == "admin" or name.startswith("admin.")}
    assert after == before
    assert capsys.readouterr().out == ""


def test_import_does_not_read_environment_configuration_files_or_hardware(monkeypatch):
    assert SOURCE.is_file(), "experimental standalone generator is not implemented"
    source = SOURCE.read_bytes()
    name = "qimei_generate_import_isolation"
    spec = importlib.util.spec_from_file_location(name, SOURCE)
    module = importlib.util.module_from_spec(spec)

    def forbidden(*args, **kwargs):
        raise AssertionError("generator import attempted external input")

    class ForbiddenEnvironment(dict):
        __getitem__ = get = __iter__ = forbidden

    with monkeypatch.context() as context:
        context.setattr(os, "environ", ForbiddenEnvironment())
        context.setattr(builtins, "open", forbidden)
        context.setattr(socket, "socket", forbidden)
        exec(compile(source, str(SOURCE), "exec"), module.__dict__)
    assert name not in sys.modules
