# 调度策略调整建议（计划 §七 第 93 行交付物）

- **状态**：建议稿，未实施。**独立排期，不得与「平台作用域模型解析」串名修复同批上线**（理由见 §5）。
- **素材来源**：pr/qimei-guard-rotation 工作树（HEAD a6c1ed7 + 本分支未提交改动）；任务 t3 / t10 的度量与探针结论。
- **写稿时间**：2026-11-23
- **度量工具**：scripts/bench_dispatch_hotpath.py（离线：临时 SQLite + 进程内假上游；四条路径、冷/热分开、模型表两个指标分开）；策略行为探针见附录 A。
- **行号约定**：以写稿时工作树为准。admin/platform/dispatch.py 仍在被并行修改，行号可能漂移 —— 引用时**以函数名为准**，行号只作索引。

## 0. 本文不涉及（已经落地，不再建议）

- 跨平台模型名错发（dispatch 在**最终目标平台内**还原模型名）—— 已修，本分支自带回归用例。
- 候选硬限制（禁用/暂停/归属/账号×模型冷却逐项过滤，排序不得把被排除者加回来）—— 已修。
- 错误分类（无人登记 404 / 声明者全禁用 403 / 有模型但无可用账号 503）—— 已修。
- gateway_router 的池边界语义（None=全体、[]=空）、health() 的可用数口径、throughput 排序键的 None 兜底 —— 已落地且有回归测试，本文**不再改动**。

本文只谈三件会改变「哪个平台接流量」的策略选择。

## 1. 现状与度量

### 1.1 round_robin：按「分钟数取模」

