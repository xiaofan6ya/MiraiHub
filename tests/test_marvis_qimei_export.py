"""Persistent rollback-export regressions; synthetic temporary snapshots only."""
from __future__ import annotations

import contextlib
import errno
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import stat
import sys
import tempfile
import types

import pytest


CORE = Path(__file__).resolve().parents[1] / "admin/platform/marvis/core"
A, B = "A" * 32, "B" * 32
Q1, Q2 = "a" * 24 + "3000" + "b" * 8, "c" * 24 + "3000" + "d" * 8
STAMP = "2026-01-01T00:00:00Z"
MARKER = "_qimei_export_complete.json"
METADATA = "_qimei_snapshot_metadata.json"


def _entry(*owners):
    return {"owners": list(owners), "real": True, "source": "synthetic-source",
            "created_at": STAMP, "last_rotated": STAMP, "last_used_at": None}


@pytest.fixture
def exporting(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="qimei-export-regression-") as temporary:
        # Match backend paths when Windows TEMP contains an 8.3 alias.
        root = Path(temporary).resolve()
        source_dir = root / "source"
        source_dir.mkdir()
        snapshot = source_dir / "state.json"
        monkeypatch.setenv("QIMEI_SNAPSHOT_PATH", str(snapshot))
        monkeypatch.setenv("QIMEI_STATE_DIR", str(root / "absent-legacy"))
        monkeypatch.setenv("QIMEI_TABLE_PATH", "")
        monkeypatch.setenv("QIMEI_POOL_PATH", "")
        name = "qimei_export_%x" % id(root)
        package = types.ModuleType(name)
        package.__path__ = [str(CORE)]
        monkeypatch.setitem(sys.modules, name, package)
        spec = importlib.util.spec_from_file_location(name + ".qimei_assign", CORE / "qimei_assign.py")
        qa = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, qa)
        assert spec.loader is not None
        spec.loader.exec_module(qa)

        def write(*, metadata=True):
            payload = {"schema": 1, "revision": 7, "table": {A: Q1, B: Q1},
                       "pool": {Q1: _entry(A, B)}}
            if metadata is not False:
                payload["metadata"] = ({"legacy_comments": {"table": {"_comment": "synthetic 中文"}},
                                        "nested": [True, 1, 1.25, None, {"key": "\\\"/"}]}
                                       if metadata is True else metadata)
            snapshot.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
            return payload

        yield types.SimpleNamespace(root=root, snapshot=snapshot, destination=root / "new-legacy",
                                    qa=qa, write=write)
        for module in tuple(sys.modules):
            if module.startswith(name + "."):
                sys.modules.pop(module, None)


def _api(qa):
    function = getattr(qa, "export_state", None)
    assert callable(function), "lossless snapshot rollback export is not implemented"
    return function


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_export_preserves_latest_revision_bindings_owners_metadata_and_source_bytes(exporting):
    payload = exporting.write()
    exporting.qa.rotate_ex(A, generator=lambda: Q2)
    payload = _read(exporting.snapshot)
    before = exporting.snapshot.read_bytes()
    report = _api(exporting.qa)(exporting.destination)
    assert report == {"ok": True, "status": "exported", "dry_run": False,
                      "revision": 8, "bindings": 2, "devices": 2, "has_metadata": True}
    assert _read(exporting.destination / "qimei_table.json") == payload["table"]
    assert _read(exporting.destination / "qimei_pool.json") == payload["pool"]
    assert _read(exporting.destination / METADATA) == payload["metadata"]
    assert _read(exporting.destination / MARKER) == {
        "schema": 1, "revision": 8, "bindings": 2, "devices": 2, "has_metadata": True}
    assert exporting.snapshot.read_bytes() == before
    assert set(path.name for path in exporting.destination.iterdir()) == {
        "qimei_table.json", "qimei_pool.json", METADATA, MARKER}


