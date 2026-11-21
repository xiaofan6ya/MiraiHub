"""Persistent offline protocol regression tests; all packets and IDs are synthetic.

The golden packet was generated once with the independent, already-installed
PyCryptodome Crypto.Cipher.AES CBC implementation and Crypto.Util.Padding.pad.
Its key is 000102030405060708090a0b0c0d0e0f, IV is
101112131415161718191a1b1c1d1e1f, and unpadded plaintext is:
0a24303132333435363738396162636465663031323334353637333030303839616263646566
120200ff1a0b73796e7468657469632d332203089601
Only the resulting fixed ciphertext is retained; runtime needs cryptography.
This verifies an offline building block, not compatibility with a real SDK.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
from functools import lru_cache
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


SOURCE = Path(__file__).resolve().parents[1] / "admin/platform/marvis/core/qimei_protocol.py"
KEY = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
IV = bytes.fromhex("101112131415161718191a1b1c1d1e1f")
QIMEI = "0123456789abcdef01234567300089abcdef"
PRIMARY = b"\x0a\x24" + QIMEI.encode("ascii")
GOLDEN = "zzyEPn6mtUBw16PxcSsapgSkLeeRs4imb4kOuCdrp6Sr+xHmYJ7CJ0vVFeyCqBNcB7y0uMh29AYoyU9eF6Otqw=="
BAD_PADDING = "zzyEPn6mtUBw16PxcSsapgSkLeeRs4imb4kOuCdrp6TtdNtrYzWXE0BbuPwwUwrG/YBOQiaY3tUrXU8wBkti3w=="


@lru_cache(maxsize=1)
def _protocol():
    assert SOURCE.is_file(), "offline QIMEI protocol decoder is not implemented"
    spec = importlib.util.spec_from_file_location("marvis_qimei_protocol_regression", SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _packet(plaintext, *, key=KEY):
    """Encrypt adversarial wire inputs to exercise the real decoder boundary."""
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(IV)).encryptor()
    return {"code": "0", "data": base64.b64encode(
        encryptor.update(padded) + encryptor.finalize()).decode("ascii")}


@pytest.mark.parametrize("form", ["dict", "integer_code", "str", "bytes"])
def test_fixed_independent_ciphertext_decodes_exact_primary_and_raw_fields(form):
    protocol = _protocol()
    response = {"code": "0", "data": GOLDEN}
    if form == "integer_code":
        response["code"] = 0
    elif form in ("str", "bytes"):
        response = json.dumps(response)
        if form == "bytes":
            response = response.encode("utf-8")
    result = protocol.decode_response(response, key=KEY, iv=IV)
    assert result.qimei36 == QIMEI
    assert result.fields == {1: QIMEI.encode("ascii"), 2: b"\x00\xff",
                             3: b"synthetic-3", 4: b"\x08\x96\x01"}


@pytest.mark.parametrize("code", [False, True, None, 0.0, 1, -1, "", "00", " 0", "0 ", "false", [], {}])
def test_nonzero_or_ambiguous_success_codes_fail_closed(code):
    protocol = _protocol()
    with pytest.raises(protocol.QimeiProtocolError):
        protocol.decode_response({"code": code, "data": GOLDEN}, key=KEY, iv=IV)


@pytest.mark.parametrize("response", [
    "", " ", "[]", "null", "0", '"0"', "{", b"\xff",
    '{"code":"1","code":"0","data":' + json.dumps(GOLDEN) + '}',
    '{"code":"0","data":"x","data":' + json.dumps(GOLDEN) + '}',
    '{"code":"0","data":' + json.dumps(GOLDEN) + ',"extra":{"k":1,"k":2}}',
    '{"code":"0","data":' + json.dumps(GOLDEN) + ',"extra":NaN}',
    {}, {"code": "0"}, {"data": GOLDEN}, {"code": "0", "data": None},
    {"code": "0", "data": 42}, [], None, bytearray(b"{}"),
])
def test_malformed_envelopes_raise_only_protocol_error(response):
    protocol = _protocol()
    with pytest.raises(protocol.QimeiProtocolError):
        protocol.decode_response(response, key=KEY, iv=IV)


@pytest.mark.parametrize("form", ["str", "bytes", "dict"])
def test_oversized_envelopes_are_rejected_even_when_code_is_success(form):
    protocol = _protocol()
    response = {"code": "0", "data": GOLDEN, "extra": "x" * (1024 * 1024)}
    if form != "dict":
        response = json.dumps(response)
        if form == "bytes":
            response = response.encode("utf-8")
    with pytest.raises(protocol.QimeiProtocolError):
        protocol.decode_response(response, key=KEY, iv=IV)


@pytest.mark.parametrize("form", ["oversized_value", "nested_escaped_value", "escaped_key", "cumulative_values"])
def test_dict_budget_rejects_oversized_content_before_any_json_encoding(monkeypatch, form):
    protocol = _protocol()
    response = {"code": "0", "data": GOLDEN}
    if form == "oversized_value":
        response["ignored"] = "\0" * (1024 * 1024 + 1)
    elif form == "nested_escaped_value":
        response["ignored"] = {"nested": ["\0" * (1024 * 1024 // 6 + 1)]}
    elif form == "escaped_key":
        response["ignored"] = {"\0" * (1024 * 1024 // 6 + 1): "x"}
    else:
        response["ignored"] = ["\0" * 1024] * 200

    def encoding_would_allocate(*args, **kwargs):
        raise AssertionError("oversized dictionary reached JSON encoding")

    monkeypatch.setattr(json.JSONEncoder, "iterencode", encoding_would_allocate)
    with pytest.raises(protocol.QimeiProtocolError):
        protocol.decode_response(response, key=KEY, iv=IV)


@pytest.mark.parametrize("form", ["cyclic_list", "cyclic_dict", "deep", "nonserializable", "nonfinite", "huge_list"])
def test_invalid_dict_graph_is_rejected_before_json_encoding_without_payloads(monkeypatch, form):
    protocol = _protocol()
    if form == "cyclic_list":
        metadata = []
        metadata.append(metadata)
    elif form == "cyclic_dict":
        metadata = {}
        metadata["sensitive-metadata"] = metadata
    elif form == "deep":
        metadata = None
        for _ in range(2000):
            metadata = [metadata]
    elif form == "nonserializable":
        metadata = object()
    elif form == "nonfinite":
        metadata = float("inf")
    else:
        metadata = [None] * (1024 * 1024 // 2 + 1)

    def encoding_would_allocate(*args, **kwargs):
        raise AssertionError("invalid dictionary graph reached JSON encoding")

    monkeypatch.setattr(json.JSONEncoder, "iterencode", encoding_would_allocate)
    with pytest.raises(protocol.QimeiProtocolError) as caught:
        protocol.decode_response({"code": "0", "data": GOLDEN, "ignored": metadata}, key=KEY, iv=IV)
    assert "sensitive-metadata" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_valid_dict_escaped_metadata_near_ascii_budget_remains_supported():
    protocol = _protocol()
    response = {"code": "0", "data": GOLDEN,
                "ignored": [None, True, False, 123, -45, 1.25, (),
                            {'"\\\b\t\n\r\f\0\x1f\x7fé\ud800😀': ""}], "filling": ""}
    overhead = len(json.dumps(response, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    response["filling"] = "\0" * ((1024 * 1024 - overhead) // 6)
    assert protocol.decode_response(response, key=KEY, iv=IV).qimei36 == QIMEI


@pytest.mark.parametrize("parameter,value", [
    ("key", b""), ("key", b"x" * 15), ("key", b"x" * 17),
    ("key", b"x" * 24), ("key", "x" * 16), ("key", bytearray(KEY)),
    ("iv", b""), ("iv", b"x" * 15), ("iv", b"x" * 17),
    ("iv", "x" * 16), ("iv", bytearray(IV)),
])
def test_aes128_key_and_iv_require_exactly_sixteen_bytes(parameter, value):
    protocol = _protocol()
    arguments = {"key": KEY, "iv": IV, parameter: value}
    with pytest.raises(protocol.QimeiProtocolError):
        protocol.decode_response({"code": "0", "data": GOLDEN}, **arguments)


def test_valid_aes192_packet_is_rejected_instead_of_changing_protocol_key_size():
    protocol = _protocol()
    key = bytes(range(24))
    with pytest.raises(protocol.QimeiProtocolError):
        protocol.decode_response(_packet(PRIMARY, key=key), key=key, iv=IV)


@pytest.mark.parametrize("data", [
    "!" + GOLDEN, GOLDEN + "\n", GOLDEN.replace("+", "-"),
    GOLDEN[:-1], GOLDEN + "=", GOLDEN[:-3] + "x==", "é", "AB==", "AAAA====",
])
def test_noncanonical_or_nonstandard_base64_is_rejected(data):
    protocol = _protocol()
    with pytest.raises(protocol.QimeiProtocolError):
        protocol.decode_response({"code": "0", "data": data}, key=KEY, iv=IV)


@pytest.mark.parametrize("data", ["", "AA==", base64.b64encode(b"x" * 17).decode("ascii")])
def test_ciphertext_must_be_nonempty_and_block_aligned(data):
    protocol = _protocol()
    with pytest.raises(protocol.QimeiProtocolError):
        protocol.decode_response({"code": "0", "data": data}, key=KEY, iv=IV)


def test_independently_encrypted_zero_padding_is_rejected():
    protocol = _protocol()
    # Independent AES-CBC encryption WITHOUT padding of this 64-byte plaintext:
    # PRIMARY + b"\x2a\x18" + bytes(24). It is valid wire data but ends in zero,
    # so bypassing PKCS#7 validation would incorrectly return the primary ID.
    with pytest.raises(protocol.QimeiProtocolError):
        protocol.decode_response({"code": "0", "data": BAD_PADDING}, key=KEY, iv=IV)


@pytest.mark.parametrize("tail", [
    b"\x00", b"\x80", b"\x80" * 10 + b"\x00", b"\x80\x00",
    b"\x48\x80", b"\x48" + b"\xff" * 9 + b"\x02",
    b"\x48" + b"\x80" * 10, b"\x48\x80\x00",
    b"\x51\x00", b"\x65\x00\x00\x00", b"\x5a\x80", b"\x5a\x05abc",
    b"\x5a" + b"\xff" * 9 + b"\x01", b"\x4b", b"\x4c", b"\x4e", b"\x4f",
    b"\x80\x80\x80\x80\x10\x00", b"\x12\x80\x00",
    b"\x10\x00", b"\x19" + b"\x00" * 8, b"\x25" + b"\x00" * 4,
])
def test_invalid_varints_tags_lengths_and_known_wire_types_are_rejected(tail):
    protocol = _protocol()
    with pytest.raises(protocol.QimeiProtocolError):
        protocol.decode_response(_packet(PRIMARY + tail), key=KEY, iv=IV)


@pytest.mark.parametrize("primary", [
    b"", b"\x12\x01x", b"\x0a\x00", b"\x0a\x24" + b"x" * 36,
    b"\x0a\x24" + b"0" * 36, b"\x0a\x23" + b"0" * 23 + b"3000" + b"0" * 8,
    b"\x0a\x25" + b"0" * 25 + b"3000" + b"0" * 8,
    b"\x0a\x24" + b"0" * 24 + b"3000" + b"0" * 7 + b"\xff",
])
def test_primary_is_required_ascii_hex_of_exact_length_and_marker(primary):
    protocol = _protocol()
    with pytest.raises(protocol.QimeiProtocolError):
        protocol.decode_response(_packet(primary), key=KEY, iv=IV)


@pytest.mark.parametrize("duplicate", [PRIMARY, b"\x12\x00\x12\x00",
                                        b"\x1a\x01x\x1a\x01y", b"\x22\x00\x22\x00"])
def test_known_fields_are_singletons_and_duplicate_values_are_not_merged(duplicate):
    protocol = _protocol()
    with pytest.raises(protocol.QimeiProtocolError):
        protocol.decode_response(_packet(PRIMARY + duplicate), key=KEY, iv=IV)


def test_unknown_supported_wire_types_are_skipped_without_inventing_fields():
    protocol = _protocol()
    unknown = (b"\x48\x96\x01\x51\x01\x02\x03\x04\x05\x06\x07\x08"
               b"\x5a\x03xyz\x65\xaa\xbb\xcc\xdd\x48\x00")
    result = protocol.decode_response(_packet(unknown + PRIMARY), key=KEY, iv=IV)
    assert result.qimei36 == QIMEI
    assert result.fields == {1: QIMEI.encode("ascii")}


def test_uppercase_ascii_hex_and_empty_optional_raw_fields_are_preserved():
    protocol = _protocol()
    primary = QIMEI.upper().encode("ascii")
    result = protocol.decode_response(_packet(b"\x0a\x24" + primary + b"\x12\x00"), key=KEY, iv=IV)
    assert result.qimei36 == QIMEI.upper()
    assert result.fields == {1: primary, 2: b""}


def test_errors_and_chains_do_not_expose_remote_payload_or_crypto_material():
    protocol = _protocol()
    secrets = ["sensitive-remote-error-body", KEY.hex(), IV.hex(), GOLDEN, QIMEI]
    responses = [{"code": "sensitive-remote-error-body", "data": GOLDEN},
                 _packet(PRIMARY + b"\x00"), {"code": "0", "data": BAD_PADDING}]
    for response in responses:
        with pytest.raises(protocol.QimeiProtocolError) as caught:
            protocol.decode_response(response, key=KEY, iv=IV)
        assert all(secret not in str(caught.value) for secret in secrets)
        assert caught.value.__cause__ is None
        assert caught.value.__suppress_context__ or caught.value.__context__ is None


def test_input_dictionary_is_not_modified():
    protocol = _protocol()
    response = {"code": "0", "data": GOLDEN, "extra": {"synthetic": [1, 2]}}
    before = json.dumps(response, sort_keys=True)
    assert protocol.decode_response(response, key=KEY, iv=IV).qimei36 == QIMEI
    assert json.dumps(response, sort_keys=True) == before


def test_decode_works_when_process_network_and_dll_loading_are_forbidden(monkeypatch):
    import ctypes
    import os
    import socket
    import subprocess

    protocol = _protocol()

    def forbidden(*args, **kwargs):
        raise AssertionError("offline decoder attempted an external operation")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(ctypes, "CDLL", forbidden)
    monkeypatch.setattr(ctypes, "WinDLL", forbidden, raising=False)
    assert protocol.decode_response({"code": "0", "data": GOLDEN}, key=KEY, iv=IV).qimei36 == QIMEI
