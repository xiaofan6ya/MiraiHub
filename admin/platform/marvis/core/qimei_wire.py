"""Offline encoding of verified QIMEI header, device payload, and inner frame.

Metadata and the raw request body are explicit caller inputs. This module never
loads SDK state, chooses a deployment key, reads a clock, or generates randomness.
Device-field bytes must already have undergone any collector character conversion.
The 1 MiB budgets cover encoded protobuf bytes, including field/length varints;
an inner frame additionally contains PKCS#7 padding and its five-byte prefix.
"""
from __future__ import annotations

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


MAX_PROTOBUF_BYTES = 1 << 20
INNER_IV = bytes.fromhex("B8221969C27A8019D533B139ADE71BCC")
FALLBACK_INNER_KEY = bytes.fromhex("FF802003F67F55BE8B535A97B01B459D")
_UINT32_MAX = (1 << 32) - 1
_UINT64_MAX = (1 << 64) - 1
_SDK_VERSION = b"5.1.4.2"
_DEVICE_UINT32_FIELDS = frozenset((49, 53))
_DEVICE_BYTE_FIELDS = frozenset(range(1, 58)) - {31, 49, 52, 53}
_DEVICE_FIELDS = _DEVICE_BYTE_FIELDS | _DEVICE_UINT32_FIELDS


class QimeiWireError(RuntimeError):
    """Wire input or encoding failed; messages never contain caller payloads."""