@pytest.mark.parametrize("metadata,has_metadata", [(False, False), ({}, True)])
def test_metadata_absence_and_present_empty_object_are_preserved_distinctly(exporting, metadata, has_metadata):
    payload = exporting.write(metadata=metadata)
    report = _api(exporting.qa)(str(exporting.destination))
    assert report["has_metadata"] is has_metadata
    assert (exporting.destination / METADATA).exists() is has_metadata
    if has_metadata:
        assert _read(exporting.destination / METADATA) == payload["metadata"]
    assert _read(exporting.destination / MARKER)["has_metadata"] is has_metadata


def test_preview_does_not_lock_create_paths_write_or_sample_a_clock(exporting, monkeypatch):
    exporting.write()
    before = {str(path.relative_to(exporting.root)): path.read_bytes()
              for path in exporting.root.rglob("*") if path.is_file()}

    def forbidden(*args, **kwargs):
        raise AssertionError("preview attempted a writer or clock")

    monkeypatch.setattr(exporting.qa, "_locked", forbidden)
    monkeypatch.setattr(exporting.qa, "_now", forbidden)
    report = _api(exporting.qa)(exporting.destination, dry_run=True)
    assert report["status"] == "ready" and report["dry_run"] is True
    assert report["revision"] == 7 and not exporting.destination.exists()
    assert {str(path.relative_to(exporting.root)): path.read_bytes()
            for path in exporting.root.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("existing", ["empty-directory", "nonempty-directory", "file"])
def test_existing_destination_is_never_overwritten_including_empty_directory(exporting, existing):
    exporting.write()
    destination = exporting.destination
    if existing == "file":
        destination.write_bytes(b"sensitive user evidence")
    else:
        destination.mkdir()
        if existing == "nonempty-directory":
            (destination / "evidence.txt").write_bytes(b"sensitive user evidence")
    before = exporting.snapshot.read_bytes()
    with pytest.raises(exporting.qa.QimeiPoolError) as caught:
        _api(exporting.qa)(destination)
    assert "sensitive" not in str(caught.value)
    assert exporting.snapshot.read_bytes() == before
    if existing == "file":
        assert destination.read_bytes() == b"sensitive user evidence"
    elif existing == "nonempty-directory":
        assert (destination / "evidence.txt").read_bytes() == b"sensitive user evidence"
    else:
        assert list(destination.iterdir()) == []


def test_missing_parent_is_rejected_without_creating_ancestry(exporting):
    exporting.write()
    destination = exporting.root / "missing-parent" / "export"
    with pytest.raises(exporting.qa.QimeiPoolError):
        _api(exporting.qa)(destination)
    assert not destination.parent.exists()


@pytest.mark.parametrize("target", ["snapshot", "source-directory", "ancestor-directory"])
def test_source_snapshot_and_its_ancestors_cannot_be_export_destinations(exporting, target):
    exporting.write()
    path = {"snapshot": exporting.snapshot, "source-directory": exporting.snapshot.parent,
            "ancestor-directory": exporting.root}[target]
    before = exporting.snapshot.read_bytes()
    with pytest.raises(exporting.qa.QimeiPoolError):
        _api(exporting.qa)(path)
    assert exporting.snapshot.read_bytes() == before


@pytest.mark.parametrize("value", [None, "", b"sensitive", 1, True, "bad\x00path"])
def test_destination_rejects_ambiguous_or_invalid_paths_without_disclosing_them(exporting, value):
    exporting.write()
    with pytest.raises(exporting.qa.QimeiPoolError) as caught:
        _api(exporting.qa)(value)
    assert "sensitive" not in str(caught.value) and caught.value.__cause__ is None
    assert not exporting.destination.exists()


@pytest.mark.parametrize("dry_run", [False, True])
def test_export_requires_snapshot_mode_without_legacy_reads_or_writes(exporting, monkeypatch, dry_run):
    monkeypatch.setenv("QIMEI_SNAPSHOT_PATH", "")
    monkeypatch.setattr(exporting.qa, "load_state", lambda *_: pytest.fail("export read legacy state"))
    monkeypatch.setattr(exporting.qa, "_locked", lambda *_: pytest.fail("export made legacy lock"))
    with pytest.raises(exporting.qa.QimeiPoolError):
        _api(exporting.qa)(exporting.destination, dry_run=dry_run)
    assert not exporting.destination.exists()


@pytest.mark.parametrize("kind", ["missing", "json", "schema", "canonical"])
def test_invalid_snapshot_never_creates_an_export_destination(exporting, kind):
    payload = exporting.write()
    if kind == "missing":
        exporting.snapshot.unlink()
        before = None
    elif kind == "json":
        exporting.snapshot.write_bytes(b"{sensitive state")
        before = exporting.snapshot.read_bytes()
    else:
        if kind == "schema":
            payload["unknown_sensitive_field"] = True
        else:
            payload["pool"][Q1]["owners"] = [A, B, "C" * 32]
        exporting.snapshot.write_text(json.dumps(payload), encoding="utf-8")
        before = exporting.snapshot.read_bytes()
    with pytest.raises(exporting.qa.QimeiPoolError) as caught:
        _api(exporting.qa)(exporting.destination)
    assert "sensitive" not in str(caught.value)
    assert A not in str(caught.value) and Q1 not in str(caught.value)
    assert not exporting.destination.exists()
    if before is None:
        assert not exporting.snapshot.exists()
    else:
        assert exporting.snapshot.read_bytes() == before


def test_actual_export_reads_and_verifies_under_the_snapshot_writer_lock(exporting, monkeypatch):
    exporting.write()
    qa = exporting.qa
    support = qa._snapshot_support()
    lock, read_snapshot = qa._locked, support.read_snapshot
    held, reads = [], []

    @contextlib.contextmanager
    def observed_lock(directory):
        with lock(directory):
            held.append(True)
            try:
                yield
            finally:
                held.pop()

    def observed_read(path):
        assert held
        reads.append(Path(path))
        return read_snapshot(path)

    monkeypatch.setattr(qa, "_locked", observed_lock)
    monkeypatch.setattr(support, "read_snapshot", observed_read)
    report = _api(qa)(exporting.destination)
    assert report["status"] == "exported" and reads == [exporting.snapshot]


def test_completion_marker_is_written_only_after_all_data_are_synced_and_read_back(exporting, monkeypatch):
    payload = exporting.write()
    qa = exporting.qa
    support = qa._snapshot_support()
    original_open, original_fsync = qa.os.open, qa.os.fsync
    original_read = qa._read_import_object
    data_paths = {name: exporting.destination / name for name in ("qimei_table.json", "qimei_pool.json", METADATA)}
    fds, synced, verified = {}, set(), set()

    def opened(path, flags, *args, **kwargs):
        name = Path(path).name
        if name == MARKER:
            assert synced == verified == set(data_paths)
        fd = original_open(path, flags, *args, **kwargs)
        if name in data_paths and flags & os.O_WRONLY:
            fds[fd] = name
        return fd

    def fsync(fd):
        if fd in fds:
            synced.add(fds[fd])
        return original_fsync(fd)

    def read(path):
        result = original_read(path)
        if Path(path).name in data_paths:
            verified.add(Path(path).name)
        return result

    monkeypatch.setattr(qa.os, "open", opened)
    monkeypatch.setattr(qa.os, "fsync", fsync)
    monkeypatch.setattr(qa, "_read_import_object", read)
    _api(qa)(exporting.destination)
    assert _read(exporting.destination / "qimei_pool.json") == payload["pool"]


@pytest.mark.parametrize("failure", ["second-file", "file-fsync", "readback", "directory-fsync"])
def test_data_failures_preserve_incomplete_destination_without_marker_and_source_unchanged(exporting, monkeypatch, failure):
    exporting.write()
    before = exporting.snapshot.read_bytes()
    qa = exporting.qa
    support = qa._snapshot_support()
    real_open, real_fsync = qa.os.open, qa.os.fsync
    real_read, real_sync_dir = qa._read_import_object, support._fsync_directory
    export_fds = set()

    def opened(path, flags, *args, **kwargs):
        if failure == "second-file" and Path(path) == exporting.destination / "qimei_pool.json":
            raise OSError(errno.EIO, "sensitive second write")
        fd = real_open(path, flags, *args, **kwargs)
        if Path(path).parent == exporting.destination and flags & os.O_WRONLY:
            export_fds.add(fd)
        return fd

    def fsync(fd):
        if failure == "file-fsync" and fd in export_fds:
            raise OSError(errno.EIO, "sensitive sync")
        return real_fsync(fd)

    def read(path):
        if failure == "readback" and Path(path).parent == exporting.destination:
            return {"sensitive": "different"}
        return real_read(path)

    def sync_dir(directory):
        if failure == "directory-fsync" and Path(directory) == exporting.destination:
            raise OSError(errno.EIO, "sensitive directory sync")
        return real_sync_dir(directory)

    monkeypatch.setattr(qa.os, "open", opened)
    monkeypatch.setattr(qa.os, "fsync", fsync)
    monkeypatch.setattr(qa, "_read_import_object", read)
    monkeypatch.setattr(support, "_fsync_directory", sync_dir)
    with pytest.raises(qa.QimeiPoolError) as caught:
        _api(qa)(exporting.destination)
    assert "sensitive" not in str(caught.value)
    assert exporting.destination.is_dir()
    assert not (exporting.destination / MARKER).exists()
    assert exporting.snapshot.read_bytes() == before


def test_exclusive_destination_creation_refuses_a_competing_empty_directory(exporting, monkeypatch):
    exporting.write()
    original = Path.mkdir

    def competitor(path, *args, **kwargs):
        if path == exporting.destination:
            original(path)
            (path / "evidence.txt").write_text("sensitive concurrent owner", encoding="utf-8")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", competitor)
    with pytest.raises(exporting.qa.QimeiPoolError):
        _api(exporting.qa)(exporting.destination)
    assert (exporting.destination / "evidence.txt").read_text(encoding="utf-8") == "sensitive concurrent owner"
    assert list(exporting.destination.iterdir()) == [exporting.destination / "evidence.txt"]


def test_exclusive_output_file_creation_preserves_injected_user_evidence(exporting, monkeypatch):
    exporting.write()
    original = exporting.qa.os.open

    def competitor(path, flags, *args, **kwargs):
        if Path(path) == exporting.destination / "qimei_pool.json":
            Path(path).write_text("sensitive concurrent evidence", encoding="utf-8")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(exporting.qa.os, "open", competitor)
    with pytest.raises(exporting.qa.QimeiPoolError):
        _api(exporting.qa)(exporting.destination)
    assert (exporting.destination / "qimei_pool.json").read_text(encoding="utf-8") == "sensitive concurrent evidence"
    assert not (exporting.destination / MARKER).exists()


def test_export_source_budget_is_checked_before_reading_or_creating_destination(exporting, monkeypatch):
    exporting.write()
    monkeypatch.setattr(exporting.qa, "MAX_EXPORT_BYTES", 1)
    support = exporting.qa._snapshot_support()
    monkeypatch.setattr(support, "read_snapshot", lambda *_: pytest.fail("oversized source reached JSON read"))
    with pytest.raises(exporting.qa.QimeiPoolError):
        _api(exporting.qa)(exporting.destination)
    assert not exporting.destination.exists()


@pytest.mark.parametrize("dry_run", [False, True])
def test_aggregate_encoded_budget_includes_metadata_and_marker_before_directory_creation(exporting, monkeypatch, dry_run):
    payload = exporting.write()
    marker = {"schema": 1, "revision": 7, "bindings": 2, "devices": 1, "has_metadata": True}
    total = sum(len(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True)) + 1
                for value in (payload["table"], payload["pool"], payload["metadata"], marker))
    assert exporting.snapshot.stat().st_size < total - 1
    monkeypatch.setattr(exporting.qa, "MAX_EXPORT_BYTES", total - 1)
    with pytest.raises(exporting.qa.QimeiPoolError):
        _api(exporting.qa)(exporting.destination, dry_run=dry_run)
    assert not exporting.destination.exists()


