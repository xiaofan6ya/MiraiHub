# Python QIMEI 生成器：显式配置与可选验证服务

此入口通过 HTTPS 向服务端申请 QIMEI，不加载 Windows SDK/Wine，不读取安装配置、硬件、账号库或设备池，也不会用本地摘要冒充 QIMEI。组件向量和受控 Linux 验证覆盖相同合成配置的稳定签发、不同 MAC 配置的标识区分；这些结果不证明历史物理设备保真性，也不证明任意新配置都能被服务端接受。入口显式启用，不修改应用的默认生成器路由或部署单元。

## 运行契约

使用 Python **3.11+**；独立虚拟环境只需 `httpx`、`cryptography`，两者已在仓库 `requirements.txt`。按文件路径运行，避免加载 `admin` 包的应用启动逻辑：

```text
python admin/platform/marvis/core/qimei_generate.py MAC TAG PORT OCTET
```

MAC 接受一致的冒号或连字符格式，必须是非零单播地址；TAG 等于其末四位十六进制，PORT 为 `8801..8899`，OCTET 为 `60..119`。后三项仅校验旧命令兼容性，不进入设备字段，也不实际创建网络命名空间或监听该 PORT。成功时 stdout 只有一行小写 QIMEI；失败时返回非零状态。必须同时显式设置：

```text
QIMEI_PYTHON_GENERATOR_ENABLED=1
QIMEI_PYTHON_GENERATOR_CONFIG=/absolute/path/config.json
```

未启用时退出码为 2，不打开配置或发送请求。不会自动查找 `.env`。每次请求生成新的 AES key/IV、nonce、RSA 填充和时钟值；MAC 规范化后稳定映射到 header 8/device 32 的合成 GUID、device 20 的小写十二位 MAC 和 device 42 的合成适配器描述。GUID 的 MD5 仅用于合成身份模板，最终 QIMEI 必须来自响应中的合法 36 位主字段；HTTP 200 或一个 32 位字段本身不算成功。

## 配置

UTF-8 JSON 最大 64 KiB。顶层**恰好**包含下方示例的 12 项；拒绝重复键、未知键及非 JSON 数字。字符串设备值为可打印 ASCII；device 49/53 为 uint32。`header` 恰好需要 `"7"`、`"10"`、`"11"`、`"12"`，不能填 header 8。device 20/32/42 由 MAC 生成；31/52 不能配置。device 1 可显式提供 PID 文本，50 可提供扩展映射的 JSON 文本。其余槽位可显式提供，未提供的不凭空采集或填充。

`app_key` 必须由使用者填写，长度 1..32 个可打印 ASCII 字节；`app_version` 非空。HTTPS endpoint、HTTP headers、RSA 公钥、inner key/version、签名参数均显式指定。下面使用公开 SDK 默认公钥、公开 inner fallback key，以及全零种子/尾部的签名 version 0；这些是明确的示例选择，不是自动发现的部署值。`timeout_seconds` 须大于 0 且不超过 120。

配置是本地部署数据，放在仓库外；目录权限 `0700`、文件 `0600`，由服务用户持有。不要提交、复制进镜像或输出配置内容；仓库内临时副本应另行加入本地忽略规则。本例假设非 root 服务用户和 `/opt` 下的仓库/虚拟环境已经准备好，以免被服务模板的 `ProtectHome=true` 隐藏。

以下命令生成**完整合成配置**，只从持久回归夹具读取公开 RSA 公钥。`FILL_AUTHORIZED_APP_KEY` 必须由使用者替换；endpoint、User-Agent 和 `1.60.3400.35` 是受控测试中显式使用的值。所列 CPU/存储模板形态已用于受控签发验证；它们是人工输入，不是当前机器信息，不代表物理机保真性。按设备区分的 MAC/GUID 由入口生成，其他共享模板值由使用者显式维护，不额外推导硬件字段。

