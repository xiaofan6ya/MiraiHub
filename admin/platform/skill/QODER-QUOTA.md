# Qoder 额度与签到：双桶 + 活动制

Qoder 的额度和签到都不直观，两个坑都会让数字显示成 0 或漏掉。

---

## 额度：两个桶，都要读

```
GET {open_api}/api/v2/quota/usage
```

实测响应（2026-10-29）：

```json
{
  "userId": "01a18092-...",
  "userType": "personal_professional_trial",
  "usageType": "credits",
  "userQuota":  {"total": 300, "used": 1, "remaining": 299, "unit": "credits"},
  "addOnQuota": {"total": 100, "used": 0, "remaining": 100, "unit": "credits"},
  "expiresAt": 1794368045643,
  "isQuotaExceeded": false,
  "totalUsagePercentage": 0.01
}
```

| 桶 | 含义 |
|---|---|
| `userQuota` | **套餐额度**（Pro Trial 300、Free 0） |
| `addOnQuota` | **签到/资源包**（每日 100 Credits 落这里） |

**可用总额 = 两个 `remaining` 之和。**

### 坑：只读 `userQuota`

签到领了 100 Credits，但 `userQuota.remaining` 不变
（签到落进 `addOnQuota`）。只读第一个桶 → 用户觉得「签到没生效」。

实测：

```
读双桶:  399 = 299（套餐）+ 100（签到）  ✓
只读第一个: 299                            ✗ 少了签到那 100
```

### 三个字段名容易错

| 用途 | 字段 | 备注 |
|---|---|---|
| 套餐额度总额 | `userQuota.total` | |
| 资源包总额 | `addOnQuota.total` | 组织包那边叫 `cap` |
| 过期时间 | `expiresAt`（毫秒） | 不是 `expireAt` |

---

## 签到：活动制，不是 daily-check-in

```
GET  {open_api}/sash/api/v1/me/campaigns?forceRefresh=true
  -> {uid, showCampaign, claimable,
      campaigns:[{campaignId, campaignKey, actionType, claimStatus,
                  startAt, endAt,
                  benefit:{kind:"CREDITS", amount:100,
                           validity:{mode:"RELATIVE_DAYS", days:30}}}]}

POST {open_api}/sash/api/v1/me/campaigns/{campaignId}/claim   body {}
  -> {status:"CLAIMED", replayed, benefit:{...},
      claimedAt, grantedAt, expiresAt}
```

### 三个必须遵守的判据

**① 顶层 `claimable` 不能当判据**

领完之后它**仍可能是 `true`**（同账号还有别的可领促销活动）。
必须按 `actionType` 逐行筛：

```python
rows = [c for c in campaigns["campaigns"]
        if c.get("actionType") == "CLAIM_BENEFIT"]
```

促销活动的 `actionType` 是 `VIEW_DETAILS` —— **不能领**。

**② 领完活动行不消失**

`claimStatus` 从 `CLAIMABLE` 变 `CLAIMED`。所以：

* 「今天已领过没」→ 直接读活动列表，**不需要本地记账**
* `claimable_of()` 只返回 `CLAIMABLE` 的行
* 判断「已领」用 `campaigns_of()` 看 `claimed` 字段

**③ legacy `daily-check-in` 已停用，一次都别碰**

```
GET /sash/api/v1/me/daily-check-in/status   -> status 恒为 "DISABLED"
                                             （国际版直接 404）
```

它的 claim 兄弟端点对未领取的日子也回 409 且不发积分。

---

## 国际版需要风控头（最难的一条）

**不带头时，活动列表里永远只有 `VIEW_DETAILS` 促销**，
看起来像「国际版没有签到」——实际是缺头。

需要的头（由本机客户端的 UMID 组件生成）：

```
Cosy-MachineToken
Cosy-MachineCode
Cosy-MachineType
```

**顺序必须是**：先拉一次拿 `uid`（在活动列表的**顶层**）→
叠风控头重拉 → 再领。因为风控身份按 uid 生成。

### 没有 UMID 组件时

给**中性说明**（缺什么、怎么补），不要报失败。
「没得领」和「接口坏了」是两件事。

---

## 每日 100 Credits 的时间窗口

实测（中国版 Pro Trial）：

```
startAt = 1790474400   -> 2026-09-27 10:00 (UTC+8)
endAt   = 1790560740   -> 2026-09-28 09:59 (UTC+8)
```

与官方客户端「每天 10:00 刷新」一致。
**不是 00:00** —— 定时任务要排到 10 点之后。

---

## 套餐名

```
GET {open_api}/api/v2/user/plan
  -> {"user_type": "personal_professional_trial",
      "plan_tier_name": "Pro Trial",
      "is_paid_plan": false, ...}
```

套餐名单独一次请求，失败不影响额度。

---

## 检查清单

```
[ ] usage 读双桶（userQuota + addOnQuota），可用 = 两者 remaining 之和
[ ] expiresAt 是毫秒时间戳，不是 expireAt
[ ] 签到按 actionType == CLAIM_BENEFIT 筛，不看顶层 claimable
[ ] claimable_of 只返回 CLAIMABLE；已领用 campaigns_of 看 claimed
[ ] 不碰 legacy daily-check-in
[ ] 国际版风控头（Cosy-Machine*）—— 先拉 uid 再叠头
[ ] 「没有活动」返回中性说明，不报失败
[ ] 签到后刷一次额度（否则 addOnQuota 不更新）
[ ] 定时任务排在每天 10:00 之后
```

---

## 实测结果（两个地区）

```
qoder-cn-main  [cn]    Pro Trial   可用 399 = 299（套餐）+ 100（签到）
2a466032-...   [intl]  Free        可用 100 =   0（套餐）+ 100（签到）
```

两个账号的签到活动都是 `CLAIMED`（当日已领）。
