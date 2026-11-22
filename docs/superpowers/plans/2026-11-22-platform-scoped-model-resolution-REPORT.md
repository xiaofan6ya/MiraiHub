# 平台作用域模型解析　验收与交接报告（任务 6 / §八 收口）

- 验收人：verifier（独立验收，不改 `admin/` 实现、不改他人测试断言）
- 验收时间：2026-11-23
- 仓库：`MiraiHub`，分支 `pr/qimei-guard-rotation`，HEAD `a6c1ed7`
- 工作树：除本计划改动外另有 30+ 个与本次无关的未提交改动（`main.py`/`desktop/`/`admin/server.py` 等），本报告只对计划涉及的 14 个文件负责，未回滚、未清理、未 `git add`
- 环境：Python 3.14.7，无 venv；全部离线，临时 SQLite + 假上游；未执行任何写工作树的 git 命令（仅 `rev-parse`/`status`/`diff`/`show`/`archive`）
- 一次性探针：`%TEMP%\miraihub_t7_probe.py`、`miraihub_t7_probe2.py`（跑完已删，仓库无残留）

---

## 一、结论（逐任务）

| 任务 | 结论 | 一句话 |
|---|---|---|
| 任务 1　入口回归（WorkBuddy 裸名/头/后缀/绑定 Key × 两种插入顺序） | **通过** | 6 个入口组合全部 200，接收器读到的都是目录原拼写 `deepseek-v4.1-flash`，`_wb_body` 与实收 JSON 一致，messages 原样 |
| 任务 2　只在最终目标内还原模型名（含两处全局 `upstream_of` 收窄） | **通过** | 生产路径已无 `models_router.upstream_of()` 调用；目标四元组映射覆盖 WorkBuddy/Marvis 两种目录/Qoder/Loomy/Trae 两区 |
| 任务 3　首选平台、候选与重试沿用同一套限制 | **通过** | 裸名聚合 A(503)→B(200) 依次尝试；后缀/头/查询参数只调 A；禁用/分组外/结果不确定/流断均不重放；候选截断到 3 后按实际列表判最后一跳 |
| 任务 4　管理台、聚合清单与调用名对齐 | **通过（带 1 条边界缺陷）** | 聚合 ID 恒为 `deepseek-v4.1-flash`、旧名 `deepseek-flash` 可解析、Qoder 专名可用、`group_models` key 为稳定字符串；**分组登记字符串与目录行大小写不一致时会丢成员 → 全 403（F6）** |
| 任务 5　调度统计与热路径 | **通过（策略调整未做，按计划属独立建议）** | `health()` 按行统计可用数、`order()` 容忍 `usable=None`；预热后每请求 SQL 15→6 / 7→1 / 52→22 / 52→23，真·全表读取 warm 0/0/0/0 |
| 任务 6　回归、验收与发布后核对 | **部分通过** | 7 条测试命令全绿；`git diff --check` 因**无关文件** `admin/console/console.css` 退出 2；11 条 findings（最高 medium），无 blocker |

**总体：可以交付，建议按第五节的批次上线；`F4`（bench 被 .gitignore 吞掉）必须在提交前处理。**

---

## 二、证据表

> 「旧值」= 计划 §一 记录的修复前现象（本轮不可重测，因为不能回退工作树）；「新值」= 本轮独立探针 + 测试实测。
> 旧值来源：计划 §一 表格与各任务汇报；**不引用**旧 bench 判定器的「全表 3.00/1.00/2.00/2.00」（口径错误，见第三节）。

