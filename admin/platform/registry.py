"""平台注册表：平台声明、查找与选择器解析。

设计约束（与仓库既有风格保持一致）：

1. **零上游依赖**：本模块不 import 任何具体平台，也不 import router，
   避免 `admin.server` 装配期出现循环导入。
2. **登记即可用**：平台包在 import 时调用 `register()`；装配方只需
   `import` 各平台包（见 `admin/platform/loader.py`），新增平台不改网关代码。
3. **选择器是字符串**：客户端用 `X-Platform: trae:cn` 这种
   `平台[:区域]` 形式表达意图；解析成 `(platform, realm)` 后交给平台自己处理。
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: 默认平台。老客户端不带任何平台标识时，行为必须与改造前完全一致 ——
#: 因此这里恒为 workbuddy，且注册表保证它永远排在首位。
DEFAULT_PLATFORM = "workbuddy"


@dataclass(frozen=True)
class Realm:
    """平台内的一个区域/区域化部署（例如 Trae 的国内版与国际版）。

    区域不是「平台」：它们共享同一套号池表、同一套记账，只是上游主机、
    客户端版本头与模型目录不同。因此用 realm 表达，而不是再拆一个平台。
    """

    key: str
    label: str
    default: bool = False


@dataclass
class PlatformSpec:
    """一个上游平台的静态声明。

    这里**刻意只放元数据**（供控制台渲染与路由选择），不放可调用对象：
    可调用对象会让注册表变成隐式依赖图，测试时很难替换。真正的行为在
    `gateway` 指向的模块里，按需懒加载。
    """

    #: 平台标识，落库到 `accounts.platform` / `api_keys.platform`。
    id: str
    #: 控制台展示名。
    label: str
    #: 该平台支持的区域；空元组表示平台无区域概念（如 workbuddy）。
    realms: tuple[Realm, ...] = ()
    #: 是否在控制台平台切换条中展示（关闭等于隐藏但保留已入库账号）。
    enabled: bool = True
    #: 是否支持「扫描本机」——即存在可复用的本机客户端登录态目录。
    supports_local_scan: bool = False
    #: 分发模块路径（`pkg.mod:func` 或 `pkg.mod`）。网关按需 import。
    #:
    #: 注意这是**转发入口**：`dispatch.py` 要求它以模块形态提供
    #: `dispatch(request, path)` 与 `PATHS`。
    gateway: str = ""
    #: 平台能力入口（`admin.platform.gateway.Gateway` 的实现）。
    #:
    #: 为什么与 `gateway` 分开：`gateway` 是热路径的**转发实现**
    #: （module，含 `dispatch`），而这里是**管理契约**（账号池 11 项操作、
    #: 任务清单、客户端探测）。两者生命周期与调用方都不同 ——
    #: 混成一个字段会让热路径 import 一堆管理模块。
    #: 留空表示该平台只做转发、不提供管理能力（过渡态）。
    platform_gateway: str = ""
    #: 控制台提示语（一句话说明这个平台的接入方式）。
    notes: str = ""
    #: 图标 key（`admin/console/platform-icons.js`）。留空则用 id 兜底。
    icon: str = ""
    #: **落库平台**：这一项在 `accounts.platform` 里存什么。
    #:
    #: 为什么需要它：Trae 的国内版与国际版在运营上是**两个平台**
    #: （不同主机、不同凭证、不同模型目录、不同额度口径），
    #: 但对存储而言它们是同一套协议实现的两种配置 —— 用同一个
    #: `platform='trae'` + `domain='cn'|'intl'` 存，避免为了「换个区」
    #: 复制一份号池代码。留空表示与 `id` 相同。
    storage_platform: str = ""
    #: **固定区域**：该平台在存储层对应的 `domain` 值。
    #: 非空时选号/统计自动按它过滤，调用方不必再传区域。
    realm: str = ""
    #: 额外元数据，供前端自由使用（不参与后端逻辑）。
    extra: dict = field(default_factory=dict)
    #: 手动授权回调适配器模块；空值表示该授权协议不使用手动回调。
    #: 模块提供 get_session(nonce) 和 parse_callback(params)，按需加载。
    auth_callback: str = ""

    def storage(self) -> str:
        """落库用的平台 id。"""
        return self.storage_platform or self.id

    def realm_keys(self) -> tuple[str, ...]:
        return tuple(r.key for r in self.realms)

    def default_realm(self) -> str:
        if self.realms:
            for realm in self.realms:
                if realm.default:
                    return realm.key
            return self.realms[0].key
        return self.realm

    def normalize_realm(self, value: str | None) -> str:
        """把任意写法归一到本平台合法的 realm；不合法则回落默认值。"""
        if self.realm:
            # 固定区域的平台（trae-cn / trae-intl）不接受覆盖：它本身就是那个区
            return self.realm
        return normalize_realm(self, value)


#: id -> spec。dict 保留插入顺序，`register` 时显式保证 DEFAULT 在最前。
_REGISTRY: dict[str, PlatformSpec] = {}

#: 别称 -> 平台 id。用于兼容既有叫法与大小写变体。
#:
#: 注意 `trae` 这个词现在**歧义**：它既可能是「整个 Trae（两区）」，也可能是
#: 「Trae 的某个区」。在 `resolve()` 里对裸 `trae` 做特殊处理（返回存储平台
#: `trae` 且不带区），这里只为 `get()` 提供一个可用对象。
_ALIASES = {
    "wb": DEFAULT_PLATFORM,
    "workbuddy": DEFAULT_PLATFORM,
    "codebuddy": DEFAULT_PLATFORM,
    "tencent": DEFAULT_PLATFORM,
    "trae": "trae",
    "traecn": "trae-cn",
    "trae-cn": "trae-cn",
    "traeintl": "trae-intl",
    "trae-intl": "trae-intl",
    "trae_intl": "trae-intl",
}


def register(spec: PlatformSpec, *, replace: bool = False) -> PlatformSpec:
    """登记一个平台。重复登记默认抛错，避免两处实现互相覆盖（静默丢配置）。"""
    if not spec.id:
        raise ValueError("平台 id 不能为空")
    if spec.id in _REGISTRY and not replace:
        raise ValueError(f"平台 {spec.id!r} 已登记")
    _REGISTRY[spec.id] = spec
    if spec.id != DEFAULT_PLATFORM:
        _reorder()
    return spec


def _reorder() -> None:
    """保证 DEFAULT_PLATFORM 恒在首位：控制台切换条的顺序即登记顺序。"""
    if DEFAULT_PLATFORM not in _REGISTRY:
        return
    ordered = {DEFAULT_PLATFORM: _REGISTRY[DEFAULT_PLATFORM]}
    for key, spec in _REGISTRY.items():
        if key != DEFAULT_PLATFORM:
            ordered[key] = spec
    _REGISTRY.clear()
    _REGISTRY.update(ordered)


def get(pid: str | None) -> PlatformSpec | None:
    """按 id（或别称）取 spec；未知返回 None。"""
    key = str(pid or "").strip().lower()
    if not key:
        return None
    return _REGISTRY.get(key) or _REGISTRY.get(_ALIASES.get(key, ""))


def all_platforms(*, enabled_only: bool = True) -> list[PlatformSpec]:
    specs = list(_REGISTRY.values())
    if enabled_only:
        specs = [s for s in specs if s.enabled]
    return specs


def ids(*, enabled_only: bool = True) -> list[str]:
    return [s.id for s in all_platforms(enabled_only=enabled_only)]


def normalize_realm(spec: PlatformSpec | None, value: str | None) -> str:
    """把 `CN` / `国内版` / `intl` / `US` / `国际版` 等写法归一到合法 realm 键。

    未登记区域的平台恒返回空串（表示「该平台没有区域概念」）。
    """
    if spec is None or not spec.realms:
        return ""
    raw = str(value or "").strip().lower()
    if not raw:
        return spec.default_realm()
    table = {}
    for realm in spec.realms:
        table[realm.key] = realm.key
        table[realm.label.lower()] = realm.key
    table.update({
        "china": "cn", "zh": "cn", "国内": "cn", "国内版": "cn", "中国": "cn",
        "us": "intl", "global": "intl", "sg": "intl", "国际": "intl",
        "国际版": "intl", "oversea": "intl", "overseas": "intl",
    })
    key = table.get(raw, "")
    return key if key in spec.realm_keys() else spec.default_realm()


def split_selector(value: str | None) -> tuple[str, str]:
    """把 `trae:cn` 拆成 `("trae", "cn")`；无冒号时 realm 为空串。

    只切第一个冒号：`trae:cn:foo` 的 realm 视为 `cn:foo`，由 normalize 兜底丢弃。
    """
    raw = str(value or "").strip()
    if not raw:
        return "", ""
    if ":" in raw:
        head, tail = raw.split(":", 1)
        return head.strip().lower(), tail.strip().lower()
    return raw.lower(), ""


def resolve(*selectors: str | None, fallback: str = DEFAULT_PLATFORM) -> tuple[str, str]:
    """从一组候选选择器里解析出 `(平台 id, realm)`。

    优先级由调用方按参数顺序表达（例如 请求头 > 查询串 > Key 绑定）。
    任何一个候选命中已知平台即返回；全部落空则回落 `fallback`。

    特殊处理 `trae` / `trae:cn` 这两个**兼容写法**：
      · `trae-cn` / `trae-intl` 是正式平台 id，直接命中；
      · `trae`      -> 落库平台 `trae`、不带区（= 两个区都算）；
      · `trae:cn`   -> 落库平台 `trae`、区 `cn`（等价于 `trae-cn`）。
    """
    for selector in selectors:
        if not selector:
            continue
        head, realm = split_selector(selector)
        # 兼容写法：`trae` 与 `trae:cn` 已拆成两个平台，但老客户端/老 Key
        # 可能还在用。`trae` = 整个 Trae（两区），`trae:cn` = 国内版。
        if head == "trae":
            if realm in ("cn", "intl"):
                return "trae", realm
            return "trae", ""
        spec = get(head)
        if spec is None:
            # 允许只写区域（`X-Platform: cn`），按 fallback 平台解释。
            fallback_spec = get(fallback)
            if fallback_spec and fallback_spec.realms:
                normalized = normalize_realm(fallback_spec, head)
                if normalized and normalized == head:
                    return fallback_spec.id, normalized
            continue
        if spec.realm:
            return spec.id, spec.realm
        return spec.id, normalize_realm(spec, realm)
    spec = get(fallback)
    return (spec.id, spec.default_realm()) if spec else (DEFAULT_PLATFORM, "")


def spec_of_account(account) -> PlatformSpec | None:
    """账号行 -> 平台 spec。老数据（platform 为空）按 workbuddy 处理。"""
    pid = getattr(account, "platform", "") or DEFAULT_PLATFORM
    return get(pid) or get(DEFAULT_PLATFORM)


def load_gateway(spec: PlatformSpec):
    """懒加载平台的分发模块。

    `gateway` 支持 `pkg.mod` 或 `pkg.mod:attr`。取不到时返回 None ——
    平台目录被裁掉不应让整个网关起不来。
    """
    target = (spec.gateway or "").strip()
    if not target:
        return None
    module_name, _, attr = target.partition(":")
    import importlib

    try:
        module = importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 - 缺模块属可降级状态
        return None
    if not attr:
        return module
    return getattr(module, attr, None)


def load_platform_gateway(spec: PlatformSpec, realm: str = ""):
    """懒加载平台的**管理契约**实现（`admin.platform.gateway.Gateway`）。

    与 `load_gateway` 的区别：那个返回转发模块（热路径），
    这个返回管理能力的对象（账号池 / 任务 / 客户端探测）。

    解析顺序：
      1. `spec.platform_gateway`（新式，显式声明）
      2. 回落到 `spec.gateway`（过渡期：老平台只声明了转发模块，
         如果那个模块恰好也实现了契约，就直接用）

    `realm` 用于实例化：Trae 这类「一个模块两个区」的平台需要知道
    当前是哪个区。模块级对象直接返回（它自己按请求上下文判断）。
    """
    target = (spec.platform_gateway or spec.gateway or "").strip()
    if not target:
        return None
    module_name, _, attr = target.partition(":")
    import importlib

    try:
        module = importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 - 缺模块属可降级状态
        return None

    obj = getattr(module, attr, None) if attr else module
    if obj is None:
        return None
    # 类 -> 按 realm 实例化；实例/模块 -> 原样返回
    if isinstance(obj, type):
        try:
            return obj(realm) if realm else obj()
        except TypeError:
            return obj()
    return obj
