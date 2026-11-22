"""qimei 自动分配：uid -> 真机 qimei36，一台设备最多 2 个账号。

文件
----
* qimei_table.json —— uid -> qimei36（保持原有形态，兼容 uskey.py 的读路径）。
* qimei_pool.json  —— qimei36 -> {owners, real, created_at, last_rotated, last_used_at}。
  这是「一台设备挂几个号」的唯一权威台账。

两个文件都放在本模块同目录（可用 QIMEI_STATE_DIR / QIMEI_TABLE_PATH /
QIMEI_POOL_PATH 覆盖，测试用临时目录）。显式设置 QIMEI_SNAPSHOT_PATH 时，
改用一个带 revision 的权威快照；不能同时设置旧表/池文件覆盖路径。

硬约束
------
MAX_OWNERS_PER_QIMEI = 2。加载时任何一台设备的 owners 超过 2 个，直接抛
QimeiPoolError。admin.platform.marvis.install() 在进程启动时调用
validate_or_raise()，所以带病状态会让容器起不来，而不是带病跑。

部署可用 QIMEI_MAX_OWNERS_PER_DEVICE 把上限**往下收紧**到 1（一号一设备）：
此时 1-owner 设备不再参与复用，生成失败直接抛 QimeiCapacityError，加载时发现
多账号设备同样拒绝启动 —— 只允许收紧，不允许放宽到 2 以上。

分配顺序（assign）
------------------
1. 表里已有该 uid -> 原样返回（source=existing），只补池台账。
2. 生成一台新的真品（source=generated）。生成器不可用 / 超时 / 重号 ->
   打 WARNING，不降级到派生。
3. 复用池里一台真品（source=shared），优先 0 个 owner（等于独立），
   其次 1 个 owner（共用，占用第 2 个名额）。挑选判据见 _pick_shareable。
4. 池里所有真品都满 2 个 -> 抛 QimeiCapacityError，不写 owners=3。
5. 派生（source=derived）只在显式 allow_derived=True / CLI --allow-derived
   时作为救灾出口，且不落表、只打 WARNING。

状态持久化
----------
QIMEI_SNAPSHOT_PATH 模式将表/池一起写入同目录临时文件并原子替换，序列化
全部写事务，校验 revision 拒绝旧快照覆盖，读路径不创建锁。必须挂载外部目录；
单文件挂载导致的替换失败会报错，绝不在此模式下就地覆盖。
旧双文件模式保留用于兼容和迁移：pool 后 table 的两次写入不是原子事务，
单文件挂载时的就地覆盖分支也不具备崩溃原子性；迁移前不要把它当作原子快照。

CLI
---
    python3 -m admin.platform.marvis.core.qimei_assign --reconcile --dry-run
    python3 -m admin.platform.marvis.core.qimei_assign --reconcile --prune
    python3 -m admin.platform.marvis.core.qimei_assign --assign UID
    python3 -m admin.platform.marvis.core.qimei_assign --release UID
    python3 -m admin.platform.marvis.core.qimei_assign --sync-pool
    python3 -m admin.platform.marvis.core.qimei_assign --serve-generator --host 127.0.0.1 --port 8792
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import errno
import hashlib
import importlib.util
import itertools
import json
import logging
import math
import os
import random
import re
import secrets
import signal
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

try:  # 旧双文件模式的 Linux 锁；快照模式由独立后端提供跨平台锁。
    import fcntl  # type: ignore
except Exception:  # noqa: BLE001
    fcntl = None  # type: ignore

logger = logging.getLogger("admin.platform.marvis.qimei_assign")

#: 硬约束：一台设备最多挂几个账号。改这个数字之前先看模块 docstring。
MAX_OWNERS_PER_QIMEI = 2
#: 部署可把单设备账号数**往下收紧**：`QIMEI_MAX_OWNERS_PER_DEVICE=1` 表示
#: 每个账号独占一台设备 —— 生成失败就拒绝（QimeiCapacityError），绝不降级共用。
#: 硬上限不可上调：上游按设备归因风险，2 号/台是实测安全线。
OWNER_LIMIT_ENV = "QIMEI_MAX_OWNERS_PER_DEVICE"
MAX_EXPORT_BYTES = 16 * 1024 * 1024


def owner_limit() -> int:
    """当前部署的单设备账号上限：默认硬上限，只允许向下收紧到 1。"""
    raw = (os.environ.get(OWNER_LIMIT_ENV) or "").strip()
    if not raw:
        return MAX_OWNERS_PER_QIMEI
    try:
        value = int(raw, 10)
    except ValueError:
        value = 0
    if not 1 <= value <= MAX_OWNERS_PER_QIMEI:
        raise QimeiPoolError("%s 只接受 1..%d，收到 %r"
                             % (OWNER_LIMIT_ENV, MAX_OWNERS_PER_QIMEI, raw))
    return value

#: qimei36 的合法形态：24 hex 摘要 + '3000' 格式标记 + 8 hex 尾段。
QIMEI_MARK = "3000"
QIMEI_RE = re.compile(r"^[0-9a-f]{24}3000[0-9a-f]{8}$")
#: 与 `uskey.DEVICE_TAG` 同一个环境变量：两个模块派生同一串，改一个必须改两个。
DEVICE_TAG = (os.environ.get("MARVIS_QIMEI_DEVICE_TAG") or "mh-qimei:")
DEVICE_MARK = "3000"

#: 生成器命令行属**部署数据**：脚本路径、运行时依赖、网络命名空间之类都在部署机上，
#: 仓库不提供默认值。没配 `QIMEI_GEN_CMD` 时就只走 `QIMEI_GEN_URL`
#: （外部 HTTP 生成服务），两条都没有则报「生成器不可用」而不是偷偷派生。
DEFAULT_GEN_CMD = ""
#: DB 位置同样属部署数据：`QIMEI_DB_PATH` > `ADMIN_DATABASE_URL`(sqlite) > 空。
#: 为空时只有 `--reconcile` 需要它，其余子命令照常工作。
DEFAULT_DB_PATH = ""
STATE_LOCK_NAME = "qimei_state.lock"

_ORM_HOOKS_INSTALLED = False
_SERVE_LOCK = threading.Lock()
_REQUEST_STATE_LOCK = threading.Lock()
_request_state_cache = None
#: rename 的间接引用：容器里状态文件是单文件 bind mount，rename 会 EBUSY，
#: 单测用它可以精确模拟那一步失败（不改全局 os.replace）。
_REPLACE = os.replace


class QimeiError(RuntimeError):
    """qimei 分配相关错误的基类。"""


class QimeiPoolError(QimeiError):
    """池台账不合法（硬约束被破坏）。启动时必须直接失败。"""


class QimeiCommitUncertain(QimeiPoolError):
    """A complete snapshot may be committed; reload before retrying a write."""


class QimeiCapacityError(QimeiError):
    """所有真品都满 2 个号，拒绝继续分配。"""


class QimeiGenError(QimeiError):
    """真品生成器不可用 / 超时 / 输出不合法。"""


class State:
    """table + pool 的内存快照。dirty 表示加载时做过归一化。"""

    __slots__ = ("table", "pool", "dirty", "table_path", "pool_path",
                 "snapshot_path", "revision", "metadata")

    def __init__(self, table: dict, pool: dict, dirty: bool,
                 table_path: Path, pool_path: Path, *, snapshot_path=None,
                 revision: int = 0, metadata=None) -> None:
        self.table = table
        self.pool = pool
        self.dirty = dirty
        self.table_path = table_path
        self.pool_path = pool_path
        self.snapshot_path = snapshot_path
        self.revision = revision
        self.metadata = metadata or {}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _now() -> str:
    return (_dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"))


def _mtime_iso(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return ""
    return (_dt.datetime.fromtimestamp(ts, _dt.timezone.utc)
            .replace(microsecond=0).isoformat().replace("+00:00", "Z"))


def normalize_uid(uid) -> str:
    """uid 规范形：去空白 + 大写（Marvis openid 就是 32 hex 大写）。"""
    return str(uid or "").strip().upper()


def normalize_qimei(value) -> str:
    return str(value or "").strip().lower()


def mask_uid(uid) -> str:
    """日志里的 uid 掩码。"""
    value = str(uid or "")
    if len(value) <= 8:
        return "****"
    return value[:4] + "****" + value[-4:]


def mask_qimei(qimei) -> str:
    value = str(qimei or "")
    if len(value) <= 10:
        return "****"
    return value[:6] + "..." + value[-4:]


def derived_qimei(uid) -> str:
    """与 uskey.qimei_for 完全一致的派生值（仅显式救灾时使用）。"""
    h = hashlib.sha256((DEVICE_TAG + str(uid)).encode("utf-8")).hexdigest()
    return h[:24] + DEVICE_MARK + h[24:32]


def _state_dir(state_dir=None) -> Path:
    if state_dir:
        return Path(state_dir).expanduser().resolve()
    env = (os.environ.get("QIMEI_STATE_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent


def _table_path(state_dir=None, table_path=None) -> Path:
    if table_path:
        return Path(table_path).expanduser().resolve()
    env = (os.environ.get("QIMEI_TABLE_PATH") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return _state_dir(state_dir) / "qimei_table.json"


def _pool_path(state_dir=None, pool_path=None) -> Path:
    if pool_path:
        return Path(pool_path).expanduser().resolve()
    env = (os.environ.get("QIMEI_POOL_PATH") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return _state_dir(state_dir) / "qimei_pool.json"


def _snapshot_path(*, table_path=None, pool_path=None):
    """Strict snapshot mode is explicit and cannot mix legacy file overrides."""
    value = (os.environ.get("QIMEI_SNAPSHOT_PATH") or "").strip()
    if not value:
        return None
    if (table_path or pool_path or
            (os.environ.get("QIMEI_TABLE_PATH") or "").strip() or
            (os.environ.get("QIMEI_POOL_PATH") or "").strip()):
        raise QimeiPoolError("QIMEI_SNAPSHOT_PATH cannot mix legacy file overrides")
    support = _snapshot_support()
    try:
        return support._canonical(value)
    except support.SnapshotError as exc:
        raise QimeiPoolError(str(exc)) from exc


def _snapshot_support():
    """Load the standard-library backend for package and standalone CLI use."""
    if __package__:
        from . import qimei_snapshot
        return qimei_snapshot
    name = "_miraihub_qimei_snapshot"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("qimei_snapshot.py"))
        if spec is None or spec.loader is None:
            raise QimeiPoolError("QIMEI snapshot backend is unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name, None)
            raise
    return module


def _read_json(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise QimeiPoolError("读不了 %s：%s" % (path, exc)) from exc
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise QimeiPoolError("%s 不是合法 JSON：%s" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise QimeiPoolError("%s 顶层必须是对象（uid -> qimei）" % path)
    return data


def _dump_json(path: Path, data: dict) -> None:
    """tmp + rename 原子写；indent 2 / sort_keys / 0600。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".%s.tmp.%d.%s" % (path.name, os.getpid(),
                                            secrets.token_hex(3)))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        try:
            _REPLACE(tmp, path)
        except OSError as exc:
            # 容器里 qimei_table.json / qimei_pool.json 是单文件 bind mount，
            # 对挂载点 rename 会 EBUSY。内容已整份写进 tmp 并 fsync，这里
            # 退回就地回填同一个 inode：宿主与容器永远指向同一个文件，
            # 不会因为换 inode 而让容器读到旧表。
            if exc.errno not in (errno.EBUSY, errno.EXDEV, errno.EPERM,
                                 errno.EACCES):
                raise
            payload = tmp.read_bytes()
            out_fd = os.open(str(path), os.O_WRONLY)
            try:
                offset = 0
                while offset < len(payload):
                    written = os.write(out_fd, payload[offset:])
                    if written <= 0:
                        raise OSError(errno.EIO, "short write", str(path))
                    offset += written
                os.ftruncate(out_fd, len(payload))
                os.fsync(out_fd)
            finally:
                os.close(out_fd)
            logger.debug("qimei 状态文件是 bind mount，已就地写入 %s", path)
        os.chmod(path, 0o600)
        try:  # Windows 不允许对目录 open()，生产 Linux 才有这一步
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError:
            dir_fd = None
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


