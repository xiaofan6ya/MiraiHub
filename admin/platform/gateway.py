"""网关契约：每个平台模块必须实现的接口。

## 这一层解决什么问题

改造前 `admin/` 直接依赖各平台的内部实现（`admin/trae/...`），
导致**加平台要改 admin，改 admin 就可能碰坏已有平台**。

本模块把「一个平台网关对 admin 来说长什么样」固定成契约：

    admin  ──只看这个契约──▶  某个平台网关

admin 里**不再出现任何平台专有名词**（`trae` / `workbuddy` / `icube` /
`checkin_credits` …），全部通过这里的方法调用。

## 为什么用 Protocol 而不是抽象基类

`typing.Protocol` 是**结构化**的：平台模块不需要 `import` 本文件就能满足契约，
只在测试里做一次 `isinstance` 校验。好处：

  * 平台模块与契约层**零耦合**，可以把整个目录搬走而不必改 import；
  * 不会出现「为了继承 ABC 而被迫引入 admin」的反向依赖。

## 职责边界（硬约束）

| 能力 | 归属 | 理由 |
|---|---|---|
| API Key 校验 / 配额 | **admin** | 用户拿一个 key 调所有平台 |
| 管理员登录 / 权限 | **admin** | 一套后台 |
| 全平台禁用/启用 | **admin** | 要求：切换任意平台都生效 |
| 聚合概览（只读） | **admin** | 首页要一眼看全部，但**不写** |
| 风控哨兵（聚合+告警） | **admin** | 报警要集中 |
| 账号池 11 项操作 | **平台** | 凭证结构、协议、口径各家不同 |
| 积分 / 积分到期 / 流水 | **平台** | 语义各家不同 |
| 使用记录 | **平台**（写）/ admin（只读聚合） | 写入方知道自己是哪个平台 |
| 定时任务执行 | **平台** | 任务类型是平台知识 |
| 客户端扫描/探测/修改 | **平台实现 + admin 通用 UI** | 见 `ClientProbe` |

## 新增平台的检查清单

1. 新建 `admin/<platform>/`，实现本文件的 `Gateway`
2. 在 `admin/platform/loader.py` 的 `BUILTIN` 加一行
3. 在 `admin/platform/registry.py` 注册 `PlatformSpec`（填 `gateway=`）
4. 跑 `tests/test_gateway_contract.py` 校验契约完整性
5. **不要**改 `admin/` 下任何其它文件 —— 如果需要改，说明契约缺了东西，
   应先扩展本文件而不是在 admin 里加平台分支
"""
from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# 账号池
# ---------------------------------------------------------------------------
@runtime_checkable
class AccountOps(Protocol):
    """账号池的 11 项操作。**由平台自己实现**，admin 只负责路由与展示。

    为什么必须是平台实现：这 11 项里没有一项能跨平台通用 ——
    手机号登录的协议、导入的文件格式、扫描的目录、刷新用的接口、
    签到的路径、积分的口径、健康检查的探测方式、导出的结构，全部不同。
    """

    def list(self, db: Any) -> list[dict]:
        """账号列表（含额度、状态、到期信息）。只读，不得有副作用。"""
        ...

    def scan(self) -> dict:
        """扫描本机客户端登录态，返回**不含 token** 的清单。

        返回值必须能安全地直接回给前端：只含 uid / name / realm / 是否有效。
        """
        ...

    def import_local(self, db: Any) -> dict:
        """把扫描到的登录态导入账号库。幂等（同一 uid 更新而非重复插入）。"""
        ...

    def import_record(self, db: Any, record: dict) -> tuple[Any, bool]:
        """导入单条外部记录。返回 `(账号对象, 是否新建)`。"""
        ...

    def refresh(self, db: Any, account_id: int, *, force: bool = False) -> dict:
        """刷新该账号的凭证与额度。"""
        ...

    def keepalive(self, db: Any) -> dict:
        """批量保活：续期临期凭证，避免掉线。"""
        ...

    def checkin(self, db: Any) -> dict:
        """签到。**没有签到概念的平台返回 `{"supported": False}`**，
        不要伪造成功 —— 假的成功比明确的「不支持」更糟。"""
        ...

    def credits(self, db: Any) -> dict:
        """额度 / 积分总览。口径由平台自己定义（积分 / 请求次数 / 百分比）。"""
        ...

    def usage(self, db: Any, **filters: Any) -> dict:
        """该平台的调用统计与明细。"""
        ...

    def health(self, db: Any, account_id: int) -> dict:
        """连通性检查。返回**分段**结果（token / 模型 / 对话 / 额度），
        这样前端能指出「哪一段坏了」，而不是只给一个「失败」。"""
        ...

    def inject(self, db: Any, account_id: int) -> dict:
        """把账号凭证写回客户端，让客户端表现为「已登录该账号」。"""
        ...

    def export(self, db: Any, ids: list[int] | None = None) -> dict:
        """导出账号（备份 / 迁移）。"""
        ...


