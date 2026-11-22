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
from admin.security import require_admin, clean_display_name

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


def _split_realm(model_id: str) -> tuple[str, str]:
    """拆出 (名字, 区)：xxx:intl -> ("xxx", "intl")；没有区后缀时区是空串。

    只有 :cn / :intl 算区域标记（与 models_router._entry_targets 的孪生规则
    同口径）；别的冒号内容属于模型名本身，原样保留。
    """
    text = str(model_id or "")
    if ":" not in text:
        return text, ""
    base, _, suffix = text.rpartition(":")
    suffix = suffix.strip().lower()
    if suffix in ("cn", "intl"):
        return base, suffix
    return text, ""


def _normalized_members(members: set[str], rows) -> set[str]:
    """把组内登记的写法归一到**同一平台、同一区**的目录行拼写。

    ## 为什么需要（F6）

    控制台展示的是可读名/展示名（DeepSeek-V4.1-Flash），管理员手填或从别处
    粘贴时大小写经常与库里那行（deepseek-v4.1-flash）不一致。而分组授权、
    目录可见性都是拿组内字符串与 model_configs.model_id 做**精确**集合交集
    —— 一不一致整组就变空，表现为这个 Key 对所有模型 403 model_not_in_group。

    ## 归一规则（严格「同行归一」，不跨平台、不跨区）

      * **原串相等优先**：成员与某一行逐字相同（含区后缀写法）时，只认这些行，
        绝不再按大小写把**别家平台**的同名行带进来 —— 这是 t16 的越权判据
        （alpha 的 demo-model 与 beta 的 DEMO-MODEL 是两条不同的登记）。
      * 只有整张目录里都**找不到原串相等的行**时，才退一步按大小写不敏感匹配；
        命中多个平台时按 storage 名排序取第一家（确定性），仍只取那一家自己的
        拼写，不做多平台并集。
      * 区语义照 :intl 规则：写 xxx:intl 的成员只认国际行，写 xxx（无后缀）
        的成员只认国内行 —— 孪生行不互相顶替。
      * 目录里完全找不到的成员**原样保留**，随后与「启用」求交时自然被丢掉
        —— 既不会放大成「全放行」，也不会把整组清空。
    """
    if not members:
        return set()
    out: set[str] = set()
    for member in members:
        text = str(member or "")
        base, realm = _split_realm(text)
        want_intl = realm == "intl"
        lowered = base.strip().lower()
        exact: set[str] = set()
        folded: dict[str, set[str]] = {}
        for row in rows or []:
            native = str(row[1] or "")
            if not native:
                continue
            row_base, row_realm = _split_realm(native)
            # 区语义：:intl 成员只认国际行，无后缀成员只认国内行
            if (row_realm == "intl") != want_intl:
                continue
            if row_base.strip().lower() != lowered:
                continue
            if native == text:
                exact.add(native)
            folded.setdefault(str(row[0] or "").lower(), set()).add(native)
        if exact:
            # 原串相等优先：绝不按大小写把别家平台的同名行也放进来
            out.update(exact)
            continue
        if folded:
            # 目录里没有逐字相同的那行：只取一家（按 storage 名排序，确定性）
            out.update(folded[sorted(folded)[0]])
            continue
        # 目录里完全没有：原样保留，交给后面的「启用」求交按既有语义丢弃
        out.add(text)
    return out


def effective_group_models(db: Session, group_id: int) -> set[str]:
    """分组实际可用的模型集合 = 组内模型（归一到目录拼写）∩ 当前启用模型。

    组内模型被禁用时自动排除；若系统未配置任何模型规则（向后兼容场景），
    则以组内列表为准（此时不存在"启用"概念）。

    组内登记的字符串先经 _normalized_members() 归一到目录行的**原始拼写**
    （大小写不敏感、同平台同区，见该函数的规则），否则大小写不一致会让整个
    分组变空。归一与「启用」判定共用同一次列查询，不额外多扫表。
    """
    models = set(group_models(db, group_id))
    if not models:
        return set()
    # 只取三列：既算「启用」集合，也做成员归一。这里原本就是
    # db.query(ModelConfig).all()，现在不再物化 TEXT 元数据。
    rows = (db.query(ModelConfig.platform, ModelConfig.model_id,
                     ModelConfig.enabled).all())
    if not rows:
        return models  # 无模型规则配置：不额外过滤
    enabled = {str(model_id) for _platform, model_id, flag in rows
               if int(flag or 0) == 1}
    return _normalized_members(models, rows) & enabled



def group_model_map(db: Session) -> dict[int, set[str]]:
    """一次性取出全部分组的有效模型集合，避免代理热路径上 N+1 查询。

    ⚠️ **当前没有任何调用方**，而且这里仍是**精确字符串交集**：它不经过
    _normalized_members()，所以「组内登记写法和目录行只差大小写」时会得到
    空集合（F6 的老症状）。将来启用它之前必须改成复用 effective_group_models()
    的归一逻辑（或直接逐组调用它），不要照着这份实现另写一份 —— 否则 F6 会
    从新入口原样重现。
    """
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

    g.name = clean_display_name(name, g.name)
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