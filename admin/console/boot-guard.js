/* 启动看门狗（独立文件，不再内联在 index.html 里）。
   要解决的是同一类故障：控制台只抛一条看不懂的
   `Uncaught SyntaxError: Unexpected end of input (at admin?tab=accounts:N)`，
   然后扫描/导入/导出/账号池的按钮点了全都没反应。

   现在 HTML 里已经没有任何内联 <script>，那种"内联块被截断 → 整页 JS 报废"
   的故障模式在结构上不存在了。剩下两种可能都会在这里被翻译成人话：
     1) HTML 本身被截断（末尾的 #wb-page-end 标记不存在）
     2) 某个外部 .js 没加载成功（核心函数缺失）
   放在 <body> 之后尽早执行，页面 load 完成后再判定，避免慢机器误报。 */
(function () {
  function showBanner(message) {
    if (document.getElementById("wbBootWarn")) return;
    var bar = document.createElement("div");
    bar.id = "wbBootWarn";
    bar.setAttribute("role", "alert");
    bar.style.cssText = "position:fixed;left:0;right:0;bottom:0;z-index:99999;padding:12px 16px;"
      + "background:#7f1d1d;color:#fff;font-size:13px;line-height:1.6;"
      + "display:flex;gap:12px;align-items:center;flex-wrap:wrap";
    var text = document.createElement("span");
    text.textContent = message;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "强制刷新";
    btn.style.cssText = "padding:6px 14px;border-radius:8px;background:#fff;color:#7f1d1d;"
      + "font-weight:600;border:0;cursor:pointer";
    btn.onclick = function () { location.reload(); };
    bar.appendChild(text);
    bar.appendChild(btn);
    (document.body || document.documentElement).appendChild(bar);
  }

  function check() {
    if (document.readyState !== "complete") { setTimeout(check, 800); return; }

    // 1) HTML 是否完整收到：末尾标记缺失 = 传输被截断
    if (!document.getElementById("wb-page-end")) {
      showBanner("页面没有完整下载（HTML 末尾缺失），部分功能可能不可用。"
        + "点「强制刷新」重新加载；若反复出现，检查代理/加速器是否在截断响应。");
      return;
    }

    // 2) 核心脚本是否都到位
    var need = ["openPhoneLogin", "openUpload", "openExport", "scanLocal", "loadAccounts"];
    var miss = need.filter(function (n) { return typeof window[n] !== "function"; });
    if (miss.length) {
      showBanner("控制台脚本没有加载完整（缺少 " + miss.join("、") + "），所以按钮点了没反应。"
        + "点「强制刷新」重试；若反复出现，说明有脚本文件请求失败。");
    }
  }

  setTimeout(check, 2000);
})();
