# Marvis uskey 签名服务 —— Linux 部署指南（已验证上线）

> 状态：**已在生产环境部署并验证通过**
> 部署时间：2026-11-18
> 原理：Linux 不能加载 Windows PE，改用 wine 托管签名，网关通过 loopback HTTP 调用

---

## 一、线上实测数据

| 指标 | 数值 | 备注 |
|---|---|---|
| wine 签名服务内存 | **91 MB** | `systemd` 统计 139 MB（含 pty 包装） |
| 网关内存 | 129 MB | 原有水平，未因本方案增加 |
| 签名延迟 | **1.62 ms/次** | 网关侧实测（连接复用） |
| 吞吐 | 617 次/秒 | 单进程 |
| wine 安装后磁盘 | +700 MB | 40G 盘从 5.2G 降到 4.5G |
| Python embeddable | 11 MB | python.org 下载，国内建议用华为云镜像 |

**部署后线上验收结果：**

```
非流式    deepseek-v4.1-flash-marvis → 200  model:"deepseek-v4-1-flash-261017"
          hy3-marvis                 → 200  model:"hy3"  content:"好。"
流式 SSE  → 正常吐 chunk
连发 6 次 → 6/6 全 200
```

---

## 二、关键坑：wine 需要真 pty

### 现象

wine 里的 python 在没有终端时直接崩：

```
Fatal Python error: init_sys_streams: can't initialize sys standard streams
Python runtime state: core initialized
OSError: [WinError 6] Invalid handle
```

**前台跑（有 tty）完全正常**，但用以下方式启动都会崩：

- `nohup wine ... &`
- `setsid wine ...`
- systemd `Type=simple` 直接 `ExecStart=wine ...`

### 原因

wine 把 Linux 的文件描述符映射成 Windows handle。进程没有控制台时，
标准流拿到的是无效句柄，python 初始化 `sys.stdin/stdout/stderr` 就失败。

### 解法

用 util-linux 的 `script` 命令包一层，它 fork 一个伪终端再执行：

```ini
ExecStart=/usr/bin/script -qfc "/usr/bin/wine /opt/marvis-sign/py/python.exe /opt/marvis-sign/uskey_sign_service.py --port 8792" /dev/null
```

**这是整个部署里最关键的一行。** 没有它，服务永远起不来。

---

## 三、端口规划（线上实际占用）

| 端口 | 用途 |
|---|---|
| 8790 | `workbuddy2api`（网关本体） |
| 8791 | `wb2api-upgrade-gate`（升级等待代理）**已被占用** |
| **8792** | `marvis-uskey`（本签名服务） |

> ⚠ 不要用 8791，会和 upgrade-gate 抢端口。

---

## 四、完整部署步骤

### 4.1 装 wine

```bash
# Alibaba Cloud Linux 3 / RHEL 8 系
dnf install -y --setopt=keepcache=0 wine-core

# 验证
wine --version   # wine-6.0.2
```

只装 `wine-core` 即可，不需要字体包和音频组件，省 60 MB。

### 4.2 准备 Windows python

```bash
mkdir -p /opt/marvis-sign/py && cd /opt/marvis-sign

# python.org 在国内很慢，用华为云镜像
curl -sSL -o py.zip \
  https://mirrors.huaweicloud.com/python/3.11.9/python-3.11.9-embed-amd64.zip

unzip -q py.zip -d py && rm -f py.zip
```

embeddable 包只用标准库（`ctypes` / `json` / `http.server`），不用改 `._pth`。

### 4.3 放文件

```bash
cp <repo>/vendor/beacon/beacon_wrapper.dll /opt/marvis-sign/
cp <repo>/vendor/beacon/qimei.dll          /opt/marvis-sign/
cp <repo>/tools/uskey_sign_service.py      /opt/marvis-sign/
chown -R www:www /opt/marvis-sign
```

### 4.4 systemd 服务

```bash
cat > /etc/systemd/system/marvis-uskey.service <<'EOF'
[Unit]
Description=Marvis uskey sign service (wine + pty)
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=www
Group=www
WorkingDirectory=/opt/marvis-sign

Environment=HOME=/opt/marvis-sign
Environment=WINEPREFIX=/opt/marvis-sign/.wine
Environment=WINEDEBUG=-all
Environment=WINEDLLOVERRIDES=mscoree,mshtml=

# ★ 关键：script 分配 pty
ExecStart=/usr/bin/script -qfc "/usr/bin/wine /opt/marvis-sign/py/python.exe /opt/marvis-sign/uskey_sign_service.py --port 8792" /dev/null

Restart=always
RestartSec=6

StandardOutput=append:/opt/marvis-sign/logs/service.log
StandardError=append:/opt/marvis-sign/logs/service.log

MemoryMax=256M
MemoryHigh=192M
CPUQuota=50%

[Install]
WantedBy=multi-user.target
EOF

mkdir -p /opt/marvis-sign/logs
chown -R www:www /opt/marvis-sign

systemctl daemon-reload
systemctl enable --now marvis-uskey
```