| # | 任务 | 旧值（修复前） | 新值（本轮实测） | 实际落点 | 调用顺序 | 对应测试名 |
|---|---|---|---|---|---|---|
| 1 | 任务1 裸名 + `X-Platform: workbuddy` | `marvis_first` 顺序下主应用读到 `main-auto` | `received=deepseek-v4.1-flash`、`cached=deepseek-v4.1-flash`、`cached==received=true`、`messages` 原样（两种顺序一致） | workbuddy | 主应用 1 次 | `test_bare_model_with_platform_header[marvis_first\|workbuddy_first]` |
| 2 | 任务1 仅 `-workbuddy` 后缀 | 主应用读到 `main-auto`；后缀入口 `scope["_wb_body"]` 仍带后缀原名 | 6 个组合全部：`received=deepseek-v4.1-flash`、`cached==received`、`x-gateway-model` 回显原始请求名 | workbuddy | 主应用 1 次 | `test_model_with_workbuddy_suffix`（2 顺序）、`test_key_bound_to_workbuddy` |
| 3 | 任务1 插入顺序互换 | 两顺序结果不同（`marvis_first` 错发） | 两顺序结果逐字段相同（探针 key 级对比）；顺序确定行迭代器下也相同 | workbuddy | 主应用 1 次 | `test_entry_result_ignores_row_iteration_order[3 入口]`、`test_platform_scoped_mapping_is_row_order_independent` |
| 4 | 任务3 显式目标 + 明确 503 | 后缀只调 A，但**头/查询/禁用 B 仍会调 B**（旧逻辑让禁用被绕过） | 裸名聚合：loomy 1 次(503) → qoder 1 次(200)，`loomy_models=['DeepSeek-V4.1-Flash']`、`qoder_models=['dfmodel']`；仅后缀/仅头/仅查询：loomy 1 次、qoder **0 次**、终态 503；禁用 B：qoder **0 次**、503 | 裸名→qoder；显式→loomy | `[loomy,qoder]` / `[loomy]` / `[loomy]` / `[loomy]` / `[loomy]` | `test_aggregate_retries_next_qualified_platform_on_explicit_503`、`test_explicit_target_keeps_its_503_and_never_tries_others[3]`、`test_disabled_platform_is_not_retried`、`test_group_denied_platform_is_not_retried`、`test_uncertain_platform_error_is_not_replayed`、`test_retry_attempts_are_capped_at_three_usable_targets` |
| 5 | 任务4 聚合公开 ID | 加入 Qoder `dfmodel` 后 `build_payload(aggregate_only=True)` 的公开 ID 从 `deepseek-v4.1-flash` 变成 `deepseek-flash` | `published_ids == ['deepseek-v4.1-flash']`（WorkBuddy + Qoder 两平台）；`deepseek-flash` → workbuddy 200；`deepseek-flash-qoder` → qoder，上游收 `dfmodel` | workbuddy / qoder | 单平台 1 次 | `test_aggregate_id_and_legacy_alias_both_reach_workbuddy`、`test_qoder_readable_suffix_forwards_platform_id`、`test_multi_member_group_publishes_single_public_id`、`test_multi_member_group_roundtrip_stays_inside_group` |
| 6 | 任务4 `group_models()` 组 key | 同一平台的 V4 Pro 与 V4.1 Flash 都返回元组 `("workbuddy","cn")`（JSON 成数组） | 2 组，key 均为 `str`：`deepseekv4pro` / `deepseekv41flash`，互不相同；`platforms` 明细保留（多区可展开） | —（纯函数） | — | `test_group_models_returns_distinct_string_keys` |
| 7 | 任务5 `health()` 可用数 | `active=1, cooling=1, usable=0, ok=False`（非 active 冷却号抵消健康号） | `accounts=2, active=1, cooling=1, usable=1, ok=True`；另 `active=2,cooling=2,usable=1`、全 active 冷却 `usable=0,ok=False`、cn/intl 分区分计 | —（纯函数） | — | `test_health_usable_counts_healthy_active_despite_non_active_cooling`、`test_health_usable_is_not_active_minus_cooling`、`test_health_all_active_cooling_means_unusable`、`test_health_realm_split_does_not_cancel_other_realm` |

---

## 三、热路径测量（前后对照）

**方法**（可复现，未用历史报表）：

- 「后」= 当前工作树跑 `python -B scripts/bench_dispatch_hotpath.py --json`
- 「前」= `git archive HEAD` 导出到 `%TEMP%\miraihub_head_bench`（HEAD `a6c1ed7` 全量代码）+ 同一份 bench 脚本，在该目录里跑同一命令 —— 同一台机、同一份 fixture（4 平台 / 128 条 `model_configs` / 7 账号 / 4 条路径 / warm=100 / 假上游、零公网）
- 每侧跑 2 次；SQL/ranking 计数两次完全一致，毫秒取两次范围
- 指标口径：`cfg语句` = 涉及 `model_configs` 的 SQL 条数；`真·全表` = 其中**无任何 WHERE** 的条数（`_is_full_table_scan`）

### warm（100 次均值）

| 路径 | SQL/请求 前→后 | cfg 语句/请求 前→后 | 真·全表/请求 前→后 | 排名/请求 前→后 | health/请求 前→后 | p50(ms) 前→后 | p95(ms) 前→后 |
|---|---|---|---|---|---|---|---|
| default_direct | **15 → 6** | **3.0 → 0.0** | 0.0 → 0.0 | 0 → 0 | 0 → 0 | 4.15 → **1.28**（run2 1.41） | 5.24 → **1.42**（1.80） |
| suffix_fixed | **7 → 1** | 1.0 → 1.0 | 0.0 → 0.0 | 0 → 0 | 0 → 0 | 1.75 → **0.39**（0.42） | 1.99 → **0.44**（0.66） |
| auto_aggregate | **52 → 22** | **2.0 → 0.0** | 0.0 → 0.0 | 1 → 1 | 3 → 3 | 11.53 → **3.96**（4.65） | 14.48 → **5.44**（6.30） |
| retry_after_503 | **52 → 23** | **2.0 → 0.0** | 0.0 → 0.0 | 1 → 1 | 3 → 3 | 11.33 → **4.38**（4.65） | 13.49 → **5.30**（6.23） |

### cold（清缓存后第 1 次）

| 路径 | SQL 前→后 | cfg 语句 前→后 | 真·全表 前→后 |
|---|---|---|---|
| default_direct | 28 → 19 | 4 → 1 | 0 → 0 |
| suffix_fixed | 21 → 4 | 3 → 3 | 1 → 1 |
| auto_aggregate | 66 → 36 | 3 → 1 | 0 → 0 |
| retry_after_503 | 66 → 37 | 3 → 1 | 0 → 0 |

### 口径校准（必须写清，避免过度宣称）

