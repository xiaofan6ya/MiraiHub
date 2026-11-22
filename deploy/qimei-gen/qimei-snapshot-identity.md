# QIMEI 原子快照与显式账号身份

生成器返回的 QIMEI、账号分配和登录设备绑定是不同阶段。快照模式用一个文件原子提交 table、pool、metadata；只有显式设置 `QIMEI_SNAPSHOT_PATH` 时启用，旧双文件后端与默认签名后端保持原有行为。不要同时配置 `QIMEI_TABLE_PATH` 或 `QIMEI_POOL_PATH`。

## 初始化与迁移

使用 Python 3.10+（容器内为 3.12；低于 3.11 时运输层自动改用等价的本地截止实现）。以下均为部署参数：

```bash
QIMEI_REPO=/opt/MiraiHub
QIMEI_PYTHON=/opt/miraihub-qimei-venv/bin/python
QIMEI_ASSIGN="$QIMEI_REPO/admin/platform/marvis/core/qimei_assign.py"
QIMEI_SNAPSHOT=/var/lib/miraihub-qimei/qimei_state.json
QIMEI_LEGACY=/absolute/path/to/legacy-state

env -u QIMEI_TABLE_PATH -u QIMEI_POOL_PATH \
  QIMEI_SNAPSHOT_PATH="$QIMEI_SNAPSHOT" \
  "$QIMEI_PYTHON" "$QIMEI_ASSIGN" --migrate-state \
  --from-legacy-dir "$QIMEI_LEGACY" --dry-run --summary-only
```

先确认预览，再在写入暂停的维护窗口去掉 `--dry-run` 执行同一迁移。来源不改写，不生成新身份；目标已有不同内容时拒绝覆盖。所有写入方、读者、挂载和回滚流程必须使用同一快照目录。

## 请求应用与显式纳管

快照模式的聊天请求直接使用本地 Python mint：账号映射只读取一次，返回的 QIMEI 同时用于 `MarvisExt.qimei36` 和签名 TLV 的两处设备字段。必需快照缺失、损坏、账号未分配、本地签名失败，均拒绝发送；不转发给无法接收该账号身份的设备级远程/DLL/侧车，也不受 `MARVIS_USKEY_FAIL_CLOSED=0` 的实验随机回退影响。`MARVIS_QIMEI36` 全局覆盖和强制侧车选项不能用于此模式。

仅快照模式仍保留旧池的最多两 owner 契约。要求每账号独占时，再显式设置 `QIMEI_MANAGED_IDENTITY=1`。启用前先在独立维护进程逐个纳管需要使用的账号；该模式下没有纳管记录的账号拒绝发送，不会隐式创建记录。纳管要求已有服务端生成、真实标记为 true、单 owner 的 QIMEI，以及当前登录/导入流程提供的有效 GUID。共享池记录、重复 GUID、派生应急值和冲突绑定均拒绝。

维护代码可使用以下库接口；变量来自应用已授权的账号/登录流程，不要将真实身份写入公开源码或报告：

```python
from admin.platform.marvis.core import device_identity

preview = device_identity.enroll_ex(account_uid, guid=login_guid, dry_run=True)
result = device_identity.enroll_ex(account_uid, guid=login_guid)

# new_login_guid 必须来自新登录/明确导入；不能使用占位值冒充重新认证。
preview = device_identity.reset_ex(account_uid, guid=new_login_guid, dry_run=True)
result = device_identity.reset_ex(account_uid, guid=new_login_guid)
```

维护进程必须显式配置相同的 managed flag、snapshot 和生成器路由。摘要只含状态/计数/revision/generation；不含 GUID/QIMEI。`reset_ex` 为当前账号生成全新、池中未使用的 QIMEI，并在一个 revision 中提交其 QIMEI、登录 GUID、随机 seed 和 generation。dev18 与会话命名空间从该账号记录派生；B 的写入不会改变 A。CID 仍按账号与真实对话区分，RID/nonce/签名随机部分保持每请求新建。UA、SDK 版本、业务 ID 等协议常量无需按账号随机改变；本功能不保证不同账号拥有不同出口 IP。

重置不创建新 access/refresh token，也不改账号库。快照提交后，旧 GUID 凭证的新请求会被拒绝，需由登录/导入流程保存新凭证；若已有 `credential.device`，其 GUID/QIMEI 也必须与记录一致。这是明确的两阶段维护，不宣称跨账号数据库与文件快照的事务。存储和离线请求验证不证明新登录凭证已获上游接受，也不证明合成 QIMEI 设备模板与物理登录设备同源。

每个请求捕获一个不可变上下文：重置前已捕获的在途请求可用完整旧身份结束，重置后捕获的请求使用完整新身份，不持锁等待 SSE 结束。该契约不取消已在途请求。钱包和续签同样核对当前登录 GUID，不能用聊天的新 GUID 搭配旧认证 GUID。

