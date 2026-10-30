"""逐账号巡检成长计划；不经过账号池，不因权重而遗漏账号。

自然日按上游 Asia/Shanghai 计算。SQLite/MySQL 的既有配置表持久化进度、
幂等 token 和跨 worker 租约，无需新增数据库迁移。
"""
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from admin.backend import AccountSession
from admin.models import Account, SystemSetting

_LEASE_KEY = "growth_plan:lease"
_CURSOR_KEY = "growth_plan:cursor"
_DAY_TZ = timezone(timedelta(hours=8))
_MAX_DRAWS = 5


def _read(db, key):
    row = db.get(SystemSetting, key)
    try:
        return json.loads(row.value) if row else {}
    except (TypeError, ValueError):
        return {}


def _write(db, key, value, *, commit=True):
    row = db.get(SystemSetting, key)
    if row is None:
        row = SystemSetting(key=key)
        db.add(row)
    row.value = json.dumps(value, ensure_ascii=False)
    if commit:
        db.commit()


class _Lease:
    """数据库 CAS 锁，覆盖手动执行、定时执行和多个 uvicorn worker。"""
    def __init__(self, db, key=_LEASE_KEY):
        self.db = db
        self.key = key
        self.value = ""

    def acquire(self):
        row = self.db.get(SystemSetting, self.key)
        if row is None:
            self.db.add(SystemSetting(key=self.key, value="{}"))
            try:
                self.db.commit()
            except IntegrityError:
                self.db.rollback()
            row = self.db.get(SystemSetting, self.key)
        self.db.refresh(row)
        try:
            expiry = json.loads(row.value).get("expires", 0)
        except (ValueError, TypeError, AttributeError):
            expiry = 0
        if expiry > time.time():
            return False
        previous = row.value
        self.value = json.dumps({"owner": uuid.uuid4().hex, "expires": time.time() + 600})
        changed = (self.db.query(SystemSetting)
                   .filter(SystemSetting.key == self.key, SystemSetting.value == previous)
                   .update({SystemSetting.value: self.value}, synchronize_session=False))
        self.db.commit()
        return bool(changed)

    def renew(self):
        previous = self.value
        value = json.loads(previous)
        value["expires"] = time.time() + 600
        self.value = json.dumps(value)
        changed = (self.db.query(SystemSetting)
                   .filter(SystemSetting.key == self.key, SystemSetting.value == previous)
                   .update({SystemSetting.value: self.value}, synchronize_session=False))
        self.db.commit()
        if not changed:
            raise RuntimeError("成长计划执行租约已失效")

    def release(self):
        (self.db.query(SystemSetting)
         .filter(SystemSetting.key == self.key, SystemSetting.value == self.value)
         .update({SystemSetting.value: "{}"}, synchronize_session=False))
        self.db.commit()


class _BudgetReached(Exception):
    pass


