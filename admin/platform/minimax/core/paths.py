"""MiniMax Code 客户端本机路径探测（扫描入库的凭证源）。

## 凭证在哪儿（官方客户端 3.1.6 实测）

    ~/.minimax/auth/prod/<buildEnv>/<region>/mcode-public/auth.json
        {"schemaVersion":1, "records":{"com.minimax.mcode.oauth.prod.cn <account>":
         {"accessToken":"mmoat_...","refreshToken":"mmort_...","tokenType":"Bearer",
          "clientId":"mcode-public","scopes":["agent.default"],
          "audience":"agent-backend","expiresAtMs":...}}}
    ~/.minimax/auth/prod/<buildEnv>/<region>/mcode-public/auth-state.json
        {"status":"authenticated","buildEnv":"prod","region":"cn",...}

- 明文 JSON（Windows 下文件权限不设防也无所谓 —— 客户端自己就没加密）。
- `buildEnv` 目录名：prod / test / staging，只认 prod（测试环境的号调不了生产）。
- `region` 目录名：cn / en。
- 扫描路径禁止写死盘符/用户名（skill 铁律 9）：统一从 `os.homedir()` 展开，
  允许 `MINIMAX_AUTH_HOME` 覆盖（多 profile / 迁移盘场景）。
"""
from __future__ import annotations

import glob
import json
import os

from admin.platform.minimax.core.endpoints import DEFAULT_REGION, normalize_region

#: 只扫生产环境（test/staging 的凭证连不上生产网关）
BUILD_ENVS = ("prod",)
REGIONS = ("cn", "en")
CLIENT_DIR_NAME = "mcode-public"


def auth_home() -> str:
    """MiniMax 凭证根目录（可被 MINIMAX_AUTH_HOME 覆盖）。"""
    override = os.environ.get("MINIMAX_AUTH_HOME", "").strip()
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".minimax")


def auth_file(realm: str = "", build_env: str = "prod") -> str:
    region = normalize_region(realm) if realm else DEFAULT_REGION
    return os.path.join(auth_home(), "auth", build_env, region,
                        CLIENT_DIR_NAME, "auth.json")


def credential_files() -> list[dict]:
    """枚举本机全部 MiniMax 凭证文件（存在的才返回）。

    返回 [{"path", "realm", "build_env"}]。
    """
    out = []
    for build_env in BUILD_ENVS:
        for region in REGIONS:
            path = auth_file(region, build_env)
            if os.path.isfile(path):
                out.append({"path": path, "realm": region,
                            "build_env": build_env})
    return out


def read_records(path: str) -> list[dict]:
    """读一个 auth.json 的全部 records，归一成导入记录。

    每条：{"token", "refresh_token", "expires_at_ms", "realm", "source",
           "client_id", "scopes", "audience", "account_key", "uid"}
    解析失败/空文件返回 []（不抛 —— 扫描要的就是「能拿多少拿多少」）。
    """
    try:
        with open(path, encoding="utf-8-sig") as handle:
            payload = json.load(handle)
    except Exception:  # noqa: BLE001 - 单个文件坏了不拖垮整次扫描
        return []
    if not isinstance(payload, dict):
        return []
    records = payload.get("records")
    if not isinstance(records, dict):
        return []
    realm_guess = DEFAULT_REGION
    # 路径里的 region 比内容更权威：.../auth/prod/en/mcode-public/auth.json
    norm = path.replace("\\", "/").lower()
    for region in REGIONS:
        if ("/%s/" % region) in norm:
            realm_guess = region
            break
    out = []
    for key, cred in records.items():
        if not isinstance(cred, dict):
            continue
        token = str(cred.get("accessToken") or "")
        if not token:
            continue
        # record key 形如 `com.minimax.mcode.oauth.prod.cn\0<account>`：
        # `\0` 前是 service 名，**后半段才是账号标识**。整串当 uid 会把
        # 不可见的 NUL 带进前端/日志，且每次扫描展示成一坨乱码。
        account_id = str(key or "").split("\0", 1)[-1].strip()
        out.append({
            "token": token,
            "refresh_token": str(cred.get("refreshToken") or ""),
            "expires_at_ms": int(cred.get("expiresAtMs") or 0),
            "realm": normalize_region(realm_guess),
            "client_id": str(cred.get("clientId") or ""),
            "scopes": cred.get("scopes") if isinstance(cred.get("scopes"), list) else [],
            "audience": str(cred.get("audience") or ""),
            "account_key": str(key or ""),
            "uid": account_id,
            "source": "local-scan",
        })
    return out


