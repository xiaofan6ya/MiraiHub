#!/usr/bin/env bash
# auto_rotate_on_banned.sh — 只有「全部活跃账号同时被设备级封禁」才自动轮换一次
#
# 判据（严格按 docs/marvis-qimei-rotation.md）:
#   1) 统计近 N 分钟（默认 10）内 marvis 平台 4100404 的**不同账号数**；
#   2) 全部活跃账号都命中 4100404，且每个号至少连续 MIN_HITS 次 → 疑似设备/IP 级封禁；
#   3) 先排除配额/凭证：调 /v3/marvis_get_wallet（业务签名，用现有可用账号凭证）；
#      返回可解析余额 = 凭证活着 → 是设备维度；
#      打不通/无 data = 配额或凭证问题 → **不轮换，只告警**；
#   4) 满足 → rotate_all_qimei.sh --all --deploy（一次性写表 + 一次部署）；
#   5) 轮换后再观察 QIMEI_POST_WATCH_SEC 秒；仍有 4100404 → 告警「轮换无效，疑似 IP
#      维度」，**不重试**；
#   6) 防抖：flock 单实例 + 冷却窗口（默认 2 小时）内不再触发。
#
# 数据来源（实测）:
#   · 4100404 的业务码**不落库**：request_audits 只记 HTTP status（403）与 outcome，
#     没有 reason/业务码；sentinel 明细走 Redis，ADMIN_REDIS_URL="" 时降级到进程内存。
#     所以「按业务码统计」的权威来源是容器日志（gateway 每条失败都打
#     "code=4100404 acc=<id>" 或 "4100404（account=<id>）"）。
#   · DB 仍参与判定：accounts.last_upstream_error 里若含 4100404 且 last_err_at 在窗口内，
#     记为该号 1 次命中（与日志取 max，不重复计数）。
#
# 环境变量（括号内为默认值）:
#   MIRAIHUB_DIR(/home/admin/MiraiHub) CONTAINER(miraihub-admin-1)
#   QIMEI_LOG_DIR(/var/log/qimei-rotate)
#   QIMEI_ROTATE_CMD($MIRAIHUB_DIR/deploy/qimei-rotate/rotate_all_qimei.sh)
#   QIMEI_WINDOW_MIN(10) QIMEI_MIN_HITS(2) QIMEI_COOLDOWN_SEC(7200)
#   QIMEI_POST_WATCH_SEC(300) QIMEI_WALLET_TRIES(3) QIMEI_AUTOROTATE_DRY_RUN(0)
#
# 退出码: 0 无需/已处理（含防抖与 dry-run），2 参数/环境问题，8 轮换失败，9 轮换后仍被封
set -uo pipefail

MIRAIHUB_DIR=${MIRAIHUB_DIR:-/home/admin/MiraiHub}
CONTAINER=${CONTAINER:-miraihub-admin-1}
QIMEI_LOG_DIR=${QIMEI_LOG_DIR:-/var/log/qimei-rotate}
QIMEI_STATE_DIR=${QIMEI_STATE_DIR:-$MIRAIHUB_DIR/admin/platform/marvis/core}
ROT=${QIMEI_ROTATE_CMD:-$MIRAIHUB_DIR/deploy/qimei-rotate/rotate_all_qimei.sh}

WINDOW_MIN=${QIMEI_WINDOW_MIN:-10}
MIN_HITS=${QIMEI_MIN_HITS:-2}
COOLDOWN=${QIMEI_COOLDOWN_SEC:-7200}
POST_WATCH=${QIMEI_POST_WATCH_SEC:-300}
WALLET_TRIES=${QIMEI_WALLET_TRIES:-3}
DRYRUN=${QIMEI_AUTOROTATE_DRY_RUN:-0}

LOG=$QIMEI_LOG_DIR/qimei-autorotate.log
ALERTLOG=$QIMEI_LOG_DIR/qimei-alerts.txt
STATE=$QIMEI_LOG_DIR/qimei-autorotate.state
LOCK=$QIMEI_LOG_DIR/.qimei-autorotate.lock

