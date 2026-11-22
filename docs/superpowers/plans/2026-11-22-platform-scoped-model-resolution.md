# MiraiHub 模型串名修复、网关调度与模型聚合执行计划

**交付目标**　客户端从模型清单复制一个 ID 发起调用时，网关能找回同一模型。每次转发都使用目标平台认可的拼写。显式选定的平台只调用一次该平台的网关。管理员可以从同一请求的记录中核对客户端模型、实际平台和转发模型。

**实施起点**　2026-11-22 在 `pr/qimei-guard-rotation` 的 `a6c1ed7` 上核对。技术栈为 Python、Starlette ASGI、FastAPI 和 SQLAlchemy。下面用函数名定位。实施者先记录自己分支的 HEAD 和现有改动，按当前版本复现。每个任务先跑失败用例，再改对应路径，最后单独报告通过的断言。可以直接按本文逐项实施。

**交付顺序**　任务 1–2 可作为第一个修复发布，解决截图里的错发。任务 3 修正固定平台和候选重试。任务 4 修正聚合 ID 和管理台分组。任务 5 修正调度可用性并衡量查询开销。任务 6 核对完整调用链。轮转和吞吐策略的调整放在任务 5 末尾，已有故障可以先行修复、单独验收。

## 一、已复现的行为

在独立 Python 进程创建临时 SQLite，使用仓库的 `ModelConfig`、`models_router.build_payload()`、`to_openai_strict()` 和 `PlatformDispatchMiddleware`。请求通过真实的 ASGI `receive` 重放。WorkBuddy 主应用用读取 JSON 的本地接收器替代。平台配置、指标和账号状态在测试进程里隔离。以下是本地结果，截图那一次的上游请求与错误码仍需用对应请求日志核对。

| 场景 | 本地观察 | 修复后断言 |
|---|---|---|
| Marvis 的 `main-auto` 先入库，WorkBuddy 的 `deepseek-v4.1-flash` 后入库，发送裸名并指定 WorkBuddy | 清单给 `deepseek-v4.1-flash`，响应头指向 WorkBuddy，主应用实际读到 `main-auto`。此时 scope 的 `_wb_body` 里仍是公开名 | 主应用实际 JSON 与本次转发的 body 缓存都为 WorkBuddy 登记的 `deepseek-v4.1-flash` |
| 同一目录，请求模型加 `-workbuddy`，同时用请求头指定 WorkBuddy | 主应用照样读到 `main-auto` | 带后缀和裸名都能调用正确的 WorkBuddy 模型 |
| 两条数据交换插入顺序 | 聚合清单相同，主应用这次读到正确的公开名 | 上面两种顺序结果一致。SQL 的自然返回顺序不构成约定 |
| 两个模拟平台 A、B 均有同一模型，Key 未绑定平台且开聚合，仅用模型后缀固定 A，A 明确返回 503 | 实际调用 A、B，最终返回 B 的 200。让禁用集合包含 B，并从策略排序中移除 B，仍然调用了 B。额外带指定 A 的请求头时只调用 A 并返回 503 | 仅后缀、仅请求头、两者一致这三种固定方式均只调用 A。另用真正允许自动重试的裸名请求验证禁用 B 也不能被调用 |
| 只有 WorkBuddy 的 V4.1 Flash，再加入 Qoder 的 `dfmodel` | `build_payload(aggregate_only=True)` 的公开 ID 从 `deepseek-v4.1-flash` 变为 `deepseek-flash` | 这一组始终发布 `deepseek-v4.1-flash`，历史裸名 `deepseek-flash` 仍可解析，Qoder 的平台专名仍可用 |
| 管理台聚合两个不同的 WorkBuddy 模型 | `group_models()` 为 V4 Pro 和 V4.1 Flash 都返回了元组 `("workbuddy", "cn")` 作为组 key。序列化到 JSON 会成为数组 | 每个模型组返回各自稳定的字符串 key，多区明细仍可展开 |
| 同一平台有一个健康 active 账号，另有一个非 active 账号处于冷却 | 用这两行调用 `gateway_router.health()` 得到 `active=1, cooling=1, usable=0, ok=False` | 健康账号计入可用数，非 active 账号的冷却时间不会抵消它 |

