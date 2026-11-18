"""Marvis uskey 签名侧车客户端 —— 生产口径。

## 两条路径：本地 mint（默认）→ 侧车（降级）

**2026-11-18 起 uskey 可以纯 Python 本地生成**，算法在 `core/uskey_local.py`：
三段（TEA 双反馈 → 异或掩 → AES-128-CBC）全部逆出来了，4/4 条真实 uskey
逐字节复现，且 `dev18` 用 `os.urandom(18)` 全随机也是上游 **HTTP 200**
（2026-11-18 实测 1.90 s，真实出字）。所以默认路径是 `_local_sign()`。

侧车（Wine + Windows node.exe，见 `deploy/marvis-uskey-sidecar/`）保留为
**降级路径**：本地 mint 抛异常时才 POST 它（`_http_sign()`）。要强制走侧车
（排障 / 对照实验）设 `MARVIS_USKEY_FORCE_SIDECAR=1`。

历史坑（别再踩）：当年手工拼 `magic + F(bus_info) + 随机尾` 100% 撞
风控错误，是因为 TLV 里还有 md5(bus_info)、两处设备串、15 字节进程常量
和 TEA/异或/AES 三层；**不是**"必须由 SDK 出"。现在三层都在
`uskey_local.py` 里，且偏移有校准常量（`OFF_*`），别手调。

## 铁律

* 本地 mint 失败**才**回落侧车；两条都失败就抛 `SidecarError`，
  **绝不**退化成随机 uskey —— 那会把「签名不可用」伪装成「上游风控」，
  让排障方向整体跑偏。
* 每组 `(cid, rid, nonce, uskey)` 只能用一次；被拒后必须**整组换新**再试。
* `qimei36` 必须与签这块 uskey 时用的那串**完全一致**（它写进 TLV 两处）；
  来源是环境变量 `MARVIS_QIMEI36`（见 `qimei36()`）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import urllib.error
import urllib.request
import uuid

logger = logging.getLogger("admin.platform.marvis.uskey")

#: 侧车地址。MiraiHub 跑在容器里时 `127.0.0.1` 是**容器自己**，
#: 必须走宿主 docker0 网关，所以默认值是 `172.17.0.1`。
#: 侧车与 MiraiHub 同 netns（host 网络 / 本机调试）时用
#: `MARVIS_USKEY_SIDECAR=http://127.0.0.1:8791/uskey` 覆盖。
SIDECAR_URL = (os.environ.get("MARVIS_USKEY_SIDECAR")
               or "http://172.17.0.1:8791/uskey").strip()

#: 侧车超时（秒）。侧车正常是毫秒级，5 s 只用来兜住「Wine 卡死」。
SIDECAR_TIMEOUT = float(os.environ.get("MARVIS_USKEY_TIMEOUT") or 5.0)

#: Beacon 的业务 id —— 侧车固定用它调 `getUskey`。
BUSINESS_ID = "7800255"

#: uskey 的 base64 头（= magic `0ac0d50c2111` 的前 8 字符）。只做体检。
USKEY_HEAD = "CsDVDCE"

#: 设备串（36 hex 字符 = 18 字节）**必须由调用方提供**。
#: 本模块不生成、不派生、不猜它 —— 它就是上游协议要求的一个输入。

#: 字节下限 = 真品口径 **376**（504 字符 base64）。
#:
#: 任何短包都是 bug，在本地拦死，不要发到上游。
#: 排障时可临时用 MARVIS_USKEY_MIN_BYTES 覆盖。
USKEY_MIN_BYTES = int(os.environ.get("MARVIS_USKEY_MIN_BYTES") or 376)


class SidecarError(RuntimeError):
    """侧车不可用或返回不合法（上层按 `network` / 平台不可用处理）。"""


#: 测试与离线环境的注入点：`set_signer(lambda **kw: {...})`。
_signer = None


def set_signer(fn=None):
    """注入/清空签名实现（`fn(**kwargs) -> {"uskey": ..., "qimei36": ...}`）。"""
    global _signer
    _signer = fn
    return _signer


def new_identity() -> dict:
    """每请求一组全新的 `(conversation_id, response_id, nonce)`。

    `conversation_id` 形如 `conv_` + 32 hex（与客户端抓包一致），
    `response_id` / `nonce` 是 uuid4。
    """
    return {
        "conversation_id": "conv_" + secrets.token_hex(16),
        "response_id": str(uuid.uuid4()),
        "nonce": str(uuid.uuid4()),
    }


def bus_info(*, agent_tag: str, conversation_id: str, response_id: str,
             scene: str, model: str, nonce: str, openid: str) -> str:
    """bus_info 七字段**固定顺序**拼接。

    顺序或分隔符错一个字符，`F(bus_info)` 就错，整块 uskey 被上游拒掉。
    这七项必须与 header / 请求体**一字不差**（model 取请求体原文）。
    """
    return ("marvisAgentTag=" + str(agent_tag) +
            "&marvisConversationID=" + str(conversation_id) +
            "&marvisResponseID=" + str(response_id) +
            "&marvisScene=" + str(scene) +
            "&model=" + str(model) +
            "&nonce=" + str(nonce) +
            "&originOpenID=" + str(openid))


def _validate(payload: dict) -> dict:
    """校验侧车回包；不合法就抛 —— **绝不**兜底造一个 uskey。"""
    if not isinstance(payload, dict):
        raise SidecarError("侧车回包不是 JSON 对象")
    uskey = str(payload.get("uskey") or "")
    qimei36 = str(payload.get("qimei36") or "")
    if not uskey:
        raise SidecarError("侧车没有返回 uskey：%s" % payload.get("error") or payload)
    try:
        raw = base64.b64decode(uskey, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise SidecarError("侧车返回的 uskey 不是合法 base64") from exc
    if len(raw) < USKEY_MIN_BYTES:
        raise SidecarError("侧车返回的 uskey 太短（%d 字节 < %d；uskeyLen=%d）"
                           % (len(raw), USKEY_MIN_BYTES, len(uskey)))
    if not uskey.startswith(USKEY_HEAD):
        # 不硬失败：magic 变了说明 SDK 换了版本，记一笔就够。
        logger.warning("uskey 头不是预期的 %s：前 8 字符 %s",
                       USKEY_HEAD, uskey[:8])
    if len(qimei36) != 36:
        logger.warning("qimei36 长度异常：%d", len(qimei36))
    return {"uskey": uskey, "qimei36": qimei36,
            "uskey_bytes": len(raw), "uskey_len": len(uskey),
            "bus_info": str(payload.get("busInfo") or ""),
            "ms": payload.get("ms")}


def _http_sign(**kwargs) -> dict:
    """POST 侧车 `/uskey`。"""
    body = json.dumps({
        "agentTag": kwargs["agent_tag"],
        "conversationId": kwargs["conversation_id"],
        "responseId": kwargs["response_id"],
        "scene": kwargs["scene"],
        "model": kwargs["model"],
        "nonce": kwargs["nonce"],
        "openId": kwargs["openid"],
    }, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        SIDECAR_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=SIDECAR_TIMEOUT) as resp:
            text = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        raise SidecarError("uskey 侧车 HTTP %d：%s" % (exc.code, detail)) from exc
    except Exception as exc:  # noqa: BLE001
        raise SidecarError(
            "uskey 侧车不可达（%s）：%s" % (SIDECAR_URL, exc)) from exc
    try:
        payload = json.loads(text or "{}")
    except ValueError as exc:
        raise SidecarError("uskey 侧车回包不是 JSON：%r" % text[:200]) from exc
    return _validate(payload)






def qimei36() -> str:
    """取设备串。**必须由调用方通过环境变量提供**，本模块不生成。

    它写进 uskey 的 TLV 两处（OFF_QIMEI1 / OFF_QIMEI2），并且必须与
    header 里的同名字段完全一致 —— 三处同值。

    未提供时直接报错：绝不回退到任何硬编码值。
    """
    value = (os.environ.get('MARVIS_QIMEI36') or '').strip()
    if not value:
        raise ValueError(
            '未设置 MARVIS_QIMEI36：设备串必须由调用方提供（36 个 hex 字符）')
    if len(value) != 36:
        raise ValueError(
            'MARVIS_QIMEI36 长度必须是 36，实际 %d：%r' % (len(value), value))
    try:
        bytes.fromhex(value)
    except ValueError:
        raise ValueError('MARVIS_QIMEI36 必须是纯 hex 字符：%r' % value)
    return value


def _local_sign(**kwargs) -> dict:
    """纯本地 mint 一块 uskey（两次系统调用：urandom + 时间戳，不碰网络）。

    算法、偏移、常量全部在 `core/uskey_local.py`；这里只负责把 bus_info 拼对、
    取设备串（`qimei36()`，来自环境变量），并复用 :func:`_validate` 的字节下限体检。
    """
    from .uskey_local import mint_uskey
    info = bus_info(**kwargs)
    device = qimei36()
    value = mint_uskey(device, info, os.urandom(18))
    return _validate({"uskey": value, "qimei36": device,
                      "busInfo": info, "ms": 0})


def sign(*, agent_tag: str, conversation_id: str, response_id: str,
         scene: str, model: str, nonce: str, openid: str) -> dict:
    """取一块 uskey：**本地 mint 优先**，失败才回落侧车。

    返回值：`{"uskey", "qimei36", "uskey_len", "uskey_bytes", "bus_info", "ms"}`。

    顺序：(1) 注入的 `_signer`（测试/离线）；(2) `MARVIS_USKEY_FORCE_SIDECAR=1`
    强制侧车；(3) `_local_sign()`；(4) 本地异常 → `_http_sign()`。
    两条路都不可用抛 :class:`SidecarError`，上层当「平台暂时不可用」处理，
    **不是**「账号坏了」。
    """
    kwargs = dict(agent_tag=agent_tag, conversation_id=conversation_id,
                  response_id=response_id, scene=scene, model=model,
                  nonce=nonce, openid=openid)
    if _signer is not None:
        return _signer(**kwargs)
    if os.environ.get("MARVIS_USKEY_FORCE_SIDECAR") == "1":
        return _http_sign(**kwargs)
    try:
        return _local_sign(**kwargs)
    except ValueError:
        # 配置类错误（比如没给设备串）不是「侧车挂了」，直接抛，别掩盖真因。
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("本地 mint uskey 失败，回落侧车（%s）：%s",
                       SIDECAR_URL, exc)
        return _http_sign(**kwargs)


def health(timeout: float = 2.0) -> dict:
    """侧车体检（控制台/排障用）：`GET /health`。"""
    url = SIDECAR_URL[:-len("/uskey")] if SIDECAR_URL.endswith("/uskey") else SIDECAR_URL
    url = url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300], "url": url}
