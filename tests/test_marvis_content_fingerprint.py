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
import json
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
    src = re.sub(r"\(\?:([^()|]*)\|[^()]*\)", r"\1", src)  # 交替组取首个分支
    src = re.sub(r"\\s[*+]", " ", src)            # 空白类归一成单个空格
    src = re.sub(r"([A-Za-z])\?", r"\1", src)     # 单字符可选取该字符
    src = re.sub(r",\?", "", src)                  # 可选逗号取缺席
    src = re.sub(r"\\(.)", r"\1", src)           # 其余转义还原成字面量
    return src


class RuleTableTests(unittest.TestCase):
    """规则表本身：条数、每条都能命中自己的样例。"""

    def test_rule_table_has_all_twelve_fingerprints(self):
        """少一条就是留一个随时会踩的 403（2026-11-18 扩表后为 12 条）。"""
        self.assertEqual(len(marvis_gateway._TENCENT_FINGERPRINT_RULES), 12)

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


class IdentityLineTests(unittest.TestCase):
    """首行身份句兜底改写（2026-11-17 实测：Claude Code / Codex 全量 403）。

    ## 这一组的由来

    上游只凭**第一条非空行的 `You are <产品名>`** 就回 403 / 4100406，而
    Claude Code 与 Codex 的身份句正好是这个形态。原来的 7 条逐句规则拦不住
    新措辞（客户端改一个字就失效），而且其中两条的替换值**本身也被拦**。

    所以这一组锁的是**形态**而不是某一句原文：

      · 首行 `You are` → `You're`（大小写、缩进、前导空行都要覆盖）；
      · 非首行不动（上游只看首行，动它等于篡改用户 prompt）；
      · user 角色不动（上游不查 user）；
      · `developer` 角色必须动（Codex 的身份句就放这儿）。
    """

    def setUp(self):
        self._env_backup = os.environ.get(marvis_gateway.SANITIZE_TENCENT_ENV)
        os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)

    def tearDown(self):
        if self._env_backup is None:
            os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)
        else:
            os.environ[marvis_gateway.SANITIZE_TENCENT_ENV] = self._env_backup

    @staticmethod
    def _first_line_clean(text):
        """业务判据：第一条非空行不能再以 `you are` 起句。"""
        for line in text.split("\n"):
            if not line.strip():
                continue
            return not re.match(r"^[ \t]*you[ \t]+are[ \t]+", line, re.I)
        return True

    def test_client_identity_sentences_are_neutralised(self):
        """各个客户端真实在发的身份句，改写后首行都不能再是 `You are`。"""
        for name, sentence in (
                ("claude-code", "You are Claude Code, Anthropic's official CLI for Claude."),
                ("claude-agent-sdk",
                 "You are a Claude agent, built on Anthropic's Claude Agent SDK."),
                ("codex",
                 "You are a coding agent running in the Codex CLI, a terminal-based coding agent."),
                ("legacy-anthropic", "You are Claude Code."),
                ("codex-plain", "You are Codex.")):
            with self.subTest(client=name):
                out, hits = marvis_gateway.rewrite_tencent_fingerprints(sentence)
                self.assertEqual(hits, 1)
                self.assertNotEqual(out, sentence)
                self.assertTrue(self._first_line_clean(out), out)
                self.assertIn("You're", out)

    def test_case_and_whitespace_shapes(self):
        """上游大小写不敏感、会跳过前导空白 —— 这几种形态都要改。"""
        for variant in ("You are Claude Code.",
                        "you are claude code.",
                        "YOU ARE CLAUDE CODE.",
                        "You  are  Claude Code.",
                        "  You are Claude Code.",
                        "\nYou are Claude Code.",
                        "\n\n   You are Claude Code."):
            with self.subTest(variant=variant):
                out, hits = marvis_gateway.rewrite_tencent_fingerprints(variant)
                self.assertEqual(hits, 1, variant)
                self.assertTrue(self._first_line_clean(out), out)

    def test_rewrite_is_idempotent(self):
        """重复改写不再变（重试 / 多入口串联都安全）。"""
        once, _ = marvis_gateway.rewrite_tencent_fingerprints("You are Claude Code.")
        twice, hits = marvis_gateway.rewrite_tencent_fingerprints(once)
        self.assertEqual(once, twice)
        self.assertEqual(hits, 0)
        self.assertIs(twice, once)

    def test_only_first_non_empty_line_is_touched(self):
        """首行不是身份句时整段不动 —— 上游也只查首行。"""
        for text in ("Preamble line.\nYou are Claude Code.",
                     "Preamble.\n\nYou are a Claude agent."):
            with self.subTest(text=text):
                out, hits = marvis_gateway.rewrite_tencent_fingerprints(text)
                self.assertIs(out, text)
                self.assertEqual(hits, 0)

    def test_markdown_prefixed_identity_is_left_alone(self):
        """`# You are ...` 实测放行，没有理由去改用户的标题行。"""
        for text in ("# You are Claude Code.", "- You are Claude Code."):
            with self.subTest(text=text):
                out, hits = marvis_gateway.rewrite_tencent_fingerprints(text)
                self.assertIs(out, text)
                self.assertEqual(hits, 0)

    def test_developer_role_is_rewritten(self):
        """Codex 把身份句放在 developer 段；只改 system 等于 Codex 全量 403。"""
        body = {"model": "x", "messages": [
            {"role": "developer", "content": "You are a coding agent running in the Codex CLI."},
            {"role": "system", "content": "You are Claude Code."},
            {"role": "user", "content": "You are Claude Code."},
        ]}
        out = marvis_gateway._prepare_body(body, "main-auto")
        self.assertTrue(self._first_line_clean(out["messages"][0]["content"]))
        self.assertTrue(self._first_line_clean(out["messages"][1]["content"]))
        # user 角色上游不查，必须逐字不动
        self.assertEqual(out["messages"][2]["content"], "You are Claude Code.")
        self.assertIs(out["messages"][2], body["messages"][2])


