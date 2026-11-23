"""Reproducible desktop packaging; never includes .env, logs or account data.

Windows 产出单文件 `miraihub-<版本>-windows-x64.exe`（附加 `.sha256`）；
macOS 产出 `miraihub-<版本>-macos-<arch>.zip`，里面是一个 `.app` 目录包
（同样附加 `.sha256`）。

两个平台共用同一份 `desktop/miraihub.spec`，图标在打包前由这里生成：

* Windows：`app.svg` → `app.png` → 多分辨率 `app.ico`（Pillow）；
* macOS：`app.svg` → `app.png`（1024）→ `app.iconset/*` → `app.icns`
  （只用系统自带的 `sips` / `iconutil`，不引入额外依赖）。

macOS 的 `.app` 用 `ditto` 打包成 zip：它保留可执行权限与包内符号链接，
用普通 `zip` 打出来的包解压后双击常常「应用已损坏」。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _machine_arch() -> str:
    machine = (platform.machine() or "").lower()
    return "arm64" if machine in ("arm64", "aarch64") else "x64"


def _render_png(assets: Path, size: int) -> None:
    """把 app.svg 渲染成 app.png（Windows 用 256，macOS 用 1024）。"""
    import resvg_py

    png = resvg_py.svg_to_bytes(
        svg_string=(assets / "app.svg").read_text(encoding="utf-8"),
        width=size, height=size)
    (assets / "app.png").write_bytes(png)


def _build_ico(assets: Path) -> None:
    from PIL import Image

    _render_png(assets, 256)
    Image.open(assets / "app.png").save(
        assets / "app.ico", format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def _build_icns(assets: Path) -> Path:
    """多尺寸 iconset → icns（sips / iconutil 是 macOS 自带的）。"""
    _render_png(assets, 1024)
    iconset = assets / "app.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        for scale, suffix in ((1, ""), (2, "@2x")):
            target = iconset / f"icon_{size}x{size}{suffix}.png"
            subprocess.run(["sips", "-z", str(size * scale), str(size * scale),
                            str(assets / "app.png"), "--out", str(target)],
                           check=True, capture_output=True)
    icns = assets / "app.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)],
                   check=True)
    shutil.rmtree(iconset, ignore_errors=True)
    return icns


def _write_windows_resource(version: str) -> None:
    parts = tuple(int(v) for v in version.split("."))
    resource = f'''VSVersionInfo(
      ffi=FixedFileInfo(filevers={parts + (0,)}, prodvers={parts + (0,)},
          mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),
      kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('CompanyName', 'MiraiHub'),
        StringStruct('FileDescription', 'MiraiHub Desktop'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('ProductName', 'MiraiHub'),
        StringStruct('ProductVersion', '{version}')])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])])])'''
    (ROOT / "build/version-resource.txt").write_text(resource, encoding="utf-8")


def _hash(path: Path) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", default="dist", help="Output folder; useful while an older client is running")
    #: PyInstaller 的工作目录（默认项目内 `build/`）。构建机磁盘紧张时，
    #: 用 `--work-dir D:/wb-build` 把中间产物挪到空间大的盘上 —— 之前 E 盘
    #: 只剩 0.3G 时，光靠 `--dist-dir` 是救不了的（临时文件还在 E 盘）。
    parser.add_argument("--work-dir", default="build", help="PyInstaller work folder (intermediate files)")
    args = parser.parse_args()

    destination = (ROOT / args.dist_dir).resolve()
    work = (ROOT / args.work_dir).resolve() if not os.path.isabs(args.work_dir) \
        else Path(args.work_dir).resolve()

    macos = sys.platform == "darwin"
    if macos:
        if _machine_arch() not in ("arm64", "x64"):
            raise SystemExit("Unsupported macOS architecture")
    else:
        if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
            raise SystemExit("Use Windows x64 or macOS (arm64/x64) with a 64-bit Python interpreter")
        if sys.maxsize <= 2**32:
            raise SystemExit("A 64-bit Python interpreter is required")

    subprocess.run([sys.executable, str(ROOT / 'scripts/check_runtime.py')], cwd=ROOT, check=True)

    version = json.loads((ROOT / "desktop/version.json").read_text(encoding="utf-8"))["version"]
    if len(tuple(int(v) for v in version.split("."))) != 3:
        raise SystemExit("desktop/version.json must contain a stable x.y.z version")

    assets = ROOT / "desktop/assets"
    (ROOT / "build").mkdir(exist_ok=True)
    if macos:
        _build_icns(assets)
    else:
        _build_ico(assets)
        _write_windows_resource(version)

    env = dict(os.environ, PYTHONUTF8="1")
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(destination),
                    "--workpath", str(work),
                    str(ROOT / "desktop/miraihub.spec")], cwd=ROOT, env=env, check=True)

    if macos:
        arch = _machine_arch()
        app = destination / f"miraihub-{version}-macos-{arch}.app"
        if not app.is_dir():
            raise SystemExit(f"PyInstaller did not produce {app}")
        archive = destination / f"miraihub-{version}-macos-{arch}.zip"
        if archive.exists():
            archive.unlink()
        # ditto 保留可执行位与包内符号链接；普通 zip 会做出「已损坏」的 app。
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                        str(app), str(archive)], check=True)
        _hash(archive)
        print(f"Built {archive} ({archive.stat().st_size / 1048576:.1f} MB)")
        return

    executable = destination / f"miraihub-{version}-windows-x64.exe"
    _hash(executable)
    print(f"Built {executable} ({executable.stat().st_size / 1048576:.1f} MB)")


if __name__ == "__main__":
    main()
