"""Offline signing component for the statically reconstructed QIMEI request path.

This component consumes explicit material, configuration and a signing-clock
reading. It does not collect hardware, read configuration files, send requests,
or generate a QIMEI. The byte layout comes from static analysis and is
characterized by original SDK instruction-component vectors with synthetic
inputs. Complete SDK request and server interoperability remain unverified.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms


MAX_MATERIAL_BYTES = 1024 * 1024
_MAX_TICKS = (1 << 63) - 1
_DEFAULT_PREFIX = bytes.fromhex("B435B4069B69AD11ED309651F7DBFA2D")
_CHACHA_NONCE = bytes(8) + bytes.fromhex("a3a244ef7fe5a465")
_HEADER = bytes.fromhex("4c438510")
_MASK_SUFFIX = bytes.fromhex("78563412")


class QimeiRequestError(RuntimeError):
    """Signing input or computation failed; messages never contain input values."""


def _reject(message: str) -> None:
    raise QimeiRequestError(message) from None


@dataclass(frozen=True)
class QimeiSigningConfig:
    """Explicit immutable settings; version zero selects the fixed prefix.

    An eight-byte seed is still required and validated for version zero even
    though that branch does not use it. No deployed configuration is inferred.
    """

    version: int
    seed8: bytes = field(repr=False)
    tail4: bytes
    enabled: bool = True

    def __post_init__(self) -> None:
        _validate_config(self)


def _validate_config(config) -> None:
    if type(config) is not QimeiSigningConfig:
        _reject("Signing configuration must be an explicit QimeiSigningConfig")
    try:
        version, seed8, tail4 = config.version, config.seed8, config.tail4
    except AttributeError:
        _reject("Signing configuration is missing a required field")
    if type(version) is not int or not 0 <= version <= 0xffffffff:
        _reject("Signing configuration version must be an exact uint32")
    if type(seed8) is not bytes or len(seed8) != 8:
        _reject("Signing configuration seed must contain exactly eight bytes")
    if type(tail4) is not bytes or len(tail4) != 4:
        _reject("Signing configuration tail must contain exactly four bytes")
    if type(config.enabled) is not bool:
        _reject("Signing configuration enabled flag must be an exact boolean")


def compute_signature(material: bytes, *, ticks_100ns: int,
                      config: QimeiSigningConfig) -> str:
    """Return the lowercase 68-character signature, or an explicitly empty one.

    ``ticks_100ns`` is the caller's signing-clock read, distinct from any earlier
    timestamp serialized into material. Only its fractional-second microseconds
    enter this layout; this function never samples a clock. Inputs require exact
    native types. Empty or disabled signing still validates every argument.
    """
    if type(material) is not bytes:
        _reject("Signing material must be an exact bytes object")
    if len(material) > MAX_MATERIAL_BYTES:
        _reject("Signing material exceeds the size limit")
    if type(ticks_100ns) is not int or not 0 <= ticks_100ns <= _MAX_TICKS:
        _reject("Signing clock must be an exact nonnegative int64")
    _validate_config(config)
    if not material or not config.enabled:
        return ""

    try:
        first = hashlib.md5(material).digest()
        fraction_us = (ticks_100ns % 10000000) // 10
        chacha_key = fraction_us.to_bytes(4, "little") + bytes(28)
        encryptor = Cipher(algorithms.ChaCha20(chacha_key, _CHACHA_NONCE),
                           mode=None).encryptor()
        transformed = encryptor.update(first) + encryptor.finalize()
        dynamic_key = bytes((0x28, transformed[5], 0x47, 0x2b,
                             first[4], 0x2c, 0x4c, 0x4b))
        prefix = config.seed8 if config.version else _DEFAULT_PREFIX
        second = hashlib.md5(prefix + dynamic_key).digest()
        mask = (first[:4] + bytes((fraction_us >> 24, 0, 0, 0))
                + transformed[1:5] + _MASK_SUFFIX)
        obfuscated = bytes(second[index] ^ dynamic_key[index & 7] ^ mask[index]
                           for index in range(16))
        header = (_HEADER + first[4:5] + transformed[5:6]
                  + fraction_us.to_bytes(4, "big") + obfuscated)
        suffix = config.version.to_bytes(4, "big") + config.tail4
        return (header + suffix).hex()
    except Exception:
        _reject("QIMEI signature computation failed")