def discover() -> list[dict]:
    """扫描本机全部 MiniMax 登录态（不含敏感字段原文，供列表展示）。

    字段对齐通用扫描弹窗（`showScanResult`）的判定：
      * `has_plain_credential` / `file` —— 「可导入」的判定键（明文 auth.json）；
      * `uid_full` —— 勾选框 data-uid 用的完整 uid（导入按它过滤）；
      * `uid` —— 展示用短标识。
    """
    items = []
    for entry in credential_files():
        for record in read_records(entry["path"]):
            token = record["token"]
            uid = record["uid"] or ("mm-" + hashlib_short(token))
            items.append({
                "uid": uid[:18] + ("…" if len(uid) > 18 else ""),
                "uid_full": uid,
                "name": "MiniMax %s" % ("国际版" if record["realm"] == "en" else "中国版"),
                "realm": record["realm"],
                "expires_at_ms": record["expires_at_ms"],
                "has_refresh": bool(record["refresh_token"]),
                "has_plain_credential": True,
                "file": entry["path"],
                "path": entry["path"],
                "token_tail": token[-6:] if len(token) > 6 else "",
            })
    return items


def hashlib_short(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def write_back_credential(realm: str, *, access_token: str,
                          refresh_token: str, expires_at_ms: int,
                          build_env: str = "prod") -> bool:
    """把网关续签后的**新票回写官方客户端 auth.json**。

    ## 为什么要回写（互踢问题的根治）

    MiniMax 的 OAuth 是**轮换票**：`oauth2/token` 每次续签都会发新票并
    **立即吊销旧票**（access 与 refresh 一同作废）。官方客户端和网关各持
    一份票，任何一方续签，另一方手里的票就全失效 —— 表现为「客户端突然
    掉登录」或「网关 401 invalid_grant」。

    续签只发生在网关时（ensure_token / keepalive），把新票写回客户端的
    `auth/prod/<env>/<region>/mcode-public/auth.json`（与客户端自己的
    file-store 同结构），双方共享最新票源。客户端正在运行时它内存态可能
    覆盖本文件 —— 那就让客户端赢（客户端下次续签后，网关扫描/续期会再
    拿到新票），不会造成额外损坏。
    """
    path = auth_file(realm, build_env)
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8-sig") as handle:
                payload = json.load(handle)
        else:
            payload = {"schemaVersion": 1, "records": {}}
        records = payload.get("records")
        if not isinstance(records, dict) or not records:
            # 客户端从未写过该文件 / 结构变了：不要造一份假的记录键，
            # 放弃回写（丢的是「回写」这个优化，不是数据）。
            return False
        # 单记录文件（实测一台机器一个登录态）：更新第一条匹配 client 的记录
        for key, cred in records.items():
            if not isinstance(cred, dict):
                continue
            if str(cred.get("clientId") or "") == "mcode-public" \
                    or str(key or "").startswith("com.minimax.mcode"):
                cred["accessToken"] = access_token
                cred["refreshToken"] = refresh_token
                cred["expiresAtMs"] = int(expires_at_ms)
                cred["tokenType"] = "Bearer"
                break
        else:
            return False
        import tempfile
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:  # noqa: BLE001 - 回写是优化，失败不影响网关自身续签
        return False