## 二、改动位置

| 文件 | 要读的入口 | 本次职责 |
|---|---|---|
| `admin/platform/dispatch.py` | `PlatformDispatchMiddleware.__call__`、`resolve`、`_route_by_model_ownership`、`_reroute_target`、`_candidates_for_model`、`_model_for_platform`、`_model_id_for` | 保留原始模型，根据最终落点生成请求体，在分发和重试时执行相同限制 |
| `admin/platform/models_router.py`、`admin/platform/model_suffix.py` | `upstream_of`、`upstream_of_in_platform`、`readable_model_id`、`build_payload`、`_entry_targets`、`alias_to_upstream`、`resolve` | 区分公开名、平台后缀、目录里的 ID 和别名。统一聚合名及区域归属 |
| `admin/model_alias.py`、`admin/routers/models.py` | `KNOWN_ALIASES`、`DISPLAY_NAMES`、`canonical_key`、`group_models`、`list_aggregate`、`_invalidate_routing_cache` | 复用已有的模型等价关系，修正管理台分组标识，验证配置编辑和目录同步后的可见性 |
| `admin/platform/gateway_router.py` | `health`、`order`、`pick` | 修正可用账号统计，区分空候选与全平台排序，测量排序开销 |
| `admin/platform/marvis/gateway.py`、`admin/platform/marvis/core/endpoints.py`、`admin/routers/proxy.py` | `_validate_model`、`canonical_model_id`、`_request_json`、`_MODEL_BLOCK_CODE` | 核对平台协议、原始模型审计及真实 11102 的账号×模型处理。优先在 dispatch 修复错发 |
| `tests/test_platform_model_routing.py`（建议新增）、`tests/test_gateway_reliability_dispatch.py`、`tests/test_gateway_reliability_models.py` | ASGI 收包、重试与目录缓存测试 | 新文件只收录本次持久回归用例，文件顶部标明 `PERSISTENT REGRESSION TESTS`。同类已有用例可就近扩展 |

## 三、任务 1　把截图问题固定为入口回归

- [ ] 在导入 `admin.db` 前将 `ADMIN_DATABASE_URL` 指向测试专属的临时 SQLite 文件。测试结束时关闭会话、释放引擎并清理临时目录。仓库的默认引擎传有连接池参数，使用文件型 SQLite 最接近已验证的环境。每轮置空目录、原生名和调度缓存，避免上一轮记录影响下一轮。
- [ ] 同时插入 `("marvis", "main-auto", 1)` 和 `("workbuddy", "deepseek-v4.1-flash", 1)`，参数化两种插入顺序。隔离外部网关、Redis 指标和真实账号。让实际代码完成目录读取、模型解析、body 重放和平台分叉。
- [ ] 从严格格式的 `/v1/models` 视图取出公开 ID，发送 `POST /v1/chat/completions`。在 WorkBuddy 接收器内调用 `await Request(scope, receive).json()` 取最终 model，同时记录 `scope["_wb_body"]`、response start 的落点头和传入的 messages。至少覆盖裸名加头、只带 WorkBuddy 后缀、绑定 WorkBuddy 的测试 Key 三条入口。只固定账号和授权条件，让真实选择器处理这三条路径。
- [ ] 在旧实现上看到由模型断言触发的失败。针对某些数据库执行计划不会随插入顺序变化的情况，另在单测中提供顺序确定的行迭代器。保留真实 SQLite 的入口断言。最终验收以接收端实际 JSON 为准。

由 ASGI 接收器填写 `observed`，至少保留下列断言。

```python
assert published_id == "deepseek-v4.1-flash"
assert observed["platform"] == "workbuddy"
assert observed["received"]["model"] == "deepseek-v4.1-flash"
assert observed["cached"]["model"] == observed["received"]["model"]
assert observed["received"]["messages"] == original["messages"]
```

## 四、任务 2　只在最终目标内还原模型名

