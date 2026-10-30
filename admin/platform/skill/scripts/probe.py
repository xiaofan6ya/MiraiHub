#!/usr/bin/env python
"""接口探针：无凭证状态下判断路径是否存在、缺什么鉴权。

## 判据表（这是全部价值所在）

| 返回 | 含义 | 下一步 |
|---|---|---|
| 200 + 业务数据 | 端点通、无需鉴权 | 直接接 |
| **401 / 403** | **端点存在**，只缺凭证 | 去设计授权 |
| **404** | 路径错 / host 不提供 | 换 host 或换路径 |
| 405 | 方法错（GET vs POST） | 换 method |
| 400 + 业务码 | 鉴权通过，参数错 | 最理想：证明整条链通了 |
| 超时 | 网络/需代理 | 先解决网络 |

> `400 参数错` 比 `200` 更有价值 —— 它证明你的凭证被接受了。

## 用法

    python probe.py --host https://api.example.com \
        --path /v1/chat/completions --method POST \
        --path /api/v2/quota/usage --method GET

    # 或从 recon 报告里读主机
    python probe.py --recon recon-report.json --top 8
"""
from __future__ import annotations

import argparse
import json
import pathlib
import ssl
import sys
import time
import urllib.error
import urllib.request

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

#: 探针用的通用头（模仿浏览器）
BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) probe/1.0",
    "Accept": "application/json, text/plain, */*",
}

#: 常见探针路径（不知道具体路径时先试这些）
COMMON_PATHS = (
    ("/", "GET"),
    ("/health", "GET"),
    ("/ping", "GET"),
    ("/api/ping", "GET"),
    ("/algo/api/v1/ping", "POST"),
    ("/api/v1/userinfo", "GET"),
    ("/api/v2/user/plan", "GET"),
    ("/api/v2/quota/usage", "GET"),
    ("/v1/models", "GET"),
    ("/v1/chat/completions", "POST"),
)


def classify(status: int, body: str) -> tuple[str, str]:
    """把响应归类成 (结论, 说明)。"""
    lowered = body[:400].lower()
    if status == 0:
        return "network", "连接失败/超时"
    if status == 404:
        return "not_found", "路径不存在或该主机不提供"
    if status == 405:
        return "wrong_method", "方法不对（换 GET/POST）"
    if status in (401, 403):
        if any(w in lowered for w in ("signature", "sign", "签名")):
            return "need_signature", "需要请求签名（不只是 token）"
        if "token" in lowered or "auth" in lowered or "credential" in lowered:
            return "need_token", "端点存在，缺有效 token"
        return "forbidden", "端点存在，被拒绝（可能需要特定头/签名）"
    if status == 400:
        return "param_error", "★ 鉴权已通过，只是参数不对（最理想）"
    if status == 429:
        return "rate_limited", "限流（端点存在）"
    if 200 <= status < 300:
        return "ok", "通"
    return "http_%d" % status, body[:80]


def probe(url: str, method: str = "GET", payload: dict | None = None,
          headers: dict | None = None, timeout: float = 12.0) -> dict:
    data = None
    if method.upper() == "POST":
        data = json.dumps(payload if payload is not None else {}).encode()
    req = urllib.request.Request(url, data=data, method=method.upper())
    merged = dict(BASE_HEADERS)
    merged.update(headers or {})
    for key, value in merged.items():
        req.add_header(key, str(value))
    if data:
        req.add_header("Content-Type", "application/json")

    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
            body = resp.read()
            text = body.decode("utf-8", "replace")
            verdict, note = classify(resp.status, text)
            return {"url": url, "method": method.upper(), "status": resp.status,
                    "verdict": verdict, "note": note,
                    "ms": int((time.time() - started) * 1000),
                    "body": text[:300]}
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            text = ""
        verdict, note = classify(exc.code, text)
        return {"url": url, "method": method.upper(), "status": exc.code,
                "verdict": verdict, "note": note,
                "ms": int((time.time() - started) * 1000),
                "body": text[:300]}
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "method": method.upper(), "status": 0,
                "verdict": "network", "note": str(exc)[:100],
                "ms": int((time.time() - started) * 1000), "body": ""}


def main() -> int:
    ap = argparse.ArgumentParser(description="无凭证接口探针")
    ap.add_argument("--host", action="append", default=[], help="主机基址，可多次")
    ap.add_argument("--path", action="append", default=[], help="路径，可多次")
    ap.add_argument("--method", default="GET")
    ap.add_argument("--recon", help="recon-report.json，自动取主机与路径")
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--common", action="store_true", help="加一批常见探针路径")
    ap.add_argument("--out", default="probe-report.json")
    args = ap.parse_args()

    targets: list[tuple[str, str]] = []

    if args.recon:
        data = json.loads(pathlib.Path(args.recon).read_text(encoding="utf-8"))
        for host, paths in list(data.get("hosts", {}).items())[:args.top]:
            scheme = "https"
            for path in (paths or ["/"])[:8]:
                if not path.startswith("/"):
                    path = "/" + path
                targets.append((scheme + "://" + host + path, args.method))

    for host in args.host:
        host = host.rstrip("/")
        if args.common or not args.path:
            for path, method in COMMON_PATHS:
                targets.append((host + path, method))
        for path in args.path:
            targets.append((host + (path if path.startswith("/") else "/" + path),
                            args.method))

    if not targets:
        print("没有目标。用 --host / --path，或 --recon + --common")
        return 1

    seen, unique = set(), []
    for url, method in targets:
        key = (url, method)
        if key not in seen:
            seen.add(key)
            unique.append((url, method))

    print("=== 探针 %d 个目标 ===" % len(unique))
    results = []
    for url, method in unique:
        item = probe(url, method)
        results.append(item)
        short = url.split("://", 1)[-1]
        print("  [%-4s] %-6s %-56s %s" % (
            item["status"] or "ERR", item["method"], short[:56], item["note"]))

    alive = [r for r in results if r["verdict"] in
             ("ok", "need_token", "need_signature", "forbidden", "param_error",
              "rate_limited")]
    print("\n=== 汇总 ===")
    print("  可达（存在该端点）: %d / %d" % (len(alive), len(results)))
    if alive:
        hosts = sorted({r["url"].split("/")[2] for r in alive})
        print("  活着的主机:", ", ".join(hosts))
    sign = [r for r in results if r["verdict"] == "need_signature"]
    if sign:
        print("  ⚠️  %d 个端点需要**请求签名**（不是普通 Bearer）" % len(sign))

    out = pathlib.Path(args.out)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("报告:", out.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
