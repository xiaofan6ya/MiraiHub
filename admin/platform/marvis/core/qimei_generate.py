"""Opt-in experimental server-issued QIMEI generator, without SDK/Wine state.

Run this file directly with MAC TAG PORT OCTET for the existing generator-command
contract. TAG must match the MAC's final four hex digits; PORT 8801..8899 and
OCTET 60..119 are validated legacy context and never become wire fields. Both
QIMEI_PYTHON_GENERATOR_ENABLED=1 and QIMEI_PYTHON_GENERATOR_CONFIG are required.
No application parameter, endpoint, key or installed configuration is discovered.

The explicit UTF-8 JSON config is at most 64 KiB, with exactly these keys:
schema=1, app_key (1..32 printable ASCII bytes), app_version (printable ASCII),
endpoint (HTTPS, no credentials/fragment), headers (ASCII string map), header
(exact string slots "7", "10", "11", "12"), device (verified numeric-string
slots containing printable ASCII strings, except uint32 slots "49" and "53"),
inner_version (uint32), inner_key_hex (16 raw bytes), signing (exact version,
seed8_hex, tail4_hex, enabled), rsa_public_key_pem (2048/e65537), timeout_seconds
(finite positive number <=120). Unknown and duplicate keys are rejected.

This is a synthetic profile, not a hardware collector. Header 8 and device 32
are the uppercase MD5 of a domain-separated normalized MAC, device 20 is that
MAC's twelve lowercase hex digits, and device 42 names a synthetic adapter.
Those three device slots cannot be configured. CPU/storage and every other
supplied device value come only from the explicit config; shared template
values do not establish physical-device fidelity or server uniqueness. The hash
is a synthetic GUID only: the returned QIMEI must come from the server decoder.
No fallback identifier or local account/state file is ever written.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
import hashlib
import importlib.machinery
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import secrets
import sys
import time
import types
from urllib.parse import urlsplit

import httpx


MAX_CONFIG_BYTES = 64 * 1024
_CONFIG_KEYS = frozenset(("schema", "app_key", "app_version", "endpoint", "headers",
                          "header", "device", "inner_version", "inner_key_hex", "signing",
                          "rsa_public_key_pem", "timeout_seconds"))
_HEADER_SLOTS = frozenset(("7", "10", "11", "12"))
_DEVICE_SLOTS = frozenset(range(1, 58)) - {20, 31, 32, 42, 52}
_SIGNING_KEYS = frozenset(("version", "seed8_hex", "tail4_hex", "enabled"))
_HEADER_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_QIMEI = re.compile(r"[0-9a-fA-F]{24}3000[0-9a-fA-F]{8}")


class QimeiGeneratorError(RuntimeError):
    """Explicit generator input or server issuance failed, without input values."""


def _reject(message: str) -> None:
    raise QimeiGeneratorError(message) from None


@lru_cache(maxsize=1)
def _load_client():
    """Load only the experimental components, bypassing application startup."""
    name = "_miraihub_experimental_qimei_generator"
    core = Path(__file__).resolve().parent
    package = types.ModuleType(name)
    package.__path__ = [str(core)]
    package.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
    sys.modules[name] = package
    spec = importlib.util.spec_from_file_location(name + ".qimei_client", core / "qimei_client.py")
    if spec is None or spec.loader is None:
        _reject("Experimental QIMEI components are unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        sys.modules.pop(name, None)
        _reject("Experimental QIMEI components are unavailable")
    return module


def normalize_mac(mac: str) -> str:
    """Accept a six-octet colon/hyphen unicast MAC and return twelve lower hex."""
    if type(mac) is not str or re.fullmatch(r"[0-9A-Fa-f]{2}([:-])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}", mac) is None:
        _reject("Generator MAC must be a nonzero unicast six-octet address")
    normalized = mac.replace(":", "").replace("-", "").lower()
    if int(normalized[:2], 16) & 1 or normalized == "0" * 12:
        _reject("Generator MAC must be a nonzero unicast six-octet address")
    return normalized


def _text(value, *, maximum=MAX_CONFIG_BYTES, nonempty=False) -> bytes:
    if (type(value) is not str or len(value) > maximum or (nonempty and not value)
            or any(not 0x20 <= ord(character) <= 0x7e for character in value)):
        _reject("Generator configuration requires bounded printable ASCII text")
    return value.encode("ascii")


def _hex(value, size) -> bytes:
    if type(value) is not str or re.fullmatch(r"[0-9a-fA-F]{%d}" % (size * 2), value) is None:
        _reject("Generator configuration contains an invalid fixed-width hex value")
    return bytes.fromhex(value)


def _uint32(value):
    if type(value) is not int or not 0 <= value <= 0xffffffff:
        _reject("Generator configuration requires an exact uint32")
    return value


def _check_config_budget(config) -> None:
    """Bound a caller-mutated config before encoding, key parsing or imports."""
    remaining = MAX_CONFIG_BYTES

    def consume(size):
        nonlocal remaining
        remaining -= size
        if remaining < 0:
            _reject("Generator configuration exceeds the byte budget")

    def string(value):
        consume(2)
        if len(value) > remaining:
            _reject("Generator configuration exceeds the byte budget")
        for character in value:
            code = ord(character)
            consume(2 if character in '\"\\\b\f\n\r\t' else
                    6 if code < 0x20 or 0x7f <= code <= 0xffff else
                    12 if code > 0xffff else 1)

    def visit(value, depth):
        kind = type(value)
        if kind is str:
            string(value)
        elif kind is bool:
            consume(4 if value else 5)
        elif kind is int:
            if value.bit_length() > 64:
                _reject("Generator configuration contains an invalid numeric value")
            consume(len(str(value)))
        elif kind is float:
            if not math.isfinite(value):
                _reject("Generator configuration contains an invalid numeric value")
            consume(len(str(value)))
        elif kind is dict and depth < 2 and len(value) <= 64:
            consume(2 + max(0, len(value) - 1))
            for name, item in value.items():
                if type(name) is not str:
                    _reject("Generator configuration requires string field keys")
                string(name)
                consume(1)
                visit(item, depth + 1)
        else:
            _reject("Generator configuration contains an invalid value")

    visit(config, 0)


def _validate_config(config):
    if type(config) is not dict or set(config) != _CONFIG_KEYS:
        _reject("Generator configuration has an invalid schema shape")
    _check_config_budget(config)
    if type(config["schema"]) is not int or config["schema"] != 1:
        _reject("Generator configuration schema must be integer 1")
    app_key = _text(config["app_key"], maximum=32, nonempty=True)
    app_version = _text(config["app_version"], nonempty=True)
    endpoint = config["endpoint"]
    if (type(endpoint) is not str or len(endpoint) > MAX_CONFIG_BYTES or "#" in endpoint
            or any(ord(character) <= 0x20 or ord(character) == 0x7f for character in endpoint)):
        _reject("Generator endpoint must be an explicit HTTPS URL")
    try:
        split = urlsplit(endpoint)
        parsed = httpx.URL(endpoint)
        if (split.scheme != "https" or not split.netloc or "@" in split.netloc
                or parsed.scheme != "https" or not parsed.is_absolute_url or parsed.userinfo):
            _reject("Generator endpoint must be an explicit HTTPS URL")
    except (ValueError, httpx.InvalidURL):
        _reject("Generator endpoint must be an explicit HTTPS URL")
    headers = config["headers"]
    if type(headers) is not dict or len(headers) > 64:
        _reject("Generator headers require an explicit bounded string map")
    headers = headers.copy()
    for name, value in headers.items():
        _text(name, nonempty=True)
        _text(value)
        if _HEADER_NAME.fullmatch(name) is None:
            _reject("Generator headers contain an invalid name")
        if name.lower() == "accept-encoding" and value.lower() != "identity":
            _reject("Generator transport requires identity encoding")
    raw_header, raw_device = config["header"], config["device"]
    if type(raw_header) is not dict or set(raw_header) != _HEADER_SLOTS:
        _reject("Generator header template requires exactly four explicit slots")
    header = {int(number): _text(value) for number, value in raw_header.items()}
    if type(raw_device) is not dict or len(raw_device) > len(_DEVICE_SLOTS):
        _reject("Generator device template must be a bounded verified field map")
    device = {}
    for name, value in raw_device.items():
        if (type(name) is not str or re.fullmatch(r"[1-9][0-9]?", name) is None
                or int(name) not in _DEVICE_SLOTS):
            _reject("Generator device template contains an unverified or reserved slot")
        number = int(name)
        device[number] = _uint32(value) if number in (49, 53) else _text(value)
    inner_version = _uint32(config["inner_version"])
    inner_key = _hex(config["inner_key_hex"], 16)
    signing = config["signing"]
    if type(signing) is not dict or set(signing) != _SIGNING_KEYS:
        _reject("Generator signing configuration has an invalid shape")
    signing_version = _uint32(signing["version"])
    signing_seed = _hex(signing["seed8_hex"], 8)
    signing_tail = _hex(signing["tail4_hex"], 4)
    if type(signing["enabled"]) is not bool:
        _reject("Generator signing enabled flag must be an exact boolean")
    public_pem = config["rsa_public_key_pem"]
    if type(public_pem) is not str or len(public_pem) > MAX_CONFIG_BYTES or not public_pem.isascii():
        _reject("Generator RSA public key is invalid")
    public_pem = public_pem.encode("ascii")
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        public = load_pem_public_key(public_pem)
        if not isinstance(public, rsa.RSAPublicKey) or public.key_size != 2048 or public.public_numbers().e != 65537:
            _reject("Generator RSA public key is invalid")
    except Exception:
        _reject("Generator RSA public key is invalid")
    timeout = config["timeout_seconds"]
    if type(timeout) not in (int, float) or not 0 < timeout <= 120:
        _reject("Generator timeout must be a finite positive number at most 120 seconds")
    return {"app_key": app_key, "app_version": app_version, "header": header, "device": device,
            "endpoint": endpoint, "headers": headers, "inner_version": inner_version,
            "inner_key": inner_key, "rsa_public_key_pem": public_pem,
            "signing": (signing_version, signing_seed, signing_tail, signing["enabled"]),
            "timeout_seconds": float(timeout)}


def load_config(path: str) -> dict:
    """Read only the explicitly named, bounded strict JSON configuration."""
    if type(path) is not str or not path:
        _reject("An explicit generator configuration path is required")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    def invalid(_value):
        raise ValueError("non-JSON numeric constant")

    try:
        with open(path, "rb") as source:
            raw = source.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            _reject("Generator configuration exceeds the byte budget")
        config = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid)
        _validate_config(config)
        return config
    except QimeiGeneratorError:
        raise
    except Exception:
        _reject("Generator configuration could not be loaded")


async def generate(mac: str, config: dict, *, client: httpx.AsyncClient) -> str:
    """Ask the server for one QIMEI using a deterministic synthetic MAC profile."""
    mac = normalize_mac(mac)
    try:
        options = _validate_config(config)
        module = _load_client()
        guid = hashlib.md5(b"qimei-experimental-mac-v1:" + mac.encode("ascii")).hexdigest().upper().encode("ascii")
        timestamp_ms = time.time_ns() // 1_000_000
        header = {number: b"" for number in range(1, 14)}
        header.update(options["header"])
        header.update({1: 1, 2: str(timestamp_ms).encode("ascii") + secrets.token_hex(8).encode("ascii"),
                       3: 3, 4: options["app_key"], 5: b"5.1.4.2", 6: options["app_version"],
                       8: guid, 9: b"", 13: 1})
        device = options["device"]
        device.update({20: mac.encode("ascii"), 32: guid,
                       42: ("Synthetic Ethernet Adapter,6," + mac + ";").encode("ascii")})
        key, iv, nonce = secrets.token_bytes(16), secrets.token_bytes(16), secrets.token_bytes(8)
        ps = bytes(secrets.randbelow(255) + 1 for _ in range(221))
        signing = module.QimeiSigningConfig(*options["signing"])
        prepared = module.prepare_request(
            header, device, inner_version=options["inner_version"], inner_key=options["inner_key"],
            rsa_public_key_pem=options["rsa_public_key_pem"], key=key, iv=iv, nonce=nonce,
            timestamp_ms=timestamp_ms, ticks_100ns=time.time_ns() // 100,
            signing=signing, rsa_padding=ps,
        )
        result = await module.send_request(prepared, endpoint=options["endpoint"],
                                           headers=options["headers"], client=client,
                                           timeout_seconds=options["timeout_seconds"])
        value = result.qimei36
        if type(value) is not str or _QIMEI.fullmatch(value) is None:
            _reject("Generator server response did not contain a valid QIMEI")
        return value.lower()
    except QimeiGeneratorError:
        raise
    except Exception:
        _reject("Experimental QIMEI generation failed")


def main(argv=None) -> int:
    """Legacy-command entry point. Disabled mode fails before config or traffic."""
    if os.environ.get("QIMEI_PYTHON_GENERATOR_ENABLED") != "1":
        print("Experimental QIMEI generator is disabled", file=sys.stderr)
        return 2
    try:
        arguments = sys.argv[1:] if argv is None else argv
        if type(arguments) is not list or len(arguments) != 4 or any(type(value) is not str for value in arguments):
            _reject("Generator requires exactly MAC TAG PORT OCTET")
        mac, tag, port, octet = arguments
        normalized = normalize_mac(mac)
        if (tag.lower() != normalized[-4:] or re.fullmatch(r"[0-9]{4}", port) is None
                or not 8801 <= int(port) <= 8899 or re.fullmatch(r"[1-9][0-9]{1,2}", octet) is None
                or not 60 <= int(octet) <= 119):
            _reject("Generator legacy arguments are invalid")
        path = os.environ.get("QIMEI_PYTHON_GENERATOR_CONFIG")
        config = load_config(path)

        async def run():
            async with httpx.AsyncClient(trust_env=False) as client:
                return await generate(mac, config, client=client)

        value = asyncio.run(run())
        if type(value) is not str or _QIMEI.fullmatch(value) is None:
            _reject("Generator server response did not contain a valid QIMEI")
        print(value.lower())
        return 0
    except KeyboardInterrupt:
        print("Experimental QIMEI generation interrupted", file=sys.stderr)
        return 130
    except Exception:
        print("Experimental QIMEI generation failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
