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
    src = re.sub(r"\(\?:([^()|]*)\|[^()]*\)", r"\1", src)  # 交替组取首个分支
    src = re.sub(r"\\s[*+]", " ", src)            # 空白类归一成单个空格
    src = re.sub(r"([A-Za-z])\?", r"\1", src)     # 单字符可选取该字符
    src = re.sub(r",\?", "", src)                  # 可选逗号取缺席
    src = re.sub(r"\\b", "", src)                  # 词边界 \b 不贡献字符
    src = re.sub(r"\\(.)", r"\1", src)           # 其余转义还原成字面量
    src = re.sub(r"^\^", "", src)               # 行首锚点 ^ 不贡献字符
    return src


class RuleTableTests(unittest.TestCase):
    """规则表本身：条数、每条都能命中自己的样例。"""

    def test_rule_table_has_all_fingerprints(self):
        """少一条就是留一个随时会踩的 403；2026-11-20 补 4b / 4c 后扩到 15 条。"""
        self.assertEqual(len(marvis_gateway._TENCENT_FINGERPRINT_RULES), 15)

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


class FindLiteralDriftTests(unittest.TestCase):
    """⚠️ 2026-11-16 缺陷的直接守卫：`find` 被截断 / 写错首词 ⇒ 规则**静默空跑**。

    那次线上表里有两条**永不命中**的规则：

      * 规则 1 首词写成 `Default branch`（插件真值 `Main branch`）；
      * 规则 3 只留半句 `To give feedback, users should report the issue at `
        （插件真值是 **99 字符**整句，尾巴是 issue URL）。

    危害在「症状为假」：上游认的是**整句字面量**，砍掉前半或后半就放行，写短了
    等于空跑 —— 表里登记了、日志里 `rules_hit` 恒 0、上游仍每轮 403/4100406，
    与「完全没登记」同形，极难归因。

    本类**只写片段与长度**，不抄整条毒串（保持本文件「样例由规则反解」的口径），
    但断言是**硬编码**的：插件表哪天变了，这里该失败 —— 那正是我们要的提醒。
    """

    #: 插件 `CONTENT_FINGERPRINTS` 7 条的真值长度（2026-11-16 逐字对齐）。
    #: 第 4 条是 `findRegex` 的源码长度（2026-11-18 扩成 Harness|Runtime 后
    #: 从 94 → 106），其余是 `find` 字面量长度。
    PLUGIN_LENGTHS = (47, 56, 99, 106, 221, 111, 26)

    #: 插件 7 条 → **本表下标**：2026-11-20 在规则 4 之后插入了 4b / 4c，
    #: 插件第 5-7 条（本表规则 5-7）的下标因此整体后移 2 位。
    #: 别再按位置切前 7 条 —— 那会把 4b 当成插件第 5 条，断言虚高。
    PLUGIN_TO_LOCAL = (0, 1, 2, 3, 6, 7, 8)

    @staticmethod
    def _literal(pattern):
        """把 `re.escape` 出来的正则还原成字面量（仅对字面量规则有意义）。"""
        return re.sub(r"\\(.)", r"\1", pattern.pattern)

    def test_rule1_first_word_is_main_not_default(self):
        """规则 1：首词必须是 `Main`（写成 `Default` 就永不命中）。"""
        pattern = marvis_gateway._TENCENT_FINGERPRINT_RULES[0][0]
        literal = self._literal(pattern)
        self.assertEqual(literal.split()[0], "Main",
                         "规则 1 首词写错就永不命中：%r" % (literal,))
        self.assertTrue(literal.startswith("Main branch ("), literal)
        self.assertNotIn("Default", literal)
        self.assertEqual(len(literal), 47)
        # 反向：上一版的错字串**不能**被这条规则命中（否则说明规则被改宽了）。
        self.assertIsNone(
            pattern.search("Default branch (you will usually use this for PRs)"))

    def test_rule3_is_the_full_99_char_sentence(self):
        """规则 3：必须是 99 字符整句（含 issue URL）；截断成半句等于空跑。"""
        pattern = marvis_gateway._TENCENT_FINGERPRINT_RULES[2][0]
        literal = self._literal(pattern)
        self.assertEqual(len(literal), 99,
                         "规则 3 长度不是 99（被截断了？）：%r" % (literal,))
        self.assertTrue(
            literal.startswith(
                "To give feedback, users should report the issue at https://"),
            "规则 3 缺 URL 尾巴，必然空跑：%r" % (literal,))
        self.assertTrue(literal.endswith("anthropics/claude-code/issues"))
        # 反向：上一版的半句前缀**不能**被这条规则命中。
        self.assertIsNone(pattern.search(
            "To give feedback, users should report the issue at "))

    def test_no_rule_lost_characters_against_plugin(self):
        """前 7 条规则的长度必须与插件逐条一致 —— 任何一条被短写，这里就红。

        注意第 4 条比的是「findRegex 的正则源码」长度（它本来就不是字面量），
        其余六条比「re.escape 之前的字面量」长度。混为一谈会让断言虚高
        —— 正则里的转义反斜杠也会被算进长度。
        """
        rules = marvis_gateway._TENCENT_FINGERPRINT_RULES
        self.assertEqual(len(rules), 15)
        # 下标映射的自检：本表第 7 条必须仍是插件第 5 条（DSH Web 运行面两句）。
        self.assertTrue(
            self._literal(rules[self.PLUGIN_TO_LOCAL[4]][0]).startswith(
                "You are interacting with the user through the "
                "DeepSeek Harness Web GUI"),
            "下标映射错位：本表第 7 条已不是插件第 5 条")
        lengths = []
        for plugin_index, local_index in enumerate(self.PLUGIN_TO_LOCAL):
            pattern = rules[local_index][0]
            if plugin_index == 3:
                lengths.append(len(pattern.pattern))
            else:
                lengths.append(len(self._literal(pattern)))
        self.assertEqual(tuple(lengths), self.PLUGIN_LENGTHS)


