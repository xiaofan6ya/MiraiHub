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
           "client_id", "scopes", "audience", "account_key"}
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
        out.append({
            "token": token,
            "refresh_token": str(cred.get("refreshToken") or ""),
            "expires_at_ms": int(cred.get("expiresAtMs") or 0),
            "realm": normalize_region(realm_guess),
            "client_id": str(cred.get("clientId") or ""),
            "scopes": cred.get("scopes") if isinstance(cred.get("scopes"), list) else [],
            "audience": str(cred.get("audience") or ""),
            "account_key": str(key or ""),
            "source": "local-scan",
        })
    return out


def discover() -> list[dict]:
    """扫描本机全部 MiniMax 登录态（不含敏感字段原文，供列表展示）。"""
    items = []
    for entry in credential_files():
        for record in read_records(entry["path"]):
            token = record["token"]
            items.append({
                "uid": record["account_key"] or ("mm-" + hashlib_short(token)),
                "name": "MiniMax %s" % ("国际版" if record["realm"] == "en" else "中国版"),
                "realm": record["realm"],
                "expires_at_ms": record["expires_at_ms"],
                "has_refresh": bool(record["refresh_token"]),
                "path": entry["path"],
                "token_tail": token[-6:] if len(token) > 6 else "",
            })
    return items


def hashlib_short(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
