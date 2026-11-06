"""ORM 模型：账号、API Key、用量日志。"""
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, Float, Index, Integer,
                      String, Text)

from admin.db import Base


class Account(Base):
    """一个上游平台的登录态凭据（默认 WorkBuddy / CodeBuddy 的 .info）。"""

    __tablename__ = "accounts"
    # 选号热路径索引：每次代理请求都要按 status + 余额 + 冷却扫描候选池。
    # 号池通常只有几十行，但 idx_status 让「活跃账号」这条永不消失的过滤
    # 不必全表扫，冷启动与批量任务下更稳。
    __table_args__ = (
        Index("ix_accounts_status", "status"),
        Index("ix_accounts_uid", "uid"),
        # 多平台：号池扫描恒带 platform 条件，单列索引避免跨平台全表扫。
        Index("ix_accounts_platform", "platform"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    #: 所属平台（见 admin/platform）。历史行为空，读取时一律按 workbuddy 解释；
    #: 迁移会回填，因此这里给默认值而不是 nullable。
    platform = Column(String(24), default="workbuddy")
    name = Column(String(120), nullable=False, default="")
    uid = Column(String(120), default="")
    enterprise_id = Column(String(120), default="")
    domain = Column(String(120), default="")
    auth_json = Column(Text, nullable=False)  # 原始 .info 内容（含 token）
    status = Column(String(16), default="active")  # active | disabled
    balance_total = Column(Integer, default=0)
    balance_remain = Column(Integer, default=0)
    last_sync_at = Column(DateTime, nullable=True)
    #: 余额快照的「上次刷新时间」（2026-11-06）。
    #:
    #: 为什么要和 `last_sync_at` 分开：`last_sync_at` 是「任何一次同步」，
    #: 包括导入时的探活、签到回写、客户端刷新 —— 用它判断「余额是不是
    #: 该刷了」会把刚导入的号也算成需要刷新。
    #:
    #: 这个字段**只由余额刷新任务写**，所以 `balance_synced_at` 落后
    #: N 分钟 = 这个号确实 N 分钟没刷过余额。几千个号的池子必须靠它
    #: 做「只刷 stale 的」，否则每轮全量重扫永远刷不完。
    balance_synced_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    # 稳定性状态机：错误计数 / 冷却 / 防撞号 / 禁用原因
    err_count = Column(Integer, default=0)          # 连续上游 5xx 计数
    cool_until = Column(DateTime, nullable=True)    # 冷却截止时间
    cool_kind = Column(String(16), default="")       # hard_credit | soft_rate | error_threshold | not_found
    last_err_at = Column(DateTime, nullable=True)
    last_err_msg = Column(String(255), default="")
    last_upstream_error = Column(Text, default="")
    max_in_flight = Column(Integer, nullable=True, default=None)  # null=global, 0=unlimited
    selection_priority = Column(Integer, nullable=True, default=None)
    last_picked_at = Column(DateTime, nullable=True)  # 最近一次被选中（仅观测；防撞号窗口已移入进程内存）
    # 熔断：连续失败达阈值后指数退避，封顶 breaker_cooldown_max。
    # 与 cool_until 是**或门**关系（任一未到期即不可选），生效的是更晚的那个。
    breaker_until = Column(DateTime, nullable=True)
    breaker_fails = Column(Integer, default=0)
    # 连续 12153「session dead」计数：达到阈值才禁用。
    # 一次就禁用等于误杀 —— 该错误在真实环境会被临时性触发（上游抖动/并发刷新）。
    session_dead_fails = Column(Integer, default=0)
    # 连败降权（传输层抖动与上游内部故障）：
    # 带权威分类的错误各有自己的惩罚，不喂这个计数器，避免重复计罚。
    consecutive_fails = Column(Integer, default=0)
    degrade_until = Column(DateTime, nullable=True)
    #: 快过期积分（供三因子权重的「快过期先用」项）。
    credits_expiring = Column(Integer, default=0)
    # ---- 积分到期口径（详见 admin/credits.py）----------------------------
    #: 7 天内到期的剩余额度合计。与 credits_expiring 的区别：后者是历史字段，
    #: 窗口口径曾为 30 天；本列严格按 EXPIRING_SOON_DAYS(=7) 统计，
    #: 是「再不烧就作废」的那部分，也是选号的首要依据。
    credits_expiring_soon = Column(Integer, default=0)
    #: 所有**还有剩余**的积分包里最早的到期时间（真实到期，非占位值）。
    #: 为空表示手里全是长期有效额度（没有作废风险）。
    credits_soonest_expire_at = Column(DateTime, nullable=True)
    #: 长期有效（无到期时间）的剩余额度合计。
    credits_evergreen = Column(Integer, default=0)
    #: 已过期但账面仍有剩余的额度（上游扣减延迟时出现），仅供对账。
    credits_expired = Column(Integer, default=0)
    #: 逐包快照明细（JSON 文本，按到期升序）。存库而不是每次现查，
    #: 是为了让「最近快到期的积分包」这类展示与统计不必对上游逐号发请求。
    credits_snapshot = Column(Text, default="")
    #: 快照采集时间，用于展示「N 分钟前更新」并判断数据是否可信。
    credits_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AccountModelCooldown(Base):
    """账号×模型级的冷却（与账号级冷却**互相独立**）。

    为什么必须独立（上游 code 6004「该模型使用量超限」）：
      * 账号级冷却会让「换个模型就能用」的号被整体摘出池子 —— 明明切模型立即可用，
        却白扔一个账号直到冷却结束；
      * 反过来，若把模型级限流当成账号级，用户会遇到「限额后换不动号」。

    所以 6004 只冷却**触发调用的那个模型**：该账号对其他模型照常可选，
    而对同一模型则被 `_select_account` 跳过。

    code 11102「该后端无此模型」复用同一张表：它不是限流而是确定性答复
    （重试无意义），所以 TTL 更长（默认 6h）且按指数退避。
    """

    __tablename__ = "account_model_cool"
    __table_args__ = (Index("ix_account_model_cool_model_until", "model", "until"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, nullable=False, index=True)

    model = Column(String(120), nullable=False, default="")
    until = Column(DateTime, nullable=True)          # 冷却截止
    kind = Column(String(24), default="")            # model_daily | model_rate | model_queue | model_block
    reason = Column(String(255), default="")
    upstream_error = Column(Text, default="")
    reset_source = Column(String(24), default="")
    hits = Column(Integer, default=0)                # 命中次数（model_block 指数退避用）
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ApiKey(Base):
    """对外共享的 API Key，带积分限额。"""

    __tablename__ = "api_keys"
    __table_args__ = (Index("ux_api_keys_account", "account_id", unique=True),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), default="")
    key_hash = Column(String(128), unique=True, nullable=False)
    key_prefix = Column(String(16), default="")  # 展示用前缀
    key_full = Column(String(2048), default="")  # 完整密钥（仅管理后台查看用，base64 编码存储）
    credit_limit = Column(Float, default=0)  # 限额（credits）；unlimited=True 时忽略
    credit_used = Column(Float, default=0)
    unlimited = Column(Integer, default=1)  # New keys default to unlimited; existing limits are preserved.
    # 该 Key 并发上限：NULL=跟随全局 ADMIN_GATEWAY_MAX_CONCURRENT；0=不限；N=最多 N 路在途
    # 注意：这是准入层限制，与账号层 ADMIN_POOL_MAX_IN_FLIGHT（防单号雪崩）相互独立。
    max_concurrent = Column(Integer, nullable=True, default=None)
    # NULL = global requests/minute; 0 = unlimited (independent of concurrency).
    rate_per_minute = Column(Integer, nullable=True, default=None)
    status = Column(String(16), default="active")  # active | revoked
    note = Column(String(255), default="")
    # 绑定的模型分组 id；0 = 未绑定（可用全部启用模型）。分组被删时按 0 处理（自动降级）
    group_id = Column(Integer, default=0)
    # NULL = public pool; a reservation survives revocation until explicitly released.
    #: 是否走「聚合模型」路由（默认开）。
    #:
    #: 开：`/v1/models` 返回跨平台聚合列表，调用聚合名可以由网关
    #:     挑任意平台转发（多平台负载分摊）。
    #: 关：只返回该平台自己的模型，行为与以前一致。
    aggregate = Column(Boolean, default=True, nullable=False, server_default="1")

    account_id = Column(Integer, nullable=True, default=None)
    #: 多选独占账号（JSON 数组的 id 列表）。**优先于 `account_id`**。
    #:
    #: 非空时只用这些账号；为空则回落到 `account_id`（单账号旧行为）。
    #: 用 JSON 而不是关联表：绑定数量极少（通常 1-5 个），
    #: 关联表会让 `scope_accounts` 这类查询复杂化。
    account_ids = Column(Text, default="")
    #: 该 Key 绑定的平台（空 = 不限制，按请求自报平台路由）。
    #: 绑定后请求方无需再带 X-Platform，且不会被跨平台借用额度。
    platform = Column(String(24), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ModelGroup(Base):
    """模型分组：一组模型的命名集合，供 API Key 绑定以限制可用模型范围。"""

    __tablename__ = "model_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False, default="")
    note = Column(String(255), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ModelGroupItem(Base):
    """分组内的模型成员（一个分组多条）。"""

    __tablename__ = "model_group_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, nullable=False, index=True)
    model_id = Column(String(120), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class UsageLog(Base):
    """每次代理调用的用量记录（用于管理后台审计）。

    记录内容：调用方 API Key、实际使用的上游账号、模型、积分消耗、token 明细，
    以及发起请求的真实客户端 IP（来自 X-Forwarded-For / X-Real-IP / 直连 socket），
    便于风控对账与上游客用途日志对齐。
    """

    __tablename__ = "usage_logs"
    # 索引说明（都是为「使用记录 / 日志」的筛选与排序服务的）：
    #   ix_usage_logs_created_id  —— 覆盖「按时间倒序分页」这条**永不消失**的主查询。
    #       原来是 PRIMARY(id) 倒序扫：日志一多，`ORDER BY id DESC LIMIT 20`
    #       在有 where 条件时仍要走全表再排序；有了 (created_at, id) 复合索引，
    #       时间范围过滤 + 倒序取前 N 条能直接在索引上完成，回表只取 20 行。
    #   ix_usage_logs_account_created —— 「按账号查询」的专用索引（本次新增需求）。
    #       账号维度是排查「某个号今天花了多少」最常用的入口，
    #       单列 account_id 索引仍要排序，复合索引把过滤 + 排序一次做完。
    #   ix_usage_logs_key_created / ix_usage_logs_model_created 同理，覆盖按 Key/模型筛选。
    __table_args__ = (
        Index("ix_usage_logs_created_id", "created_at", "id"),
        Index("ix_usage_logs_account_created", "account_id", "created_at"),
        Index("ix_usage_logs_key_created", "api_key_id", "created_at"),
        Index("ix_usage_logs_model_created", "model", "created_at"),
        Index("ix_usage_logs_error_created", "error_kind", "created_at"),
        Index("ix_usage_logs_ip_created", "client_ip", "created_at"),
        # **概览聚合专用覆盖索引**（2026-10-31 新增）。
        #
        # 首页概览要按时间范围做 4 个 SUM/COUNT 聚合：
        #     SELECT COUNT(*), SUM(prompt_tokens), SUM(completion_tokens),
        #            SUM(total_tokens), SUM(cached_tokens), SUM(credits)
        #     FROM usage_logs WHERE created_at >= ?
        #
        # 只有 `(created_at, id)` 时，每匹配一行都要**回表**读那 5 个字段。
        # 30 天窗口命中 22 万行 = 22 万次随机回表，线上实测 107ms/次。
        # 把聚合用到的列全部放进索引后，MySQL 可以走**覆盖索引**（Using index），
        # 不再回表 —— 这是减少扫描量最直接的一招。
        #
        # 列顺序有讲究：`created_at` 必须在最前（范围条件），
        # 其余列只被 SUM 读取，顺序不影响走索引，按业务可读性排。
        Index("ix_usage_logs_overview", "created_at", "error_kind",
              "prompt_tokens", "completion_tokens", "total_tokens",
              "cached_tokens", "credits"),
        # The list endpoint orders by the monotonic primary key. These
        # companions avoid a filesort when a filtered page uses keyset/ID
        # pagination.
        Index("ix_usage_logs_account_id", "account_id", "id"),
        # **积分统计 / 按账号汇总的覆盖索引**（2026-11-01 新增）。
        #
        # 这两处的 SQL 形状都是
        #     SELECT account_id|model, COUNT(*), SUM(credits), SUM(total_tokens)
        #     FROM usage_logs WHERE created_at >= ? AND error_kind IN ('','success')
        #     GROUP BY account_id|model ORDER BY SUM(credits) DESC
        # 只用 `(account_id, created_at)` 时 `credits/total_tokens/error_kind`
        # 都不在索引里，22 万行要逐行回表 —— 线上实测 `/api/credits/usage`
        # 单次 **12.7 秒**（打开积分统计页就是白屏十几秒）。
        # 把聚合列全放进索引变成覆盖扫描后，实测两条 SQL 各 0.25~0.43 秒。
        Index("ix_usage_logs_account_credits", "account_id", "created_at",
              "error_kind", "credits", "total_tokens"),
        Index("ix_usage_logs_model_credits", "model", "created_at",
              "error_kind", "credits"),
        # **日志汇总的完全覆盖索引**（2026-11-01 新增）。
        #
        # `/api/logs/summary` 与 `/api/credits/usage` 都是
        #     SELECT account_id, COUNT(*), SUM(credits), SUM(total_tokens),
        #            SUM(prompt_tokens), SUM(completion_tokens),
        #            SUM(cached_tokens), AVG(latency_ms), MAX(created_at)
        #     FROM usage_logs WHERE created_at >= ? GROUP BY account_id
        # 之前的索引只覆盖到 credits/total_tokens，`prompt/completion/cached/
        # latency_ms` 全部要**回表**取 —— 30 天口径 22 万行逐行回表，
        # 线上实测 logs/summary 922ms、credits/usage 566ms。
        # 把聚合用到的列全部放进索引后（本地 42 万行实测）：
        #     logs/summary  710ms → 172ms（4.1x）
        #     credits/usage 680ms → 133ms（5.1x）
        # 列顺序：account_id（分组键）→ created_at（范围）→ 其余按聚合顺序。
        Index("ix_usage_logs_acct_cov", "account_id", "created_at",
              "error_kind", "credits", "total_tokens", "prompt_tokens",
              "completion_tokens", "cached_tokens", "latency_ms"),
        Index("ix_usage_logs_key_id", "api_key_id", "id"),
        Index("ix_usage_logs_model_id", "model", "id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    api_key_id = Column(Integer, nullable=False, default=0)
    account_id = Column(Integer, nullable=False, default=0)
    model = Column(String(120), default="")
    credits = Column(Float, default=0)
    # 详细用量：优先取上游 usage 字段；缺省为 NULL
    prompt_tokens = Column(Integer, nullable=True, default=None)
    completion_tokens = Column(Integer, nullable=True, default=None)
    total_tokens = Column(Integer, nullable=True, default=None)
    cached_tokens = Column(Integer, nullable=True, default=None)
    # 与 IP 封禁一致：仅信任已配置代理提供的转发头，直连使用 socket 地址。
    client_ip = Column(String(64), default="")
    # 这条请求走的是哪个平台（落库平台名：workbuddy / trae / …）。
    #
    # 日志面板默认展示**全部平台**的请求，没有这一列就无法回答
    # 「同一个模型名，这条是走哪个上游的」—— 而两者的计费口径完全不同。
    # 写入时由网关填；历史行（列新增之前）为空，读取时用账号归属兜底。
    platform = Column(String(24), default="", index=False)
    # 用途标识（透传给上游的 X-Agent-Purpose），便于风控审计与上游请求用量对齐
    use_case = Column(String(64), default="")
    reasoning_effort = Column(String(32), default="")  # Requested effort; empty means model default.
    # 请求级表格日志字段（ logging）：TTFB / 总耗时 / 序号 / 错误分类
    seq = Column(Integer, default=0)
    ttfb_ms = Column(Integer, nullable=True, default=None)
    latency_ms = Column(Integer, nullable=True, default=None)
    error_kind = Column(String(32), default="")  # hard_credit | soft_rate | server | not_found | session_dead | transport | client | success
    diagnostic_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class IPRequestBucket(Base):
    """Hourly ingress counters, including requests rejected before authentication."""
    __tablename__ = "ip_request_buckets"
    __table_args__ = (Index("ix_ip_request_bucket_hour", "hour"),)
    ip = Column(String(64), primary_key=True)
    hour = Column(DateTime, primary_key=True)
    requests = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    blocked = Column(Integer, nullable=False, default=0)
    first_seen = Column(DateTime, nullable=False)
    last_seen = Column(DateTime, nullable=False)


class BlockedIP(Base):
    """Manually blocked public client IPs.

    The gateway checks this table before API-key authentication, so a blocked
    client cannot keep probing keys or spend account-pool capacity.
    """

    __tablename__ = "blocked_ips"
    __table_args__ = (
        Index("ux_blocked_ips_ip", "ip", unique=True),
        Index("ix_blocked_ips_enabled", "enabled"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    ip = Column(String(64), nullable=False)
    enabled = Column(Integer, nullable=False, default=1)
    note = Column(String(255), default="")
    # 封禁到期时间（UTC）。NULL = 永久封禁。
    #
    # 为什么要这个字段：自动封禁必须有兜底时效。手动封禁是人工判断，永久合理；
    # 自动封禁基于启发式规则（请求频率、401 率），一定会有误判——
    # 没有过期时间就意味着一次误判永久锁死一个正常客户端，而且没人记得去解。
    # 到期后由 `is_blocked_ip()` 视为未封禁，无需额外清理任务。
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProxyEndpoint(Base):
    """出站代理节点（**按并发槽分配，避免一个 IP 打太多号**）。

    ## 为什么必须要有这张表（2026-11-06）

    用户实测反馈：**一个出口 IP 请求多个账号，IP 会被上游拉黑**。
    而账号池动辄几百上千，全从本机一个 IP 出去，等于把「一个 IP 上
    有 N 个账号在活动」这件事直接写在脸上。

    有了这张表之后：
      · 每个节点声明自己允许多少并发（`max_concurrency`）；
      · 任务按「节点数 × 单节点并发」分配线程，一个节点同时只跑 N 个号；
      · 某节点被拉黑（403 / 11140 连坐）时置 `blocked_until`，
        期间不再派发，其它节点继续 —— 不会因为一个 IP 挂掉整批停摆。

    `url` 形如 `http://user:pass@host:port` 或 `socks5://host:port`；
    支持用 `{session}` 占位符表示「每个并发槽一个会话」（轮换型代理常见）。
    """

    __tablename__ = "proxy_endpoints"
    __table_args__ = (
        Index("ix_proxy_endpoints_enabled", "enabled"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    #: 展示名（如「机房A-01」「住宅-上海」）
    label = Column(String(64), default="")
    #: 代理地址；`{session}` 会被替换成随机串（轮换型代理）
    url = Column(String(512), nullable=False)
    enabled = Column(Integer, nullable=False, default=1)
    #: 这个出口允许的同时并发数。住宅代理通常 1~2，机房可以 5~10。
    max_concurrency = Column(Integer, nullable=False, default=1)
    #: 归属组（空 = 默认组）。任务可以选择用哪一组出口。
    pool = Column(String(32), default="")
    #: 最近一次成功 / 失败的时间与原因（排障用）
    last_ok_at = Column(DateTime, nullable=True)
    last_error = Column(String(255), default="")
    #: 被上游拉黑后的静默期（UTC）。期间不派发。
    blocked_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class JoinInvite(Base):
    """自助注册入口的**后台生成凭证**（邀请码）。

    背景：`/join` 与 `/api/join/*` 曾是完全公开的入口，攻击者据此把带 XSS
    载荷的自有账号塞进号池（2026-11-02 入侵）。现在入口改为 fail-closed：
    后台一张有效凭证都没有 → 整个自助入口关闭。

    安全设计：
      * `code_hash` 存 PBKDF2 哈希，**不存明文** —— 库被拖走也换不出邀请码。
      * `code_prefix` 只留前 8 位供后台肉眼识别（够排查，不够用）。
      * `max_uses` 限次（0 = 不限），`expires_at` 限时间，`enabled` 可随时撤销。
    """

    __tablename__ = "join_invites"

    id = Column(Integer, primary_key=True, autoincrement=True)
    #: PBKDF2 哈希，明文只在生成时返回一次。
    code_hash = Column(String(255), nullable=False, index=True)
    #: 明文前 8 位，仅供后台识别与日志排查。
    code_prefix = Column(String(16), nullable=False, default="")
    #: 备注（谁用这张码、给谁的）。
    note = Column(String(200), default="")
    #: 最大可用次数。0 = 不限次数（不推荐）。
    max_uses = Column(Integer, nullable=False, default=1)
    used_count = Column(Integer, nullable=False, default=0)
    #: 1 = 可用；0 = 已撤销。
    enabled = Column(Integer, nullable=False, default=1)
    #: 过期时间（UTC）。NULL = 不过期（不推荐，后台生成时会强制加上限）。
    expires_at = Column(DateTime, nullable=True)
    #: 生成者标识（后台账号名），便于追责与审计。
    created_by = Column(String(120), default="")
    #: 代次。任何一次 join 总闸 toggle 都会 +1，旧代次的邀请码立即失效。
    #: 为什么用代次而不是逐条删：漏删一条就是一个长期后门，代次只增不减，
    #: 天然不会残留（见 admin/join_gate.py）。
    epoch = Column(Integer, nullable=False, default=1, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class JoinTicket(Base):
    """join 入口的**一次性访问票据**（只存 jti，明文只出现在签发链接里）。

    职责分离：
      * 票据（本表）：只回答「能不能打开 /join 页面」，一次性、短 TTL；
      * 邀请码（JoinInvite）：只回答「能不能完成注册」，放在 POST body / 头。

    票据明文走 URL 的 `#fragment`，不随 HTTP 请求上送 —— 不进 nginx access
    log、不进 Referer、不进浏览器历史里的完整 URL。
    """

    __tablename__ = "join_tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    jti = Column(String(64), nullable=False, unique=True, index=True)
    epoch = Column(Integer, nullable=False, default=1, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_by = Column(String(120), default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class AdminSession(Base):
    """后台会话行：JWT 只是运输载体，**权威状态在这张表**。

    为什么必须落库
    --------------
    纯无状态 JWT 有两个做不到的事：
      1. **真实退出** —— JWT 签出去就收不回来，logout 只能靠前端删本地副本；
      2. **一人一份** —— 同一个令牌可以无限复制，谁拿到谁就是管理员。

    落库之后：`logout` = 置 revoked_at；换人登录 = 吊销该账号的全部旧会话；
    Cookie 被复制到另一台机器 = 设备绑定不匹配，直接拒。

    安全细节
    --------
    * `token_hash` 存 SHA-256 而不是明文：库被拖走也无法反推出可用令牌；
    * `bind_hash` = SHA-256(设备标识 Cookie)，不落原始设备 ID；
    * 每次校验都过一遍 `revoked_at IS NULL AND expires_at > now`，不依赖时钟容差。
    """

    __tablename__ = "admin_sessions"
    __table_args__ = (
        Index("ix_admin_sessions_subject_active", "subject", "revoked_at"),
        Index("ix_admin_sessions_expires", "expires_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    #: 会话标识，写进 JWT 的 `sid` 声明。
    sid = Column(String(64), nullable=False, unique=True, index=True)
    subject = Column(String(120), nullable=False, default="")
    #: 完整 JWT 的 SHA-256。同一条令牌只能对应一行，换 token 即换会话。
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    #: 设备绑定摘要（空串 = 未绑定，仅限 CLI/本地壳）。
    bind_hash = Column(String(64), nullable=False, default="")
    ip = Column(String(64), default="")
    ua_hash = Column(String(64), default="")
    fp = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    last_seen_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String(48), default="")


class ClientIdentity(Base):
    """Client-reported identifiers; never a verified hardware identity."""
    __tablename__ = "client_identities"
    kind = Column(String(20), primary_key=True)
    digest = Column(String(64), primary_key=True)
    value = Column(String(128), nullable=False)
    blocked = Column(Integer, nullable=False, default=0)
    note = Column(String(255), default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ClientIdentitySeen(Base):
    __tablename__ = "client_identity_seen"
    __table_args__ = (Index("ix_client_identity_seen_ip", "ip", "key_id"),)
    kind = Column(String(20), primary_key=True)
    digest = Column(String(64), primary_key=True)
    ip = Column(String(64), primary_key=True)
    key_id = Column(Integer, primary_key=True)
    first_seen = Column(DateTime, nullable=False)
    last_seen = Column(DateTime, nullable=False)


class RequestAudit(Base):
    """Request outcomes, including admission rejections and client disconnects.

    Kept separate from billable usage: these rows never debit credits or imply
    that an upstream model was called. No request content or credentials.
    """
    __tablename__ = "request_audits"
    __table_args__ = (
        Index("ix_request_audit_key_started", "api_key_id", "started_at"),
        Index("ix_request_audit_started", "started_at"),
        Index("ix_request_audit_ip_started", "client_ip", "started_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(32), nullable=False)
    api_key_id = Column(Integer, nullable=False)
    account_id = Column(Integer, nullable=True)
    client_ip = Column(String(64), default="")
    path = Column(String(80), nullable=False)
    user_agent = Column(String(512), default="")
    model = Column(String(120), default="")
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=False)
    status = Column(Integer, nullable=True)
    outcome = Column(String(32), nullable=False)
    reason = Column(String(64), default="")
    phase = Column(String(80), default="")
    elapsed_ms = Column(Integer, nullable=False)
    upstream_attempts = Column(Integer, default=0)


class ModelConfig(Base):
    """模型白名单配置（系统级 / 用户级）。"""
    __tablename__ = "model_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    level = Column(String(16), default="system")  # system | user
    #: 该模型属于哪个平台。
    #: 模型表**保持公共一张表**（不同平台各自多少模型，但配置入口统一），
    #: 只加一个平台列用于分组展示 —— 界面按平台分组，才看得出「这堆模型
    #: 到底哪个平台的」，否则几十个模型混在一起根本分不清。
    platform = Column(String(24), default="workbuddy")
    model_id = Column(String(120), nullable=False)  # 模型 ID，如 "deepseek-v4-flash"
    enabled = Column(Integer, default=1)  # 0/1
    note = Column(String(255), default="")
    credit_multiplier = Column(Float, default=0)  # 积分消耗倍率；0=免费模型
    credits_raw = Column(String(120), default="")  # 原始 credits 字符串（如 "x0.05" / "x0.00 credits"）
    description = Column(Text, default="")  # 官方模型说明，独立于用户备注
    upstream_metadata = Column(Text, default="{}")  # 官方活动规则 / 名称，保留完整时间配置
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class VideoTask(Base):
    """OpenAI `/v1/videos` 异步任务的本地映射。

    为什么必须落库：OpenAI 的视频 API 是**异步任务**语义，`POST /v1/videos`
    只是入队，客户端之后会带着 ``video_id`` 来轮询、下载。上游的任务 ID 已经
    包含账号归属（实测形如 ``1312831544-WandVideo-...``，前缀就是 uid），
    所以下载时**必须**用同一个账号的凭证去取，否则签名 URL 与鉴权都对不上。
    网关又不持久化「任务 → 账号」的任何信息就无法在重启后继续服务轮询，
    因此这里保存最小必要映射：任务号、模型、账号、状态、上游原始 ID。

    不保存 prompt 正文与视频内容：提示词属于调用方内容，用量日志已有可选
    的请求内容缓存（`admin/request_payload.py`，默认关闭）；视频二进制从不
    落库，只保存上游返回的签名下载地址（本身会过期，过期后按上游错误处理）。
    """

    __tablename__ = "video_tasks"
    __table_args__ = (
        Index("ix_video_tasks_key_created", "api_key_id", "created_at"),
        Index("ix_video_tasks_status_created", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    #: 对外的视频 ID（OpenAI 语义的 `video.id`）。随机串，不暴露上游 ID。
    video_id = Column(String(64), nullable=False, unique=True)
    api_key_id = Column(Integer, nullable=False, default=0)
    account_id = Column(Integer, nullable=False, default=0)
    model = Column(String(120), nullable=False, default="")
    #: queued | in_progress | completed | failed
    status = Column(String(24), nullable=False, default="queued")
    progress = Column(Integer, nullable=False, default=0)
    #: 上游任务 ID（轮询/下载都要用它）。
    upstream_id = Column(String(255), default="")
    #: 秒数 / 分辨率等**已校验并规范化**的请求参数（JSON）。
    request_json = Column(Text, default="{}")
    #: 上游完成后的下载结果（JSON：{"url": ...} 或错误说明）。
    result_json = Column(Text, default="{}")
    error_code = Column(String(64), default="")
    error_message = Column(String(512), default="")
    credits = Column(Float, default=0)
    #: 创建任务时写入的用量日志行（在途时 credits=0）。完成后再按上游
    #: 真实 usage.credit 回填同一条，避免一次生成在账本里出现两行。
    usage_log_id = Column(Integer, default=0)
    expires_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AgentConversation(Base):
    """智能体会话：一段可恢复的多轮上下文。

    与 `/v1/chat/completions` 的区别在于**状态归属**：网关是无状态代理，
    上下文由客户端维护；智能体是内置页面，上下文必须由服务端保存并在
    刷新/重启后恢复，否则「聊到一半刷新就丢失」根本没法用。

    这里的 `messages` 保存完整对话（含工具调用与工具结果），因为它就是
    下一轮要发给上游的输入 —— 单独拆表反而要每次重新拼装。体积上限由
    写入端（`agent.py`）控制：超长上下文会做截断并保留摘要标记。
    """

    __tablename__ = "agent_conversations"
    __table_args__ = (
        Index("ix_agent_conv_key_updated", "api_key_id", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    #: 对外会话 ID（前端直接用，不暴露自增主键语义）。
    conversation_id = Column(String(64), nullable=False, unique=True)
    api_key_id = Column(Integer, nullable=False, default=0)
    title = Column(String(200), default="")
    model = Column(String(120), default="")
    #: 会话模式：chat | image | video（决定 composer 的默认形态）。
    mode = Column(String(16), default="chat")
    #: 思考设置随会话保存：刷新/换设备回来还是同一套。
    #: `thinking` 为 NULL 表示「跟随模型默认」，不是「关闭」——两者必须可区分。
    reasoning = Column(String(16), default="")
    thinking = Column(Integer, nullable=True)
    #: 置顶时间（NULL = 未置顶）。置顶会话排在最前，与更新时间无关。
    pinned_at = Column(DateTime, nullable=True)
    messages = Column(Text, default="[]")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AgentAsset(Base):
    """智能体产出的媒体（图片 / 视频 / 合并结果）的元数据登记。

    文件本体落 `admin/data/agent_assets/`，这里只存指针与索引。这样做的好处：
      * 列表页不需要扫目录；
      * 能按 Key/会话隔离，避免别人猜到文件名就读到内容；
      * 清理过期资产有明确依据（`expires_at`）。
    """

    __tablename__ = "agent_assets"
    __table_args__ = (
        Index("ix_agent_asset_key_created", "api_key_id", "created_at"),
        Index("ix_agent_asset_conv", "conversation_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_id = Column(String(64), nullable=False, unique=True)
    api_key_id = Column(Integer, nullable=False, default=0)
    conversation_id = Column(String(64), default="")
    #: image | video
    kind = Column(String(16), default="image")
    model = Column(String(120), default="")
    file_name = Column(String(200), default="")
    mime = Column(String(80), default="")
    bytes = Column(Integer, default=0)
    duration_s = Column(Float, default=0)
    prompt = Column(Text, default="")
    credits = Column(Float, default=0)
    meta_json = Column(Text, default="{}")
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class GrowthPlanRecord(Base):
    """成长巡检/手动领奖的逐账号持久记录，不存凭证或上游原始响应。"""
    __tablename__ = "growth_plan_records"
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, nullable=False, index=True)
    source = Column(String(24), default="schedule")
    action = Column(String(40), default="巡检")
    ok = Column(Integer, default=1)
    credit = Column(Integer, default=0)
    result_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class CreditEvent(Base):
    """Confirmed rewards and observed balance changes are separate ledgers."""
    __tablename__ = "credit_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_key = Column(String(160), nullable=False, unique=True)
    account_id = Column(Integer, nullable=False, index=True)
    account_name = Column(String(120), default="")
    kind = Column(String(24), nullable=False, index=True)
    source = Column(String(120), default="", index=True)
    delta = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class SystemSetting(Base):
    """简单的键值配置（同步地址 / 密钥 / 其他开关）。"""

    __tablename__ = "system_settings"

    key = Column(String(120), primary_key=True)
    value = Column(Text, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Schedule(Base):
    """后台定时任务（如：整点刷新平台总积分、每日同步模型列表）。"""

    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), default="")
    task = Column(String(40), default="refresh_balances")  # refresh_balances | sync_models | daily_checkin
    #: 该任务作用于哪个平台（空 = 沿用旧行为，只跑 workbuddy）。
    #: 为什么必须显式落库：定时任务遍历的是「所有 active 账号」，
    #: 平台一多就会把 Trae 的号也塞进 WorkBuddy 的签到/成长任务里，
    #: 轻则报错刷日志，重则把上游风控引到无辜账号上。
    platform = Column(String(24), default="workbuddy")
    interval_minutes = Column(Integer, default=60)
    timing_config = Column(Text, default="")  # 空值保留系统预设；JSON 为用户执行周期
    enabled = Column(Integer, default=1)  # 0/1
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    last_result = Column(Text, default="")  # 上次运行结果摘要

    # ---- 出口代理与并发（2026-11-06）----
    #
    # 用户实测：一个出口 IP 请求多个账号，IP 会被上游拉黑。
    # 而池子动辄几百上千，全从本机一个 IP 出去等于自曝。
    #
    # 为什么挂在任务上而不是全局：签到和余额刷新的节奏完全不同 ——
    # 签到可以慢慢来（当天跑完就行，1~2 并发），余额刷新要快但不能把
    # IP 打死（多出口 + 适度并发）。一个全局值必然顾此失彼。
    #: 用哪一组代理节点（`proxy_endpoints.pool`）。空 = 直连。
    proxy_pool = Column(String(32), default="")
    #: 并发数。0 = 自动（按「可用节点数 × 单节点并发」算，最少 1）。
    concurrency = Column(Integer, default=0)
    #: 单轮时间预算（分钟）。0 = 用任务默认。到点就停，剩下的下一轮继续 ——
    #: 这是「余额刷新卡死/无限循环」的正解：**不追求一轮刷完**。
    time_budget_minutes = Column(Integer, default=0)
    #: 只刷「快照超过这么多分钟没更新」的号。0 = 用任务默认。
    #: 全量重刷在几千个号时永远刷不完，而且对刚刷过的号是纯浪费。
    stale_minutes = Column(Integer, default=0)
    # 停止领取时间（仅 daily_checkin 任务使用）：到达该时间后不再执行领取请求，
    # 避免活动下线后继续请求触发上游风控。可由活动 end_time 预填或运行中发现 EventEnded 自动写入。
    stop_after = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)