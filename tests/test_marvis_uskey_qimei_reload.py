"""Persistent regression tests for live QIMEI lookup reloads; synthetic state only."""
from __future__ import annotations

import importlib.util
import json
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
UID_C = "C" * 32
FIRST_Q = "aaaaaaaaaaaaaaaaaaaaaaaa3000bbbbbbbb"
ROTATED_Q = "cccccccccccccccccccccccc3000dddddddd"
DERIVED_A = "ae597dd5818b91dbc88c37fc300014af4530"


class QimeiLookupReloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="qimei-reload-test-")
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.environment = patch.dict(os.environ, {
            "QIMEI_STATE_DIR": str(self.directory),
            "QIMEI_TABLE_PATH": "",
            "QIMEI_POOL_PATH": "",
            "MARVIS_QIMEI_DEVICE_TAG": "qimei-regression:",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.package_name = "qimei_reload_%x" % id(self)
        package = types.ModuleType(self.package_name)
        package.__path__ = [str(CORE)]
        sys.modules[self.package_name] = package
        self.addCleanup(sys.modules.pop, self.package_name, None)
        self.qa = self._load("qimei_assign")
        self.uskey = self._load("uskey")

    def _load(self, filename, *, direct=False):
        name = ("direct_uskey_%x" % id(self) if direct
                else self.package_name + "." + filename)
        spec = importlib.util.spec_from_file_location(name, CORE / (filename + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        self.addCleanup(sys.modules.pop, name, None)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def _write(self, table, pool=None, *, directory=None):
        directory = directory or self.directory
        (directory / "qimei_table.json").write_text(json.dumps(table), encoding="utf-8")
        if pool is not None:
            (directory / "qimei_pool.json").write_text(json.dumps(pool), encoding="utf-8")

    def test_table_created_after_derived_lookup_is_observed(self):
        self.assertEqual(self.uskey.qimei_for(UID_A), DERIVED_A)
        self._write({" " + UID_A.lower() + " ": FIRST_Q})
        self.assertEqual(self.uskey.qimei_for(" " + UID_A.lower() + " "), FIRST_Q)

    def test_table_rotation_after_successful_lookup_is_observed(self):
        self._write({UID_A: FIRST_Q})
        self.assertEqual(self.uskey.qimei_for(UID_A), FIRST_Q)
        self._write({UID_A: ROTATED_Q})
        self.assertEqual(self.uskey.qimei_for(UID_A), ROTATED_Q)

    def test_unchanged_legacy_lookups_reuse_validation_until_either_file_changes(self):
        self._write({UID_A: FIRST_Q}, {FIRST_Q: {"owners": [UID_A], "real": True}})
        with patch.object(self.qa, "load_state", wraps=self.qa.load_state) as load:
            self.assertEqual(self.uskey.qimei_for(UID_A), FIRST_Q)
            self.assertEqual(self.uskey.qimei_for(UID_A), FIRST_Q)
            self.assertEqual(load.call_count, 1)
            self._write({UID_A: FIRST_Q},
                        {FIRST_Q: {"owners": [UID_A, UID_B, UID_C], "real": True}})
            with self.assertRaises(self.qa.QimeiPoolError):
                self.uskey.qimei_for(UID_A)
            self.assertEqual(load.call_count, 2)

    def test_equal_sized_in_place_rotation_with_unchanged_stat_is_observed(self):
        self._write({UID_A: FIRST_Q}, {FIRST_Q: {"owners": [UID_A], "real": True}})
        paths = [self.directory / "qimei_table.json", self.directory / "qimei_pool.json"]
        versions = {path: path.stat() for path in paths}
        original_stat = Path.stat

        def stat(path, *args, **kwargs):
            return versions[path] if path in versions else original_stat(path, *args, **kwargs)

        with patch.object(Path, "stat", new=stat):
            self.assertEqual(self.uskey.qimei_for(UID_A), FIRST_Q)
            self._write({UID_A: ROTATED_Q})
            self.assertEqual(self.uskey.qimei_for(UID_A), ROTATED_Q)

    def test_release_after_successful_lookup_restores_derived_fallback(self):
        self._write({UID_A: FIRST_Q}, {FIRST_Q: {"owners": [UID_A], "real": True}})
        self.assertEqual(self.uskey.qimei_for(UID_A), FIRST_Q)
        released = self.qa.release(UID_A, state_dir=self.directory)
        self.assertTrue(released["released"])
        self.assertEqual(self.uskey.qimei_for(UID_A), DERIVED_A)

    def test_table_deletion_after_successful_lookup_is_observed(self):
        self._write({UID_A: FIRST_Q}, {})
        self.assertEqual(self.uskey.qimei_for(UID_A), FIRST_Q)
        (self.directory / "qimei_table.json").unlink()
        self.assertEqual(self.uskey.qimei_for(UID_A), DERIVED_A)
        self.assertFalse((self.directory / "qimei_table.json").exists())

    def test_state_directory_environment_change_is_observed(self):
        self._write({UID_A: FIRST_Q})
        other = self.directory / "other"
        other.mkdir()
        self._write({UID_A: ROTATED_Q}, directory=other)
        self.assertEqual(self.uskey.qimei_for(UID_A), FIRST_Q)
        with patch.dict(os.environ, {"QIMEI_STATE_DIR": str(other)}):
            self.assertEqual(self.uskey.qimei_for(UID_A), ROTATED_Q)
        self.assertEqual(self.uskey.qimei_for(UID_A), FIRST_Q)

    def test_table_path_environment_change_is_observed(self):
        self._write({UID_A: FIRST_Q})
        other = self.directory / "alternate-table.json"
        other.write_text(json.dumps({UID_A: ROTATED_Q}), encoding="utf-8")
        self.assertEqual(self.uskey.qimei_for(UID_A), FIRST_Q)
        with patch.dict(os.environ, {"QIMEI_TABLE_PATH": str(other)}):
            self.assertEqual(self.uskey.qimei_for(UID_A), ROTATED_Q)
        self.assertEqual(self.uskey.qimei_for(UID_A), FIRST_Q)

    def test_new_invalid_pool_after_successful_lookup_propagates(self):
        self._write({UID_A: FIRST_Q}, {FIRST_Q: {"owners": [UID_A], "real": True}})
        self.assertEqual(self.uskey.qimei_for(UID_A), FIRST_Q)
        self._write({UID_A: FIRST_Q},
                    {FIRST_Q: {"owners": [UID_A, UID_B, UID_C], "real": True}})
        with self.assertRaises(self.qa.QimeiPoolError):
            self.uskey.qimei_for(UID_A)

    def test_pool_path_environment_change_revalidates_ownership(self):
        self._write({UID_A: FIRST_Q}, {})
        other = self.directory / "invalid-pool.json"
        other.write_text(json.dumps({FIRST_Q: {"owners": [UID_A, UID_B, UID_C],
                                              "real": True}}), encoding="utf-8")
        self.assertEqual(self.uskey.qimei_for(UID_A), FIRST_Q)
        with patch.dict(os.environ, {"QIMEI_POOL_PATH": str(other)}):
            with self.assertRaises(self.qa.QimeiPoolError):
                self.uskey.qimei_for(UID_A)

    def test_direct_file_import_fallback_reloads_table(self):
        direct = self._load("uskey", direct=True)
        table = self.directory / "qimei_table.json"
        with patch.dict(os.environ, {"QIMEI_TABLE_PATH": str(table)}):
            self._write({UID_A: FIRST_Q})
            self.assertEqual(direct.qimei_for(UID_A), FIRST_Q)
            self._write({UID_A: ROTATED_Q})
            self.assertEqual(direct.qimei_for(UID_A), ROTATED_Q)
            table.unlink()
            self.assertEqual(direct.qimei_for(UID_A), DERIVED_A)


if __name__ == "__main__":
    unittest.main()