- 代码：admin/platform/gateway_router.py:456-462，核心在 459 行

      slot = int(time.time() // 60) % len(pool)
      pool = pool[slot:] + pool[:slot]

- 调用链：请求 → dispatch._strategy_pick_within（admin/platform/dispatch.py:1554）→ gateway_router.pick（gateway_router.py:480）→ order(db, ids)（gateway_router.py:316）。
- 关键事实：pick() 直接算 order()，**没有缓存**；带缓存的是 cached_pick()（gateway_router.py:518，CACHE_TTL=5.0 在 490），而它只服务「无头分摊」分支（dispatch._strategy_pick，dispatch.py:1678）。另一条入口 dispatch._platform_order（dispatch.py:512）带 3 秒缓存（_ORDER_TTL，dispatch.py:107），但只用于候选排序，不决定首选。
- 实测（附录 A 探针，3 个健康平台、每平台 1 个可用号）：

  | 观测 | 结果 |
  |---|---|
  | 同一分钟内连续 60 次 order()[0] | workbuddy 60/60，distinct = 1 |
  | 固定时刻 +0 / +60 / +120 秒 | workbuddy → alpha → beta |
  | 剔除 beta 后连续 20 次 | workbuddy 20/20，distinct = 1 |

- 结论：轮转粒度是**分钟**。一分钟内的所有请求拿同一个首选，整分钟时硬切一次。对一分钟几十上百个请求的网关，这等于「分钟级粘性 + 每分钟换一次平台」，不是「各平台平均分摊」。
- 对照：random 策略（gateway_router.py:464-467）每请求真随机，没有这个问题；priority 由显式优先级决定，也不受影响。

### 1.2 throughput：按「可用账号数」排序

- 代码：gateway_router.py:441-454；排序键在 449 行

      pool.sort(key=lambda i: (-(i.get("usable") or 0), _priority_key(i)))

  reason 在 454 行：格式串 "%d 个可用账号"，值取 (item.get("usable") or 0)。
- usable 的来源：gateway_router.health（gateway_router.py:212），口径是「status=active 且 cool_until 未到期」的行数（本分支刚修正过）。它**完全不看容量和在途**。
- 实测（附录 A 探针）：beta 有 3 个号（每个号 max_in_flight=1），把 3 个号全部 lease 打满后：

      POOL.available(beta_rows) = 0
      POOL.inflight.count(每个 beta 号) = 1
      order() 结果 = [("beta", "3 个可用账号"), ("workbuddy", "1 个可用账号")]

  即「可用槽位为 0」的平台依然排第一，reason 仍写「3 个可用账号」。
- 结论：函数注释里写的「在途少的先」（442 行）与实际排序键不一致 —— 实际语义是**号多的先**。可用 slot 的数据在进程里是有的（admin/pool.py:249 InFlight；PoolRuntime.inflight，pool.py:970-1005），只是 order() 没读。

### 1.3 balance / expiring：默认平台恒置顶

- 代码：
  - balance：gateway_router.py:409-424，排序键首元素在 416-420 行 = "0 if i['platform'] == default_platform else 1"；
  - expiring：gateway_router.py:426-440，排序键首元素在 431-435 行同形；
  - default_platform 取 registry.DEFAULT_PLATFORM（gateway_router.py:388）。
- 配套：_priority_key（gateway_router.py:390-399）第二项也是 is_default —— 即 priority 策略下，未显式设优先级的平台里默认平台也排最前。
- 实测（附录 A 探针，同一份数据）：

  | 策略 | 全池 | 把默认平台从池里拿掉（order(db, [alpha, beta])） |
  |---|---|---|
  | balance | workbuddy「剩余 1%」第一，alpha「剩余 90%」第二 | alpha「剩余 90%」第一 |
  | expiring | workbuddy「无临期额度」第一，alpha「最近到期 11-25」第二 | alpha 第一 |

- 结论：显式打开 balance/expiring 后，两个策略的**主目标**（额度多先用 / 快过期先用）在默认平台上被架空 —— 默认平台哪怕只剩 1% 额度、没有任何临期额度，也仍然排第一。
- 为什么这样写（不是笔误）：注释 412-415 / 429-430 记的是真实事故 —— dispatch.resolve（dispatch.py:183）对「无头请求」回落默认平台；若默认平台被策略顶掉，老客户端用默认平台的模型名就会打到别的平台，全都 404，而且日志里看不出原因（请求是成功的）。所以置顶是**为兼容付的代价**，不是性能选择。
- 现状的收敛边界：通用名（auto/default 等，dispatch.py:93 _GENERIC_MODEL_NAMES）根本不走策略分摊（dispatch.py:1668 _spread_unbound 只在「有模型且不是通用名」时才用策略），所以「无头请求」里只有**带模型名且允许聚合**的那部分真正受策略影响 —— 这正是建议三要动的范围。

## 2. 建议一：round_robin 改成「稳定排序后的合格集合 + 可并发递增游标」

### 2.1 方案

- 输入不动：order() 已经把「合格集合」算好（禁用/不可用/池边界在 gateway_router.py:366-384 过滤），策略分支只在池内重排。
- 只把 459 行的 slot 计算换成进程内游标（示意，**本文不实施**）：

      _rr_lock = threading.Lock()
      _rr_cursor = 0                 # 进程内单调递增

      ...
      global _rr_cursor
      with _rr_lock:
          slot = _rr_cursor % len(pool)
          _rr_cursor += 1
      pool = pool[slot:] + pool[:slot]

  旋转语义与 reason 文案（「轮转位次 N」）都不变，只是「位次推进」从「时钟」换成「请求数」。
- **必须先归一再轮转**：游标只有在「池顺序稳定」时才有意义。当前 order() 的池顺序来自调用方传入的 platform_ids（_strategy_pick_within 传的是候选顺序，_platform_order 传的是 registry.ids() 顺序），两个入口顺序可能不同 —— 同一个游标值在不同入口会指向不同平台。落地时应在 **round_robin 分支内**先用 _priority_key 排序归一，再按游标旋转（不动其它策略分支）。
- 游标重置：建议在配置失效入口（gateway_router.invalidate，gateway_router.py:498）里清零，与 _cache 代次保持一致 —— 否则改完策略后从旧位次接着转，用户会看到「刚切换就还是老平台先」。

### 2.2 状态边界（必须写进发布说明）

| 部署形态 | 均匀性 | 备注 |
|---|---|---|
| 单进程 | 精确 | 60 次分布严格 20/20/20 |
| 多 worker（uvicorn --workers N / 多容器） | 大致均匀 | 每进程各自 1/N 轮转；全局请求计数不精确，最坏瞬时两个 worker 选到同一平台 |
| 共享计数器（Redis INCR） | 精确 | **不建议**：热路径多一次网络往返。本仓库 bench 的本地假上游 p50 是 0.55-5.87 ms（附录 A），同城 Redis RTT 约 0.3-1 ms，占比可观且引入新故障点 |

### 2.3 验证方法（可直接落成用例）

1. 3 个健康平台（各 1 个可用号），连续 60 次取 order(db)[0]['platform'] → 断言每个平台出现 **20±2** 次。
2. 剔除其中 1 个（order(db, [p1, p2])）再连续 20 次 → 断言只剩这两个平台、各 **10±2** 次。
3. 现状对照（红灯）：同一段代码今天跑出来是 60/60 同一平台、20/20 同一平台（§1.1 实测表），所以上面两条断言在现状下**必红**，正好当 TDD 的红灯。
4. 不回归：random / priority 分支的既有断言不许变；池边界断言已有覆盖（tests/test_gateway_reliability_scheduling.py 里的 test_order_pool_boundary_holds_for_every_strategy），轮转断言加在同一文件。

## 3. 建议二：throughput 改用「可用槽位 = Σ(max_in_flight − in_flight)」

### 3.1 已有统计能力（已核对代码，不是推测）

- admin/pool.py:249 class InFlight：键是 account_key(account)（pool.py:183-246，形如 uid:<uid> 或 id:<id>），只有 count（290）/ full（294）/ snapshot（320）三个读法，**没有 storage/realm 维度**。
- admin/pool.py:1003-1005 PoolRuntime.limit_for(account)：账号级 max_in_flight 优先，为空回落到全局 POOL.max_in_flight（默认值来自 ADMIN_POOL_MAX_IN_FLIGHT，pool.py:227，默认 3）。
- PoolRuntime.snapshot()（pool.py:1047）直接把 inflight.snapshot() 吐出来，键是账号身份 —— 说明**数据本身能按 storage+realm 聚合**，但需要网关层做一次「账号键 → (storage, realm)」映射（读 accounts 表或复用现有账号缓存）。仓库里目前**没有**这个聚合 helper：grep inflight 的消费者（admin/platform/、admin/routers/、admin/*.py）都是**按账号**取 count/full，没有按平台的求和。

### 3.2 方案

- **首选（能按区聚合时）**：slots(platform) = Σ over 该平台（区）的账号：(limit_for(account) − POOL.inflight.count(key_for(account)))，下限 0；排序键改为 (-slots, _priority_key)；reason 改成「N 个可用槽位」。
- **退路（不能按区聚合时）**：只做 storage 级近似 —— 同一 storage 的 cn/intl 共用一个槽位总数；并在 reason 里**明写口径**（例如「3 个可用槽位（storage 级）」）。现状 reason 只有「N 个可用账号」，读者看不出是哪种口径，这一点必须补。
- **回退条件**：若「slots 计算」让 bench 的确定性指标变差（每条路径的 SQL/请求 或 排名/请求 上升，或 p95 抬头），就退回现状的 usable 排序，不要带着未验证的统计上生产。判定用 scripts/bench_dispatch_hotpath.py 的两个模型表指标 + 排名次数，不看单次毫秒。

### 3.3 对比口径（怎么证明改对了）

同一份固定数据跑三组，三组都要写进用例：

| 组 | 数据 | 现状 | 改动后期望 |
|---|---|---|---|
| A 号多但打满 | beta 3 个号全 lease 满（每号 limit=1），workbuddy 1 个号空闲 | beta 第一（**错**，§1.2 实测） | workbuddy 第一 |
| B 号多且空闲 | beta 3 号空闲，workbuddy 1 号空闲 | beta 第一 | beta 第一（不能把「号多的优先」整个改掉） |
| C 在途均等 | 各 1 号、都在途 1/2 | 平手时按 _priority_key（默认平台优先） | 一致，行为可解释 |

## 4. 建议三：balance / expiring 的置顶按流量类型区分

### 4.1 方案（不改策略默认行为）

- 给 order() 增加**显式开关**：order(db, platform_ids=None, *, include_unavailable=False, pin_default=None)
  - pin_default=None（默认）：现状行为逐字节不变（416-420 / 431-435 的置顶照旧）；
  - pin_default=False：跳过置顶元素，让默认平台参与额度/到期排序；
- 由调用方按流量类型传：
  - **显式模型归属的聚合流量**（dispatch._route_by_model_ownership → _strategy_pick_within，dispatch.py:1554）：传 False；
  - **无头通用名 / 无模型归属**的路径（dispatch._strategy_pick → cached_pick，dispatch.py:1678）：保持 None。
- 为什么不由 order() 自己判断：order() 只拿到 platform_ids，没有「这个请求是否显式指定了模型」的信息。在内部猜等于把两个语义重新糊在一起 —— 历史上正是这么糊的（见 412-415 行的事故注释）。

### 4.2 上线前必须分别测的两组

1. **显式开 balance 的聚合流量**：两个平台登记同一模型，默认平台额度 1%、另一平台 90%，请求不带 X-Platform、Key 未绑定、聚合开 → 期望落到 90% 那台（默认平台不再置顶）。
2. **老客户端通用名流量**：model=auto（dispatch.py:93）→ 期望**仍然**固定默认平台；这条即使 pin_default=False 也不该变（通用名不参与策略分摊）。
3. 不回归：priority 策略的置顶来自 _priority_key 的 is_default，本建议不动它。

### 4.3 风险（这条是行为变更，不是性能优化）

- 显式开了 balance/expiring 后，默认平台若额度低，无头（带模型名、允许聚合）的流量会开始落到别的平台；若那个平台没有该模型，就是 404。所以必须：单独开关 + 灰度 + 上面两组数据都过。

## 5. 风险与排期

| # | 建议 | 兼容风险 | 回退点 | 建议顺序 |
|---|---|---|---|---|
| 1 | round_robin 游标 | 低（只动 round_robin 分支；多 worker 全局不精确已声明） | 恢复 459 那一行即可 | 第 1 批（最小、最容易验证） |
| 2 | throughput 可用槽位 | 中（要新增统计；口径错会让流量偏向号少的平台） | 恢复 449 / 454 两行，或加开关回落到 usable | 第 2 批（先出 §3.3 三组数据） |
| 3 | balance/expiring 按流量类型 | **高**（真实行为变更，可能 404） | 调用方不传 pin_default=False 即恢复旧行为 | 第 3 批（独立开关 + 灰度） |

**为什么不能与本次串名修复同批上线**：三条建议都会改变「哪个平台接流量」，而串名修复的价值在于「请求落到正确平台后模型名仍然正确」。

- 同批上线后一旦出现 404 / 落错平台，无法区分是「调度把流量挪到了不该去的平台」还是「模型名映射没修好」；
- 两边的回退会互相牵连：回退调度 → 串名修复的验证样本（落点、调用列表）全部失真；回退串名 → 调度改动失去对照基线；
- 计划 §七 第 93 行也明确写了「以上是调度策略选择，单独提交，勿与串名修复绑定上线」。

## 6. 未决问题（需要产品 / 运维拍板）

1. **多 worker 各自计数**带来的轻微不均匀（最坏瞬时两个 worker 选同一平台）能否接受？不接受就要评估共享计数器（Redis INCR）的 RTT 成本 —— 当前 bench p50 是 0.55-5.87 ms/路径（附录 A）。
2. **租约统计要不要按 storage+realm 聚合**？要做就得先定口径：cn/intl 共用一份号池时，是按账号的 domain 分，还是按平台 spec.realm 分？不定就按 storage 级近似并在 reason 里注明。
3. **显式开 balance/expiring 时，默认平台是否允许被顶掉**？这决定 pin_default 的默认值，也决定是否需要灰度开关（甚至先做成配置项）。
4. **round_robin 游标要不要在配置变更（invalidate）时重置**？建议重置（与 _cache 代次对齐），否则切完策略还从旧位次转。
5. **throughput 的「0 = 不限」语义**：PoolRuntime.limit_for（pool.py:1003-1011）先取 max(0, ...)，base <= 0 时**直接返回 0**，而全仓库对 0 的解释是「不限」（见 models.Account.max_in_flight 的 null=global / 0=unlimited 约定与 pool.py:988 的默认值 0）。计算 slots 时必须把「不限」当成无穷而不是 0，否则会把最宽松的号算成最没槽位。需要确认生产里是否真的存在这个配置值。

## 附录 A：度量口径与复现方式

- **基准脚本**：scripts/bench_dispatch_hotpath.py（本分支已交付）
  - 冷启动 + 预热后 100 次；四条路径（默认直达 / 固定后缀 / 自动聚合 / 一次 503 后重试）；
  - 指标：SQL/请求、**cfg 语句/请求**（涉及 model_configs 的条数）、**真·全表/请求**（无 WHERE 者）、排名/请求、健康度/请求、p50/p95；
  - 判定器已修（2026-11-23）：真·全表只数「无 WHERE」的语句。历史报表里的「cfg 全表 3.00/1.00/2.00/2.00」是被污染的旧口径，**不能引用**；
  - 写稿时实测（warm=100，当前工作树）：

        default_direct    SQL 6.00   cfg 0.00   真全表 0.00  排名 0.00  p50 1.54  p95 2.95
        suffix_fixed      SQL 1.00   cfg 1.00   真全表 0.00  排名 0.00  p50 0.55  p95 0.88
        auto_aggregate    SQL 22.00  cfg 0.00   真全表 0.00  排名 1.00  p50 5.16  p95 7.08
        retry_after_503   SQL 23.00  cfg 0.00   真全表 0.00  排名 1.00  p50 5.87  p95 9.55

    （auto / retry 两条路径每次请求都要排名一次 —— 这是策略被真正调用的地方，也是本轮三条建议的影响面。）
- **策略探针**（一次性，未入库；临时 SQLite + 三个固定平台 workbuddy/alpha/beta + 进程内假上游）：
  - round_robin：把 gateway_router.time 换成固定时刻的 shim，连续取 order()[0]；跨分钟用 +60 / +120 秒；
  - throughput：把 beta 的 3 个号各设 max_in_flight=1 后逐一 POOL.acquire_account 打满，再取 order()；
  - balance：改 accounts.balance_total / balance_remain 造 1% vs 90%；
  - expiring：改 accounts.credits_expiring_soon / credits_soonest_expire_at 造临期额度。
- **本文引用的全部实测数字**（写稿时工作树）：

  | 观测 | 数字 |
  |---|---|
  | round_robin 同一分钟 60 次首选 | 60/60 workbuddy（distinct=1） |
  | round_robin +0/+60/+120 秒 | workbuddy / alpha / beta |
  | round_robin 两平台 20 次 | 20/20 workbuddy |
  | throughput beta 3 号打满 | POOL.available(beta)=0，order()[0] 仍是 beta，reason「3 个可用账号」 |
  | balance 全池 | workbuddy「剩余 1%」第一；去掉默认平台后 alpha「剩余 90%」第一 |
  | expiring 全池 | workbuddy「无临期额度」第一；去掉默认平台后 alpha「最近到期 11-25」第一 |
