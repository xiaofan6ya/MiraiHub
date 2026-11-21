"""Experimental QIMEI request preparation and bounded, explicit transport.

Preparation consumes caller-provided fields, keys, entropy and clocks. Sending
requires an injected HTTPX client and explicit HTTPS endpoint and headers. The
caller owns the client's connection and logging configuration. This module
supplies no SDK host or header defaults, collects no device metadata, and emits
no logs. The response decoder uses the exact AES key and IV carried by the
request context.
"""
from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from .qimei_packet import MAX_PACKET_BYTES, encode_packet
from .qimei_protocol import MAX_RESPONSE_BYTES, QimeiResponse, decode_response
from .qimei_request import QimeiSigningConfig
from .qimei_wire import encode_device_payload, encode_header, encode_inner_frame


_MAX_TIMEOUT_SECONDS = 120
_MAX_CLEANUP_GRACE_SECONDS = 0.1
_HEADER_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")


class QimeiClientError(RuntimeError):
    """Request or response failed. Messages never include caller or remote values."""


def _reject(message: str) -> None:
    raise QimeiClientError(message) from None


@dataclass(frozen=True)
class PreparedQimeiRequest:
    """Encoded request and the AES key and IV needed for its response."""

    body: bytes = field(repr=False)
    key: bytes = field(repr=False)
    iv: bytes = field(repr=False)


def prepare_request(header_fields: dict, device_fields: dict, *, inner_version: int,
                    inner_key: bytes, rsa_public_key_pem: bytes, key: bytes,
                    iv: bytes, nonce: bytes, timestamp_ms: int, ticks_100ns: int,
                    signing: QimeiSigningConfig, rsa_padding: bytes) -> PreparedQimeiRequest:
    """Build one explicit type-1 request. Header field 4 also supplies ext.app_key."""
    if type(header_fields) is not dict or type(device_fields) is not dict:
        _reject("QIMEI request fields require exact dictionaries")
    # These are upper bounds for the verified wire slots. The wire encoders
    # validate the exact field sets and budgets after the copies are made.
    if len(header_fields) != 13 or len(device_fields) > 56:
        _reject("QIMEI request has an invalid field count")
    try:
        header_fields = header_fields.copy()
        device_fields = device_fields.copy()
        request_type = header_fields.get(1)
        if type(request_type) is not int or request_type != 1:
            _reject("QIMEI preparation requires normal request type 1")
        header = encode_header(header_fields)
        device = encode_device_payload(device_fields)
        inner = encode_inner_frame(header, device, version=inner_version, key=inner_key)
        body = encode_packet(
            inner, app_key=header_fields[4], rsa_public_key_pem=rsa_public_key_pem,
            key=key, iv=iv, nonce=nonce, timestamp_ms=timestamp_ms,
            ticks_100ns=ticks_100ns, signing=signing, rsa_padding=rsa_padding,
        )
        return PreparedQimeiRequest(body=body, key=key, iv=iv)
    except QimeiClientError:
        raise
    except Exception:
        _reject("QIMEI request preparation failed")


def _request_url(endpoint: str) -> httpx.URL:
    if (type(endpoint) is not str or "#" in endpoint
            or any(ord(character) <= 0x20 or ord(character) == 0x7f for character in endpoint)):
        _reject("QIMEI endpoint must be an absolute HTTPS URL without credentials or a fragment")
    try:
        split = urlsplit(endpoint)
        # Inspect the original authority to catch empty userinfo delimiters,
        # which the HTTPX URL parser can normalize away.
        if split.scheme != "https" or not split.netloc or "@" in split.netloc:
            _reject("QIMEI endpoint must be an absolute HTTPS URL without credentials or a fragment")
        url = httpx.URL(endpoint)
        if not url.is_absolute_url or url.scheme != "https" or url.userinfo:
            _reject("QIMEI endpoint must be an absolute HTTPS URL without credentials or a fragment")
        return url
    except (ValueError, httpx.InvalidURL):
        _reject("QIMEI endpoint is invalid")


def _request_headers(headers: dict[str, str]) -> dict[str, str]:
    if type(headers) is not dict:
        _reject("QIMEI request headers require an exact string dictionary")
    copied = headers.copy()
    for name, value in copied.items():
        if (type(name) is not str or type(value) is not str
                or _HEADER_NAME.fullmatch(name) is None or not value.isascii()
                or any(character in value for character in "\r\n\x00")):
            _reject("QIMEI request contains an invalid header")
        if name.lower() == "accept-encoding" and value.strip().lower() != "identity":
            _reject("QIMEI transport requires identity encoding")
    # Use a single canonical value even if the caller used different casing.
    copied = {name: value for name, value in copied.items() if name.lower() != "accept-encoding"}
    copied["Accept-Encoding"] = "identity"
    return copied


