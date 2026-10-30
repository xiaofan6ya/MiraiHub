#!/usr/bin/env python
"""凭证定位：在**不读进程内存、不问用户**的前提下找出凭证文件。

## 为什么不用内存扫描

内存扫描的致命缺陷：**必须让客户端开着**。
用户关掉客户端你就抓不到，这不能作为产品能力。
（实测被拒绝：「我不开你怎么读？」）

正确做法是**读源码弄清它存在哪**，然后直接读文件。

## 这个脚本做的事

1. 从源码里找 `new Store({ name: 'xxx' })` / `electron-store` 的存储名
2. 找出自定义的 userDataPath（很多产品不用默认位置！）
3. 在候选目录里找凭证文件并判断是否加密
4. 找到明文就提取并**验证**（打上游只读接口）

## 关键教训（实测踩过）

`%APPDATA%\\<产品名>` 经常**不是**真正的数据目录：

    Qoder   %APPDATA%\\Qoder CN            ✗（只有 Launcher）
            %APPDATA%\\com.qodercn.app.stable  ✓
    Loomy   %APPDATA%\\loomy                ✗（只有 cookie / cache）
            C:\\Users\\Public\\Loomy\\<sha256(用户名)[:12]>\\userData  ✓
            ↑ 这个位置是 `config-path.js` 里算出来的，猜不到

**所以必须读源码里的路径构造函数，不能靠猜目录名。**

## 用法

    # 1) 先从源码里找存储名与路径函数
    python creds.py --source "D:\\App\\resources\\app.asar.unpacked"
    # 2) 按提示的目录读文件
    python creds.py --dir "<上面找到的目录>" --verify-url <只读接口>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import ssl
import sys
import urllib.error
import urllib.request

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

#: 源码里的存储名声明（electron-store / conf 都长这样）
STORE_NAME_RE = re.compile(
    r"new\s+Store\s*\(\s*\{[^}]*?name\s*:\s*['\"]([a-zA-Z0-9_\-\.]+)['\"]", re.S)
#: `createStore({ name: 'x' })` 这类
STORE_NAME_RE2 = re.compile(
    r"name\s*:\s*['\"]([a-zA-Z0-9_\-\.]+)['\"]\s*,\s*defaults", re.S)
#: 路径构造函数
PATH_FN_RE = re.compile(
    r"function\s+(get\w*(?:Data|Config|Profile|State|Share)\w*)\s*\([^)]*\)\s*\{(.{0,900}?)\n\}", re.S)
#: 凭证字段名
CRED_KEYS = ("session", "token", "access_token", "accessToken",
             "refresh_token", "refreshToken", "security_oauth_token",
             "apiKey", "api_key", "personal_token", "cookie", "sid")


def find_source_files(root: pathlib.Path, min_kb: int = 1) -> list[pathlib.Path]:
    out = []
    for pattern in ("**/*.js", "**/*.mjs", "**/*.cjs"):
        for item in root.glob(pattern):
            try:
                if item.is_file() and item.stat().st_size >= min_kb * 1024:
                    out.append(item)
            except OSError:
                continue
    return out


def analyse_source(root: pathlib.Path) -> dict:
    """从源码里提取：存储名、路径函数、凭证字段名。"""
    result: dict = {"stores": set(), "path_functions": {}, "sources": []}
    if not root.is_dir():
        return {"error": "目录不存在: %s" % root}

    files = find_source_files(root)
    print("=== 扫 %d 个源文件 ===" % len(files))

    for item in files:
        try:
            text = item.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hit = False
        for match in STORE_NAME_RE.finditer(text):
            result["stores"].add(match.group(1))
            hit = True
        for match in STORE_NAME_RE2.finditer(text):
            result["stores"].add(match.group(1))
            hit = True
        for match in PATH_FN_RE.finditer(text):
            result["path_functions"][match.group(1)] = match.group(2).strip()[:600]
            hit = True
        if hit:
            result["sources"].append(str(item.relative_to(root)))

    result["stores"] = sorted(result["stores"])
    result["sources"] = result["sources"][:20]
    return result


def candidate_dirs(app_name: str = "") -> list[pathlib.Path]:
    """常见数据目录（含「产品名 ≠ 目录名」的变形）。"""
    out: list[pathlib.Path] = []
    for env in ("APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(env)
        if value:
            out.append(pathlib.Path(value))
    out.append(pathlib.Path.home() / "AppData/Roaming")
    # Windows 特有：Public\<Product>\<hash>
    public = pathlib.Path("C:/Users/Public")
    if public.is_dir():
        out.append(public)

    found = []
    for root in out:
        if not root.is_dir():
            continue
        if app_name:
            clean = re.sub(r"[^A-Za-z0-9]", "", app_name).lower()
            try:
                for child in root.iterdir():
                    if not child.is_dir():
                        continue
                    name = child.name.lower()
                    if clean and (clean in name or name in clean):
                        found.append(child)
            except OSError:
                continue
    return found


def inspect_file(path: pathlib.Path) -> dict:
    try:
        size = path.stat().st_size
        raw = path.read_bytes()[:4096]
    except OSError as exc:
        return {"path": str(path), "error": str(exc)[:80]}

    info = {"path": str(path), "bytes": size}
    if raw[:1] in (b"{", b"["):
        info["format"] = "json（明文）"
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            info["keys"] = list(data.keys())[:12] if isinstance(data, dict) else []
            creds = {}
            for key, value in (data.items() if isinstance(data, dict) else []):
                if key.lower() in [k.lower() for k in CRED_KEYS] and isinstance(value, str):
                    creds[key] = value
            if creds:
                info["credentials"] = creds
        except ValueError:
            info["format"] = "json（解析失败）"
    elif raw[:3] == b"v10":
        info["format"] = "DPAPI 加密（v10）"
    elif raw[:2] == b"\x1f\x8b":
        info["format"] = "gzip"
    else:
        text = raw[:120].decode("utf-8", "replace")
        printable = sum(1 for ch in text if ch.isprintable())
        info["format"] = "文本" if printable > len(text) * 0.8 else "二进制"
        info["sample"] = text[:80]
    return info


def scan_dir(root: pathlib.Path, max_depth: int = 3) -> list[dict]:
    out = []
    if not root.is_dir():
        return out
    base_depth = len(root.parts)
    for current, dirs, files in os.walk(root):
        if len(pathlib.Path(current).parts) - base_depth > max_depth:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs
                   if d.lower() not in ("cache", "code cache", "gpucache",
                                        "crashpad", "blob_storage", "node_modules",
                                        "leveldb", "partitions")]
        for name in files:
            lowered = name.lower()
            # 只看可能是凭证的文件
            if not any(marker in lowered for marker in
                       ("session", "auth", "credential", "token", "secret", "login")):
                continue
            if lowered.endswith((".log", ".html", ".css", ".map", ".pak")):
                continue
            path = pathlib.Path(current) / name
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            out.append(inspect_file(path))
    return out


def verify(url: str, token: str, header: str = "Authorization",
           scheme: str = "Bearer", extra: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url)
    req.add_header(header, ("%s %s" % (scheme, token)) if scheme else token)
    if header.lower() == "authorization":
        req.add_header("token", token)     # 双头模式
    req.add_header("Accept", "application/json")
    for key, value in (extra or {}).items():
        req.add_header(key, str(value))
    try:
        with urllib.request.urlopen(req, timeout=20, context=CTX) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")[:200]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:200]
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)[:120]


def main() -> int:
    ap = argparse.ArgumentParser(description="凭证定位（不读内存、不问用户）")
    ap.add_argument("--source", help="解包后的源码目录（找存储名与路径函数）")
    ap.add_argument("--app-name", default="", help="产品名（猜目录用）")
    ap.add_argument("--dir", action="append", default=[], help="直接扫这个目录")
    ap.add_argument("--verify-url", default="", help="只读接口，用于验证 token")
    ap.add_argument("--header", default="Authorization")
    ap.add_argument("--scheme", default="Bearer")
    ap.add_argument("--out", default="creds-report.json")
    args = ap.parse_args()

    report: dict = {}

    # ---- 1) 源码分析 ----
    if args.source:
        print("=" * 60)
        print("源码分析:", args.source)
        info = analyse_source(pathlib.Path(args.source))
        report["source"] = info
        if info.get("error"):
            print("  ", info["error"])
        else:
            print("\n=== electron-store 存储名 ===")
            for name in info["stores"]:
                print("  %-30s → userData/%s.json" % (name, name))
                if any(k in name.lower() for k in ("auth", "session", "cred", "token", "login")):
                    print("      ★ 这个很可能存凭证")
            print("\n=== 自定义路径函数 ===")
            for fn, body in info["path_functions"].items():
                print("  %s():" % fn)
                for line in body.splitlines()[:8]:
                    if line.strip():
                        print("      ", line.strip()[:110])

    # ---- 2) 目录扫描 ----
    dirs = [pathlib.Path(os.path.expandvars(d)) for d in args.dir]
    if args.app_name and not dirs:
        dirs = candidate_dirs(args.app_name)
        if dirs:
            print("\n=== 候选目录（按产品名猜，可能不全）===")
            for path in dirs:
                print("  ", path)

    findings: list[dict] = []
    for root in dirs:
        print("\n=== 扫描 %s ===" % root)
        found = scan_dir(root)
        findings.extend(found)
        for item in found:
            rel = item["path"].replace(str(root), "").lstrip("\\/")
            print("  %-40s %8sB  %s" % (rel[:40], item.get("bytes", "?"),
                                        item.get("format", "?")))
            if item.get("credentials"):
                for key, value in item["credentials"].items():
                    shown = value[:8] + "…" + value[-4:] if len(value) > 16 else "***"
                    print("      %-18s %s (len=%d)" % (key, shown, len(value)))

    report["findings"] = findings

    # ---- 3) 验证 ----
    creds = [c for item in findings for c in (item.get("credentials") or {}).items()]
    if args.verify_url and creds:
        print("\n=== 验证 ===")
        for key, value in creds:
            code, body = verify(args.verify_url, value, args.header, args.scheme)
            ok = code == 200 and "失效" not in body and "INVALID" not in body.upper()
            print("  %-18s HTTP %-4s %s" % (key, code, "有效 ★" if ok else "无效"))
            if ok:
                print("      ", body[:120])

    print("\n=== 结论 ===")
    plain = [i for i in findings if i.get("credentials")]
    if plain:
        print("  ★ 找到明文凭证 %d 处 —— 可直接实现「扫描本机导入」" % len(plain))
    elif any("DPAPI" in (i.get("format") or "") for i in findings):
        print("  凭证是 DPAPI 加密 —— 本机用户上下文可解（Windows）")
    else:
        print("  没找到明文凭证。下一步：")
        print("    1) 检查源码里的自定义 userDataPath（很多产品不用默认位置）")
        print("    2) 找 deviceToken / oauth 接口，走设备流授权")
        print("    3) 最后才考虑让用户手动粘一次")

    out = pathlib.Path(args.out)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str),
                   encoding="utf-8")
    print("\n报告:", out.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