- [ ] 在首次成功预读时，保存原始 body 字节与顶层请求名。用 `model_suffix.resolve(requested_model)` 得到后缀和仅剥离后缀的名字。后者供目录归属、精确登记、改道和候选计算使用。归一 key 只用于比较。跟踪 `_model_of(request)` 读 scope 缓存、`_model_of_body(body)` 读局部变量这两条路径，让每个调用点取到它需要的名字。
- [ ] 在 `__call__` 搜索 **两处** `models_router.upstream_of(...)`。第一处在选平台前按全表首个匹配改 body，第二处在 WorkBuddy 交给 `self.app` 前再次做全局查询。全局函数按行把 raw、slug 和 readable 与输入比较，匹配后返回该行的 **raw ID**。Marvis 的可读名因此会把公开输入反查成 `main-auto`。两处运行时写入都改走目标平台限定的映射。同时检查同段的 `alias_to_upstream` 分支，它也能在目标未定时改写裸名。其他调用者如需全局查询，先核对用途。
- [ ] 以最终的 public platform ID、storage、realm 及逻辑模型名作为一次映射的输入。先匹配此目标和此区域的原生目录 ID，优先原串相等，其次忽略大小写并返回目录原拼写。再处理本平台明确登记的别名、slug 或已有 canonical hook。可复用 `_catalog_maps`、`_model_id_for`、`_model_for_platform`。当前 `upstream_of_in_platform` 未命中时返回空串，且不涵盖全部可读别名。使用它时显式处理空值。识别到同平台多条不同原生 ID 对应同一 key 时，优先精确项，其余按既有区域及已登记的明确映射消歧。
- [ ] 平台归属和禁用检查使用该目标的目录身份。Marvis 的目录可能存槽位，也可能存可读名，两种都应通过本平台校验，随后交给 Marvis 的 `_validate_model` 转为 `main-auto`。WorkBuddy 按自己的目录拼写转发。Qoder 在本平台支持 `deepseek-flash → dfmodel`。模型未命中时沿用该入口已有的通用名、未知模型和未同步目录行为，不能拿其他平台的原生 ID 当默认值。
- [ ] 所有真实发送点使用原始 body 生成该次目标独有的 body。默认主应用、非默认首选及额外候选均覆盖。原名和目标名相同就复用原字节，否则用 `_replace_model` 仅改顶层 model。传给下游的 `_wb_body` 和 `_replay_receive(target_body, tail)` 使用同一份字节，保留原始 tail 的断开通知。原始 model 留给请求指标及既有回显头。核对 `proxy._request_json()` 后续是否用改写值覆盖 `requested_model`。打开的 `_db()` 放进现有上下文管理器。
- [ ] 验收任务 1 全部通过。再断言 Marvis 收到自己的有效输入，`_validate_model({"model": ..., "messages": [...]})` 实际得到 `main-auto`。WorkBuddy 用不同大小写登记时，接收器得到库内原拼写。普通消息、工具、流式开关和 Responses 的 `input` 保持原值。运行已有请求体上限和断开测试。

## 五、任务 3　首选平台、候选与重试沿用同一套限制

| 请求入口 | 首次选择 | 允许的后续尝试 |
|---|---|---|
| 明确模型后缀、`X-Platform` 或 `?platform=…&realm=…` | 解析到指定 public ID 和区，当前中间件先识别模型后缀，命中后直接调用 `registry.resolve(suffix_platform)`。用两者冲突的请求锁定当前行为 | 只在指定平台内部沿用其原有账号处理。平台返回 503 时将结果交给客户端 |
| 绑定平台的 Key | 优先使用绑定目标。单独覆盖仓库已有的发出前改道规则、`_KeyView` 和 `_ReroutedKey` | 即使改道视图里的 `platform` 被置空，原 Key 的账户范围、分组、配额和原有运行期重试限制都有效 |
| 裸名、Key 无平台绑定 | 在声明该模型、区域匹配且可用的候选中，先选精确登记，再按现有策略选首个。没有可用精确项时才考虑合格的别名项 | 仅现有允许的聚合调用可跨平台重试。关闭聚合的 Key 仍做模型归属选择 |
| `auto`、`default` 等通用名 | 沿用 `_GENERIC_MODEL_NAMES` 的默认路径或显式目标 | 不因某个平台恰好登记了 `auto` 就移交给它 |

