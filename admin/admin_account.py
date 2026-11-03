"""管理员账号凭据的唯一来源：环境变量 + 页面保存的覆盖值。

## 为什么要有这个模块

以前后台账号**只能**来自 `.env`（`ADMIN_USERNAME` / `ADMIN_PASSWORD`），
未配置时登录直接 503。这在服务器部署上说得通，但桌面 exe / 安卓 apk 打包后
没有可编辑的 `.env`，于是启动器在 `config.json` 里随机生成一个口令塞进环境变量
—— 用户**永远不知道自己密码是什么**，因此「局域网里用浏览器登录管理台」这件
事在本地壳上等于做不到，只能靠桥接令牌免登录。

现在凭据分两层：

1. 环境变量 ``ADMIN_USERNAME`` / ``ADMIN_PASSWORD``（部署方提供，**优先**）
2. ``SystemSetting`` 的 ``admin_username`` / ``admin_password``（页面创建/修改）

**优先级是 env > 页面账号**（产品决定）：`.env` 是部署方的显式配置，只要它
配置完整，登录就以它为准 —— 即使页面已经创建过账号。页面账号只在 env
未配置时生效，服务对象是桌面 exe / 安卓 apk 这类「没有 .env 可编辑」的部署：
引导创建后即可用这个账号从局域网的浏览器登录管理台。

两层都没有 → :func:`is_configured` 为 False，前端引导用户创建
（见 ``admin/routers/setup.py`` 与 ``front/src/pages/SetupPage.tsx``）。

因为 env 优先，**env 部署下「设置页改密码」不会生效**（改的是被 env 遮住的
覆盖值）——所以 ``/api/admin/password`` 在 ``source == "env"`` 时直接拒绝并
提示改 .env，绝不静默吞掉一次看似成功的修改。

## 为什么带缓存

``verify_admin_token`` 每个请求都要比对令牌里的用户名，而用户名现在存在库里。
如果每次请求都查一次主键，等于给所有后台接口加一次数据库往返。这里用
5 秒 TTL 的进程内缓存：写入方（同一进程）会立刻失效缓存，多进程部署时
最坏延迟 5 秒 —— 对「改名后旧令牌立即失效」这个语义完全够用。
"""
from __future__ import annotations

import logging
import threading
import time

from admin.config import settings
from admin.db import SessionLocal
from admin.models import SystemSetting
from admin.security import hash_password, verify_password

logger = logging.getLogger("admin.admin_account")

USERNAME_KEY = "admin_username"
PASSWORD_KEY = "admin_password"

#: 缓存有效期（秒）。跨进程改名最坏要等这么久才在别的进程生效。
_CACHE_TTL = 5.0


class AlreadyConfigured(RuntimeError):
    """已经存在管理员凭据，拒绝第二次初始化（fail-closed）。"""


_write_lock = threading.Lock()
_cache: dict | None = None
_cache_at = 0.0
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def _env_username() -> str:
    return (getattr(settings, "ADMIN_USERNAME", "") or "").strip()


def _env_password() -> str:
    return getattr(settings, "ADMIN_PASSWORD", "") or ""


def _read_from_db() -> dict:
    """读两行覆盖值。数据库不可用时返回 ``ok=False`` 而不是抛异常。

    为什么吞异常：本模块会被鉴权中间件在**每个请求**上调用，数据库抖动时
    抛异常会把所有接口打成 500；返回「没读到」让它自然回落到环境变量。
    """
    db = None
    try:
        db = SessionLocal()
        rows = (db.query(SystemSetting)
                .filter(SystemSetting.key.in_((USERNAME_KEY, PASSWORD_KEY)))
                .all())
        values = {row.key: (row.value or "") for row in rows}
        return {"ok": True, "username": values.get(USERNAME_KEY, "").strip(),
                "password_hash": values.get(PASSWORD_KEY, "").strip()}
    except Exception:
        logger.warning("读取管理员凭据失败（将回落到环境变量）", exc_info=True)
        return {"ok": False, "username": "", "password_hash": ""}
    finally:
        if db is not None:
            db.close()


def snapshot(force: bool = False) -> dict:
    """返回 ``{ok, username, password_hash}``（带 5 秒缓存）。

    **失败结果不入缓存**：启动早期数据库可能还没建表（``init_db()`` 在
    startup 钩子里），若把这次的 ``ok=False`` 缓存住，接下来 5 秒内所有
    鉴权与引导判定都会认为「数据库不可用」，表现为刚启动时后台短暂报错。
    失败的调用本身只是一次 SELECT，重试代价可忽略。
    """
    global _cache, _cache_at
    if not force:
        with _cache_lock:
            if _cache is not None and time.monotonic() - _cache_at < _CACHE_TTL:
                return dict(_cache)
    data = _read_from_db()
    if data.get("ok"):
        with _cache_lock:
            _cache = dict(data)
            _cache_at = time.monotonic()
    return dict(data)


