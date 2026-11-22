#!/usr/bin/env python3
"""旧控制台面板索引：列出某个面板的界面元素、相关 JS 函数、调用的接口与后端处理函数。

用法（在仓库根目录执行）：
    python docs/ui-v2/tools/panel_index.py accounts                 # 打印一个面板
    python docs/ui-v2/tools/panel_index.py --all --out <目录>        # 每个面板一个 md
    python docs/ui-v2/tools/panel_index.py --routes                 # 后端全部路由表

这是**正则静态分析**，结果是线索不是结论：动态拼接的 URL 可能漏掉，公共函数可能被算进来。
写功能契约时，以它为清单逐项打开源码核对。
"""
import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONSOLE = ROOT / "admin" / "console"
INDEX = CONSOLE / "index.html"
JS_FILES = sorted(CONSOLE.glob("*.js")) + sorted((ROOT / "admin" / "static").glob("*.js"))
CJK = re.compile(r"[一-鿿]")
UTILITY = {
    "api", "_apiRaw", "fetch", "toast", "showToast", "esc", "escapeHtml", "escHtml", "$", "$$",
    "setTimeout", "clearTimeout", "setInterval", "clearInterval", "Number", "String", "Math",
    "parseInt", "parseFloat", "JSON", "Object", "Array", "Promise", "Date", "encodeURIComponent",
    "_loadStart", "_loadEnd", "apiErrText", "confirmAction", "uiSelectHTML", "uiSelectValue",
    "activateTab", "ensurePlatforms", "panelSkeletonShow", "_renderNavForTab",
}
# 面板「自有」的接口前缀与脚本文件：调用这些前缀的函数无论藏多深都算进该面板
# （行内按钮的处理函数只出现在模板字符串里，靠调用链追不全）。
PLATFORM_APIS = ("/api/login", "/api/trae", "/api/qoder", "/api/loomy", "/api/marvis", "/api/minimax",
                 "/api/raccoon", "/api/codearts", "/api/wps-lingxi", "/api/doubao", "/api/krowork",
                 "/api/growth/")
OWNED_API = {
    "overview": ("/api/overview",), "sentinel": ("/api/sentinel",), "stats": ("/api/stats/dashboard",),
    "platforms": ("/api/platforms/", "/api/platform/"), "gateway": ("/api/gateway",),
    "accounts": ("/api/accounts",) + PLATFORM_APIS, "credits": ("/api/credits",),
    "journal": ("/api/maintenance/credit-events",), "growth": ("/api/growth-center",),
    "usage": ("/api/stats/usage",), "logs": ("/api/logs",), "keys": ("/api/keys", "/api/app/network"),
    "ip": ("/api/ip",), "models": ("/api/models",), "market": ("/api/stats/model-market",),
    "groups": ("/api/groups",), "schedules": ("/api/schedules",), "agent": ("/api/agent",),
    "client": ("/api/client-profile", "/api/client-paths"), "sync": ("/api/sync",),
    "alerts": ("/api/alerts",), "openentry": ("/api/open", "/api/join"),
    "settings": ("/api/runtime-config", "/api/proxies", "/api/app-source", "/api/admin/password",
                 "/api/settings"),
    "maintenance": ("/api/maintenance/update", "/api/maintenance/backup", "/api/maintenance/restore",
                    "/api/maintenance/restart", "/api/app/releases", "/api/app/version"),
}
OWNED_FILES = {
    "agent": ("admin/console/agent.js",), "growth": ("admin/console/growth_center.js",),
    "stats": ("admin/console/dashboard.js",), "market": ("admin/console/market.js",),
    "ip": ("admin/console/ip-management.js",), "keys": ("admin/console/key-ip.js",),
    "platforms": ("admin/console/platform-actions.js", "admin/console/platform-tiers.js"),
    "maintenance": ("admin/console/release-notes.js", "admin/console/desktop.js",
                    "admin/console/review-improvements.js"),
    "journal": ("admin/console/review-improvements.js",),
}
DATA_VARS = ("data|d|res|resp|r|j|json|body|payload|item|it|row|x|a|acc|account|k|key|p|plat|s|st|"
             "stat|stats|o|ov|info|cfg|conf|rec|log|ev|m|model|g|grp|job|t|task|v|c|n|u|b|q")
