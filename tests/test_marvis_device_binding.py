# -*- coding: utf-8 -*-
"""设备绑定骨架（`admin/platform/marvis/services/device_binding.py`）的回归锁定。

## 这些断言在锁什么

本模块是**不接线的骨架**，所以它自己的正确性就是全部交付物：

1. **同源**：绑定一次之后，`require_device()` 返回的 guid / qimei36 必须与
   绑定时写进去的完全一致 —— 两个值分开取值正是这个模块要消灭的形状；
2. **没有兜底**：缺绑定就是 `DeviceBindingMissing`，即使环境里
   `MARVIS_QIMEI36` 亮着也不许拿它顶上（否则闸门形同虚设）；
3. **不做假绑定**：非法 guid / 非法 qimei36 / 白名单外的 source 一律抛
   `ValueError`，且失败时凭证里不能留下半条绑定；
4. **解绑回到「无绑定」**：`unbind_device()` 之后闸门必须立刻拒绝。

## 为什么按文件路径加载模块，而不是 `import admin.platform.marvis...`

`admin/platform/marvis/__init__.py` 在 import 时会跑 `install()`（登记平台 +
校验 qimei 池），把整条应用栈拖进来。而这个模块的设计前提恰恰是
「import 它不产生副作用」，所以这里用 `importlib` 直接加载文件——顺带
把「本文件不依赖包初始化」这件事也验了。

## 「加载无副作用」怎么验

`test_loading_does_not_touch_environment` 里那条断言看的是 `sys.modules` /
`os.environ` 这种**进程级全局状态**，所以它必须跑在一个**干净的解释器**里：
本文件被别的测试文件（例如 `tests/test_marvis_platform.py`，它在模块顶层
`from admin.platform.marvis.core import ...`）带着一起收集时，pytest 在收集阶段
就已经把 `admin.platform.marvis` 灌进了父进程的 `sys.modules`——拿父进程的
全局状态当断言前提，必然假红。改法：`subprocess` 起一个子解释器，在子进程里
加载、在子进程里回读，父进程脏不脏都不影响结论。

## 怎么跑

    python tests/test_marvis_device_binding.py

不连库、不读环境、不写盘：测试里的设备串全是 `aaa…3000…` 这种
自造值，没有任何真机串。
"""
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (ROOT / "admin" / "platform" / "marvis" / "services"
               / "device_binding.py")


def _load_module():
    """按文件路径加载被测模块（不触发 `admin.platform.marvis` 的包初始化）。"""
    spec = importlib.util.spec_from_file_location(
        "marvis_device_binding_under_test", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: `install()` 的宿主包：`admin/platform/marvis/__init__.py` 在模块级调它。
INSTALL_HOST = "admin.platform.marvis"

#: 子进程探针：在**全新解释器**里按文件路径加载被测模块，把「加载它到底动了
#: 什么」原样回传。父进程的 `sys.modules` / `os.environ` 一概不参与判定——
#: 那正是本用例原先假红的原因（别人在模块顶层 import，收集阶段就把宿主包灌进
#: 了父进程）。脚本走 `-c` 传入，不落临时文件。
_PROBE_SOURCE = r'''
import importlib.util
import json
import os
import sys

module_path = sys.argv[1]
INSTALL_HOST = sys.argv[2]

#: 导入审计钩子：`install()` 只在宿主包的 `__init__` 里被调用一次，所以
#: 「宿主包被 import」⇔「install() 跑了」。钩子是通用的导入观察器，因此可被
#: 证明是活的（下面拿 colorsys 做对照，见 probe_live）。
seen = []


def _on_import(event, args):
    if event == "import":
        seen.append(args[0])


sys.addaudithook(_on_import)

# 对照组：钩子必须真的收到事件，否则「没看到宿主包」什么都证明不了。
import colorsys  # noqa: E402,F401
probe_live = "colorsys" in seen

env_before = dict(os.environ)
spec = importlib.util.spec_from_file_location("marvis_device_binding_probe",
                                              module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
env_after = dict(os.environ)

json.dump({
    "probe_live": probe_live,
    "loaded": callable(getattr(module, "is_valid_guid", None)),
    "install_host_imports": [n for n in seen if n == INSTALL_HOST],
    "install_host_submodules": sorted(n for n in seen
                                      if n.startswith(INSTALL_HOST + ".")),
    "install_host_in_sys_modules": INSTALL_HOST in sys.modules,
    "admin_imports": sorted(n for n in seen
                            if n == "admin" or n.startswith("admin.")),
    "env_added": sorted(set(env_after) - set(env_before)),
    "env_changed": sorted(k for k in set(env_after) & set(env_before)
                          if env_after[k] != env_before[k]),
}, sys.stdout)
sys.stdout.write("\n")
'''


def _run_load_probe(timeout=120):
    """在独立解释器里加载一次被测模块，回传观测结果（不共享父进程状态）。"""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE_SOURCE, str(MODULE_PATH), INSTALL_HOST],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )
    if proc.returncode != 0:
        raise AssertionError("子进程加载失败（exit=%s）：\n%s"
                             % (proc.returncode, proc.stderr.strip()[-2000:]))
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError("子进程没有回传观测结果：%r" % proc.stdout)
    try:
        return json.loads(lines[-1])
    except ValueError:
        raise AssertionError("子进程回传的不是 JSON：%r" % lines[-1])