请求进程缓存最近一份已校验快照及其不可变账号上下文。每次新请求检查规范路径、文件标识、大小和纳秒时间戳，文件替换后重新读取并校验全部绑定和纳管记录。读取期间版本变化会有限重试，缺失、损坏或持续变化时拒绝请求。维护写入和启动校验始终直接读取文件。所有修改应使用快照库的原子写入入口，保证新文件标识能够触发各进程缓存失效。

Windows 读句柄共享删除权限，支持的系统与文件系统使用 `FileRenameInfoEx` 的 POSIX 替换语义，让已打开的读者完成旧版本读取，新读者取得新版本。路径解析保留稳定文件名，避免并发替换时解析到已删除版本的内部路径。旧系统或文件系统明确不支持该 API 时采用 `os.replace`，并发读期间仍可能拒绝写入，调用方需处理错误后重试。提交成功后句柄关闭失败同样返回 `SnapshotCommitUncertain`，应先读回状态。本机符号链接测试受权限限制跳过，Linux 路径另经隔离回归验证。

纳管后，普通 QIMEI-only `--rotate`/到期轮换拒绝修改这些账号，必须使用完整 reset；旧分配器也不会将其独占设备共享出去。显式 release 或 reconcile prune 会在同一个快照提交中移除账号的纳管记录，其他账号保持有效。无纳管记录的旧模式不受这些限制。

生成失败、碰撞、无效绑定和提交前错误不改变快照。若抛出 `DeviceIdentityCommitUncertain`，完整新快照可能已替换但目录持久性未确认，必须重新读取当前账号 generation/绑定后决定是否重试；禁止盲目再次生成或重置。底层 `QimeiCommitUncertain` 与 `SnapshotCommitUncertain` 具有相同的读回要求。

## 手动轮换：仅显式快照模式

`qimei_assign.py` 提供 `--rotate UID` 和 `--rotate-due-days DAYS` 两个互斥操作；它们只接受已经存在的真实绑定，并要求显式 `QIMEI_SNAPSHOT_PATH`。旧表/池双文件模式不能轮换，不能同时设置 `QIMEI_TABLE_PATH` 或 `QIMEI_POOL_PATH`。若仍使用旧模式，应先单独审查迁移、挂载及回滚方案，本节不执行迁移。

所有读写方必须指向同一快照。容器部署应共享挂载**快照所在目录**，让快照、同级稳定锁和临时替换文件位于同一文件系统；不能沿用分别绑定挂载旧表/池文件的方式，也不能只绑定一个待原子替换的快照文件。执行用户需要该目录的写权限。下面路径及 UID 都是待填写的部署参数，示例不会读取账号数据库；`QIMEI_GEN_URL` 只在已经批准的独立验证环境中指向 8793：

```bash
QIMEI_ASSIGN="$QIMEI_REPO/admin/platform/marvis/core/qimei_assign.py"
QIMEI_SNAPSHOT=/var/lib/miraihub-qimei-benchmark/qimei_state.json
QIMEI_ROTATE_UID=FILL_EXISTING_ACCOUNT_UID
QIMEI_ROTATE_URL=http://127.0.0.1:8793/qimei
```

先预览单个绑定，或预览达到 30.5 天的到期绑定、最多选择 10 个账号：

```bash
env -u QIMEI_TABLE_PATH -u QIMEI_POOL_PATH \
  QIMEI_SNAPSHOT_PATH="$QIMEI_SNAPSHOT" \
  "$QIMEI_PYTHON" "$QIMEI_ASSIGN" \
  --rotate "$QIMEI_ROTATE_UID" --dry-run --no-generate --summary-only

env -u QIMEI_TABLE_PATH -u QIMEI_POOL_PATH \
  QIMEI_SNAPSHOT_PATH="$QIMEI_SNAPSHOT" \
  "$QIMEI_PYTHON" "$QIMEI_ASSIGN" \
  --rotate-due-days 30.5 --max-rotate-accounts 10 \
  --dry-run --no-generate --summary-only
```

`--dry-run` 只读取快照，不创建锁或目录、不写状态、不调用生成器；`--no-generate` 在预览中进一步明确意图。预览不占写锁，因此它是当时的候选视图，实际执行会重新读取状态。始终使用 `--summary-only`，它只输出状态和计数；不加此项的轮换 JSON 会包含 UID 和新旧标识。以下仅为计数示例，不代表实际运行结果：

```json
{"ok":true,"status":"ready","dry_run":true,"revision":7,"bindings":12,"devices":8,"eligible":4,"selected":4,"rotated":0}
```

核对预览、生成器路由与窗口后，由操作员手动执行所选操作；执行时去掉 `--dry-run` 和 `--no-generate`：