def test_final_directory_sync_failure_never_reports_success_even_if_marker_exists(exporting, monkeypatch):
    exporting.write()
    before = exporting.snapshot.read_bytes()
    support = exporting.qa._snapshot_support()
    real_sync = support._fsync_directory
    calls = []

    def sync(directory):
        if Path(directory) == exporting.destination:
            calls.append(True)
            if len(calls) == 2:
                raise OSError(errno.EIO, "sensitive final durability error")
        return real_sync(directory)

    monkeypatch.setattr(support, "_fsync_directory", sync)
    with pytest.raises(exporting.qa.QimeiPoolError) as caught:
        _api(exporting.qa)(exporting.destination)
    assert (exporting.destination / MARKER).exists()
    assert "sensitive" not in str(caught.value)
    assert exporting.snapshot.read_bytes() == before


def test_dangling_destination_symlink_is_not_followed_or_replaced(exporting):
    exporting.write()
    target = exporting.root / "absent-symlink-target"
    try:
        exporting.destination.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit creating a synthetic directory symlink")
    with pytest.raises(exporting.qa.QimeiPoolError):
        _api(exporting.qa)(exporting.destination)
    assert exporting.destination.is_symlink() and not target.exists()


@pytest.mark.parametrize("changed", ["metadata-boolean", "metadata-int-float", "pool-real-boolean"])
def test_readback_rejects_nested_json_type_changes_without_completing_export(exporting, monkeypatch, changed):
    exporting.write(metadata={"nested": [{"preserve_boolean": True, "preserve_integer": 1}],
                              "other": [False, None, 1.25]})
    before = exporting.snapshot.read_bytes()
    original_read = exporting.qa._read_import_object

    def tampered_read(path):
        path = Path(path)
        if ((changed.startswith("metadata") and path.name == METADATA)
                or (changed == "pool-real-boolean" and path.name == "qimei_pool.json")):
            actual = _read(path)
            if changed == "metadata-boolean":
                actual["nested"][0]["preserve_boolean"] = 1
            elif changed == "metadata-int-float":
                actual["nested"][0]["preserve_integer"] = 1.0
            else:
                actual[Q1]["real"] = 1
            path.write_text(json.dumps(actual), encoding="utf-8")
        return original_read(path)

    monkeypatch.setattr(exporting.qa, "_read_import_object", tampered_read)
    with pytest.raises(exporting.qa.QimeiPoolError):
        _api(exporting.qa)(exporting.destination)
    assert not (exporting.destination / MARKER).exists()
    assert exporting.snapshot.read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions are not Windows ACLs")
