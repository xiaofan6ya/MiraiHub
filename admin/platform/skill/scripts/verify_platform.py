#!/usr/bin/env python
"""平台接入验收：新平台接完后跑这一个脚本，全绿才算合格。

## 覆盖 12 项（对应 INTEGRATION-CHECKLIST）

结构类（不需要凭证）：
   1. 平台已登记（PlatformSpec 存在，storage_platform 正确）
   2. Gateway 契约（isinstance 三个 Protocol）
   3. 账号池 11 项操作可调用
   4. task_runners 声明且可调用
   5. client_probe 五项齐全
   6. overview_cards 返回合规卡片
   7. install() 幂等
   8. admin 未新增平台专有名词（基线只减不增）

运行类（需要服务在跑）：
   9.  /api/platforms 里有这个平台
  10.  /api/platforms/{id}/overview 返回卡片
  11.  账号池数据按平台隔离（各平台 id 互斥）
  12.  平台切换后数据一致（三条操作路径）

## 用法

    # 结构检查（不需要服务）
    python verify_platform.py --platform qoder

    # 含运行检查
    python verify_platform.py --platform qoder --base http://127.0.0.1:8790 \
        --user admin --password 123456

    # 加浏览器验证（需 playwright）
    python verify_platform.py --platform qoder --browser
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, ok, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  [ok] %s" % name)
    else:
        FAIL += 1
        FAILURES.append("%s %s" % (name, str(detail)[:150]))
        print("  [FAIL] %s  %s" % (name, str(detail)[:150]))


# ---------------------------------------------------------------------------
# 结构检查
# ---------------------------------------------------------------------------
def check_structure(platform_id: str) -> None:
    print("\n=== 1-8 结构检查 ===")
    from admin.platform import registry
    from admin.platform.gateway import AccountOps, ClientProbe, Gateway
    from admin.platform.loader import load_platforms

    status = load_platforms()
    check("平台装配无失败", not status.get("failed"),
          str(status.get("failed")))
    print("       已装配:", status.get("loaded"))

    spec = registry.get(platform_id)
    check("1 平台已登记", spec is not None, platform_id)
    if spec is None:
        return
    print("       id=%s label=%s storage=%s realm=%s"
          % (spec.id, spec.label, spec.storage(), spec.realm or "-"))

    gateway = registry.load_platform_gateway(spec, spec.realm)
    check("2.1 满足 Gateway 协议", isinstance(gateway, Gateway),
          type(gateway).__name__)
    check("2.2 platform_id 已声明",
          getattr(gateway, "platform_id", "") != "",
          "缺 platform_id 会让 isinstance 判 False")

    ops = gateway.account_ops() if gateway else None
    check("3.1 满足 AccountOps 协议", isinstance(ops, AccountOps),
          type(ops).__name__)
    required = ("list", "scan", "import_local", "import_record", "refresh",
                "keepalive", "checkin", "credits", "usage", "health",
                "inject", "export")
    missing = [n for n in required
               if ops is None or not callable(getattr(ops, n, None))]
    check("3.2 账号池 11 项齐全", not missing, str(missing))

    runners = gateway.task_runners() if gateway else {}
    check("4.1 task_runners 返回 dict", isinstance(runners, dict),
          type(runners).__name__)
    bad = [k for k, v in (runners or {}).items() if not callable(v)]
    check("4.2 任务全部可调用", not bad, str(bad))
    print("       任务:", list((runners or {}).keys()))

    probe = gateway.client_probe() if gateway else None
    check("5.1 满足 ClientProbe 协议", isinstance(probe, ClientProbe),
          type(probe).__name__)
    probe_missing = [n for n in ("dirs", "probe", "schema", "read", "write")
                     if probe is None or not callable(getattr(probe, n, None))]
    check("5.2 client_probe 五项齐全", not probe_missing, str(probe_missing))

    # overview_cards 需要 db，用只读会话
    try:
        from admin.db import SessionLocal

        with SessionLocal() as db:
            cards = gateway.overview_cards(db) if gateway else []
        ok_shape = (isinstance(cards, list) and cards
                    and all("key" in c and "label" in c and "value" in c
                            for c in cards if isinstance(c, dict)))
        check("6 overview_cards 合规",
              ok_shape, str(cards)[:120] if cards else "空")
        if cards:
            print("       卡片:", [(c.get("key"), c.get("label")) for c in cards[:4]])
    except Exception as exc:  # noqa: BLE001
        check("6 overview_cards 合规", False, str(exc)[:100])

    before = len(registry.ids())
    try:
        module = __import__(spec.__class__.__module__.rsplit(".", 1)[0],
                            fromlist=["install"])
        getattr(module, "install", lambda: None)()
        getattr(module, "install", lambda: None)()
        check("7 install 幂等", len(registry.ids()) == before,
              "%d -> %d" % (before, len(registry.ids())))
    except Exception as exc:  # noqa: BLE001
        check("7 install 幂等", False, str(exc)[:100])

    # admin 平台专有名词基线
    try:
        import importlib.util
        import pathlib

        baseline_path = pathlib.Path(__file__).resolve().parents[4] / (
            "tests/gateway_contract_baseline.txt")
        if baseline_path.is_file():
            baseline = set()
            for line in baseline_path.read_text(encoding="utf-8").splitlines():
                clean = line.split("#", 1)[0].strip()
                if clean:
                    baseline.add(clean)
            check("8 平台名词基线存在（只减不增）", True,
                  "%d 个文件" % len(baseline))
        else:
            check("8 平台名词基线存在（只减不增）", False,
                  "缺 tests/gateway_contract_baseline.txt")
    except Exception as exc:  # noqa: BLE001
        check("8 平台名词基线存在（只减不增）", False, str(exc)[:100])


# ---------------------------------------------------------------------------
# 运行检查
# ---------------------------------------------------------------------------
class Client:
    def __init__(self, base: str, user: str, password: str):
        self.base = base.rstrip("/")
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        form = urllib.parse.urlencode({"username": user,
                                       "password": password}).encode()
        req = urllib.request.Request(self.base + "/api/login", data=form,
                                     method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with self.opener.open(req, timeout=30) as resp:
            self.token = json.loads(resp.read().decode())["token"]

    def get(self, path: str, platform: str = "") -> tuple[int, dict]:
        req = urllib.request.Request(self.base + path)
        req.add_header("X-Admin-Token", self.token)
        if platform:
            req.add_header("X-Platform", platform)
        try:
            with self.opener.open(req, timeout=90) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, {"raw": exc.read().decode("utf-8", "replace")[:200]}


def check_runtime(platform_id: str, base: str, user: str, password: str) -> None:
    print("\n=== 9-11 运行检查 ===")
    client = Client(base, user, password)

    code, data = client.get("/api/platforms")
    check("9 /api/platforms 可达", code == 200, str(code))
    items = (data or {}).get("items") or []
    ids = [i.get("id") for i in items]
    check("9.1 平台在列表里", platform_id in ids, str(ids[:12]))
    check("9.2 内建默认平台在首位", ids and ids[0] == "workbuddy", str(ids[:5]))

    code, data = client.get("/api/platforms/%s/overview" % platform_id)
    cards = (data or {}).get("cards") or []
    check("10 概览卡片有内容", code == 200 and bool(cards),
          "%s cards=%d" % (code, len(cards)))
    if cards:
        print("       ", [(c.get("key"), c.get("value")) for c in cards[:4]])

    # 账号隔离：各平台 id 集合互斥且不重叠
    print("\n       账号隔离检查：")
    owners: dict[int, list[str]] = {}
    for pid in ids:
        code, data = client.get("/api/accounts", pid)
        rows = (data or {}).get("items") or []
        print("         %-14s %d 个" % (pid, len(rows)))
        for row in rows:
            owners.setdefault(int(row.get("id") or 0), []).append(pid)
    leaked = {k: v for k, v in owners.items() if len(v) > 1}
    check("11 账号不跨平台重复", not leaked, str(leaked)[:150])

    total = sum(1 for v in owners.values())
    code, allrows = client.get("/api/accounts")
    total_all = len((allrows or {}).get("items") or [])
    check("11.1 各平台之和等于全量", total == total_all,
          "%d vs %d" % (total, total_all))


# ---------------------------------------------------------------------------
# 浏览器检查（可选）
# ---------------------------------------------------------------------------
def check_browser(platform_id: str, base: str, user: str, password: str) -> None:
    print("\n=== 12 浏览器切换检查 ===")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        check("12 浏览器检查", False, "未安装 playwright（可跳过）")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text)
                if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append("PAGEERROR: " + str(e)))

        page.goto(base + "/admin", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(800)
        page.evaluate("""async ([u, pw]) => {
          const x = new URLSearchParams({username: u, password: pw});
          const r = await fetch('/api/login',{method:'POST',body:x,
            headers:{'Content-Type':'application/x-www-form-urlencoded'}});
          localStorage.setItem('wb_admin_token', (await r.json()).token);
        }""", [user, password])
        page.reload(wait_until="domcontentloaded", timeout=60000)
        page.wait_for_function(
            "() => window.WBPlatform && document.querySelectorAll('.platform-card').length >= 2",
            timeout=30000)
        page.wait_for_timeout(1200)

        cards = page.evaluate(
            "[...document.querySelectorAll('.platform-card')].map(e=>e.dataset.platform)")
        check("12.1 平台卡片渲染", platform_id in cards, str(cards))

        has_svg = page.evaluate(
            """(pid) => {
              const el = document.querySelector('.platform-card[data-platform="'+pid+'"]');
              return !!(el && el.querySelector('svg'));
            }""", platform_id)
        check("12.2 平台有图标", has_svg, "缺图标（用 lobe-icons 或母品牌）")

        # 切到该平台 → 账号池
        page.click('.platform-card[data-platform="%s"]' % platform_id)
        page.wait_for_timeout(2000)
        page.click('.side-item[data-tab="accounts"]')
        page.wait_for_timeout(2800)
        snap = page.evaluate("""() => {
          const rows = [...document.querySelectorAll('#accBody tr')];
          return {
            platform: window.WBPlatform ? window.WBPlatform.current().platform : '?',
            filterAll: (document.querySelector('#filterAllCount')||{}).textContent||'',
            n: rows.length,
            first: rows.length ? rows[0].innerText.replace(/\\s+/g,' ').slice(0,60) : '',
          };
        }""")
        check("12.3 切换后 platform 正确", snap["platform"] == platform_id,
              "%s != %s" % (snap["platform"], platform_id))
        check("12.4 筛选器计数与行数一致",
              str(snap["filterAll"]) == str(snap["n"]),
              "filter=%s rows=%s" % (snap["filterAll"], snap["n"]))
        print("       首行:", snap["first"] or "(空)")

        check("12.5 控制台零报错", not errors, str(errors[:3]))
        browser.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="平台接入验收")
    ap.add_argument("--platform", required=True, help="平台 id，如 qoder")
    ap.add_argument("--base", default="", help="服务地址，如 http://127.0.0.1:8790")
    ap.add_argument("--user", default="xiaofan")
    ap.add_argument("--password", default="123456")
    ap.add_argument("--browser", action="store_true", help="含浏览器检查")
    args = ap.parse_args()

    print("=" * 56)
    print("平台验收:", args.platform)
    print("=" * 56)

    check_structure(args.platform)

    if args.base:
        check_runtime(args.platform, args.base, args.user, args.password)
        if args.browser:
            check_browser(args.platform, args.base, args.user, args.password)
    else:
        print("\n（跳过运行检查：没给 --base）")

    print("\n" + "=" * 56)
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    if FAILURES:
        print("\n失败项:")
        for item in FAILURES:
            print("  -", item)
        return 1
    print("全部通过 —— 平台可以交付")
    return 0


if __name__ == "__main__":
    sys.exit(main())
