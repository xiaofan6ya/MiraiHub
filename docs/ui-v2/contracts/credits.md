# 功能契约：积分到期（S23 /credits）

| 项 | 值 |
|---|---|
| 页面 | 积分到期（模板 A） |
| 路由 | /credits（hash：/admin/v2/#/credits） |
| 设计稿 | docs/ui-v2/06-pages-b.md P7（52–74 行） |
| 旧版 | admin/console/index.html #panel-credits（2449–2559）；console-app.js 10532–11010（loadCreditStats / renderCredit* / saveCreditRoutingPolicy / sync） |
| 后端 | admin/routers/credits.py（/expiry、/selection-policy、/selection-order、/usage）；admin/routers/accounts.py:sync_credits（POST /api/accounts/credits/sync）；admin/routing_policy.py |
| 新版交付 | admin/ui/src/pages/CreditsPage.vue、admin/ui/src/api/credits.ts、admin/ui/tests/page-credits.spec.ts |

## 一、功能

1. 顶部统计（StatStrip 5 格）：账号总数、可用积分、7 天内到期、长期有效、最紧迫账号（含到期天数）。
2. 账号调度策略：选号模式（下拉，选项来自后端 routing_policy.modes）、同账号上游启动间隔（毫秒，0–60000）、保存策略；「自动选号 / 手动优先级」Badge；手动模式下可「恢复自动选号」。
3. 到期风险分布（StackMeter）：≤30 天 / 30 天以外 / 长期有效三段，分母是快照口径总量。
4. 最近快到期的积分包（DataTable）：积分、来源、账号、到期日、剩余占比 Meter；按到期升序，分页续取（加载更多）。
5. 账号到期排行（DataTable）：名次、账号（名称 + UID）、状态、剩余积分、7 天内到期、长期有效、最近到期、最近快到期的积分包、明细；客户端分页 + 排序，状态同步路由 query。
6. 账号消耗构成（近 7 天，BarList）：按账号积分消耗排行，label 带请求次数，顶部显示合计。
7. 重新采集（全部启用账号，串行，几十秒）：JobProgress 右下角卡片（进行中 → 已完成/部分失败，显示成功/失败数）+ Toast 汇总。
8. 刷新（重新拉 expiry + usage）；四态；平台上下文订阅；账号明细抽屉（仅后端返回的最近 4 个包）。

### 与 /gateway 的关系（任务要求先确认）

**不是同一后端设置，不能共用 store/接口**：

| 层 | 设置键 | 接口 | 语义 |
|---|---|---|---|
| 平台级（/gateway） | system_settings.gateway:routing | /api/gateway/config | 一次请求先交给哪个**平台**（priority / balance / expiring / throughput / round_robin / random） |
| 账号级（本页） | account_selection_mode、upstream_start_interval_ms | /api/credits/selection-policy | 选定平台后，平台内选哪个**账号**、同账号启动间隔 |

因此本页只用 /api/credits/selection-policy；改这里不会影响 /gateway，反之亦然。两页各自保存后重新拉取自己的配置。

## 二、数据展示

### 2.1 顶部统计（creditStatItems）

| 标签 | 来源字段 | 格式 | 着色/脚注 |
|---|---|---|---|
| 账号总数 | summary.accounts | fmtInt | 脚注：summary.snapshot_accounts 个已采集到期快照 |
| 可用积分 | summary.available_remaining（余额口径） | fmtCredit | brand；脚注：N 个启用账号 · M 个快到期包 |
| 7 天内到期 | summary.expiring_soon_remaining | fmtCredit | >0 warn；脚注：占全部剩余 at_risk_ratio*100（1 位小数）· 不用完会作废 |
| 长期有效 | summary.evergreen_remaining | fmtCredit | 脚注：没有作废风险，留到最后用 |
| 最紧迫账号 | summary.soonest_account_name | 文本 | >0 warn；脚注 soonestLabel(soonest_days_left)：<1 天 → 「N 小时内到期」，否则「N 天后到期」，null → 全部长期有效 |

### 2.2 到期风险分布（riskView）

- ≤30 天：windows 中 days=30 那一档的 amount（累计分档，直接取最后一档），标签带该档 packages。
- 30 天以外：max(0, summary.total_remaining − ≤30 天 − evergreen_remaining)。
- 长期有效：summary.evergreen_remaining。
- 分母 / 标题：summary.total_remaining（快照口径）；已采集账号少于账号总数时提示「（已采集 x/y 个账号）」。
- 三段都为 0 → Empty「没有任何即将到期的额度 ……」。

### 2.3 积分包表（toPackageRows）