@contextlib.contextmanager
def _locked(state_dir: Path):
    """跨进程串行化分配（容器里多线程 / 宿主 CLI 同时跑）。"""
    snapshot = _snapshot_path()
    if snapshot is not None:
        support = _snapshot_support()
        try:
            with support.snapshot_lock(snapshot):
                yield
        except support.SnapshotError as exc:
            raise QimeiPoolError(str(exc)) from exc
        return
    path = state_dir / STATE_LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.chmod(path, 0o600)
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


# ---------------------------------------------------------------------------
# 加载与校验
# ---------------------------------------------------------------------------
def _normalise_owners(qimei: str, raw, dirty_flag: list) -> list:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise QimeiPoolError("qimei %s 的 owners 必须是数组" % mask_qimei(qimei))
    owners: list[str] = []
    for item in raw:
        uid = normalize_uid(item)
        if uid and uid not in owners:
            owners.append(uid)
    if len(owners) > MAX_OWNERS_PER_QIMEI:
        raise QimeiPoolError(
            "qimei %s 的 owners=%d，超过硬上限 %d：%s"
            % (mask_qimei(qimei), len(owners), MAX_OWNERS_PER_QIMEI,
               ",".join(mask_uid(u) for u in owners)))
    return owners


def _normalise_entry(qimei: str, raw, default_ts: str, dirty_flag: list) -> dict:
    entry = raw if isinstance(raw, dict) else {}
    owners = _normalise_owners(qimei, entry.get("owners"), dirty_flag)
    if not isinstance(raw, dict):
        dirty_flag.append(qimei)
    return {
        "owners": owners,
        "real": bool(entry.get("real", True)),
        "source": str(entry.get("source") or "migrated"),
        "created_at": str(entry.get("created_at") or default_ts),
        "last_rotated": str(entry.get("last_rotated") or default_ts),
        "last_used_at": (str(entry.get("last_used_at"))
                         if entry.get("last_used_at") else None),
    }


def load_state(state_dir=None, *, table_path=None, pool_path=None) -> State:
    """加载并校验 table + pool。

    - 任一 qimei 的 owners > 2 -> QimeiPoolError（硬约束，启动失败）。
    - table 是 uid 映射的权威；pool 是容量的权威。两者漂移时在内存里归一化
      （table 的 uid 一定在对应 qimei 的 owners 里；一个 uid 只能挂一台设备），
      并把 dirty 置真，下一次写回时落盘。
    - 文件不存在视为空（首次部署时 pool 由 table 迁移出来）。
    """
    snapshot = _snapshot_path(table_path=table_path, pool_path=pool_path)
    if snapshot is not None:
        support = _snapshot_support()
        try:
            payload = support.read_snapshot(snapshot)
        except support.SnapshotError as exc:
            raise QimeiPoolError(str(exc)) from exc
        return _normalise_state(
            payload["table"], payload["pool"], table_path=snapshot,
            pool_path=snapshot, default_ts=_now(), snapshot_path=snapshot,
            revision=payload["revision"], metadata=payload.get("metadata"))
    tp = _table_path(state_dir, table_path)
    pp = _pool_path(state_dir, pool_path)
    return _normalise_state(_read_json(tp), _read_json(pp), table_path=tp,
                            pool_path=pp, default_ts=_mtime_iso(tp) or _now())


def _file_version(path: Path, *, missing_ok: bool) -> tuple:
    try:
        status = path.stat()
    except FileNotFoundError:
        if missing_ok:
            return (path, None)
        raise
    # Legacy bind mounts permit in-place writes. On Windows an equal-sized
    # overwrite can retain all stat fields, so legacy cache keys include bytes.
    content_digest = None
    if missing_ok:
        content_digest = hashlib.sha256(path.read_bytes()).digest()
    return (path, status.st_dev, status.st_ino, status.st_size,
            status.st_mtime_ns, status.st_ctime_ns, content_digest)


def _request_state() -> State:
    """Share validated request state until any source file version changes.

    The returned state is private and read-only to callers. Writers and startup
    validation use load_state instead, so mutations never affect this cache.
    Each lookup stats the resolved source paths. Changed files validate again
    and a failed refresh evicts the previous entry. Optional legacy files may
    appear or disappear, while an explicit snapshot always remains required.
    """
    global _request_state_cache
    snapshot = _snapshot_path()
    paths = (snapshot,) if snapshot is not None else (_table_path(), _pool_path())
    with _REQUEST_STATE_LOCK:
        try:
            for _ in range(3):
                version = tuple(_file_version(path, missing_ok=snapshot is None)
                                for path in paths)
                if _request_state_cache is not None:
                    cached_version, state = _request_state_cache
                    if version == cached_version:
                        return state
                _request_state_cache = None
                state = load_state()
                loaded_paths = ((state.snapshot_path,) if state.snapshot_path is not None
                                else (state.table_path, state.pool_path))
                after = tuple(_file_version(path, missing_ok=snapshot is None)
                              for path in paths)
                if loaded_paths == paths and version == after:
                    _request_state_cache = (version, state)
                    return state
        except OSError:
            _request_state_cache = None
            if snapshot is None:
                return load_state()
            raise QimeiPoolError("QIMEI snapshot could not be loaded") from None
        raise QimeiPoolError("QIMEI state changed during request capture")


