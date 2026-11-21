"""Bounded offline assembly of the normal compact QIMEI outer packet.

All bytes, entropy, clocks and signing settings are explicit inputs. This module
does not load deployment state, sample a clock, generate entropy, or send HTTP.
It covers only the cpt=1 string-map path. The nested ext map is serialized into
a string before insertion into the outer map. Native value escaping includes
slashes; all caller-provided app-key bytes are limited to printable ASCII.

Preflight reserves the worst-case slash escaping of both Base64 strings before
allocating ciphertext, RSA output, Base64 output or signing material. Therefore
the effective maximum accepted inner frame is smaller than 1 MiB, and a packet
whose actual escaped body would fit may still be rejected by this conservative
bound. The signing-material limit is checked separately using its exact length.
"""
from __future__ import annotations

from base64 import b64encode

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from .qimei_request import MAX_MATERIAL_BYTES, QimeiSigningConfig, compute_signature


MAX_PACKET_BYTES = 1 << 20
_INT64_MAX = (1 << 63) - 1
_RSA_BASE64_BYTES = 344
_MAP_SYNTAX_BYTES = 60
_ESCAPED = frozenset((ord('"'), ord("/"), ord("\\")))


class QimeiPacketError(RuntimeError):
    """Packet input or assembly failed; messages never include caller values."""


def _reject(message: str) -> None:
    raise QimeiPacketError(message) from None


def _escape_value(value: bytes) -> bytes:
    # Inputs here contain only printable ASCII. The three escaped characters
    # match the native string-map serializer; map keys below are safe constants.
    return value.replace(b"\\", b"\\\\").replace(b'"', b'\\"').replace(b"/", b"\\/")


def _serialize_map(values: dict[bytes, bytes]) -> bytes:
    """Serialize internal constant-key maps in unsigned byte-key order."""
    return b"{" + b",".join(b'"' + name + b'":"' + _escape_value(values[name]) + b'"'
                              for name in sorted(values)) + b"}"


def encode_packet(inner_frame: bytes, *, app_key: bytes, rsa_public_key_pem: bytes,
                  key: bytes, iv: bytes, nonce: bytes, timestamp_ms: int,
                  ticks_100ns: int, signing: QimeiSigningConfig,
                  rsa_padding: bytes) -> bytes:
    """Return only the compact JSON body using caller-supplied cryptographic data.

    ``key`` and ``iv`` are sixteen raw bytes each; ``nonce`` is eight raw bytes.
    ``rsa_padding`` is exactly 221 nonzero bytes, making RSAES-PKCS1-v1_5 fully
    deterministic. Only a 2048-bit RSA public key with exponent 65537 is accepted.
    ``inner_frame`` is opaque exact bytes, including an explicitly empty input.
    The nonempty app key must contain printable ASCII bytes (0x20 through 0x7e).
    Both clocks require exact nonnegative int64 values; no clock is sampled.
    """
    byte_inputs = (inner_frame, app_key, rsa_public_key_pem, key, iv, nonce, rsa_padding)
    if any(type(value) is not bytes for value in byte_inputs):
        _reject("Packet byte inputs must be exact bytes objects")
    if len(key) != 16 or len(iv) != 16 or len(nonce) != 8:
        _reject("Packet key, IV or nonce has an invalid length")
    if len(rsa_padding) != 221 or b"\x00" in rsa_padding:
        _reject("RSA padding must contain exactly 221 nonzero bytes")
    if any(type(value) is not int or not 0 <= value <= _INT64_MAX
           for value in (timestamp_ms, ticks_100ns)):
        _reject("Packet clocks must be exact nonnegative int64 integers")
    if any(len(value) > MAX_PACKET_BYTES
           for value in (inner_frame, app_key, rsa_public_key_pem)):
        _reject("Packet input exceeds the byte budget")
    if not app_key or any(value < 0x20 or value > 0x7e for value in app_key):
        _reject("Packet app key must be nonempty printable ASCII")
    try:
        # Empty material validates the exact config (including tampering) without
        # producing a signature or allocating material derived from the packet.
        compute_signature(b"", ticks_100ns=ticks_100ns, config=signing)
    except Exception:
        _reject("Packet signing configuration is invalid")

    timestamp = str(timestamp_ms).encode("ascii")
    padded_size = (len(inner_frame) // 16 + 1) * 16
    params_size = 4 * ((padded_size + 2) // 3)
    material_size = 1 + _RSA_BASE64_BYTES + params_size + len(timestamp) + 16
    if material_size > MAX_MATERIAL_BYTES:
        _reject("Packet signing material exceeds the byte budget")
    # The inner map contributes fourteen syntax bytes, four quotes of which
    # become escaped in ext's outer string. Each escaped app-key byte grows to
    # four bytes across the two value-escaping passes; other bytes remain one.
    ext_size = 18 + sum(4 if value in _ESCAPED else 1 for value in app_key)
    signature_size = 68 if signing.enabled else 0
    packet_bound = (_MAP_SYNTAX_BYTES + 1 + ext_size + 2 * _RSA_BASE64_BYTES
                    + 16 + 2 * params_size + signature_size + len(timestamp))
    if packet_bound > MAX_PACKET_BYTES:
        _reject("Packet escaped JSON exceeds the byte budget")

    try:
        public_key = load_pem_public_key(rsa_public_key_pem)
        if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size != 2048:
            _reject("Packet RSA public key must have 2048 bits and exponent 65537")
        numbers = public_key.public_numbers()
        if numbers.e != 65537:
            _reject("Packet RSA public key must have 2048 bits and exponent 65537")

        encoded_key = b"\x00\x02" + rsa_padding + b"\x00" + key + iv
        encrypted_key = pow(int.from_bytes(encoded_key, "big"), numbers.e,
                            numbers.n).to_bytes(256, "big")
        key_base64 = b64encode(encrypted_key)
        padding_size = padded_size - len(inner_frame)
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        ciphertext = (encryptor.update(inner_frame + bytes((padding_size,)) * padding_size)
                      + encryptor.finalize())
        params_base64 = b64encode(ciphertext)
        nonce_ascii = nonce.hex().encode("ascii")
        material = b"1" + key_base64 + params_base64 + timestamp + nonce_ascii
        signature = compute_signature(material, ticks_100ns=ticks_100ns,
                                      config=signing).encode("ascii")
        extension = _serialize_map({b"app_key": app_key})
        return _serialize_map({b"cpt": b"1", b"ext": extension, b"ky": key_base64,
                               b"nn": nonce_ascii, b"pms": params_base64,
                               b"sn": signature, b"tm": timestamp})
    except Exception:
        _reject("QIMEI packet assembly failed")
