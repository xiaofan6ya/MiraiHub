"""Persistent regression tests for opt-in identities; synthetic snapshots only."""
from __future__ import annotations

import copy
import dataclasses
import importlib
import json
import os
import sys
import tempfile
import types
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


CORE = Path(__file__).resolve().parents[1] / "admin/platform/marvis/core"
UID_A, UID_B, UID_C = "A" * 32, "B" * 32, "C" * 32
Q1, Q2, Q3, Q4 = [a * 24 + "3000" + b * 8
                  for a, b in (("a", "b"), ("c", "d"), ("e", "f"), ("1", "2"))]
GUID_A, GUID_B, GUID_NEW = "existing-login-guid-a", "existing-login-guid-b", "new-authenticated-guid-a"
NAMESPACE = "marvis_managed_identity_v1"
STAMP = "2026-01-01T00:00:00Z"


def pool_entry(*owners):
    return {"owners": list(owners), "real": True, "source": "synthetic",
            "created_at": STAMP, "last_rotated": STAMP, "last_used_at": None}


class DeviceIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="managed-identity-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.snapshot = self.root / "state.json"
        environment = patch.dict(os.environ, {
            "QIMEI_MANAGED_IDENTITY": "1", "QIMEI_SNAPSHOT_PATH": str(self.snapshot),
            "QIMEI_STATE_DIR": str(self.root / "unused"),
            "QIMEI_TABLE_PATH": "", "QIMEI_POOL_PATH": "", "MARVIS_QIMEI36": "",
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.package_name = "managed_identity_test_%x" % id(self)
        package = types.ModuleType(self.package_name)
        package.__path__ = [str(CORE)]
        sys.modules[self.package_name] = package
        self.addCleanup(self._unload)
        self.assertTrue((CORE / "device_identity.py").is_file(),
                        "managed device identity resolver is not implemented")
        self.identity = importlib.import_module(self.package_name + ".device_identity")
        self.qa = importlib.import_module(self.package_name + ".qimei_assign")
        self.write_snapshot()

    def _unload(self):
        for name in list(sys.modules):
            if name == self.package_name or name.startswith(self.package_name + "."):
                sys.modules.pop(name, None)

    def write_snapshot(self, value=None):
        value = value or {"schema": 1, "revision": 7,
                          "table": {UID_A: Q1, UID_B: Q2, UID_C: Q3},
                          "pool": {Q1: pool_entry(UID_A), Q2: pool_entry(UID_B),
                                   Q3: pool_entry(UID_C)},
                          "metadata": {"sibling": {"retained": [True, 1, 1.0]}}}
        self.snapshot.write_text(json.dumps(value), encoding="utf-8")

    def read(self):
        return json.loads(self.snapshot.read_text(encoding="utf-8"))

    def inventory(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def acct(self, openid=UID_A, guid=GUID_A, **extra):
        return {"openid": openid, "guid": guid, **extra}

    def enroll(self, uid=UID_A, guid=GUID_A):
        return self.identity.enroll_ex(uid, guid=guid)

    def assert_generic_error(self, action):
        with self.assertRaises(self.identity.DeviceIdentityError) as caught:
            action()
        text = str(caught.exception)
        self.assertTrue(text)
        for sensitive in (UID_A, UID_B, Q1, Q2, GUID_A, GUID_NEW, str(self.snapshot)):
            self.assertNotIn(sensitive, text)

    def test_disabled_resolve_never_reads_snapshot_or_accepts_cached_context(self):
        self.enroll()
        context = self.identity.resolve(self.acct())
        self.snapshot.unlink()
        with patch.dict(os.environ, {"QIMEI_MANAGED_IDENTITY": "0"}):
            self.assertFalse(self.identity.enabled())
            self.assertIsNone(self.identity.resolve({self.identity.CONTEXT_KEY: context}))

    def test_enabled_is_a_boolean_opt_in_and_resolve_requires_snapshot_configuration(self):
        self.assertIs(self.identity.enabled(), True)
        with patch.dict(os.environ, {"QIMEI_SNAPSHOT_PATH": ""}):
            self.assertIs(self.identity.enabled(), True)
            self.assert_generic_error(lambda: self.identity.resolve(self.acct()))

    def test_existing_snapshot_does_not_implicitly_enroll_account(self):
        before = self.inventory()
        self.assert_generic_error(lambda: self.identity.resolve(self.acct()))
        self.assertEqual(self.inventory(), before)

    def test_enroll_preserves_explicit_login_guid_sibling_metadata_and_mapping(self):
        report = self.enroll()
        state = self.read()
        self.assertEqual(report["status"], "enrolled")
        self.assertEqual(state["revision"], 8)
        self.assertEqual(state["table"][UID_A], Q1)
        self.assertEqual(state["metadata"]["sibling"], {"retained": [True, 1, 1.0]})
        context = self.identity.resolve(self.acct())
        self.assertEqual((context.uid, context.qimei36, context.guid, context.generation),
                         (UID_A, Q1, GUID_A, 1))
        self.assertEqual(len(context.dev18), 18)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            context.guid = GUID_NEW

    def test_two_accounts_have_stable_isolated_identity_across_module_reload(self):
        self.enroll()
        self.enroll(UID_B, GUID_B)
        a = self.identity.resolve(self.acct())
        b = self.identity.resolve(self.acct(UID_B, GUID_B))
        self.assertNotEqual(a.qimei36, b.qimei36)
        self.assertNotEqual(a.guid, b.guid)
        self.assertNotEqual(a.dev18, b.dev18)
        self.assertNotEqual(a.conversation_id("same-conversation"), b.conversation_id("same-conversation"))
        a_values = (a.qimei36, a.guid, a.dev18, a.generation, a.conversation_id("same-conversation"))
        reloaded = importlib.reload(self.identity)
        second = reloaded.resolve(self.acct())
        self.assertEqual(a_values, (second.qimei36, second.guid, second.dev18,
                                   second.generation, second.conversation_id("same-conversation")))

    def test_conversation_derivation_preserves_conversation_distinction(self):
        self.enroll()
        context = self.identity.resolve(self.acct())
        first = context.conversation_id("conversation-a")
        self.assertEqual(str(uuid.UUID(first)), first)
        self.assertEqual(first, context.conversation_id("conversation-a"))
        self.assertNotEqual(first, context.conversation_id("conversation-b"))

    def test_account_key_priority_and_normalization_match_assignment_loader(self):
        self.enroll()
        acct = self.acct(UID_B, GUID_A, uid=" " + UID_A.lower() + " ", id=UID_C)
        self.assertEqual(self.identity.resolve(acct).uid, UID_A)
        self.assertEqual(self.identity.resolve({"id": UID_A, "guid": GUID_A}).uid, UID_A)

    def test_resolve_loads_one_snapshot_and_cached_context_does_not_reload(self):
        self.enroll()
        original = self.qa.load_state
        calls = []
        def load(*args, **kwargs):
            calls.append(True)
            return original(*args, **kwargs)
        with patch.object(self.qa, "load_state", side_effect=load):
            context = self.identity.resolve(self.acct())
            self.assertEqual(len(calls), 1)
            self.snapshot.unlink()
            acct = self.acct(**{self.identity.CONTEXT_KEY: context})
            self.assertIs(self.identity.resolve(acct), context)
            self.assertEqual(len(calls), 1)

    def test_cached_context_rejects_changed_account_or_login_guid(self):
        self.enroll()
        context = self.identity.resolve(self.acct())
        for acct in (self.acct(UID_B, GUID_A), self.acct(guid=GUID_NEW)):
            acct[self.identity.CONTEXT_KEY] = context
            self.assert_generic_error(lambda acct=acct: self.identity.resolve(acct))

    def test_valid_credential_device_binding_is_preserved_and_must_match(self):
        self.enroll()
        device = {"guid": GUID_A, "qimei36": Q1, "bound_at": 1, "source": "login"}
        acct = self.acct(device=device)
        original = copy.deepcopy(acct)
        self.assertEqual(self.identity.resolve(acct).guid, GUID_A)
        self.assertEqual(acct, original)
        for mismatch in ({**device, "guid": GUID_NEW}, {**device, "qimei36": Q2}):
            self.assert_generic_error(lambda mismatch=mismatch: self.identity.resolve(self.acct(device=mismatch)))
        self.assertEqual(acct, original)

    def test_shared_global_override_rejects_read_and_write_paths(self):
        self.enroll()
        before = self.inventory()
        with patch.dict(os.environ, {"MARVIS_QIMEI36": Q2}):
            self.assert_generic_error(lambda: self.identity.resolve(self.acct()))
            self.assert_generic_error(lambda: self.identity.enroll_ex(UID_B, guid=GUID_B))
            self.assert_generic_error(lambda: self.identity.reset_ex(UID_A, guid=GUID_NEW, generator=lambda: Q4))
        self.assertEqual(self.inventory(), before)

    def test_missing_and_corrupt_snapshot_errors_are_generic_and_create_no_state(self):
        self.snapshot.unlink()
        before = self.inventory()
        self.assert_generic_error(lambda: self.identity.resolve(self.acct()))
        self.assertEqual(self.inventory(), before)
        self.snapshot.write_text("{", encoding="utf-8")
        before = self.inventory()
        self.assert_generic_error(lambda: self.identity.resolve(self.acct()))
        self.assertEqual(self.inventory(), before)

    def test_enroll_requires_existing_sole_owner_real_mapping(self):
        original = self.read()
        variants = []
        missing = copy.deepcopy(original)
        missing["table"].pop(UID_A)
        missing["pool"][Q1]["owners"] = []
        variants.append(missing)
        shared = copy.deepcopy(original)
        shared["table"][UID_C] = Q1
        shared["pool"][Q3]["owners"] = []
        shared["pool"][Q1]["owners"].append(UID_C)
        variants.append(shared)
        derived = copy.deepcopy(original)
        derived["pool"][Q1]["real"] = False
        variants.append(derived)
        for value in variants:
            with self.subTest(value=value):
                self.write_snapshot(value)
                before = self.snapshot.read_bytes()
                self.assert_generic_error(lambda: self.enroll())
                self.assertEqual(self.snapshot.read_bytes(), before)

    def test_enroll_explicit_qimei_must_equal_existing_mapping(self):
        before = self.snapshot.read_bytes()
        self.assert_generic_error(lambda: self.identity.enroll_ex(UID_A, guid=GUID_A, qimei36=Q2))
        self.assertEqual(self.snapshot.read_bytes(), before)

    def test_duplicate_guid_and_invalid_guid_are_rejected_without_snapshot_write(self):
        self.enroll()
        before = self.snapshot.read_bytes()
        for guid in (GUID_A, GUID_A.upper(), "unknown", "short", True, None, "x" * 65):
            with self.subTest(guid=guid):
                self.assert_generic_error(lambda guid=guid: self.identity.enroll_ex(UID_B, guid=guid))
                self.assertEqual(self.snapshot.read_bytes(), before)

    def test_enroll_is_idempotent_and_cannot_replace_existing_guid(self):
        self.enroll()
        before = self.snapshot.read_bytes()
        self.assertEqual(self.enroll()["status"], "unchanged")
        self.assertEqual(self.snapshot.read_bytes(), before)
        self.assert_generic_error(lambda: self.identity.enroll_ex(UID_A, guid=GUID_NEW))
        self.assertEqual(self.snapshot.read_bytes(), before)

    def test_dry_run_does_not_create_locks_generate_entropy_or_call_generator(self):
        before = self.inventory()
        with patch("secrets.token_hex", side_effect=AssertionError("no entropy in preview")):
            self.assertEqual(self.identity.enroll_ex(UID_A, guid=GUID_A, dry_run=True)["status"], "ready")
        self.assertEqual(self.inventory(), before)
        self.enroll()
        before = self.inventory()
        with patch("secrets.token_hex", side_effect=AssertionError("no entropy in preview")):
            result = self.identity.reset_ex(UID_A, guid=GUID_NEW,
                                           generator=lambda: self.fail("no generation in preview"), dry_run=True)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(self.inventory(), before)

    def test_reset_atomically_updates_one_account_and_requires_new_login_guid(self):
        self.enroll()
        self.enroll(UID_B, GUID_B)
        a = self.identity.resolve(self.acct())
        b = self.identity.resolve(self.acct(UID_B, GUID_B))
        previous = self.read()
        writes = []
        save = self.qa.save_state
        def saving(state):
            writes.append(copy.deepcopy((state.table, state.pool, state.metadata)))
            return save(state)
        with patch.object(self.qa, "save_state", side_effect=saving):
            report = self.identity.reset_ex(UID_A, guid=GUID_NEW, generator=lambda: Q4)
        current = self.read()
        self.assertEqual(len(writes), 1)
        self.assertEqual(current["revision"], previous["revision"] + 1)
        self.assertEqual(report["status"], "reset")
        self.assertEqual(current["table"], {UID_A: Q4, UID_B: Q2, UID_C: Q3})
        self.assertEqual(current["pool"][Q1]["owners"], [])
        self.assertEqual(current["pool"][Q4]["owners"], [UID_A])
        self.assertEqual(current["metadata"]["sibling"], previous["metadata"]["sibling"])
        self.assertEqual(current["metadata"][NAMESPACE]["accounts"][UID_B],
                         previous["metadata"][NAMESPACE]["accounts"][UID_B])
        self.assert_generic_error(lambda: self.identity.resolve(self.acct()))
        new = self.identity.resolve(self.acct(guid=GUID_NEW))
        self.assertEqual((new.guid, new.qimei36, new.generation), (GUID_NEW, Q4, 2))
        self.assertNotEqual(a.dev18, new.dev18)
        self.assertNotEqual(a.conversation_id("conversation"), new.conversation_id("conversation"))
        self.assertEqual(b, self.identity.resolve(self.acct(UID_B, GUID_B)))
        old_request = self.acct(**{self.identity.CONTEXT_KEY: a})
        self.assertIs(self.identity.resolve(old_request), a)

    def test_reset_rejects_same_or_duplicate_guid_before_generation(self):
        self.enroll()
        self.enroll(UID_B, GUID_B)
        before = self.snapshot.read_bytes()
        for guid in (GUID_A, GUID_A.upper(), GUID_B, "unknown"):
            with self.subTest(guid=guid):
                self.assert_generic_error(lambda guid=guid: self.identity.reset_ex(
                    UID_A, guid=guid, generator=lambda: self.fail("must validate GUID first")))
                self.assertEqual(self.snapshot.read_bytes(), before)

    def test_reset_failure_invalid_value_and_collision_do_not_write(self):
        self.enroll()
        before = self.snapshot.read_bytes()
        def fail():
            raise RuntimeError("synthetic external generator failure " + UID_A)
        for generator in (fail, lambda: Q1, lambda: Q2, lambda: "bad", lambda: True, False):
            with self.subTest(generator=generator):
                self.assert_generic_error(lambda generator=generator: self.identity.reset_ex(
                    UID_A, guid=GUID_NEW, generator=generator))
                self.assertEqual(self.snapshot.read_bytes(), before)

    def test_missing_managed_record_cannot_be_reset(self):
        before = self.snapshot.read_bytes()
        self.assert_generic_error(lambda: self.identity.reset_ex(UID_A, guid=GUID_NEW,
                                                                 generator=lambda: Q4))
        self.assertEqual(self.snapshot.read_bytes(), before)

    def test_qimei_only_rotation_is_rejected_before_mutating_managed_state(self):
        self.enroll()
        before = self.snapshot.read_bytes()
        with self.assertRaises(self.qa.QimeiPoolError):
            self.qa.rotate_ex(UID_A, generator=lambda: Q4)
        self.assertEqual(self.identity.resolve(self.acct()).qimei36, Q1)
        self.assertEqual(self.snapshot.read_bytes(), before)

    def test_namespace_corruption_is_rejected_without_writing(self):
        self.enroll()
        original = self.read()
        record = original["metadata"][NAMESPACE]["accounts"][UID_A]
        variants = [None, [], {}, {"schema": True, "accounts": {}},
                    {"schema": 2, "accounts": {}}, {"schema": 1, "accounts": []},
                    {"schema": 1, "accounts": {}, "unknown": True}]
        for field, value in (("seed", "bad"), ("seed", "A" * 64), ("generation", True),
                             ("generation", 0), ("generation", 1.0), ("guid", "short"),
                             ("qimei36", Q2)):
            changed = {**record, field: value}
            variants.append({"schema": 1, "accounts": {UID_A: changed}})
        variants.append({"schema": 1, "accounts": {UID_A.lower(): record}})
        variants.append({"schema": 1, "accounts": {UID_A: {**record, "unknown": 1}}})
        for namespace in variants:
            with self.subTest(namespace=namespace):
                state = copy.deepcopy(original)
                state["metadata"][NAMESPACE] = namespace
                self.write_snapshot(state)
                before = self.snapshot.read_bytes()
                self.assert_generic_error(lambda: self.identity.resolve(self.acct()))
                self.assert_generic_error(lambda: self.identity.enroll_ex(UID_B, guid=GUID_B))
                self.assert_generic_error(lambda: self.identity.reset_ex(UID_A, guid=GUID_NEW,
                                                                         generator=lambda: Q4))
                self.assertEqual(self.snapshot.read_bytes(), before)

    def test_duplicate_metadata_guid_is_rejected_for_entire_namespace(self):
        self.enroll()
        self.enroll(UID_B, GUID_B)
        state = self.read()
        state["metadata"][NAMESPACE]["accounts"][UID_B]["guid"] = GUID_A
        self.write_snapshot(state)
        self.assert_generic_error(lambda: self.identity.resolve(self.acct()))

    def test_other_account_writes_do_not_change_captured_identity_epoch(self):
        self.enroll()
        context = self.identity.resolve(self.acct())
        self.enroll(UID_B, GUID_B)
        self.assertEqual(context, self.identity.resolve(self.acct()))
        self.assertEqual(context.conversation_id("thread"),
                         self.identity.resolve(self.acct()).conversation_id("thread"))

    def test_reset_rejects_old_device_binding_even_with_updated_top_level_guid(self):
        self.enroll()
        self.identity.reset_ex(UID_A, guid=GUID_NEW, generator=lambda: Q4)
        device = {"guid": GUID_A, "qimei36": Q1, "source": "login", "bound_at": 1}
        self.assert_generic_error(lambda: self.identity.resolve(self.acct(guid=GUID_NEW, device=device)))

    def test_disabled_write_apis_are_not_implicit_enrollment_switches(self):
        before = self.inventory()
        with patch.dict(os.environ, {"QIMEI_MANAGED_IDENTITY": "0"}):
            self.assert_generic_error(lambda: self.enroll())
            self.assert_generic_error(lambda: self.identity.reset_ex(UID_A, guid=GUID_NEW,
                                                                     generator=lambda: Q4))
        self.assertEqual(self.inventory(), before)

    def test_legacy_file_overrides_cannot_bypass_explicit_snapshot(self):
        before = self.inventory()
        with patch.dict(os.environ, {"QIMEI_TABLE_PATH": str(self.root / "not-read.json")}):
            self.assert_generic_error(lambda: self.identity.resolve(self.acct()))
            self.assert_generic_error(lambda: self.enroll())
        self.assertEqual(self.inventory(), before)

    def test_context_rejects_nontext_conversation_key_without_identity_in_error(self):
        self.enroll()
        context = self.identity.resolve(self.acct())
        self.assert_generic_error(lambda: context.conversation_id({"sensitive": UID_A}))
        for sensitive in (UID_A, Q1, GUID_A, context.dev18.hex()):
            self.assertNotIn(sensitive, repr(context))

    def test_precommit_replace_failure_preserves_snapshot_and_cleans_temporary_file(self):
        self.enroll()
        before = self.inventory()
        backend = self.qa._snapshot_support()
        with patch.object(backend, "_replace_snapshot", side_effect=OSError(13, "synthetic denial " + UID_A)):
            self.assert_generic_error(lambda: self.identity.reset_ex(UID_A, guid=GUID_NEW,
                                                                     generator=lambda: Q4))
        self.assertEqual(self.inventory(), before)

    def test_stale_cas_preserves_competing_commit_without_partial_reset(self):
        self.enroll()
        previous = self.read()
        save = self.qa.save_state
        def concurrent_save(stale):
            competing = self.qa.load_state()
            competing.metadata["concurrent"] = {"preserved": True}
            save(competing)
            save(stale)
        with patch.object(self.qa, "save_state", side_effect=concurrent_save):
            self.assert_generic_error(lambda: self.identity.reset_ex(UID_A, guid=GUID_NEW,
                                                                     generator=lambda: Q4))
        state = self.read()
        self.assertEqual(state["revision"], previous["revision"] + 1)
        self.assertEqual(state["table"], previous["table"])
        self.assertEqual(state["pool"], previous["pool"])
        self.assertEqual(state["metadata"][NAMESPACE], previous["metadata"][NAMESPACE])
        self.assertEqual(state["metadata"]["concurrent"], {"preserved": True})

    def test_invalid_or_missing_credential_guid_and_private_context_fail_closed(self):
        self.enroll()
        for acct in ({"openid": UID_A}, self.acct(guid="unknown"),
                     self.acct(device={"guid": GUID_A}),
                     self.acct(**{self.identity.CONTEXT_KEY: {"uid": UID_A}})):
            with self.subTest(acct=acct):
                self.assert_generic_error(lambda acct=acct: self.identity.resolve(acct))

    # Persistent regressions for bounded request caching and linear validation.
    def test_warm_requests_reuse_validated_immutable_contexts_without_account_crossover(self):
        self.enroll()
        self.enroll(UID_B, GUID_B)
        with patch.object(self.identity, "_records", wraps=self.identity._records) as validate:
            a = self.identity.resolve(self.acct())
            b = self.identity.resolve(self.acct(UID_B, GUID_B))
            self.assertIs(self.identity.resolve(self.acct()), a)
            self.assertIs(self.identity.resolve(self.acct(UID_B, GUID_B)), b)
            self.assertEqual(validate.call_count, 1)
        self.assertEqual((a.uid, a.qimei36, a.guid), (UID_A, Q1, GUID_A))
        self.assertEqual((b.uid, b.qimei36, b.guid), (UID_B, Q2, GUID_B))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            a.qimei36 = Q2
        self.assertEqual(self.identity.resolve(self.acct()).qimei36, Q1)

    def test_namespace_validation_visits_binding_values_only_once(self):
        self.enroll()
        self.enroll(UID_B, GUID_B)
        self.enroll(UID_C, "existing-login-guid-c")
        state = self.qa.load_state()

        class CountedTable(dict):
            visited = 0

            def values(self):
                for value in super().values():
                    self.visited += 1
                    yield value

        state.table = CountedTable(state.table)
        self.assertEqual(set(self.identity._records(state)), {UID_A, UID_B, UID_C})
        self.assertLessEqual(state.table.visited, len(state.table))

    def test_reset_invalidates_warm_contexts_and_rejects_the_old_login_guid(self):
        self.enroll()
        self.enroll(UID_B, GUID_B)
        a = self.identity.resolve(self.acct())
        b = self.identity.resolve(self.acct(UID_B, GUID_B))
        self.identity.reset_ex(UID_A, guid=GUID_NEW, generator=lambda: Q4)
        self.assert_generic_error(lambda: self.identity.resolve(self.acct()))
        updated = self.identity.resolve(self.acct(guid=GUID_NEW))
        self.assertEqual((updated.qimei36, updated.guid, updated.generation), (Q4, GUID_NEW, 2))
        self.assertNotEqual(updated.dev18, a.dev18)
        self.assertEqual(self.identity.resolve(self.acct(UID_B, GUID_B)), b)

    def test_release_invalidates_warm_context_without_breaking_another_account(self):
        self.enroll()
        self.enroll(UID_B, GUID_B)
        self.identity.resolve(self.acct())
        b = self.identity.resolve(self.acct(UID_B, GUID_B))
        self.qa.release(UID_A)
        self.assert_generic_error(lambda: self.identity.resolve(self.acct()))
        self.assertEqual(self.identity.resolve(self.acct(UID_B, GUID_B)), b)

    def test_warm_cache_revalidates_the_entire_namespace_after_corruption_and_repair(self):
        self.enroll()
        self.enroll(UID_B, GUID_B)
        original = self.read()
        old = self.identity.resolve(self.acct())
        corrupted = copy.deepcopy(original)
        corrupted["metadata"][NAMESPACE]["accounts"][UID_B]["seed"] = "bad"
        self.write_snapshot(corrupted)
        self.assert_generic_error(lambda: self.identity.resolve(self.acct()))
        self.write_snapshot(original)
        self.assertEqual(self.identity.resolve(self.acct()), old)

    def test_warm_cache_rejects_missing_snapshot_and_observes_recreated_state(self):
        self.enroll()
        original = self.read()
        self.identity.resolve(self.acct())
        self.snapshot.unlink()
        self.assert_generic_error(lambda: self.identity.resolve(self.acct()))
        original["metadata"][NAMESPACE]["accounts"][UID_A]["generation"] = 2
        self.write_snapshot(original)
        self.assertEqual(self.identity.resolve(self.acct()).generation, 2)

    def test_warm_cache_observes_snapshot_path_changes_and_checks_mode_each_time(self):
        self.enroll()
        self.identity.resolve(self.acct())
        alternate = self.root / "alternate.json"
        state = self.read()
        state["table"][UID_A] = Q4
        state["pool"][Q4] = state["pool"].pop(Q1)
        state["metadata"][NAMESPACE]["accounts"][UID_A]["qimei36"] = Q4
        alternate.write_text(json.dumps(state), encoding="utf-8")
        with patch.dict(os.environ, {"QIMEI_SNAPSHOT_PATH": str(alternate)}):
            self.assertEqual(self.identity.resolve(self.acct()).qimei36, Q4)
        self.assertEqual(self.identity.resolve(self.acct()).qimei36, Q1)
        with patch.dict(os.environ, {"MARVIS_QIMEI36": Q1}):
            self.assert_generic_error(lambda: self.identity.resolve(self.acct()))

    def test_concurrent_cold_requests_publish_only_their_own_account_context(self):
        self.enroll()
        self.enroll(UID_B, GUID_B)
        credentials = [self.acct(), self.acct(UID_B, GUID_B)] * 8
        with patch.object(self.identity, "_records", wraps=self.identity._records) as validate:
            with ThreadPoolExecutor(max_workers=4) as executor:
                contexts = list(executor.map(self.identity.resolve, credentials))
            self.assertEqual(validate.call_count, 1)
        for index, context in enumerate(contexts):
            expected = (UID_A, Q1, GUID_A) if index % 2 == 0 else (UID_B, Q2, GUID_B)
            self.assertEqual((context.uid, context.qimei36, context.guid), expected)
            self.assertIs(context, contexts[index % 2])


if __name__ == "__main__":
    unittest.main()
