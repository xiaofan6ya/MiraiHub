# deploy/qimei-rotate — 定期轮换 qimei（可被 timer 触发）

把「发现设备级封禁 → 批量换设备身份 → 让新表生效」做成仓库内可部署资产：
一个判定脚本 + 一个批量轮换脚本 + 一对 systemd unit。

完整运维口径见 [../../docs/marvis-qimei-rotation.md](../../docs/marvis-qimei-rotation.md)（触发判据、周期、
dry-run、落盘、reload、回滚、防抖、告警）。本文件只讲「怎么装、怎么跑、参数是什么」。

| 文件 | 作用 |
| --- | --- |
| `auto_rotate_on_banned.sh` | 判据：统计 4100404 → 排除配额/凭证 → 决定轮不轮换（timer 的 ExecStart） |
| `rotate_all_qimei.sh` | 执行：先攒后写、只写一次、只部署一次（手工或上者的下游） |
| `qimei-rotate.service` | oneshot：跑一次判定 |
| `qimei-rotate.timer` | 每 6 小时（00/06/12/18:17）+ 开机 5 分钟后，Persistent=true |

## 安装

```bash
# 1) 单位放到 systemd
sudo install -m 0644 deploy/qimei-rotate/qimei-rotate.service /etc/systemd/system/
sudo install -m 0644 deploy/qimei-rotate/qimei-rotate.timer   /etc/systemd/system/
sudo systemctl daemon-reload

# 2) 日志目录（脚本自己 mkdir，权限不对就先手工建）
sudo install -d -m 0750 /var/log/qimei-rotate

# 3) 站点本地覆盖（生成器地址 / 热重载命令）。没有这个文件也能跑，但生成器必须配一项。
sudo install -d -m 0750 /etc/miraihub
sudo tee /etc/miraihub/qimei-rotate.env >/dev/null <<'EOF'
# 纯 Python 生成服务（推荐）
QIMEI_GEN_URL=http://172.17.0.1:8792/qimei
# 或者本机采值脚本（部署数据，仓库不给默认值）
#QIMEI_GEN_CMD=/deploy-data/qimei/gen_qimei_for_mac.sh
# 热重载钩子（c2 落地后填；空 = 回落一次 compose 重启）
#QIMEI_RELOAD_CMD=curl -fsS -X POST http://127.0.0.1:8790/...
EOF

# 4) 先手动跑一次（dry-run，不写表不部署）
sudo QIMEI_AUTOROTATE_DRY_RUN=1 systemctl start qimei-rotate.service
sudo journalctl -u qimei-rotate.service -n 50 --no-pager

# 5) 启用
sudo systemctl enable --now qimei-rotate.timer
systemctl list-timers qimei-rotate.timer --no-pager
```

## 参数

全部走环境变量；unit 里给了默认值，`/etc/miraihub/qimei-rotate.env` 可覆盖任何一项。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MIRAIHUB_DIR` | `/home/admin/MiraiHub` | 仓库根（compose 在这里跑） |
| `CONTAINER` | `miraihub-admin-1` | admin 容器名（docker exec / logs / inspect） |
| `QIMEI_STATE_DIR` | `$MIRAIHUB_DIR/admin/platform/marvis/core` | `qimei_table.json` 所在目录 |
| `QIMEI_LOG_DIR` | `/var/log/qimei-rotate` | 流水、告警、状态、备份、锁 |
| `QIMEI_GEN_URL` | 空 | 纯 Python 生成服务：`POST /qimei {"mac":"..."}` → `{"qimei36":"..."}` |
| `QIMEI_GEN_CMD` | 空 | 本机生成脚本（**mac tag port octet** 四个参数，stdout 末行取值） |
| `QIMEI_RELOAD_CMD` | 空 | 热重载命令；返回 0 视为已生效（不重启容器） |
| `QIMEI_DEPLOY_MODE` | `auto` | `auto`（重载优先、失败回落重启）/ `reload`（只重载，失败即失败）/ `restart` |
| `QIMEI_WRITE_MODE` | `inplace` | `inplace`（就地写同一 inode，热重载可见）/ `rename`（.tmp + os.replace，必须重启） |
| `QIMEI_WINDOW_MIN` | `10` | 4100404 统计窗口（分钟） |
| `QIMEI_MIN_HITS` | `2` | 每号在窗口内的最少命中次数 |
| `QIMEI_COOLDOWN_SEC` | `7200` | 冷却：同一轮封禁 2 小时内不再触发 |
| `QIMEI_POST_WATCH_SEC` | `300` | 轮换后观察窗口；仍有 4100404 就告警且**不重试** |
| `QIMEI_WALLET_TRIES` | `3` | 钱包预检最多试几个号 |
| `QIMEI_AUTOROTATE_DRY_RUN` | `0` | 置 1 = 只调 `rotate_all_qimei.sh --dry-run` |
| `QIMEI_GEN_GAP` | `1` | 逐个生成之间的间隔秒数 |
| `QIMEI_ROTATE_CMD` | `$MIRAIHUB_DIR/deploy/qimei-rotate/rotate_all_qimei.sh` | 判定脚本要调的下游 |

## 手工用法

```bash
# 只看会换什么（生成 + 形态闸门，不写表不部署）
sudo deploy/qimei-rotate/rotate_all_qimei.sh --all --dry-run