FIELD = re.compile(r"\b(?:%s)\??\.([A-Za-z_]\w*)" % DATA_VARS)
JS_METHODS = set("""length map filter forEach push join slice toFixed includes indexOf find some every reduce sort
keys values entries trim split replace toLowerCase toUpperCase startsWith endsWith then catch finally text
toString toLocaleString concat flat flatMap at pop shift unshift splice fill findIndex lastIndexOf padStart
padEnd repeat match test exec classList dataset style value checked innerHTML textContent querySelector
querySelectorAll closest addEventListener removeEventListener appendChild remove getAttribute setAttribute
focus blur click disabled hidden parentNode children target preventDefault stopPropagation get set has add
delete clear size abs min max round floor ceil now parse stringify isArray assign from""".split())


def read(path):
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


# ---------------------------------------------------------------- 后端路由
ROUTER_DEF = re.compile(r"^(\w+)\s*=\s*APIRouter\((.*)")
DECOR = re.compile(r"^@(\w+)\.(get|post|put|patch|delete|api_route|websocket)\(\s*([rbf]?[\"'])(.*?)\3(.*)")
DEF = re.compile(r"^(?:async\s+)?def\s+(\w+)\s*\(")


def backend_routes():
    files = [ROOT / "main.py"] + sorted((ROOT / "admin").rglob("*.py"))
    routes = []
    for path in files:
        # skill/ 下是新平台脚手架模板，里面的路由不是真实注册的接口
        if "tests" in path.parts or "skill" in path.parts or not path.is_file():
            continue
        lines = read(path)
        text = "\n".join(lines)
        if "APIRouter(" not in text and "@app." not in text:
            continue
        prefixes = {"app": ""}
        for line in lines:
            m = ROUTER_DEF.match(line.strip())
            if m:
                p = re.search(r"prefix\s*=\s*[\"']([^\"']*)[\"']", m.group(2))
                prefixes[m.group(1)] = p.group(1) if p else ""
        for i, raw in enumerate(lines):
            m = DECOR.match(raw.strip())
            if not m or m.group(1) not in prefixes:
                continue
            method = m.group(2).upper()
            if method == "API_ROUTE":
                ms = re.findall(r"[\"'](GET|POST|PUT|PATCH|DELETE)[\"']", m.group(5))
                method = "/".join(ms) or "ANY"
            handler, doc = "?", ""
            for j in range(i + 1, min(i + 15, len(lines))):
                d = DEF.match(lines[j].strip())
                if d:
                    handler = d.group(1)
                    for k in range(j + 1, min(j + 8, len(lines))):
                        s = lines[k].strip()
                        if s.startswith(('"""', "'''")):
                            doc = s.strip("\"' ")
                            break
                    break
            routes.append({
                "method": method, "path": prefixes[m.group(1)] + m.group(4),
                "handler": handler, "loc": "%s:%d" % (path.relative_to(ROOT).as_posix(), i + 1),
                "doc": doc[:70],
            })
    return routes


def match_route(url, routes, method=None):
    """前端 URL（`{x}` 表示动态段）→ 后端路由。返回最具体的若干个匹配。"""
    path = url.split("?")[0]
    if path.endswith("/"):            # "/api/x/" + id → 末尾还有一段
        path += "{x}"
    path = re.sub(r"(?<=[\w-])\{x\}$", "", path)   # "/api/x" + qs → 拼的是查询串
    segs = path.strip("/").split("/")
    scored = []
    for r in routes:
        rsegs = r["path"].rstrip("/").strip("/").split("/")
        if r["path"].endswith(":path}"):
            ok = len(segs) >= len(rsegs) - 1
            rsegs = rsegs[:-1]
            segs_cmp = segs[:len(rsegs)]
        else:
            ok = len(rsegs) == len(segs)
            segs_cmp = segs
        if not ok:
            continue
        score = 0
        for a, b in zip(segs_cmp, rsegs):
            if a == b:
                score += 2
            elif a == "{x}" or (b.startswith("{") and b.endswith("}")):
                score += 0
            else:
                score = -1
                break
        if score >= 0:
            scored.append((score, r))
    if not scored:
        return []
    # 先按路径具体程度取最优，再在并列里优先同方法（前端方法可能没识别出来，标 GET?）
    best = max(s for s, _ in scored)
    top = [r for s, r in scored if s == best]
    if method and not method.endswith("?"):
        same = [r for r in top if method in r["method"].split("/") or r["method"] == "ANY"]
        top = same or top
    return top[:6]


