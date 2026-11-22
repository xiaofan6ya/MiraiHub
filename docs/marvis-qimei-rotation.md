# qimei 定期轮换：可部署资产与运维口径

> 配套资产：[../deploy/qimei-rotate/](../deploy/qimei-rotate/) ｜ 判据来源：[marvis-device-id-governance.md](marvis-device-id-governance.md) ｜
> 事故复盘：[marvis-4100404-postmortem.md](marvis-4100404-postmortem.md)
>
> 本文把「定期轮换」从手工 SOP 变成仓库内资产：脚本 + systemd oneshot + timer + 自检。
> 手工人肉流程仍然是最终兜底（§10），但日常不再靠人盯日志。

§0 一句话：**timer 每 6 小时判一次；只有「全部活跃账号同时命中 4100404 且凭证仍然活着」才
轮换一次；换表靠就地写同一 inode，配好重载钩子就不用重启容器；失败与「轮换后仍被封」都
只告警、不重试。**

---

## 0. 演练纪律（真实数据不可碰）

`qimei_table.json` / `qimei_pool.json` 是**线上数据**，HANDOFF 硬约束禁止改内容；
`rotate_all_qimei.sh` 的默认 `QIMEI_STATE_DIR` 就是仓库 core 目录（生产工具的正常行为），
所以「演练」必须显式换目录：

```bash
QIMEI_STATE_DIR=tmp/qimei-state QIMEI_LOG_DIR=tmp/qimei-log \
  sudo deploy/qimei-rotate/rotate_all_qimei.sh --all --dry-run
python3 -m admin.platform.marvis.core.qimei_assign --state-dir tmp/qimei-state --reconcile --dry-run
```

- 漏 `--state-dir` / `QIMEI_STATE_DIR` 的 `assign_ex` / `reconcile` 试验会直接写线上台账；
  已发生过的痕迹：pool 里出现 `owners=["A1"]` 这类测试值、目录残留 `qimei_state.lock`。
- 恢复手段只有整份还原：`git checkout -- admin/platform/marvis/core/qimei_table.json
  admin/platform/marvis/core/qimei_pool.json`，并删掉 `qimei_state.lock`。
- 跑 `tests/test_qimei_rotation.py` 前确认 shell 无 `QIMEI_STATE_DIR` 指向 core 目录；
  该测试**只读文件**（不写盘、不设 env、不碰 `qimei_assign`）。

---

## 1. 资产清单

| 路径 | 是什么 |
| --- | --- |
| `deploy/qimei-rotate/auto_rotate_on_banned.sh` | 判定 + 触发（timer 的 ExecStart）。只判「要不要动」，不动手写表 |
| `deploy/qimei-rotate/rotate_all_qimei.sh` | 执行。先攒后写：全部生成并过闸门后，一次性写表、一次性部署 |
| `deploy/qimei-rotate/qimei-rotate.service` | `Type=oneshot`，跑一次判定 |
| `deploy/qimei-rotate/qimei-rotate.timer` | 周期：`OnCalendar=*-*-* 00/6:17:00` + `OnBootSec=5min` + `Persistent=true` |
| `deploy/qimei-rotate/README.md` | 安装 / 参数 / 退出码（快查） |
| `tests/test_qimei_rotation.py` | 纯 Python 静态自检（unit、timer、脚本语义） |

参数全走环境变量，默认值按生产机（`MIRAIHUB_DIR=/home/admin/MiraiHub`、
`CONTAINER=miraihub-admin-1`、`QIMEI_LOG_DIR=/var/log/qimei-rotate`）。仓库里**不钉死**
生成器路径：要么 `QIMEI_GEN_URL` 指向纯 Python 生成服务，要么 `QIMEI_GEN_CMD` 指向部署机上
的采值脚本。两样都没配 → 脚本以退出码 5 拒绝干活，不会偷偷走派生值。

---

## 2. 触发判据

先分维度，再决定动作（与 governance §3.1/§3.3 同一张表，这里给的是**可执行判据**）。

```text
每 6 小时（或开机 5 分钟后）跑一次 auto_rotate_on_banned.sh
  │
  ├─ 活跃 marvis 账号 = 0                      → no_rotation（退出 0）
  ├─ 窗口内 4100404 命中账号 < 全部活跃账号     → no_rotation（退出 0）：账号级抖动不动设备
  ├─ 全部命中但每号 < QIMEI_MIN_HITS 次         → no_rotation（退出 0）
  ├─ 冷却窗口内已经动过                         → debounced（退出 0）
  ├─ 钱包预检不可解析（配额/凭证问题）           → 只 ALERT，不动（退出 0）
  └─ 全部命中 + 每号 ≥2 次 + 凭证活着           → 判「设备维度」，轮换一次（退出 0 / 8 / 9）
```

