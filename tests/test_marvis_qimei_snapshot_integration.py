"""Persistent regression tests for atomic QIMEI state; synthetic fixtures only."""
from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import logging
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


CORE = Path(__file__).resolve().parents[1] / "admin/platform/marvis/core"
UID_A = "A" * 32
UID_B = "B" * 32
Q1 = "a" * 24 + "3000" + "b" * 8
Q2 = "c" * 24 + "3000" + "d" * 8
STAMP = "2026-01-01T00:00:00Z"


def pool_entry(*owners):
    return {"owners": list(owners), "real": True, "source": "synthetic",
            "created_at": STAMP, "last_rotated": STAMP, "last_used_at": None}


class SnapshotIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="qimei-snapshot-integration-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.snapshot = self.root / "state" / "qimei_state.json"
        self.legacy = self.root / "legacy"
        environment = patch.dict(os.environ, {
            "QIMEI_SNAPSHOT_PATH": str(self.snapshot),
            "QIMEI_STATE_DIR": str(self.legacy),
            "QIMEI_TABLE_PATH": "", "QIMEI_POOL_PATH": "",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.package_name = "qimei_snapshot_integration_%x" % id(self)
        package = types.ModuleType(self.package_name)
        package.__path__ = [str(CORE)]
        sys.modules[self.package_name] = package
        self.addCleanup(sys.modules.pop, self.package_name, None)
        self.qa = self._load("qimei_assign")
        self.uskey = self._load("uskey")

    def _load(self, filename, *, direct=False):
        name = (("direct_snapshot_uskey_%x" % id(self)) if direct
                else self.package_name + "." + filename)
        spec = importlib.util.spec_from_file_location(name, CORE / (filename + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        self.addCleanup(sys.modules.pop, name, None)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def _write_snapshot(self, *, revision=1, table=None, pool=None, path=None):
        path = path or self.snapshot
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": 1, "revision": revision,
                   "table": {UID_A: Q1} if table is None else table,
                   "pool": {Q1: pool_entry(UID_A)} if pool is None else pool}
        path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def _inventory(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def _write_legacy(self, *, table=None, pool=None):
        self.legacy.mkdir()
        (self.legacy / "qimei_table.json").write_text(json.dumps(
            {UID_A: Q1} if table is None else table), encoding="utf-8")
        (self.legacy / "qimei_pool.json").write_text(json.dumps(
            {Q1: pool_entry(UID_A)} if pool is None else pool), encoding="utf-8")

    def test_load_uses_one_authoritative_snapshot_without_legacy_files(self):
        self._write_snapshot(revision=7)
        state = self.qa.load_state()
        self.assertEqual(state.table, {UID_A: Q1})
        self.assertEqual(state.pool[Q1]["owners"], [UID_A])
        self.assertEqual(state.revision, 7)
        self.assertFalse(self.legacy.exists())

    def test_missing_snapshot_fails_closed_without_creating_anything(self):
        with self.assertRaises(self.qa.QimeiPoolError):
            self.qa.load_state()
        self.assertEqual(self._inventory(), {})
        self.assertFalse(self.snapshot.parent.exists())
        self.assertFalse(self.legacy.exists())

    def test_corrupt_snapshot_is_not_silently_replaced_by_derived_identity(self):
        self.snapshot.parent.mkdir()
        self.snapshot.write_bytes(b"{")
        before = self._inventory()
        with self.assertRaises(self.qa.QimeiPoolError):
            self.uskey.qimei_for(UID_A)
        self.assertEqual(self._inventory(), before)

    def test_ambiguous_legacy_file_override_is_rejected(self):
        self._write_snapshot()
        with patch.dict(os.environ, {"QIMEI_TABLE_PATH": str(self.root / "other.json")}):
            with self.assertRaises(self.qa.QimeiPoolError):
                self.qa.load_state()

    def test_preview_reads_snapshot_and_leaves_all_files_unchanged(self):
        self._write_snapshot()
        before = self._inventory()
        with patch.object(self.qa, "generate_real_qimei",
                          side_effect=AssertionError("generation forbidden")):
            result = self.qa.assign_ex(UID_B, dry_run=True)
        self.assertEqual(result["qimei36"], Q1)
        self.assertEqual(result["source"], "shared")
        self.assertEqual(result["owners"], 2)
        self.assertEqual(self._inventory(), before)
        self.assertFalse(self.legacy.exists())

    def test_assignment_commits_table_and_pool_in_the_same_new_revision(self):
        self._write_snapshot()
        self.qa.assign_ex(UID_B, generator=False)
        committed = json.loads(self.snapshot.read_text(encoding="utf-8"))
        self.assertEqual(committed["revision"], 2)
        self.assertEqual(committed["table"], {UID_A: Q1, UID_B: Q1})
        self.assertEqual(committed["pool"][Q1]["owners"], [UID_A, UID_B])
        self.assertFalse(self.legacy.exists())

    def test_stale_loaded_state_cannot_overwrite_a_later_assignment(self):
        self._write_snapshot()
        stale = self.qa.load_state()
        self.qa.assign_ex(UID_B, generator=False)
        committed = self.snapshot.read_bytes()
        with self.assertRaises(self.qa.QimeiPoolError):
            self.qa.save_state(stale)
        self.assertEqual(self.snapshot.read_bytes(), committed)

    def test_snapshot_path_changes_are_seen_by_the_same_uskey_module(self):
        self._write_snapshot()
        second = self.root / "other" / "qimei_state.json"
        self._write_snapshot(path=second, table={UID_A: Q2}, pool={Q2: pool_entry(UID_A)})
        self.assertEqual(self.uskey.qimei_for(UID_A), Q1)
        with patch.dict(os.environ, {"QIMEI_SNAPSHOT_PATH": str(second)}):
            self.assertEqual(self.uskey.qimei_for(UID_A), Q2)
        self.assertEqual(self.uskey.qimei_for(UID_A), Q1)

    @unittest.skipUnless(os.name == "nt", "requires Windows realpath rename behavior")
    def test_request_lookup_does_not_resolve_a_replaced_leaf_to_its_deleted_inode(self):
        self._write_snapshot()
        resolve = Path.resolve
        deleted_inode = self.root / "$Deleted" / "old-version"

        def resolving(path, *args, **kwargs):
            if path == self.snapshot:
                return deleted_inode
            return resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", new=resolving):
            self.assertEqual(self.uskey.qimei_for(UID_A), Q1)

    def test_unchanged_snapshot_lookups_share_validation_but_writers_read_fresh(self):
        self._write_snapshot()
        support = self.qa._snapshot_support()
        with patch.object(support, "read_snapshot", wraps=support.read_snapshot) as read:
            self.assertEqual(self.uskey.qimei_for(UID_A), Q1)
            self.assertEqual(self.uskey.qimei_for(UID_A), Q1)
            self.assertEqual(read.call_count, 1)
            self.qa.load_state()
            self.qa.load_state()
            self.assertEqual(read.call_count, 3)

    def test_cached_lookup_observes_equal_revision_size_and_mtime_replacement(self):
        self._write_snapshot()
        self.assertEqual(self.uskey.qimei_for(UID_A), Q1)
        before = self.snapshot.stat()
        replacement = self.snapshot.with_name("replacement.json")
        self._write_snapshot(path=replacement, table={UID_A: Q2}, pool={Q2: pool_entry(UID_A)})
        self.assertEqual(replacement.stat().st_size, before.st_size)
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(replacement, self.snapshot)
        self.assertEqual(self.uskey.qimei_for(UID_A), Q2)

    def test_warm_lookup_rejects_deleted_and_corrupt_snapshot_then_recovers(self):
        self._write_snapshot()
        self.assertEqual(self.uskey.qimei_for(UID_A), Q1)
        self.snapshot.unlink()
        with self.assertRaises(self.qa.QimeiPoolError):
            self.uskey.qimei_for(UID_A)
        self.snapshot.write_text("{", encoding="utf-8")
        with self.assertRaises(self.qa.QimeiPoolError):
            self.uskey.qimei_for(UID_A)
        self._write_snapshot(table={UID_A: Q2}, pool={Q2: pool_entry(UID_A)})
        self.assertEqual(self.uskey.qimei_for(UID_A), Q2)

    def test_snapshot_changed_during_request_capture_is_reloaded(self):
        self._write_snapshot()
        original = self.qa.load_state
        loads = []

        def load():
            state = original()
            loads.append(state.revision)
            if len(loads) == 1:
                replacement = self.snapshot.with_name("replacement.json")
                self._write_snapshot(path=replacement, revision=2, table={UID_A: Q2},
                                     pool={Q2: pool_entry(UID_A)})
                os.replace(replacement, self.snapshot)
            return state

        with patch.object(self.qa, "load_state", side_effect=load):
            self.assertEqual(self.uskey.qimei_for(UID_A), Q2)
        self.assertEqual(loads, [1, 2])

    def test_continuous_snapshot_changes_during_capture_fail_closed(self):
        self._write_snapshot()
        original = self.qa.load_state

        def load():
            state = original()
            replacement = self.snapshot.with_name("replacement.json")
            self._write_snapshot(path=replacement, revision=state.revision + 1)
            os.replace(replacement, self.snapshot)
            return state

        with patch.object(self.qa, "load_state", side_effect=load):
            with self.assertRaises(self.qa.QimeiPoolError):
                self.uskey.qimei_for(UID_A)

    def test_lookup_result_cannot_mutate_future_cached_assignments(self):
        self._write_snapshot()
        table = self.uskey._qimei_table()
        table[UID_A] = Q2
        self.assertEqual(self.uskey.qimei_for(UID_A), Q1)

    def test_direct_import_cannot_fall_back_to_a_legacy_table_in_snapshot_mode(self):
        direct = self._load("uskey", direct=True)
        # Even the current fallback is confined to an absent synthetic path.
        with patch.dict(os.environ, {"QIMEI_TABLE_PATH": str(self.root / "absent.json")}):
            with self.assertRaises(RuntimeError):
                direct.qimei_for(UID_A)

    def test_invalid_mutated_state_is_rejected_before_snapshot_replacement(self):
        self._write_snapshot()
        state = self.qa.load_state()
        state.pool[Q1]["owners"] = [UID_A, UID_B, "C" * 32]
        before = self.snapshot.read_bytes()
        with self.assertRaises(self.qa.QimeiPoolError):
            self.qa.save_state(state)
        self.assertEqual(self.snapshot.read_bytes(), before)

    def test_migration_preview_has_no_writes_locks_or_generator_calls(self):
        self._write_legacy()
        before = self._inventory()
        self.assertTrue(callable(getattr(self.qa, "migrate_state", None)))
        with patch.object(self.qa, "generate_real_qimei",
                          side_effect=AssertionError("generation forbidden")):
            report = self.qa.migrate_state(self.legacy, dry_run=True)
        self.assertEqual(report["bindings"], 1)
        self.assertEqual(report["devices"], 1)
        self.assertTrue(report["dry_run"])
        self.assertEqual(self._inventory(), before)
        self.assertFalse(self.snapshot.parent.exists())

    def test_migration_preserves_bindings_ownership_comments_and_source_bytes(self):
        self._write_legacy(table={UID_A: Q1, "_comment": "synthetic table"},
                           pool={Q1: pool_entry(UID_A), "_comment": "synthetic pool"})
        source = {path.name: path.read_bytes() for path in self.legacy.iterdir()}
        self.assertTrue(callable(getattr(self.qa, "migrate_state", None)))
        report = self.qa.migrate_state(self.legacy)
        snapshot = json.loads(self.snapshot.read_text(encoding="utf-8"))
        self.assertEqual(report["revision"], 1)
        self.assertEqual(snapshot["table"], {UID_A: Q1})
        self.assertEqual(snapshot["pool"], {Q1: pool_entry(UID_A)})
        self.assertEqual(snapshot["metadata"]["legacy_comments"], {
            "table": {"_comment": "synthetic table"},
            "pool": {"_comment": "synthetic pool"}})
        self.assertEqual({path.name: path.read_bytes() for path in self.legacy.iterdir()}, source)

    def test_identical_migration_is_idempotent_and_conflicting_destination_is_untouched(self):
        self._write_legacy()
        self.assertTrue(callable(getattr(self.qa, "migrate_state", None)))
        self.qa.migrate_state(self.legacy)
        before = self.snapshot.read_bytes()
        report = self.qa.migrate_state(self.legacy)
        self.assertEqual(report["status"], "unchanged")
        self.assertEqual(self.snapshot.read_bytes(), before)
        (self.legacy / "qimei_table.json").write_text(json.dumps({UID_A: Q2}), encoding="utf-8")
        (self.legacy / "qimei_pool.json").write_text(json.dumps({Q2: pool_entry(UID_A)}), encoding="utf-8")
        with self.assertRaises(self.qa.QimeiPoolError):
            self.qa.migrate_state(self.legacy)
        self.assertEqual(self.snapshot.read_bytes(), before)

    def test_migration_rejects_metadata_it_would_otherwise_discard(self):
        entry = pool_entry(UID_A)
        entry["operator_note"] = "must not be lost"
        self._write_legacy(pool={Q1: entry})
        before = self._inventory()
        self.assertTrue(callable(getattr(self.qa, "migrate_state", None)))
        with self.assertRaises(self.qa.QimeiPoolError):
            self.qa.migrate_state(self.legacy)
        self.assertEqual(self._inventory(), before)

    def test_migration_and_validation_cli_summary_contains_only_status_and_counts(self):
        self._write_legacy()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = self.qa.main(["--migrate-state", "--from-legacy-dir", str(self.legacy),
                                 "--dry-run", "--summary-only"])
        self.assertEqual(code, 0)
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["bindings"], 1)
        self.assertNotIn(UID_A, output.getvalue())
        self.assertNotIn(Q1, output.getvalue())
        self.assertFalse(self.snapshot.parent.exists())
        self._write_snapshot()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = self.qa.main(["--validate-state", "--summary-only"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["revision"], 1)
        self.assertNotIn(UID_A, output.getvalue())
        self.assertNotIn(Q1, output.getvalue())

    def test_summary_only_rejects_ordinary_assignment_before_any_state_side_effect(self):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            with self.assertRaises(SystemExit) as caught:
                self.qa.main(["--assign", UID_A, "--summary-only"])
        self.assertEqual(caught.exception.code, 2)
        self.assertNotIn(UID_A, output.getvalue() + errors.getvalue())
        self.assertEqual(self._inventory(), {})
        self.assertFalse(self.snapshot.parent.exists())

    def test_reconcile_healing_uses_a_writer_transaction_and_preserves_state(self):
        self._write_snapshot(pool={Q1: pool_entry(UID_A, UID_B)})
        real_lock, real_load = self.qa._locked, self.qa.load_state
        held = []

        @contextlib.contextmanager
        def observed_lock(directory):
            with real_lock(directory):
                held.append(True)
                try:
                    yield
                finally:
                    held.pop()

        def transaction_load(*args, **kwargs):
            self.assertTrue(held, "reconcile must load and save under the writer lock")
            return real_load(*args, **kwargs)

        with patch.object(self.qa, "_locked", observed_lock), \
                patch.object(self.qa, "load_state", transaction_load), \
                patch.object(self.qa, "_db_rows", return_value=[(1, UID_A), (2, UID_B)]):
            report = self.qa.reconcile(db_path="synthetic-db", generator=False)
        self.assertTrue(report["ok"])
        committed = json.loads(self.snapshot.read_text(encoding="utf-8"))
        self.assertEqual(committed["table"], {UID_A: Q1, UID_B: Q1})
        self.assertEqual(committed["pool"][Q1]["owners"], [UID_A, UID_B])
        self.assertEqual(committed["revision"], 2)

    def test_snapshot_load_and_save_reject_a_qimei_without_the_required_marker(self):
        bad = "e" * 36
        self._write_snapshot(table={UID_A: bad}, pool={bad: pool_entry(UID_A)})
        with self.assertRaises(self.qa.QimeiPoolError):
            self.qa.load_state()
        self._write_snapshot()
        state = self.qa.load_state()
        state.table = {UID_A: bad}
        state.pool = {bad: pool_entry(UID_A)}
        before = self.snapshot.read_bytes()
        with self.assertRaises(self.qa.QimeiPoolError):
            self.qa.save_state(state)
        self.assertEqual(self.snapshot.read_bytes(), before)

    def test_integer_real_metadata_is_not_silently_converted_to_boolean(self):
        self._write_legacy()
        for value in (0, 1):
            with self.subTest(value=value):
                entry = pool_entry(UID_A)
                entry["real"] = value
                (self.legacy / "qimei_pool.json").write_text(
                    json.dumps({Q1: entry}), encoding="utf-8")
                before = self._inventory()
                with self.assertRaises(self.qa.QimeiPoolError):
                    self.qa.migrate_state(self.legacy)
                self.assertEqual(self._inventory(), before)
                self._write_snapshot(pool={Q1: entry})
                with self.assertRaises(self.qa.QimeiPoolError):
                    self.qa.load_state()
                self.snapshot.unlink()

    def test_failed_summary_operation_logs_no_identity_fragments(self):
        self._write_legacy(pool={Q1: pool_entry(UID_A), Q2: pool_entry(UID_A)})
        output, diagnostic = io.StringIO(), io.StringIO()
        handler = logging.StreamHandler(diagnostic)
        self.qa.logger.addHandler(handler)
        self.addCleanup(self.qa.logger.removeHandler, handler)
        before = self._inventory()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(diagnostic):
            code = self.qa.main(["--migrate-state", "--from-legacy-dir", str(self.legacy),
                                 "--dry-run", "--summary-only"])
        self.assertEqual(code, 3)
        self.assertFalse(json.loads(output.getvalue())["ok"])
        for identity in (UID_A, Q1, Q2, self.qa.mask_uid(UID_A),
                         self.qa.mask_qimei(Q1), self.qa.mask_qimei(Q2)):
            self.assertNotIn(identity, output.getvalue() + diagnostic.getvalue())
        self.assertEqual(self._inventory(), before)


if __name__ == "__main__":
    unittest.main()
