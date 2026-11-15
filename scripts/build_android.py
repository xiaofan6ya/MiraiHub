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


class MobileHtmlError(RuntimeError):
    """移动端页面改写没有命中预期标记。

    为什么必须报错而不是静默跳过：历史上这里是一条
    `html.replace('<link ... /static/desktop.css />', ...)` —— 后来控制台把样式
    改挂到 `/admin/assets/desktop.css?v=…`，这条替换就再也没命中过，而
    `str.replace` 找不到目标时**不抛异常**。结果 mobile.css 从来没被打进
    APK，设置页（只在移动端存在的那一版）整屏没有样式。构建时发现不了，
    只有装到手机上才看得见。
    """


def _replace_once(html: str, old: str, new: str, label: str) -> str:
    if old not in html:
        raise MobileHtmlError(
            f"移动端改写未命中：{label}（期望片段 {old!r}）。"
            "控制台模板改过结构后，请同步更新 scripts/build_android.py。")
    return html.replace(old, new, 1)


def _sub_once(html: str, pattern: str, repl, label: str, flags=re.S) -> str:
    result, count = re.subn(pattern, repl, html, flags=flags)
    if not count:
        raise MobileHtmlError(
            f"移动端改写未命中：{label}（期望正则 {pattern!r}）。"
            "控制台模板改过结构后，请同步更新 scripts/build_android.py。")
    return result


def _sub_maybe(text: str, pattern: str, repl, label: str, flags=re.S) -> str:
    """模板漂移时允许跳过，但必须在构建日志里留下痕迹。"""
    result, count = re.subn(pattern, repl, text, flags=flags)
    if not count:
        print(f"[mobile] 跳过未命中：{label}")
    return result


