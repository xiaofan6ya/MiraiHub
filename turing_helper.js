/**
 * turing_helper.js — 从本机 WorkBuddy 桌面端自带的 Turing Shield SDK 取得设备风控 Token。
 *
 * 用途：workbuddy2api（Python 网关）需要给后端请求注入 `X-Device-Token` 头，
 * 否则敏感请求（签到 / 对话）会被上游风控识别为「非真实客户端」。设备 Token 由桌面端
 * 的 TuringShieldSDK 原生桥接生成，本脚本是 Python 侧调用该原生模块的桥梁。
 *
 * 输出（stdout，单行 JSON）：{"token": "v3:AAAA..."}；失败仅向 stderr 写错误并返回非 0。
 *
 * **SDK 目录自动发现（不写死）**：不同用户把 WorkBuddy 桌面端装在不同位置，本脚本会按
 * 以下顺序查找含 `index.cjs` / `package.json` / `turing_sdk.node` / `TuringShieldSDK.dll`
 * 的 turing-sdk 目录：
 *   1. 环境变量 WORKBUDDY_TURING_SDK_DIR（可指向 turing-sdk 目录，或指向桌面端安装基目录）
 *   1b. 环境变量 WORKBUDDY_INSTALL_DIR（桌面端安装基目录，Python 侧发现后下发）
 *   2. %LOCALAPPDATA% / %APPDATA% / %ProgramFiles% / %ProgramFiles(x86)% / %USERPROFILE% 下的
 *      WorkBuddy 或 workbuddy 目录
 *   3. 各盘根目录（WORKBUDDY_TURING_DRIVES，默认 C,D,E,F）下的 workbuddy / WorkBuddy
 *
 * **配置自动读取（不写死）**：channelId / 产品名 / 版本号优先读安装包里的
 * `resources/app.asar.unpacked/cli/product.json`（`config.turingSdk.channelId`、
 * `genieVersion`），其次读 `resources/install-manifest.json` 的 `appVersion`。
 * 这样官方发版后本脚本自动跟上，不必改代码。
 *
 * 其余可覆盖的环境变量：
 *   WORKBUDDY_TURING_CHANNEL_ID  channelId（默认读 product.json，最后兜底 109144）
 *   WORKBUDDY_TURING_PRODUCT_NAME 产品名（默认 WorkBuddy）
 *   WORKBUDDY_TURING_VERSION      产品版本（默认读安装包，最后兜底 2.0.0）
 *   WORKBUDDY_TURING_DRIVES       参与扫描的盘符列表，逗号分隔（默认 C,D,E,F）
 *   WORKBUDDY_TURING_DEBUG        置 1 时向 stderr 打印取值来源，便于排障
 */
"use strict";
const path = require("node:path");
const fs = require("node:fs");

// 已知的 SDK 相对路径（相对于桌面端安装基目录）
const REL_SDK_PATHS = [
  "resources/app.asar.unpacked/native/turing-sdk",
  "resources/native/turing-sdk",
];

// 兜底默认值：只在读不到安装包时使用，不代表真实版本
const FALLBACK_DESKTOP_VERSION = "5.5.6";
const FALLBACK_TURING_CHANNEL_ID = 109144;

// SDK 目录应具备的特征文件（命中其一即视为有效 SDK 目录）
function looksLikeSdk(dir) {
  try {
    const ents = fs.readdirSync(dir);
    return ents.some((n) =>
      /^index\.(cjs|js)$/i.test(n) ||
      n.toLowerCase() === "package.json" ||
      n.toLowerCase() === "turingshieldsdk.dll" ||
      /\.node$/i.test(n)
    );
  } catch (_) {
    return false;
  }
}

// 目录是否是桌面端安装基目录（以存在 resources/app.asar 为准）
function looksLikeInstall(dir) {
  try {
    return fs.statSync(path.join(dir, "resources", "app.asar")).isFile();
  } catch (_) {
    return false;
  }
}

function readJson(p) {
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch (_) {
    return null;
  }
}

