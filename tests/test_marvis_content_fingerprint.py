"""腾讯内容风控指纹改写：窄改写、可关闭、幂等、不碰非 system。

## 为什么要单测锁住

这条改写是**替用户改内容**，写错的三种形态都不会报错：

  * 规则写窄了 → 静默空跑，上游照样 403 / 4100406；
  * 规则写宽了 → 篡改用户 prompt，或者把 size 后缀、路径里的字样一起改掉；
  * 每次请求无条件重建 system → 上游前缀缓存失效，白吃一次全量重算。

所以这里既锁「命中必改」，也锁「不命中一个字符都不动、连对象都不换」。

## 毒串怎么来的

不写死字面量：这些串本身就是上游的内容黑名单，抄进仓库既可能被内容过滤
污染，也会让下一个人以为它们只是随手编的样例。样例一律由**规则自身的正则**
反解而来（字面量规则反解 re.escape，正则规则按空白 / 可选项归一），规则一改，
样例跟着变。
"""
import logging
import os
import re
import sys
import unittest
from pathlib import Path

# 与 tests/test_marvis_platform.py 同一口径：admin/db.py 在模块级按 settings
# 建引擎，固定成 SQLite 文件库才能脱离 MySQL 跑。
os.environ.setdefault(
    "ADMIN_DATABASE_URL",
    "sqlite:///" + str(Path(os.environ.get("TEMP", "/tmp")) / "marvis_test.db"),
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin.platform.marvis import gateway as marvis_gateway  # noqa: E402


def _specimen(pattern):
    """按规则自身的正则反解一条**必定命中**的样例（不写字面量）。"""
    src = pattern.pattern
    src = re.sub(r"\(\?:[^()]*\)\?", "", src)   # 可选非捕获组取缺席
    src = re.sub(r"\\s[*+]", " ", src)            # 空白类归一成单个空格
    src = re.sub(r"([A-Za-z])\?", r"\1", src)     # 单字符可选取该字符
    src = re.sub(r",\?", "", src)                  # 可选逗号取缺席
    src = re.sub(r"\\(.)", r"\1", src)           # 其余转义还原成字面量
    return src


class RuleTableTests(unittest.TestCase):
    """规则表本身：条数、每条都能命中自己的样例。"""

    def test_rule_table_has_all_seven_fingerprints(self):
        """少一条就是留一个随时会踩的 403。"""
        self.assertEqual(len(marvis_gateway._TENCENT_FINGERPRINT_RULES), 7)

    def test_every_rule_rewrites_its_own_specimen(self):
        """每条规则：样例命中 → 改写后文本变了、且不再命中。"""
        for index, (pattern, _replacement) in enumerate(
                marvis_gateway._TENCENT_FINGERPRINT_RULES):
            specimen = _specimen(pattern)
            self.assertIsNotNone(
                pattern.search(specimen),
                "第 %d 条样例没命中自己的规则：%r" % (index + 1, specimen))
            out, hits = marvis_gateway.rewrite_tencent_fingerprints(specimen)
            self.assertNotEqual(out, specimen, "第 %d 条没被改写" % (index + 1))
            self.assertEqual(hits, 1, "第 %d 条命中数应为 1" % (index + 1))
            self.assertIsNone(
                pattern.search(out),
                "第 %d 条改写后仍命中：%r" % (index + 1, out))


class RewriteSemanticsTests(unittest.TestCase):
    """命中必改、不命中逐字符不变、大小写不敏感、幂等。"""

    def setUp(self):
        self._env_backup = os.environ.get(marvis_gateway.SANITIZE_TENCENT_ENV)
        os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)

    def tearDown(self):
        if self._env_backup is None:
            os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)
        else:
            os.environ[marvis_gateway.SANITIZE_TENCENT_ENV] = self._env_backup

    def test_clean_text_is_untouched_object(self):
        """不含指纹的文本逐字符不变，且返回同一对象（零字符串重建）。"""
        text = "普通的用户文本，没有任何指纹。\n第二行也原样保留：size=1024。"
        out, hits = marvis_gateway.rewrite_tencent_fingerprints(text)
        self.assertIs(out, text)
        self.assertEqual(hits, 0)

    def test_empty_and_non_string_pass_through(self):
        self.assertEqual(marvis_gateway.rewrite_tencent_fingerprints(""), ("", 0))
        self.assertEqual(marvis_gateway.rewrite_tencent_fingerprints(None), (None, 0))

    def test_case_insensitive(self):
        """上游按大小写不敏感匹配；大 / 小 / 翻转三种形态都必须被改写。"""
        for index, (pattern, _replacement) in enumerate(
                marvis_gateway._TENCENT_FINGERPRINT_RULES):
            specimen = _specimen(pattern)
            for variant in (specimen.upper(), specimen.lower(), specimen.swapcase()):
                out, hits = marvis_gateway.rewrite_tencent_fingerprints(variant)
                self.assertEqual(
                    hits, 1, "第 %d 条大小写变体没命中：%r" % (index + 1, variant))
                self.assertNotEqual(out, variant)

    def test_idempotent(self):
        """连续改写两次结果相同；第二次零命中、返回同一对象（重试安全）。"""
        for index, (pattern, _replacement) in enumerate(
                marvis_gateway._TENCENT_FINGERPRINT_RULES):
            once, _ = marvis_gateway.rewrite_tencent_fingerprints(_specimen(pattern))
            twice, hits = marvis_gateway.rewrite_tencent_fingerprints(once)
            self.assertEqual(once, twice, "第 %d 条不幂等" % (index + 1))
            self.assertIs(twice, once)
            self.assertEqual(hits, 0)