判据的三条硬口径：

1. **不同账号数**，不是命中次数。同一账号刷 20 次仍算 1 个号。
2. **全部活跃账号都命中**才可能是设备维度（一台设备串着 2 个号，设备坏了两个号一起坏）。
   只有一个号坏 → 账号级，冷却/停用，不动设备。
3. **先排除配额与凭证**：调 `/v3/marvis_get_wallet`（用现有账号凭证做业务签名）。
   能解析出余额 = 凭证活着 = 问题在设备维度；打不通 / 无 data = 配额或凭证问题 →
   **只告警不轮换**。这一步是「别把没额度当成被封」的唯一硬证据。

数据来源：4100404 的业务码**不落库**（`request_audits` 只记 HTTP status 403 与 outcome），
所以权威来源是容器日志（`code=4100404 acc=<id>`）；DB 侧 `accounts.last_upstream_error`
含 4100404 且 `last_err_at` 在窗口内时记该号 1 次命中，与日志取 max，不重复计数。

---

## 3. 周期

```ini
# deploy/qimei-rotate/qimei-rotate.timer
[Timer]
OnCalendar=*-*-* 00/6:17:00   # 00:17 / 06:17 / 12:17 / 18:17
OnBootSec=5min                # 开机 5 分钟后补一次判定
RandomizedDelaySec=180        # 多机部署时不要同一秒一起 docker logs / build
AccuracySec=1min
Persistent=true               # 关机期间错过的触发点，起来后补跑一次
Unit=qimei-rotate.service
```

- **为什么是 6 小时**：设备级封禁是小时级现象。判太勤只是把「账号抖动」放大成无意义的
  docker logs 扫描；判太疏会让一批号白烧半天。6 小时 + 冷却 2 小时 = 最坏情况一天最多
  触发 4 次，实际远少于这个（必须「全部活跃账号同时命中」才会触发）。
- **为什么错开整点（:17）**：整点是备份/证书/日志轮转的扎堆时段；`docker compose build`
  挤在那时候会互相拖慢。
- **为什么还要 OnBootSec**：`Persistent=true` 只在「OnCalendar 的下一个到点」补跑；
  刚重启的机器最需要先判一次（重启常伴随封禁后的处置）。
- **TimeoutStartSec=1800**：单号采值 60~120 秒、批量 3 个号 + 观察期 300 秒，给 30 分钟，
  避免 systemd 在轮换中途把进程 kill 掉（那样会留下「表已写、未部署」的中间态）。

```bash
systemctl list-timers qimei-rotate.timer --no-pager   # 看下次触发
systemctl status qimei-rotate.timer --no-pager        # 看上次结果
```

---

## 4. dry-run

三个层次，从便宜到贵：

```bash
# ① 只判「要不要动」，不生成、不写表、不部署（含钱包预检与命中统计）
sudo QIMEI_AUTOROTATE_DRY_RUN=1 systemctl start qimei-rotate.service
sudo journalctl -u qimei-rotate.service -n 80 --no-pager

# ② 真的生成 + 过形态闸门，但不写表、不部署（能看到新 qimei 和 old 值）
sudo deploy/qimei-rotate/rotate_all_qimei.sh --all --dry-run

# ③ 只对一两个号演练，避免全量
sudo deploy/qimei-rotate/rotate_all_qimei.sh --accounts 189 --dry-run
```

- `QIMEI_AUTOROTATE_DRY_RUN=1` 时判定脚本**不写冷却状态、不进入观察期**，可以反复跑。
- `--dry-run` 与 `--deploy` 互斥（同给会以退出码 2 拒绝），避免「以为在演练，实际已上线」。
- dry-run 也会调真实生成器（花时间也花上游额度）；只想看「会不会触发」用 ①。

---

## 5. 落盘

顺序固定：**生成 → 闸门 → 备份 → 写表 →（可选）部署**。任何一步失败都停在原地。

1. **先攒**：逐个生成新 qimei，结果只落临时目录（`/tmp/qimei-rotate.XXXXXX`，
   退出时 trap 清理），**不碰线上表**。
2. **形态闸门**：`^[0-9a-f]{24}3000[0-9a-f]{8}$`（24 hex 摘要 + `3000` 格式标记 + 8 hex 尾段）。
   另外两条闸门同样致命：批内不重复、不与表里旧值相同（撞车就当失败，别上线一台「同一台设备」）。
3. **备份**：`cp -p` 到 `$QIMEI_LOG_DIR/backup/qimei_table.<batch>.json`（batch 形如
   `20261122T161500-12345`）。
