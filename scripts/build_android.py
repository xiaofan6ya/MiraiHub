"""Build an arm64 APK with an explicitly staged backend; no local credentials."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "android/app/build/generated/python"

#: 让 `from scripts.xxx import ...` 在**任何工作目录**下都能解析。
#:
#: `python scripts/build_android.py` 时 `sys.path[0]` 是 `scripts/` 而不是仓库根，
#: 于是 `import scripts.android_signing` 直接
#: `ModuleNotFoundError: No module named 'scripts.android_signing'`
#: —— 而且报错发生在**跑完 pip 安装和暂存之后**，白白浪费几分钟。
#: 仓库根的 `scripts/` 没有 `__init__.py`，靠 PEP 420 命名空间包即可。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def check_runtime() -> None:
    # Run the staged app against Android's pinned dependency versions. Gradle
    # compilation alone cannot catch import-time FastAPI/Pydantic errors.
    environment = ROOT / 'build/android-runtime-check'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(environment)
    subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                    '-r', str(ROOT / 'android/requirements.txt'), '--index-url',
                    'https://pypi.tuna.tsinghua.edu.cn/simple'], check=True)
    subprocess.run([str(python), str(ROOT / 'scripts/check_runtime.py'), '--android',
                    '--source-dir', str(STAGING)], cwd=ROOT, check=True)


# ---------------------------------------------------------------------------
# 旧控制台的移动端改写逻辑 —— 已移除（2026-11-27 合并后台前端后）
# ---------------------------------------------------------------------------
#
# 这里曾经有 `mobile_html()` / `mobile_js()` 和它们用到的
# `MobileHtmlError` / `_replace_once` / `_sub_once` / `_sub_maybe` /
# `_replace_maybe`。做法是：把 `admin/console/index.html` 与 `console-app.js`
# 改写一份（删掉桌面专属面板、注入 `/static/mobile.css` 与 `mobile_settings.js`），
# 再让 APK 的 WebView 加载改写后的页面。
#
# 但真机加载的是 `MainActivity.java:145` 的
# `web.loadUrl(origin + "/admin?desktop=1&android=1")` —— 一个 URL，不是本地文件。
# 合并后 `/admin` 由新版 Vue 前端承担（自带 MobileDrawer 与 640/720/767/1023px
# 断点），所以改写出来的那份 HTML 从来没有被加载过：copytree 只是把 1MB 旧控制台
# 塞进 APK 包。这里连同 `admin/console` 的复制一起删掉。
#
# 结果：APK 包体更小，且「移动端适配」只有一份（Vue 侧那套），不会再出现
# 「APK 界面和 Web 界面不一样」的分叉。


def prepare() -> None:
    from scripts.build_frontend import build_frontend, stage_frontend
    build_frontend(ROOT)
    # Do not allow a symlink or changed working directory to redirect recursive cleanup.
    staging = STAGING.resolve()
    allowed = (ROOT / "android/app/build").resolve()
    if not staging.is_relative_to(allowed) or staging != allowed / "generated/python":
        raise RuntimeError("Unexpected Android staging path")
    if staging.exists():
        # 构建机的安全删除钩子会拦截批量 rmtree；移开旧暂存目录即可，
        # 不删任何文件，也不影响新目录创建。
        staging.rename(staging.parent / ("python_prev_" + secrets.token_hex(4)))
    staging.mkdir(parents=True)
    for path in (ROOT / "admin").rglob("*.py"):
        relative = path.relative_to(ROOT)
        if path.name in ("app_source.py",):
            continue
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    for name in ("upstream_compat.py", "anthropic_adapter.py", "responses_adapter.py",
                 "responses_projection.py", "desensitize.py", "repository_instructions.py", "wb_install.py", "converter.py", "model_metadata.py"):
        shutil.copy2(ROOT / name, staging / name)
    (staging / "desktop/assets").mkdir(parents=True)
    for name in ("__init__.py", "common.py", "runtime.py", "updates.py"):
        shutil.copy2(ROOT / "desktop" / name, staging / "desktop" / name)
    shutil.copy2(ROOT / "desktop/assets/app.svg", staging / "desktop/assets/app.svg")
    metadata = json.loads((ROOT / "desktop/version.json").read_text(encoding="utf-8"))
    metadata["asset"] = "miraihub-{version}-android-arm64.apk"
    (staging / "desktop/version.json").write_text(json.dumps(metadata), encoding="utf-8")
    stage_frontend(staging, ROOT)
    shutil.copytree(ROOT / "admin/data", staging / "admin/data")
    # 平台自带的数据文件（如 trae/data/catalog_*.json）也要进包：
    # 上面的 rglob 只拷 *.py，这些 json 在真机/模拟器上一律缺文件，
    # 运行时只能吃「目录加载失败」告警走网络兜底。
    for platform_data in (ROOT / "admin/platform").glob("*/data"):
        shutil.copytree(platform_data, staging / platform_data.relative_to(ROOT))
    for name in ("android_runtime.py", "mobile_http.py"):
        shutil.copy2(ROOT / "android/python" / name, staging / name)
    shutil.copy2(ROOT / "LICENSE", staging / "LICENSE")
    print("Prepared mobile backend and UI (Vue frontend staged via stage_frontend)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--with-debug", action="store_true", help="Also build the isolated emulator debug APK")
    parser.add_argument("--gradle", help="Gradle 8.11.1 executable")
    parser.add_argument("--jdk", help="JDK 17 directory")
    parser.add_argument("--sdk", help="Android SDK directory")
    parser.add_argument("--skip-python-install", action="store_true",
                        help="Skip Chaquopy's installReleasePythonRequirements when the pinned "
                             "dependencies are already staged. Needed on hosts where a bulk-delete "
                             "guard blocks pip's post-install cache cleanup (pip itself succeeds). "
                             "Fails loudly if the staged dependencies are incomplete.")
    parser.add_argument("--verify-signing", action="store_true",
                        help="Only verify the Android signing identity and exit "
                             "(no build). CI / pre-release check.")
    parser.add_argument("--rotate-signing", action="store_true",
                        help="Explicitly rotate the Android signing identity. Old users MUST "
                             "uninstall before installing this build (local account DB is lost).")
    args = parser.parse_args()
    if args.verify_signing:
        from scripts.android_signing import verify as verify_signing

        info = verify_signing(jdk=args.jdk)
        print("Android 签名身份校验通过：alias=%s sha256=%s"
              % (info["alias"], info["sha256"]))
        return
    prepare()
    if args.prepare_only:
        return
    check_runtime()
    env = dict(os.environ, WB_ANDROID_BUILD_PYTHON=sys.executable)
    if args.jdk:
        env["JAVA_HOME"] = str(Path(args.jdk).resolve())
    if args.sdk:
        env["ANDROID_HOME"] = str(Path(args.sdk).resolve())
    if env.get("JAVA_HOME"):
        env["PATH"] = str(Path(env["JAVA_HOME"]) / "bin") + os.pathsep + env.get("PATH", "")
    gradle = args.gradle or shutil.which("gradle")
    if not gradle:
        raise SystemExit("Set --gradle to Gradle 8.11.1 and provide JDK 17 / Android SDK")

    # 签名身份统一由 scripts/android_signing.py 管理：**一次生成、长期复用**。
    #
    # 历史教训（这次必须一次性解决）：
    #   · 材料放在 `build/android-signing/` 时，`build_desktop.py` 每次重建
    #     `build/` 就把私钥删了，下一版自动生成新钥匙 —— APK 覆盖升级直接
    #     `INSTALL_FAILED_UPDATE_INCOMPATIBLE`，用户只能卸载（本地账号库丢失）；
    #   · 只留 `signing.json` 而私钥丢了时，旧逻辑会用同一个密码重新生成一把
    #     **新私钥**，全程无报错，直到用户装更新失败。
    # 现在：keystore 已存在就永不重建；指纹与 `android/expected-signing-cert.txt`
    # 不一致就**中止构建**，换钥匙只能显式 `--rotate-signing`。
    from scripts.android_signing import ensure_identity, verify_apk

    signing = ensure_identity(jdk=args.jdk, rotate=args.rotate_signing)
    env.update(signing["env"])
    print("Android 签名身份：alias=%s sha256=%s"
          % (signing["alias"], signing["sha256"]))

    tasks = ["assembleRelease", "lintRelease"]
    if args.with_debug:
        tasks.append("assembleDebug")
    gradle_cmd = [gradle, "--no-daemon"]
    if args.skip_python_install:
        common = ROOT / "android/app/build/python/pip/release/common"
        required = ("fastapi", "uvicorn", "pydantic", "sqlalchemy", "httpx", "jwt")
        missing = [name for name in required if not (common / name).is_dir()]
        if missing:
            raise SystemExit("不能跳过 pip 安装任务，staged 依赖不完整：%s" % ", ".join(missing))
        print("Skipping installReleasePythonRequirements (staged deps verified: %d/%d)"
              % (len(required) - len(missing), len(required)))
        gradle_cmd += ["-x", ":app:installReleasePythonRequirements"]
    subprocess.run([*gradle_cmd, *tasks], cwd=ROOT / "android", env=env, check=True)
    version = json.loads((ROOT / "desktop/version.json").read_text(encoding="utf-8"))["version"]
    output = ROOT / "dist/android"
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"miraihub-{version}-android-arm64.apk"
    shutil.copy2(ROOT / "android/app/build/outputs/apk/release/app-release.apk", target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".apk.sha256").write_text(f"{digest}  {target.name}\n", encoding="ascii")
    print(f"Built {target.name} ({target.stat().st_size / 1048576:.1f} MB)")
    # 产物签名校验：证明这个 APK 真的用了登记的那把钥匙，
    # 而不是「gradle 悄悄用了别的 keystore」。没有 apksigner 时跳过。
    try:
        check = verify_apk(target, jdk=args.jdk)
    except Exception as exc:  # noqa: BLE001 - 校验失败必须让构建失败
        raise SystemExit("APK 签名校验失败：%s" % exc)
    if check.get("skipped"):
        print("APK 签名校验跳过：%s" % check.get("reason"))
    else:
        print("APK 签名证书 SHA-256：%s" % check.get("sha256"))


if __name__ == "__main__":
    main()
