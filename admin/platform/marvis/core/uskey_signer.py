"""Marvis 设备指纹本地签名 —— 直调 Beacon SDK，不再用随机字节凑长度。

## 为什么需要这个模块

    上游模型网关的 `Ual-Access-MarvisExt` 里带一个 `uskey` 字段。
    它是 Marvis 客户端 Beacon SDK 用设备密钥算出来的指纹，
    服务端会拿它做风控比对。

    原先 `endpoints.uskey_of()` 的实现是：
        base64.b64encode(os.urandom(376))     # 纯随机
    长度对得上，但内容不是真签名 —— 风控严格时会被判非法。

    本模块直接加载 `beacon_wrapper.dll`，调里面导出的
    `beacon_get_uskey(busiid, bus_info, out_buf, buf_size)`，
    签出和正版客户端**逐字节同源**的 uskey。

## bus_info 的构造口径（逆向 + 实测确认）

    marvisAgentTag=<agent_tag>
    &marvisConversationID=<conversation_id>
    &marvisResponseID=<response_id>
    &marvisScene=<scene>
    &model=<model>
    &nonce=<nonce>
    &originOpenID=<openid>

    实测结论：
        · 字段**顺序敏感**（换序 → 输出全变）
        · 每个字段都参与摘要
        · busiid 不参与运算（只是服务端路由标识）

## 失败降级

    DLL 缺失 / 加载失败 / 平台不是 Windows / 调用返回负值
    → 自动退回随机字节（保持网关可用，不因指纹问题整条链路挂掉）
    → 记一次 WARNING，便于发现问题
"""
from __future__ import annotations

import base64
import ctypes
import logging
import os
import platform
import threading
import uuid
from typing import Callable

log = logging.getLogger(__name__)

#: uskey 的字节长度。上游按长度校验格式，必须是 376。
USKEY_BYTES = 376


class SignerUnavailable(RuntimeError):
    """签名器不可用，且调用方要求 **fail-closed**（不许用随机字节顶替）。

    ## 为什么需要这个异常（fail-closed 的理由）

    DLL 缺失时旧行为是「随机填充 376 字节」，看上去网关还活着，实际后果：

      · 上游一定回 `4100404 请求被风控拦截`；
      · `_attempt()` 会把这个 4100404 记成**当前账号**的失败并挂冷却 ——
        一个环境问题（少两个 DLL）被写成「账号被风控」，用户去换号、重扫码、
        换 Key，全是无效动作；
      · 连撞几个号，整池进入风控冷却，网关自己开始回 `503 accounts_cooling`，
        真正的故障点（签名器没起来）被彻底掩盖。

    fail-closed 的做法是：**签不出来就别发请求**，把原因直说。宁可一条请求
    明确失败，也不要拿随机指纹去污染账号池。
    """

    def __init__(self, message: str, *, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


#: DLL 候选路径，按顺序找第一个存在的。
#:
#: 只用 `vendor/beacon/` 自带的副本 —— 这是「免客户端」的落地形态：
#: beacon_wrapper.dll + qimei.dll 两个文件（4.6 MB）自足，
#: 不需要 E:\Marvis 存在、不需要客户端进程在跑、不需要 app_key。
#: 实测（2026-11-18）：把这两个文件复制到空目录，sign 出 504 字符 uskey，
#: 前 24 字节与客户端目录签出的逐字节一致（0ac0d50c2111 65537d49aee5a5dd…）。
#:
#: ⚠ 这里**故意不再挂客户端路径当兜底**。
#:    挂过一版，问题是：vendor 一旦缺失就静默回退到客户端目录，
#:    表面上还能跑，实际上「能不能工作」又和客户端装没装绑在一起，
#:    换台机器才暴露。宁可直接降级成随机填充并在日志里喊出来 ——
#:    那样至少故障点是显式的。
#:
#: 要改用别的副本，设 `MARVIS_BEACON_DLL` 指向那个文件的绝对路径。
_VENDOR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))),
    "vendor", "beacon", "beacon_wrapper.dll")

DLL_CANDIDATES = (
    os.environ.get("MARVIS_BEACON_DLL", ""),
    _VENDOR,
)

#: 输出缓冲尺寸（DLL 内部会写 base64 文本，376 字节 → 504 字符）
_BUF_SIZE = 4096

#: prompt 默认场景参数（没有上下文时用）
_DEFAULTS = {
    "agent_tag": "main",
    "scene": "agent_chat",
    "model": "deepseek-v4-pro-external",
}


