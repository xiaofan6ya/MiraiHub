"""uskey 现签 —— **每次请求都重签一枚**，不复用。

## 为什么不能缓存（2026-11-18 事故，代价是整条链路瘫了几小时）

    之前这里做的是「按 (model, openid) 建缓存，签一次用到底」，
    理由是实测「同一个 uskey 连发 3 次全部 200，上游不查重放」。

    **这个结论是错的** —— 3 次太少，看不出问题。真实情况：

        缓存条目 `deepseek-v4.1-flash-marvis|1E4AB...` 的 hits 累积到 23，
        然后整个账号开始永久 4100404「请求被风控拦截」。

    对照实验（`tools/probe_fresh_uskey.py`）：

        清空全部缓存 + 每账号现签一枚、立刻发一次  ->  4/4 全 200 ✅
        恢复缓存复用                              ->  全部 403

    ⇒ **上游认的是「客户端每次都签新票」这个行为特征。**
      同一枚 uskey 被反复使用，会被判成非客户端流量（爬虫/盗用），
      而且是**账号级**标记，不像普通限流那样等一会儿就恢复 ——
      实测等 3 分钟、把间隔拉到 25 秒都救不回来，只能等上游自己解封。

## 那么现在怎么做

    每次请求现签。DLL 签名是纯本地计算，实测 <1ms，
    比起「省这一次计算、换来几小时的 403」，直接现签是唯一正确选择。

    DLL 不可用时：返回空串让调用方降级为随机填充（短时间内会被拒，
    但那是环境问题，不是这里该掩盖的）。

## 缓存只用来「降级兜底」

    `load()` 仍然保留，但**只在 DLL 完全不可用时**由调用方当作
    最后一次尝试；正常情况下 `get_or_create()` 一律现签。

    `hits` 字段现在只做观测，不再参与任何「复用」判断。
"""
from __future__ import annotations

import atexit
import collections
import json
import logging
import os
import threading
import time
import uuid

log = logging.getLogger(__name__)

#: 缓存文件位置。跟着 vendor 走，部署时一起拷。
_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))),
    "vendor", "beacon", "uskey_cache.json")

#: 缓存结构版本。字段变了就改这个，旧缓存自动作废。
#:
#: v4：整条「复用」逻辑废弃，旧缓存里的 uskey 全部不可信（可能已被标记），
#: 升版本号让它们自动作废，避免上线后继续拿旧票去撞。
_CACHE_VERSION = 4

#: 缓存条目的最长可信时间（秒）。超过就当作没有 —— 即便有人误用了 `load()`。
#: 设为 0 表示**永不信任缓存**，`load()` 一律返回 None。
_CACHE_TRUST_SECONDS = 0


def _bucket(model: str, openid: str) -> str:
    """缓存键：model + openid 都要，缺一不可（二者都进 bus_info）。"""
    return "%s|%s" % (model or "-", openid or "-")


def _read_all() -> dict:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"version": _CACHE_VERSION, "entries": {}}
    if not isinstance(data, dict) or data.get("version") != _CACHE_VERSION:
        return {"version": _CACHE_VERSION, "entries": {}}
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    return data


def _write_all(data: dict) -> None:
    with _IO_LOCK:
        try:
            os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
            tmp = _CACHE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, _CACHE_PATH)
        except OSError as exc:
            log.warning("uskey 缓存写入失败（不影响本次使用）：%s", exc)


# ----------------------------------------------------------------------
# 观测写盘：**移出请求路径**
# ----------------------------------------------------------------------
# 历史背景：这份文件只是「签名历史」，用来诊断签名是否正常、耗时多少，
# 它**不参与任何请求决策**（`_CACHE_TRUST_SECONDS = 0`，永不回读复用）。
# 但它是每条请求都写的，而且写一次要做「全量 JSON 读 → 全量 JSON 写 →
# os.replace」，还带一把进程内没有的锁 —— 三处代价：
#
#   1. **请求路径上的同步 IO**：签名 <1ms，落盘却要 1~5ms（磁盘忙时更久），
#      而这一切都发生在 `asyncio.to_thread` 的工作线程里，占着线程池的名额；
#   2. **写放大**：并发 N 条请求就是 N 次全量重写，高 QPS 下磁盘成了瓶颈；
#   3. **互相覆盖**：`_read_all()` 到 `os.replace()` 之间没有互斥，
#      两个请求同时写，后写的会把先写的那条**整条丢掉**；临时文件名还相同
#      （`uskey_cache.json.tmp`），多发时还会撞出「文件不存在」的 OSError。
#
# 现在改成：请求线程只做一次 O(1) 的入队，由后台守护线程合并落盘。
# 丢一条诊断记录没有任何影响（`invalidate()` / 探针用的 `save()` 仍是同步写，
# 语义需要立即生效的地方不受影响）。
_PENDING: "collections.deque[dict]" = collections.deque(maxlen=512)
_PENDING_LOCK = threading.Lock()
_IO_LOCK = threading.RLock()