class FollowUpSuggestionPrefixRuleTests(unittest.TestCase):
    """规则 8：DSH「follow-up suggestion」系统提示词首句（4100406）。

    与上面几类不同，这里**必须**写字面量：要锁的恰恰是**长度边界**（31 放行 /
    32 拦截）、**前缀锚定**（不是 contains）、前导空白与大小写 —— 这些性质没法
    从正则反解，只能拿真串逐字对比。

    实测结论（2026-11-16 真机二分 26 次）：
      · 命中长度恰好 32：`...suggestio`(31) → 200，`...suggestion`(32) → 403；
      · 只查 system 角色，且**前缀锚定**：同一串前面加一句 → 200；
      · 前导空白先 strip，大小写不敏感。
    """

    #: 上游黑名单的最小触发串（32 字符）。
    POISON = "You are the follow-up suggestion"
    #: 规则改写后的文本（the → a：破坏前缀、不删句子）。
    REWRITTEN = "You are a follow-up suggestion"

    def setUp(self):
        self._env_backup = os.environ.get(marvis_gateway.SANITIZE_TENCENT_ENV)
        os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)

    def tearDown(self):
        if self._env_backup is None:
            os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)
        else:
            os.environ[marvis_gateway.SANITIZE_TENCENT_ENV] = self._env_backup

    def test_poison_at_system_head_is_rewritten(self):
        """毒串在 system 开头 → 走完整链路被改写，且改写后不再以毒前缀开头。"""
        original = self.POISON + " engine. You suggest the next user message."
        body = {"model": "x",
                "messages": [{"role": "system", "content": original}]}

        out = marvis_gateway._prepare_body(body, "main-auto")

        rewritten = out["messages"][0]["content"]
        self.assertNotEqual(rewritten, original)
        self.assertFalse(rewritten.lower().startswith(self.POISON.lower()))
        self.assertTrue(rewritten.startswith(self.REWRITTEN))
        # 只动首句那几个词，尾部正文逐字保留。
        self.assertTrue(
            rewritten.endswith(" engine. You suggest the next user message."))
        # 调用方 body 不被就地改。
        self.assertEqual(body["messages"][0]["content"], original)

    def test_thirty_two_char_boundary_hits_and_thirty_one_does_not(self):
        """32 字符命中、31 字符放行：这条边界是真机二分出来的。"""
        self.assertEqual(len(self.POISON), 32)
        short = self.POISON[:-1]
        self.assertEqual(len(short), 31)

        out, hits = marvis_gateway.rewrite_tencent_fingerprints(short)
        self.assertIs(out, short)
        self.assertEqual(hits, 0)

        out, hits = marvis_gateway.rewrite_tencent_fingerprints(self.POISON)
        self.assertEqual(hits, 1)
        self.assertNotEqual(out, self.POISON)
        self.assertFalse(out.lower().startswith(self.POISON.lower()))

    def test_prefix_anchored_not_contains(self):
        """前缀锚定：同一串出现在句中 → 上游放行，这里也**不许**改。"""
        text = "System note. " + self.POISON + " engine"
        out, hits = marvis_gateway.rewrite_tencent_fingerprints(text)
        self.assertIs(out, text)
        self.assertEqual(hits, 0)

    def test_case_insensitive(self):
        """上游大小写不敏感：全小写同样要改。"""
        text = self.POISON.lower() + " engine"
        out, hits = marvis_gateway.rewrite_tencent_fingerprints(text)
        self.assertEqual(hits, 1)
        self.assertNotEqual(out, text)
        self.assertFalse(out.lower().startswith(self.POISON.lower()))

    def test_leading_whitespace_is_covered(self):
        """前导空白先 strip：空格 / 换行 / 制表符开头的毒串照样命中。"""
        for prefix in (" ", "  ", chr(10), "  " + chr(10), chr(9)):
            text = prefix + self.POISON + " engine"
            out, hits = marvis_gateway.rewrite_tencent_fingerprints(text)
            self.assertEqual(hits, 1, "前导空白 %r 没命中" % (prefix,))
            self.assertNotEqual(out, text)
            self.assertFalse(out.lower().startswith(self.POISON.lower()))

    def test_control_sentence_with_other_noun_is_not_rewritten(self):
        """对照：只换名词（question）→ 真机 200，这里也不许改。"""
        text = "You are the follow-up question engine"
        out, hits = marvis_gateway.rewrite_tencent_fingerprints(text)
        self.assertIs(out, text)
        self.assertEqual(hits, 0)

    def test_other_auxiliary_prompts_are_not_rewritten(self):
        """其余辅助提示词一个字都不许动（规则不许写宽）。"""
        for text in (
                "You complete the message a USER is drafting in a coding agent chat UI.",
                "Generate a title for a coding-assistant conversation.",
                "你是记忆库提炼助手。根据下面的会话内容，提炼出值得长期记住的事实。",
                "You are the memory curation assistant."):
            out, hits = marvis_gateway.rewrite_tencent_fingerprints(text)
            self.assertIs(out, text, "被误改：%r" % (text,))
            self.assertEqual(hits, 0)

    def test_rule_is_eighth_and_prefix_anchored(self):
        """规则 8 必须存在、必须锚在行首、必须大小写不敏感。"""
        rules = marvis_gateway._TENCENT_FINGERPRINT_RULES
        self.assertEqual(len(rules), 15)
        # 4b / 4c 插在规则 4 之后：规则 8 的下标由 7 后移到 9。
        pattern, replacement = rules[9]
        self.assertTrue(pattern.pattern.startswith("^"),
                        "规则 8 丢了行首锚点：%r" % (pattern.pattern,))
        self.assertEqual(pattern.flags & re.IGNORECASE, re.IGNORECASE)
        self.assertEqual(replacement, self.REWRITTEN)
        # 锚点真的生效：句中同一串不命中，行首命中。
        self.assertIsNone(pattern.search("System note. " + self.POISON))
        self.assertIsNotNone(pattern.search(self.POISON))


