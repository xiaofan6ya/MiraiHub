# -*- coding: utf-8 -*-
"""账号列表服务端分页/搜索/筛选 + 导出分片 的接口级验证（内存 SQLite）。"""
import sys
import time
import unittest
from datetime import datetime
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, r"E:\反代理\wb2api")

from admin.db import get_db  # noqa: E402
from admin.models import Base, Account  # noqa: E402
from admin.security import require_admin  # noqa: E402


def make_app():
    from admin.routers import accounts as generic_routes
    from admin.platform.loomy.routers import accounts as loomy_routes
    from admin.platform.trae.routers import accounts as trae_routes
    from admin.platform.qoder.routers import accounts as qoder_routes

    app = FastAPI()
    app.include_router(generic_routes.router)
    app.include_router(loomY := loomy_routes.router)
    app.include_router(trae_routes.router)
    app.include_router(qoder_routes.router)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[require_admin] = lambda: True
    app.dependency_overrides[get_db] = _override
    return app, TestingSession


def seed(db):
    now = datetime.utcnow()
    rows = [
        Account(platform="workbuddy", name="wb-alpha", uid="wb-001", status="active",
                balance_total=1000, balance_remain=900, auth_json="{}",
                credits_expiring_soon=100, credits_evergreen=800, credits_expired=0,
                credits_soonest_expire_at=now, last_sync_at=now),
        Account(platform="workbuddy", name="wb-beta", uid="wb-002", status="active",
                balance_total=1000, balance_remain=500, auth_json="{}",
                credits_expiring_soon=0, credits_evergreen=500, credits_expired=0),
        Account(platform="workbuddy", name="wb-gamma", uid="wb-003", status="disabled",
                balance_total=10, balance_remain=7, auth_json="{}"),
        Account(platform="loomy", name="loomy-one", uid="loomy-111", domain="cn",
                status="active", balance_total=300, balance_remain=260, auth_json="{}",
                credits_expiring_soon=60, credits_evergreen=200, credits_expired=0),
        Account(platform="loomy", name="loomy-two", uid="loomy-222", domain="cn",
                status="disabled", balance_total=50, balance_remain=40, auth_json="{}"),
        Account(platform="trae", name="trae-one", uid="trae-001", domain="cn",
                status="active", balance_total=800, balance_remain=640, auth_json="{}"),
        Account(platform="qoder", name="qoder-one", uid="qoder-001", domain="cn",
                status="active", balance_total=400, balance_remain=350, auth_json="{}",
                credits_snapshot='{"user_quota": {"total": 400, "remaining": 350},'
                                 ' "addon_quota": {"total": 200, "remaining": 180}}',
                credits_synced_at=now),
        Account(platform="qoder", name="qoder-two", uid="qoder-002", domain="cn",
                status="active", balance_total=200, balance_remain=180, auth_json="{}",
                credits_snapshot='{"user_quota": {"total": 200, "remaining": 180},'
                                 ' "addon_quota": {"total": 0, "remaining": 0}}',
                credits_synced_at=now),
    ]
    db.add_all(rows)
    db.commit()
    return [r.id for r in rows]


class PaginationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, cls.Session = make_app()
        cls.client = TestClient(cls.app)
        with cls.Session() as db:
            cls.ids = seed(db)

    def test_generic_paginated(self):
        r = self.client.get("/api/accounts", params={"page": 1, "page_size": 2},
                            headers={"X-Platform": "workbuddy"})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["paginated"])
        self.assertEqual(len(d["items"]), 2)
        self.assertEqual(d["total"], 3)          # 默认平台作用域 = workbuddy
        self.assertEqual(d["summary"]["total"], 3)
        self.assertEqual(d["summary"]["active"], 2)
        self.assertEqual(d["summary"]["balance_remain"], 1400)  # 只算 active
        self.assertEqual(d["summary"]["disabled_balance_remain"], 7)
        # 第 2 页还有 1 条
        r2 = self.client.get("/api/accounts", params={"page": 2, "page_size": 2},
                             headers={"X-Platform": "workbuddy"})
        self.assertEqual(len(r2.json()["items"]), 1)

    def test_generic_search_and_status(self):
        r = self.client.get("/api/accounts", params={
            "page": 1, "page_size": 10, "search": "beta"},
            headers={"X-Platform": "workbuddy"})
        d = r.json()
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["items"][0]["name"], "wb-beta")
        # 状态筛选：tab 计数不受 status 影响（summary 仍报全池）
        r2 = self.client.get("/api/accounts", params={
            "page": 1, "page_size": 10, "status": "disabled"},
            headers={"X-Platform": "workbuddy"})
        d2 = r2.json()
        self.assertEqual(d2["total"], 1)
        self.assertEqual(d2["summary"]["total"], 3, "tab 计数不应被 status 筛选吃掉")
        self.assertEqual(d2["summary"]["active"], 2)
        self.assertEqual(d2["items"][0]["status"], "disabled")

    def test_generic_credits_range_and_sort(self):
        r = self.client.get("/api/accounts", params={
            "page": 1, "page_size": 10, "min_credits": 400, "sort": "credits_desc"},
            headers={"X-Platform": "workbuddy"})
        d = r.json()
        self.assertEqual(d["total"], 2, "900/500 命中，7 不命中")
        remains = [i["balance_remain"] for i in d["items"]]
        self.assertEqual(remains, [900, 500])

    def test_loomy_paginated(self):
        r = self.client.get("/api/loomy/accounts", params={"page": 1, "page_size": 1})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["paginated"])
        self.assertEqual(len(d["items"]), 1)
        self.assertEqual(d["total"], 2)
        self.assertEqual(d["summary"]["balance_remain"], 260)  # 只算 active
        # 空页
        r2 = self.client.get("/api/loomy/accounts", params={"page": 5, "page_size": 50})
        self.assertEqual(r2.json()["items"], [])

    def test_trae_paginated(self):
        r = self.client.get("/api/trae/accounts", params={"page": 1, "page_size": 50})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["paginated"])
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["items"][0]["name"], "trae-one")

    def test_qoder_paginated(self):
        r = self.client.get("/api/qoder/accounts", params={
            "page": 1, "page_size": 1, "sort": "credits_desc"})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["paginated"])
        self.assertEqual(len(d["items"]), 1)
        self.assertEqual(d["items"][0]["balance_remain"], 350)
        self.assertEqual(d["total"], 2)

    def test_backward_compat_full_list(self):
        """不带 page 参数 = 旧行为（全量返回），Key 表单等旧调用方不受影响。"""
        r = self.client.get("/api/accounts", headers={"X-Platform": "workbuddy"})
        d = r.json()
        self.assertNotIn("paginated", d)
        self.assertEqual(len(d["items"]), 3)
        self.assertIn("token_expiring", d["summary"])  # 全量模式保留令牌统计

    def test_export_slice_offset(self):
        """导出分片：offset+limit 取一段（大池分片导出的后端语义）。"""
        import admin.routers.accounts as accounts_module

        ids = self.ids
        # worker 在后台线程自建 SessionLocal —— patch 成测试库
        with patch.object(accounts_module, "SessionLocal", self.Session),                 patch.object(accounts_module, "encrypt_credential",
                             lambda raw: raw, create=True):
            r = self.client.post("/api/accounts/export-async", json={
                "format": "backup", "all_platforms": True,
                "offset": 1, "limit": 2,
                "ids": [int(i) for i in ids]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.status_code, 200, r.text)
        job = r.json()
        job_id = job.get("job_id") or job.get("id")
        deadline = time.time() + 15
        status = {}
        while time.time() < deadline:
            jr = self.client.get(f"/api/accounts/job/{job_id}")
            status = jr.json()
            if status.get("status") in ("finished", "failed"):
                break
            time.sleep(0.2)
        self.assertEqual(status.get("status"), "finished", status)
        self.assertEqual((status.get("result") or {}).get("count"), 2,
                         "offset=1 limit=2 应导出 2 条")


if __name__ == "__main__":
    unittest.main(verbosity=2)
