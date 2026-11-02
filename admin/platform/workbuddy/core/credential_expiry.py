"""令牌有效期（token expiry）解析 —— access 与 refresh 两条时间线。

实证结论（2026-11-02 实测，见文末「实测记录」）
---------------------------------------------
WorkBuddy 的凭证是**两条**独立的时间线，把两者混为一谈会得出完全错误的结论：

  accessToken   30 天（`expiresIn` = 2592000 秒），Keycloak 签发的 JWT，
                服务端无状态、无法提前吊销，只能等 `exp` 到点
  refreshToken  60 天（`refreshExpiresIn` = 5183999 秒 ≈ 60 天），
                每次刷新成功都**整体后移**

关键点：**accessToken 过期不是终态**。`/v2/plugin/auth/token/refresh`
只需要一个 `X-Refresh-Token` 请求头（既不需要 client_secret，也不需要
合法的 Authorization），实测在 accessToken 已过期 / 签名被伪造 / 完全缺失
三种情况下**都能换回新令牌**。

所以：
  · access 的 30 天是「剩多久该刷」的提示，不是死亡线
  · refresh 的 60 天才是真正的死亡线 —— 只要在这 60 天里成功刷新过一次，
    两条时间线就一起续上，账号**可以永久活着**
  · 真正会掉号的场景只有一个：保活连续 60 天一次都没成功

本模块只做一件事：**从 `Account.auth_json` 里把两条时间线都解析出来**，
换算成剩余天数与三态标签，供列表接口、健康检查、保活调度使用。

为什么放在平台目录里而不是 `admin/token_expiry.py`
--------------------------------------------------
`.info` 的 `auth.accessToken` / `refreshToken` / `refreshExpiresIn` 这一套是
**本平台客户端**的凭证结构，别的平台形状完全不同（顶层 `token`、`expiredAt`
等，续期逻辑也各在自己的模块里）。放在 admin 顶层会让 admin 被迫理解本平台
的内部细节，也过不了 `test_gateway_contract` 的「admin 平台无关性」约束。
admin 侧通过 `Gateway.credential_expiry()` 拿结果，不知道解析怎么做的。

为什么不做成 DB 字段
--------------------
凭证会在 `_store_locked()` 里被覆盖更新（重登 / 同步 / 刷新回写），如果额外
维护 `token_expires_at` 列，就必须保证每个写入点都同步刷新它 —— 漏一处就会
出现「库里说还没过期、实际早过期」的脏数据（保活本身就有 4 个回写点）。
JWT 的 `exp` 与 `refreshExpiresAt` 本来就写在凭证里，**读时解析**永远与凭证
保持一致。

成本：未加密凭证是一次 `json.loads`，加密凭证是一次 AES-GCM 解密，
单账号微秒~毫秒级；一次列表请求调用几十次可以忽略。
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

#: accessToken 剩余天数低于该阈值即视为「即将过期」，前端标黄。
#: 30 天周期里提前 7 天提示，够安排一次保活或手动刷新。
EXPIRING_DAYS = 7

#: refreshToken 续期窗口剩余天数低于该阈值即视为「续期告急」。
#: 窗口本身是 60 天，保活每天跑一次，留 14 天 = 14 次补救机会。
#: 这个阈值同时被保活调度用作「不等计划时刻、立刻补刷」的触发器，
#: 见 `admin/scheduler.py::_renewal_urgent_ids`。
RENEWAL_GUARD_DAYS = 14

#: 三态标签
STATE_OK = "ok"
STATE_EXPIRING = "expiring"
STATE_EXPIRED = "expired"
STATE_UNKNOWN = "unknown"


def _b64url_decode(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def _as_ms(value) -> int:
    """时间戳归一化到毫秒。

    上游各平台对 `expiresAt` 的单位并不统一（Qoder 国内版给毫秒、国际版给秒、
    Trae 混用 ISO 字符串）。这里只处理 WorkBuddy 的两种形态，且对秒/毫秒
    做一次量级判断，避免把 1796194123318 当成秒再乘一次。
    """
    try:
        num = float(value or 0)
    except (TypeError, ValueError):
        return 0
    if num <= 0:
        return 0
    if num < 100_000_000_000:       # < 1e11 → 秒级时间戳
        num *= 1000
    return int(num)


def _find_access_token(obj) -> str:
    """在凭证结构里找 accessToken。

    兼容两种形态：
      * 客户端 .info 原文：``{"auth": {"accessToken": "..."}}``
      * 平台直存：``{"accessToken": "..."}``
    """
    if not isinstance(obj, dict):
        return ""
    auth = obj.get("auth")
    if isinstance(auth, dict):
        tok = auth.get("accessToken") or auth.get("access_token")
        if isinstance(tok, str) and tok:
            return tok
    tok = obj.get("accessToken") or obj.get("access_token")
    return tok if isinstance(tok, str) else ""


def _parse_payload(auth_json: str) -> dict | None:
    """把 auth_json 解成凭证 dict（含解密），失败返回 None。"""
    if not auth_json or not isinstance(auth_json, str):
        return None
    try:
        from admin.platform.workbuddy.core.credential_codec import normalize_credential
        raw = normalize_credential(auth_json)
    except Exception:            # 解密能力缺失 / 结构损坏
        return None
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None

def _auth_of(obj: dict) -> dict:
    auth = obj.get("auth")
    return auth if isinstance(auth, dict) else obj


def _refresh_deadline(auth: dict) -> int:
    """refreshToken 的到期时刻（毫秒），0 表示推不出来。

    三种来源按可信度排序：
      1. `refreshExpiresAt` —— 上游直接给的绝对时间（最准）
      2. `lastRefreshTime + refreshExpiresIn * 1000` —— 上游给了相对时长
      3. JWT 的 `refreshToken.exp` —— 只在前两者都缺时兜底
    """
    deadline = _as_ms(auth.get("refreshExpiresAt"))
    if deadline:
        return deadline
    issued = _as_ms(auth.get("lastRefreshTime"))
    try:
        span = int(float(auth.get("refreshExpiresIn") or 0))
    except (TypeError, ValueError):
        span = 0
    if issued and span:
        return issued + span * 1000
    token = auth.get("refreshToken")
    if isinstance(token, str) and token.count(".") == 2:
        try:
            payload = json.loads(_b64url_decode(token.split(".")[1]))
            exp = payload.get("exp")
            if isinstance(exp, (int, float)) and exp > 0:
                return int(float(exp) * 1000)
        except Exception:
            pass
    return 0


def _state_of(delta_seconds: float, days_left: int, threshold: int) -> str:
    if delta_seconds <= 0:
        return STATE_EXPIRED
    return STATE_EXPIRING if days_left < threshold else STATE_OK


def _unknown_all() -> dict:
    return {
        "expires_at": None, "days_left": None, "state": STATE_UNKNOWN,
        "refresh_expires_at": None, "refresh_days_left": None,
        "refresh_state": STATE_UNKNOWN,
        # 「还能不能靠刷新救回来」：有 refreshToken 且窗口没过。
        # 这个标志比 state 有用得多 —— access 过期但它是 True 的话，
        # 一次保活就复活，完全不需要人工重登。
        "renewable": False,
        # 窗口到期时间是否**真的推出来了**。未知时 `renewable=False` 只表示
        # 「不知道」，不表示「已经废了」—— 前端要能分开显示，
        # 否则会把「格式认不出的坏号」和「窗口已过期的死号」混成一句。
        "renewal_known": False,
        "has_refresh": False,
    }


def token_expiry(auth_json: str) -> dict:
    """解析出两条时间线的到期信息。

    返回::

        {
          # —— accessToken（30 天）——
          "expires_at":         datetime | None,   # UTC
          "days_left":          int | None,        # 向下取整，可为负
          "state":              "ok" | "expiring" | "expired" | "unknown",
          # —— refreshToken（60 天滑动窗口）——
          "refresh_expires_at": datetime | None,
          "refresh_days_left":  int | None,
          "refresh_state":      "ok" | "expiring" | "expired" | "unknown",
          # —— 综合 ——
          "renewable":          bool,   # 有 refreshToken 且窗口未过
          "has_refresh":        bool,
        }

    **绝不抛异常**：凭证格式异常、解密密钥缺失、JWT 结构不符，一律降级为
    ``unknown``。列表接口不能因为一个坏号整页 500。
    """
    obj = _parse_payload(auth_json)
    if obj is None:
        return _unknown_all()
    auth = _auth_of(obj)

    now = datetime.now(timezone.utc)
    result = _unknown_all()

    # ---- 线一：accessToken ----
    tok = _find_access_token(obj)
    if tok and tok.count(".") == 2:
        try:
            payload = json.loads(_b64url_decode(tok.split(".")[1]))
            exp = payload.get("exp")
            if isinstance(exp, (int, float)) and exp > 0:
                expires_at = datetime.fromtimestamp(float(exp), tz=timezone.utc)
                delta = (expires_at - now).total_seconds()
                days_left = int(delta // 86400)
                result.update({
                    "expires_at": expires_at,
                    "days_left": days_left,
                    "state": _state_of(delta, days_left, EXPIRING_DAYS),
                })
        except Exception:
            pass

    # ---- 线二：refreshToken ----
    refresh_token = auth.get("refreshToken") or auth.get("refresh_token")
    result["has_refresh"] = isinstance(refresh_token, str) and bool(refresh_token)
    deadline = _refresh_deadline(auth)
    if deadline:
        refresh_at = datetime.fromtimestamp(deadline / 1000, tz=timezone.utc)
        rdelta = (refresh_at - now).total_seconds()
        rdays = int(rdelta // 86400)
        result.update({
            "refresh_expires_at": refresh_at,
            "refresh_days_left": rdays,
            "refresh_state": _state_of(rdelta, rdays, RENEWAL_GUARD_DAYS),
            "renewal_known": True,
        })
    result["renewable"] = bool(result["has_refresh"]
                               and result["refresh_state"] != STATE_EXPIRED)
    return result


def summary_line(auth_json: str, name: str = "") -> str:
    """排障用的一行摘要，不参与业务判断。"""
    info = token_expiry(auth_json)
    label = name or "-"
    if info["state"] == STATE_UNKNOWN and info["refresh_state"] == STATE_UNKNOWN:
        return f"{label}: 两条时间线都无法解析（凭证格式异常或本机缺少解密密钥）"
    access = ("access 到期 " + info["expires_at"].strftime("%Y-%m-%d %H:%M UTC")
              + f"，剩 {info['days_left']} 天") if info["expires_at"] else "access 未知"
    refresh = ("refresh 窗口至 " + info["refresh_expires_at"].strftime("%Y-%m-%d %H:%M UTC")
               + f"，剩 {info['refresh_days_left']} 天") if info["refresh_expires_at"] else "refresh 未知"
    tail = "可自动续期" if info["renewable"] else "已不可续期，需重新登录"
    return f"{label}: {access}；{refresh}；{tail}"


# ---------------------------------------------------------------------------
# 实测记录（2026-11-02，账号 #156 / #157，本地测试环境）
# ---------------------------------------------------------------------------
#
# 1) 刷新端点只要一个头就够，**不需要 client_secret**：
#      POST https://www.workbuddy.cn/v2/plugin/auth/token/refresh
#      X-Refresh-Token: <refreshToken>
#      X-Auth-Refresh-Source: plugin
#    不带 Authorization 也回 200 / code=0 / 带新 accessToken。
#    → 这直接证伪了「console 是 confidential client，换令牌必须 client_secret」
#      的旧结论。那个 secret 是用来走 Keycloak 标准 token 端点的，
#      而插件用自己的 `/v2/plugin/auth/token/refresh`，鉴权凭据就是 refreshToken 本身。
#
# 2) accessToken 死不死都不影响刷新：
#    原样 / 已过期+签名伪造 / 完全缺 Authorization —— 三种都是 200 + 新令牌。
#    → 「30 天到了必须重新登录」不成立。
#
# 3) refreshToken 每次刷新都换新值（轮换），但**旧值重放仍然成功**。
#    → 宽容轮换：写回失败不会立刻把号写死，下次拿旧值还能刷。
#
# 4) `refreshExpiresIn` 恒为 5183999（≈60 天），刷新后 `refreshExpiresAt`
#    整体后移 60 天。
#    → 滑动窗口：只要 60 天内成功刷过一次，账号永续。
