"""Marvis 设备身份 —— **一个账号一份** qimei36 + dev18。

## 为什么必须一账号一设备

    `beacon_wrapper.dll` 是一个**设备级**组件：同一枚 DLL 里
    `beacon_get_qimei()` 永远返回同一串设备号。所以网关里 4 个账号
    签出来的 uskey，TLV 里两处设备段完全一样 —— 上游看到的是

        设备 A → openid 402 / 403 / 404 / 411

    这是账号农场最标准的形状，比任何速率特征都先被判死，直接
    `4100404 请求被风控拦截`。而真机形态永远是「一台设备一个号」。

## 怎么派生

    `sha256(SALT | openid)` 切成两半：
        · 前 18 字节 → qimei36（36 个 hex，写进 TLV 的两处偏移）
        · 再 18 字节 → dev18（载荷头里的设备串）

    确定性派生 = 同一账号永远同一个设备，重启不变、换进程不变；
    换账号必然换设备，物理上不可能再撞。

    `SALT` 取 `MARVIS_DEVICE_SALT`，默认值为部署常量。**不需要**保密，
    它只用来把 openid 打散成互不相同的设备串；真正做校验的是上游。

## 覆盖

    想给某个号指定真机设备串（例如从客户端抓到的），设：

        MARVIS_QIMEI36_<openid>=<36 hex>

    openid 里的 `-` / `_` 一律写成 `_`（环境变量名不允许 `-`）。
    这条优先于派生值，便于个别账号做对照实验。
"""
from __future__ import annotations

import hashlib
import logging
import os

log = logging.getLogger(__name__)

#: 派生盐。换掉它等于给全池换一批设备（上游会看到一批新设备上线）。
DEFAULT_SALT = "wb2api-marvis-device-v1"

#: 设备串长度：qimei36 = 36 hex，dev18 = 18 字节
QIMEI_HEX_LEN = 36
DEV_BYTES = 18

#: qimei36 的**格式标记**：第 24-27 位固定是这四个字符。
#:
#: 真机实测（DLL `beacon_get_qimei()` 在本机拿到的）：
#:
#:     db4215ebb2ed553ee3  3000  1461950c
#:     └── 24 hex 摘要 ──┘ └标记┘ └─ 8 hex 尾 ─┘
#:
#: 这 4 位跨环境恒定，是**格式标记**而不是数据的一部分。
#: 2026-11-19 之前这里直接 `sha256(...)[:18].hex()` 取 36 位，
#: 第 24-27 位是随机的 —— 五个账号逐个查下来，没有一个合规：
#:
#:     acc=402  ...7475811b 2b2a 6a3385d9    ← 不是 3000
#:     acc=403  ...d304d605 a872 9dcd2ac3    ← 不是 3000
#:
#: 上游只要做一次格式校验，这些 qimei 全部判非法。
#: 现在按 PR #63（paul2333）的口径生成：24 hex + 3000 + 8 hex。
DEVICE_MARK = "3000"
#: 摘要部分的十六进制位数（标记之前）
_MARK_POS = 24


def _salt() -> str:
    return (os.environ.get("MARVIS_DEVICE_SALT") or DEFAULT_SALT).strip()


def _override_key(openid: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in openid)
    return "MARVIS_QIMEI36_" + safe


def is_well_formed(qimei: str) -> bool:
    """qimei36 是否符合真机格式（36 hex，且第 24-27 位是 `3000`）。"""
    q = (qimei or "").strip()
    if len(q) != QIMEI_HEX_LEN:
        return False
    try:
        bytes.fromhex(q)
    except ValueError:
        return False
    return q[_MARK_POS:_MARK_POS + len(DEVICE_MARK)] == DEVICE_MARK


def derive(openid: str) -> tuple[str, bytes]:
    """openid → (qimei36, dev18)。同一个 openid 永远得到同一组值。

    qimei36 按真机格式拼：`24 hex 摘要 + 3000 + 8 hex 尾段`。
    摘要和尾段都取自 `sha256(salt|openid)`，所以：
      · 同一账号恒定（重启、换进程都不变）；
      · 不同账号必然不同（24 hex 的碰撞概率可忽略）。
    """
    seed = hashlib.sha256(
        ("%s|%s" % (_salt(), openid or "-")).encode("utf-8")).digest()
    digest = hashlib.sha256(seed).hexdigest()    # 64 hex，切两段
    qimei = digest[:_MARK_POS] + DEVICE_MARK + digest[_MARK_POS + len(DEVICE_MARK):][:8]
    dev18 = hashlib.sha256(seed + b"|dev").digest()[:DEV_BYTES]
    return qimei, dev18


def for_account(openid: str) -> tuple[str, bytes]:
    """取这个账号该用的设备串。

    优先级：`MARVIS_QIMEI36_<openid>` 显式指定 → 派生值。
    显式值格式不对时**忽略并告警**，回落到派生值 —— 一个号配错
    不该把整条链路拖死，但要在日志里喊出来。

    ⚠ 显式值的格式校验现在包括**格式标记**：第 24-27 位必须是 `3000`。
      历史配置里如果有人贴了不带标记的 36 hex，会被拒并回落到派生值
      （否则又是一串上游一眼判非法的值）。
    """
    override = (os.environ.get(_override_key(openid)) or "").strip()
    qimei, dev18 = derive(openid)
    if not override:
        return qimei, dev18
    if len(override) != QIMEI_HEX_LEN:
        log.warning("MARVIS_QIMEI36_%s 长度必须是 %d，实际 %d —— 改用派生设备串",
                    openid[:8], QIMEI_HEX_LEN, len(override))
        return qimei, dev18
    try:
        bytes.fromhex(override)
    except ValueError:
        log.warning("MARVIS_QIMEI36_%s 不是纯 hex —— 改用派生设备串", openid[:8])
        return qimei, dev18
    if not is_well_formed(override):
        log.warning("MARVIS_QIMEI36_%s 第 %d-%d 位不是格式标记 %r —— "
                    "这样的设备串上游按非法处理，改用派生设备串",
                    openid[:8], _MARK_POS + 1, _MARK_POS + len(DEVICE_MARK),
                    DEVICE_MARK)
        return qimei, dev18
    # 设备串换了，dev18 也一起换（两者同源才像一台设备）
    dev18 = hashlib.sha256(
        (override + "|" + _salt()).encode("utf-8")).digest()[:DEV_BYTES]
    return override, dev18


def describe(openid: str) -> dict:
    """控制台 / 日志用：一眼看出这个号用的是哪台"设备"。"""
    qimei, dev18 = for_account(openid)
    return {"openid": (openid or "")[:12], "qimei36": qimei,
            "qimei_head": qimei[:12], "mark": qimei[_MARK_POS:_MARK_POS + 4],
            "well_formed": is_well_formed(qimei),
            "dev18_head": dev18.hex()[:12], "salt": _salt()}