- [ ] 复用 `tests/test_gateway_reliability_dispatch.py` 的假平台和 ASGI 记录器。A、B 都声明同一模型，A 返回明确 503，B 返回 200。分别测后缀、请求头、查询参数、Key 绑定、自动聚合。断言调用列表、实际每次的 model、终态响应和返回平台。将 B 设置为管理员禁用，再做一轮**无后缀、无显式头、允许跨平台重试**的独立用例。
- [ ] 从原始输入保存“客户端指定了平台”和“原 Key 绑定平台”两个事实。当前 `explicit_platform` 参与了多个含义，后面的 `auto_route` 只看 `_explicit_selector(request)` 及当时的 Key。计算是否可跨平台重试时显式纳入后缀、原始绑定和聚合开关。在改过的 Key 视图上只执行获准的选号。
- [ ] 让首次选择、`_reroute_target` 和重试共用本请求的目录成员与可用候选。改道接收当前已选目标，避免无绑定请求已经选中平台后，又按“想要的平台=默认平台”重复计算。保留既有绑定平台改道的正反例。用 `(public_id, realm)` 去重，用 `storage` 查目录。Trae 分区模型严格匹配当前区，Qoder 共用一份目录的两区照常展开。
- [ ] `_candidates_for_model` 会把未出现在 `_platform_order()` 中的 spec 追加到尾部。合并前按 `_is_disabled`（包含 storage 禁用）、平台/区域停用、模型归属、Key 可用账号范围、账号×模型冷却和平台暂停逐项过滤。排序没有资格把被排除者加回来。在每个待发的后续候选处再核对禁用/暂停状态。继续使用已有的状态失效机制。
- [ ] 区分“无人登记”（现有 404）、“声明者全被禁用”（现有 403）、“模型存在但当前无可用账号”（账号池/平台的可用性错误），以及“所有候选仅有历史失败记录”。软健康降级只能发生在已通过硬限制的集合中。若已有准入支持等待冷却，交给原机制处理。空候选早返回，检查 `gateway_router.order(db, [])` 的 `platform_ids or list(registry.ids())` 会扩成全平台，令 `None` 表示全体、空列表表示空，并回归现有调用者。
- [ ] 明确响应才按既有 `_RETRYABLE_STATUS` 和最多三个候选处理。`_is_uncertain`、客户端断开和已经开始发送的流都终止跨平台重放。保留默认 WorkBuddy 走 `self.app` 的分发方式。生成列表时只保留当前分支实际能调用的目标，防止缺少 adapter 的条目浪费尝试次数。截断后按实际列表判断最后一次尝试。
- [ ] 核对策略调用次数。请求内先得到合法候选，再在该集合上排序一次，可复用相同决定生成备用顺序。`random` 或 `round_robin` 在同一请求的选择、改道和重试三个阶段不得各自重新挑首选。诊断记录填本次调用的 public ID、realm、模型及尝试序号。

## 六、任务 4　让管理台、聚合清单与调用名对得上