#: 后台合并落盘的间隔（秒）。诊断用途，不需要实时。
_FLUSH_INTERVAL = 2.0

_flusher: threading.Thread | None = None
_flusher_lock = threading.Lock()


def _flusher_loop() -> None:
    while True:
        time.sleep(_FLUSH_INTERVAL)
        try:
            flush_pending()
        except Exception:  # noqa: BLE001 - 观测线程绝不因为异常退出
            log.debug("uskey 缓存后台落盘失败", exc_info=True)


def _ensure_flusher() -> None:
    global _flusher
    if _flusher is not None:
        return
    with _flusher_lock:
        if _flusher is not None:
            return
        thread = threading.Thread(target=_flusher_loop,
                                  name="marvis-uskey-cache-flush",
                                  daemon=True)
        thread.start()
        _flusher = thread
        atexit.register(flush_pending)


def record(*, model: str, openid: str, uskey: str, nonce: str,
           conversation_id: str, response_id: str, bus_info: str,
           client_version: str = "") -> dict:
    """**请求路径专用**：把一条签名历史丢进队列就返回（不碰磁盘）。

    与 `save()` 的区别只有一个：不立即落盘。条目内容完全相同，
    所以 `peek()` / `status()` 稍后看到的东西和以前一样。
    队列满了（512 条）会丢最旧的 —— 那是「落盘速度跟不上 QPS」的信号，
    丢诊断数据比阻塞请求正确。
    """
    entry = _entry(model=model, openid=openid, uskey=uskey, nonce=nonce,
                   conversation_id=conversation_id, response_id=response_id,
                   bus_info=bus_info, client_version=client_version)
    with _PENDING_LOCK:
        _PENDING.append(entry)
    _ensure_flusher()
    return entry


def flush_pending() -> int:
    """把队列里的条目合并成**一次**写盘。返回落盘条数。"""
    with _PENDING_LOCK:
        if not _PENDING:
            return 0
        batch = list(_PENDING)
        _PENDING.clear()

    data = _read_all()
    for entry in batch:
        data["entries"][_bucket(entry.get("model", ""),
                                entry.get("openid", ""))] = entry
    _write_all(data)
    return len(batch)


def load(model: str, openid: str = "") -> dict | None:
    """读缓存条目 —— **默认永不信任**，正常情况返回 None。

    保留这个函数是为了两件事：
        1. 控制台 / 探针需要看历史条目做诊断；
        2. DLL 彻底不可用时，调用方可以显式传 `trust_stale=True` 兜底。

    业务路径**不要**直接用它取 uskey 去发请求 —— 那正是 2026-11-18
    被拉黑的原因。走 `get_or_create()`。
    """
    if not model:
        return None
    entry = _read_all()["entries"].get(_bucket(model, openid))
    if not isinstance(entry, dict):
        return None
    for key in ("uskey", "nonce", "conversation_id", "response_id"):
        if not entry.get(key):
            return None
    if _CACHE_TRUST_SECONDS <= 0:
        return None
    age = time.time() - int(entry.get("created_at") or 0)
    return entry if age <= _CACHE_TRUST_SECONDS else None


def peek(model: str, openid: str = "") -> dict | None:
    """**诊断专用**：不管多旧都返回条目，不做可信性判断。

    控制台要看「以前签过什么」用它；发请求绝不能用它。
    """
    if not model:
        return None
    entry = _read_all()["entries"].get(_bucket(model, openid))
    return entry if isinstance(entry, dict) else None


def _entry(*, model: str, openid: str, uskey: str, nonce: str,
           conversation_id: str, response_id: str, bus_info: str,
           client_version: str = "") -> dict:
    """组一条签名历史条目（纯内存，不落盘）。"""
    return {
        "model": model,
        "openid": openid,
        "uskey": uskey,
        "nonce": nonce,
        "conversation_id": conversation_id,
        "response_id": response_id,
        "bus_info": bus_info,
        "client_version": client_version,
        "created_at": int(time.time()),
        "created_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "hits": 0,
    }


