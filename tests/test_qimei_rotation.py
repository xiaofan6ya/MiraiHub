"""deploy/qimei-rotate 的静态自检：unit/timer 语义 + 两个脚本的行为断言。

这组测试**不需要 bash**：全部用文本解析断言（本机是 Windows，没有 bash 也没有 WSL
发行版）。唯一需要 shell 的是 bash -n 语法检查，能找到 bash 或装好发行版的 WSL 时跑，
否则 skip 并注明原因 —— 那一步属于 Linux 上部署前的门禁。

跑法：python -m unittest tests.test_qimei_rotation -v
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_DIR = REPO_ROOT / "deploy" / "qimei-rotate"
ROTATE_SH = DEPLOY_DIR / "rotate_all_qimei.sh"
AUTO_SH = DEPLOY_DIR / "auto_rotate_on_banned.sh"
SERVICE = DEPLOY_DIR / "qimei-rotate.service"
TIMER = DEPLOY_DIR / "qimei-rotate.timer"
README = DEPLOY_DIR / "README.md"
DOC = REPO_ROOT / "docs" / "marvis-qimei-rotation.md"
GEN_SERVICE = REPO_ROOT / "deploy" / "qimei-gen" / "qimei-gen.service"

QIMEI_SHAPE = r"^[0-9a-f]{24}3000[0-9a-f]{8}$"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def unit_keys(text: str) -> dict:
    """把 unit 文件解析成 {key: [values...]}（忽略注释 / 空行 / section 头）。

    systemd 允许同名键重复（@@Environment=@@ 就有多条），所以值收成列表。
    """
    keys: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        keys.setdefault(key.strip(), []).append(value.strip())
    return keys


def unit_one(keys: dict, key: str) -> str:
    values = keys.get(key) or []
    if len(values) != 1:
        raise AssertionError("key=%s 期望恰好 1 条，实际 %r" % (key, values))
    return values[0]


def heredoc_tags(text: str) -> list:
    """按出现顺序取 <<'TAG' / <<TAG 的标签，用于断言每个 heredoc 都闭合。"""
    return re.findall(r"<<-?\s*'?([A-Za-z_][A-Za-z0-9_]*)'?", text)


def assert_heredocs_closed(case: unittest.TestCase, text: str, name: str) -> None:
    """每个 heredoc 起头都必须有一行与标签完全相同的结束行。"""
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        match = re.search(r"<<-?\s*'?([A-Za-z_][A-Za-z0-9_]*)'?", line)
        if not match:
            continue
        tag = match.group(1)
        rest = lines[idx + 1:]
        if not any(item.strip() == tag for item in rest):
            case.fail("%s: heredoc %s 没有闭合行（从第 %d 行起）" % (name, tag, idx + 1))


class AssetsPresentTests(unittest.TestCase):
    def test_assets_exist(self):
        for path in (ROTATE_SH, AUTO_SH, SERVICE, TIMER, README, DOC):
            with self.subTest(path=str(path)):
                self.assertTrue(path.is_file(), "缺文件：%s" % path)
        # 生成器 unit 属部署侧可选资产：老基线没有，别把它当必交付。
        if not GEN_SERVICE.is_file():
            self.skipTest("旧基线不含 deploy/qimei-gen/qimei-gen.service")
        self.assertTrue(GEN_SERVICE.is_file(), "缺文件：%s" % GEN_SERVICE)

    def test_scripts_are_lf_and_have_shebang(self):
        for path in (ROTATE_SH, AUTO_SH):
            text = read_text(path)
            with self.subTest(script=path.name):
                self.assertTrue(text.startswith("#!/usr/bin/env bash"), "shebang 不是 env bash")
                self.assertNotIn("\r", text, "脚本必须是 LF（CRLF 在 Linux 上会 shebang 失败）")

    def test_scripts_are_strict(self):
        for path in (ROTATE_SH, AUTO_SH):
            with self.subTest(script=path.name):
                self.assertIn("set -uo pipefail", read_text(path))


class RotateScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read_text(ROTATE_SH)

    def test_paths_are_parameterized(self):
        text = self.text
        self.assertIn("MIRAIHUB_DIR:-/home/admin/MiraiHub", text)
        self.assertIn("CONTAINER:-miraihub-admin-1", text)
        self.assertIn("QIMEI_STATE_DIR:-$MIRAIHUB_DIR/admin/platform/marvis/core", text)
        self.assertIn("QIMEI_LOG_DIR:-/var/log/qimei-rotate", text)
        # 表路径必须由 QIMEI_STATE_DIR 派生，不能另写死
        self.assertIn('TABLE=$QIMEI_STATE_DIR/qimei_table.json', text)

    def test_no_machine_specific_wine_hardcode(self):
        for path in (ROTATE_SH, AUTO_SH, SERVICE, TIMER, README, DOC):
            with self.subTest(path=path.name):
                self.assertNotIn("/opt/marvis-wine", read_text(path))

    def test_generator_is_pluggable(self):
        text = self.text
        self.assertIn("QIMEI_GEN_URL", text)
        self.assertIn("QIMEI_GEN_CMD", text)
        # HTTP 分支要真的按 qimei-gen 的契约发 JSON
        self.assertIn('"{\\"mac\\":\\"$mac\\"}"', text)
        self.assertIn("qimei36", text)
        # 两样都没配 -> 明确拒绝（退出码 5），不许偷偷走派生值
        self.assertIn("exit 5", text)
        self.assertIn("生成器未配置", text)

    def test_dry_run_stops_before_any_write(self):
        text = self.text
        dry_guard = text.index('if [ "$DRYRUN" = "1" ]; then')
        backup = text.index('cp -p "$TABLE"')
        self.assertLess(dry_guard, backup, "--dry-run 的出口必须在备份/写表之前")
        self.assertIn("未写表、未部署", text)

    def test_dry_run_and_deploy_are_mutually_exclusive(self):
        self.assertIn('[ "$DRYRUN" = "1" ] && [ "$DEPLOY" = "1" ]', self.text)
        self.assertIn("互斥", self.text)

    def test_deploy_is_gated_behind_flag(self):
        text = self.text
        gate = text.index('if [ "$DEPLOY" != "1" ]; then')
        deploy_call = text.index("deploy_once || {")
        self.assertLess(gate, deploy_call, "--deploy 的开关必须在真正的部署调用之前")
        self.assertIn("--deploy) DEPLOY=1", text)
        self.assertIn("未部署（加 --deploy 才 reload/restart）", text)

    def test_single_writer_lock(self):
        self.assertIn("flock -n 9", self.text)
        self.assertIn(".qimei-rotate.lock", self.text)
        # 没有 flock 时必须显式失败：静默 exit 0 会被误读成「已有人在跑」，整天不轮换
        self.assertIn("command -v flock", self.text)
        self.assertIn("缺 flock", self.text)

    def test_shape_gate_and_batch_uniqueness(self):
        text = self.text
        self.assertIn("SHAPE_RE='%s'" % QIMEI_SHAPE, text)
        self.assertIn('grep -qE "$SHAPE_RE"', text)
        self.assertIn("与批内其它账号重复", text)
        self.assertIn("与旧值相同", text)

    def test_backup_before_upsert_and_batch_tagged(self):
        text = self.text
        backup = text.index('cp -p "$TABLE"')
        write = text.index("PYEOF")
        self.assertLess(backup, write, "备份必须在写表之前")
        self.assertIn('"$BACKUP_DIR/qimei_table.$BATCH.json"', text)

    def test_write_is_in_place_by_default(self):
        text = self.text
        self.assertIn("QIMEI_WRITE_MODE:-inplace", text)
        self.assertIn("fh.truncate(0)", text)
        self.assertIn("bind mount", text)
        # rename 兼容分支必须存在，且被标注为「只能靠重启生效」
        self.assertIn("write_mode == \"rename\"", text)
        self.assertIn("os.replace", text)
        self.assertIn("只能靠 restart 生效", text)

    def test_reload_first_then_restart_fallback(self):
        text = self.text
        self.assertIn("QIMEI_RELOAD_CMD:-", text)
        self.assertIn('bash -c "$QIMEI_RELOAD_CMD"', text)
        self.assertIn("QIMEI_DEPLOY_MODE:-auto", text)
        self.assertIn("auto|reload|restart", text)
        # 顺序：热重载分支在前，restart 兜底在后
        self.assertLess(text.index("热重载（QIMEI_RELOAD_CMD）"),
                        text.index("action=restart reason=reload_not_configured"))
        # deploy_mode=reload 时不许偷偷重启
        self.assertIn("不回落重启", text)

    def test_restart_path_is_compose_build_then_up(self):
        text = self.text
        self.assertIn("docker compose build admin", text)
        self.assertIn("docker compose up -d --no-build admin", text)
        self.assertLess(text.index("docker compose build admin"),
                        text.index("docker compose up -d --no-build admin"))

    def test_verify_reads_container_truth(self):
        text = self.text
        self.assertIn("uskey.qimei36(", text)
        self.assertIn("MISMATCH", text)
        self.assertIn("部署后校验不一致", text)

    def test_log_lines_carry_batch(self):
        self.assertIn('log() { echo "$(date -Is) batch=$BATCH $*"', self.text)
        self.assertIn("action=rotate acc=$a uid=$u", self.text)

    def test_heredocs_are_closed(self):
        assert_heredocs_closed(self, self.text, ROTATE_SH.name)


class AutoRotateScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read_text(AUTO_SH)

    def test_judgement_defaults(self):
        text = self.text
        self.assertIn("QIMEI_WINDOW_MIN:-10", text)
        self.assertIn("QIMEI_MIN_HITS:-2", text)
        self.assertIn("QIMEI_COOLDOWN_SEC:-7200", text)
        self.assertIn("QIMEI_POST_WATCH_SEC:-300", text)
        self.assertIn("QIMEI_WALLET_TRIES:-3", text)

    def test_state_and_logs_live_under_log_dir(self):
        text = self.text
        self.assertIn("LOG=$QIMEI_LOG_DIR/qimei-autorotate.log", text)
        self.assertIn("ALERTLOG=$QIMEI_LOG_DIR/qimei-alerts.txt", text)
        self.assertIn("STATE=$QIMEI_LOG_DIR/qimei-autorotate.state", text)
        self.assertIn("QIMEI_LOG_DIR:-/var/log/qimei-rotate", text)

    def test_single_instance_and_cooldown(self):
        text = self.text
        self.assertIn("flock -n 9", text)
        self.assertIn("command -v flock", text)
        self.assertIn("skip=locked", text)
        self.assertIn("last_fire_epoch", text)
        self.assertIn('if [ "$DRYRUN" != "1" ] && [ "$LAST_FIRE" -gt 0 ] && [ "$AGE" -lt "$COOLDOWN" ]', text)
        self.assertIn("debounced", text)

    def test_wallet_precheck_gates_rotation(self):
        text = self.text
        self.assertIn("fetch_wallet", text)
        self.assertIn("grep -q '\"ok\": true'", text)
        self.assertIn("配额/凭证问题，不轮换", text)
        # 预检失败必须 exit 0（不动设备），而不是继续往下轮换
        wallet_fail = text.index("配额/凭证问题，不轮换")
        rotation = text.index("ROT_ARGS=(--all --deploy)")
        self.assertLess(wallet_fail, rotation, "钱包预检必须在轮换之前")
        self.assertIn("exit 0", text[wallet_fail:rotation])

    def test_rotation_is_called_exactly_once(self):
        text = self.text
        self.assertEqual(text.count('bash "$ROT"'), 1,
                         "只能有一处轮换调用：失败不重试是硬要求")
        self.assertIn("未做重试", text)
        self.assertIn("exit 8", text)

    def test_post_watch_does_not_retry(self):
        text = self.text
        self.assertIn("post_watch", text)
        self.assertIn("轮换无效，疑似 IP 维度", text)
        self.assertIn("不再重试", text)
        self.assertIn("exit 9", text)

    def test_dry_run_passes_through_without_deploy(self):
        text = self.text
        self.assertIn("QIMEI_AUTOROTATE_DRY_RUN:-0", text)
        self.assertIn("ROT_ARGS=(--all --dry-run)", text)
        self.assertIn("ROT_ARGS=(--all --deploy)", text)
        dry = text.index("ROT_ARGS=(--all --dry-run)")
        real = text.index("ROT_ARGS=(--all --deploy)")
        self.assertLess(dry, real)
        self.assertIn("result=dry_run_ok", text)

    def test_alerts_go_to_file_and_syslog(self):
        text = self.text
        self.assertIn("logger -t marvis-qimei-alert", text)
        self.assertIn("ALERT $msg", text)

    def test_heredocs_are_closed(self):
        assert_heredocs_closed(self, self.text, AUTO_SH.name)


class SystemdUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service_text = read_text(SERVICE)
        cls.timer_text = read_text(TIMER)
        cls.service = unit_keys(cls.service_text)
        cls.timer = unit_keys(cls.timer_text)

    def test_service_is_oneshot_running_the_judge_script(self):
        self.assertEqual(unit_one(self.service, "Type"), "oneshot")
        exec_start = unit_one(self.service, "ExecStart")
        self.assertTrue(exec_start.startswith("/usr/bin/env bash "), exec_start)
        self.assertIn("auto_rotate_on_banned.sh", exec_start)
        self.assertIn("/home/admin/MiraiHub/deploy/qimei-rotate/", exec_start)

    def test_service_working_dir_and_env(self):
        self.assertEqual(unit_one(self.service, "WorkingDirectory"), "/home/admin/MiraiHub")
        self.assertIn("-/etc/miraihub/qimei-rotate.env", self.service_text)
        env = " ".join(self.service.get("Environment", []))
        for token in ("MIRAIHUB_DIR=/home/admin/MiraiHub",
                      "CONTAINER=miraihub-admin-1",
                      "QIMEI_STATE_DIR=/home/admin/MiraiHub/admin/platform/marvis/core",
                      "QIMEI_LOG_DIR=/var/log/qimei-rotate",
                      "QIMEI_COOLDOWN_SEC=7200"):
            with self.subTest(token=token):
                self.assertIn(token, env)
        # 生成器与重载钩子只能是注释示例：仓库不钉死机器路径
        self.assertIn("#Environment=QIMEI_GEN_URL", self.service_text)
        self.assertNotIn("\nEnvironment=QIMEI_GEN_CMD", self.service_text)

    def test_service_timeout_covers_generation_plus_watch(self):
        timeout = int(unit_one(self.service, "TimeoutStartSec"))
        watch = int(re.search(r"QIMEI_POST_WATCH_SEC=(\d+)", self.service_text).group(1))
        self.assertGreaterEqual(timeout, 1800)
        self.assertGreater(timeout, watch)

    def test_service_waits_for_docker(self):
        self.assertIn("docker.service", " ".join(self.service.get("After", [])))
        self.assertIn("docker.service", " ".join(self.service.get("Wants", [])))
        self.assertTrue(self.service_text.startswith("[Unit]"))
        self.assertIn("[Install]", self.service_text)

    def test_timer_calendar_is_every_six_hours(self):
        calendar = unit_one(self.timer, "OnCalendar")
        parts = calendar.split()
        self.assertEqual(len(parts), 2, "期望 '*-*-* HH/step:MM:SS' 两段：%r" % calendar)
        date_part, time_part = parts
        self.assertEqual(date_part, "*-*-*")
        hour_field, minute_field = time_part.split(":")[:2]
        match = re.fullmatch(r"(\d{1,2})/(\d{1,2})", hour_field)
        self.assertIsNotNone(match, "OnCalendar 的小时字段应形如 00/6：%r" % hour_field)
        start, step = int(match.group(1)), int(match.group(2))
        self.assertEqual(step, 6, "周期必须是 6 小时")
        hours = [h for h in range(24) if h >= start and (h - start) % step == 0]
        self.assertEqual(hours, [0, 6, 12, 18])
        self.assertEqual(int(minute_field), 17, "错开整点，避免和整点任务撞车")

    def test_timer_boot_trigger_and_persistence(self):
        self.assertEqual(unit_one(self.timer, "OnBootSec"), "5min")
        self.assertEqual(unit_one(self.timer, "Persistent"), "true")
        self.assertEqual(unit_one(self.timer, "Unit"), "qimei-rotate.service")
        self.assertIn("timers.target", self.timer_text)
        self.assertIn("RandomizedDelaySec", self.timer_text)

    def test_timer_and_service_are_paired(self):
        self.assertIn("qimei-rotate.service", self.timer_text)
        self.assertEqual(unit_one(self.timer, "Unit"), SERVICE.name)
        self.assertTrue(SERVICE.name.endswith(".service"))

    def test_units_are_lf(self):
        self.assertNotIn("\r", self.service_text)
        self.assertNotIn("\r", self.timer_text)


class QimeiGenCouplingTests(unittest.TestCase):
    def test_python_generator_example_is_the_wine_free_path(self):
        """新版纯 Python 生成器示例必须存在且不依赖 wine。"""
        example = GEN_SERVICE.with_name("qimei-python-gen.service.example")
        if not example.exists():
            self.skipTest("旧基线不含 python 生成器 unit 示例")
        self.assertTrue(example.exists(), example)
        text = read_text(example)
        self.assertNotIn("/opt/marvis-wine", text)
        self.assertIn("qimei_generate.py", text)
        self.assertIn("QIMEI_PYTHON_GENERATOR_ENABLED", text)

    def test_legacy_wine_unit_is_documented_as_legacy(self):
        """旧 unit 若还带 wine，必须在新文档里被标成 legacy，不能当默认。"""
        if not GEN_SERVICE.is_file():
            self.skipTest("旧基线不含 deploy/qimei-gen/qimei-gen.service")
        legacy = read_text(GEN_SERVICE)
        if "/opt/marvis-wine" not in legacy:
            self.skipTest("legacy unit already wine-free")
        doc = read_text(DOC)
        self.assertIn("qimei-rotate", doc)
        self.assertIn("QIMEI_GEN_CMD", doc)


class DocsCoverageTests(unittest.TestCase):
    def test_readme_has_install_and_params(self):
        text = read_text(README)
        for token in ("systemctl enable --now qimei-rotate.timer",
                      "/var/log/qimei-rotate",
                      "QIMEI_RELOAD_CMD",
                      "QIMEI_WRITE_MODE",
                      "退出码",
                      "python -m unittest tests.test_qimei_rotation"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_rotation_doc_covers_required_topics(self):
        text = read_text(DOC)
        for token in ("触发判据", "周期", "dry-run", "落盘", "reload", "回滚", "防抖", "告警",
                      "OnCalendar", "Persistent=true", "QIMEI_COOLDOWN_SEC",
                      "QIMEI_RELOAD_CMD", "退出码 8", "退出码 9", "inode"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_doc_points_at_real_assets(self):
        text = read_text(DOC)
        for token in ("deploy/qimei-rotate/auto_rotate_on_banned.sh",
                      "deploy/qimei-rotate/rotate_all_qimei.sh",
                      "deploy/qimei-rotate/qimei-rotate.service",
                      "deploy/qimei-rotate/qimei-rotate.timer",
                      "tests/test_qimei_rotation.py"):
            with self.subTest(token=token):
                self.assertIn(token, text)


class LiveDataSafetyTests(unittest.TestCase):
    """真实 qimei_table/pool 是线上数据：这组测试只读文件，绝不写盘、不设 env。

    2026-11-22 的教训：跑 assign_ex / reconcile 试验时漏了 --state-dir，
    真实台账被写进 owners=["A1"] 这类测试值，只能整份 git checkout 还原。
    """

    def test_module_never_mutates_files_or_env(self):
        # 用 AST 扫自己，而不是扫字符串：本文件里那些「被断言的字面量」不算代码。
        source = read_text(Path(__file__))
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("os", imported, "测试模块不 import os：不设 env、不改盘")
        self.assertNotIn("tempfile", imported)

        # str.replace 不算写盘，所以这里只钉真正会改文件的四个 attrs；
        # os.replace 走不通的原因在上一行：本模块不 import os。
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        for forbidden in ("write_text", "write_bytes", "mkdir", "unlink"):
            with self.subTest(attr=forbidden):
                self.assertNotIn(forbidden, attrs, "测试模块不许写盘：.%s" % forbidden)

        runs = [node for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run"
                and getattr(node.func.value, "id", "") == "subprocess"]
        self.assertEqual(len(runs), 2,
                         "只允许两处只读探测：wsl bash -c echo、bash -n")

    def test_rotation_default_is_live_table_so_drills_must_retarget(self):
        # 默认路径就是线上表 —— 这是生产工具的正常行为；所以演练必须换目录（见下面的文档断言）
        text = read_text(ROTATE_SH)
        self.assertIn("QIMEI_STATE_DIR:-$MIRAIHUB_DIR/admin/platform/marvis/core", text)
        self.assertIn("--dry-run", text)
        self.assertIn("QIMEI_WRITE_MODE:-inplace", text)

    def test_tmp_dir_discipline_is_documented(self):
        for path in (README, DOC):
            text = read_text(path)
            with self.subTest(path=path.name):
                self.assertIn("QIMEI_STATE_DIR=tmp/", text)
                self.assertIn("--state-dir", text)
                self.assertIn("线上", text)
                self.assertIn("qimei_state.lock", text)


def _to_wsl_path(path: Path) -> str:
    posix = str(path).replace("\\", "/")
    if len(posix) > 1 and posix[1] == ":":
        return "/mnt/" + posix[0].lower() + posix[2:]
    return posix


#: Windows 上 bash 常常不在 PATH 里（Git for Windows / msys2 / cygwin 各自一份）。
_WINDOWS_BASH_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
    r"C:\msys64\usr\bin\bash.exe",
    r"C:\cygwin64\bin\bash.exe",
)


def _windows_bash_paths() -> list:
    """从 PATH 上的 git.exe 反推同装的一份 bash（Git for Windows 的 <root>/bin/bash.exe）。"""
    found = []
    git = shutil.which("git")
    if git:
        root = Path(git).resolve().parent.parent
        found.append(root / "bin" / "bash.exe")
        found.append(root / "usr" / "bin" / "bash.exe")
    found.extend(Path(item) for item in _WINDOWS_BASH_CANDIDATES)
    return found


def _find_bash() -> tuple:
    """返回 (命令前缀, 路径转换函数)；找不到可用的 bash 就返回 (None, None)。"""
    exe = shutil.which("bash")
    if exe:
        return [exe], lambda path: str(path)
    for candidate in _windows_bash_paths():
        if candidate.is_file():
            return [str(candidate)], lambda path: str(path)
    for candidate in ("wsl", "wsl.exe"):
        exe = shutil.which(candidate)
        if not exe:
            continue
        try:
            probe = subprocess.run([exe, "-e", "bash", "-c", "echo bash-ok"],
                                   capture_output=True, text=True, timeout=120)
        except Exception:  # noqa: BLE001 - 探测失败就当没有
            continue
        if probe.returncode == 0 and "bash-ok" in (probe.stdout or ""):
            return [exe, "-e", "bash"], _to_wsl_path
    return None, None


class BashSyntaxTests(unittest.TestCase):
    def test_scripts_pass_bash_n(self):
        prefix, to_path = _find_bash()
        if not prefix:
            raise unittest.SkipTest(
                "本机没有 bash（PATH / Git for Windows / msys2 / cygwin 都没有），"
                "也没有可用的 WSL 发行版：bash -n 语法检查跳过。"
                "上 Linux 部署前请手工跑一次：bash -n deploy/qimei-rotate/rotate_all_qimei.sh "
                "deploy/qimei-rotate/auto_rotate_on_banned.sh")
        for script in (ROTATE_SH, AUTO_SH):
            with self.subTest(script=script.name):
                target = script if to_path is None else to_path(script)
                proc = subprocess.run(prefix + ["-n", target],
                                      capture_output=True, text=True, timeout=180)
                self.assertEqual(proc.returncode, 0,
                                 "%s 语法错误：%s" % (script.name, proc.stderr.strip()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
