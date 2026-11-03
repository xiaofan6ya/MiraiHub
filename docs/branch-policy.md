---
name: wb2api-branch-policy
description: MiraiHub 分支策略——PR/新功能合并到自建 dev-test 分支，绝不直接进 main，也不碰 develop
metadata:
  node_type: memory
  type: feedback
  originSessionId: sess_c32bac91-2c6e-4eba-b8d3-9eaa8aa4459e
---

用户明确要求（2026-11-03）：对外部 PR（如 #9 raccoon）审查后**不要合并进 main**，也不要用远端 `develop` 分支（develop 被 PR #11 的 React/Vite 前端重构占据，与用户的 admin/console 框架完全不同构，禁止采用）。

**Why:** main 是发布线，develop 是别人的前端框架；用户的控制台是 admin/console（index.html + console-app.js 等原生 JS）。混入会导致框架冲突。

**How to apply:** 新建自维护分支 `dev-test`（origin/dev-test，基点含本地 main 最新提交链 + PR 合并），所有聚合网关协议修复、PR 合并都提交到 dev-test；origin/main 已强制回退到 fba4f81 并保持不动。后续 dev-test 验证通过后再由用户决定何时进 main。