class MessageScopeTests(unittest.TestCase):
    """只改 system；user / assistant / tool 原样透传；调用方 body 不被就地改。"""

    def setUp(self):
        self._env_backup = os.environ.get(marvis_gateway.SANITIZE_TENCENT_ENV)
        os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)
        self.poison = _specimen(marvis_gateway._TENCENT_FINGERPRINT_RULES[3][0])

    def tearDown(self):
        if self._env_backup is None:
            os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)
        else:
            os.environ[marvis_gateway.SANITIZE_TENCENT_ENV] = self._env_backup

    def test_only_system_messages_are_rewritten(self):
        system_msg = {"role": "system", "content": self.poison}
        user_msg = {"role": "user", "content": self.poison}
        assistant_msg = {"role": "assistant", "content": self.poison}
        tool_msg = {"role": "tool", "content": self.poison, "tool_call_id": "call_1"}
        body = {"model": "x",
                "messages": [system_msg, user_msg, assistant_msg, tool_msg]}

        out = marvis_gateway._prepare_body(body, "main-auto")

        self.assertNotEqual(out["messages"][0]["content"], self.poison)
        self.assertIs(out["messages"][1], user_msg)
        self.assertIs(out["messages"][2], assistant_msg)
        self.assertIs(out["messages"][3], tool_msg)
        # 调用方的 body 与其中的消息对象都不能被就地改。
        self.assertEqual(system_msg["content"], self.poison)
        self.assertIs(body["messages"][0], system_msg)

    def test_all_system_messages_are_rewritten(self):
        """非首位 system 同样会被拦，所以每一条都要改。"""
        first = {"role": "system", "content": "无指纹的开场白"}
        second = {"role": "system", "content": self.poison}
        out = marvis_gateway._prepare_body(
            {"model": "x", "messages": [first, second]}, "main-auto")
        self.assertIs(out["messages"][0], first)
        self.assertNotEqual(out["messages"][1]["content"], self.poison)

    def test_odd_shapes_do_not_crash(self):
        """畸形 messages 不能把网关打成 500：原样放行，交给上游判。"""
        body = {"model": "x",
                "messages": ["raw", None, {"role": "system", "content": None}]}
        out = marvis_gateway._prepare_body(body, "main-auto")
        self.assertIs(out["messages"], body["messages"])
        out = marvis_gateway._prepare_body(
            {"model": "x", "messages": {"not": "a list"}}, "main-auto")
        self.assertEqual(out["messages"], {"not": "a list"})
        self.assertNotIn("messages", marvis_gateway._prepare_body({"model": "x"}, "main-auto"))