- [ ] 先写两项失败测试。第一项依次启用 WorkBuddy、加入 Qoder、加入 Marvis，再移除其中一个，检查 V4.1 Flash 的聚合 ID 和单次调用。第二项把同一平台的 V4 Pro 与 V4.1 Flash 传入 `admin.model_alias.group_models()`，断言输出 `key` 各为正确的归一字符串。当前函数内层 `key = (platform, realm)` 覆盖外层分组变量，修复时用不同变量名，保留平台和区域明细。
- [ ] 对已有明确等价关系的模型组，从 `canonical_key` 搭配现有 `DISPLAY_NAMES` 得出规范公开名，再用 `slugify_model_id` 生成调用 ID。例如 `deepseekv41flash → deepseek-v4.1-flash`。历史公开拼写继续作为该组的入站别名，精确的后缀专名仍交给指定平台。V4 Flash 与 V4.1 Flash、V4 Pro 与 V4.1 Pro 分别成组。对缺少明确名称的组保留现有可调用 ID。用 fixture 验证同名冲突并按确切登记消歧，避免根据相似展示名合并不同版本。
- [ ] 将“同一行有哪些公开平台/区域、属于哪个模型组”提取为目录和 dispatch 可共用的轻量计算。复用 `readable_model_id`、`canonical_key`、`_entry_targets` 及其 `:intl` 孪生规则。`list_aggregate` 仍可展示管理端需要的禁用行、倍率和原始明细。公共目录仍先做分组权限和启用过滤。对照 `list_aggregate` 下发的 `client_id` 与全量视图 ID，二者允许形式不同，但都应能回到标注的目标。
- [ ] 对聚合视图、全量视图、受分组 Key 各做“从列表取 ID，再送入网关”的往返测试。无权成员在目录里不可见，手输别名或固定后缀也不得通过分组授权。组内只有一个平台的私有 ID 时，跨平台别名不能扩大该组的可用范围。覆盖 `?detail=1` 及 `to_openai_strict` 的现有字段白名单。
- [ ] 核对能力字段。当前聚合项采用首个成员的 caps，若两个可调度平台能力不同，目录可能高报上下文长度、图片或工具能力。先用“一个支持图片、另一个仅文本”的本地数据验证。建议无能力感知路由时只对聚合项发布所有合格目录成员共同支持的能力，数值上限取已确认的共同下限，未知值按字段契约处理。各平台详情使用自己的能力。验证严格格式序列化与客户端已有默认值，避免以空数组或缺字段意外扩大承诺。该项可独立发布。
- [ ] 检查模型新增、禁用、变更展示名和分组成员后的缓存。当前 `models_router.invalidate()` 只清清单 slot，`is_native_model_id` 另有十秒缓存。修改映射来源后将受影响索引纳入现有失效调用。复测不同分组互不串缓存、本进程写入立即可见、其他 worker 按已有指纹/TTL 刷新。不要把 Key 专属候选、冷却结果或账号可用性存入全局模型名缓存。

## 七、任务 5　改进调度统计，并量化热路径

- [ ] 修正 `gateway_router.health()`。平台可用数直接从“状态 active 且通过该统计所承诺的冷却条件”的行计算。总户数、冷却数、账号可用数分开统计。对同一 storage 的 cn 和 intl 建独立样例。用第一区的冷却账号验证不会抵消第二区的健康账号。
- [ ] 排序先使用任务 3 得出的 Key 范围和模型级候选，平台总览统计仅解释排序。构造两个 Key 各只可用一个账号、其中一个账号对该模型处于冷却的用例。另一个 Key 仍可走自己的账号。额度未知、余额真实为零、模型未开通使用各自的数据和错误类别。余额展示和结算规则由对应模块负责。
- [ ] 对同一固定数据做前后对照。建议种 4 个平台、128 条模型配置，覆盖默认直达、固定后缀、自动聚合及一次 503 后重试。用 SQLAlchemy `before_cursor_execute` 计数，分别测冷启动和预热后的 100 次请求。记录每请求 SQL 数、`model_configs` 全表读取数、排名次数、调度耗时 p50/p95。用本地响应器排除公网延迟。计时只作对照，确定性的调用计数可写入测试。
- [ ] 优先去除已证实的重复工作。预热后每次推理请求因名称映射产生的全平台模型表扫描目标为 0，已固定目标的请求无需计算全平台策略排名，自动请求仅需对本次合格池排名一次。动态账号状态、授权、目录版本仍按既有一致性要求读取。若 `health()` 对每个平台反复加载全量 ORM 账号引起主要开销，再按候选 storage/realm 批量取所需字段，复用一个请求内的结果。提交改动时给出前后两个实测数，不预设毫秒收益。

**可独立评估的策略调整。** 当前 `round_robin` 按“分钟数对候选数取模”，同一分钟的请求会集中落在同一个首选。若要按请求均摊，可在**稳定排序后的合格集合**上使用可并发递增的游标，每个新请求只推进一次。单进程与多 worker 的状态边界须说明。用相同三个健康平台连续 60 次、剔除一个后再 20 次验证分布。当前 `throughput` 主要按可用账号数排序，真实在途较多的平台也可能排第一。先确认已有租约统计能按账号及区域聚合，再比较“可用槽位”与现有排序。额度优先和到期优先目前将默认平台置顶，调整时单独测显式开启策略的聚合流量与老客户端通用名流量。以上是调度策略选择，单独提交，勿与串名修复绑定上线。

