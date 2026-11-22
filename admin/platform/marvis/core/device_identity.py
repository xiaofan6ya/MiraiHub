"""Explicit, snapshot-backed account identities; importing this module is inert.

QIMEI_MANAGED_IDENTITY=1 opts into this contract. A caller must authenticate or
import the GUID passed to enrollment/reset; this module never invents a login
GUID or proves that credentials remain valid upstream. A request captures one
frozen Context and may finish using it after a concurrent reset commits.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import re
import secrets
import threading
import uuid
from collections import Counter
from dataclasses import dataclass, field
from types import MappingProxyType

from . import qimei_assign as _assign


CONTEXT_KEY = "_qimei_managed_identity_context"
METADATA_KEY = "marvis_managed_identity_v1"
_RECORD_FIELDS = {"seed", "generation", "guid", "qimei36"}
_SEED = re.compile(r"[0-9a-f]{64}")
_PLACEHOLDERS = {"0", "null", "none", "undefined", "unknown"}
_ERROR = "managed device identity is unavailable"
_context_cache_lock = threading.RLock()
_context_cache = (None, None)


class DeviceIdentityError(RuntimeError):
    """Fail closed without exposing identity, configuration or filesystem data."""

    def __init__(self):
        super().__init__(_ERROR)


class DeviceIdentityCommitUncertain(DeviceIdentityError):
    """Read back the snapshot before deciding whether a write needs retrying."""

    def __init__(self):
        RuntimeError.__init__(self, "managed device commit requires read-back before retry")


def _fail():
    raise DeviceIdentityError()


def _digest(label: bytes, *parts) -> bytes:
    digest = hashlib.sha256(label)
    for value in parts:
        encoded = value if isinstance(value, bytes) else str(value).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.digest()


@dataclass(frozen=True)
class Context:
    uid: str = field(repr=False)
    qimei36: str = field(repr=False)
    guid: str = field(repr=False)
    dev18: bytes = field(repr=False)
    generation: int

    def conversation_id(self, base: str) -> str:
        """Keep the caller's conversation separation within this identity epoch."""
        try:
            if type(base) is not str:
                _fail()
            raw = _digest(b"marvis-managed-cid-v1\0", self.uid, self.qimei36,
                          self.generation, self.dev18, base)
            return str(uuid.UUID(bytes=raw[:16], version=4))
        except Exception:
            raise DeviceIdentityError() from None


def enabled() -> bool:
    """Return only the explicit flag; other APIs enforce its prerequisites."""
    return os.environ.get("QIMEI_MANAGED_IDENTITY") == "1"


def _require_mode():
    if (not enabled() or not (os.environ.get("QIMEI_SNAPSHOT_PATH") or "").strip()
            or (os.environ.get("MARVIS_QIMEI36") or "").strip()):
        _fail()
    if _assign._snapshot_path() is None:
        _fail()


def _uid(value) -> str:
    user = _assign.normalize_uid(value)
    if not user or user.startswith("_"):
        _fail()
    return user


def _account_uid(acct: dict) -> str:
    if type(acct) is not dict:
        _fail()
    for name in ("uid", "id", "openid"):
        value = str(acct.get(name) or "").strip()
        if value:
            return _uid(value)
    _fail()


def _guid(value) -> str:
    # Match device_binding.is_valid_guid without importing its startup package.
    if type(value) is not str:
        _fail()
    text = value.strip()
    if not 8 <= len(text) <= 64 or text.lower() in _PLACEHOLDERS:
        _fail()
    return text


def _qimei(value) -> str:
    if type(value) is not str:
        _fail()
    text = _assign.normalize_qimei(value)
    if _assign.QIMEI_RE.fullmatch(text) is None:
        _fail()
    return text


def _sole_mapping(state, user: str, qimei_counts=None) -> str:
    value = state.table.get(user)
    if type(value) is not str or _qimei(value) != value:
        _fail()
    entry = state.pool.get(value)
    if (type(entry) is not dict or entry.get("real") is not True
            or entry.get("owners") != [user]
            or (qimei_counts.get(value, 0) if qimei_counts is not None
                else sum(q == value for q in state.table.values())) != 1):
        _fail()
    return value


def _records(state) -> dict:
    if state.snapshot_path is None or type(state.metadata) is not dict:
        _fail()
    if METADATA_KEY not in state.metadata:
        return {}
    namespace = state.metadata[METADATA_KEY]
    if (type(namespace) is not dict or set(namespace) != {"schema", "accounts"}
            or type(namespace["schema"]) is not int or namespace["schema"] != 1
            or type(namespace["accounts"]) is not dict):
        _fail()
    accounts = namespace["accounts"]
    qimei_counts = Counter(state.table.values())
    seen_guids, seen_qimei = set(), set()
    for user, record in accounts.items():
        if type(user) is not str or _uid(user) != user:
            _fail()
        if type(record) is not dict or set(record) != _RECORD_FIELDS:
            _fail()
        if (type(record["seed"]) is not str or _SEED.fullmatch(record["seed"]) is None
                or type(record["generation"]) is not int or record["generation"] < 1):
            _fail()
        guid = _guid(record["guid"])
        qimei = _qimei(record["qimei36"])
        if (guid != record["guid"] or qimei != record["qimei36"]
                or qimei != _sole_mapping(state, user, qimei_counts)
                or guid.casefold() in seen_guids or qimei in seen_qimei):
            _fail()
        seen_guids.add(guid.casefold())
        seen_qimei.add(qimei)
    return accounts


def _context(user: str, record: dict) -> Context:
    dev18 = _digest(b"marvis-managed-dev18-v1\0", user, record["qimei36"],
                    record["generation"], bytes.fromhex(record["seed"]))[:18]
    return Context(user, record["qimei36"], record["guid"], dev18, record["generation"])