class SwitchTests(unittest.TestCase):
    """开关：默认开；关闭时完全跳过，返回同一个对象。"""

    def setUp(self):
        self._env_backup = os.environ.get(marvis_gateway.SANITIZE_TENCENT_ENV)
        os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)
        self.poison = _specimen(marvis_gateway._TENCENT_FINGERPRINT_RULES[3][0])

    def tearDown(self):
        if self._env_backup is None:
            os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)
        else:
            os.environ[marvis_gateway.SANITIZE_TENCENT_ENV] = self._env_backup

    def test_default_is_on(self):
        out, hits = marvis_gateway.rewrite_tencent_fingerprints(self.poison)
        self.assertEqual(hits, 1)
        self.assertNotEqual(out, self.poison)
        for value in ("1", "true", "on", "yes"):
            os.environ[marvis_gateway.SANITIZE_TENCENT_ENV] = value
            _out, hits = marvis_gateway.rewrite_tencent_fingerprints(self.poison)
            self.assertEqual(hits, 1, "%r 应视为开启" % value)

    def test_off_skips_rewrite_and_returns_same_object(self):
        for value in ("0", "false", "OFF", " no ", "No"):
            os.environ[marvis_gateway.SANITIZE_TENCENT_ENV] = value
            out, hits = marvis_gateway.rewrite_tencent_fingerprints(self.poison)
            self.assertIs(out, self.poison, "%r 应视为关闭" % value)
            self.assertEqual(hits, 0)

            body = {"model": "x",
                    "messages": [{"role": "system", "content": self.poison}]}
            prepared = marvis_gateway._prepare_body(body, "main-auto")
            self.assertIs(prepared["messages"], body["messages"],
                          "关闭时不应重建消息列表")
            self.assertEqual(prepared["messages"][0]["content"], self.poison)


class LoggingTests(unittest.TestCase):
    """命中日志只记条数，不记原文（system 里可能带用户 prompt / 路径 / 密钥）。"""

    def setUp(self):
        self._env_backup = os.environ.get(marvis_gateway.SANITIZE_TENCENT_ENV)
        os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)
        self.poison = _specimen(marvis_gateway._TENCENT_FINGERPRINT_RULES[3][0])

    def tearDown(self):
        if self._env_backup is None:
            os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)
        else:
            os.environ[marvis_gateway.SANITIZE_TENCENT_ENV] = self._env_backup

    def test_hit_logs_count_only(self):
        with self.assertLogs("admin.platform.marvis.gateway", level="INFO") as captured:
            marvis_gateway._prepare_body(
                {"model": "x", "messages": [{"role": "system", "content": self.poison}]},
                "main-auto")
        logged = "\n".join(captured.output)
        self.assertIn("Marvis 内容风控指纹改写", logged)
        self.assertIn("rules_hit=1", logged)
        self.assertNotIn(self.poison, logged, "日志里不能出现原文")

    def test_clean_request_logs_nothing(self):
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        log = logging.getLogger("admin.platform.marvis.gateway")
        log.addHandler(handler)
        try:
            marvis_gateway._prepare_body(
                {"model": "x", "messages": [{"role": "system", "content": "普通文本"}]},
                "main-auto")
        finally:
            log.removeHandler(handler)
        self.assertEqual([r for r in records if "指纹改写" in r.getMessage()], [])


class NoRegressionTests(unittest.TestCase):
    """不破坏 _prepare_body 既有契约。"""

    def test_stream_options_still_injected(self):
        out = marvis_gateway._prepare_body({"model": "x", "stream": True}, "main-auto")
        self.assertEqual(out["model"], "main-auto")
        self.assertTrue(out["stream_options"]["include_usage"])

    def test_model_rewritten_and_body_not_mutated(self):
        body = {"model": "user-visible-name"}
        out = marvis_gateway._prepare_body(body, "main-auto")
        self.assertEqual(out["model"], "main-auto")
        self.assertEqual(body["model"], "user-visible-name")


if __name__ == "__main__":
    unittest.main(verbosity=2)
