#!/usr/bin/env python
"""热路径不变量自检：网关"静默出错"的那几个点，一个个钉住。

## 为什么需要这个脚本

这一批 bug 有个共同特征：**不报错**。

  * 预读 body 跳过 → 模型名读不到 → 悄悄落到默认平台 → 400
  * 平台内 id 没还原 → 上游说"没这个模型" → 400
  * 查倍率不分平台 → 记错账 → 积分显示 0
  * 积分空结构取 0 → 真实余额被清零 → 界面说"没钱了"

它们都不会在启动时报错、也不会让契约测试变红，只会在用户用一阵子之后
突然出现。所以必须有**针对行为**的断言，而不是"能加载就算过"。

## 用法

    python admin/platform/skill/scripts/check_hotpath.py
    python admin/platform/skill/scripts/check_hotpath.py -v      # 打印细节

退出码：
    0  全部通过（SKIP 不算失败）
    1  有不变量被破坏
    2  环境不全（连 import 都失败）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 从脚本位置向上找仓库根（含 `admin/` 包的那一层）。
# 写死 `..` 的层数很容易错（skill/scripts → skill → platform → admin），
# 所以按"能不能看到 admin/__init__.py"来判断。
_here = os.path.abspath(os.path.dirname(__file__))
_root = _here
for _ in range(6):
    if os.path.isfile(os.path.join(_root, "admin", "__init__.py")):
        break
    _root = os.path.dirname(_root)
sys.path.insert(0, _root)

PASS = 0
FAIL = 0
SKIP = 0
FAILED: list[str] = []
VERBOSE = False


def ok(name: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    print("  [PASS] %s%s" % (name, ("  " + detail) if VERBOSE and detail else ""))


def bad(name: str, detail: str) -> None:
    global FAIL
    FAIL += 1
    FAILED.append("%s — %s" % (name, detail))
    print("  [FAIL] %s\n         %s" % (name, detail.replace("\n", "\n         ")))


def skip(name: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print("  [SKIP] %s  (%s)" % (name, why))


def check(name: str, fn) -> None:
    """跑一个断言函数；抛 AssertionError / Exception 都算 FAIL，但不中断。"""
    try:
        detail = fn()
        ok(name, detail if isinstance(detail, str) else "")
    except AssertionError as exc:
        bad(name, str(exc) or "断言失败")
    except Exception as exc:  # noqa: BLE001
        bad(name, "%s: %s" % (type(exc).__name__, exc))
        if VERBOSE:
            traceback.print_exc()


# ---------------------------------------------------------------------------
# 0. 依赖
# ---------------------------------------------------------------------------
def section(title: str) -> None:
    print("\n" + title)


def main() -> int:
    global VERBOSE
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    VERBOSE = args.verbose

    try:
        from starlette.requests import Request

        from admin.platform import dispatch as d
        from admin.platform import models_router as mr
        from admin.platform import model_suffix as ms
        from admin.routers.models import _parse_multiplier
    except Exception as exc:  # noqa: BLE001
        print("导入失败，环境不完整：%s: %s" % (type(exc).__name__, exc))
        return 2

    # ---------------- 1. 预读判定 ----------------
    section("1. dispatch 预读 body（决定平台后缀能否解析）")

    def scope_of(method: str, size: int | None = None):
        headers = [(b"content-type", b"application/json")]
        if size is not None:
            headers.append((b"content-length", str(size).encode()))
        return {"type": "http", "method": method, "path": "/v1/chat/completions",
                "headers": headers, "query_string": b""}

    def peek(method: str, size: int | None) -> bool:
        return d._should_peek(Request(scope_of(method, size), receive=None))

    check("POST 300B（带 Content-Length）→ 预读", lambda: (
        _assert(peek("POST", 300) is True, "小 body 必须预读")))
    check("POST 600KB → 预读（长对话不能退化）", lambda: (
        _assert(peek("POST", 600_000) is True, "600KB 必须预读")))
    check("POST chunked（无 Content-Length）→ 预读", lambda: (
        _assert(peek("POST", None) is True,
                "没有 Content-Length 时以前直接 return False，"
                "chunked 请求会丢后缀路由")))
    check("GET → 不预读（否则下游卡死）", lambda: (
        _assert(peek("GET", None) is False, "GET 不该预读")))
    check("POST >32MB → 不预读（内存保护，须有 warning）", lambda: (
        _assert(peek("POST", 64 * 1024 * 1024) is False, "超大 body 不该预读")))

    # ---------------- 2. 模型名提取 ----------------
    section("2. 从 body 取模型名（大 body 也要取到）")

    long_body = json.dumps({
        "model": "sn-deepseek-v4-1-flash-1017-raccoon",
        "messages": [{"role": "user", "content": "x" * 600_000}],
    }).encode()

    check("600KB body 取 model", lambda: _assert(
        d._model_of_body(long_body) == "sn-deepseek-v4-1-flash-1017-raccoon",
        "大 body 取到的是 %r" % d._model_of_body(long_body)[:40]))

    # model 出现在 8KB 之后 → 必须靠 JSON 兜底，而不是前 8KB 正则
    late_body = json.dumps({
        "messages": [{"role": "user", "content": "y" * 20_000}],
        "model": "glm-5.3-flash-loomy",
    }).encode()
    check("model 在 8KB 之后 → JSON 兜底", lambda: _assert(
        d._model_of_body(late_body) == "glm-5.3-flash-loomy",
        "兜底失败，取到 %r" % d._model_of_body(late_body)))

    # ---------------- 3. 平台后缀 ----------------
    section("3. 平台后缀解析")

    try:
        from admin.platform.loader import load_platforms
        load_platforms()
    except Exception as exc:  # noqa: BLE001
        print("  (load_platforms 失败，跳过后缀用例：%s)" % exc)

    def suffix_case():
        pid, bare, hit = ms.resolve("sn-glm-5-3-flash-raccoon")
        _assert(hit and pid == "raccoon" and bare == "sn-glm-5-3-flash",
                "解析结果 pid=%r bare=%r hit=%r" % (pid, bare, hit))
        return "raccoon / sn-glm-5-3-flash"

    check("`xxx-raccoon` → raccoon", suffix_case)

    # ---------------- 4. 倍率六种形态 ----------------
    section("4. 倍率解析（六种上游形态）")

    cases = [
        ("workbuddy credits", {"credits": "x0.50"}, 0.5),
        ("trae credits", {"credits": "x0.79"}, 0.79),
        ("qoder 顶层 price_factor", {"price_factor": 0.2}, 0.2),
        ("qoder 嵌套", {"x_qoder": {"price_factor": 0.5}}, 0.5),
        ("loomy 显示名", {"name": "DeepSeek V4.1 Flash(x2.0)"}, 2.0),
        ("loomy 徽标+倍率", {"name": "Spark-X2.6 正式版(限时免费)(x0.0)"}, 0.0),
        ("raccoon 实扣优先", {"billing_effective_multiplier": 0.0,
                              "billing_multiplier": 0.5}, 0.0),
        ("raccoon 常规", {"billing_effective_multiplier": 0.25,
                          "billing_multiplier": 0.25}, 0.25),
        ("真没有 → -1", {}, -1.0),
    ]
    for label, payload, expect in cases:
        def one(p=payload, e=expect, lb=label):
            got, raw = _parse_multiplier(p)
            _assert(abs(float(got) - e) < 1e-6,
                    "%s: 期望 %s，实得 %s" % (lb, e, got))
            return "→ %s" % got
        check(label, one)

    # ---------------- 5. 能力字段（DSH schema） ----------------
    section("5. /v1/models 能力字段（DSH discoverModels 只认这五个）")

    caps = mr._capabilities_of(type("R", (), {
        "model_id": "sn-glm-5-3-flash",
        "upstream_metadata": json.dumps({
            "contextWindow": 1_000_000, "maxTokens": 100_000,
            "tags": ["vision", "reasoning"],
        }),
    })())
    flat = mr._caps_flat(caps, "GLM-5.3-Flash")

    check("tags=vision → supports_images", lambda: _assert(
        caps.get("supports_images") is True,
        "tags 兜底没生效，caps=%r" % caps))
    check("tags=reasoning → supports_reasoning", lambda: _assert(
        caps.get("supports_reasoning") is True,
        "tags 兜底没生效，caps=%r" % caps))
    check("contextWindow / maxTokens", lambda: _assert(
        flat.get("contextWindow") == 1_000_000 and flat.get("maxTokens") == 100_000,
        "flat=%r" % flat))
    check("inputModalities 是数组", lambda: _assert(
        flat.get("inputModalities") == ["text", "image"],
        "flat=%r" % flat.get("inputModalities")))
    check("OpenRouter 别名同时给", lambda: _assert(
        flat.get("context_length") == 1_000_000 and
        flat.get("max_output_tokens") == 100_000,
        "别名缺失，flat keys=%r" % sorted(flat)))

    def strict_case():
        """`/v1/models` 的最后一道白名单不能把 DSH 认的键裁掉。"""
        payload = {"data": [dict(flat, id="sn-glm-5-3-flash-raccoon")]}
        entry = mr.to_openai_strict(payload)["data"][0]
        for key in ("contextWindow", "maxTokens", "inputModalities"):
            _assert(key in entry, "to_openai_strict 裁掉了 %s（客户端会显示空）" % key)
        return "5 个 DSH 键保留"

    check("/v1/models 白名单保留 DSH 键", strict_case)

    # ---------------- 6. 导出信封 ----------------
    section("6. 导出信封可拆（导出的必须能导回来）")

    def unwrap_case():
        from admin.platform.workbuddy.core.credential_codec import _unwrap_export_wrapper
        inner = {"account": {"uid": "u1"}, "auth": {"access_token": "t"}}
        envelope = json.dumps({"id": 1, "name": "n", "format": "client",
                               "item": json.dumps(inner)})
        got = _unwrap_export_wrapper(json.loads(envelope))
        _assert(isinstance(got, dict) and "auth" in got and "account" in got,
                "拆壳失败：%r" % (got,))
        return "item → auth/account"

    check("信封 {item: '<json>'} → 内层凭证", unwrap_case)

    # ---------------- 7. 脱敏 uid 勾选 ----------------
    section("7. 扫描结果勾选（脱敏 uid 也要能命中）")

    def uid_case():
        from admin.platform.raccoon.platform_gateway import _uid_selected
        full = "pu-01a1a3ed9f2c4b71"
        _assert(_uid_selected(full, [full]), "完整 uid 全等失败")
        _assert(_uid_selected(full, ["pu-01a1a******"]),
                "脱敏 uid 前缀匹配失败 —— 用户会看到「不在勾选范围内」")
        _assert(not _uid_selected(full, ["pu-9999****"]), "不该误命中别的账号")
        return "full + masked 都命中"

    check("完整 / 脱敏 / 不误伤", uid_case)

    # ---------------- 8. 积分空结构 ----------------
    section("8. 积分接口空结构必须抛错（否则余额被清零）")

    def points_case():
        from admin.platform.raccoon.core import client as rc
        original = rc.request_json
        try:
            rc.request_json = lambda *a, **k: None      # 模拟 HTTP 200 + 空 body
            raised = False
            try:
                rc.fetch_points("token", "cn", 0)
            except rc.UpstreamError:
                raised = True
            _assert(raised, "空 body 被当成「余额 0」，会覆盖真实余额")
        finally:
            rc.request_json = original
        return "UpstreamError"

    check("fetch_points(None) → UpstreamError", points_case)

    # ---------------- 9. 平台内 model id 还原（需要 DB） ----------------
    section("9. 同名模型按平台还原上游 id（需要 DB，可跳过）")

    def db_case():
        from admin.db import SessionLocal
        db = SessionLocal()
        try:
            pairs = [("glm-5.3-flash", "loomy", "GLM-5.3-Flash"),
                     ("deepseek-v4.1-flash", "trae", "DeepSeek-V4.1-Flash")]
            misses = []
            for slug, storage, expect in pairs:
                got = mr.upstream_of_in_platform(slug, storage, db)
                if got.lower() != expect.lower():
                    misses.append("%s@%s → %r（期望 %r）" % (slug, storage, got, expect))
            _assert(not misses, "；".join(misses))
            _assert(mr.upstream_of_in_platform("明显不存在的模型", "loomy", db) == "",
                    "找不到时必须返回空串（不许猜）")
        finally:
            db.close()
        return "2 组映射正确"

    try:
        check("upstream_of_in_platform", db_case)
    except Exception as exc:  # noqa: BLE001
        skip("upstream_of_in_platform", "DB 不可用：%s" % str(exc)[:60])

    # ---------------- 10. 平台能力声明 ----------------
    section("10. 平台动作：声明 ⊇ 必须能力，且前端有渲染元数据")

    def declared_actions() -> set[str]:
        from admin.platform import registry
        spec = registry.get("raccoon")
        _assert(spec is not None, "raccoon 未登记")
        return set((getattr(spec, "extra", None) or {}).get("actions") or [])

    def actions_case():
        actions = declared_actions()
        # `import-file` 不是必须的：它和 `import` 打开同一个弹窗，
        # 同时声明会出现两个一模一样的按钮（现行实现故意只留 `import`）。
        need = {"scan", "import", "export", "refresh-quota"}
        missing = need - actions
        _assert(not missing, "缺少动作声明：%r" % sorted(missing))
        return "%d 个动作" % len(actions)

    def meta_case():
        """声明的动作必须在前端 ACTION_META 里有 label，否则按钮显示成裸 id。"""
        import re
        js_path = os.path.join(_root, "admin", "console", "platform-actions.js")
        _assert(os.path.isfile(js_path), "找不到 platform-actions.js")
        with open(js_path, encoding="utf-8") as fh:
            text = fh.read()
        keys = set()
        for match in re.finditer(r'(["\']?)([a-z][a-z0-9-]*)\1\s*:\s*\{\s*label\s*:', text):
            keys.add(match.group(2))
        _assert(keys, "ACTION_META 解析为空（前端结构变了？）")
        actions = declared_actions()
        unknown = actions - keys
        _assert(not unknown,
                "这些动作前端没有 label，会渲染成裸 id：%r" % sorted(unknown))
        return "%d 个动作都有 label" % len(actions)

    try:
        check("raccoon actions 覆盖必须能力", actions_case)
        check("声明 ↔ 前端 ACTION_META 对齐", meta_case)
    except Exception as exc:  # noqa: BLE001
        skip("raccoon actions", str(exc)[:60])

    # ---------------- 汇总 ----------------
    print("\n" + "-" * 64)
    print("通过 %d / 失败 %d / 跳过 %d" % (PASS, FAIL, SKIP))
    if FAILED:
        print("\n失败项：")
        for item in FAILED:
            print("  · %s" % item)
        print("\n这些不变量被破坏时接口**不会报错**，只会静默走错平台 / 记错账。")
        return 1
    print("热路径不变量全部成立。")
    return 0


def _assert(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


if __name__ == "__main__":
    sys.exit(main())