def _varint_size(value: int) -> int:
    return max(1, (value.bit_length() + 6) // 7)


def _append_varint(output: bytearray, value: int) -> None:
    while value >= 0x80:
        output.append((value & 0x7f) | 0x80)
        value >>= 7
    output.append(value)


def _append_bytes_field(output: bytearray, number: int, value: bytes) -> None:
    _append_varint(output, (number << 3) | 2)
    _append_varint(output, len(value))
    output.extend(value)


def encode_header(fields: dict[int, int | bytes]) -> bytes:
    """Encode all thirteen verified fields, including zero/empty values.

    The exact dict must contain exactly integer keys 1..13. Fields 1 and 3 are
    exact uint32 integers, field 13 is an exact int32, and the others are exact
    bytes. The observed constants field 3 == 3 and field 5 == b"5.1.4.2" are
    required. Negative field 13 values use sign-extended uint64 varints.
    """
    if type(fields) is not dict or len(fields) != 13:
        raise QimeiWireError("header requires exactly thirteen fields in an exact dict")
    fields = fields.copy()
    if len(fields) != 13 or any(type(n) is not int or not 1 <= n <= 13 for n in fields):
        raise QimeiWireError("header requires exactly integer field keys 1 through 13")

    size = 0
    for number in range(1, 14):
        value = fields[number]
        if number in (1, 3, 13):
            lower, upper = (-(1 << 31), (1 << 31) - 1) if number == 13 else (0, _UINT32_MAX)
            if type(value) is not int or not lower <= value <= upper:
                raise QimeiWireError("header integer field has an invalid type or range")
            size += 1 + _varint_size(value & _UINT64_MAX)
        else:
            if type(value) is not bytes:
                raise QimeiWireError("header byte fields require exact bytes")
            length = len(value)
            if length > MAX_PROTOBUF_BYTES:
                raise QimeiWireError("header exceeds the protobuf byte budget")
            size += 1 + _varint_size(length) + length
        if size > MAX_PROTOBUF_BYTES:
            raise QimeiWireError("header exceeds the protobuf byte budget")
    if fields[3] != 3 or fields[5] != _SDK_VERSION:
        raise QimeiWireError("header platform or SDK version does not match the verified format")

    output = bytearray()
    for number in range(1, 14):
        value = fields[number]
        if number in (1, 3, 13):
            output.append(number << 3)
            _append_varint(output, value & _UINT64_MAX)
        else:
            _append_bytes_field(output, number, value)
    return bytes(output)


def encode_device_payload(fields: dict[int, bytes | int]) -> bytes:
    """Encode a subset of the verified device fields from post-conversion bytes.

    Callers supply exact bytes after any Windows ACP-to-UTF8 collector conversion;
    this component does not perform that conversion or collect/default metadata.
    Byte fields are 1..57 except 31, 49, 52, 53; fields 49 and 53 are exact
    uint32 integers. Field 1 carries PID text and field 50 carries the builder's
    serialized extra map when supplied. Slots 31 and 52 cannot be supplied. The
    container and field keys must be an exact dict and exact integers, respectively.

    Empty byte inputs are omitted. Nonempty byte inputs emit their prefix before
    the first NUL, even when that prefix is empty. Numeric zero is emitted. Fields
    are ordered by wire number. Each original byte input is limited to 1 MiB to
    bound NUL scanning; the final protobuf, including varints, must also fit 1 MiB.
    """
    if type(fields) is not dict or len(fields) > len(_DEVICE_FIELDS):
        raise QimeiWireError("device payload requires an exact dict of verified fields")
    fields = fields.copy()
    if any(type(n) is not int or n not in _DEVICE_FIELDS for n in fields):
        raise QimeiWireError("device payload contains an invalid field key")

    size = 0
    prefix_lengths = {}
    for number in sorted(fields):
        value = fields[number]
        if number in _DEVICE_UINT32_FIELDS:
            if type(value) is not int or not 0 <= value <= _UINT32_MAX:
                raise QimeiWireError("device numeric fields require exact uint32 integers")
            size += _varint_size(number << 3) + _varint_size(value)
        else:
            if type(value) is not bytes:
                raise QimeiWireError("device byte fields require exact post-conversion bytes")
            if len(value) > MAX_PROTOBUF_BYTES:
                raise QimeiWireError("device byte field exceeds the input byte budget")
            if not value:
                continue
            length = value.find(b"\x00")
            if length < 0:
                length = len(value)
            prefix_lengths[number] = length
            size += _varint_size((number << 3) | 2) + _varint_size(length) + length
        if size > MAX_PROTOBUF_BYTES:
            raise QimeiWireError("device payload exceeds the protobuf byte budget")

    output = bytearray()
    for number in sorted(fields):
        value = fields[number]
        if number in _DEVICE_UINT32_FIELDS:
            _append_varint(output, number << 3)
            _append_varint(output, value)
        elif value:
            _append_bytes_field(output, number, value[:prefix_lengths[number]])
    return bytes(output)


def encode_inner_frame(header: bytes, body: bytes, *, version: int, key: bytes) -> bytes:
    """Encode 03 || BE32(version) || AES128-CBC(PKCS7(two-field protobuf)).

    Header/body are opaque exact bytes. The raw sixteen-byte key and uint32
    version are mandatory: FALLBACK_INNER_KEY is only an explicit caller choice.
    The outer protobuf must fit MAX_PROTOBUF_BYTES before padding/encryption.
    """
    if type(header) is not bytes or type(body) is not bytes:
        raise QimeiWireError("inner frame header and body require exact bytes")
    if type(version) is not int or not 0 <= version <= _UINT32_MAX:
        raise QimeiWireError("inner frame version requires an exact uint32 integer")
    if type(key) is not bytes or len(key) != 16:
        raise QimeiWireError("inner frame key requires exactly sixteen raw bytes")
    if len(header) > MAX_PROTOBUF_BYTES or len(body) > MAX_PROTOBUF_BYTES:
        raise QimeiWireError("inner frame exceeds the protobuf byte budget")
    size = (1 + _varint_size(len(header)) + len(header)
            + 1 + _varint_size(len(body)) + len(body))
    if size > MAX_PROTOBUF_BYTES:
        raise QimeiWireError("inner frame exceeds the protobuf byte budget")

    try:
        outer = bytearray()
        _append_bytes_field(outer, 1, header)
        _append_bytes_field(outer, 2, body)
        padding = 16 - (size % 16)
        padded = bytes(outer) + bytes((padding,)) * padding
        encryptor = Cipher(algorithms.AES(key), modes.CBC(INNER_IV)).encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        return b"\x03" + version.to_bytes(4, "big") + ciphertext
    except Exception:
        raise QimeiWireError("inner frame encoding failed") from None
