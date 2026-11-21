"""Persistent regression tests for QIMEI snapshots; synthetic temporary state only."""
from __future__ import annotations

import errno
import importlib.util
import json
import multiprocessing
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "admin/platform/marvis/core/qimei_snapshot.py"


def _load_backend():
    name = "qimei_snapshot_regression_backend"
    spec = importlib.util.spec_from_file_location(name, SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _process_writer(filename, iterations, queue):
    try:
        backend = _load_backend()
        for _ in range(iterations):
            with backend.snapshot_lock(filename):
                current = backend.read_snapshot(filename)
                count = current["table"]["counter"] + 1
                backend.write_snapshot(filename, table={"counter": count},
                                       pool={"counter": count},
                                       expected_revision=current["revision"])
        queue.put(None)
    except BaseException as exc:
        queue.put(type(exc).__name__)


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SOURCE.is_file(), "snapshot backend is not implemented")
        self.backend = _load_backend()
        self.tmp = tempfile.TemporaryDirectory(prefix="qimei-snapshot-regression-")
        self.addCleanup(self.tmp.cleanup)
        # Match the canonical paths used by the backend on Windows short TEMP paths.
        self.directory = Path(self.tmp.name).resolve()
        self.path = self.directory / "state.json"

    def _raw(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def _create(self, **kwargs):
        return self.backend.write_snapshot(self.path, table={"account": "device"},
                                           pool={"device": {"owners": ["account"]}},
                                           expected_revision=0, **kwargs)

    def test_missing_read_does_not_create_directories_or_locks(self):
        target = self.directory / "absent" / "state.json"
        with self.assertRaises(self.backend.SnapshotError):
            self.backend.read_snapshot(target)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_valid_read_is_read_only_and_preserves_metadata(self):
        value = {"schema": 1, "revision": 4, "table": {"A": "Q"}, "pool": {},
                 "metadata": {"source": "synthetic", "nested": [1, {"ok": True}]}}
        self._raw(value)
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        self.assertEqual(self.backend.read_snapshot(self.path), value)
        self.assertEqual({p.name: p.read_bytes() for p in self.directory.iterdir()}, before)

    def test_invalid_json_fails_closed_without_leaking_payload(self):
        for payload in ("", " ", "{sensitive-device", "[]", "null", "{\"schema\": NaN}"):
            with self.subTest(payload=payload[:1]):
                self.path.write_text(payload, encoding="utf-8")
                with self.assertRaises(self.backend.SnapshotError) as caught:
                    self.backend.read_snapshot(self.path)
                self.assertNotIn("sensitive-device", str(caught.exception))
                self.assertEqual(list(self.directory.iterdir()), [self.path])

    @unittest.skipUnless(os.name == "nt", "requires Windows file sharing semantics")
    def test_open_reader_keeps_old_revision_while_writer_atomically_publishes_new(self):
        old = self._create()
        opened, release = threading.Event(), threading.Event()
        values, errors = [], []
        real_load = self.backend.json.load

        def load(stream, *args, **kwargs):
            if threading.current_thread() is reader:
                self.assertFalse(os.get_inheritable(stream.fileno()))
                opened.set()
                if not release.wait(5):
                    raise AssertionError("paused reader was not released")
            return real_load(stream, *args, **kwargs)

        def read():
            try:
                values.append(self.backend.read_snapshot(self.path))
            except BaseException as exc:
                errors.append(exc)

        reader = threading.Thread(target=read, daemon=True)
        with patch.object(self.backend.json, "load", side_effect=load):
            try:
                reader.start()
                self.assertTrue(opened.wait(5))
                new = self.backend.write_snapshot(self.path, table={"new": "value"},
                                                   pool={}, expected_revision=1)
                self.assertEqual(self.backend.read_snapshot(self.path), new)
            finally:
                release.set()
                reader.join(5)
        self.assertFalse(reader.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(values, [old])

    @unittest.skipUnless(os.name == "nt", "requires Windows atomic rename semantics")
    def test_continuous_readers_never_see_missing_or_mixed_snapshot_during_replacement(self):
        self.backend.write_snapshot(self.path, table={"value": "0" * 8192},
                                     pool={}, expected_revision=0)
        stop = threading.Event()
        ready = [threading.Event() for _ in range(4)]
        errors, counts = [], [0] * 4

        def read(index):
            while not stop.is_set():
                try:
                    value = self.backend.read_snapshot(self.path)
                    if value["table"] != {"value": str(value["revision"] - 1) * 8192}:
                        raise AssertionError("mixed snapshot revision")
                    counts[index] += 1
                    ready[index].set()
                except BaseException as exc:
                    errors.append(exc)
                    stop.set()

        readers = [threading.Thread(target=read, args=(i,), daemon=True) for i in range(4)]
        try:
            for reader in readers:
                reader.start()
            self.assertTrue(all(event.wait(5) for event in ready))
            for revision in range(1, 101):
                self.backend.write_snapshot(self.path, table={"value": str(revision) * 8192},
                                             pool={}, expected_revision=revision)
        finally:
            stop.set()
            for reader in readers:
                reader.join(5)
        self.assertFalse(any(reader.is_alive() for reader in readers))
        self.assertEqual(errors, [])
        self.assertTrue(all(counts))
        self.assertEqual(self.backend.read_snapshot(self.path)["revision"], 101)

    @unittest.skipUnless(os.name == "nt", "requires Windows native handle ownership")
    def test_reader_releases_native_handle_and_descriptor_when_wrapping_fails(self):
        import ctypes
        from ctypes import wintypes

        self._create()
        handles, descriptors = [], []
        real_open = self.backend._windows_open

        def opened(*args):
            handle = real_open(*args)
            handles.append(handle)
            return handle

        with patch.object(self.backend, "_windows_open", side_effect=opened):
            with patch.object(self.backend.msvcrt, "open_osfhandle",
                              side_effect=OSError(errno.EMFILE, "sensitive")):
                with self.assertRaises(self.backend.SnapshotError):
                    self.backend.read_snapshot(self.path)
        get_info = ctypes.WinDLL("kernel32", use_last_error=True).GetHandleInformation
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        get_info.restype = wintypes.BOOL
        flags = wintypes.DWORD()
        self.assertFalse(get_info(handles[0], ctypes.byref(flags)))
        self.assertEqual(ctypes.get_last_error(), 6)

        def failed_stream(fd, *args, **kwargs):
            descriptors.append(fd)
            raise OSError(errno.ENOMEM, "sensitive")

        with patch.object(self.backend.os, "fdopen", side_effect=failed_stream):
            with self.assertRaises(self.backend.SnapshotError):
                self.backend.read_snapshot(self.path)
        with self.assertRaises(OSError) as caught:
            os.fstat(descriptors[0])
        self.assertEqual(caught.exception.errno, errno.EBADF)

    @unittest.skipUnless(os.name == "nt", "requires Windows native rename errors")
    def test_native_rename_falls_back_only_for_unsupported_operation(self):
        import ctypes
        import types

        native = self.backend._windows_api()
        for code in (1, 50, 87, 120, 2, 5, 32):
            with self.subTest(winerror=code):
                source = self.directory / "replacement"
                target = self.directory / "destination"
                source.write_bytes(b"complete-new")
                target.write_bytes(b"complete-old")
                handles, fallback = [], []

                def create(*args):
                    handle = native.CreateFileW(*args)
                    handles.append(handle)
                    return handle

                def failed_rename(*args):
                    ctypes.set_last_error(code)
                    return False

                def legacy(source, destination):
                    fallback.append(True)
                    return real_replace(source, destination)

                api = types.SimpleNamespace(CreateFileW=create, CloseHandle=native.CloseHandle,
                                            SetFileInformationByHandle=failed_rename)
                real_replace = os.replace
                with patch.object(self.backend, "_windows_api", return_value=api):
                    with patch.object(self.backend.os, "replace", side_effect=legacy):
                        if code in (1, 50, 87, 120):
                            self.backend._replace_snapshot(source, target)
                            self.assertEqual(target.read_bytes(), b"complete-new")
                            self.assertEqual(fallback, [True])
                        else:
                            with self.assertRaises(OSError) as caught:
                                self.backend._replace_snapshot(source, target)
                            self.assertEqual(caught.exception.winerror, code)
                            self.assertEqual(target.read_bytes(), b"complete-old")
                            self.assertEqual(source.read_bytes(), b"complete-new")
                            self.assertEqual(fallback, [])
                # Successful exclusive reopen proves the native source handle closed.
                check = source if source.exists() else target
                with check.open("rb") as stream:
                    self.assertIn(stream.read(), (b"complete-new", b"complete-old"))
                source.unlink(missing_ok=True)
                target.unlink()

    @unittest.skipUnless(os.name == "nt", "requires Windows UTF-16 native paths")
    def test_native_first_create_and_replacement_support_long_non_bmp_paths(self):
        directory = self.directory
        for _ in range(7):
            directory /= "long-directory-" + "x" * 24
        target = directory / "state-\U0001f9ea.json"
        first = self.backend.write_snapshot(target, table={"old": "complete"},
                                             pool={}, expected_revision=0)
        self.assertEqual(self.backend.read_snapshot(target), first)
        second = self.backend.write_snapshot(target, table={"new": "complete"},
                                              pool={}, expected_revision=1)
        self.assertEqual(self.backend.read_snapshot(target), second)

    @unittest.skipUnless(os.name == "nt", "requires Windows post-rename handle errors")
    def test_native_close_failure_after_rename_reports_uncertain_committed_revision(self):
        import ctypes
        import types

        self._create()
        native = self.backend._windows_api()
        handle = None

        def create(*args):
            nonlocal handle
            handle = native.CreateFileW(*args)
            return handle

        def close(value):
            native.CloseHandle(value)
            ctypes.set_last_error(6)
            return False

        api = types.SimpleNamespace(CreateFileW=create, CloseHandle=close,
                                    SetFileInformationByHandle=native.SetFileInformationByHandle)
        real_api = self.backend._windows_api
        calls = []

        def library():
            calls.append(True)
            # write_snapshot first reads current revision, then opens replacement.
            return real_api() if len(calls) == 1 else api

        with patch.object(self.backend, "_windows_api", side_effect=library):
            with self.assertRaises(self.backend.SnapshotCommitUncertain):
                self.backend.write_snapshot(self.path, table={"new": "complete"},
                                             pool={}, expected_revision=1)
        self.assertIsNotNone(handle)
        self.assertEqual(self.backend.read_snapshot(self.path)["revision"], 2)
        self.assertEqual(self.backend.read_snapshot(self.path)["table"], {"new": "complete"})

    @unittest.skipUnless(os.name == "nt", "requires Windows stable leaf resolution")
    def test_canonical_never_resolves_an_ordinary_replaceable_leaf(self):
        self._create()
        real_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            if path == self.path:
                raise AssertionError("ordinary leaf could resolve to a deleted inode")
            return real_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", new=resolve):
            self.assertEqual(self.backend._canonical(self.path), self.path)
            self.assertEqual(self.backend.read_snapshot(self.path)["revision"], 1)
            self.backend.write_snapshot(self.path, table={}, pool={}, expected_revision=1)

    @unittest.skipUnless(os.name == "nt", "requires Windows symlink lock aliases")
    def test_symlink_alias_keeps_the_same_canonical_writer_lock(self):
        self._create()
        alias = self.directory / "alias.json"
        try:
            alias.symlink_to(self.path.name)
        except OSError:
            self.skipTest("host does not permit synthetic file symlinks")
        self.assertEqual(self.backend._canonical(alias), self.path)
        with self.backend.snapshot_lock(alias):
            with self.backend.snapshot_lock(self.path):
                updated = self.backend.write_snapshot(alias, table={"new": "value"},
                                                       pool={}, expected_revision=1)
        self.assertTrue(alias.is_symlink())
        self.assertEqual(self.backend.read_snapshot(alias), updated)

    def test_strict_schema_revision_and_shapes_are_required(self):
        base = {"schema": 1, "revision": 1, "table": {}, "pool": {}}
        invalid = [{}, {**base, "schema": True}, {**base, "schema": 1.0},
                   {**base, "schema": 2}, {**base, "revision": True},
                   {**base, "revision": 0}, {**base, "revision": -1},
                   {**base, "revision": 1.5}, {**base, "table": []},
                   {**base, "pool": []}, {**base, "metadata": None},
                   {**base, "metadata": []}]
        for value in invalid:
            with self.subTest(value=value):
                self._raw(value)
                with self.assertRaises(self.backend.SnapshotError):
                    self.backend.read_snapshot(self.path)

    def test_duplicate_json_fields_are_rejected(self):
        self.path.write_text('{"schema":1,"revision":1,"table":{},"pool":{},"revision":2}',
                             encoding="utf-8")
        with self.assertRaises(self.backend.SnapshotError):
            self.backend.read_snapshot(self.path)

    def test_unknown_schema_fields_fail_closed_without_discarding_data(self):
        value = {"schema": 1, "revision": 1, "table": {}, "pool": {},
                 "sensitive-device": {"future": "data"}}
        self._raw(value)
        before = self.path.read_bytes()
        with self.assertRaises(self.backend.SnapshotError) as caught:
            self.backend.read_snapshot(self.path)
        self.assertNotIn("sensitive-device", str(caught.exception))
        with self.assertRaises(self.backend.SnapshotError):
            self.backend.write_snapshot(self.path, table={}, pool={}, expected_revision=1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_create_update_and_metadata_preservation(self):
        created = self._create(metadata={"migration": {"approved": True}})
        self.assertEqual(created["revision"], 1)
        self.assertEqual(created["schema"], 1)
        updated = self.backend.write_snapshot(self.path, table={"next": "device"},
                                               pool={}, expected_revision=1)
        self.assertEqual(updated, {"schema": 1, "revision": 2,
                                   "table": {"next": "device"}, "pool": {},
                                   "metadata": {"migration": {"approved": True}}})
        self.assertEqual(self.backend.read_snapshot(self.path), updated)
        cleared = self.backend.write_snapshot(self.path, table={}, pool={},
                                               expected_revision=2, metadata={})
        self.assertEqual(cleared["metadata"], {})

    def test_create_only_and_stale_update_preserve_existing_bytes(self):
        self._create()
        before = self.path.read_bytes()
        for revision in (0, 2):
            with self.subTest(revision=revision):
                with self.assertRaises(self.backend.SnapshotConflict):
                    self.backend.write_snapshot(self.path, table={}, pool={},
                                                 expected_revision=revision)
                self.assertEqual(self.path.read_bytes(), before)

    def test_update_missing_destination_conflicts_without_creating_snapshot(self):
        with self.assertRaises(self.backend.SnapshotConflict):
            self.backend.write_snapshot(self.path, table={}, pool={}, expected_revision=1)
        self.assertFalse(self.path.exists())

    def test_update_invalid_destination_fails_closed(self):
        self.path.write_text("not-valid-json", encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaises(self.backend.SnapshotError):
            self.backend.write_snapshot(self.path, table={}, pool={}, expected_revision=1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_writer_inputs_do_not_replace_snapshot(self):
        self._create()
        before = self.path.read_bytes()
        for kwargs in ({"expected_revision": True}, {"expected_revision": -1},
                       {"expected_revision": 1.0}, {"table": []}, {"pool": []},
                       {"metadata": []}, {"table": {"sensitive-device": object()}},
                       {"metadata": {"bad": float("nan")}},
                       {"metadata": {"text": "sensitive-device\ud800"}}):
            with self.subTest(field=next(iter(kwargs))):
                args = {"table": {}, "pool": {}, "expected_revision": 1, **kwargs}
                with self.assertRaises(self.backend.SnapshotError) as caught:
                    self.backend.write_snapshot(self.path, **args)
                self.assertNotIn("sensitive-device", str(caught.exception))
                self.assertEqual(self.path.read_bytes(), before)
                self.assertFalse(any(".tmp." in p.name for p in self.directory.iterdir()))

    def test_replace_errors_never_fall_back_to_in_place_write(self):
        self._create()
        before = self.path.read_bytes()
        for code in (errno.EBUSY, errno.EXDEV, errno.EPERM, errno.EACCES, errno.EIO):
            with self.subTest(errno=code):
                with patch.object(self.backend, "_replace_snapshot",
                                  side_effect=OSError(code, "sensitive-device")):
                    with self.assertRaises(self.backend.SnapshotError) as caught:
                        self.backend.write_snapshot(self.path, table={"changed": True},
                                                     pool={}, expected_revision=1)
                self.assertNotIsInstance(caught.exception, self.backend.SnapshotCommitUncertain)
                self.assertNotIn("sensitive-device", str(caught.exception))
                self.assertEqual(self.path.read_bytes(), before)
                self.assertFalse(any(".tmp." in p.name for p in self.directory.iterdir()))

    def test_file_fsync_failure_preserves_old_snapshot_and_cleans_temp(self):
        self._create()
        before = self.path.read_bytes()
        real_mkstemp, real_fsync = self.backend.tempfile.mkstemp, self.backend.os.fsync
        temporary_fds = set()

        def mkstemp(*args, **kwargs):
            fd, name = real_mkstemp(*args, **kwargs)
            temporary_fds.add(fd)
            return fd, name

        def fsync(fd):
            if fd in temporary_fds:
                raise OSError(errno.EIO, "secret")
            return real_fsync(fd)

        with patch.object(self.backend.tempfile, "mkstemp", side_effect=mkstemp):
            with patch.object(self.backend.os, "fsync", side_effect=fsync):
                with self.assertRaises(self.backend.SnapshotError) as caught:
                    self.backend.write_snapshot(self.path, table={}, pool={},
                                                 expected_revision=1)
        self.assertNotIsInstance(caught.exception, self.backend.SnapshotCommitUncertain)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(any(".tmp." in p.name for p in self.directory.iterdir()))

    def test_directory_fsync_failure_reports_uncertain_committed_revision(self):
        self._create()
        real_open, real_fsync = self.backend.os.open, self.backend.os.fsync
        real_replace = self.backend._replace_snapshot
        directory_fds = set()
        committed = False

        def open_for_sync(path, flags, *args, **kwargs):
            if os.fspath(path) == str(self.directory):
                fd = real_open(self.path, os.O_RDWR)
                directory_fds.add(fd)
                return fd
            return real_open(path, flags, *args, **kwargs)

        def fsync(fd):
            if committed and fd in directory_fds:
                raise OSError(errno.EIO, "sensitive-device")
            return real_fsync(fd)

        def replace(source, destination):
            nonlocal committed
            real_replace(source, destination)
            committed = True

        with patch.object(self.backend.os, "open", side_effect=open_for_sync):
            with patch.object(self.backend.os, "fsync", side_effect=fsync):
                with patch.object(self.backend, "_replace_snapshot", side_effect=replace):
                    with self.assertRaises(self.backend.SnapshotCommitUncertain) as caught:
                        self.backend.write_snapshot(self.path, table={"new": "value"},
                                                     pool={}, expected_revision=1)
        self.assertIn("2", str(caught.exception))
        self.assertNotIn("sensitive-device", str(caught.exception))
        self.assertEqual(self.backend.read_snapshot(self.path)["revision"], 2)
        self.assertEqual(self.backend.read_snapshot(self.path)["table"], {"new": "value"})
        self.assertFalse(any(".tmp." in p.name for p in self.directory.iterdir()))

    def test_atomic_replace_exposes_only_complete_old_or_new_snapshots(self):
        self._create()
        observations = []
        real_replace = self.backend._replace_snapshot

        def observe_replace(source, destination):
            observations.append(self.backend.read_snapshot(destination))
            real_replace(source, destination)
            observations.append(self.backend.read_snapshot(destination))

        with patch.object(self.backend, "_replace_snapshot", side_effect=observe_replace):
            self.backend.write_snapshot(self.path, table={"next": "different"},
                                         pool={"different": {"owners": ["next"]}},
                                         expected_revision=1)
        self.assertEqual([(s["revision"], s["table"], s["pool"]) for s in observations],
                         [(1, {"account": "device"}, {"device": {"owners": ["account"]}}),
                          (2, {"next": "different"}, {"different": {"owners": ["next"]}})])

    def test_nested_lock_and_write_are_reentrant(self):
        with self.backend.snapshot_lock(self.path):
            with self.backend.snapshot_lock(self.directory / "." / "state.json"):
                self._create()
        self.assertEqual(self.backend.read_snapshot(self.path)["revision"], 1)

    @unittest.skipUnless(os.name == "nt", "requires Windows byte-range locking")
    def test_first_lock_file_contention_waits_instead_of_initializing_unlocked(self):
        lock_path = self.directory / ".state.json.lock"
        competitor_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        self.addCleanup(os.close, competitor_fd)
        real_locking = self.backend.msvcrt.locking
        self.assertEqual(os.fstat(competitor_fd).st_size, 0)
        real_locking(competitor_fd, self.backend.msvcrt.LK_NBLCK, 1)
        contender_attempted = threading.Event()
        entered, errors = [], []

        def observed_locking(fd, mode, count):
            try:
                return real_locking(fd, mode, count)
            except OSError:
                if fd != competitor_fd and mode == self.backend.msvcrt.LK_NBLCK:
                    contender_attempted.set()
                raise

        def contender():
            try:
                with self.backend.snapshot_lock(self.path):
                    entered.append(True)
            except BaseException as exc:
                errors.append((type(exc).__name__, str(exc)))

        writer = threading.Thread(target=contender, daemon=True)
        with patch.object(self.backend.msvcrt, "locking", side_effect=observed_locking):
            try:
                writer.start()
                attempted = contender_attempted.wait(2)
            finally:
                os.lseek(competitor_fd, 0, os.SEEK_SET)
                real_locking(competitor_fd, self.backend.msvcrt.LK_UNLCK, 1)
                writer.join(5)
        self.assertFalse(writer.is_alive(), "contender failed to finish after unlock")
        self.assertEqual(errors, [])
        self.assertTrue(attempted, "contender must wait through the locking retry path")
        self.assertEqual(entered, [True])
        self.assertEqual(lock_path.read_bytes(), b"")

    def test_nonexistent_root_directory_ascent_terminates_without_writing(self):
        attempts = []

        def missing_directory(path):
            attempts.append(path)
            if len(attempts) > len(self.directory.parents) + 6:
                raise AssertionError("directory ascent did not terminate at a missing root")
            return False

        with patch.object(self.backend.Path, "exists", new=missing_directory):
            with self.assertRaises(self.backend.SnapshotError):
                with self.backend.snapshot_lock(self.directory / "missing" / "state.json"):
                    self.fail("a nonexistent filesystem root cannot be a writer directory")
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_created_ancestor_directories_are_synced_before_replacement(self):
        probe = self.directory / "sync-probe"
        probe.write_bytes(b"synthetic")
        first, second = self.directory / "new", self.directory / "new" / "nested"
        target = second / "state.json"
        directories = {second, *second.parents}
        real_open, real_close = self.backend.os.open, self.backend.os.close
        real_fsync, real_mkdir = self.backend.os.fsync, self.backend.Path.mkdir
        real_replace = self.backend._replace_snapshot
        directory_fds, events = {}, []

        def directory_open(path, flags, *args, **kwargs):
            directory = Path(path)
            if directory in directories:
                fd = real_open(probe, os.O_RDWR)
                directory_fds[fd] = directory
                return fd
            return real_open(path, flags, *args, **kwargs)

        def fsync(fd):
            if fd in directory_fds:
                events.append(("sync", directory_fds[fd]))
            return real_fsync(fd)

        def close(fd):
            directory_fds.pop(fd, None)
            return real_close(fd)

        def mkdir(path, *args, **kwargs):
            result = real_mkdir(path, *args, **kwargs)
            events.append(("mkdir", path))
            return result

        def replace(source, destination):
            events.append(("replace", destination))
            return real_replace(source, destination)

        with patch.object(self.backend.os, "open", side_effect=directory_open):
            with patch.object(self.backend.os, "close", side_effect=close):
                with patch.object(self.backend.os, "fsync", side_effect=fsync):
                    with patch.object(self.backend.Path, "mkdir", new=mkdir):
                        with patch.object(self.backend, "_replace_snapshot", side_effect=replace):
                            self.backend.write_snapshot(target, table={}, pool={},
                                                         expected_revision=0)
        replace_index = events.index(("replace", target))
        self.assertEqual(events[:replace_index],
                         [("mkdir", first), ("sync", first), ("sync", self.directory),
                          ("mkdir", second), ("sync", second), ("sync", first)]
                         + [("sync", item) for item in (second, *second.parents)])
        self.assertEqual(events[replace_index + 1:], [("sync", second)])

    def test_retry_syncs_existing_ancestry_left_by_precommit_failure(self):
        probe = self.directory / "sync-probe"
        probe.write_bytes(b"synthetic")
        parent = self.directory / "new"
        target = parent / "state.json"
        ancestry = (parent, *parent.parents)
        real_open, real_close = self.backend.os.open, self.backend.os.close
        real_fsync, real_replace = self.backend.os.fsync, self.backend._replace_snapshot
        directory_fds, events = {}, []
        fail_first_sync = True

        def directory_open(path, flags, *args, **kwargs):
            directory = Path(path)
            if directory in ancestry:
                fd = real_open(probe, os.O_RDWR)
                directory_fds[fd] = directory
                return fd
            return real_open(path, flags, *args, **kwargs)

        def fsync(fd):
            nonlocal fail_first_sync
            directory = directory_fds.get(fd)
            if directory is not None:
                events.append(("sync", directory))
                if fail_first_sync:
                    fail_first_sync = False
                    raise OSError(errno.EIO, "sensitive-device")
            return real_fsync(fd)

        def close(fd):
            directory_fds.pop(fd, None)
            return real_close(fd)

        def replace(source, destination):
            events.append(("replace", destination))
            return real_replace(source, destination)

        with patch.object(self.backend.os, "open", side_effect=directory_open):
            with patch.object(self.backend.os, "close", side_effect=close):
                with patch.object(self.backend.os, "fsync", side_effect=fsync):
                    with patch.object(self.backend, "_replace_snapshot", side_effect=replace):
                        with self.assertRaises(self.backend.SnapshotError) as caught:
                            self.backend.write_snapshot(target, table={}, pool={},
                                                         expected_revision=0)
                        self.assertNotIsInstance(caught.exception,
                                                 self.backend.SnapshotCommitUncertain)
                        self.assertTrue(parent.is_dir())
                        self.assertFalse(target.exists())
                        events.clear()
                        result = self.backend.write_snapshot(target, table={}, pool={},
                                                              expected_revision=0)
        replace_index = events.index(("replace", target))
        self.assertEqual(events[:replace_index], [("sync", item) for item in ancestry])
        self.assertEqual(events[replace_index + 1:], [("sync", parent)])
        self.assertEqual(result["revision"], 1)

    def test_ancestor_directory_sync_failure_is_precommit(self):
        probe = self.directory / "sync-probe"
        probe.write_bytes(b"synthetic")
        first = self.directory / "new"
        target = first / "nested" / "state.json"
        real_open, real_close = self.backend.os.open, self.backend.os.close
        real_fsync = self.backend.os.fsync
        directory_fds = set()

        def directory_open(path, flags, *args, **kwargs):
            if Path(path) == first:
                fd = real_open(probe, os.O_RDWR)
                directory_fds.add(fd)
                return fd
            return real_open(path, flags, *args, **kwargs)

        def fsync(fd):
            if fd in directory_fds:
                raise OSError(errno.EIO, "sensitive-device")
            return real_fsync(fd)

        def close(fd):
            directory_fds.discard(fd)
            return real_close(fd)

        with patch.object(self.backend.os, "open", side_effect=directory_open):
            with patch.object(self.backend.os, "close", side_effect=close):
                with patch.object(self.backend.os, "fsync", side_effect=fsync):
                    with self.assertRaises(self.backend.SnapshotError) as caught:
                        self.backend.write_snapshot(target, table={}, pool={},
                                                     expected_revision=0)
        self.assertNotIsInstance(caught.exception, self.backend.SnapshotCommitUncertain)
        self.assertNotIn("sensitive-device", str(caught.exception))
        self.assertFalse(target.exists())
        self.assertFalse(any(".tmp." in p.name for p in first.rglob("*")))

    def test_existing_ancestor_sync_failure_refuses_update_before_replacement(self):
        self._create()
        before = self.path.read_bytes()
        blocked_ancestor = self.directory.parent
        real_open, real_fsync = self.backend.os.open, self.backend.os.fsync
        ancestor_fds = set()

        def directory_open(path, flags, *args, **kwargs):
            if Path(path) == blocked_ancestor:
                fd = real_open(self.path, os.O_RDWR)
                ancestor_fds.add(fd)
                return fd
            return real_open(path, flags, *args, **kwargs)

        def fsync(fd):
            if fd in ancestor_fds:
                raise OSError(errno.EIO, "sensitive-device")
            return real_fsync(fd)

        with patch.object(self.backend.os, "open", side_effect=directory_open):
            with patch.object(self.backend.os, "fsync", side_effect=fsync):
                with self.assertRaises(self.backend.SnapshotError) as caught:
                    self.backend.write_snapshot(self.path, table={}, pool={},
                                                 expected_revision=1)
        self.assertNotIsInstance(caught.exception, self.backend.SnapshotCommitUncertain)
        self.assertNotIn("sensitive-device", str(caught.exception))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(any(".tmp." in item.name for item in self.directory.iterdir()))

    @unittest.skipUnless(os.name == "nt", "requires Windows directory-open semantics")
    def test_windows_directory_open_missing_error_requires_existing_directory(self):
        with patch.object(self.backend.os, "open",
                          side_effect=OSError(errno.ENOENT, "sensitive-device")):
            self.backend._fsync_directory(self.directory)
            with self.assertRaises(OSError):
                self.backend._fsync_directory(self.directory / "missing")

    def test_thread_writers_serialize_load_modify_save(self):
        self.backend.write_snapshot(self.path, table={"counter": 0}, pool={"counter": 0},
                                     expected_revision=0)
        errors = []

        def increment():
            try:
                for _ in range(8):
                    with self.backend.snapshot_lock(self.path):
                        current = self.backend.read_snapshot(self.path)
                        count = current["table"]["counter"] + 1
                        self.backend.write_snapshot(self.path, table={"counter": count},
                                                     pool={"counter": count},
                                                     expected_revision=current["revision"])
            except BaseException as exc:
                errors.append(type(exc).__name__)

        threads = [threading.Thread(target=increment) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
        self.assertFalse(any(t.is_alive() for t in threads), "writer thread deadlock")
        self.assertEqual(errors, [])
        final = self.backend.read_snapshot(self.path)
        self.assertEqual((final["revision"], final["table"], final["pool"]),
                         (33, {"counter": 32}, {"counter": 32}))

    def test_separate_process_writers_serialize(self):
        self.backend.write_snapshot(self.path, table={"counter": 0}, pool={"counter": 0},
                                     expected_revision=0)
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        processes = [context.Process(target=_process_writer, args=(str(self.path), 5, queue))
                     for _ in range(3)]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(25)
            self.assertFalse(any(p.is_alive() for p in processes), "process writer deadlock")
            self.assertEqual([p.exitcode for p in processes], [0, 0, 0])
            self.assertEqual([queue.get(timeout=3) for _ in processes], [None, None, None])
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            queue.close()
            queue.join_thread()
        final = self.backend.read_snapshot(self.path)
        self.assertEqual((final["revision"], final["table"], final["pool"]),
                         (16, {"counter": 15}, {"counter": 15}))

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not Windows ACLs")
    def test_new_parent_and_snapshot_permissions_are_restrictive(self):
        target = self.directory / "new" / "nested" / "state.json"
        self.backend.write_snapshot(target, table={}, pool={}, expected_revision=0)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(target.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(target.parent.parent.stat().st_mode), 0o700)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not Windows ACLs")
    def test_existing_parent_mode_is_not_changed(self):
        self.directory.chmod(0o750)
        self._create()
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o750)


if __name__ == "__main__":
    unittest.main()
