"""SQLAlchemy 引擎 / 会话 / Base，并负责建库建表。"""
import logging
import re
import time

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from admin.config import settings

logger = logging.getLogger("admin.db")

# SQL 标识符白名单：仅允许常规字母数字下划线，杜绝任何拼接注入。
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"非法 SQL 标识符: {name!r}")
    return name


def _quote_db_name(name: str) -> str:
    """库名（来自配置）去除反引号转义后安全引用。"""
    return name.replace("`", "").replace("\\", "").strip()

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, pool_recycle=3600,
                        pool_timeout=30, pool_size=20, max_overflow=40, future=True)
if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(connection, _record):
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA synchronous=NORMAL")
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_database():
    """若 DATABASE_URL 指向的库不存在则创建（仅 MySQL）。"""
    url = settings.DATABASE_URL
    if not url.startswith("mysql"):
        return
    db_name = url.split("/")[-1].split("?")[0]
    rest = url.split("://", 1)[1]
    user_pass, host_rest = rest.split("@", 1)
    user, pwd = (user_pass.split(":", 1) + [""])[:2] if ":" in user_pass else (user_pass, "")
    host_port = host_rest.split("/", 1)[0]
    host = host_port.split(":")[0]
    port = int(host_port.split(":")[1]) if ":" in host_port else 3306
    import pymysql

    conn = pymysql.connect(host=host, port=port, user=user, password=pwd, charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{_quote_db_name(db_name)}` CHARACTER SET utf8mb4")
        conn.commit()
    finally:
        conn.close()


#: 启动阶段访问数据库的重试次数与间隔（秒）。
#:
#: 为什么必须重试而不是直接抛：`_startup()` 里任何异常都会让 uvicorn 启动失败、
#: 进程直接退出。而这台机器内存只有 1870MB，MySQL 已被内核 OOM 杀掉过多次；
#: MySQL 重启的那几十秒里，服务启动必然失败并退出，nginx 侧表现为 502
#: —— 这正是「线上老是崩、老师就断开了」的直接成因。
#: 改为「重试 + 降级启动」后：MySQL 短暂不可用只会让请求返回 503，
#: 进程不再退出，MySQL 一恢复（连接池有 pool_pre_ping）就自动恢复正常。
_STARTUP_RETRIES = 30
_STARTUP_RETRY_INTERVAL = 2.0


def _retry_db_startup(label: str, fn) -> bool:
    """带退避地执行启动期数据库操作；全部失败时返回 False（调用方降级启动）。"""
    last: Exception | None = None
    for attempt in range(1, _STARTUP_RETRIES + 1):
        try:
            fn()
            if attempt > 1:
                logger.warning("数据库%s在第 %d 次尝试后恢复", label, attempt)
            return True
        except Exception as e:  # 含 pymysql 连接异常 / SQLAlchemy 异常
            last = e
            if attempt == 1 or attempt % 5 == 0:
                logger.warning(
                    "数据库%s失败（第 %d/%d 次）：%s；%ss 后重试",
                    label, attempt, _STARTUP_RETRIES, e, _STARTUP_RETRY_INTERVAL,
                )
            time.sleep(_STARTUP_RETRY_INTERVAL)
    logger.error(
        "数据库%s在 %d 次重试后仍失败，服务将以降级模式启动"
        "（进程保持存活，请求返回 503；数据库恢复后无需重启即可自愈）。最后错误：%s",
        label, _STARTUP_RETRIES, last,
    )
    return False


def init_db() -> bool:
    """建表 + 迁移。返回 True 表示成功；False 表示数据库暂不可用（降级启动）。

    绝不向上抛异常：调用方在 FastAPI 的 startup 钩子里，抛出去会让整个服务退出。
    """
    def _do():
        from admin import models  # noqa: F401  确保模型已注册

        Base.metadata.create_all(bind=engine)
        _ensure_column("request_audits", "account_id", "INT", "NULL")
        _ensure_column("request_audits", "client_ip", "VARCHAR(64)", "DEFAULT ''")
        _ensure_column("request_audits", "user_agent", "VARCHAR(512)", "DEFAULT ''")

        # 迁移：给 api_keys 表加 key_full 列（若不存在）
        _ensure_column("api_keys", "key_full", "VARCHAR(2048)", "DEFAULT ''")

        # 迁移：多平台 / 精细化 Key 的两个列。
        #
        # 桌面版（EXE）用的是**长期存在的 SQLite 库**，老库里这两列都不存在。
        # 漏了迁移的后果不是「功能缺失」而是**接口直接 500**：
        # SQLAlchemy 按模型 SELECT 全部列，表里少一列就整条查询失败 ——
        # 表现为 /api/keys 返回 500、控制台「API Keys」页空白。
        #
        # 实测：`api_keys` 缺 aggregate / account_ids 两项时，
        # GET /api/keys → 500 Internal Server Error。
        _ensure_column("api_keys", "aggregate", "BOOLEAN", "DEFAULT 1")
        _ensure_column("api_keys", "account_ids", "TEXT", "")

        # 迁移：给 model_configs 表补齐倍率相关列（旧实例可能缺）
        _ensure_column("model_configs", "credit_multiplier", "FLOAT", "DEFAULT 0")
        _ensure_column("model_configs", "credits_raw", "VARCHAR(120)", "DEFAULT ''")
        _ensure_column("model_configs", "description", "TEXT", "")
        _ensure_column("model_configs", "upstream_metadata", "TEXT", "")

        # 迁移：给 schedules 表加 stop_after 列（daily_checkin 任务的「停止领取时间」）
        _ensure_column("schedules", "stop_after", "DATETIME", "NULL")
        # Older MySQL versions reject defaults on TEXT. Existing rows may be
        # NULL; schedule timing treats NULL and an empty config identically.
        _ensure_column("schedules", "timing_config", "TEXT", "NULL")

        # 迁移：给 usage_logs 表加详细用量与真实客户端 IP 列
        _ensure_column("usage_logs", "prompt_tokens", "INT", "NULL")
        _ensure_column("usage_logs", "completion_tokens", "INT", "NULL")
        _ensure_column("usage_logs", "total_tokens", "INT", "NULL")
        _ensure_column("usage_logs", "cached_tokens", "INT", "NULL")
        _ensure_column("usage_logs", "client_ip", "VARCHAR(64)", "DEFAULT ''")
        _ensure_column("usage_logs", "use_case", "VARCHAR(64)", "DEFAULT ''")
        _ensure_column("usage_logs", "reasoning_effort", "VARCHAR(32)", "DEFAULT ''")
        # 迁移：给 usage_logs 表加请求级表格日志字段
        _ensure_column("usage_logs", "seq", "INT", "DEFAULT 0")
        _ensure_column("usage_logs", "ttfb_ms", "INT", "NULL")
        _ensure_column("usage_logs", "latency_ms", "INT", "NULL")
        _ensure_column("usage_logs", "error_kind", "VARCHAR(32)", "DEFAULT ''")
        _ensure_column("usage_logs", "diagnostic_json", "TEXT", "NULL")
        # 迁移：平台归属。
        #
        # 为什么必须显式存而不是靠 `account_id` 反查：
        #   1. 账号删除后日志就失去归属（历史上已积累 204 条孤儿）；
        #   2. 每次按平台统计都要 JOIN accounts，平台一多就是多次全表扫；
        #   3. 平台自己写日志时本就知道自己是谁，白存一份更简单可靠。
        # 新写入由各平台网关填值；历史行由 `_backfill_usage_platform()` 回填。
        _ensure_column("usage_logs", "platform", "VARCHAR(24)", "DEFAULT ''")
        _ensure_index("usage_logs", "ix_usage_logs_platform",
                      ("platform", "created_at"))

        # 迁移：给 accounts 表加稳定性状态机字段
        _ensure_column("accounts", "err_count", "INT", "DEFAULT 0")
        _ensure_column("accounts", "cool_until", "DATETIME", "NULL")
        _ensure_column("accounts", "cool_kind", "VARCHAR(16)", "DEFAULT ''")
        _ensure_column("accounts", "last_err_at", "DATETIME", "NULL")
        _ensure_column("accounts", "last_err_msg", "VARCHAR(255)", "DEFAULT ''")
        _ensure_column("accounts", "last_upstream_error", "TEXT", "NULL")
        _ensure_column("accounts", "selection_priority", "INT", "NULL")
        _ensure_column("accounts", "max_in_flight", "INT", "NULL")
        _ensure_column("account_model_cool", "upstream_error", "TEXT", "NULL")
        _ensure_column("account_model_cool", "reset_source", "VARCHAR(24)", "DEFAULT ''")
        _ensure_column("accounts", "last_picked_at", "DATETIME", "NULL")

        # 迁移：熔断 / session-dead 连续计数 / 连败降权 / 快过期积分
        # 这些是「跨重启必须保留」的状态：重启后失忆会导致重新踩同一批雷
        # （冷账号被当健康号选中，再被上游拒一次）。
        _ensure_column("accounts", "breaker_until", "DATETIME", "NULL")
        _ensure_column("accounts", "breaker_fails", "INT", "DEFAULT 0")
        _ensure_column("accounts", "session_dead_fails", "INT", "DEFAULT 0")
        _ensure_column("accounts", "consecutive_fails", "INT", "DEFAULT 0")
        _ensure_column("accounts", "degrade_until", "DATETIME", "NULL")
        _ensure_column("accounts", "credits_expiring", "INT", "DEFAULT 0")

        # 迁移：积分到期口径字段（见 admin/credits.py）。
        # credits_expiring 的历史窗口是 30 天，而官方与参考实现按 7 天判定
        # 「快过期」；这两件事必须分开存，否则调度用的窗口会和界面显示的对不上。
        _ensure_column("accounts", "credits_expiring_soon", "INT", "DEFAULT 0")
        _ensure_column("accounts", "credits_soonest_expire_at", "DATETIME", "NULL")
        _ensure_column("accounts", "credits_evergreen", "INT", "DEFAULT 0")
        _ensure_column("accounts", "credits_expired", "INT", "DEFAULT 0")
        _ensure_column("accounts", "credits_snapshot", "TEXT", "NULL")
        _ensure_column("accounts", "credits_synced_at", "DATETIME", "NULL")

        # 迁移：api_keys 加 group_id 列（绑定模型分组；0=不限制）
        _ensure_column("api_keys", "group_id", "INT", "DEFAULT 0")
        _ensure_column("api_keys", "account_id", "INT", "NULL")
        _ensure_column("api_keys", "max_concurrent", "INT", "NULL")
        _ensure_column("api_keys", "rate_per_minute", "INT", "NULL")
        # 迁移：多平台网关。历史号池与 Key 全部属于 workbuddy，因此默认值即回填值；
        # 空串（''）是 `_ensure_column` 给不出默认值时的历史残留，读取时也按
        # workbuddy 解释（见 admin/platform/registry.py:spec_of_account）。
        _ensure_column("accounts", "platform", "VARCHAR(24)", "DEFAULT 'workbuddy'")
        _ensure_index("accounts", "ix_accounts_platform", ("platform",))
        _ensure_column("api_keys", "platform", "VARCHAR(24)", "DEFAULT ''")
        # 定时任务也要按平台归属：否则 workbuddy 的签到/成长任务会把新平台的号
        # 一起拉进来（那些号没有这些接口，只会稳定报错并污染健康度统计）。
        _ensure_column("schedules", "platform", "VARCHAR(24)", "DEFAULT 'workbuddy'")
        # 模型表保持一张公共表，只按平台分组展示：几十个模型混在一起，
        # 光看名字分不清是哪个平台的。
        _ensure_column("model_configs", "platform", "VARCHAR(24)", "DEFAULT 'workbuddy'")
        _ensure_index("model_configs", "ix_model_configs_platform", ("platform",))
        # 回填：列早于本次改造就已存在（旧版建过），因此 `_ensure_column` 不会
        # 补默认值，老行全是空串。不重建就无法按平台过滤号池。
        _backfill_platform()
        # 回填 usage_logs.platform：必须在 `_backfill_platform()` **之后**，
        # 因为它依赖 accounts.platform 已经被修正成具体平台名。
        _backfill_usage_platform()
        # 归一「区级平台名」-> storage 平台（trae-cn/intl -> trae），
        # 否则统计与筛选里会出现两条同名的「Trae 国内版」。
        _normalize_usage_platform_values()
        # 迁移：视频任务表（OpenAI /v1/videos 的对外 ID ↔ 上游任务 + 账号）。
        # 老库没有这张表时 create_all 会建；此处只为「表已存在但缺字段」的
        # 半升级状态兜底（历史版本曾在同一路径上改过列）。
        _ensure_column("video_tasks", "usage_log_id", "INT", "DEFAULT 0")
        _ensure_column("video_tasks", "credits", "FLOAT", "DEFAULT 0")
        _ensure_column("video_tasks", "progress", "INT", "DEFAULT 0")
        _ensure_column("video_tasks", "result_json", "TEXT", "NULL")
        _ensure_column("video_tasks", "expires_at", "DATETIME", "NULL")
        _ensure_column("video_tasks", "completed_at", "DATETIME", "NULL")
        _ensure_index("video_tasks", "ix_video_tasks_key_created", ("api_key_id", "created_at"))
        _ensure_index("video_tasks", "ix_video_tasks_status_created", ("status", "created_at"))
        # 迁移：内置智能体的会话与媒体资产。create_all 会建表，这里补索引
        # （老库升级时表可能已存在但缺索引）。
        _ensure_index("agent_conversations", "ix_agent_conv_key_updated",
                      ("api_key_id", "updated_at"))
        # 会话的思考设置与置顶：老库升级时补列。
        _ensure_column("agent_conversations", "reasoning", "VARCHAR(16)", "DEFAULT ''")
        _ensure_column("agent_conversations", "thinking", "INT", "NULL")
        _ensure_column("agent_conversations", "pinned_at", "DATETIME", "NULL")
        _ensure_index("agent_assets", "ix_agent_asset_key_created",
                      ("api_key_id", "created_at"))
        _ensure_index("agent_assets", "ix_agent_asset_conv", ("conversation_id",))
        # Ownership must have a database constraint, including concurrent creates.
        next(i for i in models.ApiKey.__table__.indexes
             if i.name == "ux_api_keys_account").create(bind=engine, checkfirst=True)

        # 迁移：使用记录/日志的查询索引。
        #
        # 为什么必须在迁移里建而不是只靠 create_all：老库的表已经存在，
        # create_all 对已存在的表**什么都不做**，索引永远建不上，
        # 「按账号查询慢」的根因就一直在。这里显式补齐（幂等）。
        #
        # 这几个复合索引的列序都是「等值过滤列 + created_at」：
        # 过滤完直接沿索引倒序取前 N 行，省掉回表排序 ——
        # 这正是使用记录分页从「先全表过滤再 filesort」变成「索引扫描」的关键。
        _ensure_index("usage_logs", "ix_usage_logs_created_id", ("created_at", "id"))
        _ensure_index("usage_logs", "ix_usage_logs_account_created", ("account_id", "created_at"))
        _ensure_index("usage_logs", "ix_usage_logs_key_created", ("api_key_id", "created_at"))
        _ensure_index("usage_logs", "ix_usage_logs_model_created", ("model", "created_at"))
        _ensure_index("usage_logs", "ix_usage_logs_error_created", ("error_kind", "created_at"))
        _ensure_index("usage_logs", "ix_usage_logs_ip_created", ("client_ip", "created_at"))
        _ensure_index("usage_logs", "ix_usage_logs_account_id", ("account_id", "id"))
        _ensure_index("usage_logs", "ix_usage_logs_key_id", ("api_key_id", "id"))
        _ensure_index("usage_logs", "ix_usage_logs_model_id", ("model", "id"))
        _ensure_index("request_audits", "ix_request_audit_ip_started", ("client_ip", "started_at"))
        _ensure_index("accounts", "ix_accounts_status", ("status",))
        _ensure_index("accounts", "ix_accounts_uid", ("uid",))
        _ensure_index("account_model_cool", "ix_account_model_cool_model_until", ("model", "until"))

        # 迁移：创建 system_settings / schedules 表（create_all 已处理，这里仅兜底）

    return _retry_db_startup("建表/迁移", _do)


def wait_database_ready() -> bool:
    """确保数据库存在（带重试）。返回 False 表示暂不可用，调用方降级启动。"""
    return _retry_db_startup("连接/建库", ensure_database)


def _backfill_usage_platform() -> None:
    """把 `usage_logs.platform` 的空值按账号归属回填（幂等）。

    这是**一次性历史修复**，不是运行时依赖：新日志由写入方直接带上
    `platform`。回填用 `account_id -> accounts.platform` 关联，
    关联不上的（账号已删）留空串 —— 它们不属于任何现存平台，
    统计时天然被排除，这正是我们要的语义。
    """
    try:
        with engine.begin() as conn:
            if engine.dialect.name == "sqlite":
                conn.execute(text(
                    'UPDATE usage_logs SET platform = COALESCE('
                    '(SELECT a.platform FROM accounts a WHERE a.id = usage_logs.account_id),'
                    " '') WHERE platform IS NULL OR platform = ''"))
            else:
                conn.execute(text(
                    "UPDATE `usage_logs` ul LEFT JOIN `accounts` a "
                    "ON a.id = ul.account_id "
                    "SET ul.platform = COALESCE(a.platform, '') "
                    "WHERE ul.platform IS NULL OR ul.platform = ''"))
    except Exception as e:  # noqa: BLE001 - 回填失败不该阻塞启动
        logger.warning("usage_logs.platform 回填失败（不影响启动）：%s", e)


def _normalize_usage_platform_values() -> None:
    """把 `usage_logs.platform` 里的「区级 id」收敛成 storage 平台（幂等）。

    Trae 的两个区共用一张账号池与一张模型表，但早期网关把 `trae-cn` /
    `trae-intl` 直接写进了 platform 列。结果是统计里出现两条同名
    「Trae 国内版」，平台筛选也分裂成两个桶。这里按 registry 的
    `id -> storage()` 关系一次性修正历史行；新行由 `shared.record_usage`
    在写入时就归一。
    """
    try:
        from admin.platform import registry

        pairs = []
        for spec in registry.all_platforms(enabled_only=False):
            storage = str(spec.storage() or "").lower()
            ident = str(spec.id or "").lower()
            if storage and ident and ident != storage:
                pairs.append((ident, storage))
        if not pairs:
            return
        with engine.begin() as conn:
            for ident, storage in pairs:
                if engine.dialect.name == "sqlite":
                    conn.execute(text(
                        "UPDATE usage_logs SET platform = :storage WHERE platform = :ident"),
                        {"storage": storage, "ident": ident})
                else:
                    conn.execute(text(
                        "UPDATE `usage_logs` SET `platform` = :storage "
                        "WHERE `platform` = :ident"),
                        {"storage": storage, "ident": ident})
    except Exception as e:  # noqa: BLE001 - 回填失败不该阻塞启动
        logger.warning("usage_logs.platform 归一失败（不影响启动）：%s", e)


def _backfill_platform() -> None:
    """把 platform 为空串的历史行回填成 workbuddy（幂等）。

    为什么必须单独回填：`_ensure_column` 只在**列不存在**时执行 ALTER，
    而 `platform` 列在更早的版本里就已经建过（无默认值），于是老行全是空串。
    空串会让「按平台过滤号池」把现有账号全部过滤掉 —— 表现就是升级后
    所有请求都报「无可用账号」，这是最坏的回归形态，因此这里强制收敛。

    `schedules` 同理：定时任务若 platform 为空，`_platform_of()` 会回落
    workbuddy，但显式回填能让界面直接显示归属，不必靠代码兜底。
    """
    targets = (("accounts", "platform", "workbuddy"),
               ("schedules", "platform", "workbuddy"),
               ("model_configs", "platform", "workbuddy"))
    for table, column, value in targets:
        try:
            with engine.begin() as conn:
                if engine.dialect.name == "sqlite":
                    conn.execute(text(
                        f'UPDATE "{table}" SET {column}=:v '
                        f"WHERE {column} IS NULL OR {column}=''"), {"v": value})
                else:
                    conn.execute(text(
                        f"UPDATE `{_safe_ident(table)}` SET `{_safe_ident(column)}`=:v "
                        f"WHERE `{_safe_ident(column)}` IS NULL OR `{_safe_ident(column)}`=''"),
                        {"v": value})
        except Exception as e:  # noqa: BLE001 - 回填失败不该阻塞启动
            logger.warning("%s.%s 回填失败（不影响启动）：%s", table, column, e)


def _ensure_column(table: str, col: str, col_type: str, default: str = ""):
    """检查并添加缺失的列（MySQL 兼容）。表名/列名经白名单校验；其余参数走绑定。"""
    try:
        if engine.dialect.name == "sqlite":
            exists = any(c["name"] == col for c in inspect(engine).get_columns(table))
            if not exists:
                with engine.begin() as conn:
                    conn.execute(text(f'ALTER TABLE "{_safe_ident(table)}" '
                                      f'ADD COLUMN "{_safe_ident(col)}" {col_type} {default}'))
            return
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:tbl AND COLUMN_NAME=:col"
                ),
                {"tbl": table, "col": col},
            )
            exists = result.scalar() or 0
            if not exists:
                # 表名/列名为编译期常量，经白名单校验后安全内插；col_type/default 亦为常量
                conn.execute(
                    text(
                        f"ALTER TABLE `{_safe_ident(table)}` "
                        f"ADD COLUMN `{_safe_ident(col)}` {col_type} {default}"
                    )
                )
                conn.commit()
    except Exception:
        pass  # 非 MySQL 或权限不足时静默跳过


def _ensure_index(table: str, index: str, columns: tuple[str, ...]) -> bool:
    """确保复合索引存在（幂等）。返回 True 表示本次新建。

    为什么必须显式建：`Base.metadata.create_all()` 对**已存在**的表不做任何事，
    所以老库上新加的索引永远不会生效 —— 这正是「按账号查询使用记录很慢」
    在升级后依然慢的原因。这里走 INFORMATION_SCHEMA 判断后 `CREATE INDEX`。

    索引名与列名都经 `_safe_ident` 白名单校验（只允许字母数字下划线），
    杜绝拼接注入；无法执行的方言（如 SQLite）静默跳过，不影响启动。
    """
    if not columns:
        return False
    try:
        if engine.dialect.name == "sqlite":
            with engine.begin() as conn:
                cols = ", ".join(f'"{_safe_ident(c)}"' for c in columns)
                conn.execute(text(f'CREATE INDEX IF NOT EXISTS "{_safe_ident(index)}" '
                                  f'ON "{_safe_ident(table)}" ({cols})'))
            return True
        with engine.connect() as conn:
            exists = conn.execute(
                text(
                    "SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:tbl AND INDEX_NAME=:idx"
                ),
                {"tbl": table, "idx": index},
            ).scalar() or 0
            if exists:
                return False
            cols = ", ".join(f"`{_safe_ident(c)}`" for c in columns)
            conn.execute(
                text(f"CREATE INDEX `{_safe_ident(index)}` "
                     f"ON `{_safe_ident(table)}` ({cols})")
            )
            conn.commit()
            logger.info("已创建索引 %s(%s)", index, ",".join(columns))
            return True
    except Exception as e:
        # 权限不足 / 方言不支持 / 并发建索引冲突：都不该让服务启动失败
        logger.warning("创建索引 %s 失败（不影响启动）：%s", index, e)
        return False