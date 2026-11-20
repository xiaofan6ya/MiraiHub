"""qimei 自动分配的单测（不碰生产 JSON / DB）。

跑法：
    python3 -m unittest tests.test_marvis_qimei_assign
"""
from __future__ import annotations

import errno
import importlib.util
import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path

def _load_module():
    try:
        from admin.platform.marvis.core import qimei_assign as module
        return module
    except Exception:  # 宿主没装 sqlalchemy 时按文件路径加载，保持单测可跑
        here = Path(__file__).resolve()
        for candidate in (here.with_name("qimei_assign.py"),
                          here.parents[1] / "admin" / "platform" / "marvis" / "core" / "qimei_assign.py"):
            if candidate.exists():
                spec = importlib.util.spec_from_file_location(
                    "qimei_assign_under_test", candidate)
                module = importlib.util.module_from_spec(spec)
                assert spec.loader is not None
                spec.loader.exec_module(module)
                return module
        raise


qa = _load_module()

SEED_Q = "aaaaaaaaaaaaaaaaaaaaaaaa3000bbbbbbbb"
SEED_UID = "1" * 32
UID_A = "A" * 32
UID_B = "B" * 32
UID_C = "C" * 32


def _fail():
    raise RuntimeError("generator unavailable")


class QimeiAssignTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="qimei-test-")
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, table: dict, pool: dict | None = None):
        (self.dir / "qimei_table.json").write_text(
            json.dumps(table, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if pool is not None:
            (self.dir / "qimei_pool.json").write_text(
                json.dumps(pool, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _pool(self):
        return json.loads((self.dir / "qimei_pool.json").read_text("utf-8"))

    def _table(self):
        return json.loads((self.dir / "qimei_table.json").read_text("utf-8"))

    def test_generated_shared_then_capacity_rejected(self):
        """空池：A 生成、B 共用同一台（owners=2）、C 被拒。"""
        self._write({}, {})
        a = qa.assign_ex(UID_A, generator=lambda: SEED_Q, state_dir=self.dir)
        self.assertEqual(a["source"], "generated")
        self.assertEqual(a["owners"], 1)

        b = qa.assign_ex(UID_B, generator=_fail, state_dir=self.dir)
        self.assertEqual(b["source"], "shared")
        self.assertEqual(b["qimei36"], SEED_Q)
        self.assertEqual(b["owners"], 2)

        with self.assertRaises(qa.QimeiCapacityError) as ctx:
            qa.assign_ex(UID_C, generator=_fail, state_dir=self.dir)
        self.assertIn("已满 2 号", str(ctx.exception))
        pool = self._pool()
        self.assertEqual(len(pool[SEED_Q]["owners"]), 2)
        self.assertNotIn(UID_C, self._table())

    def test_load_rejects_three_owners(self):
        self._write({SEED_UID: SEED_Q},
                    {SEED_Q: {"owners": [SEED_UID, UID_B, UID_C],
                              "real": True}})
        with self.assertRaises(qa.QimeiPoolError) as ctx:
            qa.load_state(self.dir)
        self.assertIn("owners=3", str(ctx.exception))
        self.assertIn("硬上限 2", str(ctx.exception))

    def test_cli_exits_3_on_bad_pool(self):
        self._write({SEED_UID: SEED_Q},
                    {SEED_Q: {"owners": [SEED_UID, UID_B, UID_C],
                              "real": True}})
        code = qa.main(["--state-dir", str(self.dir), "--sync-pool"])
        self.assertEqual(code, 3)

    def test_release_frees_slot(self):
        self._write({}, {})
        qa.assign_ex(UID_A, generator=lambda: SEED_Q, state_dir=self.dir)
        qa.assign_ex(UID_B, generator=_fail, state_dir=self.dir)
        released = qa.release(UID_A, state_dir=self.dir)
        self.assertEqual(released["owners"], 1)
        c = qa.assign_ex(UID_C, generator=_fail, state_dir=self.dir)
        self.assertEqual(c["source"], "shared")
        self.assertEqual(c["owners"], 2)

    @unittest.skipIf(os.name == "nt", "Windows 没有 POSIX 权限位")
    def test_format_and_permissions(self):
        self._write({}, {})
        qa.assign_ex(UID_A, generator=lambda: SEED_Q, state_dir=self.dir)
        for name in ("qimei_table.json", "qimei_pool.json"):
            path = self.dir / name
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            text = path.read_text("utf-8")
            self.assertTrue(text.endswith("\n"))
            self.assertIn('\n  "', text)  # indent=2

    def test_reconcile_dry_run_is_read_only(self):
        self._write({SEED_UID: SEED_Q},
                    {SEED_Q: {"owners": [SEED_UID], "real": True}})
        db = self.dir / "workbuddy.db"
        con = sqlite3.connect(db)
        con.execute("create table accounts (id integer primary key, platform text,"
                    " uid text, status text)")
        con.execute("insert into accounts values (1, 'marvis', ?, 'active')",
                    (SEED_UID,))
        con.execute("insert into accounts values (2, 'marvis', ?, 'active')",
                    (UID_A,))
        con.commit()
        con.close()
        before = (self.dir / "qimei_table.json").read_bytes()
        report = qa.reconcile(db_path=str(db), dry_run=True, state_dir=self.dir)
        self.assertEqual(len(report["missing"]), 1)
        self.assertEqual((self.dir / "qimei_table.json").read_bytes(), before)
        self.assertEqual(len(self._pool()[SEED_Q]["owners"]), 1)

    @unittest.skipIf(os.name == "nt", "Windows 没有 POSIX 权限位")
    def test_write_survives_bind_mounted_file(self):
        """容器形态（单文件 bind mount）：rename 报 EBUSY 时必须退回就地写。"""
        self._write({}, {})
        real = qa._REPLACE

        def _busy(src, dst):
            raise OSError(errno.EBUSY, "Device or resource busy", str(dst))

        qa._REPLACE = _busy
        try:
            result = qa.assign_ex(UID_A, generator=lambda: SEED_Q, state_dir=self.dir)
        finally:
            qa._REPLACE = real
        self.assertEqual(result["qimei36"], SEED_Q)
        self.assertEqual(self._table()[UID_A], SEED_Q)
        self.assertEqual(self._pool()[SEED_Q]["owners"], [UID_A])
        for name in ("qimei_table.json", "qimei_pool.json"):
            mode = stat.S_IMODE((self.dir / name).stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_existing_uid_is_idempotent(self):
        self._write({SEED_UID: SEED_Q},
                    {SEED_Q: {"owners": [SEED_UID], "real": True}})
        result = qa.assign_ex(SEED_UID, generator=_fail, state_dir=self.dir)
        self.assertEqual(result["source"], "existing")
        self.assertEqual(result["qimei36"], SEED_Q)
        self.assertEqual(self._table()[SEED_UID], SEED_Q)


class ShippedStateTest(unittest.TestCase):
    """仓库里**不带真值**：空表空池必须能跑通，示例文件不能被当成真表读进来。"""

    def test_repo_ships_only_examples(self):
        core = Path(qa.__file__).resolve().parent
        self.assertFalse((core / "qimei_table.json").exists())
        self.assertFalse((core / "qimei_pool.json").exists())
        self.assertTrue((core / "qimei_table.example.json").exists())
        self.assertTrue((core / "qimei_pool.example.json").exists())

    def test_empty_state_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = qa.load_state(Path(tmp))
            self.assertEqual(state.table, {})
            self.assertEqual(state.pool, {})

    def test_metadata_keys_are_not_devices(self):
        """示例文件里的 _comment / _example 不能变成一台"可分配的设备"。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "qimei_pool.json").write_text(
                json.dumps({"_comment": "note", "_example": {"k": {}}}),
                encoding="utf-8")
            (d / "qimei_table.json").write_text(
                json.dumps({"_comment": "note"}), encoding="utf-8")
            state = qa.load_state(d)
            self.assertEqual(state.pool, {})
            self.assertEqual(state.table, {})

    def test_example_files_are_metadata_only(self):
        core = Path(qa.__file__).resolve().parent
        for name in ("qimei_table.example.json", "qimei_pool.example.json"):
            data = json.loads((core / name).read_text("utf-8"))
            self.assertIsInstance(data, dict)
            for key in data:
                self.assertTrue(key.startswith("_"), "%s: %r" % (name, key))

    def test_derived_value_is_structurally_valid_and_stable(self):
        value = qa.derived_qimei("A" * 32)
        self.assertTrue(qa.QIMEI_RE.match(value), value)
        self.assertEqual(value, qa.derived_qimei("A" * 32))
        self.assertNotEqual(value, qa.derived_qimei("B" * 32))


if __name__ == "__main__":
    unittest.main()
