"""Private, cached Node runtime. Python and all application dependencies are frozen.

## 为什么三端各带一份 Node

桌面端有一部分能力（`turing_helper.js` 那套）跑在 Node 里，而用户机器上不一定
装了 Node、装了也不一定是我们要的版本。所以客户端自己下载一份钉死版本的
运行时，解压到数据目录的 `runtime/` 下，**只在这个进程里生效**（改 PATH，
不动系统环境）。

## 校验

每个平台的归档都在 `NODE_PINS` 里钉死 sha256（来自 nodejs.org 的
`dist/<版本>/SHASUMS256.txt`）。镜像只负责带宽，不负责正确性 —— 下载完先比
sha256，不一致就当这个源失败、换下一个源。这样即使某个镜像被投毒，也进不来。

## 平台

| 平台 | 归档 | 可执行文件名 |
|---|---|---|
| Windows x64 | `node-v22.14.0-win-x64.zip` | `node.exe` |
| macOS arm64 | `node-v22.14.0-darwin-arm64.tar.gz` | `bin/node` |
| macOS x64 | `node-v22.14.0-darwin-x64.tar.gz` | `bin/node` |

Windows 的 WebView2 需要单独装（系统组件）；macOS / Linux 的 WebView 由系统
自带（WKWebView / WebKitGTK），`ensure_webview2()` 在那些平台是空操作。
"""
from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

NODE_VERSION = "22.14.0"

#: 平台键 -> (归档文件名, sha256)。sha256 来自 nodejs.org 的 SHASUMS256.txt。
NODE_PINS = {
    "win32-x64": (
        f"node-v{NODE_VERSION}-win-x64.zip",
        "55b639295920b219bb2acbcfa00f90393a2789095b7323f79475c9f34795f217",
    ),
    "darwin-arm64": (
        f"node-v{NODE_VERSION}-darwin-arm64.tar.gz",
        "e9404633bc02a5162c5c573b1e2490f5fb44648345d64a958b17e325729a5e42",
    ),
    "darwin-x64": (
        f"node-v{NODE_VERSION}-darwin-x64.tar.gz",
        "6698587713ab565a94a360e091df9f6d91c8fadda6d00f0cf6526e9b40bed250",
    ),
}

#: 镜像（只用带宽，正确性由 sha256 保证）。
MIRRORS = ("https://npmmirror.com/mirrors/node/v{version}/{archive}",
           "https://mirrors.huaweicloud.com/nodejs/v{version}/{archive}",
           "https://nodejs.org/dist/v{version}/{archive}")


def platform_key() -> str:
    """`win32-x64` / `darwin-arm64` / `darwin-x64`（其它平台直接不支持）。"""
    machine = (platform.machine() or "").lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    return "%s-%s" % (sys.platform, arch)


def node_archive() -> tuple[str, str]:
    """当前平台的 `(归档名, sha256)`；不支持的平台抛错而不是瞎猜。"""
    key = platform_key()
    pin = NODE_PINS.get(key)
    if pin is None:
        raise RuntimeError(
            "桌面版暂不支持这个平台（%s）：目前提供 Windows x64 与 macOS "
            "arm64/x64" % key)
    return pin


def node_sources() -> list[str]:
    """按顺序尝试的下载地址。"""
    archive, _ = node_archive()
    return [template.format(version=NODE_VERSION, archive=archive) for template in MIRRORS]


def download(url: str, target: Path, progress=None, max_bytes=300 * 1024 * 1024) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "MiraiHub-Desktop"})
    digest = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(request, timeout=30) as response, target.open("wb") as out:
        total = int(response.headers.get("Content-Length", 0))
        if total > max_bytes:
            raise ValueError("下载文件超出大小限制")
        while chunk := response.read(256 * 1024):
            done += len(chunk)
            if done > max_bytes:
                raise ValueError("下载文件超出大小限制")
            out.write(chunk)
            digest.update(chunk)
            if progress:
                progress(done, total)
    return digest.hexdigest()


def node_dir(base: Path) -> Path:
    return base / "runtime" / f"node-{NODE_VERSION}"


