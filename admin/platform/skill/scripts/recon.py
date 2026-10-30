#!/usr/bin/env python
"""拆包侦察：从一个陌生客户端找出上游接口（**不需要任何凭证**）。

## 这个脚本解决什么问题

真实情况是：用户通常**不会**给你 PAT / token / cookie。
你必须能在无凭证状态下把「接口长什么样」摸清楚：

  * 有哪些主机
  * 有哪些路径
  * 请求头叫什么
  * 鉴权是 Bearer / 自定义签名 / 还是别的

拿到这些，才能设计「用户怎么授权」（设备流 / 扫码 / 自己在界面登录）。

## 用法

    python recon.py "<安装目录>"
    python recon.py "D:\\SomeApp" --extract worker

输出 `recon-report.json` + 控制台摘要。

## 为什么先拆 asar 再搜索

Electron 应用的业务代码常被拆到 `app.asar.unpacked` 的独立 SDK 里
（AI/agent 模块更新频繁，放外面便于热更）。
**在主进程里搜不到 URL，不代表没有接口** —— 这是最常见的误判。

## 依赖

只用标准库 + Node.js（解 asar 需要）。不装任何第三方包。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

#: 关注的关键词（用于给命中排序）
INTEREST = (
    "chat/completions", "v1/messages", "/v1/responses", "completions",
    "anthropic", "openai", "bearer", "authorization", "x-api-key",
    "signature", "cosy", "quota", "usage", "balance", "plan",
    "checkin", "campaign", "signin", "reward", "credit",
)

#: URL 正则（排除注释里的示例域名）
URL_RE = re.compile(
    rb"https?://[a-zA-Z0-9][a-zA-Z0-9\.\-]{2,80}\.[a-z]{2,12}(?::\d{1,5})?(?:/[a-zA-Z0-9_\-./{}$:]*)?")
#: 路径正则（"/xxx/yyy" 形态）
PATH_RE = re.compile(rb'"(/[a-zA-Z0-9_\-][a-zA-Z0-9_\-/]{3,90})"')
#: 头名正则
HEADER_RE = re.compile(rb'["\']([A-Za-z][A-Za-z0-9\-]{2,40})["\']\s*[,:)]')
#: 环境变量名（可覆盖 host 的入口）
ENV_RE = re.compile(rb'process\.env\.([A-Z][A-Z0-9_]{4,50})')
#: 常见鉴权头名
AUTH_HEADERS = (
    "authorization", "x-api-key", "x-auth-token", "x-access-token",
    "x-token", "x-session", "cookie", "x-csrf-token", "x-signature",
)


def find_asar(app_dir: pathlib.Path) -> list[pathlib.Path]:
    """找所有 asar（含 unpacked 目录）。"""
    out = []
    for base in (app_dir / "resources", app_dir):
        if not base.is_dir():
            continue
        for pattern in ("**/app.asar", "**/*.asar"):
            out.extend(base.glob(pattern))
    seen, result = set(), []
    for item in out:
        key = str(item.resolve())
        if key not in seen and item.is_file():
            seen.add(key)
            result.append(item)
    return result


def find_large_js(app_dir: pathlib.Path, min_kb: int = 200) -> list[pathlib.Path]:
    """找大体积 JS（业务最可能在这里）。"""
    out = []
    for pattern in ("**/*.js", "**/*.mjs", "**/*.cjs"):
        for item in app_dir.glob(pattern):
            try:
                if item.is_file() and item.stat().st_size >= min_kb * 1024:
                    out.append(item)
            except OSError:
                continue
    # 打分优先，体积只作次要参考
    out.sort(key=lambda p: (-score_path(str(p)), -p.stat().st_size))
    return out


LIST_JS = r"""
const fs = require("fs");
const p = process.argv[2];
const buf = fs.readFileSync(p);
const headerSize = buf.readUInt32LE(12);
const header = JSON.parse(buf.slice(16, 16 + headerSize).toString("utf8"));
const out = [];
(function walk(node, prefix) {
  for (const [name, info] of Object.entries(node.files || {})) {
    const full = prefix + "/" + name;
    if (info.files) walk(info, full);
    else out.push({ path: full, size: info.size, offset: Number(info.offset) });
  }
})(header, "");
process.stdout.write(JSON.stringify({ headerSize, files: out }));
"""

EXTRACT_JS = r"""
const fs = require("fs");
const path = require("path");
const [,, asarPath, dest, ...targets] = process.argv;
const buf = fs.readFileSync(asarPath);
const headerSize = buf.readUInt32LE(12);
const header = JSON.parse(buf.slice(16, 16 + headerSize).toString("utf8"));
const aligned = Math.ceil(headerSize / 4) * 4;      // 4 字节对齐，少这一步解出乱码
const base = 8 + 8 + aligned;
function find(node, parts) {
  let cur = node;
  for (const part of parts) {
    if (!cur.files || !cur.files[part]) return null;
    cur = cur.files[part];
  }
  return cur;
}
for (const target of targets) {
  const entry = find(header, target.split("/").filter(Boolean));
  if (!entry || entry.files) { console.error("MISS " + target); continue; }
  const start = base + Number(entry.offset);
  const out = path.join(dest, target.replace(/[\/\\]/g, "_"));
  fs.writeFileSync(out, buf.slice(start, start + entry.size));
  console.log("OK " + out);
}
"""


def node_bin() -> str | None:
    return shutil.which("node")


def asar_list(asar: pathlib.Path, workdir: pathlib.Path) -> list[dict]:
    script = workdir / "_list.js"
    script.write_text(LIST_JS, encoding="utf-8")
    proc = subprocess.run([node_bin(), str(script), str(asar)],
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError("asar 列表失败：%s" % proc.stderr[:400])
    return json.loads(proc.stdout).get("files", [])


def asar_extract(asar: pathlib.Path, workdir: pathlib.Path,
                 targets: list[str]) -> list[pathlib.Path]:
    script = workdir / "_extract.js"
    script.write_text(EXTRACT_JS, encoding="utf-8")
    proc = subprocess.run([node_bin(), str(script), str(asar), str(workdir), *targets],
                          capture_output=True, text=True, timeout=600)
    out = []
    for line in (proc.stdout or "").splitlines():
        if line.startswith("OK "):
            out.append(pathlib.Path(line[3:].strip()))
    return out


def scan_bytes(data: bytes) -> dict:
    """从一段字节里提取 URL / 路径 / 头名 / env。"""
    urls = set()
    for match in URL_RE.finditer(data):
        text = match.group(0).decode("ascii", "replace")
        # 去掉尾部常见的拼接残留
        text = text.rstrip("/\\,;)\"'")
        if len(text) > 12:
            urls.add(text)

    paths = set()
    for match in PATH_RE.finditer(data):
        text = match.group(1).decode("ascii", "replace")
        if text.count("/") >= 1 and len(text) >= 6:
            paths.add(text)

    envs = set(m.group(1).decode("ascii", "replace") for m in ENV_RE.finditer(data))

    headers = set()
    for match in HEADER_RE.finditer(data):
        name = match.group(1).decode("ascii", "replace")
        lowered = name.lower()
        if lowered in AUTH_HEADERS or lowered.startswith(("cosy-", "x-qoder", "x-api")):
            headers.add(name)

    return {"urls": urls, "paths": paths, "envs": envs, "headers": headers}


#: 路径关键词打分（决定从 asar 里优先提取哪些文件）
PATH_BOOST = (
    ("main", 10), ("app", 8), ("index", 8), ("server", 10), ("core", 8),
    ("api", 10), ("service", 8), ("electron", 8), ("background", 8),
    ("worker", 6), ("runtime", 6), ("out/", 6), ("dist/", 6),
    ("agent", 6), ("provider", 6), ("model", 4), ("auth", 8),
)
PATH_PENALTY = (
    ("node_modules/", -20), ("xlsx", -15), ("pdfjs", -15), ("babel", -15),
    ("monaco", -15), ("codemirror", -15), ("playwright", -15),
    ("language", -10), ("test", -10), ("spec", -10), ("fixture", -10),
    ("example", -10), ("docs", -10), ("i18n", -8), ("locale", -8),
    (".min.js", -12), ("vendor", -8),
)


def score_path(path: str) -> int:
    """给 asar 内路径打分（业务代码分高，第三方依赖分低）。"""
    lowered = path.lower()
    score = 0
    for keyword, weight in PATH_BOOST:
        if keyword in lowered:
            score += weight
    for keyword, weight in PATH_PENALTY:
        if keyword in lowered:
            score += weight
    return score


def hosts_of(urls: set[str]) -> dict:
    """按 host 归并路径。"""
    out: dict[str, set] = {}
    for url in urls:
        match = re.match(r"https?://([^/:]+)", url)
        if not match:
            continue
        host = match.group(1).lower()
        rest = url[match.end():] or "/"
        out.setdefault(host, set()).add(rest.split("?")[0])
    return out


def interesting(paths: set[str]) -> list[str]:
    scored = []
    for path in paths:
        lowered = path.lower()
        score = sum(1 for kw in INTEREST if kw in lowered)
        if score:
            scored.append((score, path))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [p for _s, p in scored]


def main() -> int:
    ap = argparse.ArgumentParser(description="拆包侦察（无需凭证）")
    ap.add_argument("app_dir", help="客户端安装目录（含 resources 的那一层）")
    ap.add_argument("--min-kb", type=int, default=200, help="只扫大于该体积的 JS")
    ap.add_argument("--top", type=int, default=24,
                help="从 asar 里按业务评分提取前 N 个 js")
    ap.add_argument("--out", default="recon-report.json")
    args = ap.parse_args()

    app = pathlib.Path(args.app_dir)
    if not app.is_dir():
        print("目录不存在:", app)
        return 1

    report: dict = {"app_dir": str(app), "sources": [], "hosts": {},
                    "paths": [], "envs": [], "headers": [], "notes": []}

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="recon-"))
    all_urls: set[str] = set()
    all_paths: set[str] = set()
    all_envs: set[str] = set()
    all_headers: set[str] = set()

    # ---- 1) 已解包的 JS（unpacked 优先，业务常在这里）----
    js_files = find_large_js(app, args.min_kb)
    print("=== 大体积 JS（>= %d KB）===" % args.min_kb)
    for item in js_files[:20]:
        print("  %8.1f MB  %s" % (item.stat().st_size / 1048576,
                                  item.relative_to(app)))
    report["sources"].append({"kind": "js", "count": len(js_files)})

    for item in js_files[:80]:
        try:
            data = item.read_bytes()
        except OSError:
            continue
        found = scan_bytes(data)
        if found["urls"] or found["paths"]:
            all_urls |= found["urls"]
            all_paths |= found["paths"]
            all_envs |= found["envs"]
            all_headers |= found["headers"]

    # ---- 2) asar 里的大 js（主进程 + renderer）----
    asars = find_asar(app)
    print("\n=== asar（%d 个）===" % len(asars))
    for asar in asars:
        print("  %8.1f MB  %s" % (asar.stat().st_size / 1048576,
                                  asar.relative_to(app)))
        if node_bin() is None:
            report["notes"].append("没有 node，跳过 %s 解包" % asar.name)
            continue
        try:
            files = asar_list(asar, workdir)
        except Exception as exc:  # noqa: BLE001
            report["notes"].append("列表失败 %s: %s" % (asar.name, str(exc)[:120]))
            continue
        js = [f for f in files if f["path"].endswith((".js", ".mjs", ".cjs"))]
        # 按「业务可能性」排序，不是按体积。
        # 体积大往往是 xlsx/pdfjs/monaco 这类依赖，跟接口无关。
        js.sort(key=lambda f: (-score_path(f["path"]), -f["size"]))
        picked = js[:args.top]
        print("      %d 文件 / %d js，按业务评分取前 %d 个：" % (
            len(files), len(js), len(picked)))
        for item in picked[:6]:
            print("        %+3d  %8.1f KB  %s" % (
                score_path(item["path"]), item["size"] / 1024, item["path"][:70]))
        targets = [f["path"] for f in picked]
        try:
            extracted = asar_extract(asar, workdir, targets)
        except Exception as exc:  # noqa: BLE001
            report["notes"].append("提取失败 %s: %s" % (asar.name, str(exc)[:120]))
            continue
        for path in extracted:
            data = path.read_bytes()
            found = scan_bytes(data)
            all_urls |= found["urls"]
            all_paths |= found["paths"]
            all_envs |= found["envs"]
            all_headers |= found["headers"]
        report["sources"].append({"kind": "asar", "name": asar.name,
                                  "files": len(files), "extracted": len(extracted)})

    # ---- 汇总 ----
    grouped = hosts_of(all_urls)
    report["hosts"] = {host: sorted(paths)[:40] for host, paths in
                       sorted(grouped.items(), key=lambda x: -len(x[1]))}
    report["paths"] = interesting(all_paths)[:120]
    report["envs"] = sorted(all_envs)
    report["headers"] = sorted(all_headers)

    print("\n=== 主机（按路径数排序）===")
    for host, paths in list(report["hosts"].items())[:15]:
        print("  %-40s %d 条路径" % (host, len(paths)))

    print("\n=== 值得关注的路径（按关键词打分）===")
    for path in report["paths"][:30]:
        print("  ", path)

    print("\n=== 可覆盖 host 的环境变量 ===")
    for env in report["envs"][:20]:
        if any(w in env for w in ("HOST", "API", "BASE", "URL", "ENDPOINT", "SERVER")):
            print("  ", env)

    print("\n=== 鉴权相关头名 ===")
    for name in report["headers"][:20]:
        print("  ", name)

    out = pathlib.Path(args.out)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n报告已写入:", out.resolve())
    print("工作目录（提取的 js）:", workdir)

    print("""
=== 下一步（都不需要凭证）===

1. 对每个候选 host 打探针（见 scripts/probe.py）：
     404 = 路径不存在 / 401 = 存在只缺凭证 / 200 = 公开
2. 找鉴权方式：搜 Authorization / x-api-key / 签名函数名
3. 找凭证存放：搜 keytar / safeStorage / auth-state / .dat / token 字段名
4. 设计授权路径：设备流（deviceToken/poll）> 扫码 > PAT > 让用户在界面登录
   —— 见 SKILL 的「无凭证接入」章节
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
