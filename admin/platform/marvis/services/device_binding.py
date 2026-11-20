"""设备绑定骨架：把「账号」与**真机采集到的那一台设备**关联起来（本轮不接线）。

## 为什么需要它

上游对每个请求都要求 `Ual-Access-Guid`（缺了直接 -109），mint 时还要把
`qimei36` 写进签名 TLV。这两串在真机上**同源**：一台物理设备报一个 guid、
一个 qimei36，换设备两者一起换。

本仓库现状是两条线各自取值：

    · qimei36 —— `core/device.py::for_account(openid)` 按账号确定性派生
      （`sha256(salt|openid)`，第 24-27 位是格式标记 `3000`，见 `is_well_formed`）；
    · guid —— 直接读凭证里的 `guid` 列；`services/accounts.py` 在导入一份
      **没带 guid** 的凭证时会补一个 `uuid.uuid4()` 写回库。

两者互不知情，于是「这台设备的 guid」与「这台设备的 qimei36」在库里是两条
没有约束的独立字符串：任何一处被替换，都不会有人报错。真机的形状是
「一对同源设备串」，把它拆成两个自由变量，就是在给后续的配对错误留口子。

本模块只补**数据形状 + 闸门**：绑定信息存在凭证 JSON 的 `device` 键里，
mint 前通过 `require_device()` 取一次，保证「取到的 guid 与 qimei36 来自
同一次绑定」。

## 本轮范围（重要）

    ✅ 纯函数骨架：校验 / 归一化 / 绑定 / 读取 / 闸门 / 指纹摘要；
    ✅ 数据落点是已有凭证 JSON 的一个键（**不改表结构、不写迁移**）；
    ❌ 不接线：本模块**没有任何生产调用点**，import 它不改变任何行为；
    ❌ 不碰 `core/device.py`、不碰 `uskey` 签名链、不改 `guid` 的任何现有取值路径。

## 数据落点：凭证 JSON 里加一个 device 键

    {
      "openid": "...", "access_token": "...", "refresh_token": "...",
      "guid": "...",                 # 已有字段（账号级 guid，历史遗留）
      "device": {                    # ← 新增：设备绑定（本模块负责）
        "guid": "...",               # 这次登录所用设备的 guid
        "qimei36": "...",            # 同一台设备的 qimei36（36 hex）
        "bound_at": 1763500000,      # 绑定时刻（unix 秒）
        "source": "login"            # login / manual / migrated
      }
    }

为什么不用新列：schema 变更要走迁移（本轮明确不做），而这份 JSON 本来就是
「凭证 + 设备串」的落点，加一个键是纯 JSON 层面的事，回滚 = 删掉这个键。

## 接线顺序（下一轮做，本轮只留接口）

    1. 登录落地：凭证写回处（`services/accounts.py` 的 upsert / refresh）→ `bind_device()`；
    2. mint 前：调用处 → `require_device()`，把返回的 guid / qimei36 显式传下去，
       两处必须是同一份；
    3. 没绑定的号 → 抛 `DeviceBindingMissing`（code=`checkin_required`），
       由调用方决定回什么错误，**绝不**现场编一组设备串顶上。

## 校验规则为什么和 `core/device.py` 对齐

`is_valid_qimei36()` 除了长度 / hex，还要求第 24-27 位是格式标记 `3000` —— 这是
`core/device.py::is_well_formed` 的规则，也是上游认的形状。本模块**不复用**那个
函数，是因为 `admin.platform.marvis` 的包 `__init__` 在 import 时会跑
`install()`（登记平台 + 校验 qimei 池），而本模块的设计前提恰恰是
「import 它不产生任何副作用」。规则是两份、语义必须一致：改动 `device.py` 的
格式规则时，这里与 `tests/test_marvis_device_binding.py` 一起改。
"""
from __future__ import annotations

import time
import uuid

#: 凭证 JSON 里承载设备绑定的键名。
DEVICE_KEY = "device"
#: 一条绑定至少要有这两项，且必须**同源**（同一台设备采集）。
REQUIRED_FIELDS = ("guid", "qimei36")
#: 允许的绑定来源（写进 device.source，排障时一眼看出是真机采集还是历史迁移）。
SOURCES = ("login", "manual", "migrated")

#: qimei36 的格式标记与位置（与 `core/device.py` 的 DEVICE_MARK / _MARK_POS 一致）。
QIMEI_MARK = "3000"
QIMEI_MARK_POS = 24
#: qimei36 长度：36 hex = 18 字节，签名 TLV 里按 18 字节写。
QIMEI_LEN = 36


class DeviceBindingMissing(Exception):
    """这个号没有可用的设备绑定 ⇒ 不许发 chat。

    它是**拒绝发送**的信号，不是「换一组设备串继续」的信号：调用方拿到它
    应当让这个号回到「需要重新登录」的状态，而不是现场编 guid / qimei36。

    `code` 面向接口层（本模块不决定 HTTP 状态码）。
    """

    code = "checkin_required"

    def __init__(self, message: str = "", *, account_id: int = 0):
        super().__init__(message or "该账号没有设备绑定，需要重新登录")
        self.message = str(message or "该账号没有设备绑定，需要重新登录")
        self.account_id = int(account_id or 0)