首次启动 wine 会初始化 `WINEPREFIX`（约 15~20 秒），之后重启很快。

### 4.5 验证

```bash
curl -s http://127.0.0.1:8792/health
# {"ok": true, "mode": "wine", "dll": "Z:\\opt\\marvis-sign\\beacon_wrapper.dll", "error": "", "pid": 32}

curl -s -X POST http://127.0.0.1:8792/sign \
  -H 'Content-Type: application/json' \
  -d '{"busiid":"7800255","bus_info":"marvisAgentTag=main"}'
# {"ok": true, "uskey": "CsDVDCER..."}
```

> `mode: wine` 和 `平台: win32` 是关键。若显示 linux，说明跑的是原生 python，
> `ctypes.CDLL` 加载不了 PE，会报 `not a valid Win32 application`。

### 4.6 网关接入

```bash
# 给网关的 systemd 服务加环境变量
sed -i '/Environment=PYTHONMALLOC=malloc/a Environment=MARVIS_USKEY_SERVICE=http://127.0.0.1:8792/sign' \
    /etc/systemd/system/workbuddy2api.service

systemctl daemon-reload
systemctl restart workbuddy2api
```

验证网关走远程模式：

```bash
journalctl -u workbuddy2api -n 50 --no-pager | grep uskey
# INFO [admin.platform.marvis.core.uskey_signer] Marvis uskey 走远程签名服务：http://127.0.0.1:8792/sign
```

---

## 五、故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 服务起不来，日志 `init_sys_streams` | 没用 `script` 包 pty | 检查 `ExecStart` 是否以 `/usr/bin/script -qfc` 开头 |
| `找不到 beacon_wrapper.dll` | DLL 没和脚本同目录 | `ls /opt/marvis-sign/*.dll` |
| `is not a valid Win32 application` | 跑的是原生 python | 确认 `which python` 是 wine 里的，不是 `/usr/bin/python3` |
| `wine: '/opt/xxx' is not owned by you` | root 跑了属主是 www 的目录 | 用 `User=www` 跑，或把目录给 root |
| 网关 `uskey 签名服务不可达` | 端口不对 / 服务没起 | `curl 127.0.0.1:8792/health` |
| 签名超 10ms | 连接没复用 | 确认网关用 `uskey_remote`（已实现 keep-alive） |
| 内存涨到 256M 被杀 | 异常 | `MemoryMax` 兜底，`Restart=always` 会自动拉起 |

---

## 六、日常运维

```bash
systemctl status marvis-uskey        # 状态
systemctl restart marvis-uskey       # 重启（约 20 秒）
journalctl -u marvis-uskey -n 50     # 日志
curl -s 127.0.0.1:8792/health        # 探活
```

**注意**：`marvis-uskey` 的日志在 `/opt/marvis-sign/logs/service.log`，
不在 journal 里（因为 `script` 会把 pty 输出写进去）。

---

## 七、为什么不用别的方案

| 方案 | 问题 |
|---|---|
| 网关跑 Windows | 要有 Windows 机器常在线 |
| 纯 Python 复现算法 | **算法还没逆向出来**（见 `uskey-reverse-report.md`） |
| 直接 `ctypes.CDLL` 加载 PE | Linux 不认 PE 格式 |
| 每请求起一个 wine 进程 | 启动开销几百毫秒，不可接受 |
| Box86/Box64 | 只做指令翻译，不含 Windows API |

---

## 八、长期路线

wine 是**过渡方案**。最终目标是纯 Python 实现。

逆向进展见 [uskey-reverse-report.md](./uskey-reverse-report.md)：
- ✅ 载荷格式已完全破译（qimei36 + GUID + 13位毫秒时间戳 + 设备信息 + URL编码bus_info）
- ✅ 实现地址锁定到 RVA `0x41190`（`beacon_get_uskey` 只是 57 条指令的转发壳）
- ✅ 确认它没有加密循环、不调用任何标准密码学函数
- ❌ 最后一层变换还没破

破掉之后把 `MARVIS_USKEY_SERVICE` 去掉即可切回本地计算，连 wine 都不用。
