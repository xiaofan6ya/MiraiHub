"""Atomic, revisioned QIMEI snapshots; account/domain validation belongs to callers.

Readers never create directories or locks. Writers coordinate through a stable
sibling lock and replace one complete snapshot, never a file's contents in place.
Use a directory mount on a local filesystem; individual file mounts cannot be
atomically replaced. Writer preflight syncs existing directory ancestry too, so a
retry cannot skip an entry left by an earlier failed directory sync. Windows
cannot normally open directories for fsync, so only
that explicitly unsupported operation is omitted there. The caller must reload
after SnapshotCommitUncertain before deciding whether an operation needs retrying.
"""
from __future__ import annotations

import contextlib
import errno
import json
import os
import tempfile
import threading
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None


class SnapshotError(RuntimeError):
    """Snapshot storage or structural validation failed; no payload in messages."""


class SnapshotConflict(SnapshotError):
    """The expected revision does not identify the current snapshot."""


class SnapshotCommitUncertain(SnapshotError):
    """Replacement occurred, but its directory durability was not confirmed."""


class _LockState:
    def __init__(self):
        self.mutex = threading.RLock()
        self.local = threading.local()


_lock_states = {}
_lock_states_guard = threading.Lock()


def _canonical(path) -> Path:
    try:
        target = Path(path).expanduser()
        if os.name == "nt":
            # realpath on an ordinary leaf can return its $Deleted name during
            # POSIX rename. Resolve stable parents and follow symlink text so
            # readers and every cooperating writer keep the same lock name.
            target = target.absolute()
            seen = set()
            while True:
                target = target.parent.resolve() / target.name
                if not target.is_symlink():
                    return target
                key = os.path.normcase(str(target))
                if key in seen or len(seen) >= 40:
                    raise RuntimeError("snapshot symlink loop")
                seen.add(key)
                link = Path(os.readlink(target))
                target = link if link.is_absolute() else target.parent / link
        return target.resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        raise SnapshotError("snapshot path resolution failed") from None


def _io_message(operation, exc) -> str:
    return "snapshot %s failed (errno=%s)" % (operation, getattr(exc, "errno", None))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non-finite JSON number")


def _validate(value):
    if not isinstance(value, dict):
        raise SnapshotError("snapshot must be an object")
    if set(value) - {"schema", "revision", "table", "pool", "metadata"}:
        raise SnapshotError("snapshot contains unsupported fields")
    if type(value.get("schema")) is not int or value["schema"] != 1:
        raise SnapshotError("snapshot schema must be integer 1")
    if type(value.get("revision")) is not int or value["revision"] < 1:
        raise SnapshotError("snapshot revision must be a positive integer")
    if not isinstance(value.get("table"), dict) or not isinstance(value.get("pool"), dict):
        raise SnapshotError("snapshot table and pool must be objects")
    if "metadata" in value and not isinstance(value["metadata"], dict):
        raise SnapshotError("snapshot metadata must be an object")
    return value


def _windows_api():
    """Load native declarations only when a Windows reader/writer needs them."""
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                               wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                              ctypes.c_void_p, wintypes.DWORD]
    api.SetFileInformationByHandle.restype = wintypes.BOOL
    return api


def _windows_path(path):
    text = str(path)
    if text.startswith("\\\\?\\"):
        return text
    return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text