## 八、任务 6　回归、验收与发布后核对

| 用例 | 通过条件 |
|---|---|
| WorkBuddy 裸名、请求头、后缀、绑定 Key | 两种插入顺序均匹配其目录原拼写，接收器绝不出现外平台转换产生的 `main-auto` |
| Marvis 槽位版及可读名版目录 | `deepseek-v4.1-flash-marvis` 和历史调用名 `main-auto-marvis` 都命中 Marvis。裸 `main-auto` 能按归属识别，Marvis 协议边界得到合法槽位 |
| Qoder、Loomy、Trae | Qoder 的 `deepseek-flash-qoder → dfmodel`、`deepseek-v4-pro-qoder → dmodel`。Loomy 使用其目录大小写。Trae 国际区模型只会发给登记的区，上游模型串按协议去区域标记 |
| 聚合仅在合格池内重试 | A 明确 503、B 合格时依次尝试，两个接收器各拿到自己的模型 ID。仅显式指定 A 时 B 的调用次数为 0。B 被禁用、分组不允许、结果不确定、流开始后断开，各自有独立断言 |
| 错误分类 | 真正的 WorkBuddy 11102 继续进入 `model_block`，只影响对应账号×模型。用本地上游响应样本覆盖。无余额、模型未知和上游 11102 仍可从日志区分 |
| 客户端和管理员 | 聚合 ID 与旧别名都可调用。管理台不同模型拥有不同字符串 key。关联同一请求的日志可辨认原始名、目标名、平台和区。需要加日志时仅加路由字段，勿输出请求正文、凭证或账号令牌 |
| 协议与可回放性 | 至少一次 Chat Completions、Responses、Messages 输入，token 计数按该平台已有支持范围验收。覆盖流式与普通响应、空体、chunked、体积上限、客户端断开、GET 模型清单以及既有响应头契约 |

**执行命令。** 从仓库根目录运行。先为测试进程设置独立临时目录及文件型 `ADMIN_DATABASE_URL`，再导入应用。旧脚本中的 `setdefault` 会继承外部配置。旧命名脚本的裸名断言依赖目录，用测试 fixture 显式插入 WorkBuddy 的公开原生行、Marvis 的槽位行和 Qoder 的缩写行。下面第一行适用于新增持久回归文件后。

```powershell
python -B -m pytest -p no:cacheprovider -q tests/test_platform_model_routing.py
python -B -m pytest -p no:cacheprovider -q tests/test_gateway_reliability_dispatch.py
python -B -m pytest -p no:cacheprovider -q tests/test_gateway_reliability_models.py
python -B tests/test_dispatch_model_ownership.py
python -B tests/test_dispatch_generic_models.py
python -B tests/test_marvis_model_naming.py
rg -n 'upstream_of\(|upstream_of_in_platform\(|alias_to_upstream\(' admin/platform
git diff --check
```

- [ ] 每项修复提交一条可审查的证据，记录旧值、新值、实际落点、调用顺序和对应测试名。记录测试基线已有失败与本次新增失败。若本分支已修某一项，复用验证通过的实现，转到下一项。
- [ ] 发布后使用既有测试 Key 以相同公开 ID 分别调用 WorkBuddy 和 Marvis，再测一次允许聚合的裸名。以请求 ID 配对核对原始模型、目标平台、目标模型、上游响应和账号×模型冷却。出现 11102 时先区分正确模型被上游拒绝或模型仍被错发。真实拒绝进入现有处理。有证据的历史误记按现有运维方式单独处理。
- [ ] 按任务分批发布。聚合命名或策略调整出现兼容问题时可独立回退，保留平台内模型映射及显式目标限制。交接报告附隔离回归结果、热路径测量、未覆盖的真实平台，以及建议先部署的修复批次。

**给实施模型的起始指令**

> 先执行任务 1，给出复现时 WorkBuddy 接收器读到的 model。随后完成任务 2 并独立验收，再按任务 3–6 依次推进。每次以最后一个接收器的实际 JSON 和平台调用列表作为证据。提交说明写清数据库种子、公开 ID、最终目标、实收模型和对应测试。调度策略行为变更作为独立建议交付。