1. **「预热后全表读取 = 0」这个目标已达成（0.0/0.0/0.0/0.0），但修复前预热后同样是 0.0**：旧判定器把「带 WHERE 的多行 SQL」误判成全表读取，t3 的 `3.00/1.00/2.00/2.00` 是**误报**，不能当作「全表读取从 3 降到 0」的证据。
2. 真正减少的是**每请求 model_configs 语句数**：`3/1/2/2 → 0/1/0/0`，总 SQL `15/7/52/52 → 6/1/22/23`；排名/健康度调用次数不变（0/0/1/1 与 0/0/3/3），符合「固定目标不排名、自动请求只排一次」的预期。
3. **cold 仍有 1 次真·全表**（suffix_fixed，前后都是 1）：`admin/platform/models_router.py:841` 的 `is_native_model_id` 自开 session 做 `db.query(ModelConfig.platform, ModelConfig.model_id).all()`，10 秒缓存。计划 §七 第 91 行的目标是「预热后为 0」，已满足；cold 项记入 F3。
4. 毫秒数只作同机对照：本机抖动约 ±10%，两轮范围已列出；不要把它们当容量结论。

---

## 四、findings

> 严重度：blocker / high / medium / low / info。**无 blocker**。修复建议只写在报告里，本轮未改任何实现。

| 编号 | 严重度 | 问题 | 建议修法 | 文件:行 |
|---|---|---|---|---|
| F1 | medium | `is_native_model_id()` 在 DB 不可达时 `return False`，与「确定不是原生 id」不可区分；`alias_to_upstream()` 据此把**原生 id 当别名改写**。实测（目录把可读名当原生行）：DB 正常 → `is_native=True`、`alias_to_upstream=("","")`（不改写）；DB 不可达 → `False`、`("marvis","main-auto")`（改写）→ 重定向层按目录校验会 404 | 返回值改三态（`True/False/None`）或抛异常，`alias_to_upstream` 在「未知」时 fail-closed（不改写）；只在查询**成功**时写负缓存（现在只缓存成功结果，这点是对的） | `admin/platform/models_router.py:845-847`、`admin/platform/model_suffix.py:109-110` |
| F2 | medium | 「额度未知 vs 真实零余额」在库层不可表达：`Account.balance_remain` 是 `Column(Integer, default=0)`，显式传 `None` 落库变 **0**（实测）；同步侧还普遍 `int(x or 0)`。选择层判据本身是对的（`IS NULL OR > min_balance`：NULL 放行、0 被挡），但只有历史/迁移写入的 NULL 行才能享受到 | 归 billing/同步侧：列改 `nullable=True, default=None`，同步写 `None` 表示未知；或新增 `balance_known` 标记列；`select_account`/`health` 展示分开「未知/零」 | `admin/models.py:35`、`admin/account_health.py:272`、`admin/routers/accounts.py:821,945`、`admin/routers/growth.py:519,601`、`admin/post_login.py:69`、`admin/platform/shared.py:766-767` |
| F3 | low | cold 启动有 1 次真·无 WHERE 的 `model_configs` 全表读取（`is_native_model_id`），warm 为 0（10s 缓存）。非 §七 目标（目标是预热后 0），但多 worker 重启时仍会各打一次 | 改成 `SELECT DISTINCT platform, model_id ... WHERE`？SQLite 无索引帮助有限；可改为按 storage 收窄的 `filter(ModelConfig.platform == storage)`（这正是函数唯一用途），顺带把返回结构从「全表 dict」改成「单平台 set」 | `admin/platform/models_router.py:841` |
| F4 | medium | `scripts/bench_dispatch_hotpath.py` **被 `.gitignore:88` 的 `scripts/*.py` 忽略且没有白名单**（`git check-ignore` 命中，`git ls-files` 不认），计划的热路径度量交付物不会随提交入库，随时可能丢 | 在 `.gitignore` 加 `!scripts/bench_dispatch_hotpath.py`（与 tests 白名单同一手法）后再提交 | `.gitignore:88`、`scripts/bench_dispatch_hotpath.py` |
| F5 | low | `_map_anthropic_model()` 仍用精确 `m in allowed` 判分组，未走分组等价解析：组里登记 `dfmodel` 时，Anthropic 客户端写 `deepseek-v4.1-flash` 会被判「不在组」→ 落到档位映射/auto | 该分支复用 `_resolve_group_model_alias`（proxy 已有）或 dispatch 的 `_group_allows`，保持「同一目录身份」语义 | `admin/routers/proxy.py:3020` |
| F6 | medium | 分组登记字符串与 `model_configs.model_id` **大小写不一致**时，`effective_group_models()` 的 `models & enabled` 是精确交集 → 成员被整组丢光，该 Key **所有**模型调用 403。实测：组 `DeepSeek-V4.1-Flash` + 目录 `deepseek-v4.1-flash` → `effective_group_models=[]`、请求 403 `model_not_in_group`；组内字符串与目录一致时 200 | 写入/同步分组时按目录原拼写归一；或在 `effective_group_models`/`_group_allows` 里先按 `(platform, canonical_key)` 把「同一行身份」映射到原拼写再精确判授权（注意别扩大到别行，t17 已证明大小写放宽会扩权，必须「同行归一」而不是「全组不区分大小写」） | `admin/routers/groups.py:53-54,68-69`、`admin/platform/dispatch.py:1445`、`admin/routers/proxy.py:2539` |
| F7 | info | 「谁都没登记」的模型名不会被 dispatch 判 404：默认平台交给主应用白名单（实测 200 + 主应用收到 `ghost-model-xyz`），显式非默认目标也会**原样转发**（实测 200、网关 1 次）。只有「模型存在于别的平台、目标平台没有」才是 404 `model_not_on_platform` | 文档化该语义（避免把「未知模型」和「未同步目录」混谈）；做「无分组」对照时用**存在于别处**的模型 | `admin/platform/dispatch.py:997-1012`（`_model_for_platform` 兜底分支） |
| F8 | low（环境基线） | `tests/test_trae_platform.py` 因本机无 MySQL 失败：`[FAIL] init_db 返回成功`（`pymysql 2003 127.0.0.1`，30 次建表重试后降级）并随后被未捕获 `OperationalError` 中断；与本计划无关 | 用 `ADMIN_DATABASE_URL` 指向临时 SQLite 同 test-harness 的做法修引导；或标记环境依赖 | `tests/test_trae_platform.py` |
| F9 | low（环境基线） | 多个 harness 塞进**同一 pytest 进程**会集体 `pymysql 2003`（实测 18 failed）：首个测试模块在设置 `ADMIN_DATABASE_URL` **之前**就 import 了 `admin.db`，引擎绑到真实 MySQL。单文件跑全绿 | 每个文件单跑（已写入 §八 命令）；若要合并跑，需要 `conftest.py` 里最早设置 `ADMIN_DATABASE_URL`，或各模块改用独立引擎而非全局 `SessionLocal` | `tests/test_gateway_reliability_*.py`、`tests/test_platform_model_routing.py`（模块级 env 赋值） |
| F10 | info | `git diff --check` 退出 2，**唯一命中**是 `admin/console/console.css:6029: new blank line at EOF`（与本次计划无关的并发改动）；本计划 14 个文件无空白错误 | 由该改动的作者修；本计划文件无需动作 | `admin/console/console.css:6029` |
| F11 | info | `models_router.upstream_of()` / `upstream_of_in_platform()` 在生产路径**已无调用者**（admin/ 下只出现在注释；`upstream_of_in_platform` 仅 `admin/platform/skill/scripts/check_hotpath.py` 引用）。这是「全局扫描」入口，留着容易被重新接线 | 标注 deprecated + 文档指向 `dispatch._model_id_in_target`；skill 脚本改调新入口后可删 | `admin/platform/models_router.py:1168,1238` |

