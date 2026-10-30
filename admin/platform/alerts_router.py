"""预警管理接口：`/api/alerts/*`。

    GET  /api/alerts/config     当前配置（**不含密码**）
    PUT  /api/alerts/config     保存渠道与 SMTP 设置（写进 .env 的运行时覆盖）
    POST /api/alerts/test       发一条测试通知，立刻验证配置对不对

## 为什么配置存在两处

`settings` 读环境变量，进程启动后不会变。但「预警管理」是界面化的运维功能，
用户改完要**立刻生效**，不可能重启服务。因此：
  · 真值仍以环境变量为准（重启后不丢）；
  · 界面保存时写进 `system_settings`，并同步覆盖 `os.environ`，
    让当前进程内的 `settings.alert_channels()` 立刻读到新值。
"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from admin import alerting
from admin.db import get_db
from admin.models import SystemSetting
from admin.security import require_admin

#: 路由装饰器里写完整路径（`/api/alerts/...`），所以这里不设 prefix。
#: 之前写了 `prefix="/api/alerts"` 且装饰器又写全路径，实际路径变成
#: `/api/alerts/api/alerts/config` —— 访问原路径就是 404。
router = APIRouter(tags=["alerts"])

CONFIG_KEY = "alerts:config"

#: 允许通过界面修改的键 -> 对应的环境变量名
FIELD_ENV = {
    "channels": "ADMIN_ALERT_CHANNELS",
    "enabled": "ADMIN_ALERT_ENABLED",
    "cooldown": "ADMIN_ALERT_COOLDOWN",
    "smtp_host": "ADMIN_ALERT_SMTP_HOST",
    "smtp_port": "ADMIN_ALERT_SMTP_PORT",
    "smtp_user": "ADMIN_ALERT_SMTP_USER",
    "smtp_password": "ADMIN_ALERT_SMTP_PASSWORD",
    "smtp_from": "ADMIN_ALERT_SMTP_FROM",
    "smtp_starttls": "ADMIN_ALERT_SMTP_STARTTLS",
    #: 一条式简写：`qqSMTP:授权码`（QQ 邮箱默认就填好这个）
    "smtp": "ADMIN_ALERT_SMTP",
    #: 默认收件邮箱：QQ 授权码属于哪个邮箱反查不到，自寄账号从这里取
    "mail_to": "ADMIN_ALERT_MAIL_TO",
}


class ChannelIn(BaseModel):
    type: str = ""
    target: str = ""


class ConfigIn(BaseModel):
    enabled: bool = True
    cooldown: int = 600
    channels: list[ChannelIn] = []
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    #: 留空表示「不修改」，避免前端把掩码串回写覆盖真密码
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    #: `qqSMTP:授权码` / `qqSMTP:邮箱@qq.com:授权码`
    smtp: str = ""
    mail_to: str = ""


def _load(db: Session) -> dict:
    row = db.query(SystemSetting).filter(SystemSetting.key == CONFIG_KEY).first()
    if not row or not row.value:
        return {}
    try:
        data = json.loads(row.value)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _apply_env(data: dict) -> None:
    """把配置写进当前进程的环境变量，让 `settings.*` 立刻生效。"""
    channels = data.get("channels") or []
    encoded = ";".join(
        "%s:%s" % (c.get("type"), c.get("target"))
        for c in channels if c.get("type") and c.get("target"))
    os.environ[FIELD_ENV["channels"]] = encoded
    os.environ[FIELD_ENV["enabled"]] = "1" if data.get("enabled", True) else "0"
    os.environ[FIELD_ENV["cooldown"]] = str(int(data.get("cooldown") or 600))
    for key in ("smtp_host", "smtp_user", "smtp_from"):
        os.environ[FIELD_ENV[key]] = str(data.get(key) or "")
    os.environ[FIELD_ENV["smtp_port"]] = str(int(data.get("smtp_port") or 465))
    os.environ[FIELD_ENV["smtp_starttls"]] = "1" if data.get("smtp_starttls", True) else "0"
    # 一条式简写：`qqSMTP:授权码`。空串也要写（表示清空，回落显式五元组）
    os.environ[FIELD_ENV["smtp"]] = str(data.get("smtp") or "")
    os.environ[FIELD_ENV["mail_to"]] = str(data.get("mail_to") or "")
    # 密码：只有调用方明确给了新值才覆盖（空串 = 保持不变）
    if data.get("smtp_password"):
        os.environ[FIELD_ENV["smtp_password"]] = str(data["smtp_password"])


def _mask(data: dict) -> dict:
    out = dict(data)
    if out.get("smtp_password"):
        out["smtp_password"] = "********"
        out["has_password"] = True
    else:
        out["has_password"] = bool(os.getenv("ADMIN_ALERT_SMTP_PASSWORD"))
        if out["has_password"]:
            out["smtp_password"] = "********"
    # 授权码同理：只暴露「有没有」，不回显掩码后的值给输入框当内容
    if out.get("smtp"):
        out["smtp"] = "********"
        out["has_smtp"] = True
    else:
        out["has_smtp"] = bool(os.getenv("ADMIN_ALERT_SMTP"))
    return out


def bootstrap() -> None:
    """服务启动时把库里保存的配置灌进 os.environ。

    否则重启后界面里看到的配置还在，但 `settings` 读不到 —— 表现是
    「配置没丢，但通知不发」。
    """
    from admin.db import SessionLocal
    try:
        with SessionLocal() as db:
            data = _load(db)
        if data:
            _apply_env(data)
    except Exception:  # noqa: BLE001 - 启动bootstrap失败不能挡住服务
        pass


@router.get("/api/alerts/config")
def get_config(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    data = _load(db)
    return {
        "config": _mask(data),
        "channels": alerting.channels_summary(),
        "labels": alerting.CHANNEL_LABELS,
        "enabled": bool(data.get("enabled", True)),
        # 邮件链路的真实状态：缺什么一目了然（QQ 授权码反查不到发件邮箱，
        # 这是用户最容易卡住的一步）
        "smtp": alerting.config_summary(),
    }


@router.put("/api/alerts/config")
def put_config(body: ConfigIn, _: bool = Depends(require_admin),
               db: Session = Depends(get_db)):
    data = {
        "enabled": bool(body.enabled),
        "cooldown": max(30, int(body.cooldown or 600)),
        "channels": [{"type": c.type, "target": c.target}
                     for c in body.channels if c.type and c.target],
        "smtp_host": body.smtp_host.strip(),
        "smtp_port": int(body.smtp_port or 465),
        "smtp_user": body.smtp_user.strip(),
        "smtp_from": body.smtp_from.strip(),
        "smtp_starttls": bool(body.smtp_starttls),
        "mail_to": body.mail_to.strip(),
    }
    # 一条式简写：`********` 是掩码回显，不能当真值写回去；
    # 空串表示「本次没改」，`__clear__` 才是「删掉已存的授权码」。
    if body.smtp == "__clear__":
        data["smtp"] = ""
    elif body.smtp and body.smtp != "********":
        data["smtp"] = body.smtp.strip()
    elif os.getenv("ADMIN_ALERT_SMTP"):
        # 前端没改（掩码/空）时保留原值，避免「保存一次就把授权码抹掉」
        data["smtp"] = os.environ["ADMIN_ALERT_SMTP"]
    # 空密码 = 不修改；掩码串也不能当真密码写回去
    if body.smtp_password and body.smtp_password != "********":
        data["smtp_password"] = body.smtp_password

    row = db.query(SystemSetting).filter(SystemSetting.key == CONFIG_KEY).first()
    payload = json.dumps(data, ensure_ascii=False)
    if row is None:
        db.add(SystemSetting(key=CONFIG_KEY, value=payload))
    else:
        row.value = payload
    db.commit()

    _apply_env(data)
    alerting.reset_cooldown()
    return {"ok": True, "channels": alerting.channels_summary()}


@router.post("/api/alerts/test")
def send_test(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """立刻发一条测试通知。把每路的成败原样返回，便于定位配置问题。"""
    data = _load(db)
    if data:
        _apply_env(data)
    event = {
        "platform": "",
        "platform_label": "测试",
        "kind": "test",
        "tripped": False,
        "level": "info",
        "summary": "这是一条来自 MiraiHub 的测试通知。收到说明预警配置可用。",
        "accounts_affected": 0,
        "codes": {},
    }
    result = alerting.deliver(event)
    ok_all = all(r["ok"] for r in result["results"]) and bool(result["results"])
    return {"ok": ok_all, "smtp": alerting.config_summary(), **result}