```bash
QIMEI_REPO=/opt/MiraiHub
QIMEI_PYTHON=/opt/miraihub-qimei-venv/bin/python
QIMEI_CONFIG=/etc/miraihub-qimei-python/config.json
QIMEI_USER=qimei-gen
sudo install -d -m 700 -o "$QIMEI_USER" -g "$QIMEI_USER" /etc/miraihub-qimei-python
sudo -u "$QIMEI_USER" "$QIMEI_PYTHON" - "$QIMEI_REPO" "$QIMEI_CONFIG" <<'PY'
import json
import os
from pathlib import Path
import sys

repo, destination = Path(sys.argv[1]), Path(sys.argv[2])
public = json.loads((repo / "tests/qimei_packet_sdk_vectors.json").read_text("utf-8"))["common"]["rsa_public_key_pem"]
storage_marker = "{0123456789abcdef0123456789a}"  # 27 hex + 花括号，共 29 字符
config = {
    "schema": 1,
    "app_key": "FILL_AUTHORIZED_APP_KEY",
    "app_version": "1.60.3400.35",
    "endpoint": "https://snowflake.qq.com/ola/v2",
    "headers": {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (Windows NT 6.1; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0", "Connection": "close"},
    "header": {"7": "Windows 11 Enterprise,level 22631", "10": "0" * 32, "11": "Marvis.exe", "12": "net_0"},
    "device": {
        "24": "Intel(R) Core(TM) i7-9700 CPU @ 3.00GHz",
        "25": "BFEBFBFF000906ED",
        "49": 0,
        "53": 0,
        "54": '{"C:":"0_1234abcd_0"}',
        "55": '{"0":"Synthetic SSD"}',
        "56": storage_marker,
        "57": "C:#" + storage_marker + "#0"
    },
    "inner_version": 1,
    "inner_key_hex": "FF802003F67F55BE8B535A97B01B459D",
    "signing": {"version": 0, "seed8_hex": "0000000000000000", "tail4_hex": "00000000", "enabled": True},
    "rsa_public_key_pem": public,
    "timeout_seconds": 15
}
os.umask(0o077)
with destination.open("x", encoding="utf-8") as output:
    json.dump(config, output, ensure_ascii=True, indent=2)
    output.write("\n")
destination.chmod(0o600)
PY
```

使用 `"x"` 创建模式，已有配置会拒绝覆盖。发布镜像不含 `tests` 时，应在带公开夹具的源码检出中完成此步骤，再按本地部署流程安装配置；不要把真实配置反向写入夹具。

## 不发请求的检查

禁用冒烟检查不需要配置，预期 stderr 提示 disabled、stdout 为空、退出码 2：

```bash
env -u QIMEI_PYTHON_GENERATOR_ENABLED "$QIMEI_PYTHON" \
  "$QIMEI_REPO/admin/platform/marvis/core/qimei_generate.py" \
  02:11:22:33:44:55 4455 8801 60
```

脚本没有 `--validate` 选项。可按文件加载其配置校验函数，以下命令只报告通过/失败，不输出值、不构造请求：

```bash
sudo -u "$QIMEI_USER" "$QIMEI_PYTHON" - "$QIMEI_REPO" "$QIMEI_CONFIG" <<'PY'
import importlib.util
from pathlib import Path
import sys

try:
    source = Path(sys.argv[1]) / "admin/platform/marvis/core/qimei_generate.py"
    spec = importlib.util.spec_from_file_location("qimei_config_check", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.load_config(sys.argv[2])
except Exception:
    print("config: invalid", file=sys.stderr)
    raise SystemExit(2)
print("config: valid")
PY
```

`config: valid` 只说明结构和类型有效，不验证 app key、endpoint 或设备模板会被服务端接受。

## 8793 可选服务

参考 `qimei-python-gen.service.example`，另建 `qimei-python-gen.service`；填写仓库、虚拟环境、用户和配置的绝对路径。关键配置是：

```ini
Environment=QIMEI_PYTHON_GENERATOR_ENABLED=1
Environment=QIMEI_PYTHON_GENERATOR_CONFIG=/etc/miraihub-qimei-python/config.json
Environment=QIMEI_GEN_SHELL=/opt/miraihub-qimei-venv/bin/python
Environment=QIMEI_GEN_CMD=/opt/MiraiHub/admin/platform/marvis/core/qimei_generate.py
Environment=QIMEI_GEN_TIMEOUT=125
ExecStart=/opt/miraihub-qimei-venv/bin/python /opt/MiraiHub/admin/platform/marvis/core/qimei_assign.py --serve-generator --host 127.0.0.1 --port 8793
```

wrapper 超时应大于配置的 HTTPS 超时及清理时间。不要启用 `QIMEI_GEN_SUDO`。该 HTTP wrapper 没有鉴权，默认只绑定回环；跨容器访问须另行明确监听地址和访问控制。`GET /health` 仅证明 wrapper 存活，不验证上游签发能力。`POST /qimei` 的现有契约为：

```json
{"mac":"02:11:22:33:44:55"}
```

成功响应为 `{"ok":true,"qimei36":"<服务端签发的36位值>"}`，失败为非 200。受控签发会实际调用上游，应单独执行，不能作为健康探测。

生产数据迁移、历史处置和回滚方案完成相应评审及操作批准之前，不替换生产的 `QIMEI_GEN_URL`、命令或原 Wine unit；功能验收采用已获接受的 Linux 端到端结果。验证服务独立使用 8793；现有 8792 Wine 服务保留。未来评审通过后，调用方可显式选择 `QIMEI_GEN_URL=http://127.0.0.1:8793/qimei`；回滚恢复先前记录的路由，原 Wine 服务无需改写。

生成器只返回标识，不负责账号分配和持久化。`QIMEI_SNAPSHOT_PATH` 是调用方状态后端的独立可选项，应在单独的迁移/回滚流程中配置；启用 Python 生成器不会迁移、清空或重建现有状态。
