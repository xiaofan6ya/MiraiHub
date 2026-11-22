"""Persistent offline outer-packet regression tests; synthetic inputs only.

RSA private keys exist only in memory. AES expected ciphertexts are independent
PyCryptodome literals, and RSA ciphertext is decrypted by cryptography. No test
loads application packages, deployment state, private-key files, or a network.
"""
from __future__ import annotations

import base64
import builtins
import importlib.machinery
import importlib.util
import json
import io
from functools import lru_cache
from pathlib import Path
import os
import socket
import sys
import time
import types

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


CORE = Path(__file__).resolve().parents[1] / "admin/platform/marvis/core"
KEY = bytes(range(16))
IV = bytes(range(16, 32))
NONCE = bytes.fromhex("0011223380aafffe")
PS = bytes(range(1, 222))


@lru_cache(maxsize=1)
def _packet():
    source = CORE / "qimei_packet.py"
    assert source.is_file(), "offline QIMEI outer packet assembler is not implemented"
    name = "qimei_packet_regression"
    package = types.ModuleType(name)
    package.__path__ = [str(CORE)]
    package.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
    sys.modules[name] = package
    spec = importlib.util.spec_from_file_location(name + ".qimei_packet", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def _private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _arguments(**changes):
    packet = _packet()
    public_pem = _private_key().public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    return {
        "app_key": b"public-app",
        "rsa_public_key_pem": public_pem,
        "key": KEY, "iv": IV, "nonce": NONCE,
        "timestamp_ms": 1700000000123, "ticks_100ns": 17000000001234567,
        "signing": packet.QimeiSigningConfig(
            version=0, seed8=bytes(range(8)), tail4=bytes.fromhex("00112233")),
        "rsa_padding": PS,
        **changes,
    }


def test_normal_packet_decrypts_to_explicit_key_iv_and_opaque_inner_frame():
    packet = _packet()
    body = packet.encode_packet(b"synthetic-inner-frame", **_arguments())
    decoded = json.loads(body)
    assert list(decoded) == ["cpt", "ext", "ky", "nn", "pms", "sn", "tm"]
    assert decoded["cpt"] == "1"
    assert decoded["ext"] == '{"app_key":"public-app"}'
    assert decoded["nn"] == "0011223380aafffe"
    assert decoded["tm"] == "1700000000123"
    ciphertext = base64.b64decode(decoded["ky"], validate=True)
    assert len(ciphertext) == 256
    assert _private_key().decrypt(ciphertext, padding.PKCS1v15()) == KEY + IV
    numbers = _private_key().private_numbers()
    encoded = pow(int.from_bytes(ciphertext, "big"), numbers.d,
                  numbers.public_numbers.n).to_bytes(256, "big")
    assert encoded == b"\x00\x02" + PS + b"\x00" + KEY + IV
    assert json.loads(decoded["ext"]) == {"app_key": "public-app"}
    assert isinstance(body, bytes)


@pytest.mark.parametrize("inner_frame,expected", [
    (b"", "CpQLtUFu8EXxw5RYxlPqWg=="),
    (b"synthetic-inner-frame", "iNdJW+FlPpclFZtEVF5X6BAyZqfGiyF8Fnp/u9Hlyxs="),
    (bytes(range(16)), "lU9k8uTobp7ugtICFmhImduT5Iri8iZj5jzfOHDNCKg="),
    (b"x" * 15, "T9b4PMwoIqNBp1cIjfwr2g=="),
    (b"x" * 17, "8dcxzC6raHR2LgRQPv1DwwZ8eWMb1O17XaecCBPSq5M="),
])
def test_outer_aes_padding_and_standard_base64_match_independent_literals(inner_frame, expected):
    body = _packet().encode_packet(inner_frame, **_arguments())
    assert json.loads(body)["pms"] == expected
    assert expected.encode().replace(b"/", b"\\/") in body


def test_signature_material_uses_unescaped_base64_and_explicit_clocks(monkeypatch):
    packet = _packet()
    arguments = _arguments(timestamp_ms=0, ticks_100ns=10)
    original = packet.compute_signature
    observed = []

    def sign(material, *, ticks_100ns, config):
        if material:
            observed.append((material, ticks_100ns, config))
        return original(material, ticks_100ns=ticks_100ns, config=config)

    monkeypatch.setattr(packet, "compute_signature", sign)
    body = packet.encode_packet(b"synthetic-inner-frame", **arguments)
    decoded = json.loads(body)
    material = (b"1" + decoded["ky"].encode("ascii")
                + b"iNdJW+FlPpclFZtEVF5X6BAyZqfGiyF8Fnp/u9Hlyxs="
                + b"0" + b"0011223380aafffe")
    assert observed == [(material, 10, arguments["signing"])]
    assert decoded["sn"] == original(material, ticks_100ns=10, config=arguments["signing"])
    assert decoded["tm"] == "0"


def test_ext_is_a_nested_serialized_string_with_native_slash_quote_and_backslash_escaping():
    packet = _packet()
    body = packet.encode_packet(b"", **_arguments(app_key=b'a"\\/z'))
    ext = json.loads(body)["ext"]
    assert ext == '{"app_key":"a\\"\\\\\\/z"}'
    assert json.loads(ext) == {"app_key": 'a"\\/z'}
    assert b'"ext":"{' in body
    assert b'\\/' in body


def test_byte_map_serialization_sorts_unsigned_keys_and_escapes_only_values():
    serializer = _packet()._serialize_map
    assert serializer({b"tm": b"0", b"cpt": b"1", b"ext": b'"/\\'}) == (
        b'{"cpt":"1","ext":"\\"\\/\\\\","tm":"0"}')


def _rejected(**changes):
    packet = _packet()
    arguments = {"inner_frame": b"synthetic-inner-frame", **_arguments(), **changes}
    with pytest.raises(packet.QimeiPacketError) as caught:
        packet.encode_packet(**arguments)
    assert isinstance(caught.value, RuntimeError)
    assert "sensitive" not in str(caught.value)
    assert caught.value.__cause__ is None
    return caught.value


class BytesSubclass(bytes):
    pass


class IntSubclass(int):
    pass


@pytest.mark.parametrize("field", ["inner_frame", "app_key", "rsa_public_key_pem",
                                    "key", "iv", "nonce", "rsa_padding"])
@pytest.mark.parametrize("value", [None, "sensitive", bytearray(b"sensitive"),
                                    memoryview(b"sensitive"), BytesSubclass(b"sensitive"), 1, True])
def test_byte_inputs_reject_implicit_conversions_and_subclasses(field, value):
    _rejected(**{field: value})


@pytest.mark.parametrize("field,size", [("key", 16), ("iv", 16), ("nonce", 8),
                                      ("rsa_padding", 221)])
@pytest.mark.parametrize("delta", [-1, 1])
def test_fixed_width_inputs_reject_incorrect_lengths(field, size, delta):
    _rejected(**{field: b"x" * (size + delta)})


@pytest.mark.parametrize("value", [b"", b"sensitive\x00", b"\x1f", b"\x7f", b"\x80", b"\xff"])
def test_app_key_rejects_empty_controls_nul_and_non_ascii(value):
    _rejected(app_key=value)


@pytest.mark.parametrize("field", ["timestamp_ms", "ticks_100ns"])
@pytest.mark.parametrize("value", [None, True, False, IntSubclass(1), -1, 1 << 63, 1.0, "1"])
def test_clocks_require_exact_nonnegative_int64_integers(field, value):
    _rejected(**{field: value})


def test_zero_entropy_bytes_and_int64_timestamp_edges_are_explicitly_supported():
    packet = _packet()
    result = json.loads(packet.encode_packet(b"", **_arguments(
        app_key=b" ", key=bytes(16), iv=bytes(16), nonce=bytes(8),
        timestamp_ms=(1 << 63) - 1, ticks_100ns=(1 << 63) - 1)))
    assert result["nn"] == "0" * 16
    assert result["tm"] == "9223372036854775807"
    assert json.loads(result["ext"]) == {"app_key": " "}


@pytest.mark.parametrize("position", [0, 110, 220])
def test_rsa_ps_rejects_any_zero_byte(position):
    _rejected(rsa_padding=PS[:position] + b"\x00" + PS[position + 1:])


@pytest.mark.parametrize("value", [None, False, {}, object()])
def test_signing_configuration_must_be_explicit_and_normalizes_validation_errors(value):
    _rejected(signing=value)


@pytest.mark.parametrize("field,value", [("version", True), ("seed8", b"sensitive"),
                                      ("tail4", None), ("enabled", 1)])
def test_tampered_signing_configuration_is_revalidated(field, value):
    config = _arguments()["signing"]
    object.__setattr__(config, field, value)
    _rejected(signing=config)


def test_incomplete_signing_configuration_errors_are_normalized():
    _rejected(signing=object.__new__(_packet().QimeiSigningConfig))


def test_signing_disabled_produces_an_empty_signature_string():
    packet = _packet()
    config = packet.QimeiSigningConfig(version=0, seed8=bytes(8), tail4=bytes(4), enabled=False)
    assert json.loads(packet.encode_packet(b"", **_arguments(signing=config)))["sn"] == ""


@pytest.mark.parametrize("kind", ["bad-pem", "private-pem", "1024-bit", "wrong-exponent"])
def test_rsa_public_key_requires_valid_2048_bit_e65537_pem(kind):
    private = _private_key()
    if kind == "bad-pem":
        value = b"sensitive invalid key"
    elif kind == "private-pem":
        value = private.private_bytes(serialization.Encoding.PEM,
                                      serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption())
    else:
        key = (rsa.generate_private_key(public_exponent=65537, key_size=1024).public_key()
               if kind == "1024-bit" else
               rsa.RSAPublicNumbers(3, private.public_key().public_numbers().n).public_key())
        value = key.public_bytes(serialization.Encoding.PEM,
                                 serialization.PublicFormat.SubjectPublicKeyInfo)
    _rejected(rsa_public_key_pem=value)


def _forbid_crypto(monkeypatch):
    packet = _packet()
    original = packet.compute_signature

    def forbidden(*args, **kwargs):
        raise CryptoPreflightViolation("expensive crypto must not run before preflight")

    def validate_only(material, **kwargs):
        if material:
            forbidden()
        return original(material, **kwargs)

    monkeypatch.setattr(packet, "Cipher", forbidden)
    monkeypatch.setattr(packet, "load_pem_public_key", forbidden)
    monkeypatch.setattr(packet, "compute_signature", validate_only)


class CryptoPreflightViolation(BaseException):
    """Bypass production exception normalization if preflight calls crypto."""


@pytest.mark.parametrize("changes", [
    {"inner_frame": b"x" * ((1 << 20) + 1)},
    {"inner_frame": b"x" * (1 << 20)},
    {"app_key": b"/" * (1 << 20)},
    {"rsa_public_key_pem": b"x" * ((1 << 20) + 1)},
])
def test_aggregate_expansion_and_oversized_inputs_fail_before_crypto(monkeypatch, changes):
    _forbid_crypto(monkeypatch)
    _rejected(**changes)


def test_exact_signature_material_limit_is_checked_before_crypto(monkeypatch):
    packet = _packet()
    monkeypatch.setattr(packet, "MAX_MATERIAL_BYTES", 385)
    _forbid_crypto(monkeypatch)
    _rejected(inner_frame=b"", timestamp_ms=0)


def test_conservative_packet_preflight_counts_nested_ext_and_base64_expansion(monkeypatch):
    packet = _packet()
    # Empty input pads to 16 bytes / 24 Base64 bytes. The 910-byte upper bound
    # reserves slash escaping for RSA(344) and params(24), plus nested ext(28),
    # cpt(1), nonce(16), signature(68), timestamp(1), and JSON syntax(60).
    monkeypatch.setattr(packet, "MAX_PACKET_BYTES", 910)
    monkeypatch.setattr(packet, "MAX_MATERIAL_BYTES", 386)
    assert len(packet.encode_packet(b"", **_arguments(timestamp_ms=0))) <= 910
    monkeypatch.setattr(packet, "MAX_PACKET_BYTES", 909)
    _forbid_crypto(monkeypatch)
    _rejected(inner_frame=b"", timestamp_ms=0)


def test_preflight_counts_both_layers_of_app_key_escaping(monkeypatch):
    packet = _packet()
    monkeypatch.setattr(packet, "MAX_PACKET_BYTES", 914)
    assert len(packet.encode_packet(b"", **_arguments(app_key=b'a"\\/z', timestamp_ms=0))) <= 914
    monkeypatch.setattr(packet, "MAX_PACKET_BYTES", 913)
    _forbid_crypto(monkeypatch)
    _rejected(inner_frame=b"", app_key=b'a"\\/z', timestamp_ms=0)


@pytest.mark.parametrize("operation", ["Cipher", "load_pem_public_key", "compute_signature"])
def test_dependency_failures_never_expose_key_payload_or_exception_text(monkeypatch, operation):
    packet = _packet()

    def failed(*args, **kwargs):
        raise ValueError("sensitive dependency detail")

    monkeypatch.setattr(packet, operation, failed)
    _rejected()


def test_encoding_is_deterministic_and_has_no_io_environment_entropy_or_clock_reads(monkeypatch):
    packet = _packet()
    arguments = _arguments()
    expected = packet.encode_packet(b"synthetic-inner-frame", **arguments)

    def forbidden(*args, **kwargs):
        raise AssertionError("packet assembly must consume only explicit inputs")

    class ForbiddenEnvironment(dict):
        __getitem__ = get = __iter__ = forbidden

    with monkeypatch.context() as context:
        for target, names in ((builtins, ("open",)), (io, ("open",)),
                              (os, ("open", "read", "write", "stat", "lstat",
                                    "listdir", "scandir", "urandom", "getenv")),
                              (time, ("time", "time_ns", "monotonic", "monotonic_ns")),
                              (socket, ("socket", "create_connection"))):
            for name in names:
                context.setattr(target, name, forbidden)
        context.setattr(os, "environ", ForbiddenEnvironment())
        assert packet.encode_packet(b"synthetic-inner-frame", **arguments) == expected


# PERSISTENT regression evidence: these literal native outputs are retained on
# purpose. Ordinary tests load no DLL, emulator, SDK, Crypto package or network.
NATIVE_PACKET_FIXTURE = json.loads(
    Path(__file__).with_name("qimei_packet_sdk_vectors.json").read_text(encoding="utf-8")
)
NATIVE_PACKET_VECTORS = NATIVE_PACKET_FIXTURE["vectors"]


def _native_arguments(**changes):
    """Use the fixed public fixture key without generating a private RSA key."""
    packet = _packet()
    return {
        "app_key": b"public-app",
        "rsa_public_key_pem": NATIVE_PACKET_FIXTURE["common"]["rsa_public_key_pem"].encode("ascii"),
        "key": KEY, "iv": IV, "nonce": NONCE,
        "timestamp_ms": 1700000000123, "ticks_100ns": 17000000001234567,
        "signing": packet.QimeiSigningConfig(
            version=0, seed8=bytes(range(8)), tail4=bytes.fromhex("00112233")),
        "rsa_padding": PS,
        **changes,
    }


@pytest.mark.parametrize(
    "vector", [row for row in NATIVE_PACKET_VECTORS if row["component"] == "outer_aes"],
    ids=lambda vector: vector["name"],
)
def test_persistent_native_outer_aes_component_literals(vector):
    body = _packet().encode_packet(
        bytes.fromhex(vector["inner_frame_hex"]),
        **_native_arguments(key=bytes.fromhex(vector["key_hex"]),
                            iv=bytes.fromhex(vector["iv_hex"])),
    )
    assert json.loads(body)["pms"] == vector["native_params_base64"]


@pytest.mark.parametrize(
    "vector", [row for row in NATIVE_PACKET_VECTORS if row["component"] == "rsa_wrap"],
    ids=lambda vector: vector["name"],
)
def test_persistent_native_rsa_integer_padding_modexp_and_output_literal(vector):
    body = _packet().encode_packet(
        b"",
        **_native_arguments(key=bytes.fromhex(vector["key_hex"]),
                            iv=bytes.fromhex(vector["iv_hex"]),
                            rsa_padding=bytes.fromhex(vector["rsa_padding_hex"])),
    )
    assert base64.b64decode(json.loads(body)["ky"], validate=True) == bytes.fromhex(
        vector["native_rsa_ciphertext_hex"])


@pytest.mark.parametrize(
    "vector", [row for row in NATIVE_PACKET_VECTORS if row["component"] == "packet_tail"],
    ids=lambda vector: vector["name"],
)
def test_persistent_native_joined_packet_tail_matches_every_raw_json_byte(vector):
    packet = _packet()
    body = packet.encode_packet(
        bytes.fromhex(vector["inner_frame_hex"]),
        **_native_arguments(
            app_key=bytes.fromhex(vector["app_key_hex"]),
            key=bytes.fromhex(vector["key_hex"]), iv=bytes.fromhex(vector["iv_hex"]),
            nonce=bytes.fromhex(vector["nonce_hex"]), timestamp_ms=vector["timestamp_ms"],
            ticks_100ns=vector["ticks_100ns"],
            signing=packet.QimeiSigningConfig(
                version=vector["signing_version"],
                seed8=bytes.fromhex(vector["signing_seed8_hex"]),
                tail4=bytes.fromhex(vector["signing_tail4_hex"])),
            rsa_padding=bytes.fromhex(vector["rsa_padding_hex"])),
    )
    assert body == bytes.fromhex(vector["native_packet_json_hex"])