# 真换：写表 + 部署（reload 钩子配好就不重启）
sudo deploy/qimei-rotate/rotate_all_qimei.sh --all --deploy

# 只换指定账号，先写表不部署，人工核对后再部署
sudo deploy/qimei-rotate/rotate_all_qimei.sh --accounts 189,190
sudo deploy/qimei-rotate/rotate_all_qimei.sh --accounts 189,190 --deploy
```

## 退出码

`rotate_all_qimei.sh`：0 成功 / 2 参数或环境错 / 3 账号或 uid 取不到 / 4 形态闸门失败 /
5 生成失败或生成器未配置 / 6 写表失败 / 7 部署或校验失败。

`auto_rotate_on_banned.sh`：0 无需/已处理（含防抖、dry-run）/ 2 参数或环境错 /
8 轮换失败（**不重试**）/ 9 轮换后仍有 4100404（判定无效，**不重试**）。

## 演练与线上数据边界（必读）

`rotate_all_qimei.sh` 默认就指向**线上**真值表（`QIMEI_STATE_DIR` 默认
`$MIRAIHUB_DIR/admin/platform/marvis/core`）——它是线上工具，不是演练工具。任何试验一律换目录：

```bash
# 脚本侧：整条链路都指到 tmp
QIMEI_STATE_DIR=$(pwd)/tmp/qimei-state QIMEI_LOG_DIR=$(pwd)/tmp/qimei-log \
  sudo deploy/qimei-rotate/rotate_all_qimei.sh --all --dry-run

# qimei_assign CLI 侧：必须显式 --state-dir（漏了就会写真实台账）
python3 -m admin.platform.marvis.core.qimei_assign \
  --state-dir tmp/qimei-state --reconcile --dry-run
```

`admin/platform/marvis/core/qimei_table.json` 与 `qimei_pool.json` 是**线上数据**：
HANDOFF 硬约束禁止改内容，实验必须用 `QIMEI_STATE_DIR=tmp/...`（或 `--state-dir tmp/...`）。

误写过的特征：pool 里出现 `owners=["A1"]` 之类的测试值、目录里多出 `qimei_state.lock` 残留。
恢复：`git checkout -- admin/platform/marvis/core/qimei_table.json admin/platform/marvis/core/qimei_pool.json`，
再删掉 `qimei_state.lock`。

跑 `python -m unittest tests.test_qimei_rotation` 之前，先确认 shell 里没有 `QIMEI_STATE_DIR`
指向仓库 core 目录（这组测试只读文件，不写盘、不设 env）。

## 两个容易踩的点

1. **单文件 bind mount 的 inode**：`qimei_table.json` 在容器里是单文件挂载。宿主机用
   `.tmp + os.replace` 换文件 = 换 inode，容器里那个挂载点仍指旧 inode —— 看起来
   「表写了」但容器永远读到旧值。所以默认 `QIMEI_WRITE_MODE=inplace`：truncate + 写回
   同一 inode。选 `rename` 就必须靠 restart 生效（脚本会自动强制 restart）。
2. **别手工改表里的一行**：`table` 是读路径权威、`pool` 是容量台账，加载时会归一化合并；
   人手改一行 = 下次加载 table/pool 不一致。要改就走这个脚本或
   `python3 -m admin.platform.marvis.core.qimei_assign --reconcile`。

## 自检

```bash
python -m unittest tests.test_qimei_rotation -v
```

纯 Python 静态断言（unit/timer 的 ExecStart、OnCalendar、Persistent；脚本的 flock、冷却、
dry-run、`--deploy` 分离、钱包预检、失败不重试）。本机没有 bash/WSL 时 `bash -n` 会
skip 并注明原因——那一步要在 Linux 上跑。
