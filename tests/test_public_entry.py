"""公开注册入口（/api/open/*）的单元测试。

覆盖四个安全关键点：
1. 平台清单遵循管理页开关（关掉的平台不出现在公开页）；
2. 会话绑定 IP / 平台，伪造或漂移一律按「不存在」处理（不泄露存在性）；
3. 进程内限流兜底在超限时拒绝（Redis 不可用场景的最后一道闸）；
4. 打码函数不回显完整手机号/账号名。
"""
import os

os.environ.setdefault(
    "ADMIN_DATABASE_URL",
    "mysql+pymysql://root:root@127.0.0.1:3306/workbuddy_admin?charset=utf8mb4")

import pytest

from admin.public_auth import (
    _load_config,
    _mask_name,
    _platform_list,
    _rate_hit,
    get_session,
    register_session,
)


@pytest.fixture()
def db():
    # 测试跑在隔离的临时 sqlite 上（wb-isolated-tests）：先建表再用
    from admin.db import Base, SessionLocal, engine

    Base.metadata.create_all(engine)
    session = SessionLocal()
    yield session
    session.close()


def test_platform_list_respects_flags(db):
    items = _platform_list(db)
    ids = [p["id"] for p in items]
    # 已接入且有公开入口适配器的平台都在清单里，workbuddy 恒排第一（order=10）。
    # doubao 已不在 `admin/platform/` 下（适配器与平台一起下线），不再断言。
    assert ids[0] == "workbuddy"
    assert {"loomy", "raccoon"} <= set(ids)


def test_platform_config_roundtrip(db):
    from admin.public_auth import _save_config

    cfg = _load_config(db)
    cfg["platforms"]["raccoon"] = False
    cfg["ticket_ttl"] = 3600
    _save_config(db, cfg)
    db.expire_all()  # 测试会话 expire_on_commit=False，手动失效读新值
    cfg2 = _load_config(db)
    assert cfg2["platforms"]["raccoon"] is False
    assert cfg2["ticket_ttl"] == 3600
    items = _platform_list(db)
    assert "raccoon" not in [p["id"] for p in items]
    # 还原
    cfg2["platforms"]["raccoon"] = True
    cfg2["ticket_ttl"] = 86400
    _save_config(db, cfg2)
    db.expire_all()


def test_session_ip_binding():
    sid = register_session("raccoon", "qr", "1.2.3.4", {})
    info = get_session(sid, "raccoon", "1.2.3.4")
    assert info["platform"] == "raccoon"
    # 换 IP → 一律「不存在」（不泄露会话存在性）
    with pytest.raises(Exception):
        get_session(sid, "raccoon", "5.6.7.8")
    # 跨平台 → 同样拒绝
    with pytest.raises(Exception):
        get_session(sid, "doubao", "1.2.3.4")


def test_session_ttl_rejects_expired(monkeypatch):
    sid = register_session("raccoon", "qr", "9.9.9.9", {})
    # 直接把会话改老（越过 TTL）
    from admin import public_auth

    public_auth._sessions[sid]["created"] -= (public_auth.SESSION_TTL + 10)
    with pytest.raises(Exception):
        get_session(sid, "raccoon", "9.9.9.9")
    assert sid not in public_auth._sessions  # 过期即焚


def test_rate_limit_memory_fallback(monkeypatch):
    # 强制走进程内兜底：让 Redis 分支抛错
    monkeypatch.setattr("redis.Redis.from_url",
                        __import__("builtins").staticmethod(
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no redis"))))
    ip = "10.99.99.99"
    ok = sum(1 for _ in range(8) if _rate_hit("sms", ip))
    # sms 桶 3 次 / 10 分钟：前 3 次放行，之后必须拒绝
    assert ok == 3


def test_mask_name_hides_full_value():
    for raw in ("13800138000", "nickname-x", "ab"):
        masked = _mask_name(raw)
        assert masked != raw
        assert "13800" not in masked