---

## 五、未覆盖的真实平台与建议先部署的修复批次

### 5.1 未覆盖（本轮全部是本地假上游）

1. **真实 WorkBuddy 主应用**（`_validate_model` 之外的真实白名单/配额/风控）与真实 Marvis 槽位协议；
2. 真实 Qoder / Loomy / Trae 上游（含 Trae `:intl` 的真实路由与计费）；
3. 真实 MySQL / Redis（本报告全部 SQLite + 内存假网关）；
4. 真实账号池、账号×模型冷却在**跨进程**下的失效时序（本进程内 3s/10s 缓存已验，跨 worker 未验）；
5. 真实 11102 与真实 402（只有本地样本分类）；
6. 流式上游真实 chunk 时序、超长 body、客户端在平台调用中途断开；
7. `/v1/messages` 的 Anthropic 档位映射在真实分组下的行为（F5）；
8. 管理台 UI 与 `?detail=1` 的真实前端消费。

### 5.2 建议上线批次

| 批次 | 内容 | 为什么这样切 |
|---|---|---|
| **第 1 批（先上）** | 任务 1–2：`dispatch` 的「目标四元组映射 + 每次发送重新生成 body + 两处全局反查收窄」；`models_router` 的公开 ID/聚合命名；`model_alias.group_models` 字符串 key | 这是截图错发的直接原因，与调度策略解耦；回退时**保留平台内映射与显式目标限制**即可（计划 §八 第 122 行） |
| **第 2 批（紧随）** | 任务 3：候选硬限制、显式目标 pin 分组复核、`candidates_degraded` 分类、重试截断；任务 5：`health()` 按行统计、`order()` 的 `usable=None` 加固 | 与第 1 批同文件（`dispatch.py`）不同 hunk；若按文件提交，建议**同一 commit 分两个可审查段落**，不要整文件回滚 |
| **第 3 批（独立）** | `proxy.py` 的 `_resolve_group_model_alias` + `_pick_best_model` 分组分支（t8 收尾） | 单独 hunk，可独立回退；`proxy.py` 里混有并发无关改动，**禁止整文件回退** |
| **单独评估、勿与错发修复绑定** | 调度策略（`round_robin` 游标 / `throughput` 可用槽位） | 计划 §七 明说单独提交；本轮**未实施**，无需回退决策 |
| **上线前必须处理** | `.gitignore` 给 `scripts/bench_dispatch_hotpath.py` 加白名单（F4） | 否则交付物与复测脚本进不了仓库 |

**不建议**把任务 4 的「聚合命名」与第 2 批混在一个回退单元：聚合名是客户端可见契约，出问题要能单独回退（保留平台内映射）。

---

## 六、发布后核对清单（§八 第 121 行落地版）

1. **准备**：取两个既有测试 Key——一个绑定 WorkBuddy（或默认平台），一个绑定 Marvis；记录 Key id，不打印明文。
2. **同公开 ID 双平台**：分别用同一个公开 ID（`deepseek-v4.1-flash`）调 WorkBuddy 与 Marvis（各自带 `X-Platform` 或平台后缀），各 3 次：
   - 期望：`x-gateway-platform` = 目标平台；上游收到的分别是 WorkBuddy 目录拼写与 Marvis 槽位（`main-auto`，由网关换算）。