binding = _load_module()

#: 自造设备串：36 hex，第 24-27 位是格式标记（与 core/device.py 的规则一致）。
QIMEI_A = "a" * 24 + "3000" + "deadbeef"
QIMEI_B = "b" * 24 + "3000" + "cafebabe"
QIMEI_BAD_MARK = "a" * 24 + "1234" + "deadbeef"
GUID_A = "guid-aaaa-0001"
GUID_B = "guid-bbbb-0002"


class ModuleShapeTests(unittest.TestCase):
    """骨架的对外形状。"""

    def test_module_exposes_skeleton(self):
        for name in ("bind_device", "unbind_device", "require_device",
                     "device_of", "normalize_device", "is_valid_guid",
                     "is_valid_qimei36", "device_fingerprint",
                     "new_guid_seed"):
            self.assertTrue(callable(getattr(binding, name, None)),
                            "缺少函数：%s" % name)
        self.assertEqual(binding.DEVICE_KEY, "device")
        self.assertEqual(tuple(binding.REQUIRED_FIELDS), ("guid", "qimei36"))
        self.assertEqual(tuple(binding.SOURCES), ("login", "manual", "migrated"))

    def test_loading_does_not_touch_environment(self):
        # 加载本身不改环境、不建库、不拖应用栈：断言全部由**子进程**给出。
        # 子进程是干净的，所以别人在模块顶层 import 了什么，都影响不到这里；
        # 父进程只负责转发结论，不提供任何前提。
        probe = _run_load_probe()

        # 哨兵必须先是活的，否则「没看到宿主包」等于什么都没说。
        self.assertTrue(probe["probe_live"], "导入哨兵没在收事件，本断言不可信")
        self.assertTrue(probe["loaded"], "子进程里没加载出被测模块")

        # install() 住在 admin/platform/marvis/__init__.py：这个包一被 import
        # 就登记平台 + 校验 qimei 池。按文件路径加载不该碰它。
        self.assertEqual(
            probe["install_host_imports"], [],
            "按路径加载 device_binding 不该 import %s（install() 在那里）"
            % INSTALL_HOST)
        self.assertEqual(probe["install_host_submodules"], [])
        self.assertFalse(probe["install_host_in_sys_modules"])
        self.assertEqual(probe["admin_imports"], [],
                         "加载把 admin 应用栈拖进来了：%r"
                         % (probe["admin_imports"],))

        # 真正想测的东西：加载前后环境变量无新增、无改写。
        self.assertEqual(probe["env_added"], [], "加载新增了环境变量")
        self.assertEqual(probe["env_changed"], [], "加载改写了环境变量")


