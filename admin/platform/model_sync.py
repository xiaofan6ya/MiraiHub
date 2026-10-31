"""平台首个账号入库后自动同步一次模型。

## 为什么要这个

模型清单是**从账号凭证向上游拉**的（见 `admin/routers/models.py`）。
一个平台如果还没有任何可用账号，就永远拉不到模型 —— 面板上那个平台的
模型列表一直是空的，用户得手动点「同步模型」。

但反过来说也不能每个账号入库都同步：批量导入 20 个号会打 20 次上游接口，
既慢又容易被限流。所以规则是：

    **某个平台从 0 个可用账号变成 ≥1 个时**，才同步一次。
    之后再加账号不再自动同步（用户想刷新可以点按钮 / 定时任务）。

## 为什么必须校验账号真的可用

用户反馈过「添加的账号自己登录失效了」。那种号虽然进了库，
但凭证是坏的，拿它去拉模型只会拿到 401 ——
既同步不到东西，又会把「上次同步失败」的状态留在面板上。

所以这里先做一次**轻量探测**（用的是现成的模型拉取本身）：
拉不到就把错误原样带回去，**不假装成功**，前端能据此提示用户
「账号凭证可能已失效，请重新授权」。
"""
from __future__ import annotations

import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

logger = logging.getLogger("admin.platform.model_sync")


def platform_is_first_account(db: Session, storage: str,
                              exclude_ids: tuple[int, ...] = ()) -> bool:
    """该落库平台在**本次入库前**是否没有任何可用账号。

    必须在 `db.commit()` **之前**判断，或者把新账号的 id 排掉 ——
    否则新账号也算进计数，永远返回 False。

    `storage` 是落库平台名（`trae` / `qoder` / `workbuddy` ...），
    不是对外的 `trae-cn` 这种区级 id。
    """
    from admin.models import Account

    name = str(storage or "").strip().lower()
    if not name:
        return False
    query = db.query(func.count(Account.id)).filter(
        Account.platform == name, Account.status == "active")
    if exclude_ids:
        query = query.filter(~Account.id.in_(list(exclude_ids)))
    try:
        return int(query.scalar() or 0) == 0
    except Exception:  # noqa: BLE001 - 计数失败按「不是首个」处理
        logger.debug("判断平台首个账号失败 platform=%s", name, exc_info=True)
        return False


def sync_models_if_first(db: Session, storage: str,
                         exclude_ids: tuple[int, ...] = ()) -> dict:
    """平台首个账号时同步模型。

    返回 `{"attempted": bool, "ok": bool, "summary": str, "detail": {...}}`。
    **绝不抛异常**：同步失败不能影响「账号已入库」这个既成事实。
    """
    result = {"attempted": False, "ok": False, "summary": "", "detail": {}}
    name = str(storage or "").strip().lower()
    if not name:
        return result
    try:
        if not platform_is_first_account(db, name, exclude_ids):
            return result
    except Exception:  # noqa: BLE001
        return result

    result["attempted"] = True
    try:
        from admin.routers.models import _do_sync_models, _invalidate_routing_cache

        # 先 commit：`_do_sync_models` 会另开查询，未提交的账号它看不见，
        # 会误判「没有可用账号」直接返回空。
        db.commit()
        detail = _do_sync_models(db, None, name) or {}
        _invalidate_routing_cache()
        added = int(detail.get("added") or 0)
        fetched = int(detail.get("total_fetched") or 0)
        problems = detail.get("problems") or {}
        if fetched <= 0 or problems:
            # 拉到了 0 个模型 / 有明确问题 => 账号本身可能不可用
            reason = ""
            if isinstance(problems, dict) and problems:
                first = list(problems.values())[0]
                reason = str(first)[:160] if not isinstance(first, dict) else str(
                    first.get("message") or first.get("error") or "")[:160]
            elif isinstance(problems, list) and problems:
                reason = str(problems[0])[:160]
            result.update({
                "ok": False,
                "summary": ("首个账号已入库，但模型同步没拿到数据%s —— "
                            "该账号凭证可能已失效，建议重新授权或在账号页试一次刷新"
                            % ("（%s）" % reason if reason else "")),
                "detail": detail,
            })
            logger.warning("平台 %s 首个账号入库后模型同步为空：%s", name, reason or "无原因")
            return result
        result.update({
            "ok": True,
            "summary": "已自动同步模型：新增 %d 个，更新 %d 个" % (
                added, int(detail.get("updated") or 0)),
            "detail": detail,
        })
        logger.info("平台 %s 首个账号入库，已自动同步模型 %d 个（新增 %d）",
                    name, fetched, added)
        return result
    except Exception as exc:  # noqa: BLE001 - 同步失败不影响入库
        # `_fetch_catalog` 在**一个平台都取不到模型**时会抛 502，
        # 语义是「这个号的凭证可能有问题」—— 对「首个账号入库」这个场景来说，
        # 这正是要提示给用户的信息，所以单独识别出来，别混在泛化错误里。
        detail_text = ""
        try:
            detail_text = str(getattr(exc, "detail", "") or exc)
        except Exception:  # noqa: BLE001
            detail_text = str(exc)
        status_code = getattr(exc, "status_code", None)
        logger.warning("平台 %s 首个账号入库后模型同步失败: %s", name, detail_text[:300])
        if status_code == 502:
            result.update({
                "ok": False,
                "summary": ("首个账号已入库，但拉不到模型目录 —— 该账号凭证可能已失效"
                            "（%s）。建议重新授权，或在「模型配置」页手动同步一次。"
                            % detail_text[:180]),
            })
        else:
            result.update({
                "ok": False,
                "summary": "账号已入库，但模型同步失败：%s（可稍后在模型页手动同步）"
                           % detail_text[:160],
            })
        return result
