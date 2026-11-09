/* ==========================================================================
   平台「平均级」排行（平台管理卡片右侧徽标）
   --------------------------------------------------------------------------
   ## 这是什么

   用户主观评估的四个档位，**只描述"用起来值不值"，不代表功能对错**：

       夯  >  人上人  >  npc  >  拉完了

   ## 数据来源有两条，优先级明确

   1. **平台自报**（首选）：`PlatformSpec.rating = {"tier":…, "reason":…}`
      → 由 `/api/platforms` 的 `rating` 字段下发。
      这是网关对自己上游的真实体感，只有平台实现最清楚；改价、改排队策略时
      跟着平台代码一起走，不会出现「前端还写着旧评价」。

   2. **前端兜底表**（本文件 `TIERS`）：平台还没声明 `rating` 时用它。
      纯粹是过渡用 —— 新平台忘记声明时不至于整张卡片没有徽标。

   两边都没有 → 不显示徽标（不做"猜一个"）。

   ## 后续可自动化

   `rating` 现在是人工填写。将来平台侧可以按**真实指标**算出来
   （成功率 / 首字节延迟 / 排队命中次数 / 每千 token 消耗），
   只要仍然吐出 `{tier, reason, scores}` 这个形状，
   前端与 `/api/platforms` 契约一行都不用改。
   ========================================================================== */

(function () {
  "use strict";

  /* 档位从高到低。`rank` 用于将来按等级排序；CSS 用 `tier` 选配色。 */
  var TIER_ORDER = [
    { key: "hang", label: "夯", rank: 4 },
    { key: "elite", label: "人上人", rank: 3 },
    { key: "npc", label: "npc", rank: 2 },
    { key: "bad", label: "拉完了", rank: 1 },
  ];

  function metaOf(tierKey) {
    for (var i = 0; i < TIER_ORDER.length; i++) {
      if (TIER_ORDER[i].key === tierKey) return TIER_ORDER[i];
    }
    return null;
  }

  function baseKey(storage) {
    return String(storage || "").toLowerCase().replace(/-(cn|intl)$/, "");
  }

  /* 兜底表：平台没在 PlatformSpec.rating 里声明时用。
     key 用落库平台名（trae-cn / trae-intl 共用一份）。 */
  var TIERS = {
    workbuddy: {
      tier: "hang",
      reason: "模型调用稳定、免排队，调用速度快；每月都有免费模型与低倍率模型，"
        + "新用户最高可免费领 4000 积分。",
    },
    trae: {
      tier: "npc",
      reason: "可能需要排队，额度给得不多，速度较慢。",
    },
    qoder: {
      tier: "bad",
      reason: "需要排队，额度不多，而且巨慢。",
    },
    loomy: {
      tier: "hang",
      reason: "稳定模型速度最快，新用户给的积分很足，每天签到还能领 5000 积分；"
        + "美中不足是积分用得偏快。",
    },
    raccoon: {
      tier: "elite",
      reason: "不算很稳定，时快时慢，但给的量还行、够用。",
    },
    codearts: {
      tier: "npc",
      reason: "保活续签比较麻烦，额度不算多但够用，速度不快。",
    },
    minimax: {
      tier: "elite",
      reason: "只有自家模型，额度消耗不快，速度还行。",
    },
    "wps-lingxi": {
      tier: "npc",
      reason: "模型给得多，但每个量的额度不大。",
    },
  };

  /**
   * 取一个平台的等级。
   *
   * @param {string} storage 落库平台名（`trae` / `qoder` / `loomy`…）
   * @param {object} [spec]  `/api/platforms` 的单项；有 `rating` 就以它为准
   * @returns {{key,label,rank,reason,source}|null}
   */
  function tierOf(storage, spec) {
    // 1) 平台自报
    var self = spec && spec.rating;
    if (self && typeof self === "object") {
      var meta = metaOf(String(self.tier || "").toLowerCase());
      if (meta) {
        return {
          key: meta.key,
          label: meta.label,
          rank: meta.rank,
          reason: String(self.reason || ""),
          source: String(self.source || "platform"),
        };
      }
    }
    // 2) 前端兜底表
    var key = baseKey(storage);
    var entry = TIERS[key];
    if (!entry) return null;
    meta = metaOf(entry.tier);
    if (!meta) return null;
    return {
      key: meta.key,
      label: meta.label,
      rank: meta.rank,
      reason: entry.reason || "",
      source: "fallback",
    };
  }

  window.WBPlatformTiers = {
    tierOf: tierOf,
    order: TIER_ORDER,
    table: TIERS,
  };
})();