class ContentBlockClassificationTests(unittest.TestCase):
    """`4100406` 必须归成 content_block，不能混进 signature_invalid。

    混错的后果不是文案问题，是**换号语义错**：403 在 `_attempt` 里不 break，
    同一条被拦的提示词会把三个好号依次撞一遍、每个号记一次失败 ——
    用户看到「号全坏了」，而号一个都没坏。
    """

    def test_content_block_is_distinguished(self):
        classify = marvis_gateway.client._classify
        cases = {
            '{"code":4100406,"msg":"非法请求"}': "content_block",
            '{"code":4100404,"msg":"risk"}': "risk_control",
            "": "signature_invalid",
        }
        for payload, expected in cases.items():
            with self.subTest(payload=payload):
                self.assertEqual(classify(403, payload), expected)

    def test_content_block_returns_actionable_400(self):
        """403 会被 Claude Code / Codex 渲染成「API 密钥无效」，必须换成 400。"""
        exc = marvis_gateway.client.UpstreamError(
            "模型网关失败（HTTP 403）", status=403, code="403",
            kind="content_block", detail='{"code":4100406,"msg":"非法请求"}')
        response = marvis_gateway._fail(exc)
        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.body.decode("utf-8"))
        error = payload["error"]
        self.assertEqual(error["type"], "content_filter")
        self.assertEqual(error["code"], "content_blocked")
        # 不能让上层以为要换号 / 换 Key
        self.assertFalse(error["upstream_account"])
        self.assertIn("4100406", error["message"])


