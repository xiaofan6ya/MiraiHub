"""Offline decoder for the statically observed encrypted QIMEI response path.

This building block does not generate identifiers or send requests. The caller
must supply the request's 16-byte AES key and IV. Real SDK request/key derivation
and a real SDK interoperability vector remain separate validation work.

The accepted envelope is JSON with code exactly "0" or integer 0 and a canonical
standard-Base64 data string. Data is AES-128-CBC with strict PKCS#7 padding, then
protobuf-compatible wire data. Fields 1 through 4 are singleton length-delimited
bytes; only field 1 has an assigned meaning here. Unknown fields with wire types
0, 1, 2, and 5 are skipped. Duplicates of fields 1 through 4, unsupported wire
types, and noncanonical/overflowing varints are rejected rather than merged.
"""
from __future__ import annotations

import base64
import binascii
import json
import math
import re
from dataclasses import dataclass

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_JSON_DEPTH = 64
_PRIMARY = re.compile(rb"[0-9A-Fa-f]{24}3000[0-9A-Fa-f]{8}")


class QimeiProtocolError(RuntimeError):
    """The supplied response cannot be safely decoded; never contains payloads."""


@dataclass(frozen=True)
class QimeiResponse:
    """Decoded primary text and a fresh dict of raw wire fields 1 through 4.

    ``qimei36`` preserves ASCII hex case. ``fields`` includes primary field 1
    and any present fields 2, 3, or 4; those optional bytes have no inferred
    semantics. The dataclass attributes are frozen, while its fresh fields dict
    remains mutable for callers that need a working copy.
    """

    qimei36: str
    fields: dict[int, bytes]


def _reject(message: str) -> None:
    raise QimeiProtocolError(message) from None


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject("Response contains duplicate JSON keys")
        result[key] = value
    return result


def _invalid_constant(_value):
    _reject("Response contains a non-JSON numeric value")


def _preflight_dict(response: dict) -> None:
    """Count compact ensure_ascii JSON before the encoder can escape a string.

    Visit one child at a time, keeping only the active ancestor IDs. Every
    visited character/node consumes the bounded output budget; container length
    lower bounds reject huge collections before walking them. No escaped copy
    of a string or collection of all descendants is created here.
    """
    remaining = MAX_RESPONSE_BYTES
    active = set()

    def consume(size):
        nonlocal remaining
        if size > remaining:
            _reject("Response exceeds the size limit")
        remaining -= size

    def string(value):
        consume(2)  # quotes
        if len(value) > remaining:
            _reject("Response exceeds the size limit")
        for char in value:
            ordinal = ord(char)
            if char in '"\\\b\f\n\r\t':
                consume(2)
            elif 0x20 <= ordinal < 0x7f:
                consume(1)
            else:
                consume(6 if ordinal <= 0xffff else 12)

    def number_text(value):
        if type(value) is int:
            # A conservative decimal-digit lower bound prevents conversion of
            # an arbitrarily large integer to a whole unbounded decimal string.
            digits_lower_bound = max(1, (value.bit_length() - 1) * 301 // 1000 + 1)
            if digits_lower_bound + (value < 0) > remaining:
                _reject("Response exceeds the size limit")
        elif not math.isfinite(value):
            _reject("Response contains a non-JSON numeric value")
        return str(value)

    def visit(value, depth):
        kind = type(value)
        if kind is str:
            string(value)
        elif value is None:
            consume(4)
        elif kind is bool:
            consume(4 if value else 5)
        elif kind in (int, float):
            consume(len(number_text(value)))
        elif kind in (dict, list, tuple):
            if depth >= _MAX_JSON_DEPTH:
                _reject("Response JSON nesting exceeds the limit")
            identity = id(value)
            if identity in active:
                _reject("Response contains a cyclic JSON structure")
            consume(2)  # container delimiters
            count = len(value)
            minimum = (5 if kind is dict else 2) * count - 1
            if count and minimum > remaining:
                _reject("Response exceeds the size limit")
            active.add(identity)
            try:
                if kind is dict:
                    for index, (key, item) in enumerate(value.items()):
                        if index:
                            consume(1)
                        if type(key) is str:
                            string(key)
                        elif key is None:
                            string("null")
                        elif type(key) is bool:
                            string("true" if key else "false")
                        elif type(key) in (int, float):
                            string(number_text(key))
                        else:
                            _reject("Response contains a non-JSON object key")
                        consume(1)  # colon
                        visit(item, depth + 1)
                else:
                    for index, item in enumerate(value):
                        if index:
                            consume(1)
                        visit(item, depth + 1)
            finally:
                active.remove(identity)
        else:
            _reject("Response contains a non-JSON value")

    visit(response, 0)


def _envelope(response: bytes | str | dict) -> dict:
    try:
        if type(response) is dict:
            _preflight_dict(response)
            # Preflight bounds each escaped chunk as well as the whole document.
            # Retain the streaming budget as a second check during serialization.
            chunks = []
            size = 0
            encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False,
                                       separators=(",", ":"))
            for chunk in encoder.iterencode(response):
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    _reject("Response exceeds the size limit")
                chunks.append(chunk)
            text = "".join(chunks)
        elif type(response) is bytes:
            if len(response) > MAX_RESPONSE_BYTES:
                _reject("Response exceeds the size limit")
            text = response.decode("utf-8", errors="strict")
        elif type(response) is str:
            if len(response) > MAX_RESPONSE_BYTES or len(response.encode("utf-8")) > MAX_RESPONSE_BYTES:
                _reject("Response exceeds the size limit")
            text = response
        else:
            _reject("Response must be JSON bytes, text, or a dictionary")
        value = json.loads(text, object_pairs_hook=_unique_pairs,
                           parse_constant=_invalid_constant)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _reject("Response is not a valid JSON envelope")
    if type(value) is not dict:
        _reject("Response envelope must be a JSON object")
    code = value.get("code")
    if not ((type(code) is str and code == "0") or (type(code) is int and code == 0)):
        _reject("Response does not indicate success")
    if type(value.get("data")) is not str:
        _reject("Response data must be Base64 text")
    return value


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for index in range(10):
        if offset >= len(data):
            _reject("Truncated wire varint")
        byte = data[offset]
        offset += 1
        if index == 9 and byte > 1:
            _reject("Wire varint exceeds 64 bits")
        value |= (byte & 0x7f) << (index * 7)
        if byte < 0x80:
            if index and byte == 0:
                _reject("Wire varint is not canonical")
            return value, offset
    _reject("Wire varint exceeds 64 bits")


