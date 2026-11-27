"""APK 移动端页面的构建产物回归测试。

历史：本文件原名 `test_console_boot.py`，上半部分 `ConsoleShellTests` 锁的是
`/admin` 下发旧控制台时注入 `<html class="console-authed">`（避免刷新先闪一下
登录卡片）。后台前端合并后那条机制已整体移除（`_console_shell` /
`_CONSOLE_SHELL_CACHE` / `stamp_asset_urls` 一并删除），入口行为改由
`tests/test_ui_v2_serving.py` 覆盖，故那段测试同步删除。

保留的部分仍然有效，而且比以往更关键：`admin/console/` 不再是 Web 后台，
但**仍是 Android APK 的构建输入**（`scripts/build_android.py:218` 的
`copytree(admin/console)` + `mobile_js()` / `mobile_html()` 改写），
所以这里的断言继续管用。

它盯的是「构建阶段静默失配」：`scripts/build_android.py` 用
`str.replace('<link ... />', ...)` 注入 mobile.css，锚点找不到时
`str.replace` 不报错，于是 mobile.css 从来没进过 APK，表现是 APK 设置页整屏没样式。
"""
import importlib.util
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _load_build_android():
    path = ROOT / "scripts" / "build_android.py"
    spec = importlib.util.spec_from_file_location("build_android_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MobileHtmlTests(unittest.TestCase):
    """`mobile_html()` 必须真的产出带移动端样式的页面。

    这里刻意不写「断言某条字符串替换执行过」，而是断言**结果**：
    mobile.css 在页面里、排在桌面样式之后、且设置页的面板确实换成了移动端版本。
    这样即使将来控制台改版，只要产物仍然正确，测试就不会误报。
    """

    @classmethod
    def setUpClass(cls):
        cls.module = _load_build_android()
        cls.source = (ROOT / "admin/console/index.html").read_text(encoding="utf-8")
        cls.html = cls.module.mobile_html(cls.source)
        # 控制台脚本已外置，桌面专属改写现在作用在 console-app.js 上。
        cls.js_source = (ROOT / "admin/console/console-app.js").read_text(encoding="utf-8")
        cls.js = cls.module.mobile_js(cls.js_source)

    def test_mobile_stylesheet_is_injected_after_desktop_styles(self):
        self.assertIn('/static/mobile.css', self.html)
        mobile_at = self.html.index('/static/mobile.css')
        self.assertGreater(mobile_at, self.html.index('/admin/assets/console.css'))
        # 必须仍在 </head> 之前，否则会被浏览器当作正文而忽略。
        self.assertLess(mobile_at, self.html.index('</head>'))
        # 样式文件本身要存在，否则 APK 里就是 404。
        self.assertTrue((ROOT / "android/web/mobile.css").is_file())

    def test_mobile_scripts_are_injected(self):
        for name in ("/static/mobile.js", "/static/mobile_settings.js"):
            with self.subTest(script=name):
                self.assertIn(name, self.html)
                self.assertLess(self.html.index(name), self.html.index('</body>'))
                self.assertTrue((ROOT / "android/web" / name.rsplit('/', 1)[-1]).is_file())

    def test_mobile_settings_panel_replaces_desktop_sync_panel(self):
        self.assertIn('id="panel-sync"', self.html)
        self.assertIn('mobile-settings-card', self.html)
        self.assertIn('</svg> 设置</button>', self.html)
        self.assertNotIn('线上同步设置', self.html)

    def test_desktop_only_panels_and_tools_are_removed(self):
        # 注意不再断言 `scanLocal()` 完全不出现：平台管理的「扫描本机」按钮
        # 会委托给它（带 typeof 保护，移动端构建删掉实现后自动降级）。
        for fragment in ('id="panel-client"', 'id="panel-settings"',
                         'async function injectAcc(',
                         'async function loadAppSource('):
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, self.html)
        # TABS 已随脚本外置搬到 console-app.js，改在 mobile_js() 的产物上验证。
        tabs = re.search(r'const TABS = \[(.*?)\];', self.js).group(1)
        self.assertNotIn('"client"', tabs)
        self.assertNotIn('"settings"', tabs)

    def test_missing_marker_raises_instead_of_silently_skipping(self):
        # 模拟控制台改版：样式链接被改名后，注入点必须报错而不是悄悄跳过。
        module = self.module
        broken = self.source.replace('</head>', '')
        with self.assertRaises(module.MobileHtmlError):
            module.mobile_html(broken)

    def test_replace_once_reports_which_rule_failed(self):
        module = self.module
        with self.assertRaises(module.MobileHtmlError) as caught:
            module._replace_once('<html></html>', 'no-such-marker', 'x', '测试锚点')
        self.assertIn('测试锚点', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