def _normalise_state(raw_table, raw_pool, *, table_path, pool_path, default_ts,
                     snapshot_path=None, revision=0, metadata=None,
                     quiet=False) -> State:
    tp, pp = table_path, pool_path
    if snapshot_path is not None:
        quiet = True
        if any(not isinstance(value, str) or not QIMEI_RE.fullmatch(value)
               for value in raw_table.values()):
            raise QimeiPoolError("snapshot table contains an invalid device identity")
        if any(not QIMEI_RE.fullmatch(key) for key in raw_pool):
            raise QimeiPoolError("snapshot pool contains an invalid device identity")
        if any(not isinstance(entry, dict) or
               ("real" in entry and type(entry["real"]) is not bool)
               for entry in raw_pool.values()):
            raise QimeiPoolError("snapshot pool contains invalid metadata types")
    warning = logger.warning if not quiet else (lambda *_args, **_kwargs: None)
    dirty_flag: list[str] = []
    #: 先读表：表里**引用到**的值即使形态不合法也必须保留（历史真值就长那样），
    #: 所以 pool 的键过滤要拿它当白名单。读一次两处复用，别读两遍。
    referenced = {normalize_qimei(value) for value in raw_table.values()
                  if normalize_qimei(value)}
    pool: dict[str, dict] = {}
    for qimei, raw in sorted(raw_pool.items()):
        q = normalize_qimei(qimei)
        if not q:
            continue
        if not QIMEI_RE.match(q) and q not in referenced:
            # 注释键（示例文件里的 `_comment`）静默跳过；其它不合法键要吭声。
            # 关键在于**不能把非设备键当成设备**：一台"设备"如果谁都能挑到，
            # 容量账本（2 号/台）就形同虚设，_pick_shareable 会把假设备发出去。
            if not q.startswith("_"):
                warning("qimei_pool 里的键不是合法 qimei36（已忽略）：%s",
                               mask_qimei(q))
            continue
        if q in pool:
            raise QimeiPoolError("qimei_pool 里有重复键：%s" % mask_qimei(q))
        pool[q] = _normalise_entry(q, raw, default_ts, dirty_flag)

    # 一个 uid 只能出现在一台设备的 owners 里；重复时以字典序靠前的为准。
    owner_to_q: dict[str, str] = {}
    for q in sorted(pool):
        entry = pool[q]
        kept = []
        for uid in entry["owners"]:
            if uid in owner_to_q and owner_to_q[uid] != q:
                warning("qimei_pool 漂移：uid=%s 同时挂在 %s 和 %s，保留前者",
                               mask_uid(uid), mask_qimei(owner_to_q[uid]),
                               mask_qimei(q))
                dirty_flag.append(q)
                continue
            owner_to_q[uid] = q
            kept.append(uid)
        entry["owners"] = kept

    table: dict[str, str] = {}
    for key, value in sorted(raw_table.items()):
        uid = normalize_uid(key)
        q = normalize_qimei(value)
        if not uid or not q:
            continue
        if uid.startswith("_"):
            continue  # 注释键（`_comment` / `_example`），uid 不可能是这种形态
        if uid in table and table[uid] != q:
            warning("qimei_table 漂移：uid=%s 有多个值，保留字典序靠前的",
                           mask_uid(uid))
            continue
        table[uid] = q
        if not QIMEI_RE.match(q):
            warning("qimei_table 里的值形态不合法（仍按真值使用）：uid=%s qimei=%s",
                           mask_uid(uid), mask_qimei(q))
        entry = pool.get(q)
        if entry is None:
            entry = {
                "owners": [],
                "real": True,
                "source": "migrated",
                "created_at": default_ts,
                "last_rotated": default_ts,
                "last_used_at": None,
            }
            pool[q] = entry
            dirty_flag.append(q)
        if uid not in entry["owners"]:
            if len(entry["owners"]) >= owner_limit():
                raise QimeiPoolError(
                    "qimei_table 要求把 uid=%s 挂到 %s，但它已有 %d 个 owner（当前上限 %d）"
                    % (mask_uid(uid), mask_qimei(q), len(entry["owners"]),
                       owner_limit()))
            entry["owners"].append(uid)
            dirty_flag.append(q)

    # 表说 A 用 q1，池里却把 A 挂在 q2 -> 从 q2 摘掉（q1 才是读路径）。
    for q in sorted(pool):
        entry = pool[q]
        kept = []
        for uid in entry["owners"]:
            target = table.get(uid)
            if target is not None and target != q:
                warning("qimei_pool 漂移：uid=%s 应从 %s 摘除（表里指向 %s）",
                               mask_uid(uid), mask_qimei(q), mask_qimei(target))
                dirty_flag.append(q)
                continue
            kept.append(uid)
        entry["owners"] = kept

    # 池里有 owner 但表里没有：可能是「先占名额、后写表」的崩溃窗口。
    # 保留名额（保守），但明确告警，交给 reconcile 修。
    for q in sorted(pool):
        for uid in pool[q]["owners"]:
            if uid not in table:
                warning("qimei_pool 有名额但 qimei_table 缺映射：uid=%s qimei=%s（reconcile 可修）",
                               mask_uid(uid), mask_qimei(q))

    limit = owner_limit()
    if limit < MAX_OWNERS_PER_QIMEI:
        shared = [q for q in sorted(pool) if len(pool[q]["owners"]) > limit]
        if shared:
            raise QimeiPoolError(
                "%s=%d 下这些设备仍挂着多个账号：%s（先把多余账号 release 再启动，"
                "不要放宽上限）"
                % (OWNER_LIMIT_ENV, limit, ", ".join(mask_qimei(q) for q in shared)))

    if snapshot_path is not None and (table != raw_table or pool != raw_pool):
        raise QimeiPoolError("QIMEI snapshot is not canonical; review migration")
    state = State(table=table, pool=pool, dirty=bool(dirty_flag),
                  table_path=tp, pool_path=pp, snapshot_path=snapshot_path,
                  revision=revision, metadata=metadata)
    return state


def validate_or_raise(state_dir=None) -> State:
    """启动钩子：池不合法就抛 QimeiPoolError。"""
    state = load_state(state_dir)
    logger.info("qimei 池校验通过：设备 %d 台 / 绑定 %d 个（上限 %d 号/台）",
                len(state.pool), len(state.table), owner_limit())
    return state


def _git_worktree_root(path: Path):
    """往上找 `.git`（目录，或 worktree 的 gitdir 文件）；找到返回该层目录。"""
    current = Path(path)
    while True:
        try:
            if (current / ".git").exists():
                return current
        except OSError:
            pass
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _guard_state_write(target: Path) -> None:
    """禁止把状态写回仓库内的默认目录（2026-11-22 事故）。

    开发机上 `qimei_assign.py` 住在 git 工作树里，同目录的 `qimei_table.json` /
    `qimei_pool.json` 曾经是**线上真值**。`_state_dir()` 在既没给 `state_dir`
    也没设 `QIMEI_STATE_DIR` 时回落到这个目录，于是任何漏传 state_dir 的
    assign / release / reconcile 都会直接改真表（已发生过两次）。

    这里只拦**默认回落**这一种情况：目标父目录就是模块目录、且模块目录在
    git 工作树内 → 抛错；显式给的 tmp 目录不受影响。容器镜像不带 `.git`
    （`.dockerignore` 有 `.git`），生产行为不变。
    """
    try:
        resolved = Path(target).resolve()
    except OSError:
        return
    module_dir = Path(__file__).resolve().parent
    if resolved.parent != module_dir:
        return
    if _git_worktree_root(module_dir) is None:
        return
    raise QimeiError(
        "拒绝写入仓库内的默认 qimei 状态目录（%s）：该目录里的 "
        "qimei_table.json / qimei_pool.json 是部署真值。请显式指定 "
        "QIMEI_STATE_DIR 或 --state-dir 指向仓库外/tmp 目录。" % module_dir)


def save_state(state: State) -> None:
    """快照模式比较 revision 后整体提交；旧模式先 pool 后 table。"""
    _guard_state_write(state.snapshot_path or state.table_path)
    if state.snapshot_path is not None:
        support = _snapshot_support()
        _normalise_state(state.table, state.pool, table_path=state.snapshot_path,
                         pool_path=state.snapshot_path, default_ts=_now(),
                         snapshot_path=state.snapshot_path)
        try:
            payload = support.write_snapshot(
                state.snapshot_path, table=state.table, pool=state.pool,
                expected_revision=state.revision, metadata=state.metadata)
        except support.SnapshotCommitUncertain as exc:
            raise QimeiCommitUncertain(str(exc)) from exc
        except support.SnapshotError as exc:
            raise QimeiPoolError(str(exc)) from exc
        state.revision = payload["revision"]
        state.dirty = False
        return
    _dump_json(state.pool_path, state.pool)
    _dump_json(state.table_path, state.table)
    state.dirty = False


