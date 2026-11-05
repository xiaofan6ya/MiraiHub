"""环境策略：生产 / 测试隔离 + 启动 fail-fast + 高危路由黑名单。

为什么单独一个模块
------------------
2026-11-02 事故里，攻击者拿到后台会话后做的第一件事是
`POST /api/app-source/locate` + `GET /api/app-source/search` ——
这两个接口能 **创建任意目录** 并 **递归读取该目录下的文本文件**，
用来捞 `.env` / 私钥 / `client_secret`。同一条链上还有
`/api/maintenance/restore`（用 512MB body 覆盖 sqlite 库文件）和
`/api/maintenance/restart`（控制进程生死）。

这些能力对本地开发是必要的，对生产是纯粹的 RCE/信息泄露面。
所以策略必须写进代码、在启动时断言，而不是靠运维"记得设环境变量"：

    APP_ENV=prod  →  不合规就拒绝启动
    APP_ENV=prod  →  高危路由一律 404（不依赖路由是否被 include）

三个环境的职责
--------------
    prod     正式域名，nginx 反代，仅绑 127.0.0.1，无客户端代码
    staging  内网 + Basic Auth，数据脱敏，可开 DEV_TOOLS
    dev      本地，全量功能

本模块**只读环境变量**，不写任何状态，可以安全地被任何模块导入。
"""
from __future__ import annotations

import os

from admin import config

#: 合法环境名。写错（例如 `production`）直接拒绝启动 —— 静默降级成 dev
#: 是这类开关最危险的失败模式。
_ALLOWED_ENVS = {"prod", "staging", "dev"}

APP_ENV = (os.getenv("APP_ENV", "dev") or "dev").strip().lower()
IS_PROD = APP_ENV == "prod"
IS_STAGING = APP_ENV == "staging"
IS_DEV = APP_ENV == "dev"


def _env_flag(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or "").strip() == "1"


#: 生产环境**无条件 404** 的路径前缀。
#:
#: 这些前缀的共同点：都能让一次后台会话变成「对宿主机的任意文件/进程操作」。
#: 注意用前缀匹配而不是精确路径 —— `/api/app-source/{anything}` 整棵树都要挡。
PROD_BLOCKED_PREFIXES: tuple[str, ...] = (
    # 任意目录创建 + 递归读取（事故中读 .env 的入口）
    "/api/app-source",
    # 覆盖数据库文件 / 控制进程生死 / 任意目录写探针
    "/api/maintenance/restore",
    "/api/maintenance/restart",
    "/api/maintenance/backup/dir",
    # 客户端安装目录探测与覆盖（读本机注册表/磁盘）
    "/api/client-paths",
    "/api/client-profile",
    # 扫描 / 导入本机登录态、向客户端注入凭据
    "/api/accounts/scan-local",
    "/api/accounts/import-local",
    "/api/accounts/refresh-local",
)

#: 生产禁止的后缀型路径（正则风格的通配由调用方展开）。
PROD_BLOCKED_SUFFIXES: tuple[str, ...] = (
    "/inject",
    "/unpack",
    "/unpack/plan",
    "/unpack/progress",
    "/reset-paths",
    "/locate",
    "/search",
)


def is_prod_blocked(path: str) -> bool:
    """该请求路径在生产是否必须返回 404。

    判定顺序：先整段前缀，再按「父路径 + 后缀」匹配，避免
    `/api/accounts/12/inject` 这类带 id 的路径漏网。
    """
    if not IS_PROD:
        return False
    p = (path or "").rstrip("/") or "/"
    for prefix in PROD_BLOCKED_PREFIXES:
        if p == prefix or p.startswith(prefix + "/"):
            return True
    for suffix in PROD_BLOCKED_SUFFIXES:
        if p.endswith(suffix):
            # 只对后台 API 生效，避免误伤静态资源
            if p.startswith("/api/"):
                return True
    return False


def local_runtime() -> bool:
    """本机客户端运行时（桌面 EXE / 安卓 WebView）。

    **生产恒为 False** —— 这是 2026-11-02 事故里最危险的一行：
    只要生产 `.env` 误带 `WORKBUDDY_DESKTOP=1`，`/admin` 就会绕过全部
    鉴权直接下发完整控制台（server.py 的 `_console_allowed`）。
    环境变量是部署时最容易"整体拷过来"的东西，不能让它成为开关。
    """
    if IS_PROD:
        return False
    return _env_flag("WORKBUDDY_DESKTOP") or _env_flag("WORKBUDDY_ANDROID")


