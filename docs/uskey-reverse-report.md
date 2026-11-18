# Marvis uskey 逆向分析报告

> 更新时间：2026-11-18
> 目标：`beacon_wrapper.dll` 的 `beacon_get_uskey`，目标是脱离 Windows DLL 做纯 Python 实现
> 结论摘要：**结构已完全摸清，算法本身未破**。线上用 wine 方案跑着（已验证）。

---

## 一、已确认的事实（都有实验支撑）

### 1.1 输入：载荷是纯文本直拼

从内存抓到的载荷明文：

```
320
db4215ebb2ed553ee359779430001461950c      qimei36（36 字符）
b2eb8c2d9f95b2401ee3c37128f2e7a5          GUID（32 hex）
1794991388484                             13 位毫秒时间戳
b2eb8c2d9f95b2401ee3c37128f2e7a5          GUID 重复
python.exe                                进程名
0Y1
1.60.3400.35                              客户端版本
MarvisKnowledgebase
x64x64                                    架构
Marvis
1.60.3200.18
64100100 002
Windows10.0.26200                         操作系统
marvisAgentTag%3Dmain%26...               URL 编码后的 bus_info
1211th Gen Intel(R) Core...               CPU 型号
```

- **无长度前缀、无分隔符**，纯字符串拼接
- `bus_info` 的 `=` / `&` 被转义成 `%3D` / `%26`
- 13 位毫秒时间戳 = 「同一 bus_info 每次输出不同」的根源

### 1.2 输出结构

```
0ac0d50c2111       6 字节魔数
+ 18 字节          随 bus_info 强雪崩的 digest
+ 352 字节         22 个 16 字节块
= 376 字节         base64 后 504 字符
```

**长度是分档的，不是连续的**：

| bus_info 长度 | uskey 字节 |
|---|---|
| 255 ~ 767（3 倍跨度） | **恒定 376** |
| 极短（`marvisAgentTag=main`） | 264 |

⇒ 体里装的不是原文，是**固定大小的结构**（TLV 之类）。
也解释了「别人实现输出 264 字节」：不是算法不同，是 bus_info 字段更少。

### 1.3 变换性质

| 实验 | 结果 | 含义 |
|---|---|---|
| 仅隔 1.2 秒（时间戳变） | **22 块全变** | CBC 链，IV 依赖时间 |
| model 改一个字符 | 22 块全变 | 同上 |
| nonce / conv 改 | 22 块全变 | 同上 |
| 同 bus_info 两次签名 | 头部 24 字节**完全相同** | 头部只绑 bus_info，不随时间 |
| `X[:24] + Y[24:]`（同 bus_info） | **200** | 头与体可分离 |
| `Z[:24] + X[24:]`（不同 bus_info） | 403 | 头绑定 bus_info |
| 任意位置改 1 比特 | 403 | 全量参与校验 |
| 冻结时间源 + RNG 后 | 前 120 字节确定（24 头 + 6 块） | IV 是确定的，明文前 6 块不含时间戳 |

### 1.4 实现位置

`beacon_get_uskey` 只是个 57 条指令的转发壳：

```asm
0x18002C83E  call 0x18003E230        ; 取单例
0x18002C853  mov  r8,  [rax]         ; vtable
0x18002C856  mov  r10, [r8+0x48]     ; vtable[9]
0x18002C865  call r10                ; ← 真实现
```

单例在 RVA `0x353070`，vtable 在 RVA `0x2D3310`，
**真实现 = RVA `0x41190`（1084 条指令）**。

### 1.5 设备指纹

```
qimei36 = db4215ebb2ed553ee359779430001461950c   （固定）
```

