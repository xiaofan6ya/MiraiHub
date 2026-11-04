# 并发与容量：实测出来的默认值

> 这份文档回答两个问题：**每个平台到底能扛多少并发**，以及**网关的三层闸门
> 该设多少**。所有数字都是 2026-11-04 用真实账号打上游测出来的，
> 不是拍脑袋，也不是"官方文档说"。

## 0. 三层闸门，别混为一谈

| 层 | 配置 | 作用 | 调大后的风险 |
|---|---|---|---|
| **Key 级** | `ADMIN_GATEWAY_MAX_CONCURRENT`（默认 24）<br>`ADMIN_GATEWAY_RATE_PER_MINUTE`（默认 300） | 拦住单个 Key 的并发/频率 | 一个 Key 就能把整池打满 |
| **账号级** | `ADMIN_POOL_MAX_IN_FLIGHT`（默认 3）<br>`accounts.max_in_flight`（按号覆盖） | 真正的背压：单号同时在途 | 上游限流 / 风控 |
| **上游会话级** | CodeArts `session_window`（2/55s） | 上游按**账号**计会话，请求结束还滞留 | 直接 400 `TM.00001041` |

排障顺序固定：**先看是哪一层在排队**。Key 级排队表现为「TTFT 匀速增长、
TPS 不变」；账号级表现为「个别号慢、换号就好」；会话级是明确的 400。

## 1. 实测：逐档加压（单 Key，放开 Key 级限制后）

| 平台 | 并发 | 成功率 | TTFT p50 | TPS p50 | 结论 |
|---|---|---|---|---|---|
| WorkBuddy (24 号) | 1 | 100% | 4.6s | 46.9 | 基线 |
| | 16 | 100% | 6.8s | 50.3 | 几乎无衰减 |
| | 32 | 100% | 7.0s | 49.7 | **可以拉满** |
| Loomy (101 号) | 1 | 100% | 1.6s | 162.8 | 基线（最快） |
| | 8 | 100% | 5.9s | 148.1 | 拐点附近 |
| | 16 | 100% | 16.2s | 174.2 | 已明显退化 |
| | 32 | 100% | 21.7s | 46.8 | 退化 13 倍，不建议 |
| Qoder (2 号) | 1 | 100% | 1.05s | 174.8 | 基线 |
| | 2 | 100% | 1.15s | 192.6 | 无衰减 |
| Trae (3 号) | 1 | 100% | 30.6s | 74.1 | 模型本身慢思考 |
| | 3 | 100% | 26.3s | 75.4 | 并发不影响首字 |
| Raccoon (1 号) | 1 | 100% | 5.5s | — | 样本不足 |
| | 2 | 100% | 3.4s | 87.0 | 可接受 |
| CodeArts (1 号) | 5 | 3 成功 + 2 排队 | 3.4s / 57s | 150~187 | 受会话窗口限制 |

> ⚠️ **Loomy 的退化不在网关**：把单号在途压到 1（101 个号各跑一路）后
> 16 并发仍然是 16.2s，说明是上游按 IP/用户维度限速，不是我们挤在几个号上。
> 这类平台只能靠"别一次发太多"，加号没用。

## 2. 推荐默认值（已落到代码 / 账号）

| 位置 | 值 | 依据 |
|---|---|---|
| `ADMIN_GATEWAY_MAX_CONCURRENT` | **24** | WorkBuddy 32 并发无衰减，留 25% 余量 |
| `ADMIN_GATEWAY_RATE_PER_MINUTE` | **300** | 60 会让 16 并发匀速放行（1 秒 1 个），agent 工具循环直接卡死 |
| `ADMIN_POOL_MAX_IN_FLIGHT`（全局） | 3 | WorkBuddy/Qoder/Trae 实测正常 |
| Loomy 账号 `max_in_flight` | **1** | 3 路时 16 并发 TTFT 21.9s；1 路时 16.2s 且 TPS 更高 |
| CodeArts 账号 `max_in_flight` | **2** | 与上游「3 会话」窗口对齐，留 1 个余量 |
| Raccoon 账号 `max_in_flight` | **2** | 单号，实测 2 路正常 |
| CodeArts 会话窗口 | 2 / 55s | 见 `CASE-CODEARTS.md §9.3` |

> 账号级覆盖在控制台「账号 → 并发」里可改，也可以直接写
> `accounts.max_in_flight`（NULL = 跟随全局）。

## 3. 怎么复测（别靠感觉）

```bash
# 放开 Key 级闸门，否则测到的是排队时间不是上游速度
py -3.10 _scratch/platform_bench.py --platforms loomy \
    --levels 1,4,8,16 --raise-key-limit 64 --rate-per-minute 6000
```

脚本做的事：

1. 用 `--raise-key-limit/--rate-per-minute` 临时改 Key 限制（结束自动恢复）；
2. 每档起 N 个线程同时打 `/v1/chat/completions`（模型名带 `-平台` 后缀）；
3. 记录 **首个 token（含 reasoning）** 到 **最后一个 token** 的窗口，
   算 TPS；解码段 < 300ms 的突发样本不计（与 `bench_router` 同口径）。

判据：

- [ ] 相邻档位 TTFT 涨幅 < 50% → 这一档还能加；
- [ ] TPS 不随并发下降（±20% 内）→ 上游没被压垮；
- [ ] 成功率 100%（除零余额号 402）→ 闸门在上游限流**之前**生效。

## 4. 一个必须记住的陷阱

**默认 `ADMIN_GATEWAY_RATE_PER_MINUTE=60` 会伪装成"上游慢"。**
16 个请求同时到达时，准入层按 60/分钟匀速放行 ≈ 1 秒一个，
TTFT 就从 1.6s 涨到 18s，而 TPS 完全正常 —— 看指标会以为是上游排队，
实际是自己限的。判断方法：把 Key 的 `rate_per_minute` 临时调大再测一次，
TTFT 掉下来就是它。

## 5. TTFT / TPS 的口径（和面板一致）

```
TTFT = 首个 token（content / reasoning / tool_calls 任一）到达
TPS  = completion_tokens / (最后一个 token - 第一个 token)
```

**思考链也算 token。** 早期口径是「TTFT 到第一个正文」，对 DeepSeek-V4.1
这类"先思考 9 秒、正文 150ms 吐完"的模型会得到
`ttfb 9.33s / latency 9.49s` → 解码段 160ms → TPS 永远"量不出来"，
而 `completion_tokens` 里明明有上千个 reasoning token。
现在统一按「首个 token」起算，分母与分子同口径。