def is_valid_guid(value: str) -> bool:
    """guid 是否可用：非空、长度合理、不是占位符。"""
    text = str(value or "").strip()
    if len(text) < 8 or len(text) > 64:
        return False
    return text.lower() not in ("0", "null", "none", "undefined", "unknown")


def is_valid_qimei36(value: str) -> bool:
    """qimei36 是否可用：36 个 hex，且第 24-27 位是格式标记 `3000`。

    纯 36 hex 并不够：`core/device.py` 的注释记了五个按 `sha256[:18]`
    直接切出来的值，第 24-27 位全是随机的，上游一次格式校验就能全判非法。
    校验放行一个形状不对的设备串，等于把它留到签名那一刻才炸。
    """
    text = str(value or "").strip()
    if len(text) != QIMEI_LEN:
        return False
    try:
        bytes.fromhex(text)
    except ValueError:
        return False
    return text[QIMEI_MARK_POS:QIMEI_MARK_POS + len(QIMEI_MARK)] == QIMEI_MARK


def device_of(credential: dict) -> dict:
    """取 device 绑定；没有 / 畸形返回 {}。**绝不抛**（只读探针路径也要能用）。"""
    data = credential or {}
    raw = data.get(DEVICE_KEY) if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    return normalize_device(raw)


def normalize_device(raw: dict) -> dict:
    """把任意来源的 device 规整成统一形状；缺字段 / 不合法返回 {}。

    返回的 source 不在白名单里一律记 `unknown`（而不是丢弃整条绑定）：
    设备串本身是有效的，来源只影响排障口径。
    """
    if not isinstance(raw, dict):
        return {}
    guid = str(raw.get("guid") or "").strip()
    qimei36 = str(raw.get("qimei36") or "").strip()
    if not is_valid_guid(guid) or not is_valid_qimei36(qimei36):
        return {}
    try:
        bound_at = int(raw.get("bound_at") or 0)
    except (TypeError, ValueError):
        bound_at = 0
    source = str(raw.get("source") or "").strip()
    return {
        "guid": guid,
        "qimei36": qimei36,
        "bound_at": bound_at,
        "source": source if source in SOURCES else "unknown",
    }


def require_device(credential: dict, *, account_id: int = 0) -> dict:
    """mint 前的闸门：没有合法绑定就抛 `DeviceBindingMissing`。

    ⚠ 这里**不允许**任何 fallback（不许读环境变量、不许现编 guid、不许退回
    任何一个共享常量）—— 缺绑定就是缺采集，正确动作是让这个号回到
    「需要重新登录」的状态。返回的 dict 是本次请求唯一该用的设备串来源。
    """
    device = device_of(credential)
    if not device:
        raise DeviceBindingMissing(
            "该账号没有设备绑定（guid + qimei36 同源），需要重新登录。",
            account_id=account_id)
    return device


def bind_device(credential: dict, *, guid: str, qimei36: str,
                source: str = "login", now: int | None = None) -> dict:
    """把一对设备串写进 `credential[DEVICE_KEY]` 并返回它（**调用方负责 commit**）。

    写入前必须校验：guid 合法、qimei36 是合规 36 hex、来源在白名单里。
    不合格直接抛 `ValueError` —— 静默吞掉只会让「绑定成功」变成谎话。

    重复绑定（同一个号再绑一次）走同一条路径：直接覆盖，并以新的
    `bound_at` / `source` 为准 —— 重登本来就应该换成本次采集到的设备串。
    """
    if not isinstance(credential, dict):
        raise TypeError("credential 必须是 dict")
    if not is_valid_guid(guid):
        raise ValueError("guid 不合法：%r" % (guid,))
    if not is_valid_qimei36(qimei36):
        raise ValueError("qimei36 不合法（必须是合规的 36 hex）：%r" % (qimei36,))
    if source not in SOURCES:
        raise ValueError("source 必须是 %r 之一：%r" % (SOURCES, source))
    device = {
        "guid": str(guid).strip(),
        "qimei36": str(qimei36).strip(),
        "bound_at": int(now if now is not None else time.time()),
        "source": source,
    }
    credential[DEVICE_KEY] = device
    return device


def unbind_device(credential: dict) -> bool:
    """删掉 `credential[DEVICE_KEY]`（**调用方负责 commit**）。

    返回是否真的删掉了一条。解绑 = 这个号回到「没有设备绑定」状态，
    下一次 `require_device()` 必然抛 `DeviceBindingMissing`；本函数**不**
    顺手补延长 bound_at 或留墓碑 —— 想留审计痕迹请写日志，别写进凭证。
    """
    if not isinstance(credential, dict):
        return False
    return credential.pop(DEVICE_KEY, None) is not None


def new_guid_seed() -> str:
    """生成一个 guid 形状的随机串。**不是**「没 guid 就编一个」的兜底。

    保留成显式函数只是为了让 grep 好找：生产路径不许用它代替设备采集，
    它能提供的只有「形状合法」，提供不了「这台设备真的存在」。
    """
    return str(uuid.uuid4())


def device_fingerprint(device: dict) -> str:
    """设备指纹摘要（日志 / 审计用，**不可逆、不含明文**）。"""
    import hashlib

    guid = str((device or {}).get("guid") or "")
    qimei36 = str((device or {}).get("qimei36") or "")
    if not guid and not qimei36:
        return ""
    blob = (guid + "|" + qimei36).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]
