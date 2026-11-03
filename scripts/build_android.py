"""Build an arm64 APK with an explicitly staged backend; no local credentials.

前端已迁移为 Vite + React SPA（front/）：APK 不再做 index.html 正则改写
（旧 mobile_html() 的字符串手术随旧控制台一起下线），改为：
  1. 拷贝 front/dist 构建产物（需先 `npm run build`）；
  2. 安卓专属 UI 由 SPA 运行时按 `window.WBAndroid` 桥自适应
     （隐藏扫描/注入入口，导航出现"安卓设置"页）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "android/app/build/generated/python"


def check_runtime() -> None:
    # Run the staged app against Android's pinned dependency versions. Gradle
    # compilation alone cannot catch import-time FastAPI/Pydantic errors.
    environment = ROOT / 'build/android-runtime-check'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(environment)
    pip_cmd = [str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
               '-r', str(ROOT / 'android/requirements.txt')]
    # 海外 CI 访问清华镜像会被 403（同 settings.gradle 跳过 aliyun 的道理），CI 直连官方 pypi
    if os.environ.get('CI') != 'true':
        pip_cmd += ['--index-url', 'https://pypi.tuna.tsinghua.edu.cn/simple']
    subprocess.run(pip_cmd, check=True)
    subprocess.run([str(python), str(ROOT / 'scripts/check_runtime.py'), '--android',
                    '--source-dir', str(STAGING)], cwd=ROOT, check=True)


def prepare() -> None:
    # Do not allow a symlink or changed working directory to redirect recursive cleanup.
    staging = STAGING.resolve()
    allowed = (ROOT / "android/app/build").resolve()
    if not staging.is_relative_to(allowed) or staging != allowed / "generated/python":
        raise RuntimeError("Unexpected Android staging path")
    dist_index = ROOT / "front" / "dist" / "index.html"
    if not dist_index.is_file():
        raise RuntimeError(
            "front/dist/index.html 不存在：请先在 front/ 下运行 `npm install && npm run build`。"
            "APK 打包不再改写 HTML，直接内置前端构建产物。")
    if staging.exists():
        shutil.rmtree(staging)
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
    shutil.copytree(ROOT / "admin/data", staging / "admin/data")
    shutil.copytree(ROOT / "front/dist", staging / "front/dist")
    for name in ("android_runtime.py", "mobile_http.py"):
        shutil.copy2(ROOT / "android/python" / name, staging / name)
    shutil.copy2(ROOT / "LICENSE", staging / "LICENSE")
    print("Prepared mobile backend and SPA frontend (android bridge adapts at runtime)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--with-debug", action="store_true", help="Also build the isolated emulator debug APK")
    parser.add_argument("--gradle", help="Gradle 8.11.1 executable")
    parser.add_argument("--jdk", help="JDK 17 directory")
    parser.add_argument("--sdk", help="Android SDK directory")
    args = parser.parse_args()
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
    # Keep one private signing identity for all upgrades. Never upload these files.
    signing = ROOT / "build/android-signing"
    signing.mkdir(parents=True, exist_ok=True)
    credentials = signing / "signing.json"
    keystore = signing / "release.jks"
    if credentials.is_file():
        config = json.loads(credentials.read_text(encoding="utf-8"))
    else:
        config = {"password": secrets.token_urlsafe(40)}
        credentials.write_text(json.dumps(config), encoding="utf-8")
    env.update(WB_ANDROID_KEYSTORE=str(keystore), WB_ANDROID_STORE_PASSWORD=config["password"],
               WB_ANDROID_KEY_PASSWORD=config["password"])
    if not keystore.is_file():
        keytool = str(Path(env["JAVA_HOME"]) / "bin/keytool") if env.get("JAVA_HOME") else "keytool"
        subprocess.run([keytool, "-genkeypair", "-keystore", str(keystore), "-storetype", "JKS",
                        "-storepass:env", "WB_ANDROID_STORE_PASSWORD", "-keypass:env", "WB_ANDROID_KEY_PASSWORD",
                        "-alias", "workbuddy2api", "-keyalg", "RSA", "-keysize", "3072",
                        "-validity", "10000", "-dname", "CN=MiraiHub Android"], env=env, check=True)
    tasks = ["assembleRelease", "lintRelease"]
    if args.with_debug:
        tasks.append("assembleDebug")
    subprocess.run([gradle, "--no-daemon", *tasks], cwd=ROOT / "android", env=env, check=True)
    version = json.loads((ROOT / "desktop/version.json").read_text(encoding="utf-8"))["version"]
    output = ROOT / "dist/android"
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"miraihub-{version}-android-arm64.apk"
    shutil.copy2(ROOT / "android/app/build/outputs/apk/release/app-release.apk", target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".apk.sha256").write_text(f"{digest}  {target.name}\n", encoding="ascii")
    print(f"Built {target.name} ({target.stat().st_size / 1048576:.1f} MB)")


if __name__ == "__main__":
    main()
