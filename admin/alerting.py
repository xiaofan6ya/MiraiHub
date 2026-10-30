"""风控预警通知：把哨兵的封控信号推到外部渠道。

## 为什么单独一个模块

哨兵（`admin/sentinel.py`）负责**发现**问题，本模块负责**叫醒人**。
两者职责分开的好处：
  · 哨兵在转发热路径上，必须极轻 —— 绝不能在这里做 HTTP 请求；
  · 通知要重试、要限流、要记录成败，比记录信号重得多。

## 去重与限速（关键）

同一个平台被封时，一次连坐会产生几十条信号。如果每条都发一条通知，
运维的邮箱/群里会被刷屏，真正的告警反而被淹没。因此：
  · 同一平台同一业务码在 `COOLDOWN` 秒内只通知一次（进程内台账）；
  · 一次通知汇总「受影响账号数 + 业务码分布」，而不是逐条发。

## 渠道

| 渠道 | 协议 | 用户拿到的地址 |
|---|---|---|
| 邮件 | SMTP（SSL/STARTTLS） | smtp.example.com:465 |
| 飞书 | 群机器人 Webhook | https://open.feishu.cn/open-apis/bot/v2/hook/xxx |
| QQ bot | 群机器人 Webhook | https://q.qq.com/... 或自建 go-cqhttp 的 HTTP 接口 |

三者都是「POST 一个 JSON」，但报文结构不同，所以各自一个 formatter。
"""
from __future__ import annotations

import json
import logging
import os
import re
import smtplib
import threading
import time
import urllib.error
import urllib.request
from email.header import Header
from email.mime.text import MIMEText

from admin.config import settings

logger = logging.getLogger("admin.alerting")

#: 同一「平台 + 业务码」多久内不重复通知（秒）
COOLDOWN = 600

_lock = threading.Lock()
#: {(platform, code): 上次通知时间}
_sent_at: dict[tuple[str, str], float] = {}

#: 渠道类型 -> 中文名（前端下拉直接用）
CHANNEL_LABELS = {
    "email": "邮件（SMTP）",
    "feishu": "飞书群机器人",
    "qqbot": "QQ 机器人",
}

# ---------------------------------------------------------------------------
# SMTP 配置：显式五元组 + QQ 一条式简写
# ---------------------------------------------------------------------------
#: 只有这个服务商的「授权码」能反推出服务器与端口，所以单独认它
QQ_SMTP_HOST = "smtp.qq.com"
_COMPACT_PROVIDERS = {
    "qqsmtp": "qq", "qq": "qq", "qqmail": "qq", "qq邮箱": "qq", "qq-mail": "qq",
}
#: QQ 邮箱授权码固定 16 位字母数字；用它兜底识别「只贴了一个码」的情况
_QQ_AUTH_CODE = re.compile(r"^[A-Za-z0-9]{16}$")


def parse_smtp_compact(raw: str) -> dict:
    """解析一条式 SMTP 配置。

    支持四种写法（这就是「只要配一个 qqSMTP:xxx 就能发信」的落地）：

        qqSMTP:abcdefghijklmnop         -> smtp.qq.com:465 + 授权码
        qqSMTP:me@qq.com:abcdefghijklm  -> 顺带指定发件邮箱
        me@qq.com:abcdefghijklmnop      -> 省略 provider
        abcdefghijklmnop                -> 裸 16 位授权码，自动认成 QQ

    解析不出来的写法（比如 `smtp.example.com`）返回空字典，让显式变量接管 ——
    这样老的五元组配置完全不受影响。
    """
    text = (raw or "").strip()
    if not text:
        return {}
    provider = ""
    if ":" in text:
        head, _, tail = text.partition(":")
        if head.strip().lower() in _COMPACT_PROVIDERS:
            provider = _COMPACT_PROVIDERS[head.strip().lower()]
            text = tail.strip()
    if not text:
        return {}
    user = ""
    if "@" in text and ":" in text:
        user, _, text = text.partition(":")
        user, text = user.strip(), text.strip()
    is_qq = (provider == "qq" or user.lower().endswith("@qq.com")
             or bool(_QQ_AUTH_CODE.match(text)))
    if is_qq:
        return {
            "provider": "qq",
            "host": QQ_SMTP_HOST,
            "port": 465,
            "ssl": True,
            "user": user,
            "password": text,
        }
    return {}