def _replace_maybe(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        print(f"[mobile] 跳过未命中：{label}")
        return text
    return text.replace(old, new, 1)


def mobile_html(html: str) -> str:
    # --- 移除桌面专属面板 -------------------------------------------------
    #
    # 导航已经改成「分组按钮」：`接入` 这个按钮同时承载 client / sync / alerts
    # 三个面板，而移动端只保留 sync（作为「设置」）、要砍掉 client 与 settings。
    # 所以这里不能直接删掉整个 `接入` 按钮（那会把设置入口一起删掉，手机上将
    # 完全没有设置页），而是先把它**改写成独立的「设置」项**，再交给后面的
    # 「移除 client 导航项」逻辑处理真正的桌面专属项。
    has_grouped_nav = 'data-group-tabs="client,sync' in html
    if has_grouped_nav:
        html = _sub_once(
            html, r'<button\b[^>]*data-tab="client"[^>]*data-group-tabs="[^"]*"[^>]*>',
            '<button class="side-item" data-tab="sync">',
            '「接入」分组 → 移动端「设置」项')
        # 文案：把按钮上的「接入」换成「设置」，与移动端设置页一致。
        html = _sub_maybe(
            html, r'(data-tab="sync">.*?)</button>',
            lambda m: re.sub(r'(</svg>)\s*接入\s*$', r'\1 设置', m.group(1)) + '</button>',
            '「接入」文案 → 「设置」')
    else:
        html = _sub_once(html, r'<button\b[^>]*data-tab="client".*?</button>',
                         '', '移除「客户端参数」导航项')
        html = _replace_once(html, '</svg> 同步设置</button>', '</svg> 设置</button>',
                             '同步设置 → 设置（导航文案）')
    html = _sub_once(html, r'<section id="panel-client".*?</section>',
                     '', '移除「客户端参数」面板')
    html = _sub_once(html, r'<section id="panel-settings".*?</section>',
                     '', '移除「管理员设置」面板')
    settings = (ROOT / "android/web/settings.html").read_text(encoding="utf-8")
    html = _sub_once(html, r'<section id="panel-sync".*?</section>',
                     lambda _: settings, '用移动端设置页替换「同步设置」')

    # --- 桌面专属脚本已外置到 console-app.js，改写见 mobile_js() ----------
    html = _sub_once(html, r'<button\b[^>]*data-tab="settings".*?</button>',
                     '', '移除「设置」导航项（由移动端设置页接管）')

    # --- 文案与凭证提示（模板可能改版，未命中只记录不中断）---------------
    html = _replace_maybe(html, '登录新账号，或从本机与备份中恢复已有账号。',
                          '手机号登录或浏览器授权，账号保存在这台手机。', '账号页文案')
    html = _replace_maybe(html, '新版加密、旧版明文及 .info.bak-时间戳备份',
                          '旧版明文及 .info.bak-时间戳备份', '导入说明文案')
    # 移动端 mobile_js() 删掉了整个 scanLocal 实现，但 index.html 里的
    # 「扫描本机」按钮还挂着 onclick="scanLocal(this)" —— 点击直接
    # ReferenceError。改派给 WBPlatform.action('scan')：platformScanLocal
    # 的 workbuddy 分支有 typeof 守卫，移动端会得到
    # 「移动端不支持扫描本机」的提示而不是脚本报错。
    html = _replace_maybe(html, 'onclick="scanLocal(this)"',
                          "onclick=\"WBPlatform.action('scan')\"",
                          '「扫描本机」按钮 → 平台动作入口')
    credential_note = ('<p class="text-xs text-slate-500 mb-2">新版 Windows 加密凭证请先在'
                       '电脑账号管理页导出“系统明文备份”后再导入，或使用手机号授权登录。</p>')
    for marker in ('<input id="upFile"', '<textarea id="reauthJson"'):
        html = _replace_maybe(html, marker, credential_note + marker,
                              f'凭证提示（{marker}）')

    # --- 挂上移动端样式与脚本 ---------------------------------------------
    #
    # 用 `</head>` 做锚点而不是匹配某条 `<link>`：后者会随控制台改版漂移
    # （这次就是 `/static/desktop.css` → `/admin/assets/desktop.css?v=…`）。
    # mobile.css 必须排在全部桌面样式之后，才能覆盖 `.mobile-mode` 下的规则。
    html = _replace_once(
        html, '</head>',
        '<link rel="stylesheet" href="/static/mobile.css" />\n</head>',
        '注入 mobile.css')
    html = _replace_once(
        html, '</body>',
        '<script src="/static/mobile.js"></script>'
        '<script src="/static/mobile_settings.js"></script>\n</body>',
        '注入 mobile.js / mobile_settings.js')
    return html


def mobile_js(js: str) -> str:
    """控制台脚本已外置：桌面专属逻辑在这里移除（原来误写在 index.html 上）。

    2026-11-04：控制台把内联脚本全部搬到 admin/console/console-app.js，
    旧改写规则一条都命中不了，APK 打包直接失败。这里按同样的语义作用在
    真正的脚本文件上；未命中的规则只记录，不阻断出包。
    """
    js = _sub_maybe(js, r'/\* ==================== 线上同步设置.*?'
                        r'(?=/\* ==================== 客户端兼容参数)',
                    'function loadSyncConfig() {}\n\n', '替换「线上同步设置」脚本')
    js = _sub_maybe(js, r'^.*\[`injectAcc\(.*\n', '', '移除「注入本机」菜单项', flags=re.M)
    js = _sub_maybe(js, r'async function injectAcc\(.*?(?=/\* 猫猫旅行)',
                    '', '移除 injectAcc 实现')
    js = _sub_maybe(js, r'/\* 扫描本机账号.*?(?=async function refreshAll\()',
                    '', '移除扫描本机账号实现')
    js = _sub_maybe(js, r'/\* ==================== 客户端兼容参数.*?'
                        r'(?=/\* ==================== 设置)',
                    '', '移除客户端参数脚本')
    js = _sub_maybe(js, r',"client"', '', 'TABS 去掉 client')
    js = _sub_maybe(js, r',"settings"', '', 'TABS 去掉 settings')
    js = _sub_maybe(js, r'^.*if \(name === "client"\).*\n', '',
                    '去掉 client 面板加载', flags=re.M)
    js = _sub_maybe(js, r'^.*if \(name === "settings"\).*\n', '',
                    '去掉 settings 面板加载', flags=re.M)
    return js


def prepare() -> None:
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
    shutil.copytree(ROOT / "admin/static", staging / "admin/static")
    shutil.copytree(ROOT / "admin/console", staging / "admin/console")
    shutil.copytree(ROOT / "admin/data", staging / "admin/data")
    # 平台自带的数据文件（如 trae/data/catalog_*.json）也要进包：
    # 上面的 rglob 只拷 *.py，这些 json 在真机/模拟器上一律缺文件，
    # 运行时只能吃「目录加载失败」告警走网络兜底。
    for platform_data in (ROOT / "admin/platform").glob("*/data"):
        shutil.copytree(platform_data, staging / platform_data.relative_to(ROOT))
    app_js = staging / "admin/console/console-app.js"
    app_js.write_text(mobile_js(app_js.read_text(encoding="utf-8")), encoding="utf-8")
    print("Mobile console script rewritten (desktop-only sections removed)")
    index = staging / "admin/console/index.html"
    index.write_text(mobile_html(index.read_text(encoding="utf-8")), encoding="utf-8")
    for name in ("mobile.js", "mobile.css", "mobile_settings.js"):
        shutil.copy2(ROOT / "android/web" / name, staging / "admin/static" / name)
    for name in ("android_runtime.py", "mobile_http.py"):
        shutil.copy2(ROOT / "android/python" / name, staging / name)
    shutil.copy2(ROOT / "LICENSE", staging / "LICENSE")
    print("Prepared mobile backend and UI (no desktop parameter page or scan tools)")


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
