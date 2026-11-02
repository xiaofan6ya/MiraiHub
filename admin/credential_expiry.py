"""凭证有效期的**平台无关**入口。

## 这层为什么存在

`auth_json` 里 accessToken / refreshToken 的字段名、到期时间的表达方式
（毫秒 vs 秒、绝对时间 vs 相对时长）、甚至「过期能不能刷新救回来」，
**每个平台都不一样**。把这些规则写在 admin 顶层，就等于让 admin 认识
每个平台的凭证结构 —— 那正是 `test_gateway_contract` 的「admin 平台无关性」
约束要挡掉的东西（它会把 `admin/*.py` 里出现平台名当成违规）。

所以真正的解析在平台模块里（当前是
`admin/platform/<平台>/core/credential_expiry.py`），本模块只做两件事：

  1. 按账号找到对应平台的 Gateway；
  2. 调 `credential_expiry(auth_json)`，拿不到就给一个「不支持」的中性结果。

## 为什么失败要返回中性结构而不是抛异常

调用方是**账号列表接口**。列表里只要有一个号的凭证解不开，
整页就该 500 吗？不该 —— 那会让一个坏号把整个账号池面板打挂。
解析不出来时如实回 `unknown` / `supported=False`，前端据此不渲染徽标。
"""
from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

#: 通用三态标签（各平台实现应复用这套词汇，便于前端统一渲染）。
STATE_OK = "ok"
STATE_EXPIRING = "expiring"
STATE_EXPIRED = "expired"
STATE_UNKNOWN = "unknown"


def _neutral(supported: bool = False) -> dict:
    return {
        "supported": supported,
        "expires_at": None, "days_left": None, "state": STATE_UNKNOWN,
        "refresh_expires_at": None, "refresh_days_left": None,
        "refresh_state": STATE_UNKNOWN,
        "renewal_known": False,
        "renewable": False,
        "has_refresh": False,
    }


def _gateway_for(account):
    """账号 -> 平台 Gateway；取不到返回 None。"""
    try:
        from admin.platform import registry

        spec = registry.spec_of_account(account)
        if spec is None:
            return None
        return registry.load_platform_gateway(spec, spec.default_realm())
    except Exception:      # noqa: BLE001 - 平台掉了不该让列表挂
        return None


def credential_expiry(account) -> dict:
    """账号凭证的两条时间线。

    返回结构见 `_neutral()`，字段语义：

    * `supported`           —— 该平台有没有实现这套解析
    * `expires_at/state`    —— 短期令牌（过期通常可刷新救回）
    * `refresh_*`           —— 续期窗口，**这才是死亡线**
    * `renewable`           —— 还能不能靠刷新救回来
    """
    gateway = _gateway_for(account)
    if gateway is None:
        return _neutral()
    resolver = getattr(gateway, "credential_expiry", None)
    if resolver is None:
        return _neutral()
    try:
        info = resolver(getattr(account, "auth_json", "") or "")
    except Exception as exc:      # noqa: BLE001
        _logger.debug("credential_expiry 失败 account=%s: %s",
                      getattr(account, "id", "?"), exc)
        return _neutral(supported=True)
    if not isinstance(info, dict):
        return _neutral(supported=True)

    result = _neutral(supported=True)
    result.update({k: v for k, v in info.items() if k in result})
    return result


def renews_guard_days() -> int:
    """续期窗口的「告急」阈值（天）。

    各平台可以有自己的值（`Gateway.renewal_guard_days()`）；
    这里取**所有平台里最保守的那个**，给调度器做兜底扫描的门槛用 ——
    宁可早提醒，不可晚。没有平台报值时给 14 天：在 60 天窗口下
    等于留出十几次补救机会。
    """
    days = []
    try:
        from admin.platform import registry

        for pid in registry.ids():
            spec = registry.get(pid)
            if spec is None:
                continue
            gateway = registry.load_platform_gateway(spec, spec.default_realm())
            value = getattr(gateway, "renewal_guard_days", None)
            if callable(value):
                try:
                    got = int(value())
                except Exception:      # noqa: BLE001
                    continue
                if got > 0:
                    days.append(got)
    except Exception:      # noqa: BLE001
        pass
    return min(days) if days else 14
