#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯标准库的 Electron asar 读取 / 解包 / 检索（不依赖 Node、npm、asar 包）。

为什么需要这个模块
------------------
逆向取证原本依赖 `asar` npm 包来解包 app.asar，于是引入一串环境前提：
装了 Node、装了 npm、能在 workspace 里 `npm i asar`、磁盘放得下解包产物。
用户机器上**这些前提经常不成立**（服务器上没装 Node、npm 装不动、嫌 287MB
解包太占地方），结果就是「拿不到源码 → 用不了」。

但 asar 的格式极其简单，是**未压缩的拼接文件**：一个 JSON 头部记录每个文件的
偏移与长度，后面紧跟着原始字节。用标准库 40 行就能读，根本不需要 Node。
本模块因此提供三种能力，全部零第三方依赖：

    list_files()  列出 asar 内所有文件（含大小 / 偏移 / 是否 unpacked）
    read_file()   读出某个文件的原始字节（**自动处理 unpacked**：见下）
    extract()     按前缀/后缀把需要的源码抽到目标目录（即「自动产出逆向产物」）
    search()      在 asar 数据区按字节检索（等价于对解包目录 grep）

关键概念：unpacked
------------------
Electron 打包时，原生模块与体积大/需可执行的文件不会被塞进 asar，而是留在
旁边的 `app.asar.unpacked/` 目录里；asar 头部把这些标成 `"unpacked": true`，
**偏移为 None**（数据不在 asar 内）。实测 WorkBuddy：

    /package.json              -> 在 asar 数据区（可纯 Python 读）
    /main/index.js             -> 在 asar 数据区
    /cli/product.json          -> unpacked（必须读磁盘上的 unpacked 目录）
    /cli/dist/codebuddy.js     -> unpacked
    /native/turing-sdk/*       -> unpacked

所以 `read_file()` 会自动分流：数据在 asar 里就直接定位读，标记 unpacked
就去 `<asar 同级>/app.asar.unpacked/<路径>` 读。调用方不用关心这个区别。
**unpacked 目录缺失时**，那部分文件是真的没了（asar 里没有备份），此时
`read_file()` 返回 None，由调用方回退到其它来源。

格式（Electron 官方布局，小端）
-------------------------------
    [0:4]     uint32 = 4
    [4:8]     uint32 = header_size
    [8:12]    uint32 = header_size + 4
    [12:16]   uint32 = json_str_len
    [16:16+json_str_len]  JSON 头部
    数据区起点 = 8 + header_size
    文件绝对偏移 = 数据区起点 + entry["offset"]

用法::

    from wb_asar import Asar
    a = Asar(path)
    print(a.list_files()[:5])
    obj = a.read_json("/package.json")      # -> dict 或 None
    n = a.extract(dest, prefixes=["main", "preload", "renderer"])
    hits = a.search(b"wbx_design_canvas_task_create", max_hits=3)
"""
from __future__ import annotations

import json
import logging
import os
import re
import struct
import sys
from pathlib import Path

_logger = logging.getLogger(__name__)

#: 头部前 16 字节固定，先读这么多就能知道 JSON 头有多长。
_PREFIX_LEN = 16

#: 默认抽取的源码前缀（与官方 asar 解包流程一致）
DEFAULT_PREFIXES = ("main", "preload", "renderer", "cli")

#: 默认抽取的扩展名（跳过原生二进制，避免抽出一堆 .node/.dll）
DEFAULT_EXTENSIONS = (".js", ".cjs", ".mjs", ".ts", ".html", ".json",
                      ".css", ".map", ".md", ".txt", ".yml", ".yaml")


class AsarError(Exception):
    """asar 文件缺失或头部损坏。"""


def _norm(path: str) -> str:
    """把各种写法的内部路径统一成 `a/b/c`（无前导斜杠、无反斜杠）。"""
    p = str(path).replace("\\", "/").strip()
    while p.startswith("/"):
        p = p[1:]
    return p


class Asar:
    """一个 app.asar 的只读视图（头部解析一次并缓存）。"""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self._header: dict | None = None
        self._data_offset: int = 0
        self._index: dict[str, dict] | None = None

    # -- 头部 ------------------------------------------------------------
    def _load_header(self) -> None:
        if self._header is not None:
            return
        if not self.path.is_file():
            raise AsarError(f"asar 不存在：{self.path}")
        with open(self.path, "rb") as f:
            head = f.read(_PREFIX_LEN)
            if len(head) < _PREFIX_LEN:
                raise AsarError(f"头部过短，不是合法的 asar：{self.path}")
            _a, header_size, _c, json_len = struct.unpack("<IIII", head)
            # 合理性校验：JSON 长度不可能超过文件本身
            if json_len <= 0 or json_len > self.path.stat().st_size:
                raise AsarError(f"头部长度异常（json_len={json_len}）：{self.path}")
            f.seek(_PREFIX_LEN)
            raw = f.read(json_len)
        try:
            self._header = json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise AsarError(f"头部 JSON 解析失败：{e}") from e
        # 数据区起点：pickle 载荷（8 字节）之后。
        # 实测校验：/package.json 的 offset=0，从 (8+header_size) 读出的正是合法 JSON。
        self._data_offset = 8 + header_size

    def _build_index(self) -> dict[str, dict]:
        if self._index is not None:
            return self._index
        self._load_header()
        out: dict[str, dict] = {}

        def walk(node: dict, prefix: str) -> None:
            for name, child in (node.get("files") or {}).items():
                p = f"{prefix}/{name}" if prefix else name
                if isinstance(child, dict) and "files" in child:
                    walk(child, p)
                elif isinstance(child, dict):
                    out[p] = child

        walk(self._header or {}, "")
        self._index = out
        return out

    # -- 查询 ------------------------------------------------------------
    def exists(self, inner_path: str) -> bool:
        return _norm(inner_path) in self._build_index()

    def list_files(self) -> list[str]:
        """所有内部路径，已排序（便于稳定输出/测试）。"""
        return sorted(self._build_index())

    def entry(self, inner_path: str) -> dict | None:
        return self._build_index().get(_norm(inner_path))

    def stat(self, inner_path: str) -> dict | None:
        """返回 {size, offset, unpacked}；不存在返回 None。"""
        e = self.entry(inner_path)
        if e is None:
            return None
        return {
            "size": int(e.get("size") or 0),
            "offset": e.get("offset"),
            "unpacked": bool(e.get("unpacked", False)),
        }

    @property
    def unpacked_dir(self) -> Path:
        """unpacked 文件所在目录（asar 同级的 app.asar.unpacked）。"""
        return self.path.parent / "app.asar.unpacked"

    def resolve_real_path(self, inner_path: str) -> Path | None:
        """标记为 unpacked 的文件在磁盘上的真实路径（存在才返回）。"""
        p = _norm(inner_path)
        cand = self.unpacked_dir / p
        return cand if cand.is_file() else None

    # -- 读取 ------------------------------------------------------------
    def read_file(self, inner_path: str) -> bytes | None:
        """读出文件原始字节；不存在或 unpacked 目录缺失时返回 None。

        自动分流：在 asar 数据区 -> seek 读；标记 unpacked -> 读磁盘旁路目录。
        """
        st = self.stat(inner_path)
        if st is None:
            return None

        # 1) unpacked：数据不在 asar 里，去同级 unpacked 目录找
        if st["unpacked"] or st["offset"] is None:
            real = self.resolve_real_path(inner_path)
            if real is None:
                _logger.debug(
                    "asar 内 %s 标记为 unpacked，但磁盘上没有（%s）",
                    inner_path, self.unpacked_dir)
                return None
            try:
                return real.read_bytes()
            except OSError as e:
                _logger.debug("读 unpacked 文件失败 %s：%s", real, e)
                return None

        # 2) 数据在 asar 中：只读需要的区间，不整体载入
        self._load_header()
        try:
            size = st["size"]
            with open(self.path, "rb") as f:
                f.seek(self._data_offset + int(st["offset"]))
                blob = f.read(size)
            # size 偶尔不可靠（如 0），此时按剩余长度兜底意义不大，直接返回
            return blob if len(blob) == size or blob else blob
        except (OSError, ValueError) as e:
            _logger.debug("读 asar 内 %s 失败：%s", inner_path, e)
            return None

    def read_text(self, inner_path: str, encoding: str = "utf-8") -> str | None:
        blob = self.read_file(inner_path)
        if blob is None:
            return None
        return blob.decode(encoding, "replace")

    def read_json(self, inner_path: str) -> dict | None:
        """读 JSON 文件（asm 里的配置文件几乎都是 JSON）。失败返回 None。"""
        txt = self.read_text(inner_path)
        if txt is None:
            return None
        try:
            obj = json.loads(txt)
        except Exception:
            return None
        return obj if isinstance(obj, dict) else None

    def iter_entries(self, prefixes=None, extensions=None):
        """按前缀 + 扩展名筛出内部路径（供 extract 使用）。"""
        for p in self.list_files():
            if prefixes:
                top = p.split("/", 1)[0]
                if top not in prefixes:
                    continue
            if extensions:
                low = p.lower()
                if not any(low.endswith(ext) for ext in extensions):
                    continue
            yield p

    # -- 抽取（= 自动产出逆向产物）---------------------------------------
    def survey(self, prefixes=None, extensions=None) -> dict:
        """**只统计不写盘**：这次抽取会涉及多少文件、多少字节。

        后台点「拆包」前要先告诉用户「要写 200MB、约 15 秒」，
        并且 `extract()` 的进度分母也来自这里。同时把 asar 头部解析一次性
        做完（本来 extract 也要做），所以它不是额外开销。
        """
        prefixes = set(prefixes) if prefixes else set(DEFAULT_PREFIXES)
        extensions = tuple(extensions) if extensions else DEFAULT_EXTENSIONS

        files = 0
        total = 0
        unpacked = 0
        missing = 0
        for inner in self.iter_entries(prefixes, extensions):
            st = self.stat(inner)
            if st is None:
                continue
            files += 1
            total += st["size"]
            if st["unpacked"] or st["offset"] is None:
                unpacked += 1
                # unpacked 文件的数据在磁盘旁路目录；那里缺了就是真的没有
                if self.resolve_real_path(inner) is None:
                    missing += 1
        return {
            "files": files,
            "bytes": total,
            "unpacked": unpacked,
            "missing": missing,
            "prefixes": sorted(prefixes),
            "extensions": list(extensions),
        }

    def extract(self, dest: str | os.PathLike, prefixes=None, extensions=None,
                overwrite: bool = False, progress=None) -> dict:
        """把源码抽到 dest，返回统计信息。

        这是「逆向产物不一定有」的解法：不再要求用户先手工跑一遍 asar 解包，
        需要时直接从原始安装位置产出。**unpacked 文件也会一并落地**
        （从磁盘旁路目录复制），所以抽完的目录是完整可读的。

        参数前缀/扩展名默认覆盖 main/preload/renderer/cli 的文本源码，
        跳过 .node/.dll 等原生二进制。

        `progress` 是可选回调 `fn(done, total, current_path)`：实测全量抽取
        是 2242 个文件 / 225MB / 约 14 秒，后台必须能给用户显示进度，
        否则界面上就是「点了没反应」。回调抛异常不影响抽取。
        """
        dest = Path(dest)
        prefixes = set(prefixes) if prefixes else set(DEFAULT_PREFIXES)
        extensions = tuple(extensions) if extensions else DEFAULT_EXTENSIONS

        written = 0
        skipped_missing = 0
        skipped_exists = 0
        total_bytes = 0
        missing_samples: list[str] = []

        # 先算总数：进度条要有分母。iter_entries 只是遍历索引（不读数据），
        # 相对于写 225MB 来说开销可忽略。
        targets = list(self.iter_entries(prefixes, extensions))
        total = len(targets)
        if progress:
            try:
                progress(0, total, "")
            except Exception:
                pass

        for i, inner in enumerate(targets, 1):
            target = dest / inner
            if target.exists() and not overwrite:
                skipped_exists += 1
            else:
                blob = self.read_file(inner)
                if blob is None:
                    skipped_missing += 1
                    if len(missing_samples) < 5:
                        missing_samples.append(inner)
                else:
                    try:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(blob)
                        written += 1
                        total_bytes += len(blob)
                    except OSError as e:
                        _logger.debug("写入 %s 失败：%s", target, e)
                        skipped_missing += 1
            if progress:
                try:
                    progress(i, total, inner)
                except Exception:
                    pass

        return {
            "dest": str(dest),
            "written": written,
            "bytes": total_bytes,
            "skipped_exists": skipped_exists,
            "skipped_missing": skipped_missing,
            "missing_samples": missing_samples,
            "total": total,
        }

    # -- 检索 ------------------------------------------------------------
    def search(self, pattern, max_hits: int = 10, regex: bool = False,
               before: int = 200, after: int = 400):
        """在 asar 数据区按字节检索，返回 [(offset, 上下文片段), ...]。

        注意：**只搜 asar 数据区**，unpacked 文件的正文不在这里（它们在磁盘上，
        用普通 grep 即可）。这是与「对解包目录 grep」的差别，输出里会标注。
        """
        if isinstance(pattern, str):
            pattern = pattern.encode("utf-8")
        size = self.path.stat().st_size
        self._load_header()
        hits = []
        if regex:
            rx = re.compile(pattern)
            with open(self.path, "rb") as f:
                data = f.read()
            for m in rx.finditer(data):
                hits.append((m.start(), data[max(0, m.start() - before):
                                             m.start() + after]))
                if len(hits) >= max_hits:
                    break
            return hits

        chunk = 1 << 20
        overlap = before + after + len(pattern)
        with open(self.path, "rb") as f:
            pos = 0
            tail = b""
            while pos < size and len(hits) < max_hits:
                f.seek(pos)
                buf = tail + f.read(chunk)
                start = 0
                while len(hits) < max_hits:
                    i = buf.find(pattern, start)
                    if i < 0:
                        break
                    abs_off = pos - len(tail) + i
                    lo = max(0, i - before)
                    hits.append((abs_off, buf[lo:i + after]))
                    start = i + 1
                tail = buf[-overlap:] if len(buf) > overlap else buf
                pos += chunk
        return hits


def find_asar(install_dir=None) -> Path | None:
    """定位 app.asar：显式安装目录 > 环境变量 > 自动发现 > None。"""
    if install_dir:
        p = Path(install_dir) / "resources" / "app.asar"
        return p if p.is_file() else None
    # 走统一发现逻辑（不写死盘符）
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from wb_install import WB
        return WB.asar_path()
    except Exception:
        env = (os.getenv("WORKBUDDY_ASAR_PATH") or "").strip()
        if env and Path(env).is_file():
            return Path(env)
        return None


def _cli(argv=None) -> int:
    """命令行入口：定位 / 列出 / 读取 / 抽取 / 检索 app.asar。

    设计意图：**取代对 `asar` npm 包的依赖**。以前要解包得先装 Node+npm 再
    `npm i asar`；现在一个 Python 命令就够，服务器上也能跑。

        python wb_asar.py where                  # 打印自动发现的 asar 路径
        python wb_asar.py list --grep canvas     # 列出内部文件
        python wb_asar.py read /package.json     # 读单个文件（unpacked 自动分流）
        python wb_asar.py extract [--dest DIR]   # 抽取源码（默认到用户缓存目录）
        python wb_asar.py search "wbx_design"    # 按字节检索
    """
    import argparse

    ap = argparse.ArgumentParser(
        prog="wb_asar",
        description="纯标准库读写 Electron asar（无需 Node/npm）")
    ap.add_argument("--asar", default=None, help="app.asar 路径；默认自动发现")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("where", help="打印 asar 路径与来源诊断")

    p_list = sub.add_parser("list", help="列出内部文件")
    p_list.add_argument("--grep", default=None, help="按子串过滤路径")

    p_read = sub.add_parser("read", help="读出单个文件（打印为文本）")
    p_read.add_argument("inner", help="内部路径，如 /package.json")

    p_ex = sub.add_parser("extract", help="抽取源码（自动产出逆向产物）")
    p_ex.add_argument("--dest", default=None,
                      help="目标目录；默认用 wb_install.WB.source_dir()")
    p_ex.add_argument("--prefixes", default=None,
                      help="逗号分隔的顶层目录，默认 main,preload,renderer,cli")
    p_ex.add_argument("--extensions", default=None,
                      help="逗号分隔的扩展名，默认文本类源码")
    p_ex.add_argument("--force", action="store_true", help="覆盖已存在文件")

    p_se = sub.add_parser("search", help="在 asar 数据区按字节检索")
    p_se.add_argument("pattern")
    p_se.add_argument("--max", type=int, default=5)
    p_se.add_argument("--regex", action="store_true")
    p_se.add_argument("--before", type=int, default=200)
    p_se.add_argument("--after", type=int, default=400)

    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    path = Path(args.asar) if args.asar else find_asar()
    if args.cmd == "where":
        print(f"asar = {path}")
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from wb_install import WB
            print(WB.describe())
        except Exception:
            pass
        return 0 if path and Path(path).is_file() else 2
    if not path or not Path(path).is_file():
        sys.stderr.write(
            "找不到 app.asar。请用 --asar 指定，或设置环境变量 "
            "WORKBUDDY_INSTALL_DIR / WORKBUDDY_ASAR_PATH，"
            "或把安装盘符加进 WORKBUDDY_DRIVES。\n")
        return 2

    a = Asar(path)
    if args.cmd == "list":
        files = a.list_files()
        if args.grep:
            files = [f for f in files if args.grep in f]
        for f in files:
            st = a.stat(f) or {}
            tag = " [unpacked]" if st.get("unpacked") else ""
            print(f"{st.get('size', 0):>12,}  {f}{tag}")
        print(f"\n共 {len(files)} 个文件")
        return 0

    if args.cmd == "read":
        txt = a.read_text(args.inner)
        if txt is None:
            st = a.stat(args.inner)
            if st and st.get("unpacked"):
                sys.stderr.write(
                    f"{args.inner} 标记为 unpacked，但磁盘上没有对应文件"
                    f"（{a.unpacked_dir}）。该文件的数据不在 asar 内。\n")
            else:
                sys.stderr.write(f"读不到 {args.inner}\n")
            return 1
        print(txt)
        return 0

    if args.cmd == "extract":
        dest = args.dest
        if not dest:
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from wb_install import WB
                dest = str(WB.source_dir())
            except Exception:
                dest = str(Path.cwd() / "app_source")
        prefixes = (tuple(p.strip() for p in args.prefixes.split(",") if p.strip())
                    if args.prefixes else None)
        extensions = (tuple(e.strip() for e in args.extensions.split(",") if e.strip())
                      if args.extensions else None)
        print(f"从 {path} 抽取到 {dest} …")
        stat = a.extract(dest, prefixes=prefixes, extensions=extensions,
                         overwrite=args.force)
        print(f"  写入 {stat['written']} 个文件"
              f"（{stat['bytes'] / 1024 / 1024:.1f}MB）"
              f"，跳过已存在 {stat['skipped_exists']}"
              f"，读不到 {stat['skipped_missing']}")
        if stat["missing_samples"]:
            print("  读不到的样例（通常是 unpacked 目录缺失）：")
            for s in stat["missing_samples"]:
                print("    -", s)
        return 0 if stat["written"] or stat["skipped_exists"] else 1

    if args.cmd == "search":
        hits = a.search(args.pattern, max_hits=args.max, regex=args.regex,
                        before=args.before, after=args.after)
        print(f"[asar] {path} pattern={args.pattern!r} hits={len(hits)}")
        for n, (off, ctx) in enumerate(hits, 1):
            print(f"\n===== HIT {n} @ {off} =====")
            print(ctx.decode("utf-8", "replace"))
        print("\n提示：只检索 asar 数据区；unpacked 文件的正文在磁盘上，用普通 grep。")
        return 0

    return 0


__all__ = ["Asar", "AsarError", "find_asar",
           "DEFAULT_PREFIXES", "DEFAULT_EXTENSIONS"]


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_cli())
