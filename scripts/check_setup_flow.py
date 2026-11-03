"""检查「首次创建管理员账号」引导接口的准入、fail-closed 与登录联动。

用临时空库跑，绝不接触开发者的 `.env` 或账号数据：

    python scripts/check_setup_flow.py

覆盖点（每一条都对应一个真实风险）：
  1. 未初始化时 `/api/setup/state` 对本机 `needs_setup=true`、远程 `false`；
  2. 未初始化时 `/api/login` 返回 503 + `setup_required`（前端据此跳引导）；
  3. 弱口令 / 过短密码 / 非法用户名被拒（400）；
  4. 本机创建成功 → 拿到令牌 → 后台接口可用；
  5. 创建后重复创建返回 409（**关键**：入口是公开写接口，不 fail-closed
     就等于让任何先到的人把后台改成自己的）；
  6. 创建后 `/api/login` 可用新账号登录、错误密码 401；
  7. 改密码后旧密码失效、新密码可登录；
  8. 已由 env 配置的实例：远程不可见引导，任何创建尝试都返回 409，
     且「设置页改密码」被明确拒绝（env 优先，改覆盖值不生效）；
  9. **env 优先**：页面创建过账号之后再配置 env，登录以 env 为准，
     页面账号的密码不再可用（优先级是产品明确的规则，必须锁进测试）。

两个场景必须跑在**独立进程**里：`admin.db` 的 engine 在 import 时就绑定
了 `ADMIN_DATABASE_URL`，同进程二次改环境变量不会重建连接池。
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

#: 测试用客户端地址：回环代表本机壳，10.x 代表局域网/公网来源。
LOCAL = ("127.0.0.1", 51234)
REMOTE = ("10.20.30.40", 51234)


def _clean_env() -> None:
    for name in list(os.environ):
        if name.startswith(("ADMIN_", "CONVERTER_", "WORKBUDDY_", "WB_DESKTOP_")):
            os.environ.pop(name)


def _base_env(db: Path, **extra: str) -> None:
    os.environ.update({
        "WORKBUDDY_DESKTOP": "1",
        "ADMIN_DATABASE_URL": "sqlite:///" + db.as_posix(),
        "ADMIN_GATEWAY_LIMIT_BACKEND": "memory",
        "ADMIN_REDIS_URL": "",
        "ADMIN_JWT_SECRET": secrets.token_urlsafe(48),
        "ADMIN_DEV_TOOLS": "0",
        **extra,
    })


def _fresh_instance(db: Path, **extra: str) -> None:
    """未初始化的实例：引导创建 → 登录 → 改密 的完整路径。"""
    _clean_env()
    _base_env(db, **extra)

    from fastapi.testclient import TestClient
    from admin.server import app
    from admin.db import engine

    try:
        with TestClient(app, client=REMOTE) as web:
            state = web.get("/api/setup/state").json()
            assert state["configured"] is False, state
            assert state["needs_setup"] is False, "远程来源不该看到创建入口"
            assert state["local"] is False, state
            # 远程创建：404（不确认「这个实例还没初始化」）
            assert web.post("/api/setup", json={
                "username": "admin", "password": "Str0ng-Pass-2026"}).status_code == 404

        with TestClient(app, client=LOCAL) as shell:
            state = shell.get("/api/setup/state").json()
            assert state["configured"] is False, state
            assert state["needs_setup"] is True, "本机必须能看到创建入口"
            assert state["local"] is True, state
            assert state["password_min"] >= 8, state

            # 未初始化时登录必须明确报 setup_required（而不是笼统 401）
            resp = shell.post("/api/login", data={"username": "admin", "password": "x"})
            assert resp.status_code == 503, resp.text
            assert resp.json()["detail"]["code"] == "setup_required", resp.text

            # 入参校验
            assert shell.post("/api/setup", json={
                "username": "ab", "password": "Str0ng-Pass-2026"}).status_code == 400
            assert shell.post("/api/setup", json={
                "username": "admin", "password": "short"}).status_code == 400
            assert shell.post("/api/setup", json={
                "username": "admin", "password": "12345678"}).status_code == 400
            assert shell.post("/api/setup", json={
                "username": "admin", "password": "admin"}).status_code == 400
            assert shell.post("/api/setup", json={
                "username": "bad name!", "password": "Str0ng-Pass-2026"}).status_code == 400

            created = shell.post("/api/setup", json={
                "username": "admin", "password": "Str0ng-Pass-2026"})
            assert created.status_code == 200, created.text
            token = created.json()["token"]
            assert shell.get("/api/accounts",
                             headers={"X-Admin-Token": token}).status_code == 200

            # 入口必须永久关闭
            again = shell.post("/api/setup", json={
                "username": "attacker", "password": "Evil-Pass-2026"})
            assert again.status_code == 409, again.text

            assert shell.get("/api/setup/state").json()["configured"] is True

            # 登录联动
            assert shell.post("/api/login", data={
                "username": "admin", "password": "Str0ng-Pass-2026"}).status_code == 200
            assert shell.post("/api/login", data={
                "username": "admin", "password": "wrong-pass"}).status_code == 401

            # 改密码
            changed = shell.patch("/api/admin/password",
                                  headers={"X-Admin-Token": token},
                                  json={"old_password": "Str0ng-Pass-2026",
                                        "new_password": "Rotated-Pass-2026"})
            assert changed.status_code == 200, changed.text
            assert shell.post("/api/login", data={
                "username": "admin", "password": "Rotated-Pass-2026"}).status_code == 200
            assert shell.post("/api/login", data={
                "username": "admin", "password": "Str0ng-Pass-2026"}).status_code == 401

        # 远程在本机创建完之后仍然看不到入口
        with TestClient(app, client=REMOTE) as web:
            assert web.get("/api/setup/state").json()["needs_setup"] is False
    finally:
        engine.dispose()


def _env_instance(db: Path) -> None:
    """已由环境变量配置的实例：不许再创建，页面改密码也要明确拒绝。"""
    _clean_env()
    _base_env(db, ADMIN_USERNAME="deployer", ADMIN_PASSWORD="Env-Pass-2026x")

    from fastapi.testclient import TestClient
    from admin.server import app
    from admin.db import engine

    try:
        with TestClient(app, client=REMOTE) as web:
            state = web.get("/api/setup/state").json()
            assert state["configured"] is True, state
            assert state["needs_setup"] is False, state
            assert state["source"] in ("env", "stored"), state
            assert web.post("/api/login", data={
                "username": "deployer", "password": "Env-Pass-2026x"}).status_code == 200
        with TestClient(app, client=LOCAL) as shell:
            assert shell.post("/api/setup", json={
                "username": "x", "password": "Whatever-2026"}).status_code == 409
            # env 优先：页面改密码不会生效，必须拒绝而不是静默吞掉
            blocked = shell.patch("/api/admin/password",
                                  headers={"X-Admin-Token": _login(shell, "deployer", "Env-Pass-2026x")},
                                  json={"old_password": "Env-Pass-2026x",
                                        "new_password": "Rotated-Pass-2026"})
            assert blocked.status_code == 400, blocked.text
            # 登录仍以 env 为准
            assert shell.post("/api/login", data={
                "username": "deployer", "password": "Env-Pass-2026x"}).status_code == 200
    finally:
        engine.dispose()


def _env_overrides_stored(db: Path) -> None:
    """页面创建过账号之后再配置 env：登录以 env 为准（优先级规则）。"""
    _clean_env()
    _base_env(db)  # 不带任何 ADMIN 凭据

    from fastapi.testclient import TestClient
    from admin.server import app
    from admin.config import settings
    from admin.db import engine

    try:
        with TestClient(app, client=LOCAL) as shell:
            created = shell.post("/api/setup", json={
                "username": "alice", "password": "Alice-Pass-2026"})
            assert created.status_code == 200, created.text
            assert shell.post("/api/login", data={
                "username": "alice", "password": "Alice-Pass-2026"}).status_code == 200
            assert shell.get("/api/setup/state").json()["source"] == "stored"

            # 模拟部署方之后在 .env 里配置了凭据并重启：
            # settings 在 import 时从环境变量取值，这里直接改单例等价于重启后的取值。
            settings.ADMIN_USERNAME = "deployer"
            settings.ADMIN_PASSWORD = "Env-Pass-2026x"

            state = shell.get("/api/setup/state").json()
            assert state["configured"] is True, state
            assert state["source"] == "env", state

            # env 赢：env 凭据可登录，页面账号密码失效
            assert shell.post("/api/login", data={
                "username": "deployer", "password": "Env-Pass-2026x"}).status_code == 200
            assert shell.post("/api/login", data={
                "username": "alice", "password": "Alice-Pass-2026"}).status_code == 401
            # 令牌 sub 也要跟着换成 env 用户名
            token = shell.post("/api/login", data={
                "username": "deployer", "password": "Env-Pass-2026x"}).json()["token"]
            assert shell.get("/api/accounts",
                             headers={"X-Admin-Token": token}).status_code == 200
    finally:
        engine.dispose()


def _login(client, username: str, password: str) -> str:
    resp = client.post("/api/login", data={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))

    # 两个场景各起一个子进程：见模块 docstring 里关于 engine 绑定的说明。
    with tempfile.TemporaryDirectory(prefix="wb-setup-") as work:
        scenario = Path(work) / "fresh.db"
        env = Path(work) / "env.db"
        both = Path(work) / "both.db"
        code = (
            "import sys; sys.path.insert(0, {root!r});"
            "import check_setup_flow as m;"
            "m.{fn}(__import__('pathlib').Path({db!r}))"
        )
        for fn, db, label in (("_fresh_instance", scenario, "未初始化实例"),
                              ("_env_instance", env, "环境变量实例"),
                              ("_env_overrides_stored", both, "env 覆盖页面账号")):
            script = code.format(root=str(root / "scripts"), fn=fn, db=str(db))
            result = subprocess.run([sys.executable, "-c", script],
                                    capture_output=True, text=True, env=_child_env())
            if result.returncode != 0:
                sys.stderr.write(f"[{label}] 检查失败：\n{result.stdout}\n{result.stderr}")
                return 1
            print(f"[{label}] OK")

    print("首次初始化引导检查通过：准入范围、fail-closed、登录与改密联动均符合预期")
    return 0


def _child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ADMIN_", "CONVERTER_", "WORKBUDDY_", "WB_DESKTOP_"))}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    return env


if __name__ == "__main__":
    raise SystemExit(main())

