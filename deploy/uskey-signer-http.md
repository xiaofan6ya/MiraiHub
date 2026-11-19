# 外部 uskey 签名服务 —— HTTP 契约

MiraiHub 需要一块**真** `Ual-Access-MarvisExt` uskey。它由厂商 SDK 在一块
"设备"上整块产出，本地拼装（`base64(magic + F(bus_info) + 随机尾)`）一定会被
上游按 `4100404` 拒掉。SDK 常常**只能在某个特定环境里跑**（Windows PE 模块、
特定 ABI、特定运行库）。所以把它拆成两半：

- **签名在能跑 SDK 的那台机器上**（"签名服务"），
- **MiraiHub 只做 HTTP 客户端**（`core/uskey.py`）。

本文只规定两边的**接口**。怎么把 SDK 跑起来（原生、容器、兼容层、直接放另一台机器）
属于部署细节，不进仓库。

## 部署形态

```
MiraiHub（容器 / 进程）  --- HTTP --->  签名服务（有 SDK 的那台机器）
        MARVIS_USKEY_SIDECAR=http://<host>:<port>/uskey
```

- 同一 netns / 同机调试：`http://127.0.0.1:<port>/uskey`
- 跨容器调用：地址写**该网卡的地址**，别写 `127.0.0.1` —— 在容器里那是容器自己。
- 地址属部署数据：走环境变量，不要写进代码默认值。

## 协议

```
GET /health
  -> {"ok": true, "qimei36": "<36 hex>", "calls": <int>,
      "busId": "<9 字符业务 id>", "appVer": "<SDK 版本>"}

POST /uskey
  {"agentTag": "main", "conversationId": "conv_<32 hex>",
   "responseId": "<uuid4>", "scene": "agent_chat",
   "model": "<请求体里的模型名原文>", "nonce": "<uuid4>",
   "openId": "<账号 openid>"}
  -> {"uskey": "<504 字符 / 376 字节>", "qimei36": "<36 hex>",
      "busInfo": "<服务端实际用的 bus_info>", "uskeyLen": 504, "ms": <int>}
```

失败一律非 2xx + `{"ok": false, "error": "..."} `（客户端按"签名不可用"处理）。

## 不变量（客户端会逐条校验，缺一条就当场炸）

1. **`bus_info` 七字段固定顺序**，错一个字符 `F(bus_info)` 就错，整块被拒：
   ```
   marvisAgentTag&marvisConversationID&marvisResponseID&marvisScene&model&nonce&originOpenID
   ```
2. **`model` 取请求体原文**，其余六项必须与请求头一字不差。
3. **设备串三处一致**：header 的 `Ual-Access-MarvisExt.qimei36` + TLV 两处。
4. **长度**：376 字节 / 504 字符（`core/uskey.py::USKEY_MIN_BYTES` 之下直接拒，
   短包发上去就是 403，只会污染号池）。
5. **一组凭证只能用一次**：`(conversationId, responseId, nonce, uskey)` 是原子组，
   撞 `4100404` 必须整组换新，不许只换其中一个。

## 客户端侧的三条铁律

1. **签名服务挂了不许本地造 uskey**：那会把"服务故障"变成"账号被风控"，
   还会把风控计数记到账号头上。`uskey.sign()` 抛 `SidecarError` 就让它抛
   （`MARVIS_USKEY_FAIL_CLOSED=0` 只给复现实验用）。
2. **常驻，别每请求起进程**：冷启是秒级，常驻后单次签名是毫秒级。
3. **别用管道接子进程 stdout**：某些运行时在 stdout 是管道时会直接崩，
   日志一律重定向到文件。

## 自检

```
curl -fsS http://<host>:<port>/health
python -c "from admin.platform.marvis.core import uskey; print(uskey.health())"
python -c "from admin.platform.marvis.core import uskey_local as U; print(U.selfcheck())"
```

`health` 看服务在不在；`uskey.health()` 看客户端连得上不；
`uskey_local.selfcheck()` 看纯 Python 那条路（设备串两个偏移、长度、可重放）
是否还成立 —— 它与签名服务无关，是本地兜底能力的体检。

## 仓库里放什么 / 不放什么

- 放：本文（接口）、`core/uskey.py`（客户端）、`core/uskey_local.py`（纯 Python
  实现 + KAT 自检）、`deploy/qimei-gen/qimei-gen.service.example`（启动模板）。
- 不放：设备串真值、SDK 二进制、`/opt/...` 这类部署路径、真实出口地址与账号 id。
  KAT 需要的真机夹具用 `MARVIS_KAT_QIMEI36` 从 CI secret 注入。