class GenericAssistantOpeningRuleTests(unittest.TestCase):
    """规则 9-13：2026-11-18 上游新增的通用开场白黑名单。

    真机对照（同一 Key / 同一模型，交替发 24 次）：不带 system 12 次全 200，
    带 system 12 次全 403；长段自定义提示词 200 ⇒ 命中的是这些固定开头。

    这里**必须写字面量**：要锁的是「上游登记的那几条原串」以及改写后的
    等价表述，没法从正则反解。规则一改，这里就该红。
    """

    #: (上游触发串, 网关改写后的文本)
    CASES = (
        ("You are an AI agent powered by DeepSeekRuntime.",
         "You are a capable AI coding assistant, powered by a large language model."),
        ("You are an AI agent powered by DeepSeek Harness.",
         "You are a capable AI coding assistant, powered by a large language model."),
        ("You are a helpful assistant",
         "You are a capable assistant"),
        ("You are a helpful AI assistant",
         "You are a capable AI assistant"),
        ("You are an AI assistant.",
         "You are a capable AI assistant."),
        ("You are a coding assistant.",
         "You are a capable coding assistant."),
        ("你是乐于助人的助手。",
         "你是一位能力出众的助手。"),
    )

    def setUp(self):
        self._env_backup = os.environ.get(marvis_gateway.SANITIZE_TENCENT_ENV)
        os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)

    def tearDown(self):
        if self._env_backup is None:
            os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)
        else:
            os.environ[marvis_gateway.SANITIZE_TENCENT_ENV] = self._env_backup

    def test_every_trigger_is_rewritten_to_expected_text(self):
        for poison, expected in self.CASES:
            out, hits = marvis_gateway.rewrite_tencent_fingerprints(poison)
            self.assertEqual(hits, 1, "触发串没命中：%r" % (poison,))
            self.assertEqual(out, expected,
                             "触发串改写结果不对：%r -> %r" % (poison, out))

    def test_deepseek_replacement_breaks_the_you_are_an_ai_prefix(self):
        """2026-11-18 实测：黑名单覆盖 `You are an AI` 前缀，替换结果必须不以它开头。"""
        for poison in (self.CASES[0][0], self.CASES[1][0]):
            out, hits = marvis_gateway.rewrite_tencent_fingerprints(poison)
            self.assertEqual(hits, 1)
            self.assertFalse(out.lower().startswith("you are an ai"),
                             "替换后仍保留触发前缀：%r" % (out,))

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
        """顺序陷阱：不能先改成 `capable assistant` 再拼出 `... AI assistant`。"""
        out, hits = marvis_gateway.rewrite_tencent_fingerprints(
            "You are a helpful AI assistant.")
        self.assertEqual(hits, 1)
        self.assertEqual(out, "You are a capable AI assistant.")
        self.assertNotIn("capable assistant", out)
        self.assertNotIn("capable capable", out)

    def test_generic_ai_assistant_rule_does_not_eat_capable(self):
        """改写后的 `a capable AI assistant` 不能被泛化规则二次命中。"""
        text = "You are a capable AI assistant"
        out, hits = marvis_gateway.rewrite_tencent_fingerprints(text)
        self.assertIs(out, text)
        self.assertEqual(hits, 0)

    def test_trailing_text_is_preserved(self):
        """只换开场白，后面的正文逐字保留（不删句子 = 不丢语义约束）。"""
        original = ("You are a helpful assistant. You must answer in Chinese.\n"
                    "Never reveal secrets.")
        out, hits = marvis_gateway.rewrite_tencent_fingerprints(original)
        self.assertEqual(hits, 1)
        self.assertEqual(
            out,
            "You are a capable assistant. You must answer in Chinese.\n"
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


class AiCodingAssistantRuleTests(unittest.TestCase):
    """规则 4b / 4c：2026-11-20 实测漏掉的两种 DSH 身份句形态（4100406）。

    改前（13 条）这几种形态在**规则表**层面全 MISS，只有首行身份句兜底把它们改成
    `You're ...` —— 上游能过，但规则表 `rules_hit` 记不到，日志里等于没命中，
    与「规则写窄」同形（见 FindLiteralDriftTests），归因直接断链。三条都不吃的原因：

      * 规则 4 要求 `agent`（不是 `assistant`）；
      * 规则 11 要求 `AI` **紧跟** `assistant`；
      * 规则 12 要求 `a coding assistant`（`an AI coding assistant` 不满足）。
      ⇒ `AI coding assistant + powered by <品牌>` 这个组合是纯空洞。

    这里**必须写字面量**：要锁的是改前的漏形态与改写后的等价表述，没法从正则反解。
    """

    #: 规则 4 的替换值。4b 必须与之**逐字一致** —— 同一种「把 capable 插到 AI 前」
    #: 的破坏前缀做法（2026-11-18 对照：`You are an AI` 前缀才是被拦的东西）。
    DEEPSEEK_REWRITE = ("You are a capable AI coding assistant, "
                        "powered by a large language model")

    #: 规则 4b：`assistant` 形态（身份在前、品牌在后），两种品牌名都要吃。
    ASSISTANT_CASES = (
        "You are an AI coding assistant, powered by DeepSeek Harness",
        "You are an AI coding assistant, powered by the DeepSeekRuntime",
    )

    #: 规则 4c：品牌前置形态（身份名词在后），只改前缀。
    BRAND_FIRST_CASE = "You are DeepSeek Harness, an agentic coding tool"

    def setUp(self):
        self._env_backup = os.environ.get(marvis_gateway.SANITIZE_TENCENT_ENV)
        os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)

    def tearDown(self):
        if self._env_backup is None:
            os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)
        else:
            os.environ[marvis_gateway.SANITIZE_TENCENT_ENV] = self._env_backup

    def test_rule_4b_replacement_is_identical_to_rule_4(self):
        """4b 的替换值必须与规则 4 逐字相同，位置必须紧跟规则 4（下标 4）。"""
        rules = marvis_gateway._TENCENT_FINGERPRINT_RULES
        self.assertEqual(rules[4][1], rules[3][1])
        self.assertEqual(rules[4][1], self.DEEPSEEK_REWRITE)
        self.assertEqual(rules[5][1], "You are a capable")

    def test_assistant_variants_are_rewritten_by_the_rule_table(self):
        """规则 4b：各命中 1 条、结果与规则 4 相同、开头不再是 `You are an AI`。"""
        for poison in self.ASSISTANT_CASES:
            out, hits = marvis_gateway.rewrite_tencent_fingerprints(poison)
            self.assertEqual(hits, 1, "规则 4b 没命中（又空跑了）：%r" % (poison,))
            self.assertEqual(out, self.DEEPSEEK_REWRITE,
                             "规则 4b 改写结果不对：%r -> %r" % (poison, out))
            self.assertFalse(out.lower().startswith("you are an ai"),
                             "替换后仍保留触发前缀：%r" % (out,))

    def test_brand_first_variant_keeps_the_identity_noun(self):
        """规则 4c：只改前缀，`agentic coding tool` 逐字保留。"""
        out, hits = marvis_gateway.rewrite_tencent_fingerprints(self.BRAND_FIRST_CASE)
        self.assertEqual(hits, 1)
        self.assertEqual(out, "You are a capable agentic coding tool")
        self.assertTrue(out.endswith("agentic coding tool"))

    def test_rule_4c_needs_the_comma_and_the_article(self):
        """4c 不许写宽：品牌后没有「逗号 + 冠词」时一个字都不改。

        没有这条，「`..., and you must ...`」会被 `an?` 吃成
        `a capable d you must ...`（真红用例）。
        """
        pattern = marvis_gateway._TENCENT_FINGERPRINT_RULES[5][0]
        for text in ("You are DeepSeek Harness users who need help.",
                     "You are DeepSeek Harness and the tool is running.",
                     "You are DeepSeek Harness, and you must answer in Chinese."):
            self.assertIsNone(pattern.search(text), "规则 4c 写宽了：%r" % (text,))

    def test_agent_without_brand_suffix_still_hits_once(self):
        """第四种形态回归保护：`agent + powered by DeepSeek`（无品牌后缀）。

        规则 4 带品牌后缀 ⇒ 规则表层面**有意**不吃它（放宽会把普通正文吃掉）；
        但它仍被首行身份句兜底命中 1 次，开头同样不再是 `You are an AI`。
        这里锁的是「不会被漏掉」，同时锁住规则 4 / 4b 的边框。
        """
        poison = "You are an AI coding agent powered by DeepSeek"
        rules = marvis_gateway._TENCENT_FINGERPRINT_RULES
        self.assertIsNone(rules[3][0].search(poison),
                          "规则 4 被写宽了（丢了品牌后缀要求）")
        self.assertIsNone(rules[4][0].search(poison), "规则 4b 不该吃 agent 形态")
        out, hits = marvis_gateway.rewrite_tencent_fingerprints(poison)
        self.assertEqual(hits, 1)
        self.assertEqual(out, "You're an AI coding agent powered by DeepSeek")
        self.assertFalse(out.lower().startswith("you are an ai"))

    def test_natural_sentence_with_brand_is_untouched(self):
        """反向：不带身份句的自然文本必须原样返回（防 4b / 4c 写宽误伤）。"""
        for text in ("DeepSeek Harness is the runtime behind this tool.",
                     "The DeepSeek Harness Web GUI is served on port 2305.",
                     "本句是中文说明，不含任何身份句。"):
            out, hits = marvis_gateway.rewrite_tencent_fingerprints(text)
            self.assertIs(out, text, "被误改：%r -> %r" % (text, out))
            self.assertEqual(hits, 0)


