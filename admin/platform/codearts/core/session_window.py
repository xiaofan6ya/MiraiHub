"""上游「并发会话数」窗口的本地镜像。

## 为什么需要它（实测 2026-11-04，直打华为云 CodeArts 上游）

上游的限制**不是**「同时在途的请求数」，而是「**同一账号下同时存活的会话数**」：

```
stream=False  连发 6 次 + 并发 4 次  → 全部 200，从不触发
stream=True   连发第 1/2/3 次      → 200
              连发第 4 次           → 400 {"error_code":"TM.00001041", ...}
              等 ~53s 后            → 恢复
```

四个关键事实（缺一不可，结论完全不同）：

1. **按账号计**。换号 = 换一个窗口，所以「撞上限后换号」是有效的补救。
2. **只有流式请求建立会话**。非流式连发 6 次全部通过 —— 所以闸门只管流式。
3. **会话在请求结束后仍滞留 ~50 秒**（实测 53s）。也就是说上游的真实约束是
   「**任意 50 秒窗口内，每账号最多 3 个流式请求**」，而不是「同时 3 个在途」。
4. **会话不可复用**。把上游返回的 chunk id（形如 `as-xxxxxxxx`）通过
   `session_id` / `conversation_id` / `previous_response_id` / `X-Session-Id`
   等字段回传，上游**每次仍新建会话**（返回的 id 每次都不同），窗口照旧被占满。
   也没有「关闭会话」接口：`/api/v2/{chat/,}{session,s}s[/<id>][/close]` 等
   15 种写法全部 404（2026-11-04 逐个探测），所以只能等 TTL。
5. **滞留是从「会话空闲」开始算的**。一次流跑了 5 秒，占位总时长是 `5 + 55`
   —— 所以 `touch()` 必须在**流结束**时再戳一次时间戳。按「请求开始」计时
   会让闸门比上游早放行「流的时长」，实测放行后照样吃 `400 TM.00001041`。

## 网关原有的闸门为什么挡不住

`admin.pool.InFlight` 数的是**在途请求**，请求一结束就释放。上游数的是
**会话**，请求结束后还要占 ~50 秒。两者口径不同：

* 网关认为「这个号空了」（在途 0），上游认为「这个号还有 3 个会话」→ 直接打回。
* 所以撞上限是必然的，不是偶发。

## 这个闸门怎么用

在**发起上游请求之前**占位，让网关自己先知道窗口满了，从而：

* 换号（多账号池时换一个窗口继续）；
* 单账号池时**等待**窗口腾出空位，而不是打上游一个注定失败的 400。

只有**真正拿到上游 200** 才算「会话已建立」。被上游 4xx 拒绝的请求
**没有**建立会话，占位要回滚 —— 否则一次参数错误会白白吃掉一个名额。

## 为什么不直接靠 `MAX_IN_FLIGHT`

`MAX_IN_FLIGHT` 是**所有平台共用**的「在途请求数」闸门，调它会影响 WorkBuddy、
Trae、Qoder 等全部平台的吞吐。而「按会话窗口限流」是 CodeArts 这一个上游的
特有语义，不该污染全局配置 —— 所以做成 CodeArts 专属的闸门。
"""
from __future__ import annotations

import itertools
import os
import threading
import time
from collections import deque

#: 每账号允许同时存活的会话数（上游硬上限 3）。
#:
#: 留 0 余量：上游按「会话」计数，我们按「请求」计数，两者存在偏差，
#: 贴着 3 去跑会在边界上偶发 400。压到 2 换取稳定。
DEFAULT_LIMIT = int(os.getenv("ADMIN_CODEARTS_SESSION_LIMIT", "2"))

#: 会话存活时长（秒）。实测 53s，取 55 留 2s 余量。
#:
#: 它决定了「一次流式请求之后，多久才能再发下一个」。这个值越大，
#: 吞吐越低（每账号 ≈ limit / ttl 个流式请求每秒），但撞 400 的概率越低。
DEFAULT_TTL = float(os.getenv("ADMIN_CODEARTS_SESSION_TTL", "55"))

#: 单次等待的最长时间（秒）。超过就放弃并让客户端稍后重试。
DEFAULT_WAIT_TIMEOUT = float(os.getenv("ADMIN_CODEARTS_SESSION_WAIT", "60"))