| 列 | 字段 | 格式 |
|---|---|---|
| 积分 | remaining | fmtCredit |
| 来源 | display_name | 文本 |
| 账号 | account_name | 文本（空 → —） |
| 到期日 | expire_date（回退 expire_label / expire_at） | 文本；expiring_soon → Badge warn「快到期」，expired → Badge danger「已过期」 |
| 剩余占比 | remaining/total | Meter，tone ok（快到期时 warn），valueText 整数百分比 |

行键 rowKey = account_id + package_code + expire_at（同一 package_code 会出现在多个账号下，避免虚拟滚动键冲突）。

### 2.4 账号到期排行

| 列 | 字段 | 格式 / 着色 |
|---|---|---|
| # | 客户端计算（页偏移 + 行序） | 弱化 |
| 账号 | account_name / uid | 名称 500，UID 等宽小字 |
| 状态 | status | active → ok「活跃」，其它 neutral |
| 剩余积分 | total_remaining | fmtCredit，强调 |
| 7 天内到期 | expiring_soon_remaining | >0 warn，否则 — |
| 长期有效 | evergreen_remaining | 弱化 |
| 最近到期 | soonest_days_left / synced_at | Badge：≤0 danger / ≤7 warn / 其它 ok / null neutral；脚注「MM-DD HH:mm 采集」 |
| 最近快到期的积分包 | next_expiring（前 2 个） | Chip：包名 + 剩余；无包时「没有仍有剩余的积分包」 |
| 操作 | — | 「明细」按钮（行点击同样打开） |

排序：账号名、剩余积分、7 天内到期、最近到期可点表头排序；空值（无到期日）永远排最后。默认无排序 → 保持后端 ordered_accounts 的紧迫度顺序。分页：每页 20/50/100，page/size/sort/order 同步路由 query（useTableQuery key = credits）。

### 2.5 账号消耗构成（usageBars）

- 取 /api/credits/usage?days=7&top=12 的 accounts，过滤 credits<=0。
- BarList：label = 「账号 · N 次」，value = credits，unit「 积分」，format fmtCredit，limit 12。
- 标题提示：近 7 天 · 合计 X 积分（total_credits）。
- 空：近 7 天没有产生积分消耗。

### 2.6 数字 / 日期口径

- 积分 fmtCredit（整数原样，小数最多 2 位）；数量 fmtInt；时间 fmtTime（北京时间）。
- 状态色：到期风险 ≤30 天 danger、30 天以外 warn、长期有效 ok；7 天内到期 warn；成功/正常 ok。全部走 tokens，不裸写颜色。

## 三、交互

| 控件 | 文案 | 调用 | 成功 | 失败/确认 |
|---|---|---|---|---|
| 保存策略 | 「保存策略」 | PUT /api/credits/selection-policy {mode, upstream_start_interval_ms} | Toast「账号调度策略已保存，新会话立即按新策略分配」；重拉 expiry | 前端校验 0–60000 整数；失败 Toast「保存账号调度策略失败：<原因>」 |
| 恢复自动选号 | 「恢复自动选号」 | POST /api/credits/selection-order {mode:automatic, ids:[], expected_ids, confirm:true} | Toast「已恢复当前自动调度策略」 | 确认框标题「恢复自动选号？」+ 后果说明；409 → Toast 提示刷新后重试 |
| 重新采集 | 页头「重新采集」 | POST /api/accounts/credits/sync | JobProgress 卡片进行中 → 完成/部分失败；Toast「采集完成：成功 x/y，N 个失败，7 天内到期 Z」；重拉 expiry + usage | 运行中按钮 loading；失败 JobProgress 置 failed + Toast「重新采集积分快照失败：<原因>」 |
| 刷新 | 页头「刷新」 | GET /expiry + GET /usage | 保留旧数据、卡片转圈 | Toast「刷新…失败：<原因>」 |
| 加载更多 | 积分包表底部「加载更多」 | GET /expiry?package_offset=已加载数&package_limit=60 | 追加行；显示「已显示 x/y 个」 | Toast「加载更多积分包失败：<原因>」 |
| 排序 | 表头排序 | 无请求（客户端排序） | 同步路由 query（sort/order/page 重置 1） | — |
| 分页 | DataTable 分页 | 无请求（客户端分页） | 同步路由 query（page/size） | — |
| 明细 | 「明细」/ 行点击 | 无请求（用 expiry.accounts[].next_expiring） | DetailDrawer：KV + 最近 4 个包 | — |
| JobProgress 查看详情 | 「查看详情」 | 无请求 | 完成 → info Toast；部分失败 → warning Toast | — |
| 关闭 JobProgress | 「隐藏」 | 无 | 卡片消失 | — |

策略卡片脏判定：selectionPolicyDirty(policy, mode, interval)；有改动才启用「保存策略」，加载新配置时不覆盖未保存的草稿。

## 四、接口

