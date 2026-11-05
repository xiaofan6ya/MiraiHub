/* 启动看门狗 + 注入检测（独立文件，index.html 里没有任何内联 <script>）。
   要解决的是同一类故障：控制台只抛一条
   `Uncaught SyntaxError: Unexpected end of input (at admin?tab=accounts:N)`，
   然后按钮整片失灵。

   本文件从"页面自身结构"入手判断三种情况：
     1) HTML 被截断        —— 末尾的 #wb-page-end 标记不存在
     2) 页面被外部注入脚本 —— 我们的页面 0 个内联脚本，DOM 里只要出现
                              无 src 且内容非空的 <script>，就一定是别人加的
     3) 核心脚本没加载到   —— 函数缺失
   全部输出到页面上（红/黄条）和 console，便于一眼定位到底是谁的问题。
   放在 <body> 之后尽早执行，load 完成后再判定，避免慢机器误报。 */
(function () {
  var BUILD = "20261103-actions1";

  function bar(color, message, detail) {
    if (document.getElementById("wbBootWarn")) return;
    var host = document.createElement("div");
    host.id = "wbBootWarn";
    host.setAttribute("role", "alert");
    host.style.cssText = "position:fixed;left:0;right:0;bottom:0;z-index:99999;padding:12px 16px;"
      + "background:" + color + ";color:#fff;font-size:13px;line-height:1.6;"
      + "display:flex;gap:12px;align-items:center;flex-wrap:wrap;pointer-events:none";

    var text = document.createElement("span");
    text.textContent = message;
    if (detail) {
      var pre = document.createElement("code");
      pre.style.cssText = "display:block;margin-top:6px;font-size:11px;opacity:.85;"
        + "white-space:pre-wrap;word-break:break-all;max-height:120px;overflow:auto";
      pre.textContent = detail;
      text.appendChild(pre);
    }

    var btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "强制刷新";
    btn.style.cssText = "padding:6px 14px;border-radius:8px;background:#fff;color:#1f2937;"
      + "font-weight:600;border:0;cursor:pointer;flex:0 0 auto;pointer-events:auto";
    btn.onclick = function () { location.reload(); };

    host.appendChild(text);
    host.appendChild(btn);
    (document.body || document.documentElement).appendChild(host);
  }

  function describe(el) {
    if (!el) return "?";
    var s = "<" + (el.tagName || "?").toLowerCase();
    if (el.id) s += " id=" + el.id;
    if (el.src) s += " src=" + el.src;
    if (el.type) s += " type=" + el.type;
    s += ">";
    var t = (el.textContent || "").replace(/\s+/g, " ").trim();
    if (t) s += " " + t.slice(0, 90);
    return s;
  }

  function check() {
    if (document.readyState !== "complete") { setTimeout(check, 800); return; }

    var end = document.getElementById("wb-page-end");

    // ---- 1) 页面里出现"无 src 的内联脚本" = 被外部注入（我们自己是 0 个）----
    // 注意：不能用「内容非空」过滤 —— 注入的脚本如果是未闭合的 `<script>`，
    // 它的 textContent 恰好是空的，那才是报 Unexpected end of input 的那一段。
    var inline = Array.prototype.filter.call(document.scripts || [], function (s) {
      return !s.src;
    });
    if (inline.length) {
      var lines = inline.map(function (s, i) {
        return (i + 1) + ") " + (s.textContent || "").replace(/\s+/g, " ").trim().slice(0, 160);
      }).join("\n");
      console.error("[WB] 页面被注入 " + inline.length + " 段内联脚本：\n" + lines);
      bar("#92400e",
        "检测到有外部程序往控制台页面注入了 " + inline.length + " 段脚本（我们自己的页面不含任何内联脚本）。"
        + "那条 Unexpected end of input 就来自它，功能不受影响；可在浏览器扩展或安全软件/加速器里关掉「网页注入/脚本注入」后消失。",
        lines);
      return;
    }

    // ---- 2) HTML 被截断：末尾标记缺失 ----
    if (!end) {
      var tail = Array.prototype.slice.call(document.body ? document.body.children : [], -3)
        .map(describe).join("\n");
      console.error("[WB] 页面不完整，#wb-page-end 标记缺失。body 最后几个元素：\n" + tail);
      bar("#7f1d1d",
        "页面没有完整下载（HTML 末尾标记缺失），部分内容可能不可用。点「强制刷新」重新加载；"
        + "若反复出现，检查代理/加速器是否在截断响应。", tail);
      return;
    }

    // ---- 3) 末尾标记之后还有 <script> = 疑似注入（页面自己的日期控件等 div 不算）----
    var extra = [];
    for (var n = end.nextElementSibling; n; n = n.nextElementSibling) {
      if ((n.tagName || "").toLowerCase() === "script") extra.push(n);
    }
    if (extra.length) {
      var d = extra.map(describe).join("\n");
      console.warn("[WB] #wb-page-end 之后出现 " + extra.length + " 个 script：" + d);
    }

    // ---- 4) 核心脚本是否都到位 ----
    // `scanLocal` 是桌面专属：Android 构建会删掉整个实现，并把按钮改派给
    // `WBPlatform.action('scan')`（那里对移动端有友好提示）。所以只有页面上
    // 仍保留 `onclick="scanLocal(...)"` 的按钮（= 桌面构建）时才把它列为必需，
    // 否则移动端每次都误报「缺少 scanLocal」。
    var need = ["openPhoneLogin", "openUpload", "openExport", "loadAccounts"];
    if (document.querySelector('[onclick^="scanLocal("]')) need.push("scanLocal");
    var miss = need.filter(function (k) { return typeof window[k] !== "function"; });
    if (miss.length) {
      console.error("[WB] 核心脚本未加载，缺少：" + miss.join(", "));
      bar("#7f1d1d",
        "控制台脚本没有加载完整（缺少 " + miss.join("、") + "），所以按钮点了没反应。"
        + "点「强制刷新」重试；若反复出现，说明有脚本文件请求失败。", null);
    } else {
      console.info("[WB] 控制台构建 " + BUILD + " 核心脚本已加载；按钮运行时错误请查看具体报错或复制诊断信息");
    }
  }

  setTimeout(check, 2000);
})();
