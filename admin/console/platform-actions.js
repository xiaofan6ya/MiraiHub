/* ==========================================================================
   平台动作按钮（扫描 / 导入 / 刷新 / 签到 …）
   --------------------------------------------------------------------------
   ## 改造前的问题

   原实现写死了「只有 trae 有按钮」：

       if (storage !== "trae") return [];

   后果：新增的 Qoder / Loomy **完全没有扫描和导入按钮** ——
   用户根本没有入口把账号导进来。而这两个平台恰恰都能读本机凭证
   （Qoder 要 PAT/设备流，Loomy 是明文 JSON 直接可读）。

   ## 新的做法

   按钮由**后端**下发（`/api/platforms` 的 `actions` 字段），
   平台在 `PlatformSpec.extra["actions"]` 里声明自己能做什么：

       extra = {"actions": ["scan", "import", "refresh-quota"]}

   前端只负责渲染。这样：
     * 新增平台：后端声明一行，前端零改动
     * 平台没有某能力：按钮自动不出现（不会点了报 404）
   ========================================================================== */

(function () {
  "use strict";

  /* 按钮图标 */
  var ACTION_ICONS = {
    search:
      '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    download:
      '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 4v12m-4-4 4 4 4-4M4 18h16"/></svg>',
    refresh:
      '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1"/></svg>',
    chart:
      '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 20h16M7 20V10M12 20V4M17 20v-7"/></svg>',
    cookie:
      '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><circle cx="9" cy="9" r="1" fill="currentColor"/><circle cx="15" cy="10" r="1" fill="currentColor"/><circle cx="10" cy="15" r="1" fill="currentColor"/></svg>',
    gift:
      '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="8" width="18" height="13" rx="2"/><path d="M3 12h18M12 8v13M12 8a3 3 0 1 0-3-3c0 2 3 3 3 3zm0 0a3 3 0 1 1 3-3c0 2-3 3-3 3z"/></svg>',
    key:
      '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"><circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3M14 9l3 3"/></svg>',
  };

  /* 动作 -> 展示配置。**只有展示信息，能力由后端决定** */
  var ACTION_META = {
    scan: { label: "扫描本机", icon: "search" },
    import: { label: "导入本机", icon: "download", primary: true },
    "refresh-quota": { label: "刷新额度", icon: "refresh" },
    usage: { label: "用量明细", icon: "chart" },
    // Trae 的 Cookie 只用于「查询用量明细」，不是登录手段 ——
    // 名字里写清楚，避免用户以为要靠贴 Cookie 才能登录。
    "web-cookie": { label: "用量 Cookie", icon: "cookie" },
    checkin: { label: "签到领取", icon: "gift" },
    "begin-auth": { label: "授权登录", icon: "key", primary: true },
  };

  /**
   * 该平台能做哪些动作。
   *
   * 优先读后端下发的 `spec.actions`；没有就按落库平台给一份默认值
   * （兼容还没声明 actions 的平台）。
   */
  function actionsFor(spec) {
    if (!spec) return [];

    var declared = spec.actions;
    if (!Array.isArray(declared) || declared.length === 0) {
      // 兼容层：老平台没声明 actions 时按 storage_platform 给默认
      var storage = (spec.storage_platform || spec.id || "").toLowerCase();
      if (storage === "trae") {
        declared = ["begin-auth", "scan", "import", "refresh-quota",
                    "usage", "web-cookie"];
      } else if (storage === "qoder") {
        // Qoder 凭证加密在原生层：不支持本地导入，只能设备授权
        declared = ["begin-auth", "scan", "refresh-quota", "checkin", "usage"];
      } else if (storage === "loomy") {
        declared = ["scan", "import", "refresh-quota"];
      } else {
        declared = [];
      }
    }

    // 「支持扫描」由后端 `supports_local_scan` 决定：不支持就别把按钮摆出来，
    // 点了必然空结果。但**账号总得有办法加进来** —— 这时至少要有授权登录。
    var supportsScan = spec.supports_local_scan !== false;
    if (!supportsScan) {
      declared = declared.filter(function (name) {
        return name !== "scan" && name !== "import";
      });
      if (declared.indexOf("begin-auth") < 0) {
        declared = ["begin-auth"].concat(declared);
      }
    }

    return declared
      .map(function (name) {
        var meta = ACTION_META[name] || { label: name, icon: "key" };
        return {
          name: name,
          label: meta.label,
          icon: meta.icon,
          primary: Boolean(meta.primary),
        };
      })
      .filter(function (item) {
        return Boolean(item.name);
      });
  }

  /* 暴露给 platform-switch.js 使用 */
  window.WBPlatformActions = {
    actionsFor: actionsFor,
    icons: ACTION_ICONS,
    meta: ACTION_META,
  };
})();