def _request_context(prepared: PreparedQimeiRequest) -> tuple[bytes, bytes, bytes]:
    if type(prepared) is not PreparedQimeiRequest:
        _reject("QIMEI request context has an invalid type")
    try:
        body, key, iv = prepared.body, prepared.key, prepared.iv
    except AttributeError:
        _reject("QIMEI request context is incomplete")
    if type(body) is not bytes or not 0 < len(body) <= MAX_PACKET_BYTES:
        _reject("QIMEI request body is invalid or exceeds the byte budget")
    if type(key) is not bytes or type(iv) is not bytes or len(key) != 16 or len(iv) != 16:
        _reject("QIMEI request context requires a sixteen-byte key and IV")
    return body, key, iv


def _response_limit(response: httpx.Response) -> int | None:
    if response.status_code != 200:
        _reject("QIMEI endpoint did not return HTTP 200")
    encodings = response.headers.get_list("content-encoding")
    if encodings and (len(encodings) != 1 or encodings[0].strip().lower() != "identity"):
        _reject("QIMEI response requires identity encoding")
    lengths = response.headers.get_list("content-length")
    if not lengths:
        return None
    if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
        _reject("QIMEI response has an invalid Content-Length")
    digits = lengths[0].lstrip("0") or "0"
    if len(digits) > len(str(MAX_RESPONSE_BYTES)) or int(digits) > MAX_RESPONSE_BYTES:
        _reject("QIMEI response exceeds the byte budget")
    return int(digits)


async def send_request(prepared: PreparedQimeiRequest, *, endpoint: str,
                       headers: dict[str, str], client: httpx.AsyncClient,
                       timeout_seconds: float = 3.0) -> QimeiResponse:
    """POST one prepared request and decode an identity-encoded bounded response.

    A dedicated HTTPX request keeps client-level headers, cookies, and auth out
    of the request. The injected client still controls connection configuration,
    event hooks and any retries implemented by its transport. This function
    submits the request once and disables HTTPX redirect following.

    HTTP I/O, including HTTPX's implicit close after a complete body, shares the
    request deadline. Final best-effort cleanup has a fresh grace period of at
    most min(timeout_seconds, 0.1) seconds, including after that deadline already
    delivered cancellation. This may add that bounded grace to an error or
    interruption. It assumes cooperative cancellation by the injected transport
    and does not leave a background cleanup task running.
    """
    if not isinstance(client, httpx.AsyncClient):
        _reject("QIMEI transport requires an injected HTTPX AsyncClient")
    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= _MAX_TIMEOUT_SECONDS:
        _reject("QIMEI timeout must be a finite positive number at most 120 seconds")
    body, key, iv = _request_context(prepared)
    url = _request_url(endpoint)
    outgoing = _request_headers(headers)
    timeout = float(timeout_seconds)
    response = None
    try:
        request = httpx.Request(
            "POST", url, content=body, headers=outgoing,
            extensions={"timeout": httpx.Timeout(timeout).as_dict()},
        )
        try:
            async with asyncio.timeout(timeout):
                response = await client.send(request, stream=True, auth=None, follow_redirects=False)
                declared = _response_limit(response)
                buffer = bytearray()
                async for chunk in response.aiter_raw():
                    size = len(buffer) + len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        _reject("QIMEI response exceeds the byte budget")
                    if declared is not None and size > declared:
                        _reject("QIMEI response length does not match Content-Length")
                    buffer.extend(chunk)
                if declared is not None and len(buffer) != declared:
                    _reject("QIMEI response length does not match Content-Length")
        finally:
            if response is not None:
                # The I/O timeout has exited, so its timer cannot replace an
                # active interruption while the independent cleanup timer runs.
                active_error = sys.exception()
                try:
                    async with asyncio.timeout(min(timeout, _MAX_CLEANUP_GRACE_SECONDS)):
                        await response.aclose()
                except Exception:
                    if active_error is None:
                        raise
    except QimeiClientError:
        raise
    except (TimeoutError, httpx.TimeoutException):
        _reject("QIMEI transport timed out")
    except Exception:
        _reject("QIMEI transport failed")
    try:
        return decode_response(bytes(buffer), key=key, iv=iv)
    except Exception:
        _reject("QIMEI response decoding failed")
