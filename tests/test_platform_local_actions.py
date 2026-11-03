"""Local Trae actions must keep CN and international credentials separate."""
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin.db import get_db
from admin.security import require_admin
from admin.platform.trae.routers import accounts as routes
from admin.platform.trae.services import accounts


class LocalActionScopeTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(routes.router)
        self.app.dependency_overrides[require_admin] = lambda: True
        self.app.dependency_overrides[get_db] = lambda: object()
        self.client = TestClient(self.app)
        self.records = [{"uid": "cn-demo", "realm": "cn"},
                        {"uid": "intl-demo", "realm": "intl"}]

    def test_scan_returns_only_selected_realm(self):
        for realm in ("cn", "intl", ""):
            with self.subTest(realm=realm), patch.object(
                    accounts, "discover", return_value={"items": self.records.copy()}), \
                    patch.object(accounts, "appdata_hint", return_value="demo"):
                response = self.client.get("/api/trae/scan-local", params={"realm": realm})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["items"],
                                 [r for r in self.records if not realm or r["realm"] == realm])

    def test_import_only_writes_selected_realm(self):
        for realm in ("cn", "intl", ""):
            with self.subTest(realm=realm), patch.object(
                    accounts, "collect_accounts", return_value=self.records.copy()), \
                    patch.object(accounts, "upsert", return_value=(None, True)) as upsert:
                response = self.client.post("/api/trae/import-local", params={"realm": realm})
                self.assertEqual(response.status_code, 200)
                expected = [r for r in self.records if not realm or r["realm"] == realm]
                self.assertEqual([c.args[1] for c in upsert.call_args_list], expected)
                self.assertEqual(response.json()["added"], len(expected))

    def test_no_local_credential_in_selected_realm_does_not_import_other_region(self):
        with patch.object(accounts, "collect_accounts", return_value=[self.records[1]]), \
                patch.object(accounts, "upsert") as upsert:
            response = self.client.post("/api/trae/import-local?realm=cn")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["added"], 0)
            upsert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