class _Beacon:
    """Beacon SDK 的一次性加载器（线程安全，失败后不再重试）。"""

    __slots__ = ("_fn", "_lib", "_dll_path", "_loaded", "_error", "_lock",
                 "_ok", "_remote")

    def __init__(self) -> None:
        self._fn: Callable | None = None
        self._lib = None
        self._dll_path = ""
        self._loaded = False
        self._ok = False
        self._error = ""
        self._lock = threading.Lock()
        self._remote = None

    # ------------------------------------------------------------------
    def _find_dll(self) -> str:
        for p in DLL_CANDIDATES:
            if p and os.path.isfile(p):
                return p
        return ""

    def load(self) -> bool:
        """幂等加载。成功返回 True，之后直接走缓存。

        ## 两种模式，自动选

            1. **本地**：Windows 上直接 ctypes 加载 vendor/beacon 的 DLL；
            2. **远程**：非 Windows（或本地 DLL 不可用）时，尝试连
               `MARVIS_USKEY_SERVICE`（默认 127.0.0.1:8791）——
               那是跑在 wine 里的签名服务，见 `tools/uskey_sign_service.py`。

        远程模式是为了让 Linux 网关也能用：Linux 的 ctypes.CDLL 连 PE 格式
        都不认，但 wine 能加载。把签名这一步代理给 wine，
        网关主体仍然跑原生 Linux python。
        """
        if self._loaded:
            return self._ok
        with self._lock:
            if self._loaded:
                return self._ok

            self._loaded = True

            if platform.system() != "Windows":
                # 非 Windows 上先试远程签名服务。这是线上（Linux）的正路。
                if self._try_remote():
                    return True
                self._error = (
                    "当前平台 %s 无法加载 beacon_wrapper.dll（Windows x64 PE），"
                    "且 uskey 签名服务不可达" % platform.system())
                log.error(
                    "Marvis uskey 签名不可用：%s。\n"
                    "  ⇒ uskey 会降级为随机填充，上游一定回 4100404。\n"
                    "  ⇒ Linux 上请起 wine 签名服务：\n"
                    "       cd /opt/marvis-sign && wine py/python.exe "
                    "uskey_sign_service.py --port 8791\n"
                    "     并设 MARVIS_USKEY_SERVICE=http://127.0.0.1:8791/sign\n"
                    "  ⇒ 或在 Windows 主机上跑网关（vendor/beacon 两个 DLL 自足）。",
                    self._error)
                return False

            dll = self._find_dll()
            if not dll:
                # Windows 上 vendor 缺失时，也允许走远程（比如另起一个服务）
                if self._try_remote():
                    return True
                self._error = "找不到 beacon_wrapper.dll"
                log.error("Marvis uskey 签名不可用：%s（可设 "
                          "MARVIS_BEACON_DLL 指向该文件），将降级为随机填充"
                          " —— 上游会回 4100404", self._error)
                return False

            try:
                # DLL 依赖同目录下的其它库，先把目录加进搜索路径
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(os.path.dirname(dll))

                lib = ctypes.CDLL(dll)
                fn = lib.beacon_get_uskey
                fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                               ctypes.c_char_p, ctypes.c_int]
                fn.restype = ctypes.c_int

                # beacon_init 有的版本没有，有就调
                init = getattr(lib, "beacon_init", None)
                if init is not None:
                    init.argtypes = []
                    init.restype = None
                    try:
                        init()
                    except Exception:  # noqa: BLE001
                        pass

                # 自检一次：空输入也要能跑通，跑不通就当加载失败
                probe = ctypes.create_string_buffer(_BUF_SIZE)
                r = fn(b"7800255", b"marvisAgentTag=main", probe, _BUF_SIZE)
                if r < 0:
                    self._error = f"自检返回 {r}"
                    log.warning("Marvis uskey 本地签名不可用：%s，退回随机填充",
                                self._error)
                    return False

                self._lib = lib
                self._fn = fn
                self._dll_path = dll
                self._ok = True
                log.info("Marvis uskey 本地签名已就绪：%s", dll)
                return True

            except Exception as exc:  # noqa: BLE001
                self._error = f"{type(exc).__name__}: {exc}"
                log.warning("Marvis uskey 本地签名不可用：%s，退回随机填充",
                            self._error)
                return False

    # ------------------------------------------------------------------
    def sign(self, busiid: str, bus_info: str) -> str:
        """签一个 uskey。本地有 DLL 就直接调，否则走远程服务。

        失败抛异常，由上层降级为随机填充。
        """
        if not self.load():
            raise RuntimeError(self._error or "签名器未就绪")

        # 远程模式：把活交给 wine 里跑的签名服务
        if self._remote is not None:
            return self._remote.sign(busiid, bus_info)

        if self._fn is None:
            raise RuntimeError(self._error or "Beacon 未加载")

        buf = ctypes.create_string_buffer(_BUF_SIZE)
        ret = self._fn(busiid.encode("utf-8"), bus_info.encode("utf-8"),
                       buf, _BUF_SIZE)
        if ret < 0:
            raise RuntimeError(f"beacon_get_uskey 返回 {ret}")

        text = buf.value.decode("ascii", "replace").strip()
        if not text:
            raise RuntimeError("beacon_get_uskey 返回空")
        return text

    # ------------------------------------------------------------------
    def _try_remote(self) -> bool:
        """试远程签名服务。通了就把模式切过去。"""
        try:
            from . import uskey_remote
        except Exception:  # noqa: BLE001
            return False
        try:
            if not uskey_remote.available():
                return False
        except Exception:  # noqa: BLE001
            return False

        self._remote = uskey_remote
        self._ok = True
        self._dll_path = uskey_remote.status().get("url", "")
        log.info("Marvis uskey 走远程签名服务：%s", self._dll_path)
        return True

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.load()

    @property
    def dll_path(self) -> str:
        return self._dll_path

    @property
    def error(self) -> str:
        self.load()
        return self._error


