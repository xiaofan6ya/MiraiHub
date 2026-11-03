---
name: wb2api-protocol-bridge
description: 聚合网关三协议工具调用统一走 admin/platform/protocol_bridge.py；with_pings
  心跳设计约束；mock 测试入口；全仓 pytest 有 114 存量失败
metadata:
  node_type: memory
  type: project
  originSessionId: sess_c32bac91-2c6e-4eba-b8d3-9eaa8aa4459e
---

2026-11-04 起聚合网关三端点（/v1/chat/completions、/v1/messages、/v1/responses）的协议转换统一收敛到 `admin/platform/protocol_bridge.py`，loomy/raccoon/qoder 全部接入，trae 非 SOLO 路径本就完整（core/protocol.py）、SOLO 分支内联修复。

关键约束（改动前必读）：
- 桥的流式中继要求内层 StreamingResponse 产 **OpenAI chat.completion.chunk SSE**；桥负责转 Anthropic/Responses。
- `with_pings` 心跳必须在**源**注入（读线程+队列），禁止在中继用 `asyncio.wait_for(__anext__)` 定时——超时取消会双线程并发 next() 撕裂流。
- loomy 上游归一层（`normalize_messages`）故意把 role=tool 转成 `[tool result for <id>]` user 文本、assistant.tool_calls 转 JSON 块——这是现场实测约束，别改回透传；但 `content:null` 的 assistant 工具调用消息不能丢（已修）。
- loomy 归因头：ChatId/MsgId/TurnId/X-Loomy-Request-Purpose/InvokeOrigin + 双 token 头 + traceparent（缺 traceparent 上游**挂死不报错**）。
- 积分链路：流式必须注入 `stream_options.include_usage`；loomy credit 优先取 `usage.points_consumed`；`/v1/balance` 走 `/points/balance` 的 availableBalance（træ/qoder balance 本来就是真数据）。
- 测试入口：`python -I tests/test_protocol_bridge.py`（桥 6 用例）、`python -I tests/test_loomy_endtoend.py`（mock 上游端到端，用 ADMIN_LOOMY_BASE_URL 注入 mock）。Loomy 客户端逆向语料在 `_scratch/loomy_client/`（1145 文件，协议要点见[[wb2api-branch-policy]]同期的对话）。
- **全仓 pytest 有 ~114 失败 + 9 错误的历史存量**（wb_login/console 移动端/upstream_upload/perf_guardrails/trae sqlalchemy 等），与网关无关；评估改动用 `git checkout <基线> && pytest tests/ --ignore=tests/test_gateway_hardening.py -rf` 对比失败集合（tests/ 大多未入 git，worktree 里没有）。
- tests/ 目录在 .gitignore 里，新测试文件要 `git add -f`。