4. **写表**：默认 `QIMEI_WRITE_MODE=inplace` —— `truncate(0)` + 写回**同一 inode**。
   原因见 §6：表在容器里是单文件 bind mount，换 inode 等于容器永远读旧值。
5. **部署**：只有 `--deploy` 才做（§6）。
6. **流水**：每行一条 `action=rotate acc=.. uid=.. mac=.. old=.. new=..` 落到
   `$QIMEI_LOG_DIR/qimei-rotations.txt`，带 `batch=` 便于一次操作回溯。

容量台账（`qimei_pool.json`）：脚本**不直接改 pool**。`table` 是读路径权威，加载时会把
table 合并回 pool（名额只会更保守）；需要主动归一化时跑
`python3 -m admin.platform.marvis.core.qimei_assign --reconcile`。手工改表里的一行是本仓
明确的反模式（governance §3.4）。

---

## 6. reload（换表后怎么让它生效）

三种模式，由 `QIMEI_DEPLOY_MODE` 选：

| 模式 | 行为 | 何时用 |
| --- | --- | --- |
| `auto`（默认） | 配了 `QIMEI_RELOAD_CMD` 就先热重载；失败/没配 → 回落一次 `compose build + up` | 日常 |
| `reload` | 只热重载；没配钩子或重载失败 → 退出码 7，**不重启** | 已经确认重载可用、且要求零重启 |
| `restart` | 跳过重载，直接 build + up | 重载出了怀疑、或改了表以外的代码 |

**c2（热重载）落地后，定期轮换不再需要重启 admin**：填上重载钩子即可，例如

```ini
# /etc/miraihub/qimei-rotate.env
QIMEI_RELOAD_CMD=curl -fsS -X POST http://127.0.0.1:8790/<重载入口>
```

（`<重载入口>` 以 c2 交付的接口为准；带鉴权的入口把 token 放同一个 env 文件，别写进 unit。）

两个实现细节决定了「重载到底有没有用」，值得记在这里：

1. **inode**：`qimei_table.json` 是单文件 bind mount。宿主机 `os.replace` 换文件 =
   换 inode，容器里的挂载点仍指旧 inode → 进程重载了也读不到新值。所以默认就地写。
   选了 `QIMEI_WRITE_MODE=rename` 脚本会**自动强制 restart**（日志里
   `action=deploy_mode_forced mode=restart reason=write_mode_rename`）。
2. **跨进程改缓存不算重载**：`docker exec ... python3 -c 'uskey._QIMEI_TABLE_CACHE=None'`
   只影响那个一次性进程，worker 自己的缓存没变。脚本因此**只探测入口是否存在**
   （日志 `action=reload_probe RELOAD_ENTRY=true/false`），真重载只走
   `QIMEI_RELOAD_CMD`（HTTP / SIGHUP 之类能让 worker 自己重读的钩子）。

部署后一律做一次容器内真值校验：`docker exec ... uskey.qimei36(uid)` 逐号比对，
不一致就是退出码 7（表写了、部署了，但线上跑的是旧值 —— 这种必须立刻看到）。

---

## 7. 回滚

回滚是**整份换回**，不是改一行。

```bash
LOG_DIR=/var/log/qimei-rotate
# 1) 找上一份备份（文件名里带 batch 时间戳）
ls -1t $LOG_DIR/backup/qimei_table.*.json | head -5

# 2) 整份换回（就地写：cp 到同一个文件，保持 inode）
sudo cp -p $LOG_DIR/backup/qimei_table.20261122T161500-12345.json \
           /home/admin/MiraiHub/admin/platform/marvis/core/qimei_table.json

# 3) 让它生效：配了重载钩子就直接调，没有就 restart 一次
sudo bash -c "$QIMEI_RELOAD_CMD"        # 或
cd /home/admin/MiraiHub && sudo docker compose up -d --no-build admin

# 4) 验证单个账号（不要全量放量验证）
sudo docker exec miraihub-admin-1 python3 -c "
import sys; sys.path.insert(0,'/app')
from admin.platform.marvis.core import uskey
print(uskey.qimei36('<uid>'))"
```

回滚判据：轮换后观察期内仍有 4100404（判定「轮换无效，疑似 IP 维度」）、或线上校验不一致。
回滚完把 `$QIMEI_LOG_DIR/qimei-autorotate.state` 里的 `last_fire_epoch` 保留 ——
冷却窗口能防止「回滚后立刻又被自动换一遍」。

---

## 8. 防抖

四层，缺一层就会把抖动放大成事故：

1. `flock -n`：`.qimei-autorotate.lock`（判重）与 `.qimei-rotate.lock`（写表）两级互斥，
   timer 与手工操作撞车时后者直接退出 0，不排队。