SUDO=""
if [ "$(id -u)" != "0" ] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

[ -f "$ROT" ] || { echo "[x] 轮换脚本不存在：$ROT" >&2; exit 2; }
[ -x "$ROT" ] || echo "[!] 轮换脚本没有 exec 位（$ROT），按 bash \"\$ROT\" 调用，继续" >&2

# 数值参数会被拼进容器内 python（timedelta(minutes=$WINDOW_MIN) / cut -f1-$WALLET_TRIES），
# 非数字不是「跑不对」而是语法错；在这里一次性拦掉。
for _num in "$WINDOW_MIN" "$MIN_HITS" "$COOLDOWN" "$POST_WATCH" "$WALLET_TRIES"; do
  case "$_num" in
    ''|*[!0-9]*) echo "[x] 数值参数含非数字：'$_num'（检查 QIMEI_WINDOW_MIN / QIMEI_MIN_HITS / QIMEI_COOLDOWN_SEC / QIMEI_POST_WATCH_SEC / QIMEI_WALLET_TRIES）" >&2
                 exit 2 ;;
  esac
done
mkdir -p "$QIMEI_LOG_DIR" 2>/dev/null || true
[ -w "$QIMEI_LOG_DIR" ] || { echo "[x] 日志目录不可写：$QIMEI_LOG_DIR" >&2; exit 2; }

# 防抖第一层是单实例锁；没有 flock 就明确失败，别把「无锁」当成「已有人跑」。
command -v flock >/dev/null 2>&1 || { echo "[x] 缺 flock（util-linux），拒绝在无锁状态下跑" >&2; exit 2; }
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "$(date -Is) skip=locked" >>"$LOG"
  exit 0
fi