class TopLevelSystemFieldTests(unittest.TestCase):
    """顶层 `system` 字段（Anthropic 风格）也必须过规则表。"""

    def setUp(self):
        self._env_backup = os.environ.get(marvis_gateway.SANITIZE_TENCENT_ENV)
        os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)

    def tearDown(self):
        if self._env_backup is None:
            os.environ.pop(marvis_gateway.SANITIZE_TENCENT_ENV, None)
        else:
            os.environ[marvis_gateway.SANITIZE_TENCENT_ENV] = self._env_backup

    def test_top_level_system_is_rewritten(self):
        poison = "You are a helpful assistant"
        body = {"model": "x", "system": poison,
                "messages": [{"role": "user", "content": "ping"}]}
        out = marvis_gateway._prepare_body(body, "main-auto")
        self.assertEqual(out["system"], "You are a capable assistant")
        self.assertEqual(body["system"], poison, "调用方 body 不能被就地改")
        self.assertIs(out["messages"], body["messages"])

    def test_top_level_system_zero_hit_returns_same_object(self):
        text = "普通系统提示词，没有指纹。"
        out = marvis_gateway._prepare_body({"model": "x", "system": text},
                                           "main-auto")
        self.assertIs(out["system"], text)

    def test_non_string_top_level_system_passes_through(self):
        payload = [{"type": "text", "text": "You are a helpful assistant"}]
        out = marvis_gateway._prepare_body({"model": "x", "system": payload},
                                           "main-auto")
        self.assertIs(out["system"], payload)

    def test_hits_from_messages_and_top_level_system_are_summed(self):
        body = {"model": "x",
                "system": "You are a helpful assistant",
                "messages": [{"role": "system",
                              "content": "You are an AI assistant."}]}
        with self.assertLogs("admin.platform.marvis.gateway",
                             level="INFO") as captured:
            out = marvis_gateway._prepare_body(body, "main-auto")
        self.assertEqual(out["system"], "You are a capable assistant")
        self.assertEqual(out["messages"][0]["content"],
                         "You are a capable AI assistant.")
        self.assertIn("rules_hit=2", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main(verbosity=2)
