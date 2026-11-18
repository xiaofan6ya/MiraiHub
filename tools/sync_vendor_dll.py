"""vendor DLL 自动同步 —— 解决「客户端一更新就要手动重新拿 DLL」。

## 它解决什么

    依赖 beacon_wrapper.dll + qimei.dll 的代价不是「要重写代码」，
    而是「客户端更新后要重新复制文件」。手工做容易漏、容易忘，
    更怕的是复制了不配套的两个版本（wrapper 新、qimei 旧）导致签名静默失败。

    本脚本把这件事变成一条命令：
        1. 扫所有已安装的 Marvis（注册表 + 常见路径）
        2. 取版本最高的那个目录
        3. 与 vendor 当前副本比 hash
        4. 不同就复制过去，并做一次签名自检
        5. 自检失败自动回滚（不留半坏状态）

## 为什么不直接去客户端目录加载

    那样等于把「能不能用」绑在客户端装没装上。vendor 自持一份，
    客户端卸载、换机器、扔服务器都不受影响 —— 前一轮已经验证过。

## 用法

    python tools/sync_vendor_dll.py              # 检查并同步
    python tools/sync_vendor_dll.py --check      # 只看差异，不动文件
    python tools/sync_vendor_dll.py --force      # 版本相同也覆盖
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR_DIR = os.path.join(ROOT, "vendor", "beacon")
FILES = ("beacon_wrapper.dll", "qimei.dll")

#: 自己实现的签名自检 —— 不 import 项目代码，避免循环依赖
SELFTEST = r'''
import base64, ctypes, json, os, sys
dll = sys.argv[1]
if hasattr(os, "add_dll_directory"):
    try: os.add_dll_directory(os.path.dirname(os.path.abspath(dll)))
    except OSError: pass
out = {}
try:
    lib = ctypes.CDLL(dll)
    init = lib.beacon_init
    init.argtypes = []; init.restype = ctypes.c_int
    out["init"] = init()
    fn = lib.beacon_get_uskey
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    fn.restype = ctypes.c_int
    buf = ctypes.create_string_buffer(8192)
    bi = ("marvisAgentTag=main&marvisConversationID="
          "11111111-1111-4111-8111-111111111111&marvisResponseID=" + "a"*32 +
          "&marvisScene=agent_chat&model=deepseek-v4-pro-external"
          "&nonce=22222222-2222-4222-8222-222222222222"
          "&originOpenID=E35C97C155B04B69BE4CE83A4929F172")
    out["ret"] = fn(b"7800255", bi.encode(), buf, 8192)
    txt = buf.value.decode("ascii", "replace").strip()
    raw = base64.b64decode(txt + "=" * (-len(txt) % 4))
    out["len"] = len(raw)
    out["head"] = raw[:6].hex()
    # qimei 也验一下，缺 qimei.dll 时这里会失败
    qf = getattr(lib, "beacon_get_qimei", None)
    if qf:
        qf.argtypes = [ctypes.c_char_p, ctypes.c_int]
        qf.restype = ctypes.c_int
        qb = ctypes.create_string_buffer(8192)
        qf(qb, 8192)
        out["qimei"] = qb.value.decode("ascii", "replace").strip()
except Exception as e:
    out["error"] = "%s: %s" % (type(e).__name__, e)
print(json.dumps(out))
'''


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def discover_installs() -> list[dict]:
    """找出所有已安装的 Marvis 版本目录。"""
    sys.path.insert(0, ROOT)
    found: dict[str, dict] = {}

    roots: list[str] = []
    # 注册表 InstallPath
    try:
        import winreg

        for hive, sub in ((winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\Tencent\Marvis"),
                          (winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\WOW6432Node\Tencent\Marvis"),
                          (winreg.HKEY_CURRENT_USER,
                           r"SOFTWARE\Tencent\Marvis")):
            try:
                with winreg.OpenKey(hive, sub) as key:
                    val, _ = winreg.QueryValueEx(key, "InstallPath")
                if val:
                    roots.append(str(val))
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass

    roots += [r"E:\Marvis", r"C:\Program Files\Tencent\Marvis",
              r"C:\Program Files (x86)\Tencent\Marvis"]

    for root in dict.fromkeys(roots):
        appdir = os.path.join(root, "Application")
        if not os.path.isdir(appdir):
            continue
        for name in os.listdir(appdir):
            parts = name.split(".")
            if len(parts) < 3 or not all(p.isdigit() for p in parts):
                continue
            native = os.path.join(appdir, name, "win32-native")
            wrapper = os.path.join(native, "beacon_wrapper.dll")
            if not os.path.isfile(wrapper):
                continue
            found[name] = {
                "version": name,
                "key": tuple(int(p) for p in parts),
                "dir": native,
                "wrapper": wrapper,
                "qimei": os.path.join(native, "qimei.dll"),
            }
    return sorted(found.values(), key=lambda x: x["key"], reverse=True)


def selftest(native_dir: str) -> dict:
    """用目录里的 DLL 跑一次签名自检。"""
    wrapper = os.path.join(native_dir, "beacon_wrapper.dll")
    if not os.path.isfile(wrapper):
        return {"error": "beacon_wrapper.dll 不存在"}
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(SELFTEST)
        script = fh.name
    try:
        proc = subprocess.run([sys.executable, script, wrapper],
                              capture_output=True, text=True, timeout=120,
                              encoding="utf-8", errors="replace")
        out = (proc.stdout or "").strip()
        if not out:
            return {"error": (proc.stderr or "无输出")[:200]}
        import json as _json

        return _json.loads(out.splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        try:
            os.unlink(script)
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true", help="只报告，不改文件")
    p.add_argument("--force", action="store_true", help="版本相同也覆盖")
    args = p.parse_args(argv)

    print("=" * 74)
    print("已安装的 Marvis")
    print("=" * 74)
    installs = discover_installs()
    if not installs:
        print("  ❌ 没找到任何 Marvis 安装")
        print("     若客户端已卸载，vendor 里的副本可以继续用（这正是它的意义）。")
        return 0
    for it in installs:
        print("  %-18s %s" % (it["version"], it["dir"]))

    best = installs[0]
    print("\n  最高版本: %s" % best["version"])

    print()
    print("=" * 74)
    print("与 vendor 对比")
    print("=" * 74)
    os.makedirs(VENDOR_DIR, exist_ok=True)

    diffs: list[str] = []
    for name in FILES:
        src = os.path.join(best["dir"], name)
        dst = os.path.join(VENDOR_DIR, name)
        if not os.path.isfile(src):
            print("  %-22s 源缺失，跳过" % name)
            continue
        src_hash = sha256_of(src)
        dst_hash = sha256_of(dst) if os.path.isfile(dst) else "(无)"
        same = src_hash == dst_hash
        print("  %-22s %s" % (name, "相同" if same else "不同"))
        if not same:
            print("      源  %s  %d 字节" % (src_hash[:16], os.path.getsize(src)))
            if dst_hash != "(无)":
                print("      vendor %s  %d 字节"
                      % (dst_hash[:16], os.path.getsize(dst)))
            diffs.append(name)

    if not diffs and not args.force:
        print("\n  ✅ vendor 已是最新，无需同步")
        return 0

    if args.check:
        print("\n  （--check：只报告，未改动）")
        return 0

    # ---------- 备份 ----------
    print()
    print("=" * 74)
    print("备份并同步")
    print("=" * 74)
    backup = os.path.join(VENDOR_DIR, "_backup")
    os.makedirs(backup, exist_ok=True)
    for name in FILES:
        cur = os.path.join(VENDOR_DIR, name)
        if os.path.isfile(cur):
            shutil.copy2(cur, os.path.join(backup, name))
            print("  备份 %s" % name)

    # ---------- 复制到临时目录先自检 ----------
    stage = tempfile.mkdtemp(prefix="vendor-stage-")
    try:
        for name in FILES:
            src = os.path.join(best["dir"], name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(stage, name))

        print("\n  对新副本做签名自检 …")
        st = selftest(stage)
        print("    %s" % st)
        ok = (st.get("init") == 0 and st.get("ret", -1) >= 0
              and st.get("head") == "0ac0d50c2111")
        if not ok:
            print("\n  ❌ 自检未通过，放弃同步（vendor 保持原样）")
            return 1

        for name in FILES:
            src = os.path.join(stage, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(VENDOR_DIR, name))
                print("  已更新 %s" % name)
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    # ---------- 同步后复验 ----------
    print("\n  复验 vendor 副本 …")
    st2 = selftest(VENDOR_DIR)
    print("    %s" % st2)
    ok2 = (st2.get("init") == 0 and st2.get("ret", -1) >= 0
           and st2.get("head") == "0ac0d50c2111")
    if not ok2:
        print("\n  ❌ 更新后自检失败，回滚")
        for name in FILES:
            src = os.path.join(backup, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(VENDOR_DIR, name))
                print("  已回滚 %s" % name)
        return 1

    print("\n  ✅ 同步完成且自检通过")
    print("     uskey 头部 %s  len=%s  qimei=%s"
          % (st2.get("head"), st2.get("len"), st2.get("qimei", "")[:20]))
    print("\n  重启网关生效：")
    print("     停止现有 uvicorn 进程后重新启动即可")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
