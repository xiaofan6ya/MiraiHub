"""Persistent offline QIMEI signing regression tests; synthetic inputs only.

Fixed signatures below were produced once by an independent pure-Python ChaCha20
quarter-round implementation plus stdlib MD5, without production imports or
cryptography. That producer is not retained or executed by these tests.

The separate persistent JSON fixture contains literal outputs from emulating
original SDK signing instructions with synthetic material and explicit inputs.
Its provenance and limitations exclude complete request construction, SDK
lifecycle and server interoperability. Runtime tests only replay these literals;
they need neither a DLL, Unicorn, Crypto package, runtime state nor a clock.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from dataclasses import FrozenInstanceError
from functools import lru_cache
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "admin/platform/marvis/core/qimei_request.py"
NATIVE_COMPONENT_VECTORS = json.loads(
    Path(__file__).with_name("qimei_signature_sdk_vectors.json").read_text(encoding="utf-8")
)["vectors"]
MATERIAL = b"\x00qimei synthetic material\xff"
DEFAULT = "4c4385102b920001e2406276590eef2d84029cd6b2dfe6dcfc7f0000000000112233"
CONFIGURED = "4c4385100136000f423fc94bc806c9e801fe26cdc95e6b99c02e123456780080feff"
MAX_EDGES = "4c43851011fa0007498c35bfce83879e83f0db3dd7211aa001f3ffffffffffffffff"
TICK_ZERO = "4c4385102bfc000000009924475a04009b892d45dd4277ec2b8b0000000000112233"
TICK_ONE = "4c4385102b5b00000001d21deada8373629358e36150a52ff06d0000000000112233"
ZERO_SEED = "4c4385102bfc00000000bff21807ed828d2df3c061c17d85a8f40000000100000000"
FF_SEED = "4c4385102bfc00000000bbaf4c8ef5067b276d4e5c295cdea74d0000000100000000"


@lru_cache(maxsize=1)
def _request():
    assert SOURCE.is_file(), "offline QIMEI signing component is not implemented"
    spec = importlib.util.spec_from_file_location("marvis_qimei_request_regression", SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _config(**changes):
    arguments = {"version": 0, "seed8": bytes(range(8)),
                 "tail4": bytes.fromhex("00112233"), **changes}
    return _request().QimeiSigningConfig(**arguments)


@pytest.mark.parametrize("material,ticks,configuration,expected", [
    (MATERIAL, 17000000001234567, {}, DEFAULT),
    (b"second synthetic material", 9999999,
     {"version": 0x12345678, "seed8": bytes.fromhex("1032547698badcfe"),
      "tail4": bytes.fromhex("0080feff")}, CONFIGURED),
    (b"edge-seed", (1 << 63) - 1,
     {"version": 0xffffffff, "seed8": b"\xff" * 8, "tail4": b"\xff" * 4}, MAX_EDGES),
])
def test_fixed_independent_vectors_preserve_exact_layout(material, ticks, configuration, expected):
    request = _request()
    result = request.compute_signature(material, ticks_100ns=ticks, config=_config(**configuration))
    assert result == expected
    assert re.fullmatch(r"[0-9a-f]{68}", result)


@pytest.mark.parametrize("vector", NATIVE_COMPONENT_VECTORS, ids=lambda vector: vector["name"])
def test_native_instruction_component_literals_match_exactly(vector):
    config = _config(version=vector["version"],
                     seed8=bytes.fromhex(vector["seed8_hex"]),
                     tail4=bytes.fromhex(vector["tail4_hex"]))
    result = _request().compute_signature(bytes.fromhex(vector["material_hex"]),
                                           ticks_100ns=vector["ticks"], config=config)
    assert result == vector["native_34_hex"]


@pytest.mark.parametrize("ticks,expected", [
    (0, TICK_ZERO), (9, TICK_ZERO), (10, TICK_ONE),
    (10000000, TICK_ZERO), (17000000000000009, TICK_ZERO),
])
def test_explicit_signing_clock_uses_fractional_microseconds_not_milliseconds(ticks, expected):
    assert _request().compute_signature(MATERIAL, ticks_100ns=ticks, config=_config()) == expected


@pytest.mark.parametrize("seed", [bytes(8), b"\xff" * 8])
def test_zero_version_uses_fixed_prefix_even_with_a_different_explicit_seed(seed):
    assert _request().compute_signature(
        MATERIAL, ticks_100ns=17000000001234567, config=_config(seed8=seed)) == DEFAULT


@pytest.mark.parametrize("seed,expected", [(bytes(8), ZERO_SEED), (b"\xff" * 8, FF_SEED)])
def test_nonzero_version_uses_all_eight_raw_seed_bytes(seed, expected):
    assert _request().compute_signature(
        MATERIAL, ticks_100ns=0,
        config=_config(version=1, seed8=seed, tail4=bytes(4))) == expected


@pytest.mark.parametrize("material,enabled", [(b"", True), (b"", False), (MATERIAL, False)])
def test_empty_or_explicitly_disabled_signing_returns_empty_text(material, enabled):
    assert _request().compute_signature(
        material, ticks_100ns=0, config=_config(enabled=enabled)) == ""


def test_config_is_frozen_and_does_not_expose_the_seed_in_repr():
    seed = b"s3cr3t!!"
    config = _config(seed8=seed)
    assert seed.decode() not in repr(config)
    assert repr(seed) not in repr(config)
    with pytest.raises(FrozenInstanceError):
        config.version = 1


class _Integer(int):
    pass


class _Bytes(bytes):
    pass


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", False), ("version", -1),
    ("version", 1 << 32), ("version", 0.0), ("version", "0"),
    ("version", _Integer(1)),
    ("seed8", b""), ("seed8", bytes(7)), ("seed8", bytes(9)),
    ("seed8", "12345678"), ("seed8", bytearray(8)),
    ("seed8", memoryview(bytes(8))), ("seed8", _Bytes(bytes(8))),
    ("tail4", b""), ("tail4", bytes(3)), ("tail4", bytes(5)),
    ("tail4", "1234"), ("tail4", bytearray(4)),
    ("tail4", memoryview(bytes(4))), ("tail4", _Bytes(bytes(4))),
    ("enabled", 0), ("enabled", 1), ("enabled", None), ("enabled", "false"),
])
def test_config_rejects_ambiguous_types_and_invalid_ranges(field, value):
    request = _request()
    with pytest.raises(request.QimeiRequestError):
        _config(**{field: value})


@pytest.mark.parametrize("material", [None, "sensitive-material", bytearray(b"x"),
                                      memoryview(b"x"), _Bytes(b"x")])
def test_material_requires_exact_bytes(material):
    request = _request()
    with pytest.raises(request.QimeiRequestError):
        request.compute_signature(material, ticks_100ns=0, config=_config())


@pytest.mark.parametrize("ticks", [True, False, -1, 1 << 63, 1.0, "0", None, _Integer(1)])
def test_signing_clock_requires_a_nonnegative_exact_int64(ticks):
    request = _request()
    with pytest.raises(request.QimeiRequestError):
        request.compute_signature(MATERIAL, ticks_100ns=ticks, config=_config())


@pytest.mark.parametrize("config", [None, {}, object(), b"12345678"])
def test_config_must_be_explicit_and_valid(config):
    request = _request()
    with pytest.raises(request.QimeiRequestError):
        request.compute_signature(MATERIAL, ticks_100ns=0, config=config)


@pytest.mark.parametrize("material,ticks,enabled", [
    (b"", True, True), (b"", -1, False), (None, 0, False),
    (b"x" * (1024 * 1024 + 1), 0, False),
])
def test_empty_and_disabled_paths_still_validate_all_arguments(material, ticks, enabled):
    request = _request()
    with pytest.raises(request.QimeiRequestError):
        request.compute_signature(material, ticks_100ns=ticks, config=_config(enabled=enabled))


@pytest.mark.parametrize("field,value", [("version", True), ("seed8", "12345678"),
                                        ("tail4", b"x"), ("enabled", 0)])
def test_compute_revalidates_config_fields_even_if_frozen_initialization_was_bypassed(field, value):
    request = _request()
    config = _config()
    object.__setattr__(config, field, value)
    with pytest.raises(request.QimeiRequestError):
        request.compute_signature(b"", ticks_100ns=0, config=config)


@pytest.mark.parametrize("field", ["version", "seed8", "tail4"])
def test_missing_required_config_fields_have_a_sanitized_error_boundary(field):
    request = _request()
    config = _config(version=0x11223344, seed8=b"s3cr3t!!", tail4=bytes.fromhex("deadbeef"))
    object.__delattr__(config, field)
    with pytest.raises(request.QimeiRequestError) as caught:
        request.compute_signature(b"", ticks_100ns=0, config=config)
    assert "s3cr3t!!" not in str(caught.value)
    assert "deadbeef" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ or caught.value.__context__ is None


def test_material_limit_is_one_mib_inclusive():
    request = _request()
    result = request.compute_signature(b"x" * (1024 * 1024), ticks_100ns=0, config=_config())
    assert re.fullmatch(r"[0-9a-f]{68}", result)
    with pytest.raises(request.QimeiRequestError):
        request.compute_signature(b"x" * (1024 * 1024 + 1), ticks_100ns=0, config=_config())


@pytest.mark.parametrize("boundary", ["md5", "chacha"])
def test_backend_failures_hide_material_configuration_and_exception_chain(monkeypatch, boundary):
    request = _request()
    secret = "sensitive-material-and-configuration"

    def fail(*args, **kwargs):
        raise RuntimeError(secret)

    if boundary == "md5":
        monkeypatch.setattr(request.hashlib, "md5", fail)
    else:
        monkeypatch.setattr(request, "Cipher", fail)
    with pytest.raises(request.QimeiRequestError) as caught:
        request.compute_signature(MATERIAL, ticks_100ns=0, config=_config())
    assert secret not in str(caught.value)
    assert MATERIAL.hex() not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ or caught.value.__context__ is None


def test_signing_performs_no_file_clock_entropy_process_network_or_sdk_calls(monkeypatch):
    import builtins
    import ctypes
    import io
    import os
    import socket
    import subprocess
    import time

    request, config = _request(), _config()

    def forbidden(*args, **kwargs):
        raise AssertionError("offline signing attempted an external operation")

    with monkeypatch.context() as scoped:
        for target, attribute in [(builtins, "open"), (io, "open"), (os, "open"),
                                  (os, "stat"), (os, "urandom"), (os, "system"),
                                  (socket, "socket"), (socket, "create_connection"),
                                  (subprocess, "Popen"), (ctypes, "CDLL"),
                                  (time, "time"), (time, "time_ns")]:
            scoped.setattr(target, attribute, forbidden)
        scoped.setattr(ctypes, "WinDLL", forbidden, raising=False)
        result = request.compute_signature(MATERIAL, ticks_100ns=17000000001234567, config=config)
    assert result == DEFAULT