def _number(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _active_today(result, day):
    today = (result.get("data") or {}).get("today") or {}
    return bool(result.get("ok") and today.get("date") == day
                and today.get("is_active") is True)


def _account_plan(sess, state, save, call, day, model):
    steps = []
    credit = 0
    deferred = False

    def add(name, ok=True, message="", reward=0):
        nonlocal credit
        credit += _number(reward)
        steps.append({"step": name, "ok": bool(ok), "message": message,
                      "reward": _number(reward)})

    def request(name, fn, *args):
        result = call(fn, *args)
        if not result.get("ok"):
            # 只记录状态码，不将上游可能包含凭证的原始异常写入日志。
            add(name, False, f"接口失败 HTTP {result.get('status', 0)} / code {result.get('code', '-')}")
            return None
        return result.get("data") or {}

    try:
        heatmap = call(sess.growth_heatmap)
        if _active_today(heatmap, day):
            state["active_day"] = day
            save()
            add("每日活跃", message="今日热力图已点亮，跳过对话")
        elif not heatmap.get("ok"):
            add("每日活跃", False, "热力图查询失败，等待下轮检查")
        else:
            if state.get("chat_day") != day:
                attempt = state.get("chat_attempt_at", 0)
                if time.time() - attempt >= 3600:
                    state["chat_attempt_at"] = time.time()
                    save()  # 在发请求前落库，超时/崩溃后不会立刻重复发起
                    chat = call(sess.growth_fire_event, ["chat_request_send"], model)
                    if chat.get("ok"):
                        state["chat_day"] = day
                        save()
                    else:
                        add("每日活跃对话", False, "免费模型对话未完成，一小时后重试")
                else:
                    add("每日活跃对话", False, "上次请求失败或结果未知，一小时后重试")
            if state.get("chat_day") == day:
                for delay in (0, .5, 1):
                    if delay:
                        time.sleep(delay)
                    if _active_today(call(sess.growth_heatmap), day):
                        state["active_day"] = day
                        save()
                        break
                confirmed = state.get("active_day") == day
                add("每日活跃", confirmed, "对话已完成，热力图已点亮" if confirmed
                    else "对话已完成，活跃尚未入账；下轮只查状态")

        # Buddy 加油站积分签到与热力图活跃是两套独立活动。
        if state.get("checkin_day") != day:
            status = call(sess.get_checkin_status)
            if status.get("today_checked_in"):
                state["checkin_day"] = day
                save()
                add("每日打卡", message="今日积分已领")
            elif status.get("active") is False:
                add("每日打卡", message="签到活动未开启或已结束")
            elif status.get("active") is True:
                result = call(sess.claim_daily_checkin)
                already = result.get("status") == "already_claimed"
                if result.get("ok") or already:
                    state["checkin_day"] = day
                    save()
                add("每日打卡", result.get("ok", False) or already,
                    "今日积分已领" if already else "签到积分已领取" if result.get("ok")
                    else "未领取，下轮重新检查签到状态",
                    result.get("credit", 0) if result.get("ok") else 0)
            else:
                add("每日打卡", False, "签到状态不可用")
        else:
            add("每日打卡", message="今日积分已领")

        buddy = call(sess.buddy_info)
        if buddy is None:
            if request("宠物协议", sess.buddy_agreement) is not None:
                adopted = request("首次领养", sess.buddy_first)
                if adopted is not None:
                    add("首次领养", message="首次宠物已领取", reward=adopted.get("credit"))
                    buddy = call(sess.buddy_info)
        if buddy:
            travel = call(sess.travel_status)
            if travel.get("state") == "arrived":
                gift = request("领取礼物", sess.travel_claim)
                if gift is not None:
                    add("领取礼物", message="旅行礼物已领取", reward=gift.get("reward_credit"))
                    travel = call(sess.travel_status)
            if travel.get("state") == "idle" and not travel.get("daily_limit_reached"):
                config = request("旅行地点", sess._growth, "GET", "/activity/growth/buddy/travel/config")
                locations = (config or {}).get("locations") or []
                # 新接口返回 code（coffee/mall/gym/ancient_town），旧接口返回
                # location_id。上游仍接收 1..4 的 location_id。
                location = next((p.get("location_id") or p.get("id") for p in locations
                                 if p.get("location_id") is not None or p.get("id") is not None), None)
                if location is None and locations:
                    codes = {"coffee": 1, "mall": 2, "gym": 3, "ancient_town": 4}
                    location = codes.get(str(locations[0].get("code") or ""))
                if location is not None:
                    if request("派出旅行", sess.travel_depart, location) is not None:
                        add("派出旅行", message="已派出，下轮到站自动领礼物")
                elif config is not None:
                    add("派出旅行", False, "暂无可用旅行地点")
            elif travel.get("state") == "traveling":
                add("领取礼物", message="旅行中，到站后领取")
            elif travel.get("daily_limit_reached"):
                add("派出旅行", message="已达到今日旅行上限")

        streak = request("连登奖励", sess.growth_streak)
        if streak is not None:
            redemption = streak.get("redemption_status") or {}
            tiers = redemption.get("tiers") or []
            eligible = [t for t in tiers if t.get("tier") and
                        redemption.get(f"tier_{t['tier']}_status") == "available"]
            for tier in eligible:
                name = tier["tier"]
                token = "redeem-" + uuid.uuid5(uuid.NAMESPACE_URL,
                    state["identity"] + ":" + day[:7] + ":" + name).hex
                data = request(f"兑换{name}奖励", sess.growth_redeem, name, token)
                if data is not None:
                    overflow = _number(data.get("cards_overflow"))
                    add(f"兑换{name}奖励", message="已领取连登奖励" +
                        (f"，补登卡达上限，溢出 {overflow} 张" if overflow else ""),
                        reward=data.get("credit_granted"))
            if not eligible:
                add("连登奖励", message="暂无可兑换档位（未达标或本月已领）")

        lottery = request("限时抽奖", sess._growth, "GET", "/activity/growth/lottery/summary")
        if lottery is not None:
            module = lottery.get("module") or {}
            if module.get("enabled") is True:
                chances = request("抽奖次数", sess.growth_lottery_chances)
                available = _number((chances or {}).get("balance"))
                if state.get("draw_day") != day:
                    state["draw_day"], state["draw_count"] = day, 0
                # pending token 跨天保存。返回丢失时先重放原 token，不产生新扣费。
                attempts = min(available + bool(state.get("draw_token")),
                               _MAX_DRAWS - _number(state.get("draw_count")))
                for _ in range(max(0, attempts)):
                    state.setdefault("draw_token", "draw-" + uuid.uuid4().hex)
                    save()
                    result = call(sess.growth_lottery_draw, state["draw_token"])
                    if not result.get("ok"):
                        msg = str(result.get("msg") or "").lower()
                        if result.get("status") == 409 and "duplicate" in msg:
                            state.pop("draw_token", None)
                            state["draw_count"] += 1
                            save()
                            add("限时抽奖", message="此前抽奖已处理，避免重复计奖")
                            continue
                        if "insufficient lottery chance balance" in msg or "lottery disabled" in msg:
                            state.pop("draw_token", None)
                            save()
                        add("限时抽奖", False, "抽奖未确认，下轮使用同一 token 核对")
                        break
                    state.pop("draw_token", None)
                    state["draw_count"] += 1
                    save()
                    data = result.get("data") or {}
                    add("限时抽奖", message="抽奖已完成；实物奖品请在官方成长中心填写领取信息",
                        reward=data.get("credit_granted"))
                if not attempts:
                    add("限时抽奖", message="暂无免费次数或已达每日自动抽奖上限（5次）")
            else:
                add("限时抽奖", message="限时抽奖未开启")

        quota = request("盲盒额度", sess.growth_buddy_quota)
        if quota is not None:
            count = min(_number(quota.get("affordable")), _number(quota.get("max_open_count")), 5)
            if state.get("box_attempt_day") == day:
                confirmed = state.get("box_confirmed_day") == day
                add("开启盲盒", confirmed, message="今日已完成开盒；避免重复消耗能量" if confirmed
                    else "今日开盒结果未知，避免重复消耗；请在官方宠物列表核对")
            elif count <= 0:
                add("开启盲盒", message="能量不足，保留至下轮检查")
            else:
                # open 不支持 client_token；发送前记账，一天最多一批，未知结果不重试。
                state["box_attempt_day"] = day
                save()
                boxes = request("开启盲盒", sess.growth_buddy_open, count)
                if boxes is not None:
                    state["box_confirmed_day"] = day
                    save()
                    add("开启盲盒", message=f"已开启 {len(boxes.get('results') or [])} 个盲盒")
                    request("开盒后额度", sess.growth_buddy_quota)
    except _BudgetReached:
        deferred = True
        add("巡检", False, "本轮时间预算已到，下轮继续检查")
    except Exception as exc:
        add("巡检", False, f"执行失败（{type(exc).__name__}），下轮重试")
    return {"ok": all(s["ok"] for s in steps), "credit": credit, "deferred": deferred, "steps": steps,
            "reason": "；".join(f"{s['step']}：{s['message']}" for s in steps)}


def run_growth_plan(db, *, budget_seconds=180, progress=None, now=None, lease=None):
    """一轮最多 3 分钟，游标轮转；尾部账号不会受权重或前面慢账号影响。"""
    from admin.scheduler import _account_label
    from admin.routers.growth import _pick_free_model

    if lease is None:
        lease = _Lease(db)
        if not lease.acquire():
            return {"task": "run_growth_plan", "ok": True, "skipped": "成长计划正在执行"}
    started = time.monotonic()
    deadline = started + budget_seconds
    day = (now or datetime.now(_DAY_TZ)).astimezone(_DAY_TZ).date().isoformat()
    detail = []
    try:
        accounts = db.query(Account).filter(Account.status == "active").order_by(Account.id).all()
        cursor_state = _read(db, _CURSOR_KEY)
        cursor = _number(cursor_state.get("id"))
        outstanding = cursor_state.get("remaining") or []
        if outstanding:
            by_id = {a.id: a for a in accounts}
            accounts = [by_id[i] for i in outstanding if i in by_id]
        else:
            accounts = [a for a in accounts if a.id > cursor] + [a for a in accounts if a.id <= cursor]
        remaining = [a.id for a in accounts]
        model = _pick_free_model(db)

        def call(fn, *args):
            if time.monotonic() >= deadline:
                raise _BudgetReached()
            lease.renew()
            return fn(*args)

        for account in accounts:
            if time.monotonic() >= deadline:
                break
            identity = hashlib.sha256(str(account.uid or f"account:{account.id}").encode()).hexdigest()[:32]
            key = "growth_plan:account:" + identity
            state = _read(db, key)
            state["identity"] = identity
            from admin.growth_center import load_snapshot, observe, store_snapshot, record_result, SECTIONS
            snapshot = load_snapshot(db, account)
            sess = None
            try:
                sess = AccountSession(account.auth_json)

                def save():
                    account.auth_json = sess.updated_json()
                    _write(db, key, state)

                def observed_call(fn, *args):
                    value = call(fn, *args)
                    name = {"growth_heatmap": "heatmap", "growth_streak": "streak",
                            "growth_buddy_quota": "quota", "buddy_info": "buddy",
                            "travel_status": "travel", "get_checkin_status": "checkin"}.get(fn.__name__)
                    data = value
                    if name == "buddy":
                        data = {"buddy": value}
                    elif name in ("heatmap", "streak", "quota"):
                        data = value.get("data") if value.get("ok") else None
                    elif fn.__name__ == "_growth" and args and args[0] == "GET":
                        name = next((key for key, (_, path) in SECTIONS.items() if path == args[1]), None)
                        data = value.get("data") if value.get("ok") else None
                    if name and data is not None:
                        observe(snapshot, name, data)
                        if name == "quota" and "balance" in data:
                            observe(snapshot, "energy", {"balance": data["balance"]})
                    return value

                result = _account_plan(sess, state, save, observed_call, day, model)
                if result.get("credit", 0) > 0:
                    from admin.routers.accounts import _sync_credit_snapshot
                    try:
                        balance = sess.fetch_balance()
                        account.balance_total = int(balance.get("total") or 0)
                        account.balance_remain = int(balance.get("remain") or 0)
                        account.last_sync_at = datetime.utcnow()
                        result["balance_after"] = account.balance_remain
                        _sync_credit_snapshot(account, sess.fetch_credit_details())
                    except Exception:
                        result["resource_refresh_failed"] = True
            except Exception as exc:
                result = {"ok": False, "credit": 0, "reason": f"账号会话失败（{type(exc).__name__}）"}
            finally:
                if sess is not None:
                    try:
                        account.auth_json = sess.updated_json()
                        db.commit()
                    except Exception:
                        db.rollback()
                        result = {"ok": False, "credit": 0, "reason": "账号状态保存失败，需复查"}
                    finally:
                        try:
                            sess.close()
                        except Exception:
                            pass
            detail.append({"account_id": account.id, "account": _account_label(account), **result})
            store_snapshot(db, account, snapshot)
            record_result(db, account.id, result)
            remaining.remove(account.id)
            if result.get("deferred"):
                remaining.append(account.id)
            _write(db, _CURSOR_KEY, {"id": account.id, "remaining": remaining})
            if progress:
                progress({"task": "run_growth_plan", "ok": True, "running": True,
                          "summary": f"成长计划执行中 {len(detail)}/{len(accounts)}",
                          "detail": detail})
        failed = sum(not r["ok"] for r in detail)
        _write(db, _CURSOR_KEY, {"id": detail[-1]["account_id"] if detail else cursor,
                               "remaining": remaining})
        pending = len(remaining)
        credit = sum(r["credit"] for r in detail)
        return {"task": "run_growth_plan", "ok": failed == 0, "total": len(accounts),
                "processed": len(detail), "pending": pending, "failed": failed,
                "credit": credit, "elapsed_s": round(time.monotonic() - started, 1),
                "detail": detail, "summary": f"成长计划已检查 {len(detail)}/{len(accounts)} 个账号，"
                f"领取 {credit} 积分" + (f"，{failed} 个需复查" if failed else "") +
                (f"，剩余 {pending} 个将在一分钟后继续" if pending else "")}
    finally:
        lease.release()