def _read_import_object(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("nonfinite number")

    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream, object_pairs_hook=unique,
                              parse_constant=invalid_constant)
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise QimeiPoolError("legacy import requires two valid JSON objects") from None


def _state_summary(state: State, *, dry_run=False, status="valid") -> dict:
    return {"ok": True, "status": status, "dry_run": bool(dry_run),
            "revision": state.revision, "bindings": len(state.table),
            "devices": len(state.pool)}


def migrate_state(source_dir, *, dry_run=False) -> dict:
    """Import legacy state without generation, source writes or lossy healing."""
    destination = _snapshot_path()
    if destination is None:
        raise QimeiPoolError("migration requires QIMEI_SNAPSHOT_PATH")
    source = Path(source_dir).expanduser().resolve()
    tp, pp = source / "qimei_table.json", source / "qimei_pool.json"
    if destination in (tp, pp):
        raise QimeiPoolError("snapshot destination cannot replace a legacy source")
    table_input, pool_input = _read_import_object(tp), _read_import_object(pp)
    comments = {"table": {}, "pool": {}}
    table, pool = {}, {}
    for key, value in table_input.items():
        if key.startswith("_"):
            comments["table"][key] = value
            continue
        if (not isinstance(value, str) or not normalize_uid(key) or
                not QIMEI_RE.fullmatch(normalize_qimei(value))):
            raise QimeiPoolError("legacy table requires manual migration review")
        uid, qimei = normalize_uid(key), normalize_qimei(value)
        if uid in table:
            raise QimeiPoolError("legacy table contains duplicate normalized identities")
        table[uid] = qimei
    allowed = {"owners", "real", "source", "created_at", "last_rotated", "last_used_at"}
    for key, value in pool_input.items():
        if key.startswith("_"):
            comments["pool"][key] = value
            continue
        qimei = normalize_qimei(key)
        if (not QIMEI_RE.fullmatch(qimei) or qimei in pool or
                not isinstance(value, dict) or set(value) - allowed or
                not isinstance(value.get("owners"), list) or
                any(not isinstance(uid, str) or not normalize_uid(uid)
                    for uid in value["owners"])):
            raise QimeiPoolError("legacy pool requires manual migration review")
        pool[qimei] = {**value, "owners": [normalize_uid(uid) for uid in value["owners"]]}
    state = _normalise_state(table, pool, table_path=tp, pool_path=pp,
                             default_ts=_mtime_iso(tp) or _now(), quiet=True)
    if state.table != table or set(state.pool) != set(pool):
        raise QimeiPoolError("migration would change bindings or device records")
    for qimei, entry in pool.items():
        if any(type(state.pool[qimei].get(key)) is not type(value) or
               state.pool[qimei].get(key) != value for key, value in entry.items()):
            raise QimeiPoolError("migration would change ownership or known metadata")
    metadata = {"legacy_comments": comments}
    state.snapshot_path, state.revision, state.metadata = destination, 1, metadata
    support = _snapshot_support()
    try:
        if destination.exists():
            existing = support.read_snapshot(destination)
            if (existing["table"] != state.table or existing["pool"] != state.pool or
                    existing.get("metadata", {}) != metadata):
                raise QimeiPoolError("snapshot destination conflicts with legacy import")
            state.revision = existing["revision"]
            return _state_summary(state, dry_run=dry_run, status="unchanged")
        if not dry_run:
            payload = support.write_snapshot(destination, table=state.table,
                                             pool=state.pool, expected_revision=0,
                                             metadata=metadata)
            state.revision = payload["revision"]
    except support.SnapshotError as exc:
        raise QimeiPoolError(str(exc)) from exc
    return _state_summary(state, dry_run=dry_run,
                          status="ready" if dry_run else "migrated")


def _export_destination(value, snapshot: Path) -> Path:
    if not isinstance(value, (str, Path)) or (isinstance(value, str) and not value):
        raise QimeiPoolError("export requires a new explicit destination directory")
    raw = Path(value).expanduser().absolute()
    if not raw.name or raw.name in (".", "..") or raw.exists() or raw.is_symlink():
        raise QimeiPoolError("export destination must not already exist")
    parent = raw.parent.resolve(strict=True)
    if not parent.is_dir():
        raise QimeiPoolError("export destination parent must already be a directory")
    destination = parent / raw.name
    if destination == snapshot or destination in snapshot.parents:
        raise QimeiPoolError("export destination cannot replace snapshot storage")
    return destination


def _export_json_size(value: dict, remaining: int) -> int:
    encoder = json.JSONEncoder(indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False)
    size = 1  # trailing newline
    for chunk in encoder.iterencode(value):
        size += len(chunk)
        if size > remaining:
            raise QimeiPoolError("export data exceeds the byte budget")
    return size


