/* 统一的安全请求入口（必须**最早加载**，早于任何业务脚本）。
 *
 * 为什么要在 fetch 上做一层，而不是逐个改调用点：
 *   控制台里有 200+ 处 fetch，散落在 console-app.js / platform-switch.js /
 *   agent.js / ip-management.js / key-ip.js / review-improvements.js …
 *   一处漏改就是一个 CSRF 缺口，而漏改在测试里几乎发现不了。
 *   把策略收在唯一出口，新增调用点自动继承。
 *
 * 它做两件事：
 *   1. 同源请求默认 `credentials: "same-origin"` —— 会话凭证是 HttpOnly
 *      Cookie，不带 credentials 就等于没登录；
 *   2. 非安全方法自动补 `X-CSRF-Token`（双提交：值与 `wb_csrf` Cookie 相同）。
 *      攻击者跨站发请求带不上自定义头；能读 Cookie 的只有同源脚本。
 *
 * 注意：**不要**在这里加任何令牌读取逻辑 —— 令牌本身就不该被 JS 看见。
 */
(function () {
  "use strict";
  if (window.__wbSecureFetchInstalled) return;
  window.__wbSecureFetchInstalled = true;

  var rawFetch = window.fetch ? window.fetch.bind(window) : null;
  if (!rawFetch) return;

  var SAFE_METHODS = { GET: 1, HEAD: 1, OPTIONS: 1 };

  function readCookie(name) {
    try {
      var pattern = new RegExp("(?:^|;\\s*)" + name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "=([^;]*)");
      var m = document.cookie.match(pattern);
      return m ? decodeURIComponent(m[1]) : "";
    } catch (e) {
      return "";
    }
  }

  window.fetch = function (input, init) {
    var opts = init ? Object.assign({}, init) : {};
    var url = null;
    try {
      url = new URL(typeof input === "string" ? input : input.url, location.href);
    } catch (e) {
      return rawFetch(input, init);
    }
    // 跨源请求原样放行：CSP 的 connect-src 已经在浏览器层挡住大部分，
    // 我们不在 JS 里假装做网络策略。
    if (url.origin !== location.origin) return rawFetch(input, opts);

    if (opts.credentials === undefined) opts.credentials = "same-origin";

    var method = String(opts.method || (input && input.method) || "GET").toUpperCase();
    if (!SAFE_METHODS[method]) {
      try {
        var headers = new Headers(opts.headers || (input && input.headers) || undefined);
        if (!headers.has("X-CSRF-Token")) {
          var token = readCookie("wb_csrf");
          if (token) headers.set("X-CSRF-Token", token);
        }
        opts.headers = headers;
      } catch (e) {
        /* Headers 不可用时保持原样，让服务端返回 403 —— 失败要显性 */
      }
    }
    return rawFetch(input, opts);
  };
})();