2. **冷却窗口** `QIMEI_COOLDOWN_SEC=7200`：同一轮封禁 2 小时内不再触发。
   状态写在 `qimei-autorotate.state`（`last_fire_epoch` / `last_fire_at` / `signature` /
   `result`），掉电不丢。
3. **观察期** `QIMEI_POST_WATCH_SEC=300`：轮换后 5 分钟还看到 4100404 → 退出码 9 +
   告警「轮换无效，疑似 IP 维度」，**不再重试**。重试只会把新设备一起烧掉。
4. **失败不重试**：轮换脚本非 0 → 退出码 8 + 告警，等下一次 timer（或人工）。批量轮换本身
   是「状态迁移」，不是 retry（governance §3.4）。

---

## 9. 告警

告警落三个地方，够用且不引入新组件：

| 去处 | 内容 |
| --- | --- |
| `$QIMEI_LOG_DIR/qimei-alerts.txt` | 每条 `ALERT` 一行，长期留存 |
| `$QIMEI_LOG_DIR/qimei-autorotate.log` | 全量流水（`start` / `hits` / `judge` / `wallet` / `action` / `result`） |
| systemd journal（`logger -t marvis-qimei-alert`） | 接 rsyslog 时直接进集中告警 |

需要人工介入的四种：

- `wallet 不可解析` → 配额/凭证问题，**不是设备问题**，去查额度或登录态。
- `轮换失败（rc≠0）` → 看 `qimei-rotations.txt` 同一 batch 的上一行，**不重试**。
- `轮换后仍 4100404` → 设备维度判断错了（多半是出口 IP 维度），换出口再说。
- `qimei 形态不合法 / 批内重复` → 生成器坏或随机撞车；表未改，可安全重跑。

```bash
tail -n 50 /var/log/qimei-rotate/qimei-autorotate.log
cat /var/log/qimei-rotate/qimei-autorotate.state      # 上一轮判定结论
journalctl -t marvis-qimei-alert --since -7d --no-pager
```

---

## 10. 部署与验收

```bash
# 安装单位
sudo install -m 0644 deploy/qimei-rotate/qimei-rotate.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo install -d -m 0750 /var/log/qimei-rotate /etc/miraihub
sudoedit /etc/miraihub/qimei-rotate.env     # 至少配 QIMEI_GEN_URL 或 QIMEI_GEN_CMD

# 先演练，再启用
sudo QIMEI_AUTOROTATE_DRY_RUN=1 systemctl start qimei-rotate.service
sudo journalctl -u qimei-rotate.service -n 80 --no-pager
sudo systemctl enable --now qimei-rotate.timer

# 验收
systemctl list-timers qimei-rotate.timer --no-pager     # NEXT 应在 6 小时内
python -m unittest tests.test_qimei_rotation -v          # 静态自检全绿
```

升级顺序：先改脚本（仓库）→ 再 `systemctl restart` 不需要（oneshot 每次重新读文件）→
只改 unit/timer 才 `daemon-reload`。停用：`systemctl disable --now qimei-rotate.timer`。

---

## 11. 排障

| 现象 | 看什么 |
| --- | --- |
| timer 没触发 | `systemctl list-timers`、`systemctl status qimei-rotate.timer`；`Persistent=true` 的补跑只在到点后 |
| 一直 `no_rotation` | 日志 `hit_accounts` / `full(>=2)`：不是「全部账号都命中」就不会动 |
| 一直被 `debounced` | `qimei-autorotate.state` 的 `last_fire_at`；急着重跑就删掉 `last_fire_epoch` 再 `systemctl start` |
| `skip=locked` | 另一个实例还在跑（`ps -ef \| grep qimei`），等它结束 |
| 退出码 5 | 生成器没配/生成失败：`QIMEI_GEN_URL` 是否可达、`QIMEI_GEN_CMD` 是否可执行 |
| 退出码 7 | 部署或校验失败：容器 `health`、compose build 日志、`uskey.qimei36(uid)` 是否等于表值 |
| 退出码 8 / 9 | 分别对应「轮换失败」与「轮换后仍被封」，都**不重试**，按 §9 处置 |

---

## 附：本文的边界

- 只覆盖「设备身份轮换」这一条链路；出口 IP 轮换（`core/egress.py`）是另一条链路。
- 判据依赖容器日志格式（`acc=<id>` / `account=<id>`）；网关改日志格式时先改
  `auto_rotate_on_banned.sh` 的 `sed` 表达式，否则会静默统计成 0 命中（表现是一直
  `no_rotation`）。
- 本文不承诺「轮换一定解封」：全号同时被封也可能是出口 IP 维度，那种情况轮换设备无效，
  脚本会以退出码 9 明确告诉你别白烧设备。
