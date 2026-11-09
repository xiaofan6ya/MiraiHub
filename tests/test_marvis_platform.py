"""腾讯 Marvis 平台：协议层回归测试。

这些断言锁的都是「写错了不会报错、只会静默出问题」的地方 ——

  * 签名原文拼错 → 上游 403，报错只说「签名错误」，看不出少了个字段；
  * `Ual-Access-MarvisExt` 带空格 → 服务端按原文比对，静默判无效；
  * 模型 id 不归一 → 400 无效模型 id，看着像「账号坏了」；
  * 不可用模型回 401 → 上层当成登录失效，把整池好号全标死；
  * QQ 的 state 写成整数 → 上游判参数无效（必须与渲染层一致是小数）。
"""
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

# 必须早于 `admin.*` 导入：`admin/db.py` 在模块级按 settings 建引擎，
# 默认值是 MySQL；固定成 SQLite 文件库才能脱库跑（内存库不接受连接池参数）。
os.environ.setdefault(
    "ADMIN_DATABASE_URL",
    "sqlite:///" + str(Path(os.environ.get("TEMP", "/tmp")) / "marvis_test.db"),
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin.platform.marvis.core import client, endpoints  # noqa: E402
from admin.platform.marvis.services import accounts as marvis_accounts  # noqa: E402

ACCT = {"openid": "openid-abc", "access_token": "tok-xyz", "guid": "guid-123",
        "login_type": "1"}


class SignatureTests(unittest.TestCase):
    def test_signature_is_md5_of_concatenation(self):
        """签名口径：md5(query + timestamp + accessKey + nonce)，小写 hex。"""
        expected = hashlib.md5(
            ("{}" + "1700000000000" + endpoints.ACCESS_KEY + "1234").encode()
        ).hexdigest()
        self.assertEqual(
            endpoints.ual_signature("{}", "1700000000000", "1234"), expected)
        self.assertEqual(len(expected), 32)
        self.assertEqual(expected, expected.lower())

    def test_business_headers_carry_signature_and_identity(self):
        headers = endpoints.business_headers(ACCT, query="{}", method="POST")
        for key in ("Ual-Access-Businessid", "Ual-Access-Timestamp",
                    "Ual-Access-Nonce", "Ual-Access-Signature",
                    "Ual-Access-Access-Token", "Ual-Access-Openid",
                    "Ual-Access-Guid", "Ual-Access-Login-Type"):
            self.assertIn(key, headers, "缺 %s" % key)
        # 签名必须能用同一组 timestamp/nonce 复算出来
        again = endpoints.ual_signature(
            "{}", headers["Ual-Access-Timestamp"], headers["Ual-Access-Nonce"])
        self.assertEqual(headers["Ual-Access-Signature"], again)
        self.assertEqual(headers["Ual-Access-Businessid"], endpoints.BUSINESS_ID)

    def test_business_headers_use_browser_ua(self):
        """业务接口必须用客户端浏览器 UA；换成普通 Chrome UA 会被判参数无效。"""
        headers = endpoints.business_headers(ACCT, query="{}")
        self.assertIn("Marvis/", headers["User-Agent"])


class LlmHeaderTests(unittest.TestCase):
    def test_llm_headers_shape(self):
        headers = endpoints.llm_headers(ACCT)
        for key in ("Ual-Access-Openid", "Ual-Access-Access-Token",
                    "Ual-Access-Login-Type", "Ual-Access-Guid",
                    "Ual-Access-MarvisExt", "Marvis-Protocol-Version"):
            self.assertIn(key, headers, "缺 %s" % key)
        self.assertEqual(headers["Ual-Access-Openid"], ACCT["openid"])
        self.assertEqual(headers["Ual-Access-Access-Token"], ACCT["access_token"])

    def test_llm_headers_do_not_carry_business_signature(self):
        """模型网关**不能**带签名头 —— 会把 403 风控变成 401 登录失效（实测）。"""
        headers = endpoints.llm_headers(ACCT)
        self.assertNotIn("Ual-Access-Signature", headers)
        self.assertNotIn("Ual-Access-Timestamp", headers)

    def test_llm_ua_is_the_asyncopenai_one(self):
        headers = endpoints.llm_headers(ACCT)
        self.assertEqual(headers["User-Agent"], endpoints.CLIENT_UA)
        self.assertIn("AsyncOpenAI", headers["User-Agent"])

    def test_marvis_ext_is_compact_json(self):
        """`Ual-Access-MarvisExt` 必须无空格 —— 服务端按原文字符串校验。"""
        ext = endpoints.marvis_ext(ACCT)
        self.assertNotIn(" ", ext, "含空格会被判无效")
        parsed = json.loads(ext)
        self.assertEqual(parsed["guid"], ACCT["guid"])
        self.assertEqual(parsed["reqScene"], 1)
        self.assertEqual(parsed["taskType"], "user")
        self.assertIs(parsed["isIoa"], False)
        # uskey 是 376 字节的 base64 → ceil(376/3)*4 = 504 字符（含 '=' 填充）
        self.assertEqual(len(parsed["uskey"]), 504)
        # nonce 每次不同（上游拿去去重）
        self.assertNotEqual(parsed["nonce"],
                            json.loads(endpoints.marvis_ext(ACCT))["nonce"])


class ModelTests(unittest.TestCase):
    def test_display_name_maps_to_slot(self):
        """用户传显示名 → 上游只认槽位名；不归一会 400。"""
        self.assertEqual(
            endpoints.canonical_model_id("deepseek-v4-1-flash-261017"), "main-auto")
        self.assertEqual(
            endpoints.canonical_model_id("claude-sonnet-4-6"), "claude-sonnet-4-6")

    def test_legacy_index_maps_to_slot(self):
        """旧版把目录存成数组下标，老路由不能作废。"""
        self.assertEqual(endpoints.canonical_model_id("0"), "main-auto")
        self.assertEqual(endpoints.canonical_model_id("7"),
                         "hunyuan-turbos-vision")

    def test_slot_passes_through(self):
        self.assertEqual(endpoints.canonical_model_id("main-auto"), "main-auto")

    def test_unavailable_model_is_trimmed_and_cased(self):
        """denylist 比对前要 trim + 小写，否则手工配的 'Kimi-K2.5 ' 会绕过。"""
        self.assertTrue(endpoints.is_unavailable_model("kimi-k2.5"))
        self.assertTrue(endpoints.is_unavailable_model("  Kimi-K2.5 "))
        self.assertFalse(endpoints.is_unavailable_model("main-auto"))

    def test_catalog_matches_table(self):
        catalog = endpoints.models_catalog()
        self.assertEqual(len(catalog), len(endpoints.MODEL_TABLE))
        for item in catalog:
            self.assertTrue(item["api_id"], "api_id 不能空")
            self.assertTrue(item["id"])
            self.assertGreater(item["context"], 0)

    def test_every_table_slot_is_unique(self):
        slots = [row[0] for row in endpoints.MODEL_TABLE]
        self.assertEqual(len(slots), len(set(slots)))
        names = [row[1] for row in endpoints.MODEL_TABLE]
        self.assertEqual(len(names), len(set(names)))


class AuthUrlTests(unittest.TestCase):
    def test_qq_state_is_decimal(self):
        """QQ 的 state 必须与渲染层一致是**小数**；整数会被判参数无效。"""
        from urllib.parse import urlparse, parse_qs

        url = endpoints.authorize_url("QC", "guid-1")
        query = parse_qs(urlparse(url).query)
        state = query["state"][0]
        self.assertIn(".", state, "state 必须是小数形态：%r" % state)
        self.assertEqual(query["client_id"][0], endpoints.QQ_APP_ID)
        self.assertIn("login_type=QC", query["redirect_uri"][0])

    def test_wx_state_is_guid(self):
        from urllib.parse import urlparse, parse_qs

        url = endpoints.authorize_url("WX", "guid-xyz")
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["state"][0], "guid-xyz")
        self.assertEqual(query["appid"][0], endpoints.WX_APP_ID)
        self.assertIn("login_type=WX", query["redirect_uri"][0])