class ValidatorTests(unittest.TestCase):
    """guid / qimei36 的校验边界。"""

    def test_guid_bounds(self):
        self.assertFalse(binding.is_valid_guid(""))
        self.assertFalse(binding.is_valid_guid("x" * 7))
        self.assertTrue(binding.is_valid_guid("x" * 8))
        self.assertTrue(binding.is_valid_guid("x" * 64))
        self.assertFalse(binding.is_valid_guid("x" * 65))

    def test_guid_placeholders_rejected(self):
        for bad in ("0", "null", "NULL", " none ", "undefined", "unknown"):
            self.assertFalse(binding.is_valid_guid(bad), bad)
        # 大小写 / 空白规整后仍是合法值
        self.assertTrue(binding.is_valid_guid("  guid-aaaa-0001  "))

    def test_qimei36_requires_hex_of_36(self):
        self.assertTrue(binding.is_valid_qimei36(QIMEI_A))
        self.assertFalse(binding.is_valid_qimei36(""))
        self.assertFalse(binding.is_valid_qimei36(QIMEI_A[:-1]))
        self.assertFalse(binding.is_valid_qimei36(QIMEI_A + "a"))
        self.assertFalse(binding.is_valid_qimei36("z" * 36))

    def test_qimei36_requires_format_marker(self):
        # 36 hex 但标记位不对 —— core/device.py 的注释里记着这类值会被上游判非法
        self.assertFalse(binding.is_valid_qimei36(QIMEI_BAD_MARK))
        self.assertEqual(QIMEI_BAD_MARK[24:28], "1234")
        self.assertEqual(QIMEI_A[24:28], binding.QIMEI_MARK)


class BindTests(unittest.TestCase):
    """绑定 / 重复绑定 / 绑定失败不留痕。"""

    def test_bind_writes_and_returns_device(self):
        cred = {"openid": "acc-a"}
        device = binding.bind_device(cred, guid=GUID_A, qimei36=QIMEI_A,
                                     now=1763500000)
        self.assertEqual(device, {"guid": GUID_A, "qimei36": QIMEI_A,
                                  "bound_at": 1763500000, "source": "login"})
        self.assertEqual(cred["device"], device)

    def test_bind_normalizes_whitespace(self):
        cred = {}
        device = binding.bind_device(cred, guid="  " + GUID_A + "  ",
                                     qimei36="  " + QIMEI_A + "  ", now=1)
        self.assertEqual(device["guid"], GUID_A)
        self.assertEqual(device["qimei36"], QIMEI_A)

    def test_rebind_overwrites_previous_pair(self):
        cred = {}
        binding.bind_device(cred, guid=GUID_A, qimei36=QIMEI_A, now=100)
        binding.bind_device(cred, guid=GUID_B, qimei36=QIMEI_B,
                            source="manual", now=200)
        self.assertEqual(binding.device_of(cred),
                         {"guid": GUID_B, "qimei36": QIMEI_B,
                          "bound_at": 200, "source": "manual"})
        # 只剩一条绑定：没有历史残留、没有列表堆积
        self.assertEqual(len(binding.device_of(cred)), 4)

    def test_bind_rejects_bad_input_without_partial_write(self):
        for kwargs in (
            {"guid": "", "qimei36": QIMEI_A},
            {"guid": GUID_A, "qimei36": QIMEI_BAD_MARK},
            {"guid": GUID_A, "qimei36": QIMEI_A, "source": "import"},
        ):
            cred = {"openid": "acc-a"}
            with self.assertRaises(ValueError):
                binding.bind_device(cred, **kwargs)
            self.assertNotIn("device", cred, kwargs)

    def test_bind_requires_dict_credential(self):
        with self.assertRaises(TypeError):
            binding.bind_device(None, guid=GUID_A, qimei36=QIMEI_A)

    def test_source_whitelist_in_normalize(self):
        # source 不在白名单：设备串本身有效，只把来源记成 unknown
        cred = {}
        binding.bind_device(cred, guid=GUID_A, qimei36=QIMEI_A, now=5)
        cred["device"]["source"] = "stolen"
        self.assertEqual(binding.device_of(cred)["source"], "unknown")


class UnbindTests(unittest.TestCase):
    """解绑：回到「没有绑定」。"""

    def test_unbind_removes_and_reports(self):
        cred = {}
        binding.bind_device(cred, guid=GUID_A, qimei36=QIMEI_A)
        self.assertTrue(binding.unbind_device(cred))
        self.assertNotIn("device", cred)
        self.assertEqual(binding.device_of(cred), {})
        # 幂等：再删一次返回 False
        self.assertFalse(binding.unbind_device(cred))

    def test_unbind_closes_the_gate(self):
        cred = {}
        binding.bind_device(cred, guid=GUID_A, qimei36=QIMEI_A)
        self.assertTrue(binding.require_device(cred))
        binding.unbind_device(cred)
        with self.assertRaises(binding.DeviceBindingMissing):
            binding.require_device(cred)

    def test_unbind_tolerates_non_dict(self):
        self.assertFalse(binding.unbind_device(None))