def _request_contexts(state):
    """Cache one fully validated request state as immutable account contexts."""
    global _context_cache
    cached_state, contexts = _context_cache
    if state is cached_state:
        return contexts
    with _context_cache_lock:
        cached_state, contexts = _context_cache
        if state is cached_state:
            return contexts
        # A failed validation must not leave an earlier state's contexts active.
        _context_cache = (None, None)
        contexts = MappingProxyType({user: _context(user, record)
                                     for user, record in _records(state).items()})
        _context_cache = (state, contexts)
        return contexts


def _check_credential(acct: dict, context: Context):
    if (_account_uid(acct) != context.uid or _guid(acct.get("guid")) != context.guid
            or _qimei(context.qimei36) != context.qimei36
            or type(context.dev18) is not bytes or len(context.dev18) != 18
            or type(context.generation) is not int or context.generation < 1):
        _fail()
    device = acct.get("device")
    if device is not None:
        if (type(device) is not dict or _guid(device.get("guid")) != context.guid
                or _qimei(device.get("qimei36")) != context.qimei36):
            _fail()


def resolve(acct: dict) -> Context | None:
    """Capture once, or reuse the caller's already captured in-flight context."""
    global _context_cache
    if not enabled():
        return None
    try:
        _require_mode()
        user = _account_uid(acct)
        if CONTEXT_KEY in acct:
            context = acct[CONTEXT_KEY]
            if not isinstance(context, Context):
                _fail()
        else:
            try:
                state = _assign._request_state()
            except Exception:
                with _context_cache_lock:
                    _context_cache = (None, None)
                raise
            context = _request_contexts(state).get(user)
            if context is None:
                _fail()
        _check_credential(acct, context)
        return context
    except Exception:
        raise DeviceIdentityError() from None


def _unique_guid(accounts: dict, user: str, guid: str):
    if any(other != user and record["guid"].casefold() == guid.casefold()
           for other, record in accounts.items()):
        _fail()


def _report(status: str, state, dry_run: bool, generation: int) -> dict:
    return {"ok": True, "status": status, "dry_run": bool(dry_run),
            "revision": state.revision, "generation": generation}


def _new_record(guid: str, qimei: str, generation: int) -> dict:
    seed = secrets.token_hex(32)
    if type(seed) is not str or _SEED.fullmatch(seed) is None:
        _fail()
    return {"seed": seed, "generation": generation, "guid": guid, "qimei36": qimei}


def enroll_ex(uid, *, guid, qimei36=None, state_dir=None, dry_run=False) -> dict:
    """Explicitly adopt one existing real, sole-owner snapshot assignment."""
    try:
        _require_mode()
        user, login_guid = _uid(uid), _guid(guid)
        lock = contextlib.nullcontext() if dry_run else _assign._locked(_assign._state_dir(state_dir))
        with lock:
            state = _assign.load_state(state_dir)
            accounts = _records(state)
            qimei = _sole_mapping(state, user)
            if qimei36 is not None and _qimei(qimei36) != qimei:
                _fail()
            _unique_guid(accounts, user, login_guid)
            existing = accounts.get(user)
            if existing is not None:
                if existing["guid"] != login_guid:
                    _fail()
                return _report("unchanged", state, dry_run, existing["generation"])
            if dry_run:
                return _report("ready", state, True, 1)
            updated = dict(accounts)
            updated[user] = _new_record(login_guid, qimei, 1)
            state.metadata[METADATA_KEY] = {"schema": 1, "accounts": updated}
            _records(state)
            state.dirty = True
            _assign.save_state(state)
            return _report("enrolled", state, False, 1)
    except _assign.QimeiCommitUncertain:
        raise DeviceIdentityCommitUncertain() from None
    except Exception:
        raise DeviceIdentityError() from None


def reset_ex(uid, *, guid, generator=None, state_dir=None, dry_run=False) -> dict:
    """Commit a new QIMEI and epoch with an explicitly supplied new login GUID."""
    try:
        _require_mode()
        user, login_guid = _uid(uid), _guid(guid)
        if generator is not None and not callable(generator):
            _fail()
        lock = contextlib.nullcontext() if dry_run else _assign._locked(_assign._state_dir(state_dir))
        with lock:
            state = _assign.load_state(state_dir)
            accounts = _records(state)
            current = accounts.get(user)
            if current is None or current["guid"].casefold() == login_guid.casefold():
                _fail()
            old_qimei = _sole_mapping(state, user)
            _unique_guid(accounts, user, login_guid)
            generation = current["generation"] + 1
            if dry_run:
                return _report("ready", state, True, generation)
            generate = _assign.generate_real_qimei if generator is None else generator
            qimei = _qimei(generate())
            if qimei in state.pool or qimei in state.table.values():
                _fail()
            record = _new_record(login_guid, qimei, generation)
            if record["seed"] == current["seed"]:
                _fail()
            timestamp = _assign._now()
            updated = dict(accounts)
            updated[user] = record
            state.pool[old_qimei]["owners"].remove(user)
            state.pool[qimei] = {"owners": [user], "real": True, "source": "generated",
                                 "created_at": timestamp, "last_rotated": timestamp,
                                 "last_used_at": timestamp}
            state.table[user] = qimei
            state.metadata[METADATA_KEY] = {"schema": 1, "accounts": updated}
            _records(state)
            state.dirty = True
            _assign.save_state(state)
            return _report("reset", state, False, generation)
    except _assign.QimeiCommitUncertain:
        raise DeviceIdentityCommitUncertain() from None
    except Exception:
        raise DeviceIdentityError() from None