3. **允许聚合的裸名**：用开聚合、未绑平台的 Key 发裸名 `deepseek-v4.1-flash` 3 次：
   - 期望：落点在候选池内；若首选明确 503，则第二次落到下一个合格平台，且**两个接收端各自收到自己的拼写**。
4. **按请求 ID 配对**：对上面每次请求，取同一条日志/指标链，逐项核对：原始模型 / 目标平台 / 目标模型 / 上游响应码 / 账号×模型冷却（只对该账号×该模型新增一条，不牵连同 Key 其他账号与其他模型）。
5. **11102 分流**：出现 11102 时先判两件事——
   - a) 若「目标模型确实存在于该平台目录、且就是它自己的拼写」→ 属**上游/账号侧拒绝**，走既有 `model_block`（只冷却该账号×该模型）；
   - b) 若日志里 `target_model` 与目录拼写不一致（或出现 `main-auto` 发给了非 Marvis 平台）→ 属**模型仍被错发**，立刻按第五节的批次回退第 1 批，保留平台内映射与显式目标限制。
6. **日志自检**：抽一条 INFO 级日志确认含 `requested / target_model / platform / realm / attempt`，且**不含**正文、Key、账号令牌。
7. **聚合名兼容**：确认 `/v1/models`（strict 与 `?detail=1`）里聚合 ID 仍是 `deepseek-v4.1-flash`，旧 `deepseek-flash` 仍可调用，Qoder 专名 `deepseek-flash-qoder` 仍直达 `dfmodel`。
8. **管理台**：确认两个不同 WorkBuddy 模型的组 key 是不同字符串，且多区明细可展开。

---

## 附录 A：§八 命令复跑、§二 入口核对与四条专项

### A.1 执行命令（仓库根，`$env:PYTHONUTF8='1'`）

| # | 命令 | 结果 |
|---|---|---|
| 1 | `python -B -m pytest -p no:cacheprovider -q tests/test_platform_model_routing.py` | **56 passed**（1.43s） |
| 2 | `python -B -m pytest -p no:cacheprovider -q tests/test_gateway_reliability_dispatch.py` | **44 passed**（1.21s） |
| 3 | `python -B -m pytest -p no:cacheprovider -q tests/test_gateway_reliability_models.py` | **27 passed**（6.96s） |
| 4 | `python -B -m pytest -p no:cacheprovider -q tests/test_gateway_reliability_scheduling.py` | **40 passed**（5.57s） |
| 5 | `python -B tests/test_dispatch_model_ownership.py` | **14 通过 / 0 失败** |
| 6 | `python -B tests/test_dispatch_generic_models.py` | **通过 18 / 失败 0** |
| 7 | `python -B tests/test_marvis_model_naming.py` | **通过 18 / 18** |
| 8 | `git diff --check` | **exit 2**：`admin/console/console.css:6029: new blank line at EOF`（无关文件，见 F10） |
| 9 | `rg -n 'upstream_of\(|upstream_of_in_platform\(|alias_to_upstream\(' admin/platform` | **本机无 ripgrep**，改用 `Select-String` + Python 复算（见 A.2） |

补充：`python -B -m pytest -p no:cacheprovider -q tests/test_dispatch_retry_degraded.py` → **6 passed**（任务 3 的软降级/错误分类持久用例）。

### A.2 `rg` 的等价替代（必须注明）

本机 PATH 无 ripgrep。用 PowerShell 逐文件读行 + 正则等价复算，结果（`admin/` 全目录）：

| 符号 | 生产代码调用点 | 其他 |
|---|---|---|
| `models_router.upstream_of(` | **0** | 仅注释（`dispatch.py:2077,2301`）、文档/skill |
| `upstream_of_in_platform(` | **0** | 仅 `admin/platform/skill/scripts/check_hotpath.py:298,302` |
| `alias_to_upstream(` | **2**：`admin/platform/dispatch.py:2070`、`admin/platform/shared.py:995` | 定义在 `model_suffix.py:76` |

结论：计划 §四 第 54 行要求的两处 `__call__` 全局反查**已消除**。

### A.3 §二 改动位置逐入口核对