def _windows_open(api, path, access):
    import ctypes

    # OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, share READ | WRITE | DELETE.
    handle = api.CreateFileW(_windows_path(path), access, 7, None, 3, 128, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


@contextlib.contextmanager
def _open_reader(path):
    if os.name != "nt":
        with path.open("r", encoding="utf-8") as stream:
            yield stream
        return
    api = _windows_api()
    handle = _windows_open(api, path, 0x80000000)  # GENERIC_READ
    fd = None
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
        handle = None  # Ownership transferred to the CRT descriptor.
        with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as stream:
            yield stream
    finally:
        if fd is not None:
            os.close(fd)
        elif handle is not None:
            api.CloseHandle(handle)


def read_snapshot(path) -> dict:
    """Read one complete revision strictly, without locks or filesystem writes."""
    target = _canonical(path)
    try:
        with _open_reader(target) as stream:
            value = json.load(stream, object_pairs_hook=_unique_object,
                              parse_constant=_reject_constant)
    except FileNotFoundError:
        raise SnapshotError("snapshot is missing") from None
    except OSError as exc:
        raise SnapshotError(_io_message("read", exc)) from None
    except (ValueError, UnicodeError, RecursionError):
        raise SnapshotError("snapshot contains invalid JSON") from None
    return _validate(value)


def _ensure_directory(directory: Path) -> None:
    missing = []
    current = directory
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            raise SnapshotError("snapshot filesystem root is unavailable")
        current = parent
    for item in reversed(missing):
        try:
            item.mkdir(mode=0o700)
        except FileExistsError:
            if not item.is_dir():
                raise
        _fsync_directory(item)
        _fsync_directory(item.parent)
    current = directory
    while True:
        _fsync_directory(current)
        parent = current.parent
        if parent == current:
            break
        current = parent


def _acquire_os_lock(fd):
    if fcntl is not None:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                return
            except InterruptedError:
                continue
    if msvcrt is None:
        raise SnapshotError("snapshot OS locking is unavailable")
    # Windows byte-range locks may extend beyond EOF; initialization would race
    # with a different handle locking this byte before an unlocked write.
    while True:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            time.sleep(0.05)


def _release_os_lock(fd):
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
    elif msvcrt is not None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def snapshot_lock(path):
    """Serialize writers across threads/processes, with same-thread reentrancy."""
    target = _canonical(path)
    key = os.path.normcase(str(target))
    with _lock_states_guard:
        state = _lock_states.setdefault(key, _LockState())
    with state.mutex:
        depth = getattr(state.local, "depth", 0)
        if depth:
            state.local.depth = depth + 1
            try:
                yield target
            finally:
                state.local.depth = depth
            return
        fd = None
        locked = False
        try:
            _ensure_directory(target.parent)
            fd = os.open(target.with_name(".%s.lock" % target.name),
                         os.O_CREAT | os.O_RDWR, 0o600)
            _acquire_os_lock(fd)
            locked = True
            state.local.depth = 1
            yield target
        except OSError as exc:
            raise SnapshotError(_io_message("lock", exc)) from None
        finally:
            state.local.depth = 0
            if fd is not None:
                try:
                    if locked:
                        _release_os_lock(fd)
                finally:
                    os.close(fd)


def _fsync_directory(directory):
    try:
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        # Windows does not support opening a directory with os.open for fsync.
        # Its CRT can report ENOENT for an existing drive root; never ignore
        # that code for a directory which really disappeared or is missing.
        if os.name == "nt":
            if exc.errno in (errno.EACCES, errno.EPERM, errno.EINVAL,
                             errno.EISDIR, errno.ENOTSUP):
                return
            if exc.errno == errno.ENOENT and Path(directory).is_dir():
                return
        raise
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace_snapshot(source, target):
    if os.name != "nt":
        os.replace(source, target)
        return
    import ctypes
    from ctypes import wintypes

    class RenameInfo(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("RootDirectory", wintypes.HANDLE),
                    ("FileNameLength", wintypes.DWORD), ("FileName", wintypes.WCHAR * 1)]

    api = _windows_api()
    handle = _windows_open(api, source, 0x00110000)  # DELETE | SYNCHRONIZE
    renamed = False
    error = None
    try:
        encoded = _windows_path(target).encode("utf-16-le")
        length = RenameInfo.FileName.offset + len(encoded)
        buffer = ctypes.create_string_buffer(max(ctypes.sizeof(RenameInfo), length + 2))
        info = RenameInfo.from_buffer(buffer)
        info.Flags = 3  # REPLACE_IF_EXISTS | POSIX_SEMANTICS; no name/backup window.
        info.RootDirectory = None
        info.FileNameLength = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.FileName.offset, encoded, len(encoded))
        # https://learn.microsoft.com/windows/win32/api/winbase/ns-winbase-file_rename_info
        if api.SetFileInformationByHandle(handle, 22, buffer, length):
            renamed = True
        else:
            error = ctypes.get_last_error()
    finally:
        if not api.CloseHandle(handle):
            if renamed:
                raise SnapshotCommitUncertain(
                    "snapshot replaced; native handle close failed; reload before retry") from None
            raise ctypes.WinError(ctypes.get_last_error())
    if error is not None:
        if error in (1, 50, 87, 120):  # Unsupported API/class/flags on older OS/filesystems.
            os.replace(source, target)  # Preserve legacy atomic behavior; never write in place.
        else:
            raise ctypes.WinError(error)


def write_snapshot(path, *, table, pool, expected_revision, metadata=None) -> dict:
    """Compare-and-replace one revision, preserving omitted existing metadata."""
    if type(expected_revision) is not int or expected_revision < 0:
        raise SnapshotError("expected revision must be a nonnegative integer")
    if not isinstance(table, dict) or not isinstance(pool, dict):
        raise SnapshotError("snapshot table and pool must be objects")
    if metadata is not None and not isinstance(metadata, dict):
        raise SnapshotError("snapshot metadata must be an object")
    with snapshot_lock(path) as target:
        current = None
        if expected_revision == 0:
            if target.exists():
                raise SnapshotConflict("snapshot already exists; creation refused")
        else:
            if not target.exists():
                raise SnapshotConflict("snapshot is missing; update refused")
            current = read_snapshot(target)
            if current["revision"] != expected_revision:
                raise SnapshotConflict("snapshot revision changed; reload before retry")
        payload = {"schema": 1, "revision": expected_revision + 1,
                   "table": table, "pool": pool}
        if metadata is not None:
            payload["metadata"] = metadata
        elif current is not None and "metadata" in current:
            payload["metadata"] = current["metadata"]
        try:
            serialized = json.dumps(payload, indent=2, sort_keys=True,
                                    ensure_ascii=False, allow_nan=False) + "\n"
            serialized.encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise SnapshotError("snapshot payload is not JSON serializable") from None
        temporary = None
        fd = None
        replaced = False
        try:
            fd, name = tempfile.mkstemp(prefix=".%s.tmp." % target.name,
                                         dir=target.parent)
            temporary = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                fd = None
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            _replace_snapshot(temporary, target)
            replaced = True
            _fsync_directory(target.parent)
        except OSError as exc:
            if replaced:
                raise SnapshotCommitUncertain(
                    "snapshot revision %d replaced; directory durability uncertain; "
                    "reload before retry" % payload["revision"]) from None
            raise SnapshotError(_io_message("write before replacement", exc)) from None
        finally:
            if fd is not None:
                os.close(fd)
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass
        return json.loads(serialized)