def extend_concat(line, pos, url):
    """`"/api/accounts/" + id + "/inject"` → `/api/accounts/{x}/inject`。"""
    rest = line[pos:]
    while True:
        m = re.match(r"\s*\+\s*(?:[A-Za-z_$][\w$.]*(?:\([^()]*\))?|\([^()]*\))", rest)
        if not m:
            break
        url += "{x}"
        rest = rest[m.end():]
        m2 = re.match(r"\s*\+\s*([\"'`])([^\"'`]*)\1", rest)
        if not m2:
            break
        url += re.sub(r"\$\{[^}]*\}", "{x}", m2.group(2))
        rest = rest[m2.end():]
    return url


# ---------------------------------------------------------------- 前端 JS
FUNC_PATTERNS = [
    re.compile(r"^(\s*)(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\("),
    re.compile(r"^(\s*)(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"),
    re.compile(r"^(\s*)([A-Za-z_$][\w$]*)\s*:\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*=>)"),
    re.compile(r"^(\s*)(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{\s*$"),
    re.compile(r"^(\s*)(?:window\.)?([A-Za-z_$][\w$.]*)\s*=\s*(?:async\s+)?function\b"),
]
KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "else", "do", "try", "with"}
API_LIT = re.compile(r"([\"'`])((?:/api/|/admin/|/v1/|/gw/)[^\"'`\s]*)\1")
METHOD = re.compile(r"method\s*:\s*[\"'`](GET|POST|PUT|PATCH|DELETE)[\"'`]", re.I)
STR_LIT = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'|`((?:[^`\\]|\\.)*)`")


def brace_depths(lines):
    """每行结束时的花括号深度（跳过字符串、模板、注释；`${}` 不计入）。"""
    depth, stack, in_block, out = 0, [], False, []
    for line in lines:
        i, n, quote = 0, len(line), None
        while i < n:
            c = line[i]
            if in_block:
                if line.startswith("*/", i):
                    in_block = False
                    i += 2
                    continue
                i += 1
                continue
            if quote:
                if c == "\\":
                    i += 2
                    continue
                if c == quote:
                    quote = None
                i += 1
                continue
            if stack and stack[-1] == "tmpl":
                if c == "\\":
                    i += 2
                    continue
                if c == "`":
                    stack.pop()
                elif line.startswith("${", i):
                    stack.append(("expr", depth))
                    i += 2
                    continue
                i += 1
                continue
            if line.startswith("//", i):
                break
            if line.startswith("/*", i):
                in_block = True
                i += 2
                continue
            if c in "'\"":
                quote = c
            elif c == "`":
                stack.append("tmpl")
            elif c == "{":
                depth += 1
            elif c == "}":
                if stack and isinstance(stack[-1], tuple) and stack[-1][1] == depth:
                    stack.pop()
                else:
                    depth -= 1
            i += 1
        out.append(depth)
    return out


def js_functions(known_ids):
    funcs = []
    for path in JS_FILES:
        lines = read(path)
        depths = brace_depths(lines)
        rel = path.relative_to(ROOT).as_posix()
        starts = []
        for i, line in enumerate(lines):
            for pat in FUNC_PATTERNS:
                m = pat.match(line)
                if m and m.group(2) not in KEYWORDS:
                    starts.append((i, m.group(2).split(".")[-1]))
                    break
        spans = []
        for i, name in starts:
            before = depths[i - 1] if i else 0
            end, opened = i, False
            for j in range(i, len(lines)):
                if depths[j] > before:
                    opened = True
                if depths[j] <= before and (opened or j > i + 1):
                    end = j
                    break
                end = j
            spans.append((i, end, name))
        # 不在任何具名函数内的顶层语句（事件监听等）也当成一个单元
        covered = [False] * len(lines)
        for s, e, _ in spans:
            for k in range(s, e + 1):
                covered[k] = True
        i = 0
        while i < len(lines):
            if not covered[i] and lines[i].strip() and depths[i] > (depths[i - 1] if i else 0):
                s = i
                base = depths[i - 1] if i else 0
                while i < len(lines) and depths[i] > base:
                    i += 1
                spans.append((s, min(i, len(lines) - 1), "@toplevel_L%d" % (s + 1)))
            i += 1
        names = {n for _, _, n in spans}
        for s, e, name in spans:
            body = lines[s:e + 1]
            apis, ids, calls, texts, fields = [], set(), set(), [], []
            for k, line in enumerate(body):
                if line.lstrip().startswith(("//", "*", "/*")):
                    continue
                for m in API_LIT.finditer(line):
                    url = extend_concat(line, m.end(), re.sub(r"\$\{[^}]*\}", "{x}", m.group(2)))
                    # 方法只在这次调用的参数里找：截到下一个 api(/fetch( 为止
                    window = line[m.end():] + " " + " ".join(body[k + 1:k + 10])
                    window = re.split(r"\b(?:api|fetch)\(", window)[0]
                    mm = METHOD.search(window)
                    apis.append(((mm.group(1).upper() if mm else "GET?"), url, s + k + 1))
                for lit in STR_LIT.finditer(line):
                    val = next(g for g in lit.groups() if g is not None)
                    for tok in re.findall(r"#?([A-Za-z][\w-]{2,})", val):
                        if tok in known_ids:
                            ids.add(tok)
                    if CJK.search(val):
                        clean = re.sub(r"<[^>]+>", " ", val)
                        clean = re.sub(r"\$\{[^}]*\}", "…", clean)
                        clean = re.sub(r"\s+", " ", clean).strip()
                        if clean and CJK.search(clean):
                            texts.append(clean[:80])
                for fm in FIELD.finditer(line):
                    if fm.group(1) not in JS_METHODS:
                        fields.append(fm.group(1))
                for tok in re.findall(r"([A-Za-z_$][\w$]*)\s*\(", line):
                    if tok in names and tok != name:
                        calls.add(tok)
            funcs.append({
                "name": name, "file": rel, "start": s + 1, "end": e + 1, "apis": apis,
                "ids": ids, "calls": calls, "texts": list(dict.fromkeys(texts)),
                "fields": list(dict.fromkeys(fields)),
            })
    return funcs