| 文件 | §二 要求的入口 | 状态 | 证据 |
|---|---|---|---|
| `admin/platform/dispatch.py` | `__call__`、`resolve`、`_route_by_model_ownership`、`_reroute_target`、`_candidates_for_model`、`_model_for_platform`、`_model_id_for` | **全部改到** | `_model_id_in_target:798`（新）、`_model_id_for:907`（薄封装）、`_candidates_detail:1126`（硬限制）、`_group_allows:1416`、`model_not_in_group_payload:1453`、`_send_body:1994`、`__call__` 内固定目标分组复核 `:2141-2169`、目标映射 `:2179-2195`、尝试循环 `:2501-2549` |
| `admin/platform/models_router.py`、`model_suffix.py` | `upstream_of`、`upstream_of_in_platform`、`readable_model_id`、`build_payload`、`_entry_targets`、`alias_to_upstream`、`resolve` | **改到（两文件分工）** | models_router：`_intersect_caps:320`、`_entry_targets:543`、`public_model_id:635`、`resolve_public_targets:702`、`build_payload:890`、`platform_of_model:1086+`、`upstream_of:1238` 保留但生产零调用；model_suffix **相对 HEAD 未改**（`git diff` 为空）——`alias_to_upstream`/`resolve` 被 dispatch 的新调用点安全复用 |
| `admin/model_alias.py`、`admin/routers/models.py` | `KNOWN_ALIASES`、`DISPLAY_NAMES`、`canonical_key`、`group_models`、`list_aggregate`、`_invalidate_routing_cache` | **改到** | model_alias：`group_models:201`，内层变量改名为 `slot`（`:259-273`），`KNOWN_ALIASES/DISPLAY_NAMES/canonical_key` 复用未改；routers/models：`_invalidate_routing_cache:96`、`list_aggregate:493`（+21 行） |
| `admin/platform/gateway_router.py` | `health`、`order`、`pick` | **改到（pick 复用）** | `health:212` 按行统计 `usable`，注释明写「usable 与 cooling **不再相减**」（`:227`）；`order:316`，`usable` 为 `None` 按 0（`:449`）、`%d` 同样兜底（`:454`）；`pick:480` 未改（复用 order） |
| `admin/platform/marvis/gateway.py`、`marvis/core/endpoints.py`、`admin/routers/proxy.py` | `_validate_model`、`canonical_model_id`、`_request_json`、`_MODEL_BLOCK_CODE` | **核对通过（前三个复用，proxy 改）** | marvis 两文件相对 HEAD 未改：`_validate_model:529` 仍把可读名/槽位归一成 `main-auto`（测试 D 实测）、`canonical_model_id:276`；proxy `_request_json:2110`、`_MODEL_BLOCK_CODE:844` 未改，新增 `_resolve_group_model_alias:2616`、`_pick_best_model` 分组分支改 `:2695+` |

### A.4 完整性核对（队长要求的 14 文件符号存在性）

| 文件 | 符号 | 结果 |
|---|---|---|
| dispatch.py | `_model_id_in_target` / `_send_body` / `model_not_in_group_payload` / `candidates_degraded` | 全部存在：`:798` / `:1994` / `:1453` / `:2497` |
| models_router.py | `public_model_id` / `resolve_public_targets` / `_intersect_caps` | `:635` / `:702` / `:320` |
| model_alias.py | `group_models` 内层变量改名 | `slot`（`:259-273`） |
| gateway_router.py | `usable` 按行统计、throughput 的 `usable=None→0` | `:225-294`、`:449-454` |
| proxy.py | `_resolve_group_model_alias` | `:2616`（`_pick_best_model` 调用点 `:2706`） |
| bench | `_is_full_table_scan` | `:128` |

`git diff --stat`（本计划 7 个已跟踪文件；3 个新测试文件未被跟踪、bench 被忽略）：

```
 .gitignore                       |  10 +
 admin/model_alias.py             |  11 +-
 admin/platform/dispatch.py       | 886 ++++++++++++++++++++++++++++++---------
 admin/platform/gateway_router.py |  91 +++-
 admin/platform/models_router.py  | 423 ++++++++++++++++---
 admin/routers/models.py          |  21 +
 admin/routers/proxy.py           | 146 ++++++-
 7 files changed, 1294 insertions(+), 294 deletions(-)
```

注意两件事：
1. `admin/routers/proxy.py` 的 diff 里**混有与本次计划无关的并发改动**（约 540 行附近的 `_start_real_credits_backfill` 等），评审时按 hunk 看，别整文件回退。
2. `scripts/bench_dispatch_hotpath.py` 被忽略（F4）；`tests/test_platform_model_routing.py`、`test_gateway_reliability_scheduling.py`、`test_dispatch_retry_degraded.py` 已有白名单，可入库。

---

## 附录 B：四条专项核对

### A. 日志脱敏与可辨认 —— 通过

探针（INFO 级，生产默认看一眼）采到的完整日志：

```text
dispatch 收到 | path=/v1/chat/completions model='deepseek-v4.1-flash-qoder' bytes=128
dispatch 分叉 | requested='deepseek-v4.1-flash-qoder' target_model='dfmodel' platform='qoder' realm='cn' explicit=True has_gw=True default='workbuddy' suffix_platform='qoder'
dispatch 尝试 | attempt=1/1 public_platform='qoder' realm='cn' model='dfmodel' requested='deepseek-v4.1-flash-qoder'
```

- 正文标记 `SECRET-PAYLOAD-MARKER-9c1f`：**未出现**；Key 明文 `sk-regression-aggregate-0001`：**未出现**；只有 `bytes=128` 这种长度。
- 同一请求一行里可读出：原始名 `requested`、目标名 `model`、平台 `qoder`、区 `cn`、尝试序号。
- 持久用例：`test_request_log_carries_no_payload_or_credentials`、`test_request_log_identifies_names_platform_and_realm`（均在 56 passed 内）。

### B. 「额度未知 vs 真实零余额」 —— 判据不同，但库层几乎无法表达「未知」（F2）

实测：
- `Account(balance_remain=None)` → 落库读出 **0**（`Column(default=0)`）；raw SQL `UPDATE ... SET balance_remain = NULL` 后才能读回 `None`。
- 选择层判据**确实不同**：`select_account(..., min_balance=0)` 过滤条件是 `balance_remain IS NULL OR balance_remain > 0`（`shared.py:766-767`）：
  - NULL 行：**入选**（探针返回 `workbuddy-test-0`, `balance=None`）
  - 0 行：**被挡**（只留零余额行时返回 `None`）
  - 排序也区分：已知余额优先（`shared.py:810`）
