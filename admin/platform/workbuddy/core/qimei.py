"""WorkBuddy 设备号（qimei36）—— **一个账号一台设备，永不复用**。

## 为什么需要这个模块（2026-11-27 复核客户端 5.11.3）

客户端已经把腾讯 QIMEI 接进来了：

  * 包内 `@tencent/qimei-node@1.2.2` + `resources/qimei.dll`（win32 appKey
    `0WIN070JLSH64IEE`，见 `cli/product.json` 的 `config.qimei36AppKey`）；
    主进程用 `main/qimei-helper.js` 起一个 helper 子进程取 qimei36，
    成功后缓存到 `~/.workbuddy/qimei-cache.json`（`{"qimei36": "<36 hex>"}`）。
  * 报给上游的头只有一个：`X-Device-Id-List: {"qimei36":"<36 hex>"}`，
    由 `WorkbuddyAuthStateHeaderProvider.provide()` 组装，同一处还带
    `X-Custom-Channel-Id: unknown` 与 `X-Device-Token`（Turing Shield）。
  * 真机值的第 24-27 位恒为格式标记 `3000`：本机实测
    `db4215ebb2ed553ee359779430001461950c`，Marvis 的生成器也是同一口径。

## 为什么要按账号派生

设备号是**设备级**标识，一台机器一串。号池如果把几十个账号塞到同一串上，
上游只要做一次关联就能把整池串成一窝 —— 一个号出事，其余全被连坐。
所以这里按账号派生：**一个账号一串**，跨重启稳定（设备号频繁变化比固定更
容易被标记），且算法生成的值上游照收（与 Marvis 同一套口径）。

## 为什么不复用 Marvis 的 qimei 池

Marvis 的池子是「几台设备装几个号」（有 owners 上限、要落盘、可轮换），因为
它的签名 TLV 只认 uskey 发出来的真值。WorkBuddy 这边只是把设备号上报，
一账号一串固定值就够 —— 不引入状态文件、不加锁、不占容量、不会撞上限。

## 怎么关

`ADMIN_WB_QIMEI=0`（或 `off` / `false` / `no`）。关掉之后 `device_headers()`
返回空 dict，调用方照常发请求，只是不带设备号。
"""
from __future__ import annotations

import hashlib
import json
import os

#: 真机 qimei36 的格式标记与位置（0-based）。本机客户端缓存值
#: `db4215ebb2ed553ee359779430001461950c` 的第 24-27 位正是 `3000`。
MARK = "3000"
MARK_POS = 24
#: 36 hex = 18 字节，与真机一致。
LENGTH = 36

#: 派生命名空间：换它等于换一整批设备号，**不要**随手改。
_NAMESPACE = b"workbuddy-qimei-v1\0"

#: 视为「关闭」的取值。
_DISABLED = ("0", "off", "false", "no", "disable", "disabled")


def enabled() -> bool:
    """设备号开关。默认开：缺它就是「一台不带设备号的桌面端」，同样可疑。"""
    return os.getenv("ADMIN_WB_QIMEI", "1").strip().lower() not in _DISABLED


def is_valid(value) -> bool:
    """是否是合法 qimei36：36 个 hex，且第 24-27 位是格式标记 `3000`。"""
    if not isinstance(value, str) or len(value) != LENGTH:
        return False
    text = value.strip().lower()
    if len(text) != LENGTH or any(ch not in "0123456789abcdef" for ch in text):
        return False
    return text[MARK_POS:MARK_POS + len(MARK)] == MARK


def derive(seed: str) -> str:
    """由稳定种子派生一串 qimei36（`24 hex + 3000 + 8 hex`）。

    确定性：同一 seed 永远得到同一串 —— 账号在服务端眼里始终是**同一台设备**。
    空 seed 返回空串：宁可不发，也不要退回一个共用值。
    """
    text = str(seed or "").strip()
    if not text:
        return ""
    digest = hashlib.sha256(_NAMESPACE + text.encode("utf-8")).hexdigest()
    return digest[:MARK_POS] + MARK + digest[MARK_POS:MARK_POS + 8]


def for_account(uid: str = "", phone: str = "") -> str:
    """一个账号一串：uid 优先，其次手机号；两者都没有就返回空。"""
    return derive(uid) or derive(phone) or ""


def override_of(auth) -> str:
    """凭证里显式带的 qimei36（真机导入的号以它为准）。"""
    if not isinstance(auth, dict):
        return ""
    for key in ("qimei36", "qimei"):
        value = auth.get(key)
        if is_valid(value):
            return value.strip().lower()
    return ""


def from_credential(auth_json: str) -> str:
    """从整份凭证 JSON 里读显式 qimei36（加密凭证解不开就当没有）。"""
    if not auth_json or not isinstance(auth_json, str):
        return ""
    try:
        data = json.loads(auth_json)
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    found = override_of(data.get("auth"))
    if found:
        return found
    return override_of(data)


def resolve(uid: str = "", phone: str = "", auth=None) -> str:
    """最终生效的 qimei36：显式值 > uid 派生 > 手机号派生。"""
    if not enabled():
        return ""
    return override_of(auth) or for_account(uid, phone)


def mask(value) -> str:
    """日志用：只露头尾，别把设备号整串写进日志。"""
    text = str(value or "")
    return f"{text[:6]}…{text[-4:]}" if len(text) > 12 else "-"


def device_headers(uid: str = "", phone: str = "", auth=None) -> dict:
    """设备号请求头，与客户端 `WorkbuddyAuthStateHeaderProvider` 同形状。

    客户端发的是 `JSON.stringify({qimei36})`，即 `{"qimei36":"..."}`（无空格），
    这里用 `separators` 对齐，避免多一个可判的格式差异。
    """
    value = resolve(uid, phone, auth)
    if not value:
        return {}
    return {
        "X-Device-Id-List": json.dumps({"qimei36": value},
                                       ensure_ascii=False, separators=(",", ":")),
        "X-Custom-Channel-Id": "unknown",
    }