# ---------------------------------------------------------------- 面板 HTML
def strip_html(s):
    s = re.sub(r"<svg\b.*?</svg>", "", s, flags=re.S)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    return s


def text_of(fragment):
    t = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", t).strip()


def panels():
    lines = read(INDEX)
    starts = [(i, m.group(1)) for i, l in enumerate(lines)
              for m in [re.search(r"<section\s+id=\"panel-([\w-]+)\"", l)] if m]
    out = {}
    for i, tab in starts:
        depth, end = 0, i
        for j in range(i, len(lines)):
            depth += len(re.findall(r"<section\b", lines[j])) - len(re.findall(r"</section>", lines[j]))
            if depth <= 0:
                end = j
                break
        out[tab] = (i, end)
    # 去掉嵌套在别的面板里的子面板行
    result = {}
    for tab, (s, e) in out.items():
        holes = [(s2, e2) for t2, (s2, e2) in out.items() if t2 != tab and s < s2 and e2 <= e]
        keep = [k for k in range(s, e + 1) if not any(a <= k <= b for a, b in holes)]
        result[tab] = (s + 1, e + 1, "\n".join(lines[k] for k in keep))
    return result


def all_ids():
    html = "\n".join(read(INDEX))
    ids = set(re.findall(r"\bid=\"([\w-]+)\"", html))
    return ids, html


def panel_summary(html):
    html = strip_html(html)
    heads = [text_of(h) for h in re.findall(r"<h[1-6][^>]*>(.*?)</h[1-6]>", html, re.S)]
    buttons = []
    for attrs, inner in re.findall(r"<button\b([^>]*)>(.*?)</button>", html, re.S):
        bid = re.search(r"\bid=\"([^\"]+)\"", attrs)
        on = re.search(r"onclick=\"([^\"]+)\"", attrs)
        data = re.findall(r"(data-[\w-]+)=\"([^\"]*)\"", attrs)
        data = [d for d in data if d[0] not in ("data-tooltip",)]
        tip = re.search(r"data-tooltip=\"([^\"]+)\"", attrs)
        buttons.append((text_of(inner), bid.group(1) if bid else "", on.group(1) if on else "",
                        " ".join("%s=%s" % d for d in data), tip.group(1) if tip else ""))
    inputs = []
    for tag, attrs in re.findall(r"<(input|select|textarea)\b([^>]*)>", html):
        g = lambda k: (re.search(r"\b%s=\"([^\"]*)\"" % k, attrs) or [None, ""])[1]
        inputs.append((tag, g("id"), g("type"), g("placeholder"), g("value")))
    labels = [text_of(x) for x in re.findall(r"<label\b[^>]*>(.*?)</label>", html, re.S)]
    ths = [text_of(x) for x in re.findall(r"<th\b[^>]*>(.*?)</th>", html, re.S)]
    paras = [text_of(x) for x in re.findall(r"<(?:p|span|div)\b[^>]*class=\"[^\"]*(?:hint|note|sub|desc|muted|tip)[^\"]*\"[^>]*>(.*?)</(?:p|span|div)>", html, re.S)]
    paras += [text_of(x) for x in re.findall(r"<p\b[^>]*>(.*?)</p>", html, re.S)]
    handlers = set()
    for attr in re.findall(r"\bon\w+=\"([^\"]+)\"", html):
        handlers.update(re.findall(r"([A-Za-z_$][\w$.]*)\s*\(", attr))
    ids = re.findall(r"\bid=\"([\w-]+)\"", html)
    return heads, buttons, inputs, labels, ths, [p for p in dict.fromkeys(paras) if p], handlers, ids