- 持久用例：`test_unknown_balance_is_selectable_but_ranked_last`、`test_zero_balance_is_filtered_by_min_balance`、`test_three_insufficient_causes_have_distinct_data`（40 passed 内）。
- 结论：**选择/健康侧无需改**；隐患在写库侧（`Column(default=0)` + 多处 `int(x or 0)`），归 billing/同步侧，记 F2。

### C. `is_native_model_id` 的异常契约 —— 复现成功（F1）

复现（目录把可读名当原生行，`marvis` 行 `model_id=deepseek-v4.1-flash`）：

| 场景 | `is_native_model_id("marvis","deepseek-v4.1-flash")` | `alias_to_upstream("deepseek-v4.1-flash-marvis")` |
|---|---|---|
| DB 正常 | **True** | **("", "")**（原生 id 优先，不改写） |
| `admin.db.SessionLocal` 抛异常（DB 不可达） | **False** | **("marvis", "main-auto")**（把原生 id 当别名改写） |

路径：`models_router.py:845-847` `except Exception: logger.warning(...); return False` → `model_suffix.py:109-110` 的 `if storage and mr.is_native_model_id(storage, bare): return "", ""` 被绕过 → 落到 `MODEL_NAME_MAP` 反查，返回槽位名。后果：转发层若按目录校验归属，`main-auto` 不在「可读名目录」里 → 404。建议修法见 F1（三态/异常 + 未知时 fail-closed）。

### D. 测试基线归因 —— 通过，无 blocker

- `tests/test_marvis_model_naming.py`：`git diff` **只有数据库引导**（新增 `_TMP_DIR`/`ADMIN_DATABASE_URL` 直接赋值、`_CATALOG` 种子、`_bootstrap_catalog()`、`_teardown()`、`__main__` 的 try/finally）与 import 增补；**断言/测试函数体零改动**（无 `check(...)` 行被改）。16/18 → 18/18 属环境修复，不是放宽断言。
- `tests/test_trae_platform.py`：本机跑出 `[FAIL] init_db 返回成功`（`pymysql 2003 ... 127.0.0.1`，建表重试 30 次后降级），随后因未捕获的 `OperationalError` 中断，未能打印末尾汇总（日志 4 处 Traceback）。与本次改动无关，属 MySQL 环境基线；单列不阻塞验收。

---

## 附录 C：本轮探针与可复现命令

```powershell
# §八 七条（仓库根，逐个文件单跑；不要合并进程，见 F9）
$env:PYTHONUTF8='1'
python -B -m pytest -p no:cacheprovider -q tests/test_platform_model_routing.py      # 56 passed
python -B -m pytest -p no:cacheprovider -q tests/test_gateway_reliability_dispatch.py # 44 passed
python -B -m pytest -p no:cacheprovider -q tests/test_gateway_reliability_models.py   # 27 passed
python -B -m pytest -p no:cacheprovider -q tests/test_gateway_reliability_scheduling.py # 40 passed
python -B tests/test_dispatch_model_ownership.py                                      # 14 / 0
python -B tests/test_dispatch_generic_models.py                                       # 18 / 0
python -B tests/test_marvis_model_naming.py                                           # 18 / 18
git diff --check                                                                      # exit 2（无关 console.css，见 F10）

# 热路径（前/后）
python -B scripts/bench_dispatch_hotpath.py --json
# 「前」：git archive HEAD 到临时目录 + 同一 bench 脚本，在该目录跑同一命令

# rg 替代
Get-ChildItem -Recurse -File -Filter *.py admin/platform | ForEach-Object {
  $f=$_.FullName; $i=0; Get-Content -LiteralPath $f | ForEach-Object {
    $i++; if ($_ -match 'upstream_of\(|upstream_of_in_platform\(|alias_to_upstream\(') { "{0}:{1}: {2}" -f $f, $i, $_.Trim() }
  }
}
```

一次性探针覆盖：§一 行 1/2/3（6 个入口组合）、行 4（5 个选择器）、行 5（4 个名字）、行 6（`group_models` 两组）、行 7（`health()`）、专项 A（INFO 日志）、专项 B（NULL/0）、专项 C（DB 不可达）、分组大小写（F6）与未知模型（F7）。探针文件与 HEAD 快照目录已从 `%TEMP%` 删除。

---

## 附录 D　队长增量（verifier 交付之后）

- 记录人：lead（队长）
- 时间：2026-11-23
- 范围：只记录 verifier 出报告之后发生的变更与复跑结果；前文（一～六节与附录 A/B/C）结论按 verifier 出报告时的状态解读，不追溯改写。

### D.1 findings 状态更新