// 从已定位的安装基目录读出版本号与 turing 配置；读不到返回空对象。
function readInstallMeta(base) {
  const out = { version: "", channelId: 0, productName: "", source: {} };
  if (!base) return out;
  const unpacked = path.join(base, "resources", "app.asar.unpacked");

  const productJson = readJson(path.join(unpacked, "cli", "product.json"));
  if (productJson) {
    const turing = (productJson.config || {}).turingSdk;
    if (turing && Number.isInteger(turing.channelId) && turing.channelId > 0) {
      out.channelId = turing.channelId;
      out.source.channelId = "cli/product.json";
    }
    if (typeof productJson.genieVersion === "string" && productJson.genieVersion) {
      out.version = productJson.genieVersion;
      out.source.version = "cli/product.json";
    }
    if (typeof productJson.applicationName === "string" && productJson.applicationName) {
      out.productName = productJson.applicationName;
      out.source.productName = "cli/product.json";
    }
  }

  const manifest = readJson(path.join(base, "resources", "install-manifest.json"));
  if (manifest && !out.version &&
      typeof manifest.appVersion === "string" && manifest.appVersion) {
    out.version = manifest.appVersion;
    out.source.version = "install-manifest.json";
  }
  return out;
}

function collectCandidateDirs() {
  const out = [];
  const add = (d) => { if (d && !out.includes(d)) out.push(d); };

  // 1) 显式覆盖（最高优先级）
  //    WORKBUDDY_TURING_SDK_DIR 是历史变量名，继续支持；
  //    WORKBUDDY_INSTALL_DIR 由 Python 侧发现后下发，优先级相同。
  for (const ev of ["WORKBUDDY_TURING_SDK_DIR", "WORKBUDDY_INSTALL_DIR"]) {
    const explicit = process.env[ev];
    if (!explicit) continue;
    const e = path.resolve(explicit);
    if (looksLikeSdk(e)) {
      add(e);
    } else {
      // 当作安装基目录，拼上已知相对路径再试
      for (const rel of REL_SDK_PATHS) add(path.join(e, rel));
    }
  }

  // 2) 常见安装基目录
  const bases = [];
  for (const ev of ["LOCALAPPDATA", "APPDATA", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "USERPROFILE", "HOME"]) {
    const v = process.env[ev];
    if (v) {
      bases.push(path.join(v, "WorkBuddy"));
      bases.push(path.join(v, "workbuddy"));
    }
  }

  // 3) 各盘根目录（支持纯盘符 C / 带冒号 C: / 完整根路径 C:\ 三种写法）
  const drives = (process.env.WORKBUDDY_TURING_DRIVES || process.env.WORKBUDDY_DRIVES || "C,D,E,F")
    .split(",").map((s) => s.trim()).filter(Boolean);
  for (const d of drives) {
    let root;
    if (/^[A-Za-z]$/.test(d)) {
      root = d + ":\\";          // 纯盘符 C -> C:\
    } else if (/^[A-Za-z]:$/.test(d)) {
      root = d + "\\";           // C: -> C:\
    } else if (d.endsWith("\\") || d.endsWith("/")) {
      root = d;                  // 已是根路径
    } else {
      root = d + "\\";
    }
    bases.push(path.join(root, "workbuddy"));
    bases.push(path.join(root, "WorkBuddy"));
  }

  for (const base of bases) {
    for (const rel of REL_SDK_PATHS) add(path.join(base, rel));
  }
  return out;
}

function findSdkDir() {
  for (const dir of collectCandidateDirs()) {
    if (looksLikeSdk(dir)) return dir;
  }
  return null;
}

// 反推 SDK 目录所属的安装基目录（…/resources/app.asar.unpacked/native/turing-sdk）
function installBaseOf(sdkDir) {
  let p = sdkDir;
  for (let i = 0; i < 6; i++) {
    p = path.dirname(p);
    if (looksLikeInstall(p)) return p;
  }
  return process.env.WORKBUDDY_INSTALL_DIR
    ? path.resolve(process.env.WORKBUDDY_INSTALL_DIR) : null;
}

