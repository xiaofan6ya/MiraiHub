#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 app.asar 抽取字节区间或整个文件，便于精读被 minify 的源码。

**纯标准库实现**（委托给 `wb_asar.Asar`），不需要 Node/npm。
app.asar 位置自动发现；标记为 unpacked 的文件会自动去磁盘旁路目录读。

用法:
    # 按字节区间抽取（读 asar 数据区）
    python scripts/asar_slice.py 276590824 276600000

    # 按内部路径读单个文件（unpacked 也能读，如官方 cli/product.json）
    python scripts/asar_slice.py --read /cli/product.json

    # 列出内部文件（可用 --grep 过滤）
    python scripts/asar_slice.py --list --grep canvas

    # 抽取源码到目录（= 自动产出逆向产物，无需先跑 asar 解包）
    python scripts/asar_slice.py --extract --dest D:/wb_source

    # 显式指定 asar
    python scripts/asar_slice.py --list --asar "E:/workbuddy/resources/app.asar"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from wb_asar import Asar  # noqa: E402
from wb_install import WB  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _safe(text: str) -> str:
    return text.encode("utf-8", "replace").decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("start", nargs="?", type=int, help="起始字节（区间模式）")
    ap.add_argument("end", nargs="?", type=int, help="结束字节（区间模式）")
    ap.add_argument("--asar", default=None, help="app.asar 路径；默认自动发现")
    ap.add_argument("--read", default=None, help="读单个内部路径，如 /cli/product.json")
    ap.add_argument("--list", action="store_true", help="列出内部文件")
    ap.add_argument("--grep", default=None, help="配合 --list 过滤路径子串")
    ap.add_argument("--extract", action="store_true", help="抽取源码到 --dest")
    ap.add_argument("--dest", default=None, help="抽取目标目录（默认用户缓存目录）")
    args = ap.parse_args()

    asar = Path(args.asar) if args.asar else (WB.asar_path() or Path(""))
    if not asar or not asar.is_file():
        sys.stderr.write(
            "找不到 app.asar。\n"
            f"  {WB.describe()}\n"
            "请用 WORKBUDDY_INSTALL_DIR / WORKBUDDY_ASAR_PATH 指定，"
            "或 --asar 直接给出路径。\n"
        )
        return 2

    a = Asar(asar)

    if args.list:
        files = a.list_files()
        if args.grep:
            files = [f for f in files if args.grep in f]
        for f in files:
            st = a.stat(f) or {}
            tag = " [unpacked]" if st.get("unpacked") else ""
            print(f"{st.get('size', 0):>12,}  {f}{tag}")
        print(f"\n共 {len(files)} 个文件")
        return 0

    if args.read:
        txt = a.read_text(args.read)
        if txt is None:
            st = a.stat(args.read)
            if st and st.get("unpacked"):
                sys.stderr.write(
                    f"{args.read} 标记为 unpacked，但磁盘上没有对应文件"
                    f"（{a.unpacked_dir}）。数据不在 asar 内，无法读出。\n")
            else:
                sys.stderr.write(f"读不到 {args.read}\n")
            return 1
        print(_safe(txt))
        return 0

    if args.extract:
        dest = args.dest or str(WB.source_dir())
        stat = a.extract(dest)
        print(f"抽取到 {dest}：写入 {stat['written']} 个文件"
              f"（{stat['bytes'] / 1024 / 1024:.1f}MB），"
              f"跳过已存在 {stat['skipped_exists']}，"
              f"读不到 {stat['skipped_missing']}")
        if stat["missing_samples"]:
            print("  读不到的样例（通常是 unpacked 目录缺失）：")
            for s in stat["missing_samples"]:
                print("    -", s)
        return 0 if stat["written"] or stat["skipped_exists"] else 1

    if args.start is None or args.end is None:
        ap.error("区间模式需要同时给出 start 与 end；或用 --read/--list/--extract")

    # 区间模式：只读需要的区间（不整体载入 asar）
    # 注意：区间是**文件绝对字节**（含头部），与旧版行为一致。
    with open(asar, "rb") as f:
        f.seek(args.start)
        blob = f.read(args.end - args.start)
    print(_safe(blob.decode("utf-8", "replace")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
