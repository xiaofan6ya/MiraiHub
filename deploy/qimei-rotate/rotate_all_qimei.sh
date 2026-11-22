#!/usr/bin/env bash
# rotate_all_qimei.sh — 批量轮换 qimei：先攒后写、只写一次、只部署一次
#
# 用法:
#   rotate_all_qimei.sh [--accounts id1,id2,...] [--all] [--deploy] [--dry-run] [--tag TAG]
#
# 设计:
#   1) 先逐个生成新 qimei（串行，间隔 QIMEI_GEN_GAP 秒）；
#   2) 每个都过形态闸门 ^[0-9a-f]{24}3000[0-9a-f]{8}$，且批内互不相同、不与现有表重复；
#   3) 任一失败 → 中止，一个字节都不写表（先攒后写）；
#   4) 全部通过才一次性 upsert：先备份到 $QIMEI_LOG_DIR/backup/，再**就地写同一 inode**
#      —— 表在容器里是单文件 bind mount，rename 会让容器继续看旧 inode（见 README）；
#   5) 只有 --deploy 才部署：优先热重载（配了 QIMEI_RELOAD_CMD 就无需重启），
#      没配/失败则回落一次 compose build + up（秒级切换）；
#   6) 流水写入 $QIMEI_LOG_DIR/qimei-rotations.txt，每行带 batch=；
#   7) --dry-run：只生成 + 校验，不写表、不部署（演练用）。
#
# 环境变量（括号内为默认值）:
#   MIRAIHUB_DIR(/home/admin/MiraiHub)
#   CONTAINER(miraihub-admin-1)
#   QIMEI_STATE_DIR($MIRAIHUB_DIR/admin/platform/marvis/core)
#   QIMEI_LOG_DIR(/var/log/qimei-rotate)
#   QIMEI_GEN_URL("")    纯 Python 生成服务地址：POST /qimei {"mac": "..."} -> {"qimei36": "..."}
#   QIMEI_GEN_CMD("")    本机生成脚本（部署数据；老 Wine 采值脚本放这里，仓库不给默认值）
#   QIMEI_GEN_PREFIX("") QIMEI_GEN_CMD 的 QIMEI_PREFIX 前缀目录（可选）
#   QIMEI_RELOAD_CMD("") 热重载命令（配好即无需重启容器）
#   QIMEI_DEPLOY_MODE(auto|reload|restart) 默认 auto
#   QIMEI_WRITE_MODE(inplace|rename)       默认 inplace（rename 只能靠重启生效）
#   QIMEI_GEN_GAP(1) QIMEI_GEN_TIMEOUT(120) QIMEI_BATCH(<时间戳>-<pid>)
#
# 退出码: 0 成功 / 2 参数或环境错 / 3 账号或 uid 取不到 / 4 形态闸门失败 /
#         5 生成失败或生成器未配置 / 6 写表失败 / 7 部署或校验失败
set -uo pipefail

MIRAIHUB_DIR=${MIRAIHUB_DIR:-/home/admin/MiraiHub}
CONTAINER=${CONTAINER:-miraihub-admin-1}
QIMEI_STATE_DIR=${QIMEI_STATE_DIR:-$MIRAIHUB_DIR/admin/platform/marvis/core}
QIMEI_LOG_DIR=${QIMEI_LOG_DIR:-/var/log/qimei-rotate}
QIMEI_GEN_URL=${QIMEI_GEN_URL:-}
QIMEI_GEN_CMD=${QIMEI_GEN_CMD:-}
QIMEI_GEN_PREFIX=${QIMEI_GEN_PREFIX:-}
QIMEI_GEN_TIMEOUT=${QIMEI_GEN_TIMEOUT:-120}
QIMEI_RELOAD_CMD=${QIMEI_RELOAD_CMD:-}
QIMEI_DEPLOY_MODE=${QIMEI_DEPLOY_MODE:-auto}
QIMEI_WRITE_MODE=${QIMEI_WRITE_MODE:-inplace}