> 所有请求走 @/api/http：同源 Cookie、非 GET 自动补 X-CSRF-Token、每个请求带 X-Platform。**平台作用域来自 X-Platform 头**，不是 query 参数（旧版拼的 platform=/realm= query 会被后端忽略）。

### 4.1 GET /api/credits/expiry

参数：package_limit（1–500，默认 60）、package_offset（默认 0）、include_disabled（默认 false）、days（建议窗口，默认 7）。

响应（credits.py:credit_expiry_stats）：

| 字段 | 类型 | 说明 |
|---|---|---|
| generated_at | number(ms) | 生成时间 |
| expiring_soon_days | number | 平台判定「快到期」的天数（7） |
| summary.accounts | number | 作用域内（默认仅 active）账号数 |
| summary.available_remaining | number | 余额口径可用积分（Account.balance_remain 之和） |
| summary.total_remaining | number | 快照口径剩余（只覆盖采集过到期数据的账号） |
| summary.snapshot_accounts | number | 已采集到期快照的账号数 |
| summary.expiring_soon_remaining / evergreen_remaining / expired_remaining | number | 7 天内到期 / 长期有效 / 已过期 |
| summary.expiring_accounts / expiring_packages | number | 有快到期额度的账号数 / 快到期包数 |
| summary.soonest_expire_at / soonest_days_left | number / number | 最紧迫到期时间与剩余天数 |
| summary.soonest_account_id / soonest_account_name | number / string | 最紧迫账号 |
| summary.at_risk_ratio | number(0–1) | 快到期额度 / total_remaining |
| summary.packages_total | number | 逐包总数（用于「加载更多」） |
| windows[] | {days, amount, packages} | 累计分档：1/3/7/14/30 天 |
| accounts[] | CreditAccountRow | 账号级明细，已按 ordered_accounts 的紧迫度排序 |
| selection_mode | manual / automatic | 是否有人工排序覆盖 |
| selection_order | number[] | 当前真实选号顺序（恢复自动时作 expected_ids） |
| routing_policy | SelectionPolicy | 账号选号模式 + 间隔 + modes 文案 |
| packages[] | CreditPackageRow | 按到期升序的逐包分页（带 account_id/account_name） |
| packages_total / package_offset / package_limit / has_more | number / boolean | 分页状态 |

CreditAccountRow 字段：account_id、account_name、uid、status、selection_priority、balance_remain、total_remaining、total_capacity、expiring_soon_remaining、evergreen_remaining、expired_remaining、soonest_expire_at、soonest_days_left、soonest_label、expiring_soon、expiring_package_count、package_count、active_package_count、synced_at、next_expiring[4]。

### 4.2 GET / PUT /api/credits/selection-policy

GET 响应 = PUT 响应 = routing_policy.selection_policy(db)：{mode, upstream_start_interval_ms, modes:[{value,label,description}]}。PUT 请求体 {mode, upstream_start_interval_ms}：mode 必须在 MODE_IDS；间隔 0–60000（pydantic ge/le，strict int）；保存时会清空所有账号的 selection_priority（恢复自动选号）。错误：400「未知的账号调度模式」/ 422 校验失败。

### 4.3 POST /api/credits/selection-order

请求体 {mode: manual|automatic, ids: number[], expected_ids: number[], confirm: boolean}。confirm 必须为 true，否则 400；expected_ids 与当前排序不一致 → 409「账号或排序已变化，请刷新后重试」。本页只用于自动模式（清手动优先级）。

### 4.4 GET /api/credits/usage

参数：days（默认 7）、account_id、top（默认 20，上限 200）。响应：{range_days, total_credits, accounts:[{account_id, account_name, requests, credits, tokens, remaining, expiring_soon_remaining, soonest_days_left}], models:[{model, requests, credits}]}。平台作用域同样来自 X-Platform。

### 4.5 POST /api/accounts/credits/sync

全部启用账号串行重新采集（每号间隔 KEEPALIVE_ACCOUNT_GAP），响应 {ok, total, refreshed, failed, credits_expiring_soon, detail:[{id, account, ok, error?, expiring_soon?, soonest_days_left?, packages?}]}。**同步请求、无 job_id**（后端没有为该操作提供任务队列），账号多时几十秒。

## 五、跨页联动

- 平台上下文：expiry / usage 都按 X-Platform 收窄；页面订阅 usePlatformStore().onChange 重取 expiry + usage，卸载取消。账号调度策略是全局设置，不随平台变化。
- 与 /gateway：见 §一「与 /gateway 的关系」，两套设置互不影响。
- 与 /accounts（账号池）：数据同源（accounts 表的积分快照列）；本页只读，不修改账号。单账号完整积分包明细在账号池的「积分明细」（accounts.py:/{id}/credit-details）。
- 与 /journal：积分流水记录每笔变动；本页是存量到期视图，不做跳转。

## 六、边界情况