log() { echo "$(date -Is) $*" | tee -a "$LOG"; }
alert() {
  local msg="$1"
  echo "$(date -Is) ALERT $msg" | tee -a "$ALERTLOG" >>"$LOG"
  logger -t marvis-qimei-alert "$msg" 2>/dev/null || true
}
set_state() {
  # set_state <result> <detail> [fired_epoch] [signature]
  python3 - "$STATE" "$1" "$2" "${3:-0}" "${4:-}" "$WINDOW_MIN" <<'PY'
import io, json, os, sys, time
path, result, detail, fired, sig, window = sys.argv[1:7]
data = {}
try:
    data = json.load(io.open(path, encoding="utf-8"))
except Exception:
    data = {}
data.update({
    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "result": result,
    "detail": detail,
    "window_min": int(window),
})
if float(fired or 0) > 0:
    data["last_fire_epoch"] = float(fired)
    data["last_fire_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(float(fired)))
    data["signature"] = sig
tmp = path + ".tmp"
io.open(tmp, "w", encoding="utf-8", newline="\n").write(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
os.replace(tmp, path)
PY
}

NOW=$(date +%s)
LAST_FIRE=$(python3 -c "
import io,json,sys
try:
    d=json.load(io.open(sys.argv[1],encoding='utf-8'))
    print(int(float(d.get('last_fire_epoch') or 0)))
except Exception:
    print(0)
" "$STATE")
AGE=$((NOW - LAST_FIRE))
log "start window=${WINDOW_MIN}m min_hits=$MIN_HITS cooldown=${COOLDOWN}s post_watch=${POST_WATCH}s dry_run=$DRYRUN last_fire=${LAST_FIRE:-0} age=${AGE}s"

# ---------- 1) 活跃账号 ----------
ACTIVE=$($SUDO docker exec "$CONTAINER" python3 -c "
import sqlite3
c=sqlite3.connect('file:/data/workbuddy.db?mode=ro',uri=True)
print(' '.join(str(r[0]) for r in c.execute(\"select id from accounts where platform='marvis' and status='active' order by id\")))
" 2>/dev/null | tr -d '\r')
ACTIVE=$(echo "$ACTIVE" | xargs)
N_ACTIVE=$(echo "$ACTIVE" | wc -w | tr -d ' ')
if [ "$N_ACTIVE" -eq 0 ]; then
  log "result=no_rotation reason=no_active_marvis_accounts"
  set_state no_rotation "活跃 marvis 账号为 0"
  exit 0
fi

# ---------- 2) 4100404 命中统计 ----------
# 2a) 容器日志（权威：带业务码）
LOG_RAW=$($SUDO docker logs --since "${WINDOW_MIN}m" -t "$CONTAINER" 2>&1 | grep -F '4100404' || true)
LOG_HITS=$(printf '%s\n' "$LOG_RAW" | sed -nE 's/.*(acc|account)=([0-9]+).*/\2/p' | grep -E '^[0-9]+$' | sort | uniq -c || true)
# 2b) DB：accounts.last_upstream_error 里含 4100404 且 last_err_at 在窗口内（每个号记 1 次）
DB_CODE=$($SUDO docker exec "$CONTAINER" python3 -c "
import sqlite3, datetime
c=sqlite3.connect('file:/data/workbuddy.db?mode=ro',uri=True)
cut=(datetime.datetime.now()-datetime.timedelta(minutes=$WINDOW_MIN)).strftime('%Y-%m-%d %H:%M:%S')
for aid,raw,at in c.execute(\"select id,last_upstream_error,last_err_at from accounts where platform='marvis'\"):
    if raw and '4100404' in str(raw) and at and str(at)[:19] >= cut:
        print(aid)
" 2>/dev/null | tr -d '\r' | xargs)

HITS_FILE=$(mktemp /tmp/qimei-hits.XXXXXX)
for a in $ACTIVE; do
  n=$(printf '%s\n' "$LOG_HITS" | awk -v a="$a" '$2==a{print $1}' | head -1)
  n=${n:-0}
  if echo " $DB_CODE " | grep -q " $a "; then
    [ "$n" -lt 1 ] && n=1
  fi
  printf '%s %s\n' "$a" "$n" >>"$HITS_FILE"
done
HIT_ACCOUNTS=$(awk '$2>0{c++} END{print c+0}' "$HITS_FILE")
FULL_ACCOUNTS=$(awk -v m="$MIN_HITS" '$2>=m{c++} END{print c+0}' "$HITS_FILE")
SUMMARY=$(awk '{printf "%s:%s ", $1, $2}' "$HITS_FILE")
SIG=$(awk '$2>0{printf "%s,", $1}' "$HITS_FILE")
log "hits window=${WINDOW_MIN}m active=$N_ACTIVE hit_accounts=$HIT_ACCOUNTS full(>=${MIN_HITS}) inspect=[$SUMMARY]"
rm -f "$HITS_FILE"

if [ "$FULL_ACCOUNTS" -lt "$N_ACTIVE" ]; then
  log "result=no_rotation reason=not_all_accounts_banned ($FULL_ACCOUNTS/$N_ACTIVE 达到 ${MIN_HITS} 次)"
  set_state no_rotation "仅 $HIT_ACCOUNTS/$N_ACTIVE 个账号命中 4100404，$FULL_ACCOUNTS 个达到 ${MIN_HITS} 次"
  exit 0
fi
log "judge=device_level_suspected all $N_ACTIVE accounts hit 4100404 >= ${MIN_HITS} times in ${WINDOW_MIN}m"

# ---------- 3) 防抖（flock 已在上面；这里是冷却窗口） ----------
if [ "$DRYRUN" != "1" ] && [ "$LAST_FIRE" -gt 0 ] && [ "$AGE" -lt "$COOLDOWN" ]; then
  log "result=debounced last_fire=${AGE}s ago < cooldown=${COOLDOWN}s signature=[$SIG]"
  set_state debounced "同一轮封禁已处理过（${AGE}s 前），冷却中"
  exit 0
fi

# ---------- 4) 先排除配额/凭证 ----------
WALLET_IDS=$(echo "$ACTIVE" | cut -d' ' -f1-"$WALLET_TRIES")
log "wallet_probe accounts=[$WALLET_IDS]"
WALLET=$(timeout 60 $SUDO docker exec -i "$CONTAINER" python3 - "$WALLET_IDS" <<'PY'
import json, sqlite3, sys
sys.path.insert(0, '/app')
from admin.platform.marvis.core import client
ids = [int(x) for x in sys.argv[1:] if str(x).strip().isdigit()]
con = sqlite3.connect('file:/data/workbuddy.db?mode=ro', uri=True)
last = ''
for aid in ids:
    row = con.execute('select auth_json from accounts where id=?', (aid,)).fetchone()
    if not row or not row[0]:
        last = 'acc %s 无凭证' % aid
        continue
    try:
        cred = json.loads(row[0])
        w = client.fetch_wallet(cred, 'cn', account_id=aid)
        if isinstance(w, dict) and ('avail_tokens' in w or 'total_tokens' in w):
            print(json.dumps({'ok': True, 'acc': aid,
                              'avail_tokens': str(w.get('avail_tokens')),
                              'total_tokens': str(w.get('total_tokens'))}, ensure_ascii=False))
            sys.exit(0)
        last = 'acc %s 返回无 data（登录态失效）' % aid
    except Exception as exc:
        last = 'acc %s %s' % (aid, str(exc)[:140])
print(json.dumps({'ok': False, 'err': last or 'no credential'}, ensure_ascii=False))
PY
)
log "wallet=$WALLET"

if ! printf '%s' "$WALLET" | grep -q '"ok": true'; then
  alert "全部账号 4100404，但钱包接口不可解析（$WALLET）→ 判为配额/凭证问题，不轮换"
  set_state quota_or_credential "wallet 不可解析：$WALLET"
  exit 0
fi
log "judge=credential_alive -> device_dimension_confirmed"

# ---------- 5) 轮换一次（失败不重试） ----------
if [ "$DRYRUN" = "1" ]; then
  ROT_ARGS=(--all --dry-run)
  log "action=rotate_dry_run start（只生成+校验，不写表、不部署）"
else
  ROT_ARGS=(--all --deploy)
  log "action=rotate_all start"
fi
ROT_OUT=$($SUDO bash "$ROT" "${ROT_ARGS[@]}" 2>&1)
ROT_RC=$?
printf '%s\n' "$ROT_OUT" | sed 's/^/    /' >>"$LOG"
if [ "$ROT_RC" -ne 0 ]; then
  alert "轮换失败（rotate_all_qimei.sh rc=$ROT_RC），未做重试"
  set_state rotate_failed "rc=$ROT_RC 未做重试"
  exit 8
fi

if [ "$DRYRUN" = "1" ]; then
  set_state dry_run "dry-run 通过（未写表、未部署）"
  log "result=dry_run_ok"
  exit 0
fi

set_state rotated "已轮换 $N_ACTIVE 个账号" "$NOW" "$SIG"
log "action=rotate_all done rc=0"

# ---------- 6) 轮换后再观察一个窗口 ----------
if [ "$POST_WATCH" -gt 0 ]; then
  log "post_watch sleep=${POST_WATCH}s"
  sleep "$POST_WATCH"
  AFTER_HITS=$($SUDO docker logs --since "${POST_WATCH}s" -t "$CONTAINER" 2>&1 | grep -F '4100404' | sed -nE 's/.*(acc|account)=([0-9]+).*/\2/p' | sort -u | tr '\n' ',' || true)
  if [ -n "$AFTER_HITS" ]; then
    alert "轮换后 ${POST_WATCH}s 内仍有 4100404（acc=$AFTER_HITS）→ 轮换无效，疑似 IP 维度；不再重试"
    set_state ineffective_after_rotate "轮换后仍有 4100404: $AFTER_HITS" "$NOW" "$SIG"
    exit 9
  fi
  log "post_watch clean：轮换后 ${POST_WATCH}s 内无 4100404"
fi

set_state rotated_ok "轮换完成且观察期无 4100404" "$NOW" "$SIG"
log "result=rotated_ok"
exit 0