class RiskControlTests(unittest.TestCase):
    """设备 / IP 风控（4100404）与内容风控（4100406）是**两件完全不同的事**。

    ## 为什么必须锁住（2026-11-17 用户实测）

    两者都回 403，以前共用一条处理路径，后果是把「平台封了出口」说成
    「你的提示词有问题」。用户按提示去改提示词，怎么改都没用 —— 实测
    **连最简请求都过不了**：

        {"model":"main-auto","messages":[{"role":"user","content":"hi"}]}
        -> 403 {"code":4100404,"msg":"客户端通用错误:请求被风控拦截"}

    而同一份凭证的钱包接口 `/v3/marvis_get_wallet` 回 200（余额正常）。
    ⇒ 号没坏、key 没坏，是出口 IP / 设备画像被平台拦。

    另一个更隐蔽的坑：风控以前**不 break**，一条请求会把池子里所有号
    依次撞一遍、每个号吃 600 秒冷却 —— 4 个号撞满就是全池冷却，
    之后哪怕风控解除，网关自己也回 `503 accounts_cooling` 把请求挡在门外。
    """

    def test_risk_control_is_not_content_block(self):
        classify = marvis_gateway.client._classify
        self.assertEqual(
            classify(403, '{"code":4100404,"msg":"客户端通用错误:请求被风控拦截"}'),
            "risk_control")
        self.assertEqual(classify(403, '{"code":4100406,"msg":"非法请求"}'),
                         "content_block")

    def test_risk_control_returns_429_with_retry_after(self):
        """必须是 429 + Retry-After：403 会被客户端当成「API 密钥无效」。"""
        exc = marvis_gateway.client.UpstreamError(
            "模型网关失败（HTTP 403）", status=403, code="403",
            kind="risk_control",
            detail='{"code":4100404,"msg":"客户端通用错误:请求被风控拦截"}')
        response = marvis_gateway._fail(exc)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers.get("retry-after"), "600")
        error = json.loads(response.body.decode("utf-8"))["error"]
        self.assertEqual(error["type"], "upstream_risk_control")
        self.assertEqual(error["code"], "risk_control")
        # 不能让上层去 failover 换号（换号 100% 无效）
        self.assertFalse(error["upstream_account"])
        # 文案必须指向「出口 IP」，而不是提示词 / 账号
        self.assertIn("出口", error["message"])
        self.assertIn("4100404", error["message"])

    def test_risk_control_stops_retry_and_spares_other_accounts(self):
        """命中风控**立刻停**：不换号、只记当前号一次、冷却 60 秒。"""
        import datetime as _dt
        from unittest.mock import MagicMock, patch

        account = MagicMock()
        account.id = 404
        account.uid = "uid-404"
        account.cool_until = None
        account.cool_kind = ""
        account.balance_total = 10000000
        account.balance_remain = 8000000

        other = MagicMock()
        other.id = 411

        calls = {"selected": 0}

        def fake_select(db, **kwargs):
            calls["selected"] += 1
            return account if calls["selected"] == 1 else other

        risk = marvis_gateway.client.UpstreamError(
            "模型网关失败（HTTP 403）", status=403, code="403",
            kind="risk_control",
            detail='{"code":4100404,"msg":"请求被风控拦截"}')

        with patch.object(marvis_gateway.shared, "select_account", fake_select), \
                patch.object(marvis_gateway.shared, "lease", return_value=True), \
                patch.object(marvis_gateway.shared, "release"), \
                patch.object(marvis_gateway.shared, "note_success"), \
                patch.object(marvis_gateway.shared, "note_failure") as note_failure, \
                patch.object(marvis_gateway.marvis_accounts, "ensure_token",
                             return_value={}), \
                patch.object(marvis_gateway.client, "chat_completions",
                             side_effect=risk):
            with self.assertRaises(marvis_gateway.client.UpstreamError):
                marvis_gateway._attempt(None, None, None, {"model": "main-auto"},
                                        "cn", stream=False)

        # 只撞了一个号就停 —— 不许把池子里的号依次撞一遍
        self.assertEqual(calls["selected"], 1,
                         "风控后仍然继续换号，会把全池撞进冷却")
        self.assertEqual(note_failure.call_count, 1)
        kwargs = note_failure.call_args.kwargs
        self.assertEqual(kwargs["kind"], "risk_control")
        self.assertEqual(kwargs["cool_seconds"], 60)


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