# ---------------------------------------------------------------------------
# 客户端探测
# ---------------------------------------------------------------------------
@runtime_checkable
class ClientProbe(Protocol):
    """客户端参数的扫描 / 探测 / 修改。

    admin 提供**通用界面与流程**（选择平台 → 扫描 → 列出字段 → 修改 → 备份 → 回滚），
    平台只回答「我的客户端在哪、有哪些可改项、怎么读写」。
    这样「客户端参数」页面切换平台时字段自动变化，不必为新平台重写页面。
    """

    def dirs(self) -> list[str]:
        """该平台客户端的安装 / 数据目录候选（存在的才返回）。"""
        ...

    def probe(self) -> dict:
        """探测客户端状态：版本、安装路径、是否运行中。"""
        ...

    def schema(self) -> list[dict]:
        """可修改项的声明。每项含 `key` / `label` / `type` / `default`。

        admin 据此自动渲染表单 —— 平台不需要写任何前端代码。
        """
        ...

    def read(self, key: str) -> Any:
        """读取当前值。"""
        ...

    def write(self, key: str, value: Any) -> dict:
        """写入（**必须**先备份，并返回备份标识以便回滚）。"""
        ...


# ---------------------------------------------------------------------------
# 平台概览卡片
# ---------------------------------------------------------------------------
@runtime_checkable
class OverviewProvider(Protocol):
    """平台概览卡片的**声明式**来源。

    ## 为什么卡片必须由平台声明

    改造前 `loadPlatformOverview()` 里是两套 `if isTrae { ... } else { ... }`
    写死的分支：新增平台就得再加一个分支，而且前端必须认识每个平台的
    统计口径（Trae 是「区域分布」、WorkBuddy 是「7 天内到期」）。

    现在反过来：平台返回一个**卡片规格列表**，前端只负责渲染：

        [
          {"key": "accounts", "label": "账号总数", "value": 2,
           "sub": "启用 2 · 已禁用 0", "icon": "fa-users", "tone": ""},
          {"key": "credits", "label": "可用积分", "value": 9719,
           "sub": "总额度 11540", "icon": "fa-wallet", "tone": "brand"},
          ...
        ]

    新增平台时前端一行不用改，删除平台时卡片自动消失。
    """

    def overview_cards(self, db: Any) -> list[dict]:
        """返回该平台的概览卡片列表。

        每项的字段：
          * `key`   稳定标识（前端做缓存/动画用，必填）
          * `label` 卡片标题（必填）
          * `value` 主数值（字符串或数字，必填）
          * `sub`   副标题（可空）
          * `icon`  Font Awesome 图标名（可空，默认 `fa-circle`）
          * `tone`  配色：`""` / `"brand"` / `"warn"` / `"danger"`
        实现方负责**不抛异常**：某个统计失败时给 0 并写 `note`，
        而不是让整个概览页打不开。
        """
        ...


# ---------------------------------------------------------------------------
# 网关主体
# ---------------------------------------------------------------------------
@runtime_checkable
class Gateway(Protocol):
    """一个平台网关的完整契约。

    方法分五组：转发 / 目录 / 账号池 / 任务 / 客户端探测。
    """

    #: 平台 id（与 `PlatformSpec.id` 一致）
    platform_id: str

    # ---- 转发（OpenAI / Anthropic 兼容入口）----
    def chat_completions(self, request: Any, body: dict) -> Any:
        """/v1/chat/completions"""
        ...

    def responses(self, request: Any, body: dict) -> Any:
        """/v1/responses"""
        ...

    def messages(self, request: Any, body: dict) -> Any:
        """/v1/messages（Anthropic）"""
        ...

    def count_tokens(self, request: Any, body: dict) -> Any:
        """/v1/messages/count_tokens"""
        ...

    # ---- 目录 ----
    def list_models(self, request: Any) -> dict:
        """/v1/models：**只返回该平台可用的模型**。

        必须自己过滤掉账号调不通的模型（如需要会员订阅的），
        否则客户端会选到一个必然失败的模型。
        """
        ...

    def balance(self, request: Any) -> dict:
        """/v1/balance"""
        ...

    # ---- 账号池 ----
    def account_ops(self) -> AccountOps:
        """返回该平台的账号池实现。"""
        ...

    # ---- 定时任务 ----
    def task_runners(self) -> dict[str, Callable]:
        """该平台支持的定时任务：`{任务名: 执行函数}`。

        admin 的调度器只负责「按时触发 + 记录结果」，
        **不认识任何具体任务名** —— 任务类型是平台知识。
        """
        ...

    # ---- 客户端探测 ----
    def client_probe(self) -> ClientProbe:
        """返回该平台的客户端探测实现。"""
        ...

    # ---- 概览卡片 ----
    def overview_cards(self, db: Any) -> list[dict]:
        """该平台的概览卡片（见 `OverviewProvider.overview_cards`）。

        admin 的「平台管理」页面拿到什么就渲染什么 —— 平台增删自动反映，
        前端不需要认识任何具体平台的统计口径。
        """
        ...
