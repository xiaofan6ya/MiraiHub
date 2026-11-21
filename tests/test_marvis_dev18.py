"""Persistent regression: local signing keeps one dev18 per account/QIMEI.

Uses only a private import package and synthetic identities; no app startup,
runtime state, network, or credentials are read.
"""
import importlib
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

CORE = Path(__file__).resolve().parents[1] / "admin/platform/marvis/core"
Q1 = "a" * 24 + "3000" + "b" * 8
Q2 = "c" * 24 + "3000" + "d" * 8


class StableDev18Regression(unittest.TestCase):
    def setUp(self):
        self.name = "_dev18_regression_%x" % id(self)
        pkg = types.ModuleType(self.name)
        pkg.__path__ = [str(CORE)]
        sys.modules[self.name] = pkg
        self.addCleanup(self.cleanup_modules)
        self.u = importlib.import_module(self.name + ".uskey")
        self.local = importlib.import_module(self.name + ".uskey_local")

    def cleanup_modules(self):
        for key in list(sys.modules):
            if key == self.name or key.startswith(self.name + "."):
                sys.modules.pop(key, None)

    def test_stable_across_requests_and_module_reload(self):
        first = self.u.dev18_for("account-a", Q1)
        self.assertEqual(len(first), 18)
        self.assertEqual(self.u.dev18_for("account-a", Q1), first)
        importlib.reload(self.u)
        self.assertEqual(self.u.dev18_for("account-a", Q1), first)

    def test_account_and_explicit_qimei_rotation_are_separate_devices(self):
        first = self.u.dev18_for("account-a", Q1)
        self.assertNotEqual(self.u.dev18_for("account-b", Q1), first)
        self.assertNotEqual(self.u.dev18_for("account-a", Q2), first)

    def test_no_shared_identity_for_an_empty_account(self):
        with self.assertRaises(ValueError):
            self.u.dev18_for("", Q1)

    def test_real_mint_uses_stable_dev18_but_fresh_request_signature(self):
        kwargs = dict(agent_tag="main", conversation_id="conv-a",
                      response_id="rid-a", scene="agent_chat", model="main-auto",
                      nonce="nonce-a", openid="synthetic-openid")
        with patch.object(self.u, "qimei36", return_value=Q1):
            a = self.u._local_sign(device_key="account-a", **kwargs)
            kwargs["response_id"] = "rid-b"
            kwargs["nonce"] = "nonce-b"
            b = self.u._local_sign(device_key="account-a", **kwargs)
        da = self.local.decode_uskey(a["uskey"])
        db = self.local.decode_uskey(b["uskey"])
        self.assertEqual(da["dev18"], db["dev18"])
        self.assertEqual(da["dev18"], self.u.dev18_for("account-a", Q1))
        self.assertNotEqual(a["uskey"], b["uskey"])


if __name__ == "__main__":
    unittest.main()
