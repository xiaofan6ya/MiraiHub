"""CodeArts 每日签到 / 权益领取（2026-11-07，从官方客户端 D:/CodeArts 还原）。

## 两条领取体系（客户端 BenefitService 的两条腿）

1. **benefit claim**（opengw.developer.huaweicloud.com）
   * `GET  /api/v1/benefit/claim` —— 查领取状态（`error_code=="0000"` 成功）
   * `POST /api/v1/benefit/claim?channel=DESKTOP` —— 领取（body `{}`）
2. **welfare**（snap-access.cn-north-4.myhuaweicloud.com，策略中心）
   * `GET  /v1/ops/delivery?channel=DESKTOP` —— 活动列表（`claimActions[]`）
   * `POST /v1/ops/claim` body `{campaignId, idempotentKey, channel:"DESKTOP", benefitId?}`
     benefitId 缺省 = 批领全部 ELIGIBLE（排行榜权益除外）

## 鉴权：华为云 SDK-HMAC-SHA256 请求签名

账号库存的 `access_key / secret_key / security_token` 是登录时 STS 签发的
**临时凭证**（6h，有 refresh_token 就能一直续）——正好是签名所需三元组。
签名照抄客户端 `sign()`：canonical URI 尾部强制 `/`、query 全量编码排序、
头全小写排序（含 host + X-Sdk-Date）、body sha256、
`Authorization: SDK-HMAC-SHA256 Access=..., SignedHeaders=..., Signature=...`。

「每日签到」在 CodeArts 上的形态就是登录事件触发的**权益领取**：
定时任务每天代跑一遍 checkBenefitClaim / welfare delivery，
把可领的 campaign 批量领掉（幂等键防重，重复领上游会拒绝，无害）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

from sqlalchemy.orm import Session

from admin.platform.codearts.services import accounts as ca

logger = logging.getLogger("admin.platform.codearts.benefit")

ALGORITHM = "SDK-HMAC-SHA256"
BENEFIT_BASE = "https://opengw.developer.huaweicloud.com"
POLICY_CENTER_BASE = "https://snap-access.cn-north-4.myhuaweicloud.com"
DEFAULT_TIMEOUT = 30.0


def _sign(method: str, url: str, headers: dict, body: str,
          access_key: str, secret_key: str) -> dict:
    """华为云 SDK-HMAC-SHA256 请求签名（照抄客户端实现）。"""
    parsed = urlparse(url)
    now = datetime.now(timezone.utc)
    sdk_date = now.strftime("%Y%m%dT%H%M%SZ")

    # canonical URI：按 / 分段 encodeURIComponent，且**尾部强制 /**
    parts = [p for p in parsed.path.split("/") if p != ""]
    canonical_uri = "/".join(urllib.request.quote(p, safe="") for p in parts)
    canonical_uri = "/" + canonical_uri + ("/" if canonical_uri else "/")
    # canonical query：k=v 全量编码后排序
    from urllib.parse import parse_qsl

    query_items = sorted(
        "%s=%s" % (urllib.request.quote(k, safe=""), urllib.request.quote(v, safe=""))
        for k, v in parse_qsl(parsed.query))
    canonical_query = "&".join(query_items)

    signing_headers = dict(headers)
    signing_headers["host"] = parsed.netloc
    signing_headers["X-Sdk-Date"] = sdk_date
    entries = sorted(((k.lower(), str(v).strip()) for k, v in signing_headers.items()),
                     key=lambda kv: kv[0])
    canonical_headers = "".join("%s:%s\n" % (k, v) for k, v in entries)
    signed_headers = ";".join(k for k, _ in entries)
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    canonical_request = "\n".join([
        method.upper(), canonical_uri, canonical_query,
        canonical_headers, signed_headers, body_hash])
    sts = "%s\n%s\n%s" % (
        ALGORITHM, sdk_date,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest())
    signature = hmac.new(secret_key.encode("utf-8"),
                         sts.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = "%s Access=%s, SignedHeaders=%s, Signature=%s" % (
        ALGORITHM, access_key, signed_headers, signature)
    out = dict(signing_headers)
    out["Authorization"] = authorization
    return out


def _request(method: str, url: str, *, headers: dict, body=None,
             account_id: int = 0) -> dict:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method=method.upper())
    for key, value in headers.items():
        req.add_header(str(key), str(value))
    try:
        from admin.upstream_http import urllib_opener

        with urllib_opener().open(req, timeout=DEFAULT_TIMEOUT) as resp:
            text = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        payload = ""
        try:
            payload = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError("HTTP %s：%s" % (exc.code, payload[:160])) from exc
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise RuntimeError("返回非 JSON：%s" % text[:120]) from exc
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def _signed_headers(method: str, url: str, *, creds: dict, body: str = "",
                    trace_id: str = "") -> dict:
    base = {
        "X-Security-Token": str(creds.get("security_token") or ""),
        "Agent-Type": "PromptCenter",
        "Content-Type": "application/json",
        "X-Language": "zh-cn",
    }
    if trace_id:
        base["x-snap-traceid"] = trace_id
    return _sign(method, url, base, body,
                 str(creds.get("access_key") or ""),
                 str(creds.get("secret_key") or ""))


def _claimable_rows(delivery: dict) -> list[dict]:
    """从 welfare delivery 响应里抠「可领取」的活动行。

    响应字段没有公开文档，这里做**防御性解析**：递归找带
    campaignId/id 的行，状态字段命中 ELIGIBLE/CLAIMABLE 就算可领。
    """
    rows: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            cid = node.get("campaignId") or node.get("campaign_id")
            if cid and (node.get("claimActions") is not None
                        or node.get("status") or node.get("claimStatus")
                        or node.get("state")):
                rows.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(delivery)
    out = []
    for row in rows:
        blob = json.dumps(row, ensure_ascii=False).upper()
        status = str(row.get("status") or "")
        claimable = row.get("claimable") is True or (
            ("ELIGIBLE" in blob or "CLAIMABLE" in blob) and status != "CLAIMED")
        if not claimable:
            continue
        base = {
            "campaign_id": row.get("campaignId") or row.get("campaign_id"),
            "title": row.get("title") or "",
            "amount": row.get("benefitAmount"),
            "unit": row.get("benefitUnit") or "CREDIT",
        }
        actions = row.get("claimActions") or []
        if isinstance(actions, list) and actions:
            for action in actions:
                if isinstance(action, dict):
                    out.append({**base, "benefit_id": action.get("benefitId"),
                                "idempotent_key": action.get("idempotentKey")
                                or str(uuid.uuid4())})
        else:
            out.append({**base, "benefit_id": None,
                        "idempotent_key": str(uuid.uuid4())})
    return out


def _delivery_items(delivery: dict) -> list[dict]:
    """delivery 响应里的活动行（不筛状态），供「今日已领」判定。

    实测结构（2026-11-07）：`data.data.items[]`，行字段
    `campaignId / title / type / benefitAmount / claimable(bool) /
    status("CLAIMED")`。「每日签到领100积分」= type=USER_LOGIN、
    benefitAmount=100 的行。
    """
    inner = delivery.get("data")
    if isinstance(inner, dict):
        cand = inner.get("items") or inner.get("list") or inner.get("campaigns")
        if isinstance(cand, list):
            return [r for r in cand if isinstance(r, dict)]
    return []


def _all_claimed(delivery: dict) -> bool:
    """可领活动是否都已 CLAIMED（「今日已领」判定）。"""
    rows = [r for r in _delivery_items(delivery)
            if r.get("claimable") is True or r.get("type") == "USER_LOGIN"]
    if not rows:
        return False
    return all(str(r.get("status") or "") == "CLAIMED"
               for r in rows if r.get("claimable") is not True)


def run_daily_checkin(db: Session, schedule=None, realm: str = "") -> dict:
    """CodeArts 每日签到：benefit claim + welfare 批领。"""
    accounts = ca.accounts_for(db, realm)
    items: list[dict] = []
    failed: list[dict] = []
    granted = 0

    for account in accounts:
        if (getattr(account, "status", "") or "") != "active":
            continue
        info: dict = {"account_id": account.id, "account": account.name}
        try:
            ca.ensure_credential(db, account)
            creds = ca.credentials_of(account)
            if not creds.get("access_key") or not creds.get("security_token"):
                raise RuntimeError("缺少 AK/SK 或 security_token，请重新登录客户端")

            credit_gained = 0.0
            claimed_titles: list[str] = []

            # ---- 1) welfare 主通道：列表 → 可领批领（积分额在 benefitAmount）----
            delivery_url = POLICY_CENTER_BASE + "/v1/ops/delivery?channel=DESKTOP"
            delivery = _request(
                "GET", delivery_url,
                headers=_signed_headers("GET", delivery_url, creds=creds,
                                        trace_id=str(uuid.uuid4())),
                account_id=account.id)
            rows = _claimable_rows(delivery)
            claimed_now = 0
            errors: list[str] = []
            for row in rows:
                body = {"campaignId": row["campaign_id"],
                        "idempotentKey": row["idempotent_key"],
                        "channel": "DESKTOP"}
                if row.get("benefit_id"):
                    body["benefitId"] = row["benefit_id"]
                wurl = POLICY_CENTER_BASE + "/v1/ops/claim"
                try:
                    result = _request(
                        "POST", wurl,
                        headers=_signed_headers("POST", wurl, creds=creds,
                                                body=json.dumps(body)),
                        body=body, account_id=account.id)
                    if not (result.get("error_code")
                            and result.get("error_code") != "0000"):
                        claimed_now += 1
                        credit_gained += float(row.get("amount") or 0)
                        if row.get("title"):
                            claimed_titles.append(str(row["title"]))
                    else:
                        errors.append(str(result.get("error_msg")
                                          or result.get("message") or "")[:80])
                except Exception as exc:  # noqa: BLE001 - 单活动失败不影响其余
                    errors.append(str(exc)[:80])
            info["welfare_campaigns"] = len(rows)
            info["welfare_claimed"] = claimed_now
            if errors:
                info["welfare_errors"] = errors[:3]

            # ---- 2) benefit 辅通道（opengw）：可领则领，失败只记录 ----
            try:
                claim_url = BENEFIT_BASE + "/api/v1/benefit/claim"
                claim_headers = _signed_headers(
                    "POST", claim_url + "?channel=DESKTOP", creds=creds, body="{}")
                claim_headers["Content-Type"] = "application/json"
                claim_result = _request(
                    "POST", claim_url + "?channel=DESKTOP",
                    headers=claim_headers, body={}, account_id=account.id)
                if str(claim_result.get("error_code") or "") == "0000":
                    info["benefit_claim"] = "ok"
                    result_data = claim_result.get("result")
                    amount = (result_data or {}).get("amount") \
                        if isinstance(result_data, dict) else None
                    if isinstance(amount, (int, float)):
                        credit_gained += float(amount)
                        claimed_now += 1
                else:
                    info["benefit_claim"] = str(
                        claim_result.get("error_msg")
                        or claim_result.get("message") or "no-change")[:80]
            except Exception as exc:  # noqa: BLE001 - 辅通道失败不影响主结果
                info["benefit_claim"] = str(exc)[:80]

            # ---- 汇总：领完刷一次余额，结果表格直接可读 ----
            quota_result = ca.sync_quota(db, account, with_models=False)
            info["credit"] = round(credit_gained, 2) if credit_gained else 0
            info["available"] = ((quota_result.get("quota") or {}).get("remain")
                                 if quota_result.get("ok") else None)
            if claimed_now:
                granted += 1
                info.update({"ok": True, "status": "已领取",
                             "reason": ("、".join(claimed_titles[:3])
                                        or "领取权益 %d 项" % claimed_now)})
            else:
                info.update({"ok": True,
                             "status": "今日已领" if _all_claimed(delivery)
                             else "无可领活动",
                             "reason": ""})
        except Exception as exc:  # noqa: BLE001 - 单号失败不影响整批
            failed.append({"account_id": account.id, "account": account.name,
                           "error": str(exc)[:160]})
            continue
        items.append(info)

    total = len(items) + len(failed)
    if total == 0:
        summary = "没有 CodeArts 账号"
    elif granted:
        summary = "签到完成：%d 个账号，新领取权益 %d 个" % (total, granted)
    else:
        summary = "签到检查完成：%d 个账号，今日无可新领权益（已领过或暂无活动）" % total
    if failed:
        summary += "，失败 %d" % len(failed)
    logger.info("CodeArts %s", summary)
    return {
        "ok": not failed,
        "supported": True,
        "platform": "codearts",
        "realm": realm or "",
        "total": total,
        "granted": granted,
        "items": items,
        "failed": failed,
        "summary": summary,
    }