TABLE=$QIMEI_STATE_DIR/qimei_table.json
ROTLOG=$QIMEI_LOG_DIR/qimei-rotations.txt
BACKUP_DIR=$QIMEI_LOG_DIR/backup
LOCK=$QIMEI_LOG_DIR/.qimei-rotate.lock
SHAPE_RE='^[0-9a-f]{24}3000[0-9a-f]{8}$'
GAP=${QIMEI_GEN_GAP:-1}

MODE=""
ACCS_ARG=""
DEPLOY=0
DRYRUN=0
TAG=""

# 以 root 跑（systemd oneshot）时不需要 sudo；手工跑时用它提权。
SUDO=""
if [ "$(id -u)" != "0" ] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

# 流水（qimei-rotations.txt）与备份里落的是 uid / mac / qimei —— 设备真值。
# 默认 022 会让同机其它用户读到，这里只给自己看。
umask 077

usage() {
  cat <<'USAGE'
rotate_all_qimei.sh [--accounts id1,id2,...] [--all] [--deploy] [--dry-run] [--tag TAG]

  --all                轮换所有 active 的 marvis 账号
  --accounts id1,id2   只轮换指定账号（逗号分隔）
  --tag TAG            生成器 tag（默认 rotHHMMSS）
  --dry-run            只生成 + 校验，不写表、不部署
  --deploy             写表后部署：优先热重载，无重载钩子则 restart 一次
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --all) MODE=all; shift ;;
    --accounts) MODE=list; ACCS_ARG="${2:-}"; shift 2 ;;
    --deploy) DEPLOY=1; shift ;;
    --dry-run) DRYRUN=1; shift ;;
    --tag) TAG="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[ -z "$MODE" ] && { echo "必须给 --all 或 --accounts id1,id2" >&2; exit 2; }
[ "$DRYRUN" = "1" ] && [ "$DEPLOY" = "1" ] && { echo "--dry-run 与 --deploy 互斥（dry-run 不写表也不部署）" >&2; exit 2; }
case "$QIMEI_DEPLOY_MODE" in auto|reload|restart) ;; *) echo "QIMEI_DEPLOY_MODE 只接受 auto|reload|restart" >&2; exit 2 ;; esac
case "$QIMEI_WRITE_MODE" in inplace|rename) ;; *) echo "QIMEI_WRITE_MODE 只接受 inplace|rename" >&2; exit 2 ;; esac
[ -r "$TABLE" ] || { echo "读不到真值表：$TABLE" >&2; exit 2; }
[ -n "$QIMEI_GEN_URL" ] || [ -n "$QIMEI_GEN_CMD" ] || {
  echo "生成器未配置：设 QIMEI_GEN_URL（纯 Python 生成服务）或 QIMEI_GEN_CMD（本机脚本）" >&2
  exit 5
}
[ -n "$QIMEI_GEN_URL" ] && ! command -v curl >/dev/null 2>&1 && { echo "QIMEI_GEN_URL 需要 curl" >&2; exit 2; }

mkdir -p "$QIMEI_LOG_DIR" 2>/dev/null || true
[ -w "$QIMEI_LOG_DIR" ] || { echo "日志目录不可写：$QIMEI_LOG_DIR" >&2; exit 2; }

# 同一时刻只允许一个批量轮换（timer 与 autorotate 可能撞车）——没有 flock 就别跑，
# 否则「加锁失败」会被误读成「已有人在跑」而静默跳过整次轮换。
command -v flock >/dev/null 2>&1 || { echo "[x] 缺 flock（util-linux），拒绝在无锁状态下跑" >&2; exit 2; }
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[!] 已有一个轮换在跑（lock $LOCK），本次退出" >&2
  exit 0
fi