- `beacon_get_qimei(out_buf, size)`，之前返回 -1 是因为漏了 `beacon_init()`
- qimei 初始化后在 `C:\ProgramData\Tencent\qimei\` 落指纹文件
- uskey 长度 376 ↔ 264 的切换与 qimei 状态相关

---

## 二、已排除的可能（全部 hook 实测）

| 算法 | 检测方式 | 结果 |
|---|---|---|
| AES（软件，查表） | 监控 S-Box 表读取 | 0 次 |
| AES-NI（硬件指令） | hook 全部 278 个 `aesenc`/`aeskeygenassist` | 0 次 |
| SHA-256 | hook H0..H3 常量写入点 | 0 次 |
| MD5 | hook B/C/D 独有初值写入点 | 0 次 |
| SHA-1 | hook E=0xC3D2E1F0 | 0 次 |
| CRC32 | hook 反射多项式引用 | 0 次 |
| TEA | qimei.dll 的 delta 点 | 0 次 |
| DPAPI | `CryptProtectData` | 未调用 |
| BCrypt / NCrypt | 相关导出 | 未调用 |
| SystemFunction040/041 | 相关导出 | 未调用 |
| 标准哈希截断 | 155 种组合（md5/sha1/sha256/sha512/blake2/sha3 × 拼接 × HMAC × 折叠） | 全不中 |
| 同上，用 URL 编码输入 | 408 种组合 | 全不中 |
| AES 已知明文攻击 | qimei36 当首块明文 × 60000 个静态 key × 6 种 IV × 18 种对齐 | 全不中 |

**结论：`0x41190` 用自实现变换，key 是运行时派生的，不在静态常量里。**

（DLL 里确实带 OpenSSL 的 AES 实现，含 AES-NI 与 Bit-sliced AES，
但这些是死代码，`beacon_get_uskey` 不走它们。）

---

## 三、为什么动态手段全部失败

### 3.1 二进制形态不落地内存

```
堆上搜 0ac0d50c2111  -> 0 处
栈上搜 0ac0d50c2111  -> 0 处
栈上搜 base64 长串   -> 0 处（但 out_buf 里明明有 504 字符）
```

⇒ DLL **边算边直接输出 base64 字符**，376 字节二进制从不作为完整缓冲存在。

这一条解释了为什么内存快照、扫缓冲、hook 写入全都拿不到中间态。

### 3.2 out_buf 写入追踪

用 `VirtualAlloc` 给 out_buf 分配独占一页 + `MemoryAccessMonitor`：

```
唯一捕获到的写入指令：RVA 0x2285B9  →  rep stosb
```

反汇编确认 `0x2285D0` 是 memset（用 xmm 批量填充）。
即 out_buf 先被清零，而 base64 内容的写入没有走这个页的常规写路径。

### 3.3 `0x41190` 里没有加密循环

线性反汇编 4000 字节、1084 条指令，只有 3 个循环：

| 起 | 止 | 字节 | 指令 | 性质 |
|---|---|---|---|---|
| 0x41400 | 0x41408 | 8 | 3 | 短跳 |
| 0x41430 | 0x41438 | 8 | 3 | 短跳 |
| 0x41950 | 0x41982 | 50 | 14 | 红黑树遍历（`cmp byte [rbx+0x19]` 是节点颜色） |

⇒ 加密不在这里，在它调用的深层。

### 3.4 Stalker 调用图：152 个目标，全是工具函数

用 Stalker 追踪一次完整执行，按密码学特征打分后逐个验证：

| RVA | 实际身份 |
|---|---|
| 0x3D5E0 | `std::string` 分配/重分配 |
| 0x4B130 | 读标志 + 一串 `std::string` 操作 |
| 0x2CF00 / 0x2D060 | 日志输出 |
| 0x2239B4 | `KERNEL32!GetCurrentThreadId`（IAT 解析确认） |

**没有一个是真的变换函数。**

---

## 四、剩余工作的真实评估

要完成纯 Python 实现，必须**逐指令逆向 `0x41190`（1084 条）及其深层调用链**。

没有捷径：

- ❌ 内存快照：二进制不落地
- ❌ 页监控：只抓到 memset，内容写入走别的路径
- ❌ 静态 key 扫描：key 运行时派生
- ❌ Stalker 调用图：152 个目标全是工具函数
- ❌ 已知明文攻击：60000 个 key 全不中

**工作量：3~7 个工作日**，取决于变换是否内联展开。

### 建议的下一步（按性价比）

1. **向对方要参考**：如果能拿到那个 419 行实现的函数签名或关键常量，
   一小时内就能验证是不是同一算法 —— 比自己从 1084 条指令里重建快得多
2. **x64dbg 单步**：在 `0x41190` 下单步 + 跟踪标志寄存器，能看出分组结构
3. **逐指令读**：可控但慢，按天计

---

## 五、替代方案：wine（已在生产验证）

### 架构

```
Linux 网关（原生 python）
    │  HTTP POST /sign {busiid, bus_info}   ← 连接复用
    ▼
127.0.0.1:8792  （wine 里跑的签名服务）
    │  ctypes
    ▼
beacon_wrapper.dll + qimei.dll
```

### 生产实测数据

| 指标 | 数值 |
|---|---|
| wine 签名服务内存 | 91 MB（systemd 统计 139 MB，含 pty 包装） |
| 网关内存 | 129 MB（原有水平） |
| 签名延迟 | 1.62 ms/次 |
| 吞吐 | 617 次/秒 |
| 非流式 | ✅ 200 |
| 流式 SSE | ✅ 正常吐 chunk |
| 连发 6 次 | ✅ 6/6 |

### 关键坑

**wine 必须有真 pty**，否则 python 初始化崩在：

```
Fatal Python error: init_sys_streams
OSError: [WinError 6] Invalid handle
```

`nohup` / `setsid` / systemd 直接 `ExecStart=wine` 全崩，必须用：

```ini
ExecStart=/usr/bin/script -qfc "/usr/bin/wine .../python.exe .../uskey_sign_service.py --port 8792" /dev/null
```

详细部署见 [uskey-wine-deploy.md](./uskey-wine-deploy.md)。

---

## 六、结论

| 项 | 状态 |
|---|---|
| 载荷格式 | ✅ 完全清楚 |
| 输出结构 | ✅ 24 头 + 22×16 体，长度分档 |
| 变换性质 | ✅ CBC 链、IV 依赖时间、不压缩 |
| 实现位置 | ✅ RVA 0x41190 |
| 实现**不**做什么 | ✅ 无标准哈希、无 AES-NI、无 TEA、无 DPAPI |
| **变换算法** | ❌ 内联在深层调用里，二进制不落地 |
| **key 来源** | ❌ 运行时派生 |
| 纯 Python 实现 | ❌ 阻塞在上面两项 |
| Linux 可用 | ✅ wine 方案已上线 |

**如果目标是「Linux 上能稳定跑」→ 已完成。**
**如果目标是「彻底不依赖 Windows 二进制」→ 还需 3~7 个工作日逐指令逆向。**