| 编号 | 原结论 | 现状 | 证据 |
|---|---|---|---|
| F4 | bench 被 `.gitignore:88 scripts/*.py` 吞掉，提交前必须补白名单 | **已闭环** | `.gitignore` 末尾补 `!scripts/bench_dispatch_hotpath.py`；`git check-ignore -v` 命中否定规则，`git status -uall` 显示 `?? scripts/bench_dispatch_hotpath.py` |
| F6 | 分组登记大小写与目录行不一致时丢光成员 → 整组 403 | **已修** | `admin/routers/groups.py`：原串相等优先 → 无锚点才大小写不敏感且多平台命中取 storage 排序第一家 → 区语义照 `:intl` 孪生不顶替 → 目录缺失成员原样保留交由「启用」求交。修复前 `effective_group_models()=set()`、`_pick_best_model(...)=None`；修复后 `{deepseek-v4.1-flash}` 正常返回。反向断言保留：`alpha demo-model` 与 `beta DEMO-MODEL` 不互相授权 |
| F6b | （verifier 未列，成员在修复中自报）`build_payload(group_id=...)` 用原始登记字符串过滤，导致「能调用但清单看不到」 | **已修** | 改走 `effective_group_models()`，缓存指纹同步改为归一 frozenset。修复前该分组 Key 清单为空；修复后 aggregate / full / `to_openai_strict` 三形态均含 `deepseek-v4.1-flash`，组外 `dfmodel`/`main-auto` 一个都不出现 |
| F1 | `is_native_model_id` 在 DB 不可达时返回 False，与「确定不是原生 id」不可区分 | 维持 | 跨模块失败语义契约，改动会波及热路径；建议单独评估「区分未知与确定否」 |
| F2 | `balance_remain` 的 `default=0` 让显式 None 落库成 0 | 维持 | 写库侧问题，归 billing/同步侧 |
| F3 | cold 启动 1 次真·全表（`models_router.py:841`），warm 为 0 | 维持 | 计划 §七 第 91 行目标是预热后为 0，已达成；优化属独立性能改动 |
| F5 | `proxy._map_anthropic_model` 仍有独立 `m in allowed` | 维持 | 涉及 Anthropic 档位语义，非本次验收项 |
| F7 | 「谁都没登记」的模型在默认/显式目标下不判 404，原样转发 | 维持 | 既有行为，计划要求是「沿用入口已有行为」 |
| F8/F9 | trae 2 项 MySQL 基线；多文件同进程跑 18 failed | 维持 | 环境基线，验收一律按文件单跑 |
| F10 | `git diff --check` 命中 `admin/console/console.css:6029` | 维持 | 与本计划无关的并发改动；本计划 8 个文件 `diff --check` 为 exit 0 |
| F11 | `upstream_of` / `upstream_of_in_platform` 生产零调用 | 维持 | 建议在 `models_router` 标注 deprecated，本轮未改 |

### D.2 队长复跑（按文件单跑，规避跨文件 env 绑定）

| 套件 | 结果 |
|---|---|
| `tests/test_gateway_reliability_models.py` | **36 passed**（F6b 新增 4 例后由 32 增至 36） |
| `tests/test_platform_model_routing.py` | **56 passed** |
| `tests/test_gateway_reliability_dispatch.py` | **44 passed** |
| `tests/test_gateway_reliability_scheduling.py` | **40 passed** |
| `tests/test_dispatch_retry_degraded.py` | **6 passed** |
| `tests/test_dispatch_model_ownership.py` | 14 通过 / 0 失败 |
| `tests/test_dispatch_generic_models.py` | 18 通过 / 0 失败 |
| `tests/test_marvis_model_naming.py` | 18 / 18 |
| `git diff --check`（本计划 8 个文件） | exit 0 |

### D.3 交付物可入库性

`.gitignore` 原本对 `tests/*`、`scripts/*.py` 采白名单制，且 `docs/**` 忽略整个文档树（连本计划的计划文档本身也不可跟踪）。已补齐：

- `!tests/test_platform_model_routing.py`
- `!tests/test_gateway_reliability_scheduling.py`
- `!tests/test_dispatch_retry_degraded.py`
- `!scripts/bench_dispatch_hotpath.py`
- `!docs/superpowers/` → `!docs/superpowers/plans/` → `!docs/superpowers/plans/*.md`（父目录被排除时，仅靠 `!*.md` 不生效，必须逐级再包含）

核验：`git status -uall` 中四个测试/脚本与 7 份计划文档均为 `??` 可跟踪。

### D.4 工作树完整性事件（影响交付方式，务必知悉）

- 期间发生过一次「改动被单独打回 HEAD」：`admin/routers/proxy.py` 一度 `git diff --stat` 为空，`_resolve_group_model_alias` 消失，导致 3 条分组用例确定性变红。已按原样恢复并复验。
- `.gitignore` 的白名单行也曾在并发编辑中被清掉一次，已重新补齐。
- 该仓库同时有其他活在并发落地（`main.py`、`desktop/`、`admin/server.py` 等 40+ 文件），因此：
  1. 本计划 14 个文件的快照留存于 `%TEMP%\miraihub-plan-backup-20261123-004828`；
  2. 提交时**按 hunk 评审**，禁止整文件回退；`proxy.py`、`gateway_router.py` 等混有无关改动。

### D.5 上线批次修订

- 第 1 批：不变（任务 1-2 + 聚合命名 + `model_alias` group key）。
- 第 2 批：不变（任务 3 + 任务 5）。
- 第 3 批：追加 `admin/routers/groups.py` 的归一 hunk 与 `models_router.build_payload` 的分组过滤 hunk（F6 / F6b），同样按 hunk 评审、禁止整文件回退。
- 单独评估：调度策略（round_robin / throughput / 默认平台置顶）本轮仍未实施，建议文档见 `docs/superpowers/plans/2026-11-22-dispatch-strategy-proposals.md`。
