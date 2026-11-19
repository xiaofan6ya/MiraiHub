"""按账号固定出口（egress）—— 可插拔的**出口池**，仓库里不带任何出口数据。

## 要解决的问题

上游会按「同一出口 IP + 多账号」聚簇，于是「所有账号共用宿主出口」这种
形态本身就是一个特征。这里把出口选择做成**按账号稳定映射**：

    idx = int(sha256(str(account_key))[:8], 16) % len(pool)

同一账号永远命中同一出口（不会一会儿这个 IP、一会儿那个 IP —— 那更像
异常），不同账号尽量分散。

## 出口从哪来（**数据一律不进仓库**）

按优先级取到即停：

1. `MARVIS_EGRESS_POOL` —— 出口列表本身，可以是 JSON 数组
   （`["http://gw-a.example:8080"]`）或逗号分隔串。
   若该值是**已存在的文件路径**，按文件读（兼容「环境变量给路径」的早期用法）。
2. `MARVIS_EGRESS_POOL_FILE` —— 指向 JSON 文件：
   `{"exits": ["http://gw-a.example:8080"]}`。
3. 模块同目录的 `egress_pool.json`（存在才读；仓库只带
   `egress_pool.example.json` 说明格式）。

全都没配 → `pick()` 返回 `""`，调用方回退到平台级代理 / 直连。
**出口池是可选加速项，不是单点故障**：没有它系统照样跑。

## 线程语义

`_pool()` 按「环境变量原值」缓存；改了 env（或轮换出口）后调 `reload()`
即可生效，不需要重启进程。
"""
import hashlib
import json
import logging
import os

logger = logging.getLogger(__name__)

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
#: 只有**存在**才会被读；仓库只提交 `egress_pool.example.json`。
DEFAULT_POOL_FILE = os.path.join(_MODULE_DIR, "egress_pool.json")

_CACHE = None
_CACHE_KEY = None


def _normalize(items):
    """去空、去重、保持顺序（重复出口等于给同一个 IP 加权，没意义）。"""
    out = []
    for item in items or []:
        value = str(item or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def _parse_inline(raw: str):
    """解析 `MARVIS_EGRESS_POOL` 的内联值（JSON 数组或逗号分隔串）。"""
    text = str(raw or "").strip()
    if not text:
        return []
    if text[:1] in "[{":
        try:
            data = json.loads(text)
        except ValueError:
            logger.warning("MARVIS_EGRESS_POOL 不是合法 JSON，按逗号串解析")
            return _normalize(text.split(","))
        items = data.get("exits", []) if isinstance(data, dict) else data
        return _normalize(items if isinstance(items, (list, tuple)) else [items])
    return _normalize(text.split(","))


def _read_file(path: str):
    """读出口池文件；不存在返回 []（**不刷警告** —— 默认就是不带文件）。"""
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        logger.warning("出口池文件 %s 解析失败（按空池处理）：%s", path, exc)
        return []
    items = data.get("exits", []) if isinstance(data, dict) else data
    return _normalize(items if isinstance(items, (list, tuple)) else [items])


def _load():
    raw = str(os.environ.get("MARVIS_EGRESS_POOL") or "").strip()
    if raw:
        # 兼容早期用法：环境变量直接给文件路径。
        if raw[:1] not in "[{" and os.path.isfile(raw):
            return _read_file(raw)
        return _parse_inline(raw)
    path = str(os.environ.get("MARVIS_EGRESS_POOL_FILE") or "").strip()
    return _read_file(path or DEFAULT_POOL_FILE)


def _pool():
    global _CACHE, _CACHE_KEY
    key = (os.environ.get("MARVIS_EGRESS_POOL", ""),
           os.environ.get("MARVIS_EGRESS_POOL_FILE", ""))
    if _CACHE is None or key != _CACHE_KEY:
        _CACHE = _load()
        _CACHE_KEY = key
        if _CACHE:
            logger.info("出口池已加载：%d 个出口", len(_CACHE))
        else:
            logger.debug("出口池为空（回退平台出口 / 直连）")
    return _CACHE


def reload():
    """丢掉缓存重新加载（改过环境变量、轮换出口后调用）。"""
    global _CACHE, _CACHE_KEY
    _CACHE = None
    _CACHE_KEY = None
    return _pool()


def exits():
    """当前出口列表的副本（自检 / 日志用；出口 URL 不含凭证，别把凭证写进池）。"""
    return list(_pool())


def pick(account_key) -> str:
    """按账号标识**稳定**选一个出口；池为空返回 ""（= 不指定出口）。

    稳定性来自 `sha256(str(account_key))`：同一账号恒定同一出口，
    与进程、时间无关，所以换 worker / 重启都不会导致出口漂移。
    """
    pool = _pool()
    if not pool:
        return ""
    key = str(account_key or "")
    digest = hashlib.sha256(key.encode()).hexdigest()
    return pool[int(digest[:8], 16) % len(pool)]