_beacon = _Beacon()


# ----------------------------------------------------------------------
# bus_info 构造
# ----------------------------------------------------------------------
def build_bus_info(*, openid: str, conversation_id: str = "",
                   response_id: str = "", scene: str = "",
                   model: str = "", nonce: str = "",
                   agent_tag: str = "") -> str:
    """按上游口径拼 bus_info。**字段顺序不能改**（实测顺序敏感）。"""
    return (
        f"marvisAgentTag={agent_tag or _DEFAULTS['agent_tag']}"
        f"&marvisConversationID={conversation_id}"
        f"&marvisResponseID={response_id}"
        f"&marvisScene={scene or _DEFAULTS['scene']}"
        f"&model={model or _DEFAULTS['model']}"
        f"&nonce={nonce or uuid.uuid4()}"
        f"&originOpenID={openid}"
    )


# ----------------------------------------------------------------------
# 对外接口
# ----------------------------------------------------------------------
def uskey(*, openid: str = "", busiid: str = "7800255",
          conversation_id: str = "", response_id: str = "",
          scene: str = "", model: str = "", nonce: str = "",
          agent_tag: str = "", bus_info: str = "",
          allow_fallback: bool = True) -> str:
    """签一个 uskey（base64 文本，504 字符 / 376 字节）。

    `bus_info` 给了就直接用它（缓存层已经拼好，避免重复拼导致字段顺序漂移）；
    没给就按其余参数现场拼。

    ## 两种失败策略，由 `allow_fallback` 决定

      · `allow_fallback=True`（默认，旧行为）：DLL 不可用 → 随机填充 376 字节。
        返回串永远非空，调用方无从分辨「真签」和「随机」。**业务主路径不要用**：
        随机指纹会被上游判 4100404，并把这个结果记到当前账号头上（见
        `SignerUnavailable` 的说明）。

      · `allow_fallback=False`（fail-closed）：签不出来抛 `SignerUnavailable`。
        `endpoints.uskey_identity()` 走的是这条，与 `MARVIS_USKEY_FAIL_CLOSED`
        开关（默认开启）配合。

    保留 `True` 作为默认值，是给探针 / 历史对比脚本用的 ——
    它们需要「无论环境怎样都能拿到一串东西」来复现旧结论。
    """
    if not bus_info:
        bus_info = build_bus_info(
            openid=openid,
            conversation_id=conversation_id,
            response_id=response_id,
            scene=scene,
            model=model,
            nonce=nonce,
            agent_tag=agent_tag,
        )

    try:
        return _beacon.sign(busiid, bus_info)
    except Exception as exc:  # noqa: BLE001
        if not allow_fallback:
            raise SignerUnavailable(
                "Marvis 客户端指纹签名不可用：%s" % (exc or "签名器未就绪"),
                detail=str(exc)[:300]) from exc
        # 降级不能静默：第一次失败已经 warning 过，这里只在 debug 留痕
        log.debug("uskey 真签名失败，退回随机填充：%s", exc)
        return base64.b64encode(os.urandom(USKEY_BYTES)).decode("ascii")


def status() -> dict:
    """给控制台 / 自检用的状态快照。"""
    ok = _beacon.available
    return {
        "available": ok,
        "dll": _beacon.dll_path,
        "error": "" if ok else _beacon.error,
        "bytes": USKEY_BYTES,
        "mode": "sdk" if ok else "random-fallback",
    }