class GenericAssistantOpeningRuleTests(unittest.TestCase):
    """规则 9-13：通用开场白（`You are a helpful assistant` 这一类）。

    真机对照（同一把 key、同一个槽位，交替 24 次）：不带 system 12 次全 200，
    带 system 12 次全 403 / 4100406；长段自定义提示词 200 ⇒ 命中的是这些固定
    开头，不是长度。

    这里**必须写字面量**：要锁的是「上游登记的那几条原串」以及改写后的等价
    表述，没法像 `RuleTableTests` 那样从正则反解。规则一改，这里就该红。

    ⚠ 期望值是 `You're` 形态，不是 `You are a capable ...`：本文件跑在上游
    代码上，`rewrite_tencent_fingerprints()` 末尾还有 `_rewrite_identity_line()`
    兜底 —— 只要首行以 `You are ` 起句就会被再改一次。规则自己写成 `You're`，
    一条规则就只改一次，`rules_hit` 不会虚高一倍。
    """

    #: 规则 4 的两个品牌名：旧名中间带空格，新名连写。
    BRANDS = ("DeepSeek " + "Runtime", "DeepSeek" + "Runtime")

    #: (上游触发串, 网关改写后的文本)
    CASES = (
        ("You are a helpful AI assistant", "You're a capable AI assistant"),
        ("You are a helpful assistant", "You're a capable assistant"),
        ("You are an AI assistant.", "You're a capable AI assistant."),
        ("You are a coding assistant.", "You're a capable coding assistant."),
        ("你是乐于助人的助手。", "你是一位能力出众的助手。"),
    )

    def setUp(self):
        self._env_backup = os.environ.get(marvis_gateway.SANITIZE_TENCENT_ENV)
        os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)

    def tearDown(self):
        if self._env_backup is None:
            os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)
        else:
            os.environ[marvis_gateway.SANITIZE_TENCENT_ENV] = self._env_backup

    def test_every_generic_opening_is_rewritten_to_expected_text(self):
        """5 条通用开场白：命中一次、改写结果逐字锁死。"""
        for poison, expected in self.CASES:
            out, hits = marvis_gateway.rewrite_tencent_fingerprints(poison)
            self.assertEqual(hits, 1, "开场白没命中：%r" % (poison,))
            self.assertEqual(out, expected,
                             "改写结果不对：%r -> %r" % (poison, out))

    def test_rule_four_covers_both_brand_names(self):
        """规则 4 的品牌名兼容：换名之后旧正则空跑，两个名字都必须被**规则 4**吃掉。

        这里直接查表项（`pattern` / `replacement`），而不是只看
        `rewrite_tencent_fingerprints()` 的返回值：末尾的 `_rewrite_identity_line()`
        兜底会把任何以 `You are ` 起句的首行改成 `You're `，于是「规则 4 空跑」
        也能被它掩盖成「命中 1 次」—— 那正是这条规则在真机上静默失效、
        日志看着却不像坏了的原因。
        """
        pattern, replacement = marvis_gateway._TENCENT_FINGERPRINT_RULES[3]
        expected = ("You're a capable AI coding assistant, "
                    "powered by a large language model")
        for brand in self.BRANDS:
            trigger = "You are an AI coding agent, powered by " + brand
            self.assertIsNotNone(pattern.search(trigger),
                                 "品牌名 %r 没被规则 4 覆盖" % (brand,))
            self.assertEqual(pattern.sub(replacement, trigger), expected)

    def test_rule_four_rewritten_text_breaks_the_you_are_an_ai_prefix(self):
        """黑名单锚的是 `You are an AI` 前缀：改写结果不能还带着它。"""
        for brand in self.BRANDS:
            trigger = "You are an AI coding agent, powered by " + brand
            out, hits = marvis_gateway.rewrite_tencent_fingerprints(trigger)
            self.assertEqual(hits, 1, "没命中：%r" % (brand,))
            self.assertFalse(out.lower().startswith("you are an ai"),
                             "替换后仍保留触发前缀：%r" % (out,))
            self.assertEqual(
                out,
                "You're a capable AI coding assistant, "
                "powered by a large language model")

    def test_case_and_whitespace_drift_is_covered(self):
        """大小写翻转 + 连续空白 / 换行都要命中（上游按归一后的文本判）。"""
        for poison, _expected in self.CASES:
            if not poison.isascii():
                continue
            for variant in (poison.upper(), poison.lower(), poison.swapcase(),
                            re.sub(r" ", "  \n", poison)):
                out, hits = marvis_gateway.rewrite_tencent_fingerprints(variant)
                self.assertEqual(hits, 1, "变体没命中：%r" % (variant,))
                self.assertNotEqual(out, variant)

    def test_helpful_ai_assistant_is_not_double_rewritten(self):
        """顺序陷阱：不能先改成 `You're a capable assistant`，把 `AI` 吃掉。"""
        out, hits = marvis_gateway.rewrite_tencent_fingerprints(
            "You are a helpful AI assistant")
        self.assertEqual(hits, 1)
        self.assertEqual(out, "You're a capable AI assistant")
        self.assertNotIn("capable capable", out)

    def test_generic_rule_does_not_eat_capable_text(self):
        """已经写成 `capable` 的正文不能被泛化规则二次改写。"""
        text = "You're a capable AI assistant"
        out, hits = marvis_gateway.rewrite_tencent_fingerprints(text)
        self.assertIs(out, text)
        self.assertEqual(hits, 0)

    def test_trailing_text_is_preserved(self):
        """只换开场白，后面正文逐字保留（不删句子 = 不丢语义约束）。"""
        original = ("You are a helpful assistant. You must answer in Chinese.\n"
                    "Never reveal secrets.")
        out, hits = marvis_gateway.rewrite_tencent_fingerprints(original)
        self.assertEqual(hits, 1)
        self.assertEqual(
            out,
            "You're a capable assistant. You must answer in Chinese.\n"
            "Never reveal secrets.")

    def test_full_pipeline_rewrites_system_message(self):
        """走 `_prepare_body`：messages 里的 system 消息同样被改写。"""
        for poison, expected in self.CASES:
            body = {"model": "x",
                    "messages": [{"role": "system", "content": poison},
                                 {"role": "user", "content": "ping"}]}
            out = marvis_gateway._prepare_body(body, "main-auto")
            self.assertEqual(out["messages"][0]["content"], expected,
                             "完整链路没改写：%r" % (poison,))
            self.assertEqual(body["messages"][0]["content"], poison)

    def test_switch_off_skips_generic_openings(self):
        """`MIRAIHUB_MARVIS_SANITIZE_TENCENT=0` 时逐字符透传、连对象都不换。"""
        os.environ[marvis_gateway.SANITIZE_TENCENT_ENV] = "0"
        for poison, _expected in self.CASES:
            out, hits = marvis_gateway.rewrite_tencent_fingerprints(poison)
            self.assertIs(out, poison)
            self.assertEqual(hits, 0)