| 场景 | 表现 |
|---|---|
| 首次加载 | 页面骨架（6 行 NSkeleton）；各卡片独立 loading |
| 加载失败（无数据） | ErrorBlock「加载积分到期失败」+ 详情 + 重试 |
| 刷新失败（有旧数据） | 保留旧数据；expiry 用 compact ErrorBlock + Toast，usage 用局部 ErrorBlock + Toast |
| 没有启用账号 | StatStrip 全 0；账号排行 Empty「没有启用账号。先在「账号池」里添加账号。」 |
| 从未采集快照 | snapshot_accounts=0；风险卡 Empty；「可用积分」仍显示余额口径，风险条提示采集覆盖 |
| 没有积分包 | 积分包表 Empty「没有仍有剩余的积分包。点右上「重新采集」…」 |
| 近 7 天无消耗 | BarList 空状态「近 7 天没有产生积分消耗。」 |
| 采集部分失败 | JobProgress status=failed、显示成功/失败数；Toast 汇总；明细错误在响应 detail（页面暂不展开，见实现对照差异） |
| 采集请求超时/网络错误 | JobProgress failed + Toast；可再次点击重新采集 |
| 策略保存校验 | 间隔非 0–60000 整数 → Toast 拦截，不发请求 |
| 恢复自动选号并发冲突 | 409 → Toast「恢复自动选号失败：账号或排序已变化，请刷新后重试」；重拉数据 |
| 手动优先级存在 | Badge「手动优先级」；「恢复自动选号」可用 |
| 401 | http 层跳登录 |
| 手机 375px | 策略表单纵向；统计 2 列；三张表走 DataTable 卡片模式；BarList 单列 |

## 七、旧版有但本次任务未要求的功能（未实现，原因）

| 功能 | 旧版位置 | 说明 |
|---|---|---|
| 账号上移/下移（写回真实选号顺序） | console-app.js:10953–11004 | 会改变真实流量分配，风险高；本任务未列，仅保留「恢复自动选号」。需要时在账号池/后续步骤实现 |
| 含已禁用账号开关 | #creditShowDisabled | 任务未列；接口支持 include_disabled，页面暂不暴露（默认只统计启用账号） |
| 单账号重新采集（refreshAcc） | console-app.js:10968 | 任务只要求全部重新采集；单账号采集走账号池的刷新入口 |
| 单账号完整积分包明细 | /api/accounts/{id}/credit-details | 本页抽屉只展示 expiry 响应里的最近 4 个包（后端刻意不在列表返回全量，避免响应膨胀） |

## 实现对照

| 功能 | 新版位置 | 状态 |
|---|---|---|
| StatStrip：账号总数 / 可用积分 / 7 天内到期 / 长期有效 / 最紧迫账号 | creditStatItems() + StatStrip | 已实现 |
| 账号调度策略卡：选号模式下拉（后端 modes）+ 间隔输入 + 保存 + 自动选号 Badge | CreditsPage 策略卡 + saveSelectionPolicy | 已实现（Badge 按 selection_mode 显示「自动选号」或「手动优先级」） |
| 恢复自动选号 | restoreAutomaticSelection + 确认框 | 已实现（旧版「恢复自动排序」） |
| 到期风险 StackMeter（≤30 天 / 30 天以外 / 长期有效） | riskView() + StackMeter | 已实现 |
| 最近到期积分包 DataTable + 分页续取 | toPackageRows + DataTable + loadMorePackages | 已实现 |
| 账号到期排行（列、排序、分页、路由 query） | accountColumns + useTableQuery('credits') | 已实现（客户端分页/排序，page/size/sort/order 同步地址栏） |
| 未记录账号置底 | — | 不适用：expiry 只返回账号行（无 account_id=0 的汇总行） |
| 账号消耗构成近 7 天 BarList | usageBars + BarList | 已实现 |
| 重新采集（后台任务 JobProgress） | syncAll + JobProgress | 已实现（差异：后端接口是同步、无 job_id，卡片进度由页面本地跟踪，不是服务端任务轮询） |
| 刷新 | 页头刷新 → loadAll | 已实现 |
| 四态（首次/刷新/空/错误） | 页面骨架 + 各卡片 loading/Empty/ErrorBlock | 已实现 |
| 平台上下文订阅（卸载取消） | usePlatformStore().onChange | 已实现 |
| 手机 375px 卡片模式 | 布局断点 + DataTable mobile 标注 | 已实现（未截图验证） |
| 账号明细抽屉 | DetailDrawer + KV + 最近 4 包表 | 已实现（旧版为弹窗 + 全量明细；取舍见 §七） |
| 手动上移/下移、含已禁用账号、单账号采集 | — | 未实现（旧版功能，本任务未列；见 §七） |
| 与 gateway 共用 store | — | 不适用：确认两套是不同后端设置（§一），不能共用 |