def test_new_export_directory_and_all_files_have_restrictive_permissions(exporting):
    exporting.write()
    _api(exporting.qa)(exporting.destination)
    assert stat.S_IMODE(exporting.destination.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in exporting.destination.iterdir())


def test_cli_export_preview_and_success_are_count_only_and_never_expose_paths_or_identities(exporting):
    exporting.write()
    output, errors = io.StringIO(), io.StringIO()
    handler = logging.StreamHandler(errors)
    exporting.qa.logger.addHandler(handler)
    arguments = ["--export-state", "--to-legacy-dir", str(exporting.destination), "--summary-only"]
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            assert exporting.qa.main([*arguments, "--dry-run"]) == 0
        assert json.loads(output.getvalue())["status"] == "ready"
        output.seek(0)
        output.truncate()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            assert exporting.qa.main(arguments) == 0
        assert json.loads(output.getvalue())["status"] == "exported"
        output.seek(0)
        output.truncate()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            assert exporting.qa.main(arguments) == 3
        assert not json.loads(output.getvalue())["ok"]
        for value in (A, B, Q1, str(exporting.snapshot), str(exporting.destination),
                      exporting.qa.mask_uid(A), exporting.qa.mask_qimei(Q1)):
            assert value not in output.getvalue() + errors.getvalue()
    finally:
        exporting.qa.logger.removeHandler(handler)


def test_cli_export_requires_explicit_destination_and_is_mutually_exclusive(exporting):
    exporting.write()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert exporting.qa.main(["--export-state", "--summary-only"]) == 3
    assert not json.loads(output.getvalue())["ok"]
    with contextlib.redirect_stdout(io.StringIO()):
        assert exporting.qa.main(["--export-state", "--rotate", A, "--summary-only"]) == 2
