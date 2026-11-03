/* 模型使用统计：按模型查看当日账号可用性 / 用量 / 日额度估算。 */
let _modelPoolList = [], _modelPoolCurrent = "";
const MODEL_POOL_LIMIT_KINDS = {model_rate: "短时限流", model_queue: "上游排队", model_block: "模型不可用"};
async function loadModelPoolDetail(model) {
  const target = model || _modelPoolCurrent;
  if (!target) return;
  _modelPoolCurrent = target;
  const note = $("#modelPoolNote"); if (note) note.textContent = "加载中…";
  try {
    const {ok, data} = await safeJson(await api("/api/stats/model-pool?model=" + encodeURIComponent(target)));
    if (!ok) throw new Error(typeof data.detail === "string" ? data.detail : "加载失败");
    renderModelPoolDetail(data);
    if (note) note.textContent = data.note || "";
  } catch (error) {
    const rows = $("#modelPoolRows"), stats = $("#modelPoolStats");
    if (rows) rows.innerHTML = `<tr><td colspan="8" class="text-center py-4" style="color:var(--fg-subtle)">加载失败：${esc(error.message)}</td></tr>`;
    if (stats) stats.textContent = "";
    if (note) note.textContent = "";
  }
}
async function loadModelPoolList(preserve) {
  try {
    const {ok, data} = await safeJson(await api("/api/stats/model-pool"));
    if (!ok) return;
    _modelPoolList = data.models || [];
    const host = $("#modelPoolSelectHost"); if (!host) return;
    const current = preserve && _modelPoolCurrent ? _modelPoolCurrent
      : (_modelPoolList.length
          ? _modelPoolList.reduce((a, b) => (b.tokens_today || 0) > (a.tokens_today || 0) ? b : a).model
          : "");
    host.innerHTML = uiSelectHTML("modelPoolSelect", {
      placeholder: "选择模型",
      options: _modelPoolList.map(m => ({
        value: m.model,
        label: `${m.model}${m.enabled ? "" : "（未启用）"} · 今日 ${fmtNum(m.tokens_today)} Token${m.exhausted_accounts ? ` · ${m.exhausted_accounts} 个打满` : ""}`
      })),
      value: current,
      onChange: value => loadModelPoolDetail(value)
    });
    initUISelects([["modelPoolSelect", "模型"]]);
    if (current && current !== _modelPoolCurrent) await loadModelPoolDetail(current);
  } catch (error) {
    const note = $("#modelPoolNote");
    if (note) note.textContent = "模型列表加载失败：" + (error && error.message ? error.message : error);
  }
}
function _modelPoolBar(entry, quota) {
  if (!quota) return `<div class="mp-bar"><span style="width:0%"></span></div>`;
  const pct = Math.min(100, Math.round(entry.used_24h_tokens / quota * 100));
  const tone = entry.daily_exhausted ? "var(--danger,#e54545)" : (pct >= 80 ? "var(--warn,#d97706)" : "var(--brand)");
  return `<div class="mp-bar" data-tooltip="近 24h 已用 ${fmtNum(entry.used_24h_tokens)} / 估算额度 ${fmtNum(quota)}（滚动 24h 口径）"><span style="width:${pct}%;background:${tone}"></span></div>`;
}
function renderModelPoolDetail(data) {
  const s = data.summary || {}, quota = s.est_per_account_quota;
  const fmtQuota = v => v == null ? "未知" : fmtNum(v);
  $("#modelPoolStats").innerHTML = [
    statCard("可用账号", `${s.usable_accounts ?? 0}`, { icon:"fa-users", iconColor:"text-emerald-600",
      sub: `共 ${s.total_accounts ?? 0} 个 · 日耗尽 ${s.exhausted_accounts ?? 0} · 限流 ${s.model_limited_accounts ?? 0} · 冷却 ${s.cooling_accounts ?? 0} · 停用 ${s.inactive_accounts ?? 0}` }),
    statCard("今日已用 Token", fmtNum(s.tokens_today ?? 0), { icon:"fa-fire", iconColor:"text-orange-500",
      sub: `成功请求 ${fmtNum(s.requests_today ?? 0)} 次 · 北京时间 00:00 起${s.today_elapsed_hours != null ? `（已 ${Number(s.today_elapsed_hours).toFixed(1)} 小时）` : ""}` }),
    statCard("近 24 小时（滚动）", fmtNum(s.rolling_24h ? s.rolling_24h.tokens : 0),
      { icon:"fa-clock-rotate-left", iconColor:"text-indigo-500",
        sub: `成功请求 ${fmtNum(s.rolling_24h ? s.rolling_24h.requests : 0)} 次 · 与「使用记录→近 1 天」同口径` }),
    statCard("单账号额度（估·滚动 24h）", fmtQuota(quota), { icon:"fa-gauge-high", iconColor:"text-sky-500",
      sub: quota == null ? "还没有账号打满，无法推断" : `按 ${s.quota_samples} 个已打满账号·打满时刻回看 24h 的实测均值` }),
    statCard("剩余总额度（估）", s.est_total_remaining == null ? "未知" : fmtNum(s.est_total_remaining),
      { icon:"fa-battery-half", iconColor:"text-emerald-600", tone:"brand",
        sub: s.est_pool_capacity == null ? "选出模型后按可用账号估算"
            : `可用 ${s.usable_accounts} 个 × 估算额度 ${fmtNum(quota)} = ${fmtNum(s.est_pool_capacity)}（剩余 = 额度 − 近 24h 已用）` })
  ].join("");
  const rows = (data.accounts || []).slice().sort((a, b) =>
    (b.usable - a.usable) || (b.daily_exhausted - a.daily_exhausted) ||
    (b.used_24h_tokens - a.used_24h_tokens) || (a.id - b.id));
  $("#modelPoolRows").innerHTML = rows.map(entry => {
    const state = entry.daily_exhausted
      ? `<span class="badge bg-rose-100 text-rose-700" data-tooltip="6004/6008 日额度负缓存${entry.daily_reset_at ?" · " + esc(fmtTime(entry.daily_reset_at)) + " 恢复" : ""}">日额度耗尽</span>`
      : entry.limit_kind ? `<span class="badge bg-amber-100 text-amber-700">${esc(MODEL_POOL_LIMIT_KINDS[entry.limit_kind] || entry.limit_kind)}</span>`
      : entry.usable ? `<span class="badge bg-emerald-100 text-emerald-700">可用</span>`
      : entry.status !== "active" ? `<span class="badge bg-slate-200 text-slate-600">已停用</span>`
      : entry.cooling ? `<span class="badge bg-amber-100 text-amber-700">账号冷却中</span>`
      : `<span class="badge bg-amber-100 text-amber-700">暂不可选</span>`;
    return `<tr>
      <td><span class="font-medium">${esc(entry.name)}</span>${entry.uid ? `<span class="text-xs ml-1" style="color:var(--fg-subtle)">${esc(entry.uid)}</span>` : ""}</td>
      <td>${state}</td>
      <td class="text-right font-mono">${fmtNum(entry.used_24h_tokens)}</td>
      <td>${_modelPoolBar(entry, quota)}</td>
      <td class="text-right font-mono" data-tooltip="今日自然日 ${fmtNum(entry.requests_today)} 次">${fmtNum(entry.requests_24h)}</td>
      <td class="text-right font-mono">${entry.est_remaining == null ? '<span style="color:var(--fg-subtle)">—</span>' : fmtNum(entry.est_remaining)}</td>
      <td class="text-right font-mono">${fmtNum(entry.balance_remain)}</td>
      <td class="text-xs">${entry.daily_reset_at ? esc(fmtTime(entry.daily_reset_at)) : '<span style="color:var(--fg-subtle)">—</span>'}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="8" class="text-center py-4" style="color:var(--fg-subtle)">没有账号记录</td></tr>`;
}
document.addEventListener("DOMContentLoaded", () => {
  const panel = $("#modelPoolPanel");
  if (!panel) return;
  panel.addEventListener("toggle", () => {
    if (panel.open && !_modelPoolList.length) loadModelPoolList(false);
  });
});