```bash
env -u QIMEI_TABLE_PATH -u QIMEI_POOL_PATH \
  QIMEI_SNAPSHOT_PATH="$QIMEI_SNAPSHOT" QIMEI_GEN_URL="$QIMEI_ROTATE_URL" \
  QIMEI_GEN_TIMEOUT=125 \
  "$QIMEI_PYTHON" "$QIMEI_ASSIGN" \
  --rotate "$QIMEI_ROTATE_UID" --summary-only

env -u QIMEI_TABLE_PATH -u QIMEI_POOL_PATH \
  QIMEI_SNAPSHOT_PATH="$QIMEI_SNAPSHOT" QIMEI_GEN_URL="$QIMEI_ROTATE_URL" \
  QIMEI_GEN_TIMEOUT=125 \
  "$QIMEI_PYTHON" "$QIMEI_ASSIGN" \
  --rotate-due-days 30.5 --max-rotate-accounts 10 --summary-only
```

两种执行命令按需选择，不应为了检查而连续执行。`--rotate-due-days` 必须是有限正数，支持小数天；`--max-rotate-accounts` 默认 10，允许 1..1000。到期判断使用带时区的有效 `last_rotated`，统一转换到 UTC，截止时间为包含边界的 `now - DAYS`；按最旧时间、规范化 UID 排序。参与判断的真实绑定存在缺失、无时区或无效时间戳时拒绝操作，不猜测年龄。

每批先为所有选中账号取得全新标识，全部成功后才修改内存绑定并一次原子提交快照修订。生成失败、返回非法值、命中池内已有值或批内重复值时，整批不修改快照；已发生的上游申请不会因本地放弃而被撤回。轮换不回退到共用旧设备或派生值。旧池条目及其历史元数据保留，只移除被轮换账号的 owner；同设备的其他 owner 保持原绑定，即使旧条目变为无 owner 也不删除。

执行期间写锁覆盖读取、整批生成和提交，耗时会受批大小、串行生成延迟和超时影响，并可能阻塞其他写入；先使用小批次和明确的生成器超时。成功摘要的 `status` 为 `rotated`；没有候选时为 `unchanged`。执行失败返回退出码 3 和 `rotation_failed`；摘要模式的参数解析失败返回退出码 2 和 `argument_error`，均不回显标识或错误入参。`eligible` 是全部到期账号数，`selected` 受本批上限约束。

网关标识查询在后续读取中重新加载快照。Linux 一次性容器已验证现有 HTTP wrapper 调用 Python CLI 和真实服务，为两个合成账号分配不同标识，再原子轮换其中一个：另一个绑定不变、查询立即读到新值、revision 只增加一次，到期预览不改写快照。这不等同于历史物理机样本或生产切换验收。本节仅提供人工操作，不安装 cron、systemd timer 或其他自动轮换任务，也不改变生产路由、原 Wine 服务或历史数据保留策略。

## 最新快照的回滚导出

快照产生新写入后，恢复旧双文件备份会丢掉新绑定。`--export-state` 只读取显式快照，并导出到一个**尚不存在的新目录**；父目录必须已存在。它不改写源快照、revision 或既有目标。先预览，再按需实际导出：

```bash
QIMEI_EXPORT=/var/lib/miraihub-qimei-rollback/new-export
env -u QIMEI_TABLE_PATH -u QIMEI_POOL_PATH \
  QIMEI_SNAPSHOT_PATH="$QIMEI_SNAPSHOT" \
  "$QIMEI_PYTHON" "$QIMEI_ASSIGN" \
  --export-state --to-legacy-dir "$QIMEI_EXPORT" --dry-run --summary-only

env -u QIMEI_TABLE_PATH -u QIMEI_POOL_PATH \
  QIMEI_SNAPSHOT_PATH="$QIMEI_SNAPSHOT" \
  "$QIMEI_PYTHON" "$QIMEI_ASSIGN" \
  --export-state --to-legacy-dir "$QIMEI_EXPORT" --summary-only
```

实际导出持有快照写锁，创建权限 `0700` 的目录及 `0600` 的文件，逐个同步并按保留 JSON 类型的方式回读校验，最后写 `_qimei_export_complete.json`。必须同时满足**进程成功返回**和**完成标记存在**才能使用：这不是两个文件的原子发布；失败可能留下不完整目录，最终目录同步失败也可能留下持久性不确定的标记。不要覆盖、自动复用或删除这些现场文件；另选新目录重做。

导出包含 `qimei_table.json`、`qimei_pool.json`；快照存在 metadata 时另有 `_qimei_snapshot_metadata.json`。旧双文件读取器忽略该 sidecar，但回滚和以后再迁移时必须保留，导出器不会自动把它重新导入。摘要只返回 revision、绑定/设备计数和 metadata 是否存在，不输出路径或标识。输入快照和所有输出 JSON 分别有 16 MiB 上限。

真正回滚时，操作方仍须暂停所有写入，再使用最新且成功的导出更新旧后端并核对计数、owner 与标识一致性；不可因拿到一个导出目录就直接修改生产挂载或路由。Linux 隔离验证已覆盖源快照不变、元数据类型保留、完成标记和权限；本节没有执行生产迁移或回滚。