const sdkDir = findSdkDir();
if (!sdkDir) {
  process.stderr.write(
    "TuringShield SDK 未找到。本脚本依赖本机已安装的 WorkBuddy 桌面端自带的 TuringShieldSDK 原生模块。\n" +
    "已搜索以下候选目录（设置环境变量 WORKBUDDY_TURING_SDK_DIR 指向含 index.cjs / TuringShieldSDK.dll 的目录即可覆盖）：\n"
  );
  for (const d of collectCandidateDirs()) process.stderr.write("  - " + d + "\n");
  process.stderr.write("\n若你已安装桌面端但目录特殊，请设置 WORKBUDDY_TURING_SDK_DIR 后重试。\n");
  process.exit(1);
}

// 把 SDK 目录及其 build/Release 加入 DLL 搜索路径，提升 TuringShieldSDK.dll 解析成功率
try {
  const extra = [sdkDir, path.join(sdkDir, "build", "Release")];
  const sep = process.platform === "win32" ? ";" : ":";
  process.env.PATH = extra.join(sep) + sep + (process.env.PATH || "");
} catch (_) {
  /* 忽略：PATH 增强失败不影响主流程，仅作为辅助 */
}

let turing;
try {
  turing = require(sdkDir);
} catch (e) {
  process.stderr.write("require turing sdk failed: " + (e && e.message ? e.message : String(e)) + "\n");
  process.stderr.write("SDK dir: " + sdkDir + "\n");
  process.exit(1);
}

// 配置：env 覆盖 > 安装包元数据 > 兜底默认
const installBase = installBaseOf(sdkDir);
const meta = readInstallMeta(installBase);

function pickEnv(name, metaVal, fallback) {
  const v = process.env[name];
  if (v !== undefined && String(v).trim() !== "") return String(v).trim();
  if (metaVal !== undefined && metaVal !== null && String(metaVal).trim() !== "") {
    return String(metaVal).trim();
  }
  return String(fallback);
}

const channelId = parseInt(
  pickEnv("WORKBUDDY_TURING_CHANNEL_ID", meta.channelId, FALLBACK_TURING_CHANNEL_ID), 10);
const productName = pickEnv("WORKBUDDY_TURING_PRODUCT_NAME", meta.productName, "WorkBuddy");
const productVersion = pickEnv("WORKBUDDY_TURING_VERSION", meta.version, FALLBACK_DESKTOP_VERSION);

if (process.env.WORKBUDDY_TURING_DEBUG === "1") {
  process.stderr.write(
    "[turing_helper] sdkDir=" + sdkDir + "\n" +
    "[turing_helper] installBase=" + installBase + "\n" +
    "[turing_helper] channelId=" + channelId +
      " (" + (process.env.WORKBUDDY_TURING_CHANNEL_ID ? "env"
              : (meta.source.channelId || "fallback")) + ")\n" +
    "[turing_helper] version=" + productVersion +
      " (" + (process.env.WORKBUDDY_TURING_VERSION ? "env"
              : (meta.source.version || "fallback")) + ")\n" +
    "[turing_helper] productName=" + productName + "\n"
  );
}

function isSupported() {
  try {
    return !turing.isSupported || turing.isSupported();
  } catch (e) {
    return false;
  }
}

(async () => {
  if (!isSupported()) {
    const loadErr = (typeof turing.getLoadError === "function") ? turing.getLoadError() : null;
    process.stderr.write("turing sdk not supported" + (loadErr ? (": " + loadErr) : " on this platform") + "\n");
    process.stderr.write("SDK dir: " + sdkDir + "\n");
    process.exit(1);
  }
  try {
    turing.configure(channelId, productName, productVersion);
    const token = await turing.fetchDeviceToken({
      usingCachedMessage: true,
      includesOutdatedMessage: true,
      includesDeviceInfo: true,
      timeoutMs: 15000,
    });
    const t = (token || "").toString().trim();
    if (!t) {
      process.stderr.write("turing sdk returned empty token\n");
      process.stderr.write("SDK dir: " + sdkDir + "\n");
      process.exit(1);
    }
    process.stdout.write(JSON.stringify({ token: t }));
  } catch (e) {
    process.stderr.write("fetch device token failed: " + (e && e.message ? e.message : String(e)) + "\n");
    process.stderr.write("SDK dir: " + sdkDir + "\n");
    process.exit(1);
  }
})();
