#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 app.asar 里做带上下文的原始字节检索。

**纯标准库实现**（委托给 `wb_asar.Asar`）：不依赖 Node / npm / asar 包，
且分块读取（不会把 297MB 整个读进内存）。app.asar 的位置自动发现。

注意检索范围：asar 的**数据区**。标记为 unpacked 的文件（如 `cli/product.json`、
`cli/dist/codebuddy.js`）正文不在 asar 里，而在旁边的 `app.asar.unpacked/`，
对它们请直接用普通 grep / 或 `wb_asar.py read`。

用法:
    python scripts/asar_grep.py "wbx_design_canvas_task_create" --before 800 --after 2500 --max 3
    python scripts/asar_grep.py "/v2/report" --regex --max 10
    python scripts/asar_grep.py "ardot" --asar "E:/workbuddy/resources/app.asar"
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from wb_asar import Asar  # noqa: E402
from wb_install import WB  # noqa: E402

try:  # Windows 控制台默认 GBK，会把替换字符打崩
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern")
    ap.add_argument("--asar", default=None, help="app.asar 路径；默认自动发现")
    ap.add_argument("--before", type=int, default=500)
    ap.add_argument("--after", type=int, default=1500)
    ap.add_argument("--max", type=int, default=5)
    ap.add_argument("--regex", action="store_true")
    ap.add_argument("--decode-errors", default="replace")
    args = ap.parse_args()

    asar = Path(args.asar) if args.asar else (WB.asar_path() or Path(""))
    if not asar or not asar.is_file():
        sys.stderr.write(
            "找不到 app.asar。\n"
            f"  {WB.describe()}\n"
            "请用 WORKBUDDY_INSTALL_DIR / WORKBUDDY_ASAR_PATH 指定，"
            "或 WORKBUDDY_DRIVES 增加扫描盘符，或用 --asar 直接给出路径。\n"
        )
        return 2

    hits = Asar(asar).search(
        args.pattern, max_hits=args.max, regex=args.regex,
        before=args.before, after=args.after)

    print(f"[asar] {asar} pattern={args.pattern!r} hits={len(hits)}")
    for n, (off, ctx) in enumerate(hits, 1):
        chunk = ctx.decode("utf-8", args.decode_errors)
        # 控制台安全：替换掉无法编码的代理字符
        chunk = chunk.encode("utf-8", "replace").decode("utf-8", "replace")
        print(f"\n{'=' * 30} HIT {n} @ {off} {'=' * 30}")
        print(chunk)
    print("\n提示：只检索 asar 数据区；unpacked 文件正文在磁盘上，用普通 grep。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