class RequireDeviceTests(unittest.TestCase):
    """mint 前的闸门。"""

    def test_require_returns_same_pair_as_bind(self):
        cred = {}
        bound = binding.bind_device(cred, guid=GUID_A, qimei36=QIMEI_A, now=42)
        got = binding.require_device(cred)
        self.assertEqual(got, bound)
        # 同源：不是「各自取一次」拼出来的
        self.assertEqual((got["guid"], got["qimei36"]), (GUID_A, QIMEI_A))

    def test_require_raises_with_code_and_account(self):
        with self.assertRaises(binding.DeviceBindingMissing) as ctx:
            binding.require_device({"openid": "acc-a"}, account_id=402)
        err = ctx.exception
        self.assertEqual(err.code, "checkin_required")
        self.assertEqual(err.account_id, 402)
        self.assertIn("设备绑定", err.message)
        self.assertIsInstance(err, Exception)

    def test_unknown_account_and_empty_credential(self):
        # 未知账号：凭证整个不存在
        self.assertEqual(binding.device_of(None), {})
        self.assertEqual(binding.device_of({}), {})
        with self.assertRaises(binding.DeviceBindingMissing) as ctx:
            binding.require_device(None, account_id=9999)
        self.assertEqual(ctx.exception.account_id, 9999)
        # 账号存在但没绑过设备（只有凭证）
        with self.assertRaises(binding.DeviceBindingMissing):
            binding.require_device({"openid": "acc-unknown",
                                    "access_token": "x"}, account_id=1)

    def test_malformed_binding_is_treated_as_missing(self):
        for raw in ({"guid": GUID_A, "qimei36": QIMEI_BAD_MARK},
                    {"guid": "x", "qimei36": QIMEI_A},
                    {"guid": GUID_A},
                    "guid+qimei",
                    ["guid", "qimei"]):
            cred = {"device": raw}
            self.assertEqual(binding.device_of(cred), {}, raw)
            with self.assertRaises(binding.DeviceBindingMissing):
                binding.require_device(cred, account_id=7)

    def test_no_environment_fallback(self):
        # 环境里就算配了共享设备串，也不许被当成绑定
        with mock.patch.dict(os.environ,
                             {"MARVIS_QIMEI36": QIMEI_A,
                              "MARVIS_QIMEI36_acc_a": QIMEI_B}):
            with self.assertRaises(binding.DeviceBindingMissing):
                binding.require_device({"openid": "acc_a"}, account_id=3)


class FingerprintTests(unittest.TestCase):
    """指纹摘要：可对账、不可还原。"""

    def test_fingerprint_is_stable_and_short(self):
        device = {"guid": GUID_A, "qimei36": QIMEI_A}
        first = binding.device_fingerprint(device)
        self.assertEqual(first, binding.device_fingerprint(dict(device)))
        self.assertEqual(len(first), 16)
        self.assertNotIn(GUID_A, first)
        self.assertNotIn(QIMEI_A, first)

    def test_fingerprint_differs_by_device(self):
        a = binding.device_fingerprint({"guid": GUID_A, "qimei36": QIMEI_A})
        b = binding.device_fingerprint({"guid": GUID_B, "qimei36": QIMEI_B})
        self.assertNotEqual(a, b)

    def test_fingerprint_empty_input(self):
        self.assertEqual(binding.device_fingerprint({}), "")
        self.assertEqual(binding.device_fingerprint(None), "")


class GuidSeedTests(unittest.TestCase):
    """new_guid_seed 只保证形状，不是采集替代品。"""

    def test_seed_is_valid_and_unique(self):
        one = binding.new_guid_seed()
        two = binding.new_guid_seed()
        self.assertTrue(binding.is_valid_guid(one))
        self.assertNotEqual(one, two)


if __name__ == "__main__":
    unittest.main(verbosity=2)