def fs_ops_allowed() -> bool:
    """是否允许「任意路径读写 / 目录创建 / 覆盖数据库 / 重启进程」这类运维能力。

    ## 2026-11-05 改为**默认关闭**

    原实现在非 prod 恒返回 True。问题是「线上到底是不是 prod」完全靠部署方自觉：
    直接 `python main.py` 且没设 `APP_ENV=prod` 就是 dev，于是
    `/api/app-source/*`（任意目录 mkdir + 递归读文本）、
    `/api/maintenance/restore`（上传字节覆盖 sqlite 库）、`/restart`（进程自杀）、
    `/api/client-paths/*`（覆盖客户端安装目录）对**所有持有管理员会话的人**敞开。
    拿到会话（XSS / 凭证泄露）不需要 RCE，靠这几条就能读 `.env`、改库、把
    二进制写到任意目录 —— 这就是"被当跳板"的真实形态。

    现在的规则（按优先级）：
      1. 显式 `ADMIN_ALLOW_FS_OPS=0/1` —— 运维明确指定，永远优先；
      2. 本机桌面 / 安卓运行时 —— 只监听回环、没有公网面，保持可用；
      3. 其余（含以服务方式跑的 dev/staging）—— **一律关闭**。

    需要临时排障时显式设 `ADMIN_ALLOW_FS_OPS=1` 并重启，用完立刻关掉，
    不要写进生产 `.env` 常驻。
    """
    explicit = (os.getenv("ADMIN_ALLOW_FS_OPS") or "").strip()
    if explicit:
        return explicit == "1"
    if IS_PROD:
        return False
    # local_runtime() 只读环境变量，不会回调本函数；prod 恒 False。
    if local_runtime():
        return True
    return False


def assert_boot_policy() -> None:
    """生产启动策略断言。不合规直接抛 RuntimeError，让进程起不来。

    宁可启动失败（nginx 502，5 秒内可见），也不要带着「DEV_TOOLS 开着 +
    弱 JWT 密钥 + 全网卡监听」的组合对外服务 —— 那正是事故当天的状态。
    """
    if not IS_PROD:
        # 非 prod 也要挡住最危险的一种误配置：服务绑在公网/全网卡，却带着
        # 「本机客户端免密」标志。server._console_allowed() 命中该标志时
        # 不校验任何 Cookie/JWT，等于 /admin 与 /admin/assets/* 整体裸奔
        # （事故报告 R2）。桌面 EXE 只监听回环，所以按 HOST 判断。
        host = (os.getenv("ADMIN_HOST", "") or "").strip()
        if (host and host not in ("127.0.0.1", "::1", "localhost")
                and (_env_flag("WORKBUDDY_DESKTOP") or _env_flag("WORKBUDDY_ANDROID"))):
            raise RuntimeError(
                "启动策略校验失败：ADMIN_HOST=%s 不是回环，却设置了 "
                "WORKBUDDY_DESKTOP/WORKBUDDY_ANDROID —— 该标志会绕过 /admin 的全部鉴权。"
                "请去掉该变量，或把 APP_ENV 设为 prod（prod 下该标志恒被忽略）。" % host)
        return

    errs: list[str] = []
    s = config.settings

    if (os.getenv("APP_ENV", "") or "").strip().lower() != "prod":
        errs.append("APP_ENV 必须显式为 prod")

    if getattr(s, "DEV_TOOLS", False):
        errs.append("ADMIN_DEV_TOOLS 必须为 0（/api/app-source/* 生产禁用）")
    if getattr(s, "ENABLE_DOCS", False):
        errs.append("ADMIN_ENABLE_DOCS 必须为 0（/docs、/openapi.json 生产禁用）")

    if _env_flag("WORKBUDDY_DESKTOP") or _env_flag("WORKBUDDY_ANDROID"):
        errs.append("生产禁止 WORKBUDDY_DESKTOP/ANDROID（会绕过 /admin 登录门禁）")

    host = (os.getenv("ADMIN_HOST", "") or "").strip()
    if host not in ("127.0.0.1", "::1", "localhost"):
        errs.append(f"ADMIN_HOST 必须绑回环（当前 {host or '未设置'}），只允许 nginx 反代")

    secret = (getattr(s, "JWT_SECRET", "") or "")
    if len(secret) < 32 or secret.startswith("dev-insecure"):
        errs.append("ADMIN_JWT_SECRET 缺失、过短或仍是开发默认值（需 >=32 字节）")

    if len((os.getenv("JOIN_TICKET_SECRET", "") or "")) < 32:
        errs.append("JOIN_TICKET_SECRET 未配置或短于 32 字节（join 票据签名密钥）")

    try:
        hours = int(getattr(s, "JWT_EXPIRE_HOURS", 24))
    except (TypeError, ValueError):
        hours = 24
    if hours > 8:
        errs.append(f"ADMIN_JWT_EXPIRE_HOURS 应 <= 8（当前 {hours}）")

    origins = getattr(s, "CORS_ORIGINS", ["*"]) or ["*"]
    if "*" in origins:
        errs.append("ADMIN_CORS_ORIGINS 生产禁止 *，请列正式域名")

    if (getattr(s, "ADMIN_PASSWORD", "") or "") == "admin123":
        errs.append("ADMIN_PASSWORD 仍是 admin123 弱口令")

    if errs:
        raise RuntimeError(
            "启动策略校验失败 APP_ENV=prod：\n  - " + "\n  - ".join(errs)
        )


def describe() -> dict:
    """给后台「运行配置」页展示的一条摘要（不含任何密钥）。"""
    return {
        "app_env": APP_ENV,
        "is_prod": IS_PROD,
        "fs_ops_allowed": fs_ops_allowed(),
        "blocked_prefixes": list(PROD_BLOCKED_PREFIXES) if IS_PROD else [],
    }