def activate_loaders():
    src = "\n".join(read(CONSOLE / "console-app.js"))
    m = re.search(r"\nfunction activateTab\(.*?\n\}\n", src, re.S)
    body = m.group(0) if m else ""
    loaders = defaultdict(set)
    for part in re.split(r"(?=if \(name === )", body):
        t = re.match(r"if \(name === [\"'](\w+)[\"']\)", part)
        if t:
            stmt = part.split("\n  if (")[0]
            for tok in re.findall(r"([A-Za-z_$][\w$.]*)\s*\(", stmt):
                tok = tok.split(".")[-1]
                if tok not in ("if", "then", "setTimeout", "try", "catch", "warn", "load", "enter"):
                    loaders[t.group(1)].add(tok)
                elif tok in ("load", "enter"):
                    owner = re.search(r"(WB\w+)\??\.%s" % tok, stmt)
                    if owner:
                        loaders[t.group(1)].add(owner.group(1) + "." + tok)
    return loaders


def build_tab(tab, ctx):
    funcs, routes, pnl, loaders = ctx["funcs"], ctx["routes"], ctx["panels"], ctx["loaders"]
    s, e, html = pnl[tab]
    heads, buttons, inputs, labels, ths, paras, handlers, ids = panel_summary(html)
    idset = set(ids)
    by_name = defaultdict(list)
    for f in funcs:
        by_name[f["name"]].append(f)
    modules = ctx["modules"]

    def resolve(name, from_file=None, caller=None):
        """`WBStatsDashboard.load` 只认定义该模块的文件；裸名先找调用者内部的局部函数，
        再找同文件唯一的同名函数；同名有多个又分不清时宁可不算（避免把别处的 tick 拉进来）。"""
        if "." in name:
            mod, fn = name.rsplit(".", 1)
            files = modules.get(mod.split(".")[-1])
            return [g for g in by_name.get(fn, []) if not files or g["file"] in files]
        cands = by_name.get(name, [])
        same = [g for g in cands if g["file"] == from_file]
        if caller is not None:
            nested = [g for g in same if caller["start"] < g["start"] <= caller["end"]]
            if nested:
                return nested
        if len(same) == 1:
            return same
        if not same and len(cands) == 1:
            return cands
        return []

    seeds = {x for x in set(handlers) | set(loaders.get(tab, ())) if x.split(".")[-1] not in UTILITY}
    picked = {}
    for sd in seeds:
        for f in resolve(sd, "admin/console/console-app.js"):
            picked[(f["file"], f["start"])] = f
    for f in funcs:
        own = f["ids"] & idset
        owned_api = any(u.startswith(p) for _, u, _ in f["apis"] for p in OWNED_API.get(tab, ()))
        owned_file = f["file"] in OWNED_FILES.get(tab, ()) and (f["apis"] or f["texts"])
        if (own and f["name"] not in UTILITY) or owned_api or owned_file:
            picked[(f["file"], f["start"])] = f
    frontier = list(picked.values())
    for _ in range(2):
        nxt = []
        for f in frontier:
            for c in f["calls"]:
                if c in UTILITY:
                    continue
                for g in resolve(c, f["file"], f):
                    key = (g["file"], g["start"])
                    other_panel_ids = g["ids"] - idset
                    if key in picked or (g["ids"] and not (g["ids"] & idset) and other_panel_ids):
                        continue
                    if g["apis"] or (g["ids"] & idset):
                        picked[key] = g
                        nxt.append(g)
        frontier = nxt
    out = ["# 旧版面板索引：%s（自动生成，仅作核对清单）" % tab, ""]
    out.append("面板 HTML：`admin/console/index.html` 第 %d–%d 行；activateTab 入口：%s" % (
        s, e, "、".join(sorted(loaders.get(tab, ()))) or "无"))
    out += ["", "## 1. 面板静态结构", ""]
    if heads:
        out.append("- 标题：" + " / ".join(h for h in heads if h))
    if buttons:
        out.append("- 按钮（文案 | id | onclick | data-* | 提示）：")
        for b in buttons:
            out.append("  - " + " | ".join(x if x else "—" for x in b))
    if inputs:
        out.append("- 输入控件（标签 | id | type | placeholder | value）：")
        for t in inputs:
            out.append("  - " + " | ".join(x if x else "—" for x in t))
    if labels:
        out.append("- 标签文字：" + " / ".join(l for l in labels if l))
    if ths:
        out.append("- 静态表头：" + " / ".join(t for t in ths if t))
    if paras:
        out.append("- 说明文字：")
        for p in paras[:30]:
            out.append("  - " + p[:220])
    out += ["", "## 2. 相关函数（按文件与行号）", ""]
    api_rows = {}
    for f in sorted(picked.values(), key=lambda f: (f["file"], f["start"])):
        if f["name"].startswith("@toplevel") and not (f["apis"] or f["texts"]):
            continue
        # 局部函数与外层函数同名时，只保留外层（它的正文里已包含局部函数）
        if any(g["file"] == f["file"] and g["start"] < f["start"] and g["end"] >= f["end"]
               and g["name"] == f["name"] for g in picked.values()):
            continue
        apis = "；".join("%s %s" % (m, u) for m, u, _ in f["apis"]) or "—"
        out.append("- `%s` %s:%d–%d · 接口：%s" % (f["name"], f["file"], f["start"], f["end"], apis))
        if f["fields"]:
            out.append("  - 读取字段：" + ", ".join(f["fields"][:45]))
        if f["texts"]:
            out.append("  - 文案：" + " ｜ ".join(f["texts"][:28]))
        for m, u, line in f["apis"]:
            api_rows.setdefault((m, u), []).append("%s(%s:%d)" % (f["name"], f["file"].split("/")[-1], line))
    out += ["", "## 3. 接口汇总（前端调用 → 后端处理函数）", "",
            "| 方法 | 前端路径 | 调用处 | 后端匹配 |", "|---|---|---|---|"]
    for (m, u), where in sorted(api_rows.items(), key=lambda x: x[0][1]):
        hits = match_route(u, routes, m)
        back = "<br>".join("%s %s → `%s` %s" % (r["method"], r["path"], r["handler"], r["loc"]) for r in hits) or "未匹配（URL 可能是拼接的）"
        out.append("| %s | `%s` | %s | %s |" % (m, u, "、".join(sorted(set(where)))[:200], back))
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tab", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--routes", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    routes = backend_routes()
    if a.routes:
        lines = ["| 方法 | 路径 | 处理函数 | 位置 | 说明 |", "|---|---|---|---|---|"]
        for r in sorted(routes, key=lambda r: r["path"]):
            lines.append("| %s | `%s` | `%s` | %s | %s |" % (r["method"], r["path"], r["handler"], r["loc"], r["doc"]))
        text = "# 后端路由表（自动生成，共 %d 条）\n\n" % len(routes) + "\n".join(lines) + "\n"
        if a.out:
            Path(a.out).mkdir(parents=True, exist_ok=True)
            (Path(a.out) / "_routes.md").write_text(text, encoding="utf-8")
        else:
            sys.stdout.reconfigure(encoding="utf-8")
            print(text)
        return
    ids, _ = all_ids()
    modules = defaultdict(set)
    for path in JS_FILES:
        for mod in re.findall(r"window\.(WB\w+)\s*=", path.read_text(encoding="utf-8", errors="replace")):
            modules[mod].add(path.relative_to(ROOT).as_posix())
    ctx = {"funcs": js_functions(ids), "routes": routes, "panels": panels(),
           "loaders": activate_loaders(), "modules": modules}
    tabs = sorted(ctx["panels"]) if a.all else [a.tab]
    for tab in tabs:
        if tab not in ctx["panels"]:
            sys.exit("未知面板：%s（可选：%s）" % (tab, "、".join(sorted(ctx["panels"]))))
        text = build_tab(tab, ctx)
        if a.out:
            Path(a.out).mkdir(parents=True, exist_ok=True)
            (Path(a.out) / ("%s.md" % tab)).write_text(text, encoding="utf-8")
        else:
            sys.stdout.reconfigure(encoding="utf-8")
            print(text)


if __name__ == "__main__":
    main()