def invalidate() -> None:
    """丢弃缓存：写入方必须在提交事务后调用。"""
    global _cache, _cache_at
    with _cache_lock:
        _cache = None
        _cache_at = 0.0


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------

def state(force: bool = False) -> dict:
    """当前生效的管理员账号状态。

    **优先级：环境变量 > 页面创建的账号**（产品决定，2026-11-03）：

    * ``"env"``    .env 同时配置了 ADMIN_USERNAME 与 ADMIN_PASSWORD —— 部署方的
                   显式配置是权威，即使页面已经创建过账号也以它为准；
    * ``"stored"`` 页面创建的账号（``admin_username`` / ``admin_password``），
                   只在环境变量未配置（或只配了一半）时生效 —— 桌面 exe /
                   安卓 apk 没有 .env，走的就是这一层；
    * ``""``       两层都没有 —— 需要引导用户创建。

    只配了一半（比如只有用户名没有密码）不算配置完成：登录需要两者，
    这种状态由引导页/启动日志给出明确提示，而不是拿半份配置去猜。
    """
    snap = snapshot(force=force)
    stored_user = snap.get("username") or ""
    stored_hash = snap.get("password_hash") or ""
    env_user, env_pass = _env_username(), _env_password()
    if env_user and env_pass:
        return {"configured": True, "username": env_user, "source": "env",
                "db_ready": bool(snap.get("ok"))}
    if stored_user and stored_hash:
        return {"configured": True, "username": stored_user, "source": "stored",
                "db_ready": bool(snap.get("ok"))}
    return {"configured": False, "username": "", "source": "",
            "db_ready": bool(snap.get("ok"))}


def is_configured() -> bool:
    return bool(state()["configured"])


def effective_username() -> str:
    """令牌 ``sub`` 要比对的名字：环境变量优先，其次页面创建的账号。"""
    return state()["username"]


def verify_admin_password(password: str) -> bool:
    """校验管理员密码。来源判定与 :func:`state` 完全一致：env 优先。

    兼容旧版明文行（读取时迁移为哈希）；两者都未配置时一律 False，
    绝不允许空口令登录。
    """
    snap = snapshot()
    stored = snap.get("password_hash") or ""
    env_user, env_pass = _env_username(), _env_password()
    if env_user and env_pass:
        if not verify_password(password, env_pass):
            return False
        # 首次使用 env 密码时落一份哈希：之后若部署方清空 .env，
        # 账号不至于直接消失（用户名行未播种，所以仍会进入引导创建）。
        _persist_password_hash(hash_password(env_pass))
        return True
    if stored:
        if not stored.startswith("pbkdf2$"):
            # 旧版明文配置：校验通过后顺手迁移成哈希，避免长期躺明文。
            if verify_password(password, stored):
                _persist_password_hash(hash_password(password))
                return True
            return False
        return verify_password(password, stored)
    return False


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------

def _upsert(db, key: str, value: str) -> None:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemSetting(key=key, value=value))


def _persist_password_hash(hashed: str) -> None:
    db = None
    try:
        db = SessionLocal()
        _upsert(db, PASSWORD_KEY, hashed)
        db.commit()
    except Exception:
        logger.warning("保存管理员密码哈希失败（本次校验不受影响）", exc_info=True)
    finally:
        if db is not None:
            db.close()
        invalidate()


def set_credentials(username: str, password: str) -> None:
    """首次创建管理员账号。

    已存在凭据时抛 :class:`AlreadyConfigured` —— 这是**必须**的：
    引导接口是公开的，一旦允许重复写入，任何人都能把管理员改成自己。
    """
    with _write_lock:
        if state(force=True)["configured"]:
            raise AlreadyConfigured("管理员账号已存在")
        db = None
        try:
            db = SessionLocal()
            _upsert(db, USERNAME_KEY, username)
            _upsert(db, PASSWORD_KEY, hash_password(password))
            db.commit()
        finally:
            if db is not None:
                db.close()
            invalidate()


def update_password(password: str) -> None:
    """后台「修改密码」：写入页面账号覆盖值。

    **调用方必须先确认 ``state()["source"] == "stored"``**：env 优先级更高，
    若凭据来自环境变量，这里写入的覆盖值会被 env 遮住 —— 一次「看似成功」
    的修改实际改不了登录口令。``/api/admin/password`` 据此在 env 来源时
    直接拒绝并提示改 .env，而不是让用户误以为密码已经换了。
    """
    with _write_lock:
        current = state(force=True)
        db = None
        try:
            db = SessionLocal()
            # 防御：env 来源的覆盖值会被遮住，调用方应已拦截；这里兜底同步
            # 用户名行，避免留下「用户名来自 env、密码来自库」的错配状态。
            if current["configured"] and current["source"] == "env":
                _upsert(db, USERNAME_KEY, current["username"])
            _upsert(db, PASSWORD_KEY, hash_password(password))
            db.commit()
        finally:
            if db is not None:
                db.close()
            invalidate()