def save(*, model: str, openid: str, uskey: str, nonce: str,
         conversation_id: str, response_id: str, bus_info: str,
         client_version: str = "") -> dict:
    """记一条签名历史并**立即落盘**（探针 / 维护工具用，业务请求路径走 `record()`）。

    以前 `get_or_create()` 调的就是这个函数 —— 于是每条业务请求都在
    请求线程里做一次全量 JSON 重写。现在业务路径改成 `record()`（入队），
    这个同步版本留给「写完马上就要读」的场景（探针、控制台手动触发）。
    """
    entry = _entry(model=model, openid=openid, uskey=uskey, nonce=nonce,
                   conversation_id=conversation_id, response_id=response_id,
                   bus_info=bus_info, client_version=client_version)
    data = _read_all()
    data["entries"][_bucket(model, openid)] = entry
    _write_all(data)
    return entry


def invalidate(model: str = "", openid: str = "", reason: str = "") -> None:
    """删某个桶（或全部）的缓存。"""
    data = _read_all()
    if model:
        data["entries"].pop(_bucket(model, openid), None)
    else:
        data["entries"] = {}
    _write_all(data)
    log.warning("uskey 历史作废 | model=%s openid=%s… %s",
                model or "(全部)", (openid or "")[:8],
                "| " + reason if reason else "")


def touch(model: str, openid: str = "") -> None:
    """给诊断条目记一次「被读取」。

    ⚠ 现在只用于观测：记下来是为了能看出「有没有代码在错误地复用旧票」。
    一旦 hits 开始持续增长，说明有调用方绕过了 `get_or_create()`。
    """
    data = _read_all()
    key = _bucket(model, openid)
    entry = data["entries"].get(key)
    if not isinstance(entry, dict):
        return
    entry["hits"] = int(entry.get("hits") or 0) + 1
    entry["last_used_at"] = int(time.time())
    _write_all(data)


def status() -> dict:
    """给控制台看的状态。"""
    data = _read_all()
    entries = data.get("entries") or {}
    now = time.time()
    out = []
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        created = int(entry.get("created_at") or 0)
        out.append({
            "key": key,
            "model": entry.get("model", ""),
            "openid": str(entry.get("openid", ""))[:10],
            "age_hours": round((now - created) / 3600, 1) if created else 0,
            "hits": entry.get("hits", 0),
            "created_iso": entry.get("created_iso", ""),
            "uskey_head": str(entry.get("uskey", ""))[:16],
        })
    return {"path": _CACHE_PATH, "cached": bool(out),
            "entries": sorted(out, key=lambda x: -x["hits"])}


def get_or_create(*, model: str, openid: str, sign_fn,
                  scene: str = "agent_chat", agent_tag: str = "main",
                  conversation_id: str = "", response_id: str = "",
                  client_version: str = "") -> tuple[str, str, str, str] | None:
    """现签一枚 uskey，返回 (uskey, nonce, conv, rid)。

    ## 名字为什么还叫 get_or_create

    函数名留着是为了不改调用方，但语义已经变成 **always create**：
    **不查缓存、不复用**，每次调用都重新签。

    这是 2026-11-18 事故的直接修复 —— 详见模块顶部说明。
    """
    nonce = str(uuid.uuid4())
    # conv/rid 也每次都新生成：客户端本来就是一问一答各一对 id。
    # 复用同一对 id 同样属于「不像客户端」的行为。
    conversation_id = conversation_id or str(uuid.uuid4())
    response_id = response_id or uuid.uuid4().hex
    bus_info = (
        "marvisAgentTag=%s" % agent_tag
        + "&marvisConversationID=%s" % conversation_id
        + "&marvisResponseID=%s" % response_id
        + "&marvisScene=%s" % scene
        + "&model=%s" % model
        + "&nonce=%s" % nonce
        + "&originOpenID=%s" % openid
    )
    uskey = sign_fn(bus_info)
    if not uskey:
        return None

    # 存一份只为诊断（看签名是否正常、耗时多少），**不会被读回去复用**。
    #
    # ⚠ 这里用 `record()` 而不是 `save()`：入队即返回，请求线程不再做磁盘 IO。
    # 这份历史的唯一用途是「事后看签名长什么样」，晚 2 秒落盘没有任何损失。
    record(model=model, openid=openid, uskey=uskey, nonce=nonce,
           conversation_id=conversation_id, response_id=response_id,
           bus_info=bus_info, client_version=client_version)
    return uskey, nonce, conversation_id, response_id
