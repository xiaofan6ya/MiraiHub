"""Persistent regression tests for qimei 自动分配（不碰生产 JSON / DB）。

跑法：
    python3 -m unittest tests.test_marvis_qimei_assign
"""
from __future__ import annotations

import errno
import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

def _load_module():
    # Persistent tests load this module without application startup or ORM hooks.
    path = Path(__file__).resolve().parents[1] / "admin/platform/marvis/core/qimei_assign.py"
    spec = importlib.util.spec_from_file_location("qimei_assign_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


qa = _load_module()

SEED_Q = "aaaaaaaaaaaaaaaaaaaaaaaa3000bbbbbbbb"
NEW_Q = "cccccccccccccccccccccccc3000dddddddd"
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
        self.env = patch.dict(os.environ, {"QIMEI_STATE_DIR": str(self.dir),
                                          "QIMEI_TABLE_PATH": "",
                                          "QIMEI_POOL_PATH": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, table: dict, pool: dict | None = None, *, directory=None):
        directory = directory or self.dir
        (directory / "qimei_table.json").write_text(
            json.dumps(table, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if pool is not None:
            (directory / "qimei_pool.json").write_text(
                json.dumps(pool, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _snapshot(self, directory):
        if not directory.exists():
            return None
        return {path.name: path.read_bytes() for path in directory.iterdir()}

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

    def test_cli_assign_no_generate_uses_shareable(self):
        """Persistent regression: --no-generate must use existing capacity."""
        self._write({SEED_UID: SEED_Q},
                    {SEED_Q: {"owners": [SEED_UID], "real": True}})
        output = io.StringIO()
        with patch.object(qa, "generate_real_qimei", return_value=NEW_Q) as generate:
            with contextlib.redirect_stdout(output):
                code = qa.main(["--state-dir", str(self.dir), "--assign", UID_A,
                                "--no-generate", "--json"])
            generate.assert_not_called()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["source"], "shared")
        self.assertEqual(self._table()[UID_A], SEED_Q)
        self.assertEqual(set(self._pool()), {SEED_Q})

    def test_cli_assign_no_generate_rejects_full_pool(self):
        """Persistent regression: disabled generation cannot add capacity."""
        self._write({SEED_UID: SEED_Q, UID_A: SEED_Q},
                    {SEED_Q: {"owners": [SEED_UID, UID_A], "real": True}})
        before = {name: (self.dir / name).read_bytes()
                  for name in ("qimei_table.json", "qimei_pool.json")}
        output = io.StringIO()
        with patch.object(qa, "generate_real_qimei", return_value=NEW_Q) as generate:
            with contextlib.redirect_stdout(output):
                code = qa.main(["--state-dir", str(self.dir), "--assign", UID_B,
                                "--no-generate", "--json"])
            generate.assert_not_called()
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["kind"], "QimeiCapacityError")
        for name, value in before.items():
            self.assertEqual((self.dir / name).read_bytes(), value)

    def _preview_cases(self):
        return (
            ("existing", {SEED_UID: SEED_Q}, {}, SEED_UID, False, "existing", 1),
            ("shareable", {SEED_UID: SEED_Q},
             {SEED_Q: {"owners": [SEED_UID], "real": True}},
             UID_A, False, "shared", 2),
            ("unused", {}, {SEED_Q: {"owners": [], "real": True}},
             UID_A, False, "shared", 1),
            ("new", {}, {}, UID_A, False, None, 0),
            ("derived", {}, {}, UID_A, True, "derived", 0),
            ("absent", None, None, UID_A, False, None, 0),
        )

    def test_direct_assign_dry_run_has_no_side_effects(self):
        """Persistent regression: direct previews never generate, lock or save."""
        for name, table, pool, uid, allow_derived, source, owners in self._preview_cases():
            with self.subTest(case=name):
                directory = self.dir / name
                if table is not None:
                    directory.mkdir()
                    self._write(table, pool, directory=directory)
                before = self._snapshot(directory)
                with patch.object(qa, "generate_real_qimei", return_value=NEW_Q) as generate:
                    with patch.object(qa, "_http_generate", side_effect=AssertionError("HTTP forbidden")) as http:
                        with patch.object(qa, "_cmd_generate", side_effect=AssertionError("command forbidden")) as command:
                            if source is None:
                                with self.assertRaises(qa.QimeiCapacityError):
                                    qa.assign_ex(uid, generator=generate,
                                                 state_dir=directory, dry_run=True)
                            else:
                                result = qa.assign_ex(uid, generator=generate,
                                                      state_dir=directory,
                                                      dry_run=True,
                                                      allow_derived=allow_derived)
                                self.assertEqual(result["source"], source)
                                self.assertEqual(result["owners"], owners)
                                if source != "derived":
                                    self.assertEqual(result["qimei36"], SEED_Q)
                            generate.assert_not_called()
                            http.assert_not_called()
                            command.assert_not_called()
                self.assertEqual(self._snapshot(directory), before)

    def test_cli_assign_dry_run_has_no_side_effects(self):
        """Persistent regression: CLI previews honor dry-run across capacity states."""
        for name, table, pool, uid, allow_derived, source, owners in self._preview_cases():
            with self.subTest(case=name):
                directory = self.dir / name
                if table is not None:
                    directory.mkdir()
                    self._write(table, pool, directory=directory)
                before = self._snapshot(directory)
                argv = ["--state-dir", str(directory), "--assign", uid,
                        "--dry-run", "--json"]
                if allow_derived:
                    argv.append("--allow-derived")
                output = io.StringIO()
                with patch.object(qa, "generate_real_qimei", return_value=NEW_Q) as generate:
                    with contextlib.redirect_stdout(output):
                        code = qa.main(argv)
                    generate.assert_not_called()
                payload = json.loads(output.getvalue())
                self.assertEqual(code, 2 if source is None else 0)
                if source is None:
                    self.assertEqual(payload["kind"], "QimeiCapacityError")
                else:
                    self.assertEqual(payload["source"], source)
                    self.assertEqual(payload["owners"], owners)
                    if source != "derived":
                        self.assertEqual(payload["qimei36"], SEED_Q)
                self.assertEqual(self._snapshot(directory), before)


    def test_owner_limit_only_accepts_one_or_two(self):
        os.environ.pop(qa.OWNER_LIMIT_ENV, None)
        self.assertEqual(qa.owner_limit(), qa.MAX_OWNERS_PER_QIMEI)
        with patch.dict(os.environ, {qa.OWNER_LIMIT_ENV: "1"}):
            self.assertEqual(qa.owner_limit(), 1)
        for bad in ("0", "3", "abc", "-1", "1.5"):
            with patch.dict(os.environ, {qa.OWNER_LIMIT_ENV: bad}):
                with self.assertRaises(qa.QimeiPoolError):
                    qa.owner_limit()
        with patch.dict(os.environ, {qa.OWNER_LIMIT_ENV: "  "}):
            self.assertEqual(qa.owner_limit(), qa.MAX_OWNERS_PER_QIMEI)  # 空白视为未设置

    def test_owner_limit_one_never_shares(self):
        """上限=1：生成失败不得复用 1-owner 设备，直接容量拒绝且不写盘。"""
        self._write({}, {})
        with patch.dict(os.environ, {qa.OWNER_LIMIT_ENV: "1"}):
            a = qa.assign_ex(UID_A, generator=lambda: SEED_Q, state_dir=self.dir)
            self.assertEqual((a["source"], a["owners"]), ("generated", 1))
            before = self._snapshot(self.dir)
            with self.assertRaises(qa.QimeiCapacityError) as ctx:
                qa.assign_ex(UID_B, generator=_fail, state_dir=self.dir)
            self.assertIn("已满 1 号", str(ctx.exception))
            self.assertEqual(self._snapshot(self.dir), before)
        self.assertEqual(self._pool()[SEED_Q]["owners"], [UID_A])
        self.assertNotIn(UID_B, self._table())

    def test_owner_limit_one_rejects_shared_ledger_on_load(self):
        self._write({SEED_UID: SEED_Q, UID_A: SEED_Q},
                    {SEED_Q: {"owners": [SEED_UID, UID_A], "real": True}})
        with patch.dict(os.environ, {qa.OWNER_LIMIT_ENV: "1"}):
            with self.assertRaises(qa.QimeiPoolError) as ctx:
                qa.load_state(self.dir)
        self.assertIn(qa.OWNER_LIMIT_ENV, str(ctx.exception))
        # 默认上限仍允许 2 号/台，未收紧的部署不受影响。
        self.assertEqual(len(qa.load_state(self.dir).pool[SEED_Q]["owners"]), 2)

    def test_owner_limit_one_recycles_idle_device(self):
        """0-owner 的闲置设备在上限=1 下仍可被独占复用，不算共用。"""
        self._write({}, {})
        qa.assign_ex(UID_A, generator=lambda: SEED_Q, state_dir=self.dir)
        qa.release(UID_A, state_dir=self.dir)
        with patch.dict(os.environ, {qa.OWNER_LIMIT_ENV: "1"}):
            b = qa.assign_ex(UID_B, generator=_fail, state_dir=self.dir)
        self.assertEqual((b["source"], b["owners"]), ("shared", 1))
        self.assertEqual(b["qimei36"], SEED_Q)


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
