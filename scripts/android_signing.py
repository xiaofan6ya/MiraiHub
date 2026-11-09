"""统一的 Android 签名身份管理（**一次生成，长期复用**）。

## 为什么必须有这个模块

APK 覆盖安装的判据是「包名 + 签名证书」。同一包名换一把私钥，系统会直接
`INSTALL_FAILED_UPDATE_INCOMPATIBLE`，用户只能先卸载 —— 而卸载会带走
本地账号库。历史上有两种事故都会造成它：

1. **签名目录被清理**：早期签名材料放在 `build/android-signing/`，
   而 `build_desktop.py` 每次打包都会重建 `build/` —— 每发一版换一把钥匙；
2. **keystore 丢了但配置还在**：`signing.json`（密码）留着，`release.jks`
   被删，下一次打包会「用同一个密码重新生成一把新私钥」，全程没有任何
   报错，直到用户装更新失败。

本模块把签名身份收敛成一条明确的契约：

    android-signing/release.jks          私钥（**永不入库、永不重建**）
    android-signing/signing.json         别名 + 密码 + 证书指纹 + 创建时间
    android/expected-signing-cert.txt    证书 SHA-256（**可提交，公开信息**）

打包前 `verify()` 会拿 keystore 的真实指纹去比对 `expected-signing-cert.txt`：
不一致就**中止构建**，绝不再静默产出一个换过签名的 APK。
需要换钥匙时只能显式 `--rotate`，它会打印「这一版必须让用户卸载一次」的提示。

## 用法

    python scripts/android_signing.py --show        # 看当前身份
    python scripts/android_signing.py --verify      # 校验（不一致退出码 1）
    python scripts/android_signing.py --rotate      # 显式换钥匙（危险）
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 签名材料的默认位置。**必须在 `build/` 之外**：那个目录每次打包都会被重建。
DEFAULT_SIGNING_DIR = ROOT / "android-signing"
LEGACY_SIGNING_DIR = ROOT / "build/android-signing"

#: 证书指纹落这里的目的是「可提交、可 review」——它只是公钥指纹，不是秘密。
#: 有了它，任何一次「悄悄换了签名」的提交都能在 code review 里被看见。
EXPECTED_CERT_FILE = ROOT / "android/expected-signing-cert.txt"

KEYSTORE_NAME = "release.jks"
CONFIG_NAME = "signing.json"

#: 固定签名的参数。改动任何一项都等于换身份 —— 必须走 `--rotate`。
KEY_ALG = "RSA"
KEY_SIZE = 3072
VALIDITY_DAYS = 10000
DEFAULT_ALIAS = "workbuddy2api"
DNAME = "CN=MiraiHub Android, OU=Release, O=MiraiHub, C=CN"


class SigningError(RuntimeError):
    """签名身份相关的硬错误（构建必须中止）。"""


# ---------------------------------------------------------------------------
# 路径 / 配置
# ---------------------------------------------------------------------------
def signing_dir() -> Path:
    override = (os.environ.get("WB_ANDROID_SIGNING_DIR") or "").strip()
    return Path(override).resolve() if override else DEFAULT_SIGNING_DIR


def _migrate_legacy(target: Path) -> None:
    """把老位置（`build/android-signing`）的身份搬过来，别让升级过的人再丢一次。"""
    if target.exists() or not LEGACY_SIGNING_DIR.is_dir():
        return
    shutil.copytree(LEGACY_SIGNING_DIR, target)
    print("[signing] 已迁移签名身份：%s -> %s" % (LEGACY_SIGNING_DIR, target))


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_config(path: Path, config: dict) -> None:
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_config(target: Path | None = None) -> dict:
    target = target or signing_dir()
    config = _read_json(target / CONFIG_NAME)
    config.setdefault("alias", DEFAULT_ALIAS)
    # 历史配置只有 `password` 一个字段；store/key 同密码是当时的约定，
    # 这里补齐但保留原值，避免老身份失效。
    if "store_password" not in config and config.get("password"):
        config["store_password"] = config["password"]
    if "key_password" not in config and config.get("password"):
        config["key_password"] = config["password"]
    config.setdefault("keystore", KEYSTORE_NAME)
    config.setdefault("sha256", "")
    return config


# ---------------------------------------------------------------------------
# 证书指纹
# ---------------------------------------------------------------------------
def _keytool(jdk: str | None = None) -> str:
    if jdk:
        candidate = Path(jdk) / "bin" / ("keytool.exe" if os.name == "nt" else "keytool")
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("keytool")
    if not found:
        raise SigningError(
            "找不到 keytool（需要 JDK 17）。请安装 JDK 或用 --jdk 指定 JAVA_HOME。")
    return found


def cert_sha256(keystore: Path, store_password: str, alias: str,
                *, jdk: str | None = None) -> str:
    """读出 keystore 里证书的 SHA-256 指纹（大写十六进制，无冒号）。

    ## 为什么不用 `keytool -list -v` 的文本输出

    那条输出里同时有 `SHA1:` / `SHA256:` 两行指纹、还有
    `Signature algorithm name: SHA256withRSA`，在**中文 Windows** 上
    用 GBK 编码时整段会被替换字符打乱，正则经常抓不到 —— 一旦抓不到，
    签名校验就变成「每次都失败」或者「静默放过」，两种都不能接受。

    改成 `keytool -exportcert -rfc` 导出 PEM，再用 hashlib 自己算：
    输入是纯 ASCII，结果与 JDK 版本、系统编码、locale 全都无关。

    密码走 **stdin** 而不是命令行：`-storepass <明文>` 会出现在进程列表里
    （同一台机器上的其他用户能直接看到私钥口令）。
    """
    if not keystore.is_file():
        raise SigningError("keystore 不存在：%s" % keystore)
    proc = subprocess.run(
        [_keytool(jdk), "-exportcert", "-rfc", "-alias", alias,
         "-keystore", str(keystore)],
        input=store_password + "\n",
        capture_output=True, text=True, encoding="ascii", errors="replace")
    pem = proc.stdout or ""
    if proc.returncode != 0 or "BEGIN CERTIFICATE" not in pem:
        detail = (proc.stderr or pem).strip()[:400]
        raise SigningError(
            "读取证书失败（密码或别名不对）：%s" % (detail or "keytool 没有输出证书"))
    body = "".join(line.strip() for line in pem.splitlines()
                   if line.strip() and not line.startswith("-----"))
    try:
        der = base64.b64decode(body, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise SigningError("证书 PEM 解析失败：%s" % exc)
    return hashlib.sha256(der).hexdigest().upper()


def expected_sha256() -> str:
    """仓库里登记的期望指纹（空串表示还没登记过）。

    只认 `sha256=<hex>` 这一行。早期实现「取第一个非注释行」——
    文件里同时有 `alias=` 行时，取到的会是 `workbuddy2api`，
    校验于是永远失败（或者被当成指纹乱比）。这里按 key 解析。
    兼容裸一行指纹的旧格式（没有 `=` 且看起来像指纹）。
    """
    try:
        text = EXPECTED_CERT_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            if key.strip().lower() == "sha256":
                return value.strip().replace(":", "").upper()
            continue
        candidate = line.replace(":", "").upper()
        if re.fullmatch(r"[0-9A-F]{64}", candidate):
            return candidate
    return ""


def _write_expected(digest: str, alias: str) -> None:
    EXPECTED_CERT_FILE.parent.mkdir(parents=True, exist_ok=True)
    EXPECTED_CERT_FILE.write_text(
        "# Android 发布签名证书的 SHA-256（公钥指纹，不是秘密，可以提交）\n"
        "#\n"
        "# 打包前 scripts/android_signing.py --verify 会做比对：\n"
        "# 不一致 = APK 换了签名 = 老用户必须卸载重装（会丢本地账号库）。\n"
        "# 只有在**故意**更换签名身份时才允许更新本文件（--rotate）。\n"
        "alias=%s\n"
        "sha256=%s\n" % (alias, digest),
        encoding="utf-8")


def _fingerprint_note(digest: str) -> str:
    spaced = ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))
    return spaced


# ---------------------------------------------------------------------------
# 创建 / 校验
# ---------------------------------------------------------------------------
def ensure_identity(*, jdk: str | None = None, rotate: bool = False,
                    quiet: bool = False) -> dict:
    """确保签名身份存在且与登记值一致；返回可直接注入 gradle 的环境变量。

    ## 四条硬规则

    1. keystore 已存在 → **永不重新生成**（换钥匙只能 `rotate=True`）；
    2. `signing.json` 缺失但 keystore 在 → 从 keystore 反推密码不可行，
       直接报错让人找回配置或显式 rotate（不要瞎猜密码把号废掉）；
    3. 已登记期望指纹 → 必须一致，否则中止构建；
    4. 未登记 → 首次登记（只在 keystore 是这次新建的，或显式 rotate 时发生）。
    """
    target = signing_dir()
    _migrate_legacy(target)
    target.mkdir(parents=True, exist_ok=True)
    config_path = target / CONFIG_NAME
    keystore = target / (load_config(target).get("keystore") or KEYSTORE_NAME)

    if rotate and keystore.is_file():
        backup = keystore.with_suffix(".jks.bak-%s" % datetime.now(timezone.utc)
                                      .strftime("%Y%m%d-%H%M%S"))
        keystore.rename(backup)
        if not quiet:
            print("[signing] 旧身份已备份：%s" % backup.name)

    config = load_config(target)
    created = False
    if not keystore.is_file():
        if config_path.is_file() and not rotate:
            # 配置在、私钥没了：绝不能拿旧密码重新生成一把新钥匙 ——
            # 那正是「静默换签名」的事故形态。
            raise SigningError(
                "签名配置存在但私钥 %s 丢失。\n"
                "请先找回备份（android-signing/ 目录、或构建机的备份）。\n"
                "确实要换签名身份时再显式执行：python scripts/android_signing.py --rotate\n"
                "注意：换签名后所有老用户必须先卸载再安装（会丢本地账号库）。"
                % keystore)
        password = secrets.token_urlsafe(40)
        config.update({
            "alias": config.get("alias") or DEFAULT_ALIAS,
            "keystore": keystore.name,
            "store_password": password,
            "key_password": password,
            "password": password,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "key_alg": KEY_ALG, "key_size": KEY_SIZE,
            "validity_days": VALIDITY_DAYS,
        })
        _write_config(config_path, config)
        env = {**os.environ,
               "WB_ANDROID_STORE_PASSWORD": password,
               "WB_ANDROID_KEY_PASSWORD": password}
        subprocess.run(
            [_keytool(jdk), "-genkeypair", "-keystore", str(keystore),
             "-storetype", "JKS",
             "-storepass:env", "WB_ANDROID_STORE_PASSWORD",
             "-keypass:env", "WB_ANDROID_KEY_PASSWORD",
             "-alias", config["alias"],
             "-keyalg", KEY_ALG, "-keysize", str(KEY_SIZE),
             "-validity", str(VALIDITY_DAYS), "-dname", DNAME],
            env=env, check=True)
        created = True
        if not quiet:
            print("[signing] 已生成新的签名身份：%s（alias=%s）"
                  % (keystore.name, config["alias"]))
    elif not config_path.is_file():
        raise SigningError(
            "私钥 %s 存在但缺少 %s（密码丢失）。\n"
            "密码无法从 JKS 反推。请找回 signing.json 备份，"
            "或显式 --rotate 重新生成一套身份（老用户需卸载重装）。"
            % (keystore.name, CONFIG_NAME))

    digest = cert_sha256(keystore, config["store_password"],
                         config["alias"], jdk=jdk)
    expected = expected_sha256()
    if not expected:
        _write_expected(digest, config["alias"])
        if not quiet:
            print("[signing] 已登记签名指纹：%s" % _fingerprint_note(digest))
    elif expected != digest:
        hint = ""
        if rotate:
            _write_expected(digest, config["alias"])
            hint = ("\n已按 --rotate 更新登记文件。**这一版必须让老用户卸载重装**，"
                    "并在更新公告里写清楚。")
        raise SigningError(
            "签名身份与登记值不一致，已中止构建。\n"
            "  登记值：%s\n"
            "  当前值：%s\n"
            "这说明 keystore 被换过/重建过 —— 直接出包会让老用户"
            "INSTALL_FAILED_UPDATE_INCOMPATIBLE（只能卸载，本地账号库会丢）。\n"
            "请恢复原来的 android-signing/release.jks；"
            "确实要换就显式 --rotate。%s"
            % (_fingerprint_note(expected), _fingerprint_note(digest), hint))

    config["sha256"] = digest
    config["keystore_path"] = str(keystore)
    _write_config(config_path, config)
    if created and not quiet:
        print("[signing] 指纹：%s" % _fingerprint_note(digest))
    return {
        "dir": str(target),
        "keystore": str(keystore),
        "alias": config["alias"],
        "sha256": digest,
        "created": created,
        "env": {
            "WB_ANDROID_KEYSTORE": str(keystore),
            "WB_ANDROID_STORE_PASSWORD": config["store_password"],
            "WB_ANDROID_KEY_PASSWORD": config["key_password"],
            "WB_ANDROID_KEY_ALIAS": config["alias"],
        },
    }


def verify(*, jdk: str | None = None) -> dict:
    """只校验、不创建（CI / 打包前置检查用）。"""
    target = signing_dir()
    config_path = target / CONFIG_NAME
    keystore = target / (load_config(target).get("keystore") or KEYSTORE_NAME)
    if not config_path.is_file() or not keystore.is_file():
        raise SigningError(
            "签名身份不完整：%s 或 %s 缺失。\n"
            "第一次打包会自动生成；已有历史包的情况下请先找回备份。"
            % (keystore, config_path))
    config = load_config(target)
    digest = cert_sha256(keystore, config["store_password"],
                         config["alias"], jdk=jdk)
    expected = expected_sha256()
    if expected and expected != digest:
        raise SigningError("签名指纹不一致：登记 %s / 实际 %s"
                           % (_fingerprint_note(expected),
                              _fingerprint_note(digest)))
    return {"ok": True, "dir": str(target), "keystore": str(keystore),
            "alias": config["alias"], "sha256": digest,
            "expected": expected or "(未登记)"}


def verify_apk(apk: Path, *, jdk: str | None = None) -> dict:
    """用 apksigner 校验**产物 APK** 的签名证书，证明出包用了登记身份。

    `apksigner` 属于 Android SDK build-tools；没有 SDK 的机器上跳过，
    但 keystore 指纹校验（`ensure_identity` / `verify`）依旧生效。
    """
    sdk = (os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
           or "").strip()
    exe = ""
    if sdk:
        bt = Path(sdk) / "build-tools"
        if bt.is_dir():
            for version in sorted(bt.iterdir(), reverse=True):
                candidate = version / ("apksigner.bat" if os.name == "nt" else "apksigner")
                if candidate.is_file():
                    exe = str(candidate)
                    break
    if not exe:
        found = shutil.which("apksigner")
        exe = found or ""
    expected = expected_sha256()
    if not exe:
        return {"ok": True, "skipped": True,
                "reason": "未找到 apksigner（无 Android SDK build-tools），跳过产物校验",
                "expected": expected}
    if not apk.is_file():
        raise SigningError("APK 不存在：%s" % apk)
    proc = subprocess.run([exe, "verify", "--print-certs", str(apk)],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SigningError("apksigner 校验失败：%s"
                           % (proc.stderr or proc.stdout).strip()[:400])
    match = re.search(r"certificate SHA-256 digest:\s*([0-9a-fA-F]+)", proc.stdout)
    actual = match.group(1).upper() if match else ""
    if expected and actual and actual != expected:
        raise SigningError(
            "产物 APK 的签名证书与登记值不一致：%s != %s"
            % (_fingerprint_note(actual), _fingerprint_note(expected)))
    return {"ok": True, "skipped": False, "apk": str(apk),
            "sha256": actual, "expected": expected or "(未登记)"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Android 发布签名身份管理")
    parser.add_argument("--show", action="store_true", help="显示当前签名身份")
    parser.add_argument("--verify", action="store_true", help="只校验不创建")
    parser.add_argument("--rotate", action="store_true",
                        help="显式更换签名身份（老用户必须卸载重装）")
    parser.add_argument("--jdk", help="JDK 目录（默认用 PATH 里的 keytool）")
    args = parser.parse_args()

    try:
        if args.verify:
            info = verify(jdk=args.jdk)
            print("签名身份校验通过")
        else:
            info = ensure_identity(jdk=args.jdk, rotate=args.rotate)
    except SigningError as exc:
        print("[signing] 失败：%s" % exc, file=sys.stderr)
        return 1

    print("  目录：%s" % info.get("dir", signing_dir()))
    print("  私钥：%s" % info["keystore"])
    print("  别名：%s" % info["alias"])
    print("  证书 SHA-256：%s" % _fingerprint_note(info["sha256"]))
    print("  登记文件：%s" % EXPECTED_CERT_FILE.relative_to(ROOT))
    if args.rotate:
        print("\n⚠️ 已更换签名身份：这一版 APK 必须让用户先卸载再安装，"
              "并提前在更新公告里说明。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())