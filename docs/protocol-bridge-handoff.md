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


## 2026-11-04 增补（大池列表/导出/导入/UI）

- **服务端分页已全面接入**：`admin/platform/shared.py` 的 `list_page_params / apply_account_filters / account_order / summarize_accounts_sql` 是四平台统一的分页基建；无 `page` 参数=旧全量行为（Key 表单等旧调用方兼容）。summary 走 SQL 聚合，**tab 计数不含 status 筛选**（显示全池分布）。验证入口：`python -m pytest tests/test_list_pagination.py`（8 用例，内存 SQLite + TestClient）。
- 前端统一状态 `window._accListState` + `accListQS/accSearchInput/accListReload/accPageTo/accSelectedIds/withPick`（console-app.js）；行勾选列用 `withPick(rowHtml,id)` 注入，不动各平台行构造器。
- 导出分片：`/api/accounts/export-async` 新增 `offset`（与 limit 组合成第 N 片）；前端导出弹窗 scope=selected（读勾选）/slice。
- **trae SOLO 工具槽字段必须一次给全**（`{"_n":n}` 惰性初始化曾致 100% KeyError 断流，f324a5e 修复）；`_solo_open_tool` 开块后不得立即 stop/done（参数多帧增量），统一 finish 段关闭。冒烟套件：`python -I tests/test_platform_smoke.py`（9 用例）。
- **真实链路冒烟**：`_scratch/real_smoke.py`（不入 git）——真实 DB 凭证→真实上游，loomy 工具调用/协议完整性、trae/qoder 流式全过。注意：trae token 在 auth_json 加密层，必须走 `trae_pool.credentials()` 解密，直接 `client.token_of(account)` 拿到的是空。
- UI：确认框图标 key 无 `fa-` 前缀；遮罩统一 rgba(0,0,0,.45~.55)（用户否决 slate 蓝灰）；docs/protocol-bridge-handoff.md 与 docs/branch-policy.md 已落项目（docs/ 在 gitignore，提交要 -f）。
- **loomy 导入两步化**：前端「扫描→勾选→确认」（platformImportLocal 不再静默 {all:true}）；后端 import-local 支持 uids 过滤，且必须**同时接受完整 uid 与打码 uid**（扫描弹窗只回打码形态）。WorkBuddy 保持原直接导入行为未动。
- growth `_acct_tasks_cache` 已有 TTL+`_ACCT_TASKS_MAX` 上限清理（旧记忆里的「疑无上限」已不成立）。


## 2026-11-04 增补③（小浣熊扫描+扫码授权）

- **小浣熊客户端凭证=明文 auth.json**（PR #9 的 "Electron LevelDB" 结论不成立）：`BOX_AGENT_CONFIG_DIR` 或 `~/.office-raccoon/agent/config/auth.json`，内容 `{access_token: IAM-JWT, office_identity}`；IAM JWT `iss=xiaohuanxiong.com/iam`、`sub=pu-*`（uid）、exp 30 天；是 IAM token 时客户端**不存 refresh_token**。逆向语料：`_scratch/raccoon_client/`（客户端 asar 的 build/electron/main/account+agent 目录）。
- **扫码授权协议**（官方 IAM）：`POST https://xiaohuanxiong.com/api/iam/v1/auth/mp/qrcode` {} → `{qrcode_code, scan_path:"/login/mp", expires_in:300, poll_interval:2}`；轮询 `POST .../auth/mp/qrcode/login` {qrcode_code} → pending/logging/canceled/success(+access_token)；取消 `PUT /auth/mp/qrcode` {qrcode_code,status:"canceled"}。二维码内容=`https://xiaohuanxiong.com/login/mp?code=<qrcode_code>&appname=商汤小浣熊&source=platform`（brand 官方值）。网关实现：`services/webauth.py`（内存会话表）+ `/api/raccoon/auth/begin|poll|qr.svg|cancel`；QR 用后端 `qrcode` 库出 SVG（qrcode.make 的 error_correction 要传 constants.ERROR_CORRECT_M 不是 "M"，to_string() 返回 bytes）。
- 滑块=**阿里云验证码**（AliyunCaptcha.js，chunk 1469 verifyCaptcha）——不可自动化也不需要；短信接口 `POST /api/iam/v1/sms/send` {nation_code,phone,captcha_token,scene:"login"} → verification_id，登录 `POST /api/iam/v1/auth/login/sms`；另有 `POST /auth/login/password/phone|email`（账号密码，无滑块）——均未启用，扫码零摩擦。IAM 信封 `{code:"OK",data}`，base `/api/iam/v1`（登录页 chunk 的 baseUrl 默认值）。
- raccoon PlatformSpec 已改 `supports_local_scan=True`、auth_mode="device-auth"、actions=[scan,import,begin-auth,refresh-quota]；控制台 platformBeginAuth 有 raccoon 扫码分支（qr.svg 弹窗+轮询，弹窗关闭即停）。
- 本地验证 UI 用临时实例：`ADMIN_PORT=8795 python -m uvicorn admin.server:app --port 8795`（cookie 按 127.0.0.1 域共享，8795 免登录），验证完 taskkill。