class SessionWindow:
    """按账号统计「最近 TTL 秒内建立了多少个上游会话」。

    线程安全。进程内状态（与 `admin.pool.InFlight` 同口径 —— 都是纯内存，
    重启后自然重建）。
    """

    def __init__(self, limit: int = DEFAULT_LIMIT,
                 ttl: float = DEFAULT_TTL):
        self.limit = max(1, int(limit))
        self.ttl = max(1.0, float(ttl))
        self._lock = threading.Lock()
        self._counter = itertools.count(1)
        # uid -> deque[(token, created_at)]
        self._sessions: dict[str, deque[tuple[int, float]]] = {}

    # -- 内部 -------------------------------------------------------------
    def _prune(self, bucket: deque, now: float) -> None:
        """丢掉已过期（会话已在上游释放）的条目。"""
        while bucket and now - bucket[0][1] >= self.ttl:
            bucket.popleft()

    def _used(self, uid: str, now: float) -> int:
        bucket = self._sessions.get(uid)
        if not bucket:
            return 0
        self._prune(bucket, now)
        return len(bucket)

    # -- 对外 -------------------------------------------------------------
    def try_acquire(self, uid: str, now: float | None = None):
        """占一个名额。成功返回 token，窗口已满返回 None。

        token 用于 `rollback`：上游拒绝（未建立会话）时把名额还回去。
        """
        uid = uid or ""
        if not uid:
            return 0
        now = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._sessions.setdefault(uid, deque())
            self._prune(bucket, now)
            if len(bucket) >= self.limit:
                return None
            token = next(self._counter)
            bucket.append((token, now))
            return token

    def rollback(self, uid: str, token) -> None:
        """归还一个名额（上游拒绝了请求，会话并未建立）。"""
        uid = uid or ""
        if not uid or not token:
            return
        with self._lock:
            bucket = self._sessions.get(uid)
            if not bucket:
                return
            for index, (item_token, _ts) in enumerate(bucket):
                if item_token == token:
                    del bucket[index]
                    return

    def touch(self, uid: str, token, now: float | None = None) -> None:
        """把这条会话的释放时刻推迟到 `now + ttl`（**流结束时调用**）。

        ## 为什么必须在「结束时」再戳一次

        上游的滞留是**从会话空闲开始算**的，不是从建立开始算：一次流跑了
        5 秒，它占位的总时长是 `5 + 55`。而本闸门原来在**请求开始**时打时间戳，
        于是「已经跑了 5 秒的请求」在 t=55s 就被当成释放 —— 但上游那边还有
        5 秒没到。

        实测（2026-11-04，5 路并发经网关）：t≈57.8s 时闸门放行了两路，
        上游仍然回 `400 TM.00001041` —— 正是这 5 秒的差值。
        改成本函数后，占位从「流结束」起算，宁可保守少放一路，也不放行
        一个注定 400 的请求（那会把客户端的重试风暴引进来）。
        """
        uid = uid or ""
        if not uid or not token:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._sessions.get(uid)
            if not bucket:
                return
            for index, (item_token, ts) in enumerate(bucket):
                if item_token == token:
                    # deque 必须保持时间序（`_prune` 只看最左）：
                    # 取出来再 append，等于把它挪到最后。
                    del bucket[index]
                    bucket.append((item_token, now))
                    return

    def used(self, uid: str, now: float | None = None) -> int:
        """当前占用了几个名额（观测用）。"""
        uid = uid or ""
        if not uid:
            return 0
        now = time.monotonic() if now is None else now
        with self._lock:
            return self._used(uid, now)

    def wait_time(self, uid: str, now: float | None = None) -> float:
        """这个账号还要等多久才有空位（秒）。空着则返回 0。"""
        uid = uid or ""
        if not uid:
            return 0.0
        now = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._sessions.get(uid)
            if not bucket:
                return 0.0
            self._prune(bucket, now)
            if len(bucket) < self.limit:
                return 0.0
            # 最老的一条过期后才有空位
            return max(0.0, (bucket[0][1] + self.ttl) - now)

    def wait_time_any(self, uids, now: float | None = None) -> float:
        """一组账号里，最快多久有账号腾出空位。"""
        waits = [self.wait_time(uid, now) for uid in uids if uid]
        waits = [w for w in waits if w > 0]
        return min(waits) if waits else 0.0

    def snapshot(self) -> dict:
        """观测快照：uid -> 当前占用数。"""
        now = time.monotonic()
        with self._lock:
            return {uid: self._used(uid, now) for uid in self._sessions}

    def reset(self, uid: str = "") -> None:
        """清空（排障用）。"""
        with self._lock:
            if uid:
                self._sessions.pop(uid, None)
            else:
                self._sessions.clear()


#: 进程级单例。
WINDOW = SessionWindow()