def _qq_mailbox_from_channels() -> str:
    """邮箱渠道里的第一个 QQ 收件地址（授权码的自寄账号）。"""
    for channel in settings.alert_channels():
        if (channel.get("type") or "").lower() != "email":
            continue
        for part in str(channel.get("target") or "").split(","):
            addr = part.strip()
            if "@qq.com" in addr.lower():
                return addr
    return ""


def _env(name: str, default: str = "") -> str:
    """直接读环境变量。

    为什么不用 `settings.X`：`Settings` 的字段是**类属性**，在 import 时
    就把 `os.getenv` 的结果固化下来了。而「预警管理」保存配置是写
    `os.environ` 让当前进程立刻生效 —— 读类属性会永远读到启动那一刻的旧值
    （实测：界面填了 127.0.0.1:2525，发信还在连 smtp.qq.com）。
    """
    value = os.getenv(name)
    return (value if value is not None else default) or ""


def smtp_settings() -> dict:
    """合并「显式配置 + 一条式简写 + 默认收件人」，给出可直接发信的一组值。

    优先级刻意是「显式 > 简写」：老配置里已经填了 host/user 的人，
    不会因为新加了一个简写变量就被悄悄改掉发件账号。
    """
    compact = parse_smtp_compact(_env("ADMIN_ALERT_SMTP"))
    explicit_host = _env("ADMIN_ALERT_SMTP_HOST").strip()
    host = (explicit_host or compact.get("host") or "").strip()
    port = int(_env("ADMIN_ALERT_SMTP_PORT", "465") or compact.get("port") or 465)
    user = (_env("ADMIN_ALERT_SMTP_USER") or compact.get("user") or "").strip()
    # 密码取值优先级要看「有没有显式服务器」：
    #   · 显式填了 host + password（自建 SMTP / 企业邮）-> 显式值优先；
    #   · 否则若有 `qqSMTP:授权码`，授权码优先 —— 否则界面上先试过一次
    #     密码、后来改成一条式简写时，旧密码会盖掉授权码，表现为「明明配了
    #     qqSMTP 却一直 535 认证失败」。
    explicit_password = _env("ADMIN_ALERT_SMTP_PASSWORD")
    if explicit_host and explicit_password:
        password = explicit_password
    else:
        password = compact.get("password") or explicit_password or ""
    sender = _env("ADMIN_ALERT_SMTP_FROM").strip()
    mail_to = _env("ADMIN_ALERT_MAIL_TO").strip()

    # 授权码属于哪个邮箱，腾讯侧无法反查 —— 只能从「收件邮箱 / 发件人 / 渠道地址」
    # 里找一个 @qq.com 当作自寄账号。找不到就让报错信息说清楚该补哪个字段。
    if not user:
        for candidate in (mail_to, sender, _qq_mailbox_from_channels()):
            if candidate and "@qq.com" in candidate.lower():
                user = candidate.split(",")[0].strip()
                break
    if not sender:
        sender = user or mail_to
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "from": sender,
        "mail_to": mail_to,
        "ssl": port == 465 or (bool(compact.get("ssl")) and not explicit_host),
        "starttls": _env("ADMIN_ALERT_SMTP_STARTTLS", "1") != "0",
        "compact": bool(compact),
    }


def active_channels() -> list[dict]:
    """真正要发的渠道清单。

    只配了「默认收件邮箱」+ QQ 授权码、没单独加 `email:` 渠道时，
    自动补一条邮件渠道 —— 否则用户按提示填完授权码还以为没生效。
    """
    channels = list(settings.alert_channels())
    mail_to = _env("ADMIN_ALERT_MAIL_TO").strip()
    if mail_to and not any((c.get("type") or "").lower() == "email" for c in channels):
        channels.append({"type": "email", "target": mail_to})
    return channels


# ---------------------------------------------------------------------------
# 渠道报文
# ---------------------------------------------------------------------------
def _feishu_payload(title: str, text: str, level: str) -> dict:
    """飞书自定义机器人。用 `interactive` 卡片比纯文本更醒目。"""
    template = "red" if level == "danger" else "orange" if level == "warn" else "blue"
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": template,
            },
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": text}}],
        },
    }