BATCH="${QIMEI_BATCH:-$(date +%Y%m%dT%H%M%S)-$$}"
WORK=$(mktemp -d /tmp/qimei-rotate.XXXXXX) || exit 5
cleanup() {
  local bytes=0
  [ -d "$WORK" ] && bytes=$(du -sb "$WORK" 2>/dev/null | cut -f1)
  rm -rf "$WORK"
  echo "[i] 临时产物已清理: $WORK (${bytes:-0} B)" >&2
}
trap cleanup EXIT

log() { echo "$(date -Is) batch=$BATCH $*" | tee -a "$ROTLOG" >&2; }
say() { echo "$*"; }

# ---------- 生成器：优先 HTTP（纯 Python 服务），其次本机脚本 ----------
gen_one() {
  # gen_one <mac> <tag> <port> <octet>；成功时 stdout 只有 qimei36 一行
  local mac="$1" tag="$2" port="$3" oct="$4"
  if [ -n "$QIMEI_GEN_URL" ]; then
    curl -fsS --max-time "$QIMEI_GEN_TIMEOUT" \
      -H 'Content-Type: application/json' \
      -d "{\"mac\":\"$mac\"}" "$QIMEI_GEN_URL" |
      python3 -c 'import json,sys
d = json.load(sys.stdin)
v = d.get("qimei36") or (d.get("data") or {}).get("qimei36") or ""
print(str(v).strip())'
    return $?
  fi
  [ -x "$QIMEI_GEN_CMD" ] || { echo "生成脚本不可执行：$QIMEI_GEN_CMD" >&2; return 5; }
  if [ -n "$QIMEI_GEN_PREFIX" ]; then
    QIMEI_PREFIX="$QIMEI_GEN_PREFIX" timeout "$QIMEI_GEN_TIMEOUT" \
      "$QIMEI_GEN_CMD" "$mac" "$tag" "$port" "$oct"
  else
    timeout "$QIMEI_GEN_TIMEOUT" "$QIMEI_GEN_CMD" "$mac" "$tag" "$port" "$oct"
  fi
}