def _fields(plaintext: bytes) -> dict[int, bytes]:
    fields = {}
    offset = 0
    while offset < len(plaintext):
        tag, offset = _varint(plaintext, offset)
        number, wire_type = tag >> 3, tag & 7
        if number == 0 or number > 0x1fffffff:
            _reject("Wire field number is invalid")
        known = 1 <= number <= 4
        if known and (wire_type != 2 or number in fields):
            _reject("Known wire fields must be singleton byte strings")
        if wire_type == 0:
            _, offset = _varint(plaintext, offset)
            continue
        if wire_type == 1:
            length = 8
        elif wire_type == 2:
            length, offset = _varint(plaintext, offset)
        elif wire_type == 5:
            length = 4
        else:
            _reject("Wire type is unsupported")
        if length > len(plaintext) - offset:
            _reject("Wire field is truncated")
        if known:
            fields[number] = plaintext[offset:offset + length]
        offset += length
    return fields


def decode_response(response: bytes | str | dict, *, key: bytes, iv: bytes) -> QimeiResponse:
    """Decode one bounded response without I/O or fallback identifier creation.

    Bytes/text input is limited to 1 MiB of UTF-8; dict input has the same limit
    on its compact ASCII JSON serialization, preflighted before escaping any
    string. Dict inputs permit native JSON values plus tuples as arrays, with
    at most 64 container levels; cycles and custom value types are rejected.
    Duplicate JSON keys are rejected
    at every nesting level when supplied as bytes/text (a dict has already lost
    any duplicates). Keys and IVs must be exact ``bytes`` objects of length 16.
    All malformed input raises a payload-free ``QimeiProtocolError``.
    """
    if type(key) is not bytes or len(key) != 16:
        _reject("AES-128 key must contain exactly 16 bytes")
    if type(iv) is not bytes or len(iv) != 16:
        _reject("AES-CBC IV must contain exactly 16 bytes")
    envelope = _envelope(response)
    try:
        encoded = envelope["data"].encode("ascii", errors="strict")
        ciphertext = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError, binascii.Error):
        _reject("Response data is not standard Base64")
    if base64.b64encode(ciphertext) != encoded:
        _reject("Response Base64 encoding is not canonical")
    if not ciphertext or len(ciphertext) % 16:
        _reject("Ciphertext must contain complete nonempty AES blocks")
    try:
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
    except ValueError:
        _reject("Response decryption or padding is invalid")
    fields = _fields(plaintext)
    primary = fields.get(1, b"")
    if _PRIMARY.fullmatch(primary) is None:
        _reject("Primary QIMEI field is invalid or missing")
    return QimeiResponse(qimei36=primary.decode("ascii"), fields=fields)