def node_executable(base: Path) -> Path:
    """Node 可执行文件（Windows 是 `node.exe`，其它平台是 `node`）。"""
    return node_dir(base) / ("node.exe" if os.name == "nt" else "node")


def configure_node(base: Path) -> bool:
    binary = node_executable(base)
    if binary.is_file():
        os.environ["PATH"] = str(binary.parent) + os.pathsep + os.environ.get("PATH", "")
        return True
    return False


def _extract(archive: Path, staged: Path, *, name: str) -> None:
    """把归档里的 node 可执行文件与 LICENSE 解到 `staged/`。"""
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            root = f"node-v{NODE_VERSION}-win-x64/"
            (staged / "node.exe").write_bytes(bundle.read(root + "node.exe"))
            for extra in ("LICENSE", "README.md"):
                try:
                    (staged / extra).write_bytes(bundle.read(root + extra))
                except KeyError:
                    pass
        return
    # tar.gz：`<root>/bin/node`，其余是文档
    with tarfile.open(archive, "r:gz") as bundle:
        root = "node-v%s-%s/" % (NODE_VERSION, platform_key())
        binary = bundle.extractfile(root + "bin/node")
        if binary is None:
            raise ValueError("归档里没有 bin/node")
        (staged / "node").write_bytes(binary.read())
        (staged / "node").chmod(0o755)
        for extra in ("LICENSE", "README.md"):
            member = bundle.extractfile(root + extra)
            if member is not None:
                (staged / extra).write_bytes(member.read())


def ensure_node(base: Path, status) -> Path:
    destination = node_dir(base)
    binary = node_executable(base)
    if binary.is_file():
        return destination
    archive_name, expected = node_archive()
    destination.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    with tempfile.TemporaryDirectory(dir=destination.parent) as work:
        archive = Path(work) / ("node.zip" if archive_name.endswith(".zip") else "node.tar.gz")
        for url in node_sources():
            try:
                status("正在从镜像准备 Node.js（首次启动）")
                actual = download(url, archive, lambda n, total: status(
                    f"准备 Node.js：{n / 1048576:.1f} / {total / 1048576:.1f} MB"))
                if actual != expected:
                    raise ValueError("Node.js SHA256 校验失败")
                staged = Path(work) / "ready"
                staged.mkdir()
                _extract(archive, staged, name=archive_name)
                probe = staged / ("node.exe" if os.name == "nt" else "node")
                subprocess.run([str(probe), "--version"], check=True,
                               capture_output=True, timeout=10,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                staged.replace(destination)
                return destination
            except Exception as exc:  # noqa: BLE001 - 一个源失败就换下一个
                errors.append(str(exc))
    raise RuntimeError("Node.js 自动准备失败，可在版本窗口重试：" + "; ".join(errors))


def has_webview2() -> bool:
    """Windows 才需要 WebView2；macOS / Linux 用系统自带的 WebView。"""
    if os.name != "nt":
        return True
    import winreg
    key = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as handle:
                    version, _ = winreg.QueryValueEx(handle, "pv")
                    if int(version.split(".")[0]) >= 86:
                        return True
            except (OSError, ValueError):
                pass
    return False


def ensure_webview2(base: Path) -> None:
    if has_webview2():
        return
    # Microsoft distributes the evergreen bootstrapper from this canonical link.
    # It installs per-user silently; never replace it with an unsigned third-party mirror.
    installer = base / "runtime" / "MicrosoftEdgeWebview2Setup.exe"
    installer.parent.mkdir(parents=True, exist_ok=True)
    download("https://go.microsoft.com/fwlink/p/?LinkId=2124703", installer, max_bytes=10*1048576)
    os.environ["WB_WEBVIEW_INSTALLER"] = str(installer)
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
               "$s=Get-AuthenticodeSignature -LiteralPath $env:WB_WEBVIEW_INSTALLER; "
               "if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'Microsoft Corporation'){exit 1}"]
    subprocess.run(command, check=True, capture_output=True, timeout=30,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    subprocess.run([str(installer), "/silent", "/install"], check=True, timeout=300,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    if not has_webview2():
        raise RuntimeError("WebView2 安装未完成，请检查网络后再次双击 EXE")