# ---------- 1) 账号清单 ----------
if [ "$MODE" = "all" ]; then
  ACC_LIST=$($SUDO docker exec "$CONTAINER" python3 -c "
import sqlite3
c=sqlite3.connect('file:/data/workbuddy.db?mode=ro',uri=True)
print(' '.join(str(r[0]) for r in c.execute(\"select id from accounts where platform='marvis' and status='active' order by id\")))
" 2>/dev/null | tr -d '\r')
else
  ACC_LIST=$(echo "$ACCS_ARG" | tr ',' ' ')
fi
ACC_LIST=$(echo "$ACC_LIST" | xargs)
[ -z "$ACC_LIST" ] && { echo "[x] 账号清单为空" >&2; exit 3; }
say "== 批量轮换 qimei =="
say "batch=$BATCH accounts=[$ACC_LIST] deploy=$DEPLOY dry-run=$DRYRUN gen=$([ -n "$QIMEI_GEN_URL" ] && echo http || echo cmd)"

uid_of() {
  # 输出必须与 qimei_assign.normalize_uid() 同形（strip + 大写）：
  # 表的主键就是这个规范形，大小写不一致时 upsert 不是「更新」而是「新增一条设备记录」。
  $SUDO docker exec "$CONTAINER" python3 -c "
import sqlite3,sys
c=sqlite3.connect('file:/data/workbuddy.db?mode=ro',uri=True)
r=c.execute(\"select uid from accounts where id=? and platform='marvis'\",(int(sys.argv[1]),)).fetchone()
print(r[0] if r else '')
" "$1" 2>/dev/null | tr -d '\r\n' | tr '[:lower:]' '[:upper:]'
}

# ---------- 2) 逐个生成（先攒） ----------
: >"$WORK/pairs.txt"
N=0
i=0
for ACC in $ACC_LIST; do
  i=$((i+1))
  N=$((N+1))
  UID_=$(uid_of "$ACC")
  [ -n "$UID_" ] || { echo "[x] acc=$ACC 不存在或取不到 uid，中止（未写表）" >&2; exit 3; }
  MAC=$(printf '02:%02x:%02x:%02x:%02x:%02x' $((RANDOM%256)) $((RANDOM%256)) $((RANDOM%256)) $((RANDOM%256)) $((RANDOM%256)))
  PORT=$((8800 + RANDOM % 190))
  OCT=$((RANDOM % 250))
  say "--- [$i] acc=$ACC uid=$UID_ mac=$MAC port=$PORT octet=$OCT"
  Q=$(gen_one "$MAC" "${TAG:-rot$(date +%H%M%S)}" "$PORT" "$OCT" 2>"$WORK/gen-$ACC.log" | tail -1 | tr -d '\r\n')
  if [ -z "$Q" ]; then
    echo "[x] acc=$ACC 生成失败（gen 日志尾部）:" >&2
    tail -6 "$WORK/gen-$ACC.log" >&2
    echo "[x] 中止：未写表、未部署" >&2
    exit 5
  fi
  if ! printf '%s' "$Q" | grep -qE "$SHAPE_RE"; then
    echo "[x] acc=$ACC qimei 形态不合法: $Q（闸门 $SHAPE_RE）" >&2
    echo "[x] 中止：未写表、未部署" >&2
    exit 4
  fi
  if grep -q "|$Q|" "$WORK/pairs.txt" 2>/dev/null; then
    echo "[x] acc=$ACC 生成的 qimei 与批内其它账号重复: $Q" >&2
    exit 4
  fi
  OLD=$(python3 -c "import io,json,sys;print(json.load(io.open(sys.argv[1],encoding='utf-8')).get(sys.argv[2],''))" "$TABLE" "$UID_")
  if [ "$OLD" = "$Q" ]; then
    echo "[x] acc=$ACC 新 qimei 与旧值相同（随机 MAC 撞车）: $Q" >&2
    exit 4
  fi
  printf '%s|%s|%s|%s|%s\n' "$ACC" "$UID_" "$MAC" "$Q" "$OLD" >>"$WORK/pairs.txt"
  say "    形态闸门 PASS  qimei=$Q  old=${OLD:-（表中无）}"
  sleep "$GAP"
done

# ---------- 3) 汇总 ----------
say ""
say "== 生成汇总（全部过闸门才继续）=="
printf '%-6s %-34s %-17s %-42s\n' ACC UID MAC QIMEI
while IFS='|' read -r a u m q o; do
  printf '%-6s %-34s %-17s %-42s\n' "$a" "$u" "$m" "$q"
done <"$WORK/pairs.txt"

if [ "$DRYRUN" = "1" ]; then
  say ""
  say "== DRY-RUN：未写表、未部署 =="
  say "batch=$BATCH 通过闸门 $N/$N，表 $TABLE 未改动"
  log "action=dry_run ok accounts=$N table_unchanged=1"
  exit 0
fi

# ---------- 4) 一次性 upsert（先备份，再就地写同一 inode） ----------
if [ ! -d "$BACKUP_DIR" ]; then mkdir -p "$BACKUP_DIR" || { echo "[x] 建不了备份目录 $BACKUP_DIR" >&2; exit 6; }; fi
cp -p "$TABLE" "$BACKUP_DIR/qimei_table.$BATCH.json" || { echo "[x] 备份失败" >&2; exit 6; }
python3 - "$TABLE" "$WORK/pairs.txt" "$QIMEI_WRITE_MODE" "$BACKUP_DIR/qimei_table.$BATCH.json" <<'PYEOF'
import io, json, os, sys
table_path, pairs_path, write_mode = sys.argv[1], sys.argv[2], sys.argv[3]
backup_path = sys.argv[4] if len(sys.argv) > 4 else ""

with io.open(table_path, encoding="utf-8") as fh:
    table = json.load(fh)
before = len(table)
changed = []
for line in io.open(pairs_path, encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    acc, uid, mac, q, old = line.split("|")
    table[uid] = q
    changed.append((acc, uid, old, q))

text = json.dumps(table, indent=2, sort_keys=True) + "\n"
if write_mode == "rename":
    # 兼容路径：.tmp + os.replace。容器侧的**单文件 bind mount 会继续指向旧 inode**，
    # 所以这条路径必须靠 restart 生效，不能只 reload。
    tmp = "%s.tmp.%d" % (table_path, os.getpid())
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, table_path)
else:
    # 默认路径：就地写同一个 inode（否则热重载看不到新表）。
    # 顺序是「先写正文，再截掉旧尾巴」，不是「先 truncate(0)」：
    # 后者被 kill -9 / OOM / TimeoutStopSec 卡在两步之间就是**0 字节表**，
    # 读端立刻 500；前者最坏是「新内容 + 旧尾巴」，至少还能从备份认出来。
    with io.open(table_path, "r+", encoding="utf-8", newline="\n") as fh:
        fh.seek(0)
        fh.write(text)
        fh.truncate()
        fh.flush()
        os.fsync(fh.fileno())
    # 写完立刻回读：解析不了或条目数不对，就用本次备份整份还原，别把坏表留给进程。
    try:
        with io.open(table_path, encoding="utf-8") as fh:
            check = json.load(fh)
        if len(check) != len(table):
            raise ValueError("entries %d != %d" % (len(check), len(table)))
    except Exception as exc:  # noqa: BLE001 - 任何异常都按「表坏了」处理
        if backup_path and os.path.isfile(backup_path):
            with io.open(backup_path, "rb") as src, io.open(table_path, "wb") as dst:
                dst.write(src.read())
                dst.flush()
                os.fsync(dst.fileno())
            print("restore=backup reason=%s" % str(exc)[:120], file=sys.stderr)
        raise SystemExit(1)

print("entries before=%d after=%d mode=%s" % (before, len(table), write_mode))
for acc, uid, old, q in changed:
    print("upsert acc=%s uid=%s old=%s new=%s" % (acc, uid, old or "-", q))
PYEOF
RC=$?
[ "$RC" -eq 0 ] || { echo "[x] 写表失败（已备份到 $BACKUP_DIR/qimei_table.$BATCH.json）" >&2; exit 6; }
while IFS='|' read -r a u m q o; do
  log "action=rotate acc=$a uid=$u mac=$m old=${o:-none} new=$q"
done <"$WORK/pairs.txt"

# ---------- 5) 部署（--deploy 才做） ----------
if [ "$DEPLOY" != "1" ]; then
  say ""
  say "== 未部署（加 --deploy 才 reload/restart）=="
  log "action=table_written deploy=0"
  exit 0
fi

reload_entry_probe() {
  # 只探测容器内代码有没有热重载入口，不跨进程调用它：
  # 另起一个 python 进程改自己的缓存对 worker 无效，真重载要走 HTTP/SIGHUP 钩子。
  $SUDO docker exec "$CONTAINER" python3 -c '
import sys
sys.path.insert(0, "/app")
try:
    from admin.platform.marvis.core import uskey
except Exception as exc:
    print("RELOAD_ENTRY=unknown err=%s" % str(exc)[:80])
    raise SystemExit(0)
names = [n for n in ("reload_qimei_table", "reload_qimei", "reload_table")
         if callable(getattr(uskey, n, None))]
print("RELOAD_ENTRY=%s names=%s" % (bool(names), ",".join(names) or "-"))
' 2>/dev/null | tr -d '\r'
}

verify_table() {
  local fail=0 a u m q o got
  say "== 校验容器内真值 =="
  while IFS='|' read -r a u m q o; do
    # uid 走 argv，不拼进源码：拼进去的话 uid 带引号就是一个容器内代码执行点。
    got=$($SUDO docker exec "$CONTAINER" python3 -c '
import sys
sys.path.insert(0,"/app")
from admin.platform.marvis.core import uskey
print(uskey.qimei36(sys.argv[1]))
' "$u" 2>/dev/null | tr -d '\r\n')
    if [ "$got" = "$q" ]; then
      say "  acc=$a OK  $got"
    else
      say "  acc=$a MISMATCH got=$got want=$q"
      fail=1
    fi
  done <"$WORK/pairs.txt"
  return "$fail"
}

deploy_once() {
  local rc st probe
  probe=$(reload_entry_probe)
  log "action=reload_probe $probe"

  if [ "$QIMEI_WRITE_MODE" != "inplace" ]; then
    log "warn=inode_swap write_mode=$QIMEI_WRITE_MODE reason=bind_mount_looks_at_old_inode"
    if [ "$QIMEI_DEPLOY_MODE" = "reload" ]; then
      echo "[x] write_mode=rename 只能靠 restart 生效（单文件 bind mount 仍指旧 inode）" >&2
      return 7
    fi
    QIMEI_DEPLOY_MODE=restart
    log "action=deploy_mode_forced mode=restart reason=write_mode_rename"
  fi

  if [ "$QIMEI_DEPLOY_MODE" != "restart" ] && [ -n "$QIMEI_RELOAD_CMD" ]; then
    say "== 部署：热重载（QIMEI_RELOAD_CMD）=="
    if bash -c "$QIMEI_RELOAD_CMD" >>"$ROTLOG" 2>&1; then
      log "action=reload mode=cmd ok"
      return 0
    fi
    log "warn=reload_failed mode=cmd"
    if [ "$QIMEI_DEPLOY_MODE" = "reload" ]; then
      echo "[x] 热重载失败，且 QIMEI_DEPLOY_MODE=reload（不回落重启）" >&2
      return 7
    fi
  elif [ "$QIMEI_DEPLOY_MODE" = "reload" ]; then
    echo "[x] QIMEI_DEPLOY_MODE=reload 但 QIMEI_RELOAD_CMD 为空" >&2
    return 7
  fi

  log "action=restart reason=reload_not_configured"
  say "== 部署：build admin（服务不断）=="
  ( cd "$MIRAIHUB_DIR" && $SUDO docker compose build admin ) 2>&1 | tail -5 | sed 's/^/    /' | tee -a "$ROTLOG"
  rc=${PIPESTATUS[0]}
  [ "$rc" -eq 0 ] || { echo "[x] build 失败（表已写，旧容器仍在跑）" >&2; return 7; }

  say "== 部署：up -d --no-build admin（秒级切换）=="
  ( cd "$MIRAIHUB_DIR" && $SUDO docker compose up -d --no-build admin ) 2>&1 | tail -5 | sed 's/^/    /' | tee -a "$ROTLOG"
  rc=${PIPESTATUS[0]}
  # 不查这条退出码就等于「up 失败也报 deploy ok」：表已经写了，容器还跑着旧进程。
  [ "$rc" -eq 0 ] || { echo "[x] up -d 失败（表已写，旧容器仍在跑）" >&2; return 7; }

  say "== 等健康 =="
  st=unknown
  local i
  for i in $(seq 1 60); do
    st=$($SUDO docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo unknown)
    [ "$st" = "healthy" ] && break
    sleep 2
  done
  say "health=$st"
  log "action=restart done health=$st"
  # healthy 才算成功；unhealthy 明确失败。unknown 只告警：容器可能没配 HEALTHCHECK，
  # 这不是部署失败，硬判会让「没有健康检查」的部署永远退出 7。
  if [ "$st" = "unhealthy" ]; then
    echo "[x] 容器已启动但健康检查失败（health=unhealthy）：$SUDO docker logs --tail 50 $CONTAINER" >&2
    return 7
  fi
  [ "$st" = "healthy" ] || log "warn=health_not_healthy value=$st reason=no_healthcheck_or_slow_start"
  return 0
}

deploy_once || { log "action=deploy_failed"; exit 7; }
verify_table || { echo "[x] 部署后校验不一致" >&2; log "action=verify_failed"; exit 7; }
log "action=deploy ok accounts=$N"
say ""
say "== 完成：$N 个账号，一次部署 =="
exit 0
