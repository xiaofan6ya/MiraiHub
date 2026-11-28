"""多平台适配回归：issue #125 / #126 / #128 / #129。

只用合成数据：不读真实凭证、不连上游。
"""
from __future__ import annotations

import json
import types
from unittest import TestCase
from unittest.mock import patch

from tests import assert_test_database
from admin.db import Base, SessionLocal, engine
from admin.models import Account, ModelConfig


class _DbCase(TestCase):
    def setUp(self):
        assert_test_database(engine)
        Base.metadata.create_all(engine)
        self.db = SessionLocal()
        self.db.query(ModelConfig).delete()
        self.db.query(Account).delete()
        self.addCleanup(self.db.close)


class ModelPlatformScopeTests(_DbCase):
    """#125-1：proxy 传 `platform=` 时不能 TypeError（auto 模型 500）。"""

    def setUp(self):
        super().setUp()
        self.db.add_all([
            # 历史行：platform 为 NULL，仍属 WorkBuddy
            ModelConfig(model_id="wb-free-null", enabled=1,
                        credit_multiplier=0, level="system"),
            ModelConfig(model_id="wb-free", platform="workbuddy", enabled=1,
                        credit_multiplier=0, level="system"),
            ModelConfig(model_id="trae-free", platform="trae", enabled=1,
                        credit_multiplier=0, level="system"),
        ])
        self.db.commit()

    def test_accepts_platform_kwarg(self):
        from admin.routers import models as models_router

        free = models_router._get_free_models(self.db, platform="workbuddy")
        enabled = models_router._get_enabled_models(self.db, platform="workbuddy")
        self.assertEqual(free, {"wb-free-null", "wb-free"})
        self.assertEqual(enabled, {"wb-free-null", "wb-free"})
        # 不传 platform 时保持旧行为（全库）
        self.assertIn("trae-free", models_router._get_enabled_models(self.db))

    def test_auto_picks_a_workbuddy_model(self):
        from admin.routers.proxy import _pick_best_model

        picked = _pick_best_model(self.db, "auto", None)
        self.assertIn(picked, {"wb-free", "wb-free-null"})


class QoderLinkFallbackTests(TestCase):
    """#126：主机不可达（DNS/拒绝）也应回退旧链路；timeout 不回退。"""

    def _err(self, kind, detail):
        from admin.platform.qoder.core.client import UpstreamError

        return UpstreamError("x", status=0, kind=kind, detail=detail)

    def test_unreachable_falls_back(self):
        from admin.platform.qoder.services import chat

        self.assertTrue(chat._is_link_gone(self._err(
            "network", "<urlopen error [Errno 11001] getaddrinfo failed>")))
        self.assertTrue(chat._is_link_gone(self._err(
            "network", "[WinError 10061] 由于目标计算机积极拒绝")))

    def test_timeout_does_not_fall_back(self):
        from admin.platform.qoder.services import chat

        self.assertFalse(chat._is_link_gone(
            self._err("network", "<urlopen error timed out>")))

    def test_intl_has_no_model_base(self):
        from admin.platform.qoder.core import endpoints

        self.assertEqual(endpoints.model_base("intl"), "")
        self.assertEqual(endpoints.model_base("cn"), "https://gateway.qoder.com.cn")


class PlatformHealthStageTests(_DbCase):
    """#129：阶段名按语义映射，缺阶段时按整体健康回落且 detail 不为空。"""

    def test_qoder_stage_names_are_mapped(self):
        from admin import account_health
        from admin.platform import registry

        acc = Account(name="q", uid="q-1", platform="qoder",
                      auth_json="{}", status="active")
        self.db.add(acc)
        self.db.commit()
        self.db.refresh(acc)

        class Ops:
            def health(self, _db, _id):
                return {"ok": False, "stages": {
                    "token": {"ok": True, "detail": "dt-ja6（27）"},
                    "models": {"ok": True, "detail": "16 个模型"},
                    "quota": {"ok": False, "error": "额度接口 403"},
                }}

        class Gw:
            def account_ops(self):
                return Ops()

        with patch.object(registry, "spec_for_storage",
                          return_value=types.SimpleNamespace(realm="cn")), \
                patch.object(registry, "load_platform_gateway", return_value=Gw()):
            out = account_health._platform_health(self.db, acc)

        stages = out["stages"]
        self.assertTrue(stages["balance"]["ok"])
        self.assertEqual(stages["balance"]["detail"], "dt-ja6（27）")
        self.assertEqual(stages["models"]["detail"], "16 个模型")
        self.assertTrue(out["reason"])
        self.assertIn("quota", out["reason"])


class CodeArtsAgentPayloadTests(TestCase):
    """#128：VS Code 壳的平铺结构 / ISO 过期 / JWK 对象要能归一。"""

    def test_flat_payload_is_normalized(self):
        from admin.platform.codearts.core import session

        local = session.parse_payload({
            "securitytoken": "sts",
            "accessKey": "AK",
            "secretKey": "SK",
            "expires_at": "2026-12-01T03:04:05Z",
            "refresh_token": "rt",
            "account": {"id": "uid-9", "label": "dev@example.com"},
            "dpop": {"privateKey": {"kty": "EC", "crv": "P-256", "x": "a", "y": "b", "d": "c"},
                     "publicKey": {"kty": "EC", "crv": "P-256", "x": "a", "y": "b"}},
            "pkcePair": {"codeVerifier": "verifier"},
        }, source="state.vscdb")
        self.assertEqual((local.access_key, local.secret_key), ("AK", "SK"))
        self.assertEqual((local.security_token, local.refresh_token), ("sts", "rt"))
        self.assertEqual((local.uid, local.user_name), ("uid-9", "dev@example.com"))
        self.assertGreater(local.expires_at, 1796000000000)
        self.assertEqual(json.loads(local.dpop_private_key)["kty"], "EC")
        self.assertEqual(local.pkce_code_verifier, "verifier")

    def test_ide_payload_still_works(self):
        from admin.platform.codearts.core import session

        local = session.parse_payload({"session": {
            "accessKey": "AK2", "secretKey": "SK2", "securityToken": "sts2",
            "refreshToken": "rt2", "expiresAt": 1796000000000,
            "userId": "uid-2", "userName": "ide",
        }, "loginContext": {
            "pkcePair": {"codeVerifier": "v2"},
            "dpopKeyPair": {"privateKey": "{\"kty\":\"EC\"}", "publicKey": "{}"},
        }})
        self.assertEqual(local.access_key, "AK2")
        self.assertEqual(local.expires_at, 1796000000000)
        self.assertEqual(local.uid, "uid-2")

    def test_missing_keys_raise_no_login(self):
        from admin.platform.codearts.core import session

        with self.assertRaises(session.SessionError) as ctx:
            session.parse_payload({"securitytoken": "x"})
        self.assertEqual(ctx.exception.kind, "no_login")


class TraeUidTests(TestCase):
    """#125-3：上游不给 UserId 时从 access token 解，避免账号互相覆盖。"""

    def test_uid_from_jwt(self):
        import base64

        from admin.platform.trae.routers import accounts as trae_router

        def b64(obj):
            raw = json.dumps(obj, separators=(",", ":")).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        token = "%s.%s.%s" % (b64({"alg": "RS256"}),
                              b64({"data": {"id": "2926256481121401"}}), "sig")
        self.assertEqual(trae_router._uid_from_jwt(token), "2926256481121401")
        self.assertEqual(trae_router._uid_from_jwt("garbage"), "")


if __name__ == "__main__":
    import unittest

    unittest.main(verbosity=2)
