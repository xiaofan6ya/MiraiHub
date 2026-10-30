"""模型分组管理：分组 CRUD + 组内模型多选。

分组用于给 API Key 限定可用模型范围：未绑定分组的 Key 可用全部启用模型；
绑定后只能调用组内模型，越界返回 403。

分组内模型的「有效性」是动态判定的：
- 组内记录了模型 id，但该模型可能后来被禁用（model_configs.enabled=0）；
- 因此对外提供 _effective_group_models()，只返回「组内 ∩ 当前启用」的模型，
  被禁用的模型自动从可用集里隐式排除，不需要手动清理分组。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from admin.db import get_db
from admin.models import ApiKey, ModelConfig, ModelGroup, ModelGroupItem
from admin.security import require_admin

router = APIRouter(prefix="/api/groups", tags=["groups"])


class GroupIn(BaseModel):
    name: str = ""
    note: str = ""
    models: list[str] = []


def group_models(db: Session, group_id: int) -> list[str]:
    """分组内记录的原始模型 id 列表（不判断是否启用）。"""
    rows = (
        db.query(ModelGroupItem)
        .filter(ModelGroupItem.group_id == group_id)
        .order_by(ModelGroupItem.id)
        .all()
    )
    return [r.model_id for r in rows]


def effective_group_models(db: Session, group_id: int) -> set[str]:
    """分组实际可用的模型集合 = 组内模型 ∩ 当前启用模型。

    组内模型被禁用时自动排除；若系统未配置任何模型规则（向后兼容场景），
    则以组内列表为准（此时不存在"启用"概念）。
    """
    models = set(group_models(db, group_id))
    if not models:
        return set()
    configs = db.query(ModelConfig).all()
    if not configs:
        return models  # 无模型规则配置：不额外过滤
    enabled = {m.model_id for m in configs if m.enabled == 1}
    return models & enabled


def group_model_map(db: Session) -> dict[int, set[str]]:
    """一次性取出全部分组的有效模型集合，避免代理热路径上 N+1 查询。"""
    items = db.query(ModelGroupItem).all()
    by_group: dict[int, set[str]] = {}
    for it in items:
        by_group.setdefault(it.group_id, set()).add(it.model_id)
    if not by_group:
        return {}
    configs = db.query(ModelConfig).all()
    if not configs:
        return by_group
    enabled = {m.model_id for m in configs if m.enabled == 1}
    return {gid: (models & enabled) for gid, models in by_group.items()}


@router.get("")
def list_groups(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """分组列表，附带模型数与绑定的 Key 数。"""
    groups = db.query(ModelGroup).order_by(ModelGroup.id.desc()).all()
    key_counts: dict[int, int] = {}
    for k in db.query(ApiKey).all():
        gid = int(k.group_id or 0)
        if gid:
            key_counts[gid] = key_counts.get(gid, 0) + 1

    items = []
    for g in groups:
        raw = group_models(db, g.id)
        items.append({
            "id": g.id,
            "name": g.name,
            "note": g.note or "",
            "models": raw,
            "model_count": len(raw),
            "effective_count": len(effective_group_models(db, g.id)),
            "key_count": key_counts.get(g.id, 0),
            "created_at": g.created_at.isoformat() if g.created_at else None,
        })
    return {"items": items}


@router.post("")
def create_group(body: GroupIn, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="分组名称不能为空")
    if db.query(ModelGroup).filter(ModelGroup.name == name).first():
        raise HTTPException(status_code=409, detail=f"分组「{name}」已存在")
    if not body.models:
        raise HTTPException(status_code=400, detail="请至少选择一个模型")

    g = ModelGroup(name=name, note=body.note or "")
    db.add(g)
    db.flush()
    for mid in dict.fromkeys(body.models):  # 去重且保序
        db.add(ModelGroupItem(group_id=g.id, model_id=mid))
    db.commit()
    db.refresh(g)
    return {"id": g.id, "name": g.name, "model_count": len(set(body.models)), "ok": True}


@router.put("/{group_id}")
def update_group(group_id: int, body: GroupIn, _: bool = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """整体更新分组（名称 / 备注 / 模型列表）。

    模型列表为全量覆盖语义：传什么就是什么，便于前端多选框直接提交。
    """
    g = db.query(ModelGroup).filter(ModelGroup.id == group_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="分组不存在")

    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="分组名称不能为空")
    dup = db.query(ModelGroup).filter(ModelGroup.name == name, ModelGroup.id != group_id).first()
    if dup:
        raise HTTPException(status_code=409, detail=f"分组「{name}」已存在")

    if not body.models:
        raise HTTPException(status_code=400, detail="请至少选择一个模型")

    g.name = name
    g.note = body.note or ""
    db.query(ModelGroupItem).filter(ModelGroupItem.group_id == group_id).delete()
    for mid in dict.fromkeys(body.models):
        db.add(ModelGroupItem(group_id=group_id, model_id=mid))
    db.commit()
    return {"id": g.id, "ok": True}


@router.delete("/{group_id}")
def delete_group(group_id: int, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """删除分组。绑定了该分组的 Key 会自动降级为「不限制」（group_id 置 0）。"""
    g = db.query(ModelGroup).filter(ModelGroup.id == group_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="分组不存在")
    unbound = (
        db.query(ApiKey)
        .filter(ApiKey.group_id == group_id)
        .update({ApiKey.group_id: 0}, synchronize_session=False)
    )
    db.query(ModelGroupItem).filter(ModelGroupItem.group_id == group_id).delete()
    db.delete(g)
    db.commit()
    return {"id": group_id, "ok": True, "unbound_keys": int(unbound or 0)}