class TopLevelSystemFieldTests(unittest.TestCase):
    """顶层 `system` 字段（Anthropic 形态）也必须过规则表。

    `/v1/messages` 会把顶层 system 搬进 `messages[0]`，但走
    `/v1/chat/completions` 的客户端可以直接带一个顶层 `system` —— 旧实现只
    遍历 `messages[]`，这份正文整段在改写作用域之外：同一句触发串换个承载
    字段就能打到上游，日志里 `rules_hit` 还是 0。
    """

    POISON = "You are a helpful assistant"
    EXPECTED = "You're a capable assistant"

    def setUp(self):
        self._env_backup = os.environ.get(marvis_gateway.SANITIZE_TENCENT_ENV)
        os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)

    def tearDown(self):
        if self._env_backup is None:
            os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)
        else:
            os.environ[marvis_gateway.SANITIZE_TENCENT_ENV] = self._env_backup

    def test_top_level_system_is_rewritten_and_stays_top_level(self):
        """改写后**仍在顶层**（上游没有角色归一化，字段不该被搬走）。"""
        body = {"model": "x", "system": self.POISON,
                "messages": [{"role": "user", "content": "ping"}]}
        out = marvis_gateway._prepare_body(body, "main-auto")
        self.assertEqual(out["system"], self.EXPECTED)
        self.assertIn("system", out, "顶层 system 被搬走了：%r" % (out,))
        self.assertEqual(body["system"], self.POISON, "调用方 body 不能被就地改")
        self.assertIs(out["messages"], body["messages"])

    def test_top_level_system_list_is_rewritten(self):
        """Anthropic 的数组形态同样逐段洗，非指纹段逐字不动。"""
        payload = [{"type": "text", "text": self.POISON},
                   {"type": "text", "text": "回答用中文。"}]
        out = marvis_gateway._prepare_body({"model": "x", "system": payload},
                                           "main-auto")
        self.assertEqual(out["system"][0]["text"], self.EXPECTED)
        self.assertEqual(out["system"][1]["text"], "回答用中文。")
        self.assertEqual(payload[0]["text"], self.POISON, "入参 list 不能被就地改")

    def test_zero_hit_top_level_system_is_the_same_object(self):
        """零命中：连字符串对象都不换（前缀缓存不能被无谓重建打掉）。"""
        text = "普通系统提示词，没有指纹。"
        out = marvis_gateway._prepare_body({"model": "x", "system": text},
                                           "main-auto")
        self.assertIs(out["system"], text)

    def test_other_shapes_pass_through(self):
        out = marvis_gateway._prepare_body({"model": "x", "system": 123},
                                           "main-auto")
        self.assertEqual(out["system"], 123)

    def test_top_level_and_message_hits_are_both_logged(self):
        """两处命中各记一条，且日志里不带原文。"""
        with self.assertLogs("admin.platform.marvis.gateway",
                             level="INFO") as captured:
            marvis_gateway._prepare_body(
                {"model": "x", "system": self.POISON,
                 "messages": [{"role": "system",
                               "content": "You are a coding assistant"}]},
                "main-auto")
        logged = "\n".join(captured.output)
        self.assertIn("rules_hit=1", logged)
        self.assertIn("顶层 system", logged)
        self.assertNotIn(self.POISON, logged, "日志里不能出现原文")


if __name__ == "__main__":
    unittest.main(verbosity=2)