class ImportTests(unittest.TestCase):
    def test_record_accepts_wrapped_credential(self):
        record = marvis_accounts.record_from_payload(
            {"credential": {"openid": "o", "access_token": "t", "guid": "g"}})
        self.assertEqual(record["openid"], "o")
        self.assertEqual(record["access_token"], "t")

    def test_record_rejects_non_json(self):
        with self.assertRaises(ValueError):
            marvis_accounts.record_from_payload("not json at all")

    def test_import_batch_counts_shapes(self):
        """JSON 数组 / JSONL 都要认，且逐条回报 added/updated。"""
        from admin.db import SessionLocal
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from admin.db import Base

        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            # 数组形态
            result = marvis_accounts.import_batch(db, json.dumps([
                {"openid": "a1", "access_token": "t1", "guid": "g1", "nickname": "号一"},
                {"openid": "a2", "access_token": "t2", "guid": "g2"},
            ]))
            self.assertEqual(result["added"], 2)
            self.assertEqual(result["failed"], 0)

            # 同一个 openid 再来一次 → 更新而不是新增
            result = marvis_accounts.import_batch(db, json.dumps(
                [{"openid": "a1", "access_token": "t1b", "guid": "g1"}]))
            self.assertEqual(result["added"], 0)
            self.assertEqual(result["updated"], 1)

            # JSONL 形态
            result = marvis_accounts.import_batch(
                db, '{"openid":"a3","access_token":"t3","guid":"g3"}\n'
                    '{"openid":"a4","access_token":"t4","guid":"g4"}\n')
            self.assertEqual(result["added"], 2)

            # 缺 guid 的凭证要被补上（否则永远 -109）
            rows = marvis_accounts.accounts_for(db)
            self.assertTrue(all(marvis_accounts.guid_of(
                marvis_accounts.credentials_of(row)) for row in rows))

    def test_import_reports_missing_fields(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from admin.db import Base

        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            result = marvis_accounts.import_batch(
                db, json.dumps([{"openid": "x"}, {"access_token": "y"}]))
            self.assertEqual(result["failed"], 2)
            self.assertEqual(result["added"], 0)
            self.assertTrue(result["errors"])


class RouterHelperTests(unittest.TestCase):
    def test_extract_code_variants(self):
        from admin.platform.marvis.routers.accounts import _extract_code

        self.assertEqual(
            _extract_code("https://x.com/cb?login_type=QC&code=ABC123&state=1.5"),
            "ABC123")
        self.assertEqual(_extract_code("code=ABC123&state=1"), "ABC123")
        self.assertEqual(_extract_code('{"code":"ABC123"}'), "ABC123")
        self.assertEqual(_extract_code("ABC123"), "ABC123")
        self.assertEqual(_extract_code(""), "")

    def test_extract_code_ignores_short_json_code(self):
        """失败响应里的 code 是数字错误码，不能当成授权码。"""
        from admin.platform.marvis.routers.accounts import _extract_code

        self.assertEqual(_extract_code('{"code":-109}'), "")


class GatewayGuardTests(unittest.TestCase):
    def test_unavailable_model_is_404_not_401(self):
        """不可用模型必须 404。401 会让上层把整池账号标成登录失效。"""
        from admin.platform.marvis.gateway import _BadRequest, _validate_model

        with self.assertRaises(_BadRequest) as ctx:
            _validate_model({"model": "kimi-k2.5",
                             "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(ctx.exception.code, "model_not_found")

    def test_unknown_model_rejected_locally(self):
        from admin.platform.marvis.gateway import _BadRequest, _validate_model

        with self.assertRaises(_BadRequest):
            _validate_model({"model": "no-such-model",
                             "messages": [{"role": "user", "content": "hi"}]})

    def test_known_model_passes_and_is_canonicalised(self):
        from admin.platform.marvis.gateway import _validate_model

        slot = _validate_model({"model": "deepseek-v4-1-flash-261017",
                                "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(slot, "main-auto")
        self.assertEqual(slot, "main-auto")

    def test_stream_options_include_usage_is_forced(self):
        """不补 include_usage，上游不返回 usage 帧，用量统计永远是 0。"""
        from admin.platform.marvis.gateway import _prepare_body

        prepared = _prepare_body({"model": "x", "stream": True}, "main-auto")
        self.assertEqual(prepared["model"], "main-auto")
        self.assertTrue(prepared["stream_options"]["include_usage"])
        # 非流式不该被塞 stream_options
        prepared = _prepare_body({"model": "x"}, "main-auto")
        self.assertNotIn("stream_options", prepared)


if __name__ == "__main__":
    unittest.main(verbosity=2)