def _write_export_json(path: Path, value: dict, remaining: int) -> int:
    """Create one private file exclusively, bounding aggregate encoded bytes."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        encoder = json.JSONEncoder(indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False)
        written = 0
        for chunk in encoder.iterencode(value):
            written += len(chunk)  # ensure_ascii makes character/byte counts equal.
            if written + 1 > remaining:
                raise QimeiPoolError("export data exceeds the byte budget")
            stream.write(chunk)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return written + 1


def export_state(to_legacy_dir, *, dry_run=False) -> dict:
    """Export the latest validated snapshot into an exclusively created directory.

    The source snapshot is never changed. Actual export holds its writer lock;
    preview does not lock, sample a clock, create paths or write. Destination
    parents must already exist, and existing directories/files/symlinks are never
    reused. Snapshot input and aggregate ASCII JSON output each have a 16 MiB
    cap. Table/pool values are copied semantically without normalization loss.
    Present snapshot metadata is retained in _qimei_snapshot_metadata.json;
    legacy readers ignore it, so operators must keep it when rolling back or
    later remigrating. No automatic metadata reimport is performed here.

    This is not an atomic directory publication. A partial export is preserved
    for inspection after failure, without deleting or replacing evidence. Both
    data files and any metadata sidecar are fsynced and read back before the
    count-only _qimei_export_complete.json marker is written last. Operators
    must require BOTH a successful process result and that marker before use.
    A final marker/directory sync failure can leave a marker with uncertain
    durability; marker presence alone never authorizes rollback. Quiescing the
    old writer and applying an export remain separate operator actions.
    """
    try:
        snapshot = _snapshot_path()
        if snapshot is None:
            raise QimeiPoolError("export requires QIMEI_SNAPSHOT_PATH")
        destination = _export_destination(to_legacy_dir, snapshot)
        if not snapshot.is_file():
            raise QimeiPoolError("export snapshot is missing")
        support = _snapshot_support()
        with contextlib.nullcontext() if dry_run else _locked(_state_dir()):
            if snapshot.stat().st_size > MAX_EXPORT_BYTES:
                raise QimeiPoolError("export snapshot exceeds the byte budget")
            payload = support.read_snapshot(snapshot)
            # Validate the domain without a clock read or mutation of raw data.
            _normalise_state(payload["table"], payload["pool"], table_path=snapshot,
                             pool_path=snapshot, default_ts="", snapshot_path=snapshot,
                             revision=payload["revision"], metadata=payload.get("metadata"))
            has_metadata = "metadata" in payload
            report = {"ok": True, "status": "ready" if dry_run else "exported",
                      "dry_run": bool(dry_run), "revision": payload["revision"],
                      "bindings": len(payload["table"]), "devices": len(payload["pool"]),
                      "has_metadata": has_metadata}
            files = [("qimei_table.json", payload["table"]),
                     ("qimei_pool.json", payload["pool"])]
            if has_metadata:
                files.append(("_qimei_snapshot_metadata.json", payload["metadata"]))
            marker = {"schema": 1, "revision": report["revision"],
                      "bindings": report["bindings"], "devices": report["devices"],
                      "has_metadata": has_metadata}
            remaining = MAX_EXPORT_BYTES
            for _name, value in [*files, ("_qimei_export_complete.json", marker)]:
                remaining -= _export_json_size(value, remaining)
            if dry_run:
                return report
            destination.mkdir(mode=0o700)
            remaining = MAX_EXPORT_BYTES
            for name, value in files:
                remaining -= _write_export_json(destination / name, value, remaining)
            for name, value in files:
                path = destination / name
                if path.stat().st_size > MAX_EXPORT_BYTES:
                    raise QimeiPoolError("export read-back exceeds the byte budget")
                actual = _read_import_object(path)
                encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=True,
                                           allow_nan=False, separators=(",", ":"))
                # Python equality merges True/1 and 1/1.0. Canonical JSON streams
                # preserve these distinctions without two whole encoded copies.
                if any(expected_chunk != actual_chunk for expected_chunk, actual_chunk in
                       itertools.zip_longest(encoder.iterencode(value), encoder.iterencode(actual))):
                    raise QimeiPoolError("export read-back verification failed")
            support._fsync_directory(destination)
            support._fsync_directory(destination.parent)
            _write_export_json(destination / "_qimei_export_complete.json", marker, remaining)
            support._fsync_directory(destination)
            return report
    except Exception:
        raise QimeiPoolError("snapshot export failed; destination may be incomplete or durability uncertain") from None


# ---------------------------------------------------------------------------
# 生成真品
# ---------------------------------------------------------------------------
def random_mac() -> str:
    """本地管理、单播的随机 MAC（02:xx:xx:xx:xx:xx）。"""
    tail = [random.SystemRandom().randrange(0, 256) for _ in range(5)]
    return "02:" + ":".join("%02x" % b for b in tail)


def _parse_qimei_output(text: str) -> str:
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if re.fullmatch(r"[0-9a-f]{36}", line) and QIMEI_RE.match(line):
            return line
        found = re.search(r"qimei36=([0-9a-f]{36})", line)
        if found and QIMEI_RE.match(found.group(1)):
            return found.group(1)
    return ""


def _gen_timeout() -> float:
    try:
        return max(5.0, float(os.environ.get("QIMEI_GEN_TIMEOUT") or 30))
    except (TypeError, ValueError):
        return 30.0


def _http_generate(url: str, mac: str) -> str:
    import urllib.request
    body = json.dumps({"mac": mac}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_gen_timeout() + 5) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except Exception as exc:  # noqa: BLE001
        raise QimeiGenError("生成器 HTTP 不可用（%s）：%s" % (url, exc)) from exc
    value = normalize_qimei((payload or {}).get("qimei36"))
    if not QIMEI_RE.match(value):
        raise QimeiGenError("生成器返回的 qimei36 不合法：%r" % (payload,))
    return value


def _cmd_generate(cmd: str, mac: str) -> str:
    tag = mac.replace(":", "")[-4:]
    port = str(random.SystemRandom().randrange(8801, 8900))
    octet = str(random.SystemRandom().randrange(60, 120))
    argv: list[str] = []
    if os.environ.get("QIMEI_GEN_SUDO") == "1":
        argv += ["sudo", "-n"]
    argv += [os.environ.get("QIMEI_GEN_SHELL") or "/bin/bash", cmd, mac, tag,
             port, octet]
    timeout = _gen_timeout()
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            stdout, stderr = proc.communicate()
        raise QimeiGenError("生成器超时（>%.0fs，mac=%s）" % (timeout, mac))
    if proc.returncode != 0:
        raise QimeiGenError(
            "生成器退出 %s（mac=%s）：%s"
            % (proc.returncode, mac, (stderr or stdout or "").strip()[-300:]))
    value = _parse_qimei_output(stdout)
    if not value:
        raise QimeiGenError("生成器没有输出合法 qimei36：%r"
                            % ((stdout or "")[-300:],))
    return value


def generate_real_qimei(*, mac: str | None = None) -> str:
    """走宿主生成器：优先 HTTP 生成服务，其次本机脚本。"""
    mac = mac or random_mac()
    url = (os.environ.get("QIMEI_GEN_URL") or "").strip()
    if url:
        return _http_generate(url, mac)
    cmd = (os.environ.get("QIMEI_GEN_CMD") or DEFAULT_GEN_CMD).strip()
    if not cmd or not os.path.exists(cmd):
        raise QimeiGenError(
            "生成器不可用：QIMEI_GEN_CMD=%r 不存在（配 QIMEI_GEN_URL 指向外部"
            " HTTP 生成服务，或把 QIMEI_GEN_CMD 指向本机能执行的生成脚本）"
            % cmd)
    return _cmd_generate(cmd, mac)


# ---------------------------------------------------------------------------
# 分配 / 回收
# ---------------------------------------------------------------------------
def _managed_accounts(state: State) -> dict:
    """Recognize the opt-in namespace without importing request-time code."""
    if "marvis_managed_identity_v1" not in state.metadata:
        return {}
    namespace = state.metadata["marvis_managed_identity_v1"]
    if (type(namespace) is not dict or set(namespace) != {"schema", "accounts"}
            or type(namespace["schema"]) is not int or namespace["schema"] != 1
            or type(namespace["accounts"]) is not dict):
        raise QimeiPoolError("managed device identity metadata is invalid")
    return namespace["accounts"]


def _forget_managed_account(state: State, uid: str) -> bool:
    accounts = _managed_accounts(state)
    if uid not in accounts:
        return False
    remaining = dict(accounts)
    del remaining[uid]
    state.metadata["marvis_managed_identity_v1"] = {"schema": 1, "accounts": remaining}
    state.dirty = True
    return True


def _pick_shareable(state: State, uid: str) -> str:
    """挑一台可复用的真品。

    判据（可落地、无外部依赖）：
      1. real == True；
      2. owners < 2；
      3. 按 (owners 数量, last_used_at, created_at, qimei) 升序：
         0 个 owner 的最优先（等于独立设备，还省一次生成），
         然后是最久没被用过的 1-owner 设备，最后用 qimei 字符串做确定性兜底。

    为什么不是「按 4100404 历史最少」：池里没有按设备聚合的风控计数（4100404
    是账号级日志），拿不到可信数据；等以后加了 per-qimei 风险列再插到第一排序位。
    """
    candidates = []
    managed = _managed_accounts(state)
    limit = owner_limit()
    for qimei, entry in state.pool.items():
        if not entry.get("real", True):
            continue
        owners = entry.get("owners") or []
        if any(owner in managed for owner in owners) and uid not in owners:
            continue  # Managed devices remain exclusive, even for legacy callers.
        if uid in owners:
            return qimei
        if len(owners) >= limit:
            continue
        candidates.append((len(owners), entry.get("last_used_at") or "",
                           entry.get("created_at") or "", qimei))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return candidates[0][3]


def assign_ex(uid, *, generator=None, state_dir=None, allow_derived: bool = False,
              dry_run: bool = False) -> dict:
    """分配（详细版）：返回 uid / qimei36 / source / owners。

    dry_run 只预览当前快照中的容量，不生成、不写盘，也不创建锁文件。
    """
    user = normalize_uid(uid)
    if not user:
        raise ValueError("uid 不能为空")
    sd = _state_dir(state_dir)
    limit = owner_limit()
    with (contextlib.nullcontext() if dry_run else _locked(sd)):
        state = load_state(sd)
        now = _now()

        qimei = state.table.get(user)
        if user in _managed_accounts(state) and not qimei:
            raise QimeiPoolError("managed account assignment requires explicit rebinding")
        if qimei:
            entry = state.pool.setdefault(qimei, {
                "owners": [], "real": True, "source": "migrated",
                "created_at": now, "last_rotated": now, "last_used_at": None})
            if user not in entry["owners"]:
                if len(entry["owners"]) >= limit:
                    raise QimeiPoolError(
                        "qimei %s 已满 %d 号，无法补挂 uid=%s"
                        % (mask_qimei(qimei), limit,
                           mask_uid(user)))
                entry["owners"].append(user)
                state.dirty = True
            entry["last_used_at"] = now
            state.dirty = True
            if not dry_run:
                save_state(state)
            owners = len(entry["owners"])
            logger.info("qimei assigned uid=%s source=existing owners=%d qimei=%s",
                        mask_uid(user), owners, mask_qimei(qimei))
            return {"uid": user, "qimei36": qimei, "source": "existing",
                    "owners": owners, "qimei_masked": mask_qimei(qimei)}

        generated = ""
        gen_error = None
        if not dry_run and generator is not False:
            try:
                generated = (generator() if callable(generator)
                             else generate_real_qimei())
                generated = normalize_qimei(generated)
                if not QIMEI_RE.match(generated):
                    raise QimeiGenError("生成的 qimei36 形态不合法：%r" % generated)
                if generated in state.pool:
                    raise QimeiGenError("生成的 qimei36 已在池里：%s"
                                        % mask_qimei(generated))
            except Exception as exc:  # noqa: BLE001
                gen_error = exc
                generated = ""
                logger.warning(
                    "qimei generate failed uid=%s reason=%s; fallback=shared",
                    mask_uid(user), str(exc)[:300])

        if generated:
            state.pool[generated] = {
                "owners": [user], "real": True, "source": "generated",
                "created_at": now, "last_rotated": now, "last_used_at": now}
            state.table[user] = generated
            state.dirty = True
            if not dry_run:
                save_state(state)
            logger.info("qimei assigned uid=%s source=generated owners=1 qimei=%s",
                        mask_uid(user), mask_qimei(generated))
            return {"uid": user, "qimei36": generated, "source": "generated",
                    "owners": 1, "qimei_masked": mask_qimei(generated)}

        shared = _pick_shareable(state, user)
        if shared:
            entry = state.pool[shared]
            entry["owners"].append(user)
            entry["last_used_at"] = now
            state.table[user] = shared
            state.dirty = True
            if not dry_run:
                save_state(state)
            owners = len(entry["owners"])
            logger.info("qimei assigned uid=%s source=shared owners=%d qimei=%s",
                        mask_uid(user), owners, mask_qimei(shared))
            return {"uid": user, "qimei36": shared, "source": "shared",
                    "owners": owners, "qimei_masked": mask_qimei(shared)}

        if allow_derived:
            value = derived_qimei(user)
            logger.warning(
                "qimei assigned uid=%s source=derived owners=0 qimei=%s "
                "(显式救灾：未落表、未占名额；派生值会自报假的)",
                mask_uid(user), mask_qimei(value))
            return {"uid": user, "qimei36": value, "source": "derived",
                    "owners": 0, "qimei_masked": mask_qimei(value)}

        raise QimeiCapacityError(
            "qimei 已满 %d 号：所有真品设备都已有 %d 个账号，拒绝分配 uid=%s"
            "（生成失败：%s）"
            % (limit, limit, mask_uid(user),
               str(gen_error or "生成器未启用")[:200]))


def assign(uid, *, generator=None, state_dir=None) -> str:
    """需求里的主入口：返回 qimei36，并原子写回两个 JSON。"""
    return assign_ex(uid, generator=generator, state_dir=state_dir)["qimei36"]


def release(uid, *, state_dir=None, reason: str = "") -> dict:
    """账号删除 / uid 变更：把名额还回池子（设备本身保留，可再复用）。"""
    user = normalize_uid(uid)
    if not user:
        return {"uid": user, "released": False, "owners": 0}
    sd = _state_dir(state_dir)
    with _locked(sd):
        state = load_state(sd)
        _forget_managed_account(state, user)
        old = state.table.pop(user, None)
        freed = []
        for qimei, entry in state.pool.items():
            if user in entry["owners"]:
                entry["owners"].remove(user)
                freed.append(qimei)
                state.dirty = True
        if old and old not in freed:
            freed.append(old)
        if state.dirty:
            save_state(state)
        owners = (len(state.pool.get(old, {}).get("owners", []))
                  if old else 0)
        logger.info("qimei released uid=%s qimei=%s owners=%d reason=%s",
                    mask_uid(user),
                    mask_qimei(old) if old else "-", owners, reason or "-")
        return {"uid": user, "released": bool(old or freed),
                "qimei36": old or "", "owners": owners,
                "freed": [mask_qimei(q) for q in freed]}


# ---------------------------------------------------------------------------
# 显式快照轮换：不影响旧双文件分配/回收，也不自动安排任务。
# ---------------------------------------------------------------------------
def _rotation_mode() -> None:
    if _snapshot_path() is None:
        raise QimeiPoolError("rotation requires QIMEI_SNAPSHOT_PATH")


def _rotation_commit(state: State, users: list[str], *, generator, dry_run: bool,
                     eligible: int, timestamp=None) -> dict:
    """Generate the complete batch before mutating it; save one revision only."""
    managed = _managed_accounts(state)
    if any(user in managed for user in users):
        raise QimeiPoolError("managed accounts require a full device identity reset")
    report = {"ok": True, "status": "ready" if dry_run and users else "unchanged",
              "dry_run": bool(dry_run), "revision": state.revision,
              "bindings": len(state.table), "devices": len(state.pool),
              "eligible": eligible, "selected": len(users), "rotated": 0,
              "uids": list(users), "rotations": []}
    if dry_run or not users:
        return report
    if generator is not None and not callable(generator):
        raise QimeiGenError("rotation requires an enabled generator")
    generate = generate_real_qimei if generator is None else generator
    used = set(state.pool)
    replacements = []
    for user in users:
        try:
            value = generate()
            if type(value) is not str:
                raise ValueError("invalid generated type")
            value = normalize_qimei(value)
            if QIMEI_RE.fullmatch(value) is None or value in used:
                raise ValueError("invalid or existing generated identity")
        except Exception:
            raise QimeiGenError("rotation generation failed or returned an existing identity") from None
        used.add(value)
        replacements.append((user, state.table[user], value))
    timestamp = _now() if timestamp is None else timestamp
    for user, old, value in replacements:
        state.pool[old]["owners"].remove(user)
        state.pool[value] = {
            "owners": [user], "real": True, "source": "generated",
            "created_at": timestamp, "last_rotated": timestamp, "last_used_at": timestamp}
        state.table[user] = value
    state.dirty = True
    save_state(state)
    report.update(status="rotated", revision=state.revision, devices=len(state.pool),
                  rotated=len(users),
                  rotations=[{"uid": user, "previous_qimei36": old, "qimei36": value}
                             for user, old, value in replacements])
    logger.info("qimei rotation accounts=%d revision=%d", len(users), state.revision)
    return report


def rotate_ex(uid, *, generator=None, state_dir=None, dry_run: bool = False) -> dict:
    """Rotate one existing real binding atomically in explicit snapshot mode.

    No sharing or derived fallback is permitted. The old device and its metadata
    remain in the pool, preserving other owners. Preview only reports eligibility
    and never creates locks, calls a generator or saves. Generation happens under
    the writer lock, matching assignment's serialization contract.
    """
    _rotation_mode()
    user = normalize_uid(uid)
    if not user or user.startswith("_"):
        raise QimeiPoolError("rotation requires an existing real assignment")
    sd = _state_dir(state_dir)
    with contextlib.nullcontext() if dry_run else _locked(sd):
        state = load_state(sd)
        old = state.table.get(user)
        if old is None or state.pool[old]["real"] is not True:
            raise QimeiPoolError("rotation requires an existing real assignment")
        report = _rotation_commit(state, [user], generator=generator,
                                  dry_run=dry_run, eligible=1)
        report["uid"] = user
        if report["rotated"]:
            report["qimei36"] = state.table[user]
        return report


def _rotation_clock(now):
    if now is None:
        return _dt.datetime.now(_dt.timezone.utc)
    if type(now) is not _dt.datetime or now.tzinfo is None:
        raise QimeiPoolError("rotation clock requires a timezone-aware datetime")
    try:
        if now.utcoffset() is None:
            raise ValueError("naive clock")
        return now.astimezone(_dt.timezone.utc)
    except Exception:
        raise QimeiPoolError("rotation clock requires a timezone-aware datetime") from None


def _rotation_timestamp(value):
    try:
        if type(value) is not str or not value:
            raise ValueError("missing timestamp")
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("naive timestamp")
        return parsed.astimezone(_dt.timezone.utc)
    except Exception:
        raise QimeiPoolError("rotation requires valid timezone-aware last_rotated timestamps") from None


def rotate_due(*, older_than_days, generator=None, state_dir=None,
               dry_run: bool = False, max_accounts: int = 10, now=None) -> dict:
    """Atomically rotate a bounded batch ordered by UTC age, then normalized UID.

    The positive day threshold is explicit. ``now`` may be an explicit aware
    datetime for deterministic operation; otherwise UTC is sampled once. Only
    assigned real entries participate. Their timestamps must be valid and aware;
    malformed age data fails closed. The inclusive cutoff is ``now - days``.
    All selected identities must be fresh against the entire pool and batch.
    Generation runs under the writer lock, so bound max_accounts (1..1000) and
    generator latency. No timer, database access or scheduling is installed.
    """
    _rotation_mode()
    if (type(older_than_days) not in (int, float) or not 0 < older_than_days
            or (type(older_than_days) is float and not math.isfinite(older_than_days))):
        raise QimeiPoolError("rotation age must be an exact finite positive day threshold")
    if type(max_accounts) is not int or not 1 <= max_accounts <= 1000:
        raise QimeiPoolError("rotation account limit must be an exact integer from 1 to 1000")
    clock = _rotation_clock(now)
    try:
        cutoff = clock - _dt.timedelta(days=older_than_days)
    except (OverflowError, ValueError):
        raise QimeiPoolError("rotation age exceeds the supported UTC range") from None
    sd = _state_dir(state_dir)
    with contextlib.nullcontext() if dry_run else _locked(sd):
        state = load_state(sd)
        candidates = []
        for user, value in state.table.items():
            entry = state.pool[value]
            if entry["real"] is not True:
                continue
            rotated = _rotation_timestamp(entry["last_rotated"])
            if rotated <= cutoff:
                candidates.append((rotated, user))
        candidates.sort()
        users = [user for _rotated, user in candidates[:max_accounts]]
        stamp = clock.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return _rotation_commit(state, users, generator=generator, dry_run=dry_run,
                                eligible=len(candidates), timestamp=stamp)


# ---------------------------------------------------------------------------
# DB reconcile
# ---------------------------------------------------------------------------
def _default_db_path() -> str:
    env = (os.environ.get("QIMEI_DB_PATH") or "").strip()
    if env:
        return env
    url = (os.environ.get("ADMIN_DATABASE_URL") or "").strip()
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///"):]
    if url.startswith("sqlite://"):
        return url[len("sqlite://"):]
    return DEFAULT_DB_PATH


def _db_rows(db_path: str, *, active_only: bool) -> list[tuple[int, str]]:
    path = Path(db_path)
    if not path.exists():
        raise QimeiError("数据库不存在：%s（用 --db 指定）" % db_path)
    sql = "select id, uid from accounts where uid is not null and uid <> ''"
    if active_only:
        sql += " and platform = 'marvis' and status = 'active'"
    sql += " order by id"
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=10)
        try:
            rows = con.execute(sql).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        raise QimeiError("读数据库失败（%s）：%s" % (db_path, exc)) from exc
    return [(int(r[0]), str(r[1])) for r in rows]


def reconcile(*, db_path: str | None = None, dry_run: bool = False,
              prune: bool = False, generator=None, state_dir=None) -> dict:
    """对账；快照模式将完整操作串行化，预览保持只读。"""
    # Snapshot mode has a reentrant lock covering every read/mutate/save path.
    transaction = (_locked(_state_dir(state_dir))
                   if _snapshot_path() is not None and not dry_run
                   else contextlib.nullcontext())
    with transaction:
        return _reconcile_impl(db_path=db_path, dry_run=dry_run, prune=prune,
                               generator=generator, state_dir=state_dir)


def _reconcile_impl(*, db_path: str | None = None, dry_run: bool = False,
                    prune: bool = False, generator=None, state_dir=None) -> dict:
    """扫描 active 且未绑定的 Marvis 账号，逐个分配。

    dry_run 只读、不生成、不写盘；prune 额外回收「账号已不存在」的名额。
    """
    sd = _state_dir(state_dir)
    db = db_path or _default_db_path()
    if not db:
        raise QimeiError("未指定数据库：用 --db，或设 QIMEI_DB_PATH / "
                         "ADMIN_DATABASE_URL（sqlite:///...）")
    state = load_state(sd)
    rows = _db_rows(db, active_only=True)
    active = {normalize_uid(uid) for _id, uid in rows}
    owner_to_q = {}
    for qimei, entry in state.pool.items():
        for uid in entry["owners"]:
            owner_to_q[uid] = qimei

    missing: list[dict] = []
    healed: list[dict] = []
    for acc_id, raw_uid in rows:
        uid = normalize_uid(raw_uid)
        if not uid:
            continue
        if uid in state.table:
            continue
        if uid in owner_to_q:
            healed.append({"id": acc_id, "uid": uid,
                           "qimei36": owner_to_q[uid]})
        else:
            missing.append({"id": acc_id, "uid": uid})

    report = {
        "ok": True, "dry_run": bool(dry_run), "db": db,
        "state_dir": str(sd), "accounts": len(rows),
        "assigned": [], "healed": healed, "failed": [], "missing": missing,
        "pruned": [], "message": "",
    }
    if dry_run:
        if not missing and not healed:
            report["message"] = ("无需分配：%d 个 active Marvis 账号均已绑定 qimei"
                                 % len(rows))
        else:
            report["message"] = ("dry-run：%d 个账号待分配、%d 个待补表"
                                 % (len(missing), len(healed)))
        return report

    changed = False
    for item in healed:
        state.table[item["uid"]] = item["qimei36"]
        changed = True
        logger.info("qimei healed uid=%s qimei=%s",
                    mask_uid(item["uid"]), mask_qimei(item["qimei36"]))
    if prune:
        all_uids = {normalize_uid(uid) for _id, uid in
                    _db_rows(db, active_only=False)}
        for qimei in sorted(state.pool):
            entry = state.pool[qimei]
            kept = []
            for uid in entry["owners"]:
                if uid in all_uids:
                    kept.append(uid)
                else:
                    report["pruned"].append({"uid": uid, "qimei36": qimei})
                    changed = True
            entry["owners"] = kept
        for uid in sorted(state.table):
            if uid not in all_uids:
                report["pruned"].append({"uid": uid,
                                         "qimei36": state.table[uid]})
                del state.table[uid]
                changed = True
        for uid in list(_managed_accounts(state)):
            if uid not in all_uids:
                _forget_managed_account(state, uid)
                changed = True
    if changed:
        save_state(state)

    for item in missing:
        try:
            result = assign_ex(item["uid"], generator=generator, state_dir=sd)
            report["assigned"].append({**item, **result})
        except Exception as exc:  # noqa: BLE001
            logger.error("qimei assign failed uid=%s reason=%s",
                         mask_uid(item["uid"]), str(exc)[:300])
            report["failed"].append({**item, "error": str(exc)})
    report["ok"] = not report["failed"]
    if not missing and not healed and not report["pruned"]:
        report["message"] = ("无需分配：%d 个 active Marvis 账号均已绑定 qimei"
                             % len(rows))
    else:
        report["message"] = ("reconcile：分配 %d、补表 %d、失败 %d、回收 %d"
                             % (len(report["assigned"]), len(healed),
                                len(report["failed"]), len(report["pruned"])))
    return report


# ---------------------------------------------------------------------------
# ORM 删除钩子：账号删除后回收名额
# ---------------------------------------------------------------------------
def install_orm_hooks() -> None:
    """在进程内注册 Account 删除钩子（覆盖所有 ORM 删除路径）。

    删除发生在 flush 阶段，此时事务还没提交；如果在 after_delete 里直接改
    JSON，一旦事务回滚就会出现「账号还在、名额已释放、表里没映射」的坏状态。
    所以先把 uid 记到 session.info，after_commit 才真正 release，
    after_rollback 直接丢弃。
    """
    global _ORM_HOOKS_INSTALLED
    if _ORM_HOOKS_INSTALLED:
        return
    try:
        from sqlalchemy import event
        from sqlalchemy.orm import Session, object_session
        from admin.models import Account
    except Exception as exc:  # noqa: BLE001
        logger.warning("qimei ORM 钩子未安装（%s）", exc)
        return

    @event.listens_for(Account, "after_delete")
    def _qimei_after_delete(mapper, connection, target):  # noqa: ANN001
        try:
            if (getattr(target, "platform", "") or "") != "marvis":
                return
            uid = normalize_uid(getattr(target, "uid", ""))
            if not uid:
                return
            session = object_session(target)
            if session is None:
                return
            session.info.setdefault("_qimei_release_uids", []).append(uid)
        except Exception:  # noqa: BLE001
            logger.warning("qimei 删除钩子记录失败", exc_info=True)

    @event.listens_for(Session, "after_commit")
    def _qimei_after_commit(session):  # noqa: ANN001
        uids = session.info.pop("_qimei_release_uids", [])
        for uid in dict.fromkeys(uids):
            try:
                release(uid, reason="account_deleted")
            except Exception:  # noqa: BLE001
                logger.warning("qimei 回收失败 uid=%s", mask_uid(uid),
                               exc_info=True)

    @event.listens_for(Session, "after_rollback")
    def _qimei_after_rollback(session):  # noqa: ANN001
        session.info.pop("_qimei_release_uids", None)

    _ORM_HOOKS_INSTALLED = True
    logger.info("qimei ORM 删除钩子已安装")


# ---------------------------------------------------------------------------
# 生成服务（宿主）
# ---------------------------------------------------------------------------
def serve_generator(host: str, port: int) -> None:
    """极简 HTTP 生成服务：POST /qimei {"mac": "..."} -> {"qimei36": "..."}。

    给**自己生成不了**的一方用：生成能力（脚本、依赖、网络命名空间）留在
    能跑它的那台机器上，通过 HTTP 暴露；调用方配
    `QIMEI_GEN_URL=http://<host>:<port>/qimei` 即可，不必把生成器依赖
    装进同一个容器。

    只做三件事：校验入参 MAC、调 `QIMEI_GEN_CMD`、把 stdout 里的
    qimei36 摘出来。不存任何表，**它自己也不知道哪台设备已经发给谁** ——
    名额账本在 `qimei_pool.json` 那一侧。
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        server_version = "qimei-gen/1"

        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path.split("?")[0] in ("/health", "/"):
                self._json(200, {"ok": True, "cmd": os.environ.get(
                    "QIMEI_GEN_CMD") or DEFAULT_GEN_CMD})
            else:
                self._json(404, {"ok": False, "error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path.split("?")[0] != "/qimei":
                self._json(404, {"ok": False, "error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except ValueError:
                self._json(400, {"ok": False, "error": "bad json"})
                return
            mac = str((payload or {}).get("mac") or "").strip().lower()
            if mac and not re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", mac):
                self._json(400, {"ok": False, "error": "bad mac"})
                return
            try:
                cmd = (os.environ.get("QIMEI_GEN_CMD") or DEFAULT_GEN_CMD).strip()
                if not os.path.exists(cmd):
                    raise QimeiGenError("生成器不存在：%s" % cmd)
                with _SERVE_LOCK:
                    value = _cmd_generate(cmd, mac or random_mac())
                self._json(200, {"ok": True, "qimei36": value})
            except Exception as exc:  # noqa: BLE001
                logger.error("生成服务失败：%s", exc)
                self._json(500, {"ok": False, "error": str(exc)[:300]})

        def log_message(self, fmt, *args):  # noqa: ANN001
            logger.info("qimei-gen %s - %s", self.address_string(), fmt % args)

    server = ThreadingHTTPServer((host, port), Handler)
    logger.info("qimei 生成服务监听 http://%s:%d/qimei", host, port)
    server.serve_forever()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
class _SummaryOnlyArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        raise QimeiError("snapshot summary arguments are invalid") from None


def _build_parser(*, summary_only=False) -> argparse.ArgumentParser:
    parser_class = _SummaryOnlyArgumentParser if summary_only else argparse.ArgumentParser
    parser = parser_class(
        prog="python3 -m admin.platform.marvis.core.qimei_assign",
        description="qimei 自动分配 / 回收 / 对账（一台设备最多 2 个号）")
    parser.add_argument("--state-dir", default="", help="qimei_table/pool 所在目录")
    parser.add_argument("--db", default="",
                        help="accounts 表所在 sqlite 路径"
                             "（默认取 QIMEI_DB_PATH / ADMIN_DATABASE_URL）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--verbose", action="store_true", help="DEBUG 日志")
    parser.add_argument("--allow-derived", action="store_true",
                        help="救灾：池满时允许派生值（不落表、只打 WARNING）")
    parser.add_argument("--no-generate", action="store_true",
                        help="不生成真品，只用池里现成的设备")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--reconcile", action="store_true", help="扫描 active 账号补齐分配")
    action.add_argument("--assign", default="", metavar="UID", help="给单个 uid 分配")
    action.add_argument("--release", default="", metavar="UID", help="回收单个 uid 名额")
    action.add_argument("--sync-pool", action="store_true", help="只把 table 迁移成 pool")
    action.add_argument("--serve-generator", action="store_true", help="起宿主生成服务")
    action.add_argument("--migrate-state", action="store_true",
                        help="从旧表/池迁入 QIMEI_SNAPSHOT_PATH，不生成或轮换")
    action.add_argument("--validate-state", action="store_true", help="校验原子快照")
    action.add_argument("--export-state", action="store_true",
                        help="导出最新快照到全新旧表/池目录（需成功结果和完成标记）")
    action.add_argument("--rotate", default=None, metavar="UID", help="原子轮换单个现有真机绑定（仅快照）")
    action.add_argument("--rotate-due-days", type=float, default=None, metavar="DAYS",
                        help="原子轮换达到显式天数的绑定（仅快照）")
    parser.add_argument("--max-rotate-accounts", type=int, default=10,
                        help="单次轮换上限 1..1000（默认 10）")
    parser.add_argument("--from-legacy-dir", default="", help="迁移来源目录")
    parser.add_argument("--to-legacy-dir", default="", help="导出目的目录（必须不存在且父目录已存在）")
    parser.add_argument("--summary-only", action="store_true", help="状态操作只输出计数和状态")
    parser.add_argument("--dry-run", action="store_true", help="只读演练，不生成不写盘")
    parser.add_argument("--prune", action="store_true", help="回收账号已不存在的名额")
    parser.add_argument("--host", default="127.0.0.1",
                        help="生成服务监听地址（跨容器调用时显式改成该网卡地址）")
    parser.add_argument("--port", type=int, default=8792, help="生成服务端口")
    return parser


def _print_report(report: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print("qimei reconcile: db=%s state=%s accounts=%d assigned=%d healed=%d failed=%d pruned=%d dry_run=%s"
          % (report["db"], report["state_dir"], report["accounts"],
             len(report["assigned"]), len(report["healed"]),
             len(report["failed"]), len(report["pruned"]),
             report["dry_run"]))
    for item in report["assigned"]:
        print("  assigned uid=%s source=%s owners=%d"
              % (mask_uid(item["uid"]), item.get("source", "?"),
                 item.get("owners", 0)))
    for item in report["healed"]:
        print("  healed   uid=%s qimei=%s" % (mask_uid(item["uid"]),
                                             mask_qimei(item["qimei36"])))
    for item in report["failed"]:
        print("  FAILED   uid=%s error=%s" % (mask_uid(item["uid"]),
                                             item["error"][:200]))
    for item in report["pruned"]:
        print("  pruned   uid=%s qimei=%s" % (mask_uid(item["uid"]),
                                             mask_qimei(item["qimei36"])))
    print(report["message"])


def main(argv=None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    # argparse accepts long-option abbreviations, and can reject an attached
    # value before it reaches the summary flag. Cover both forms before parsing.
    summary_requested = any(type(value) is str and value.startswith("--")
                            and len(value.split("=", 1)[0]) > 2
                            and "--summary-only".startswith(value.split("=", 1)[0])
                            for value in arguments)
    try:
        args = _build_parser(summary_only=summary_requested).parse_args(arguments)
    except QimeiError:
        print(json.dumps({"ok": False, "status": "argument_error"}, sort_keys=True))
        return 2
    rotation_action = args.rotate is not None or args.rotate_due_days is not None
    if args.summary_only and not (args.migrate_state or args.validate_state or args.export_state or rotation_action):
        _build_parser().error("--summary-only requires a snapshot state operation")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sd = args.state_dir or None
    if rotation_action:
        try:
            if args.rotate is not None:
                report = rotate_ex(args.rotate, generator=False if args.no_generate else None,
                                   state_dir=sd, dry_run=args.dry_run)
            else:
                report = rotate_due(older_than_days=args.rotate_due_days,
                                    generator=False if args.no_generate else None, state_dir=sd,
                                    dry_run=args.dry_run, max_accounts=args.max_rotate_accounts)
        except QimeiError:
            print(json.dumps({"ok": False, "status": "rotation_failed"}, sort_keys=True))
            return 3
        if args.summary_only:
            report = {name: report[name] for name in ("ok", "status", "dry_run", "revision",
                                                       "bindings", "devices", "eligible", "selected", "rotated")}
        print(json.dumps(report, sort_keys=True))
        return 0
    if args.migrate_state or args.validate_state or args.export_state:
        try:
            if args.migrate_state:
                if not args.from_legacy_dir:
                    raise QimeiPoolError("migration source directory is required")
                report = migrate_state(args.from_legacy_dir, dry_run=args.dry_run)
            elif args.export_state:
                report = export_state(args.to_legacy_dir, dry_run=args.dry_run)
            else:
                if _snapshot_path() is None:
                    raise QimeiPoolError("validation requires QIMEI_SNAPSHOT_PATH")
                report = _state_summary(load_state(sd))
        except QimeiError:
            print(json.dumps({"ok": False, "status": "state_operation_failed",
                              "kind": "QimeiPoolError"}, sort_keys=True))
            return 3
        print(json.dumps(report, sort_keys=True))
        return 0
    try:
        if args.serve_generator:
            serve_generator(args.host, args.port)
            return 0
        if args.reconcile:
            report = reconcile(db_path=args.db or None, dry_run=args.dry_run,
                               prune=args.prune,
                               generator=False if args.no_generate else None,
                               state_dir=sd)
            _print_report(report, args.json)
            return 0 if report["ok"] else 2
        if args.assign:
            result = assign_ex(args.assign, state_dir=sd,
                               allow_derived=args.allow_derived,
                               generator=False if args.no_generate else None,
                               dry_run=args.dry_run)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2,
                                 sort_keys=True))
            else:
                print("qimei assigned uid=%s source=%s owners=%d qimei=%s"
                      % (mask_uid(result["uid"]), result["source"],
                         result["owners"], result["qimei36"]))
            return 0
        if args.release:
            result = release(args.release, state_dir=sd, reason="cli")
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2,
                                 sort_keys=True))
            else:
                print("qimei released uid=%s qimei=%s owners=%d"
                      % (mask_uid(result["uid"]),
                         mask_qimei(result["qimei36"]), result["owners"]))
            return 0
        if args.sync_pool:
            with _locked(_state_dir(sd)):
                state = load_state(sd)
                save_state(state)
            print("qimei pool 已同步：设备 %d 台 / 绑定 %d 个"
                  % (len(state.pool), len(state.table)))
            return 0
        _build_parser().print_help()
        return 0
    except QimeiPoolError as exc:
        logger.error("qimei_pool 校验失败，拒绝启动/继续：%s", exc)
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc),
                              "kind": "QimeiPoolError"}, ensure_ascii=False))
        return 3
    except QimeiCapacityError as exc:
        logger.error("%s", exc)
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc),
                              "kind": "QimeiCapacityError"}, ensure_ascii=False))
        return 2
    except QimeiError as exc:
        logger.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