def _qqbot_payload(title: str, text: str, level: str) -> dict:
    """QQ 群机器人。

    兼容两种常见形态：官方 QQ 开放平台的 `msg_type` 报文，以及
    go-cqhttp / OneBot 的 `send_group_msg`。这里用后者（更通用），
    官方平台若需 `msg_type` 可自行在 target 里带 `?official=1`。
    """
    return {
        "action": "send_group_msg",
        "params": {
            "group_id": None,  # 由调用方在 target 里以 `group:123` 形式给出
            "message": [
                {"type": "text", "data": {"text": "%s\n%s" % (title, text)}},
            ],
        },
    }


def _format_text(event: dict) -> tuple[str, str, str]:
    """把事件整理成 (标题, 正文, 级别)。正文用 Markdown/纯文本都能读的写法。"""
    level = event.get("level") or ("danger" if event.get("tripped") else "warn")
    platform = event.get("platform_label") or event.get("platform") or "未知平台"
    if event.get("tripped"):
        title = "[风控告警] %s 疑似批次封控" % platform
    elif event.get("kind") == "resumed":
        title = "[风控恢复] %s 已恢复" % platform
    else:
        title = "[风控提醒] %s 出现封控信号" % platform

    lines = []
    if event.get("summary"):
        lines.append(event["summary"])
    accounts = event.get("accounts_affected")
    if accounts:
        lines.append("受影响账号：%s 个" % accounts)
    codes = event.get("codes") or {}
    if codes:
        lines.append("业务码分布：" + "、".join(
            "%s×%s" % (k, v) for k, v in sorted(codes.items())))
    if event.get("suggested_pause_seconds"):
        lines.append("建议操作：暂停该平台 %d 分钟止损"
                     % max(1, event["suggested_pause_seconds"] // 60))
    if event.get("detail"):
        lines.append("明细：%s" % str(event["detail"])[:300])
    lines.append("时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    return title, "\n".join(lines), level


# ---------------------------------------------------------------------------
# 发送
# ---------------------------------------------------------------------------
def _send_email(target: str, title: str, text: str) -> tuple[bool, str]:
    """SMTP 发信。`target` 是收件地址；服务器配置见 `smtp_settings()`。

    一条式简写（`ADMIN_ALERT_SMTP=qqSMTP:授权码`）也会被这里解析：
    QQ 邮箱的服务器/端口/SSL 全都能推出来，用户只需要把码贴进去。
    """
    conf = smtp_settings()
    host = conf["host"]
    port = int(conf["port"] or 465)
    user = conf["user"]
    password = conf["password"]
    sender = conf["from"]

    if not host:
        return False, ("未配置邮件服务器：填 ADMIN_ALERT_SMTP=qqSMTP:<授权码>，"
                       "或分别填 ADMIN_ALERT_SMTP_HOST / USER / PASSWORD")
    if not sender:
        return False, ("未配置发件邮箱：QQ 授权码属于哪个邮箱无法反查，"
                       "请把收件邮箱（ADMIN_ALERT_MAIL_TO）或发件人填上，"
                       "或用 qqSMTP:你的邮箱@qq.com:授权码")
    if password and not user:
        return False, "配置了授权码但缺少发件账号（user）"

    message = MIMEText(text, "plain", "utf-8")
    message["Subject"] = Header(title, "utf-8")
    message["From"] = sender
    message["To"] = target

    recipients = [t.strip() for t in str(target or "").split(",") if t.strip()]
    if not recipients:
        return False, "收件人为空"

    try:
        if port == 465 or conf["ssl"]:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.ehlo()
            if conf["starttls"]:
                server.starttls()
                server.ehlo()
        try:
            if user:
                server.login(user, password)
            server.sendmail(sender, recipients, message.as_string())
        finally:
            try:
                server.quit()
            except Exception:  # noqa: BLE001
                pass
        return True, ""
    except Exception as exc:  # noqa: BLE001 - 通知失败不能影响业务
        return False, "%s: %s" % (type(exc).__name__, exc)


def _post_json(url: str, payload: dict, timeout: float = 12.0) -> tuple[bool, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        # 飞书/QQ 都用 code/status 表达业务失败（HTTP 200 但 code != 0）
        try:
            data = json.loads(raw)
        except ValueError:
            return True, raw[:200]
        if isinstance(data, dict):
            code = data.get("code", data.get("retcode", data.get("status_code", 0)))
            if code not in (0, None, "0"):
                return False, "接口返回 code=%s %s" % (code, data.get("msg") or data.get("message") or "")
        return True, ""
    except urllib.error.HTTPError as exc:
        return False, "HTTP %s %s" % (exc.code, exc.read().decode("utf-8", "replace")[:200])
    except Exception as exc:  # noqa: BLE001
        return False, "%s: %s" % (type(exc).__name__, exc)


def _send_webhook(kind: str, target: str, title: str, text: str,
                  level: str) -> tuple[bool, str]:
    url = (target or "").strip()
    if not url.startswith(("http://", "https://")):
        return False, "Webhook 地址必须以 http(s):// 开头"
    if kind == "feishu":
        return _post_json(url, _feishu_payload(title, text, level))
    payload = _qqbot_payload(title, text, level)
    # target 里可带 `?group=123456` 指定群号（go-cqhttp 必填）
    if "?" in url:
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(url)
        group = (parse_qs(parsed.query).get("group") or [""])[0]
        url = "%s://%s%s" % (parsed.scheme, parsed.netloc, parsed.path)
        if group:
            payload["params"]["group_id"] = group
    if payload["params"].get("group_id") is None:
        return False, "QQ 机器人需要在地址后加 ?group=群号"
    return _post_json(url, payload)


def deliver(event: dict) -> dict:
    """把一条事件推给所有启用的渠道。返回每路的成败。

    **不做去重判断**（去重在 `notify` 里），方便「测试发送」直接调用。
    """
    title, text, level = _format_text(event)
    results = []
    for channel in active_channels():
        kind = channel.get("type") or ""
        target = channel.get("target") or ""
        if not target:
            continue
        if kind == "email":
            ok, error = _send_email(target, title, text)
        elif kind in ("feishu", "qqbot"):
            ok, error = _send_webhook(kind, target, title, text, level)
        else:
            ok, error = False, "未知渠道类型：%s" % kind
        results.append({"type": kind, "target": target, "ok": ok, "error": error})
        if not ok:
            logger.warning("预警发送失败 %s -> %s: %s", kind, target, error)
    return {"title": title, "text": text, "level": level, "results": results}


def notify(event: dict) -> dict | None:
    """带去重的通知。同一 (平台, 主业务码) 在 `COOLDOWN` 内只发一次。

    返回 `None` 表示被去重跳过 —— 调用方据此知道「没发」而不是「发了但失败」。
    """
    if not settings.alert_enabled():
        return None
    platform = str(event.get("platform") or "")
    codes = event.get("codes") or {}
    # 主业务码：取出现最多的那个，用于去重键
    primary = max(codes, key=codes.get) if codes else str(event.get("kind") or "")
    key = (platform, primary)
    now = time.time()
    with _lock:
        last = _sent_at.get(key, 0.0)
        if now - last < COOLDOWN:
            return None
        _sent_at[key] = now
    return deliver(event)


def reset_cooldown() -> None:
    """清空去重台账（测试 / 手动重发用）。"""
    with _lock:
        _sent_at.clear()


def channels_summary() -> list[dict]:
    """给控制台用的渠道列表（**不返回**密码等敏感值）。"""
    out = []
    for channel in active_channels():
        kind = channel.get("type") or ""
        out.append({
            "type": kind,
            "label": CHANNEL_LABELS.get(kind, kind),
            "target": channel.get("target") or "",
            "enabled": bool(channel.get("target")),
        })
    return out


def config_summary() -> dict:
    """邮件配置的健康度（给界面显示「还差哪一步」）。

    用户最容易卡住的地方不是填授权码，而是**不知道缺收件邮箱**：
    QQ 的授权码反查不到账号，所以发件账号必须来自别处。
    """
    conf = smtp_settings()
    missing = []
    if not conf["host"]:
        missing.append("邮件服务器（或 ADMIN_ALERT_SMTP=qqSMTP:授权码）")
    if not conf["user"]:
        missing.append("发件邮箱（QQ 授权码所属邮箱，无法自动反查）")
    if conf["password"] == "":
        missing.append("SMTP 授权码 / 密码")
    if not (conf["mail_to"] or any(
            (c.get("type") or "").lower() == "email" for c in settings.alert_channels())):
        missing.append("收件邮箱（默认收件人）")
    return {
        "host": conf["host"],
        "port": conf["port"],
        "user": conf["user"],
        "from": conf["from"],
        "mail_to": conf["mail_to"],
        "ssl": conf["ssl"],
        "compact": conf["compact"],
        "has_password": bool(conf["password"]),
        "ready": not missing,
        "missing": missing,
    }