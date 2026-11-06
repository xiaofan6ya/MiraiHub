"""代理凭证的加密存储 + 日志脱敏。

## 为什么必须加密（2026-11-06 用户强调）

> 这个安全性一定要做好，这个很危险。

代理 URL 里通常带账密：`http://user:pass@host:port`。它落库就是**明文
凭据**，而这张表会被备份、会被导出、会被截图、会被 `/api/proxies` 读。
一条泄露就等于整批出口被白嫖，而且这些出口往往绑定着付费的住宅/隧道资源。

所以：

1. **落库加密**：`proxy_endpoints.url` 存 `enc:v1:<base64>`，
   key 由 `ADMIN_JWT_SECRET` 派生（换 secret = 旧密文失效，符合预期）。
2. **API 永远打码**：面板只看到 `http://user:***@host:port`，
   要看原文只能重新填一遍（不提供「查看密码」）。
3. **日志脱敏**：`last_error` / 异常文本里出现的 `user:pass@` 一律替换成
   `***`，避免排障时把凭据打进日志文件。
4. **环境变量引用**：URL 写成 `env:PROXY_MAIN` 时直接从环境变量读，
   **完全不落库** —— 最敏感的场景连密文都不要有。

## 迁移兼容

`decrypt()` 遇到不带 `enc:v1:` 前缀的值原样返回，所以老数据（明文）
不会因为升级而失效；写入时会自动升级成密文。
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re

_logger = logging.getLogger("admin.secret_box")

#: 密文前缀。改这个等于让所有旧密文失效，不要动。
MAGIC = "enc:v1:"

#: 环境变量引用的前缀：`env:NAME`
ENV_PREFIX = "env:"

_AAD = b"proxy-endpoint-url-v1"


def _key() -> bytes:
    """从服务密钥派生 32 字节 AES key。

    刻意不用随机 salt：这是**配置解密**不是用户密码，同一份部署必须能
    解同一份密文。换 `ADMIN_JWT_SECRET` 会让旧密文解不开 —— 这也是
    预期行为（轮换密钥 = 强制重填代理）。
    """
    from admin.config import settings

    secret = str(getattr(settings, "JWT_SECRET", "") or "dev-insecure")
    return hashlib.sha256(("proxy-box|" + secret).encode()).digest()


def encrypt(plaintext: str) -> str:
    """明文 URL → `enc:v1:<b64(iv|tag|ciphertext)>`。

    空值、已加密的值原样返回（幂等）。
    """
    text = str(plaintext or "")
    if not text or text.startswith(MAGIC):
        return text
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        iv = os.urandom(12)
        blob = AESGCM(_key()).encrypt(iv, text.encode(), _AAD)
        return MAGIC + base64.b64encode(iv + blob).decode()
    except Exception as exc:  # noqa: BLE001 - 加密不可用时**不能静默存明文**
        _logger.error("代理 URL 加密失败，拒绝以明文落库：%s", exc)
        raise


def decrypt(value: str) -> str:
    """`enc:v1:` 密文 → 明文；明文原样返回（迁移兼容）。"""
    text = str(value or "")
    if not text.startswith(MAGIC):
        return text
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        blob = base64.b64decode(text[len(MAGIC):])
        iv, body = blob[:12], blob[12:]
        return AESGCM(_key()).decrypt(iv, body, _AAD).decode()
    except Exception as exc:  # noqa: BLE001
        _logger.warning("代理 URL 解密失败（密钥轮换或数据损坏）：%s", exc)
        return ""


def resolve(value: str) -> str:
    """取**可直接使用**的 URL：`env:NAME` → 环境变量，密文 → 解密。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(ENV_PREFIX):
        name = text[len(ENV_PREFIX):].strip()
        return str(os.getenv(name, "") or "").strip()
    return decrypt(text)


#: `scheme://user:pass@host` 里的 `user:pass` 段
_CRED_PATTERN = re.compile(r"(://[^:/@\s]+:)[^@\s]*(@)")
#: 裸的 `user:pass@host:port`（没有 scheme 时也会出现）
_BARE_CRED_PATTERN = re.compile(r"\b([\w.\-]+:)[^\s@/]+(@[\w.\-]+)")


def scrub(text: str) -> str:
    """把文本里的代理账密替换成 `***`。

    用途：写 `last_error`、打日志、拼异常信息。**任何可能被落库或写文件的
    字符串都要过一遍这里** —— 排障时最容易顺手把整条 URL 塞进错误消息。
    """
    value = str(text or "")
    if not value:
        return value
    value = _CRED_PATTERN.sub(r"\1***\2", value)
    value = _BARE_CRED_PATTERN.sub(r"\1***\2", value)
    return value


def mask(url: str) -> str:
    """展示用打码：`http://user:***@host:port`。"""
    return scrub(url)