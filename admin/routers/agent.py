"""内置对话智能体：`/api/agent/*`。

与 `/v1/*` 的区别
-----------------
`/v1/*` 是**对外网关**：无状态、每个请求自带完整上下文、面向第三方客户端。
智能体是**内置页面**：有会话状态、工具能力、免登录（免填 Key），
并且直接复用后台已登录的会话与 Key 权益。

因此它挂在 `/api/agent` 下（后台路由，需要会话 Cookie 或 X-Admin-Token），
而不是 `/v1`。这样「token 共享、免登录」不是靠放宽鉴权实现的，
而是**复用后台已有的登录态** —— 安全边界清楚，也不需要再造一套凭据。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
import time

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from admin import agent as agent_meta
from admin import media_service
from admin.db import SessionLocal, get_db
from admin.models import Account, AgentAsset, AgentConversation, ApiKey, ModelConfig, UsageLog
from admin.request_scheduling import release_connection
from admin.security import require_admin
from model_metadata import is_image_model, is_video_model, is_video_model_id

_logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/agent', tags=['agent'])

#: 供智能体选择的模型能力过滤。工具集只在对应能力存在时才注入，
#: 否则模型会看到「生视频工具」却没有视频模型，白白浪费一次工具调用。
MAX_CONTEXT_MESSAGES = 400


# ---------------------------------------------------------------------------
# 会话与 Key
# ---------------------------------------------------------------------------

def _default_key(db: Session) -> ApiKey | None:
    """智能体用哪个 Key 的权益。

    「免登录、token 共享」的真实含义是：**管理后台已经登录**，所以直接用
    池子里第一个启用中的 Key 的配额与分组，用户不必再填一次 Key。
    优先选无限额度且未绑定分组的公共 Key —— 它对账号池的可见范围最完整。
    """
    rows = db.query(ApiKey).filter(ApiKey.status == 'active').all()
    if not rows:
        return None
    def rank(key: ApiKey):
        return (0 if key.unlimited else 1,
                0 if not (key.group_id or 0) else 1,
                0 if getattr(key, 'account_id', None) is None else 1,
                key.id)
    return sorted(rows, key=rank)[0]


def _conversation(db: Session, key: ApiKey, conversation_id: str | None) -> AgentConversation:
    if conversation_id:
        row = db.query(AgentConversation).filter(
            AgentConversation.conversation_id == str(conversation_id)).first()
        if row is not None:
            return row
    row = AgentConversation(
        conversation_id=agent_meta.new_id('conv'),
        api_key_id=key.id if key else 0,
        title='', model='', mode='chat', messages='[]')
    db.add(row)
    db.commit()
    return row


def _load_messages(row: AgentConversation) -> list[dict]:
    try:
        value = json.loads(row.messages or '[]')
    except (ValueError, TypeError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _save_messages(db: Session, row: AgentConversation, messages: list[dict]) -> None:
    payload = messages[-MAX_CONTEXT_MESSAGES:]
    row.messages = json.dumps(payload, ensure_ascii=False)
    if not row.title:
        for item in payload:
            if item.get('role') == 'user' and isinstance(item.get('content'), str):
                row.title = item['content'].strip()[:80]
                break
    db.commit()


# ---------------------------------------------------------------------------
# 模型目录
# ---------------------------------------------------------------------------

def _enabled_models(db: Session) -> list[ModelConfig]:
    rows = db.query(ModelConfig).filter(ModelConfig.enabled == 1).all()
    # 去重：历史库里有同一模型的多行（不同 level），对前端只应出现一次。
    seen: set[str] = set()
    out: list[ModelConfig] = []
    for row in rows:
        if row.model_id in seen:
            continue
        seen.add(row.model_id)
        out.append(row)
    return sorted(out, key=lambda row: row.model_id)


def _video_rows(db: Session, rows) -> list[dict]:
    out = []
    for row in rows:
        if not is_video_model_id(row.model_id):
            continue
        capability = media_service.video_capability(db, row.model_id)
        out.append({
            'id': row.model_id,
            'name': row.note or row.model_id,
            'resolutions': capability.get('resolutions') or [],
            'default_resolution': capability.get('defaultResolution') or '',
            'aspect_ratios': capability.get('aspectRatios') or [],
            'default_aspect_ratio': capability.get('defaultAspectRatio') or '',
            'min_seconds': capability.get('minSeconds'),
            'max_seconds': capability.get('maxSeconds'),
            'default_seconds': capability.get('defaultSeconds'),
            'supported_seconds': capability.get('supportedSeconds') or [],
            'audio_mode': capability.get('audioMode') or '',
            'default_enable_audio': capability.get('defaultEnableAudio'),
        })
    return out


@router.get('/bootstrap')
def bootstrap(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """智能体首屏需要的全部静态信息（一次请求搞定，避免竞态）。"""
    key = _default_key(db)
    rows = _enabled_models(db)
    text_models = [row.model_id for row in rows if not is_image_model(row.model_id)
                   and not is_video_model({'model_id': row.model_id})]
    image_models = [row.model_id for row in rows if is_image_model(row.model_id)]
    video_rows = _video_rows(db, rows)
    return {
        'key': ({'id': key.id, 'name': key.name,
                 'unlimited': bool(key.unlimited),
                 'credit_limit': key.credit_limit, 'credit_used': key.credit_used}
                if key else None),
        'models': {'chat': text_models, 'image': image_models,
                   'video': [row['id'] for row in video_rows]},
        'video': video_rows,
        'estimates': {
            'video_per_second': media_service.VIDEO_CREDITS_PER_SECOND,
            'video_factors': media_service.VIDEO_RESOLUTION_FACTORS,
            'image_per_unit': media_service.IMAGE_CREDITS_PER_UNIT,
        },
        'merge': media_service.video_merge.describe(),
        'presets': PRESETS,
        'limits': {'max_tool_steps': agent_meta.MAX_TOOL_STEPS,
                   'context_budget': agent_meta.CONTEXT_TOKEN_BUDGET},
    }


# ---------------------------------------------------------------------------
# 预设：风格 / 镜头 / 剧本骨架
# ---------------------------------------------------------------------------

#: 这些预设是「提示词模板」，选中后插入输入框而不是直接发送 ——
#: 用户几乎总要改几个词，直接发出去反而要多一步撤销。
PRESETS = {
    'image': [
        {'id': 'photo', 'label': '写实摄影', 'icon': 'fa-camera', 'size': '1024x1024',
         'prompt': '写实摄影风格，自然光，浅景深，细节锐利，35mm 镜头，{主题}'},
        {'id': 'illustration', 'label': '日系插画', 'icon': 'fa-palette', 'size': '1024x1024',
         'prompt': '日系清新插画风格，柔和配色，干净的线条，宫崎骏式光影，{主题}'},
        {'id': 'cinematic', 'label': '电影感', 'icon': 'fa-film', 'size': '1280x720',
         'prompt': '电影感画面，宽银幕构图，戏剧性侧逆光，体积雾，胶片颗粒，{主题}'},
        {'id': 'product', 'label': '产品级', 'icon': 'fa-box', 'size': '1024x1024',
         'prompt': '高端产品摄影，无影棚柔光，纯净背景，材质纹理清晰，商业广告质感，{主题}'},
        {'id': 'poster', 'label': '海报', 'icon': 'fa-image', 'size': '1024x1536',
         'prompt': '创意海报设计，强对比色块，几何构成，留白得当，视觉中心突出，{主题}'},
        {'id': 'ink', 'label': '国风水墨', 'icon': 'fa-feather', 'size': '1024x1024',
         'prompt': '中国水墨画风格，留白意境，墨色浓淡变化，宣纸质感，{主题}'},
    ],
    'video': [
        {'id': 'cat', 'label': '萌宠日常', 'icon': 'fa-cat', 'seconds': 5,
         'resolution': '720P', 'aspect_ratio': '16:9',
         'prompt': '一只橘猫在午后阳光里的木地板上伸懒腰，镜头缓慢推近，暖色调，浅景深，{主题}'},
        {'id': 'landscape', 'label': '风光航拍', 'icon': 'fa-mountain', 'seconds': 8,
         'resolution': '1080P', 'aspect_ratio': '16:9',
         'prompt': '无人机航拍，晨雾中的山峦与云海，镜头缓慢前推并有微幅横移，电影级调色，{主题}'},
        {'id': 'portrait', 'label': '人像运镜', 'icon': 'fa-user', 'seconds': 5,
         'resolution': '1080P', 'aspect_ratio': '9:16',
         'prompt': '竖屏人像特写，柔和的窗光，镜头轻微手持晃动，眼神自然看向镜头，{主题}'},
        {'id': 'anime', 'label': '动漫分镜', 'icon': 'fa-star', 'seconds': 5,
         'resolution': '720P', 'aspect_ratio': '16:9',
         'prompt': '日式动漫分镜，动态线条，速度感，高饱和色彩，镜头快速掠过，{主题}'},
        {'id': 'product_video', 'label': '产品展示', 'icon': 'fa-box', 'seconds': 5,
         'resolution': '1080P', 'aspect_ratio': '16:9',
         'prompt': '产品悬浮旋转展示，纯净渐变背景，棚拍补光，镜头环绕，高级质感，{主题}'},
        {'id': 'city', 'label': '城市夜景', 'icon': 'fa-city', 'seconds': 6,
         'resolution': '1080P', 'aspect_ratio': '16:9',
         'prompt': '城市夜景延时，车流光轨，霓虹倒映在湿漉漉的街道上，镜头缓慢上升，{主题}'},
    ],
}


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

def _build_registry(db: Session, key: ApiKey | None, context_holder: dict):
    """按当前可用模型装配工具集。

    刻意做成「按能力注入」：如果池子里没有视频模型，就不暴露生视频工具。
    模型看到不存在的工具会反复试探，既浪费 token 也浪费时间。
    """
    registry = agent_meta.ToolRegistry()
    rows = _enabled_models(db)
    chat_models = [row.model_id for row in rows
                   if not is_image_model(row.model_id)
                   and not is_video_model({'model_id': row.model_id})]
    image_models = [row.model_id for row in rows if is_image_model(row.model_id)]
    video_models = [row.model_id for row in rows if is_video_model_id(row.model_id)]

    def model_list(values):
        return {'type': 'string', 'enum': values[:24]} if values else {'type': 'string'}

    if image_models:
        def run_image(context, prompt, model='', size=''):
            chosen = model if model in image_models else image_models[0]
            if not (prompt or '').strip():
                return {'ok': False, 'error': 'prompt 不能为空'}
            return media_service.generate_image(
                db, api_key_id=context.api_key_id, model=chosen, prompt=prompt,
                size=size, conversation_id=context.conversation_id)
        registry.add(agent_meta.Tool(
            'generate_image',
            '根据文字描述生成一张图片。适合封面、插图、海报、产品图。'
            '返回 asset_id，前端会直接展示图片，不要在回答里粘贴 base64。',
            {'type': 'object',
             'properties': {
                 'prompt': {'type': 'string', 'description': '画面描述，越具体越好'},
                 'model': model_list(image_models),
                 'size': {'type': 'string', 'description': '可选尺寸，例如 1024x1024'},
             },
             'required': ['prompt']},
            run_image, label='生成图片'))

    if video_models:
        def run_video(context, prompt, model='', seconds=None, resolution='',
                      aspect_ratio='', audio=None):
            chosen = model if model in video_models else video_models[0]
            if not (prompt or '').strip():
                return {'ok': False, 'error': 'prompt 不能为空'}
            # 生视频动辄几十秒到几分钟，必须让前端能显示「正在生成」。
            context.emit({'type': 'notice', 'text': f'正在生成视频（{chosen}），通常需要 1~5 分钟…'})
            return media_service.generate_video(
                db, api_key_id=context.api_key_id, model=chosen, prompt=prompt,
                seconds=seconds, size=resolution, aspect_ratio=aspect_ratio,
                audio=audio, conversation_id=context.conversation_id,
                should_cancel=context.cancelled)
        registry.add(agent_meta.Tool(
            'generate_video',
            '根据文字描述生成一段短视频（单段通常不超过 15 秒）。'
            '要更长的成片请分段生成后用 merge_videos 合并。'
            '该工具会阻塞到视频生成完成，耗时可能几分钟。',
            {'type': 'object',
             'properties': {
                 'prompt': {'type': 'string', 'description': '分镜描述，包含主体、动作、镜头与光线'},
                 'model': model_list(video_models),
                 'seconds': {'type': 'integer', 'description': '时长（秒）'},
                 'resolution': {'type': 'string', 'description': '分辨率，如 720P / 1080P'},
                 'aspect_ratio': {'type': 'string', 'description': '画面比例，如 16:9 / 9:16'},
             },
             'required': ['prompt']},
            run_video, label='生成视频'))

    def run_merge(context, asset_ids, prompt=''):
        return media_service.merge_assets(
            db, api_key_id=context.api_key_id, asset_ids=asset_ids,
            conversation_id=context.conversation_id, prompt=prompt)
    registry.add(agent_meta.Tool(
        'merge_videos',
        '把多个已生成的视频片段按给定顺序合并成一条长视频（本地 ffmpeg 无损拼接）。'
        '用于把分镜片段接成完整成片。',
        {'type': 'object',
         'properties': {
             'asset_ids': {'type': 'array', 'items': {'type': 'string'},
                           'description': '按播放顺序排列的 asset_id 列表'},
             'prompt': {'type': 'string', 'description': '成片标题或说明（可选）'},
         },
         'required': ['asset_ids']},
        run_merge, label='合并视频'))

    def run_plan(context, total_seconds, model=''):
        chosen = model if model in video_models else (video_models[0] if video_models else '')
        if not chosen:
            return {'ok': False, 'error': '当前没有可用的视频模型'}
        return {'ok': True, **media_service.plan_long_video(int(total_seconds), chosen, db),
                'model': chosen}
    registry.add(agent_meta.Tool(
        'plan_long_video',
        '把「想要的总时长」拆成多个不超过模型上限的分镜时长，并预估总积分。'
        '在要求长视频（>15 秒）时先用它做规划，再逐段生成。',
        {'type': 'object',
         'properties': {
             'total_seconds': {'type': 'integer', 'description': '目标总时长（秒）'},
             'model': model_list(video_models),
         },
         'required': ['total_seconds']},
        run_plan, label='规划长视频'))

    def run_models(context, kind='all'):
        return {'ok': True, 'chat': chat_models[:40], 'image': image_models[:40],
                'video': video_models[:40], 'merge_available': media_service.video_merge.available(),
                'summary': f'{len(chat_models)} 个对话模型 · {len(image_models)} 个生图模型 · '
                           f'{len(video_models)} 个视频模型'}
    registry.add(agent_meta.Tool(
        'list_models',
        '列出当前可用的对话 / 生图 / 视频模型。不确定有哪些模型时先调用它。',
        {'type': 'object', 'properties': {
            'kind': {'type': 'string', 'enum': ['all', 'chat', 'image', 'video']}}},
        run_models, label='查询可用模型'))

    def run_assets(context, kind='', limit=20):
        items = media_service.list_assets(db, api_key_id=context.api_key_id,
                                         kind=kind, limit=int(limit or 20))
        return {'ok': True, 'items': items, 'count': len(items),
                'summary': f'最近 {len(items)} 个产出'}
    registry.add(agent_meta.Tool(
        'list_assets',
        '列出本次会话（同一 Key）已生成的图片与视频资产，可取到 asset_id 用于合并。',
        {'type': 'object', 'properties': {
            'kind': {'type': 'string', 'enum': ['', 'image', 'video']},
            'limit': {'type': 'integer'}}},
        run_assets, label='列出资产'))

    return registry


def _system_prompt(db: Session) -> str:
    rows = _enabled_models(db)
    chat = [row.model_id for row in rows if not is_image_model(row.model_id)
            and not is_video_model({'model_id': row.model_id})]
    images = [row.model_id for row in rows if is_image_model(row.model_id)]
    videos = [row.model_id for row in rows if is_video_model_id(row.model_id)]
    merge_ok = media_service.video_merge.available()
    lines = [
        '你是 MiraiHub 管理后台内置的创作助手，可以直接生成图片和视频。',
        '',
        '## 当前能力',
        f'- 生图模型：{"、".join(images[:8]) if images else "无"}',
        f'- 视频模型：{"、".join(videos[:8]) if videos else "无"}',
        f'- 本地视频合并：{"可用" if merge_ok else "不可用（缺少 ffmpeg）"}',
        '',
        '## 行为准则',
        '1. 用户要图/视频时**直接调用工具**，不要只描述你打算怎么做。',
        '2. 生图/生视频的 prompt 要具体：主体、动作、环境、光线、镜头、风格。',
        '3. 视频单段上限 15 秒。用户要更长的成片时，先用 plan_long_video 规划分镜，',
        '   逐个 generate_video，最后用 merge_videos 按顺序合并。',
        '4. 生成完成后用一两句话说明结果（模型、时长、积分），不要复述整段 prompt。',
        '5. 工具返回 ok=false 时，读懂 error 后换参数或换模型重试一次；不要盲目重复同一调用。',
        '6. 你无法访问外网、不能读写用户本机文件、不能代替用户付款。',
        '7. 回答用中文，简洁、直接，不要堆砌免责声明。',
    ]
    if chat:
        lines.append('')
        lines.append('## 可用对话模型')
        lines.append('、'.join(chat[:20]))
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# 模型调用（内部走自己的 /v1 网关，复用池化/重试/计费）
# ---------------------------------------------------------------------------

async def _call_model(db, key: ApiKey, model: str, messages: list[dict],
                      tools: list[dict] | None, temperature: float = 0.7,
                      max_tokens: int = 4096, reasoning: str = '',
                      thinking=None, on_delta=None) -> dict:
    """调用对话补全，返回 assistant 消息（含 tool_calls）。

    为什么自己实现而不调 `/v1/chat/completions`：账号轮转、冷却、计费、
    错误分类全都在 `proxy` 里，重写一份必然漂移；但走 HTTP 又要多一跳
    本机连接并把 SSE 再解析一遍。折中是**复用 proxy 的同步原语**、
    在本进程内完成流式转发（见 `_stream_chat`），语义与外部调用一致。

    `reasoning`：思考档位（low/medium/high/max）。`thinking`：True/False
    显式开关思考（模型支持时）。两者都为空则沿用模型默认。
    `on_delta(kind, text)`：收到增量时回调，用于真正的流式显示。
    """
    body = {
        'model': model,
        'messages': messages,
        'stream': False,
        'temperature': temperature,
        'max_tokens': max_tokens,
    }
    if reasoning:
        body['reasoning_effort'] = reasoning
    if thinking is not None:
        body['thinking'] = {'type': 'enabled' if thinking else 'disabled'}
    if tools:
        body['tools'] = tools
        body['tool_choice'] = 'auto'
    return await _stream_chat(db, key, body, on_delta=on_delta)


async def _stream_chat(db, key: ApiKey, body: dict, on_delta=None) -> dict:
    """执行一次对话补全，**边收边回调**，最后返回聚合后的 assistant 消息。

    为什么不用「同步收完再返回整段」：智能体页面的价值有一半在「能看见它
    在想、在写」。整段返回意味着用户要盯着转圈等十几秒。这里在收到每个 SSE
    块时立刻把 `content` / `reasoning_content` 增量交给回调，前端才能真的
    流式显示。

    重试语义与 `/v1/chat/completions` 一致：只对**明确的账号/配额拒绝**换号
    重试。一旦已经向客户端吐过增量就不再重试 —— 否则会把两段不同的回答拼在
    一起；此时把错误原样返回。
    """
    from admin import upstream_identity as identity
    from admin.backend import AccountSession
    from admin.routers import proxy

    model = body.get('model')
    outbound = dict(body)
    outbound['stream'] = True
    outbound.setdefault('stream_options', {})['include_usage'] = True
    # 内嵌智能体也是一次真实的上游调用：报出归属与用途，并给每次尝试独立的
    # trace（上游按 X-Conversation-Request-ID 去重，复用会让后续调用不落账）。
    agent_purpose = identity.purpose_for('agent')
    agent_conversation = proxy.pool.extract_session_key(outbound)
    agent_turn = identity.turn_id(outbound)

    budget = proxy.pool_budget(db, key, [model])
    tried: set[int] = set()
    last = '没有可用账号'
    emitted = False

    def deliver(kind, text):
        nonlocal emitted
        if not on_delta or not text:
            return
        on_delta(kind, text)
        emitted = True

    while budget.available():
        acc = proxy._select_account(db, exclude_ids=tried, model=model,
                                    account_id=getattr(key, 'account_id', None), key_id=key.id)
        if acc is None:
            break
        if acc.uid:
            tried.update(row[0] for row in
                         db.query(Account.id).filter(Account.uid == acc.uid).all())
        tried.add(acc.id)
        budget.consume()
        session = None
        try:
            session = AccountSession(acc.auth_json)
            agent_extra = identity.identity_headers(agent_purpose)
            agent_extra.update(identity.trace_headers(agent_conversation, agent_turn))
            session._gateway_headers = session.get_headers(extra=agent_extra)
            release_connection(db)
            chunks: list[str] = []
            total = 0
            async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=20),
                                         limits=proxy.HTTP_LIMITS) as client:
                async with client.stream('POST', proxy.settings.BACKEND + '/v2/chat/completions',
                                         headers=session._gateway_headers,
                                         json=outbound) as response:
                    if response.status_code >= 400:
                        detail = (await response.aread()).decode('utf-8', 'replace')[:65536]
                        last = detail[:200]
                        kind = proxy._classify_error(response.status_code, detail)
                        proxy._apply_account_policy(db, acc, kind, response.status_code, detail,
                                                    model=model)
                        continue
                    buffer = ''
                    async for piece in response.aiter_text():
                        buffer += piece
                        total += len(piece)
                        if total > 4_000_000:
                            raise RuntimeError('模型响应超过 4 MB 上限')
                        while '\n\n' in buffer:
                            event, buffer = buffer.split('\n\n', 1)
                            chunks.append(event + '\n\n')
                            _emit_delta(event, deliver)
            if buffer.strip():
                chunks.append(buffer + '\n\n')
                _emit_delta(buffer, deliver)
            text = ''.join(chunks)
            if proxy._looks_like_inband_error(text):
                last = text[:200]
                if emitted:
                    return {'ok': False, 'error': last,
                            'message': {'role': 'assistant', 'content': ''}}
                continue
            message = proxy._aggregate_chat_sse(text)['choices'][0]['message']
            usage = proxy._parse_usage(text)
            credits = usage.get('credits')
            proxy._note_success(db, acc)
            proxy._record_usage(key.id, acc.id, model, credits, None,
                                use_case='agent/chat', error_kind='success',
                                prompt_tokens=usage.get('prompt_tokens'),
                                completion_tokens=usage.get('completion_tokens'),
                                total_tokens=usage.get('total_tokens'))
            return {'ok': True, 'message': message, 'usage': usage or {}, 'model': model}
        except Exception as exc:
            last = str(exc)
            if emitted:
                return {'ok': False, 'error': last,
                        'message': {'role': 'assistant', 'content': ''}}
            continue
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
    return {'ok': False, 'error': last, 'message': {'role': 'assistant', 'content': ''}}


def _emit_delta(event_block: str, deliver) -> None:
    """把一个 SSE 事件块里的增量交给 deliver(kind, text)。

    `deliver` 抛异常时必须向外传播：那是「客户端已断开」的唯一信号，
    吞掉它会变成「客户端走了还在烧积分」。
    """
    data_lines = [line[5:].strip() for line in event_block.splitlines()
                  if line.startswith('data:')]
    if not data_lines:
        return
    payload = '\n'.join(data_lines)
    if not payload or payload == '[DONE]':
        return
    try:
        chunk = json.loads(payload)
    except (ValueError, TypeError):
        return
    if not isinstance(chunk, dict):
        return
    reasoning: list[str] = []
    content: list[str] = []
    for choice in chunk.get('choices') or []:
        if not isinstance(choice, dict):
            continue
        delta = choice.get('delta') or {}
        value = delta.get('reasoning_content')
        if isinstance(value, str) and value:
            reasoning.append(value)
        value = delta.get('content')
        if isinstance(value, str) and value:
            content.append(value)
    if reasoning:
        deliver('reasoning', ''.join(reasoning))
    if content:
        deliver('content', ''.join(content))


# ---------------------------------------------------------------------------
# 主循环（SSE）
# ---------------------------------------------------------------------------

def _sse(event: dict) -> bytes:
    return ('data: ' + json.dumps(event, ensure_ascii=False) + '\n\n').encode()


@router.post('/chat')
async def chat(request: Request, _: bool = Depends(require_admin),
               db: Session = Depends(get_db)):
    """一轮智能体对话（SSE 流式返回事件）。

    事件类型：
      `meta`      会话/模型等元信息
      `delta`     回答正文增量
      `reasoning` 思考增量（若有）
      `tool_start` / `tool_end`  工具调度过程
      `notice`    阶段性提示（例如「正在生成视频」）
      `done`      本轮结束（含积分与消息数）
      `error`     失败
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={'detail': '无效的 JSON 请求'})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={'detail': '请求体必须为 JSON 对象'})
    text = str(body.get('message') or '').strip()
    if not text:
        return JSONResponse(status_code=400, content={'detail': '消息不能为空'})

    key = _default_key(db)
    if key is None:
        return JSONResponse(status_code=503,
                            content={'detail': '还没有可用的 API Key，请先在「API Keys」创建'})
    row = _conversation(db, key, body.get('conversation_id'))
    model = str(body.get('model') or '').strip()
    mode = str(body.get('mode') or 'chat').strip() or 'chat'
    rows = _enabled_models(db)
    available = [item.model_id for item in rows
                 if not is_image_model(item.model_id)
                 and not is_video_model({'model_id': item.model_id})]
    if not model or model not in available:
        model = available[0] if available else ''
    if not model:
        return JSONResponse(status_code=503, content={'detail': '没有已启用的对话模型'})

    # 前端随消息带来的生成参数（生图尺寸 / 视频时长与分辨率）。
    hints = {}
    for key_name in ('image', 'video'):
        value = body.get(key_name)
        if isinstance(value, dict):
            hints[key_name] = {str(k): v for k, v in value.items()
                               if k in ('model', 'size', 'resolution', 'seconds', 'aspect')}

    # 思考控制：档位（low/medium/high/max）与显式开关。
    # 两个都要**落库**：刷新页面、换设备回来都应当还是同一套设置，
    # 否则「我明明选了深思考，刷新后变回默认」会让人怀疑设置没生效。
    reasoning = str(body.get('reasoning') or '').strip().lower()
    if reasoning not in ('', 'low', 'medium', 'high', 'xhigh', 'max', 'minimal'):
        reasoning = ''
    thinking = body.get('thinking')
    if thinking not in (True, False):
        thinking = None

    row.model = model
    row.mode = mode
    row.reasoning = reasoning
    row.thinking = None if thinking is None else int(thinking)
    db.commit()

    return StreamingResponse(
        _run_turn(request, db, key, row, text, model, mode, hints,
                  reasoning=reasoning, thinking=thinking),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


def _parameter_hint(mode: str, hints: dict) -> str:
    """把用户在右侧面板选的参数写成一句提示，附在用户消息前面。

    为什么不直接塞进 system prompt：参数是**本轮**的，而 system 是全会话的。
    混进去会让模型以为上一轮的时长这一轮还生效。
    """
    if not isinstance(hints, dict) or not hints:
        return ''
    parts: list[str] = []
    video = hints.get('video') if isinstance(hints.get('video'), dict) else {}
    image = hints.get('image') if isinstance(hints.get('image'), dict) else {}
    if mode == 'video' or video:
        if video.get('model'):
            parts.append(f"视频模型={video['model']}")
        if video.get('resolution'):
            parts.append(f"分辨率={video['resolution']}")
        if video.get('seconds'):
            parts.append(f"时长={video['seconds']}秒")
        if video.get('aspect'):
            parts.append(f"比例={video['aspect']}")
    if mode == 'image' or image:
        if image.get('model'):
            parts.append(f"生图模型={image['model']}")
        if image.get('size'):
            parts.append(f"尺寸={image['size']}")
    if not parts:
        return ''
    return f"（本轮参数：{'，'.join(parts)}。请调用对应工具时使用这些参数。）\n"


async def _run_turn(request, db, key, row, text, model, mode, hints=None,
                     reasoning='', thinking=None):
    """执行一轮：模型 ↔ 工具循环，**边生成边推事件**。

    事件顺序（前端据此渲染）：
        meta → reasoning* → delta* → tool_start → tool_end → media → … → done

    `reasoning` / `thinking` 来自前端左下角的开关：用户能自己决定要不要
    看模型思考、思考多深。推理型模型（池子里大部分是 `onlyReasoning`）
    不接受关闭，此时静默忽略 `thinking=False` 而不是报错。
    """
    state = {'cancelled': False}

    def cancelled():
        return state['cancelled']

    messages = _load_messages(row)
    if not any(item.get('role') == 'system' for item in messages):
        messages.insert(0, {'role': 'system', 'content': _system_prompt(db)})
    # 用户在前端选的生成参数（尺寸/时长/分辨率）要随消息交给模型，
    # 否则它只能猜 —— 用户在右侧选了 1080P，模型却按默认 720P 生成，
    # 预估值和实际扣费就会对不上。
    hint = _parameter_hint(mode, hints or {})
    messages.append({'role': 'user', 'content': hint + text if hint else text})

    registry = _build_registry(db, key, state)
    tools = registry.specs()
    total_credits = 0.0
    # Token 用量同样来自 `_stream_chat` 解析的上游 usage（与写入
    # usage_logs 的是同一份数据），这里只做累加，**不另算一套**。
    total_prompt = 0
    total_completion = 0
    total_tokens = 0
    steps = 0
    # 增量要**边收边推**。回调是同步的，不能在里面 yield；而如果先把增量攒起来
    # 等整轮结束再吐，前端就只能一次性看到全文 —— 那跟非流式没有区别。
    # 所以用一个队列：模型调用作为独立 task 跑，主循环并发消费队列。
    queue: asyncio.Queue = asyncio.Queue()

    def on_delta(kind, piece):
        queue.put_nowait({'type': 'reasoning' if kind == 'reasoning' else 'delta',
                          'text': piece})

    try:
        yield _sse({'type': 'meta', 'conversation_id': row.conversation_id,
                    'model': model, 'mode': mode, 'reasoning': reasoning,
                    'thinking': thinking,
                    'context': _context_usage(db, messages),
                    'tools': [tool.name for tool in registry._tools.values()]})
        while steps <= agent_meta.MAX_TOOL_STEPS:
            steps += 1
            if await request.is_disconnected():
                state['cancelled'] = True
                return
            trimmed = agent_meta.trim_context(messages)
            dropped = len(messages) - len(trimmed)
            if dropped > 0:
                note = agent_meta.summarize_dropped(messages, trimmed)
                trimmed = list(trimmed)
                if note:
                    trimmed.insert(1 if trimmed and trimmed[0].get('role') == 'system' else 0,
                                   {'role': 'system', 'content': note})
            # 把模型调用放到 task 里跑，主循环并发消费队列 —— 这样每个 token
            # 到达就能立刻转成 SSE 事件，而不是等整轮结束。
            call = asyncio.create_task(
                _call_model(db, key, model, trimmed, tools,
                            reasoning=reasoning, thinking=thinking,
                            on_delta=on_delta))
            # 边收边推：**一个常驻的 queue.get() 任务 + wait(FIRST_COMPLETED)**。
            #
            # 之前写成「每轮新建再取消 getter」，取消时机会与 on_delta 的
            # put_nowait 竞争，实测丢掉首个事件之后的全部增量（前端只收到
            # meta，然后整轮空白）。这里 getter 只在**成功取到一条**之后才
            # 重建，杜绝取消竞争。
            getter = asyncio.create_task(queue.get())
            while True:
                done, _pending = await asyncio.wait(
                    {call, getter}, return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    yield _sse(getter.result())
                    getter = asyncio.create_task(queue.get())
                    # 调用已结束且队列取空 → 收尾
                    if call.done() and queue.empty():
                        break
                    continue
                break
            getter.cancel()
            try:
                await getter
            except asyncio.CancelledError:
                pass
            while not queue.empty():
                yield _sse(queue.get_nowait())
            result = call.result()
            if not result.get('ok'):
                if not result.get('message', {}).get('content'):
                    yield _sse({'type': 'error',
                                'message': result.get('error') or '模型调用失败'})
                    return
            message = result.get('message') or {}
            usage = result.get('usage') or {}
            total_credits += float(usage.get('credits') or 0)
            total_prompt += int(usage.get('prompt_tokens') or 0)
            total_completion += int(usage.get('completion_tokens') or 0)
            total_tokens += int(usage.get('total_tokens') or 0)
            content = message.get('content') or ''
            reasoning_text = message.get('reasoning_content') or ''
            # 思考内容必须**一起落库**：设计要求刷新后还在。
            # 只存 content 的话，`/conversations/{id}` 回来的历史里没有思考，
            # 刷新后思考块自然就没了。
            entry = {'role': 'assistant', 'content': content or None}
            if reasoning_text:
                entry['reasoning_content'] = reasoning_text
            if message.get('tool_calls'):
                entry['tool_calls'] = message['tool_calls']
            # 本轮用量存进消息里：重新打开会话时用量行还能显示，
            # 否则「刷新后用量消失」会让人以为没记账。数值直接取上游
            # usage（与 usage_logs 同源），不做二次计算。
            if usage:
                entry['usage'] = {
                    'credits': round(float(usage.get('credits') or 0), 4),
                    'prompt_tokens': int(usage.get('prompt_tokens') or 0),
                    'completion_tokens': int(usage.get('completion_tokens') or 0),
                    'total_tokens': int(usage.get('total_tokens') or 0),
                }
            messages.append(entry)

            calls = message.get('tool_calls') or []
            if not calls:
                break
            context = agent_meta.AgentContext(
                api_key_id=key.id, conversation_id=row.conversation_id,
                mode=mode, model=model,
                on_event=lambda event: None, cancelled=cancelled)
            failed = False
            for call in calls:
                if state['cancelled'] or await request.is_disconnected():
                    state['cancelled'] = True
                    return
                function = call.get('function') or {}
                name = function.get('name') or ''
                events: list[dict] = []
                context.on_event = events.append
                outcome = await asyncio.to_thread(
                    agent_meta.dispatch_tool, registry, name, function.get('arguments'), context)
                for event in events:
                    yield _sse(event)
                # 媒体产出单独广播：前端要把它渲染成图片/视频卡片。
                # 不放进工具摘要，是因为大 JSON 会撑爆事件流。
                if outcome.get('ok') and outcome.get('asset_id'):
                    yield _sse({'type': 'media', 'asset': {
                        'asset_id': outcome['asset_id'],
                        'kind': outcome.get('kind') or 'image',
                        'model': outcome.get('model') or '',
                        'credits': outcome.get('credits') or 0,
                        'duration_s': outcome.get('duration_s') or 0,
                        'prompt': (outcome.get('prompt') or text)[:200],
                    }})
                failed = failed or not outcome.get('ok')
                messages.append({
                    'role': 'tool',
                    'tool_call_id': call.get('id') or agent_meta.new_id('call'),
                    'name': name,
                    'content': agent_meta._clip(json.dumps(outcome, ensure_ascii=False)),
                })
            if failed and steps > agent_meta.MAX_TOOL_STEPS - 1:
                # 连续失败又到了步数上限：停下来，别让模型无限重试烧积分。
                break
        _save_messages(db, row, messages)
        yield _sse({'type': 'done', 'credits': round(total_credits, 2),
                    'usage': {
                        'credits': round(total_credits, 2),
                        'prompt_tokens': total_prompt,
                        'completion_tokens': total_completion,
                        'total_tokens': total_tokens,
                    },
                    'steps': steps, 'conversation_id': row.conversation_id,
                    'messages': len(messages),
                    'context': _context_usage(db, messages)})
    except asyncio.CancelledError:
        state['cancelled'] = True
        try:
            _save_messages(db, row, messages)
        except Exception:
            pass
        raise
    except Exception as exc:
        _logger.exception('智能体对话失败')
        yield _sse({'type': 'error', 'message': str(exc)})


# ---------------------------------------------------------------------------
# 会话管理
# ---------------------------------------------------------------------------

def _context_usage(db, messages) -> dict:
    """当前上下文占用了多少（供前端显示「上下文已用 x%」）。

    口径刻意与 `agent_meta.trim_context` 一致：估算值来自同一套
    `estimate_tokens`，这样「进度条满了」和「历史开始被裁掉」是同一件事，
    用户不会看到「还有 40% 余量」却已经丢了早期消息。
    """
    budget = agent_meta.CONTEXT_TOKEN_BUDGET
    used = sum(agent_meta._message_tokens(message)
               for message in messages if isinstance(message, dict))
    return {
        'used_tokens': used,
        'budget_tokens': budget,
        'percent': min(100, round(used / budget * 100)) if budget else 0,
        'messages': len(messages),
    }


def _conversation_view(row: AgentConversation, messages=None) -> dict:
    payload = {
        'conversation_id': row.conversation_id,
        'title': row.title or '新会话',
        'model': row.model,
        'mode': row.mode,
        'reasoning': row.reasoning or '',
        'thinking': None if row.thinking is None else bool(row.thinking),
        'pinned': row.pinned_at is not None,
        'pinned_at': row.pinned_at.isoformat() if row.pinned_at else '',
        'updated_at': row.updated_at.isoformat() if row.updated_at else '',
        'created_at': row.created_at.isoformat() if row.created_at else '',
    }
    if messages is not None:
        payload['messages'] = messages
    return payload


@router.get('/conversations')
def list_conversations(_: bool = Depends(require_admin), db: Session = Depends(get_db),
                       limit: int = 40):
    key = _default_key(db)
    query = db.query(AgentConversation)
    if key is not None:
        query = query.filter(AgentConversation.api_key_id == key.id)
    # 置顶优先，其余按更新时间倒序。SQL 里用 `pinned_at IS NULL` 排序而不是
    # 在 Python 侧 sort：后者会在分页边界上出现「置顶会话被 limit 切掉」。
    rows = (query.order_by(AgentConversation.pinned_at.is_(None),
                           AgentConversation.pinned_at.desc(),
                           AgentConversation.updated_at.desc())
            .limit(max(1, min(100, limit))).all())
    return {'items': [_conversation_view(row) for row in rows]}


@router.get('/conversations/{conversation_id}')
def get_conversation(conversation_id: str, _: bool = Depends(require_admin),
                     db: Session = Depends(get_db)):
    row = db.query(AgentConversation).filter(
        AgentConversation.conversation_id == conversation_id).first()
    if row is None:
        raise HTTPException(404, '会话不存在')
    messages = _load_messages(row)
    payload = _conversation_view(row, messages)
    payload['context'] = _context_usage(db, messages)
    return payload


@router.patch('/conversations/{conversation_id}')
def update_conversation(conversation_id: str, body: dict,
                        _: bool = Depends(require_admin),
                        db: Session = Depends(get_db)):
    """重命名 / 置顶 / 取消置顶。只更新传了值的字段。"""
    row = db.query(AgentConversation).filter(
        AgentConversation.conversation_id == conversation_id).first()
    if row is None:
        raise HTTPException(404, '会话不存在')
    updated = []
    if 'title' in (body or {}):
        title = str(body.get('title') or '').strip()[:120]
        if not title:
            raise HTTPException(400, '标题不能为空')
        row.title = title
        updated.append('title')
    if 'pinned' in (body or {}):
        row.pinned_at = datetime.utcnow() if body.get('pinned') else None
        updated.append('pinned')
    if 'model' in (body or {}):
        row.model = str(body.get('model') or '')[:120]
        updated.append('model')
    if 'reasoning' in (body or {}):
        row.reasoning = str(body.get('reasoning') or '')[:16]
        updated.append('reasoning')
    if 'thinking' in (body or {}):
        value = body.get('thinking')
        row.thinking = None if value is None else int(bool(value))
        updated.append('thinking')
    if 'mode' in (body or {}):
        row.mode = str(body.get('mode') or 'chat')[:16]
        updated.append('mode')
    db.commit()
    return {'ok': True, 'updated': updated, **_conversation_view(row)}


@router.delete('/conversations/{conversation_id}')
def delete_conversation(conversation_id: str, _: bool = Depends(require_admin),
                        db: Session = Depends(get_db)):
    db.query(AgentConversation).filter(
        AgentConversation.conversation_id == conversation_id).delete(synchronize_session=False)
    db.commit()
    return {'ok': True}


# ---------------------------------------------------------------------------
# 资产
# ---------------------------------------------------------------------------

@router.get('/assets')
def list_assets(_: bool = Depends(require_admin), db: Session = Depends(get_db),
                kind: str = '', limit: int = 60):
    key = _default_key(db)
    if key is None:
        return {'items': []}
    return {'items': media_service.list_assets(db, api_key_id=key.id, kind=kind, limit=limit)}


@router.get('/assets/{asset_id}')
def asset_file(asset_id: str, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    key = _default_key(db)
    if key is None:
        raise HTTPException(404, '资产不存在')
    row = db.query(AgentAsset).filter(AgentAsset.asset_id == asset_id,
                                      AgentAsset.api_key_id == key.id).first()
    if row is None:
        raise HTTPException(404, '资产不存在')
    path = media_service.asset_path(row)
    if not path.is_file():
        raise HTTPException(404, '资产文件已过期或被清理')
    return FileResponse(path, media_type=row.mime or 'application/octet-stream',
                        headers={'Cache-Control': 'private, max-age=3600',
                                 'Content-Disposition': f'inline; filename="{row.file_name}"'})


# ---------------------------------------------------------------------------
# 预估 / 提示词增强 / 合并 / 规划
# ---------------------------------------------------------------------------

@router.post('/estimate')
def estimate(request_body: dict, _: bool = Depends(require_admin),
             db: Session = Depends(get_db)):
    """积分预估（仅提示，不参与记账）。"""
    kind = str((request_body or {}).get('kind') or 'video')
    if kind == 'image':
        return media_service.estimate_image_credits(
            int(request_body.get('count') or 1),
            request_body.get('size') or '', request_body.get('model') or '', db=db)
    seconds = request_body.get('seconds') or 0
    resolution = request_body.get('resolution') or ''
    model = request_body.get('model') or ''
    result = media_service.estimate_video_credits(seconds, resolution, model, db=db)
    total = request_body.get('total_seconds')
    if total:
        result['long'] = media_service.plan_long_video(int(total), model, db) if model else {}
    return result


@router.post('/enhance')
async def enhance(request_body: dict, request: Request,
                  _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """提示词增强：用对话模型把用户的草稿改写成更具体的创作提示词。

    这是「提示词增强按钮」背后的实现。刻意用**低温度 + 明确输出约束**，
    并要求只返回改写后的提示词本身 —— 否则模型很爱附赠一段解释，
    用户还得手动删掉。
    """
    draft = str((request_body or {}).get('prompt') or '').strip()
    if not draft:
        return {'ok': False, 'error': '请先输入提示词'}
    key = _default_key(db)
    if key is None:
        return {'ok': False, 'error': '还没有可用的 API Key'}
    kind = str(request_body.get('kind') or 'image')
    mode = str(request_body.get('mode') or 'chat')
    model = str(request_body.get('model') or '').strip()
    rows = _enabled_models(db)
    available = [item.model_id for item in rows
                 if not is_image_model(item.model_id)
                 and not is_video_model({'model_id': item.model_id})]
    if not available:
        return {'ok': False, 'error': '没有已启用的对话模型'}
    if model not in available:
        model = available[0]
    # 提示词增强只要一句结论，不需要长篇思考。池子里绝大多数模型是
    # `onlyReasoning=true`（思考不可关），会把预算全烧在 reasoning_content 上，
    # `content` 直接返回 null —— 实测正是这样。所以优先挑一个**能关思考**的模型；
    # 没有的话再退而求其次，用推理模型的 reasoning 末段兜底。
    if not request_body.get('model'):
        model = _plain_model(db, available, model)

    rules = {
        'image': '把它改写成一段更具体的**图片生成提示词**：补充主体细节、环境、光线、'
                 '构图、镜头与画风。不要加解释，不要加引号，只输出提示词正文。',
        'video': '把它改写成一段更具体的**视频分镜提示词**：补充主体动作、镜头运动、'
                 '光线氛围与节奏。不要加解释，只输出提示词正文。',
    }.get(kind, '把它改写成更清晰、更有可执行性的表述。只输出改写结果。')
    messages = [
        {'role': 'system', 'content': '你是专业的 AI 绘画/视频提示词工程师。' + rules},
        {'role': 'user', 'content': draft},
    ]
    result = await _call_model(db, key, model, messages, None, temperature=0.35,
                               max_tokens=2048, reasoning='minimal')
    if not result.get('ok'):
        return {'ok': False, 'error': result.get('error') or '增强失败'}
    message = result.get('message') or {}
    refined = _extract_prompt(message.get('content') or '')
    if not refined:
        # 推理型模型会把预算全烧在 reasoning_content 上，`content` 直接是 null。
        # 实测 deepseek 会把最终答案写在思考末尾，所以这里回退取末段。
        refined = _extract_prompt(message.get('reasoning_content') or '')
    if not refined:
        return {'ok': False, 'error': '模型没有返回可用内容，请重试或换一个对话模型'}
    return {'ok': True, 'prompt': refined, 'model': model, 'mode': mode}


def _plain_model(db, available: list[str], fallback: str) -> str:
    """挑一个「思考可以关掉」的对话模型，用于提示词增强这类短输出任务。

    池子里大多数模型 `onlyReasoning=true`：它们不接受关闭思考，会把
    max_tokens 全用在 reasoning_content 上，`content` 返回 null。对「改写一句
    提示词」这种任务，思考纯属浪费 —— 用一个能关思考的模型又快又稳。
    """
    rows = {row.model_id: row for row in _enabled_models(db)}
    for mid in available:
        row = rows.get(mid)
        if row is None:
            continue
        try:
            meta = json.loads(row.upstream_metadata or '{}')
        except (ValueError, TypeError):
            meta = {}
        if not isinstance(meta, dict):
            continue
        only = meta.get('onlyReasoning')
        supports = meta.get('supportsReasoning')
        # 明确「不支持思考」或「支持但不强制」都可直接出结论。
        if only is False or supports is False:
            return mid
    return fallback


def _extract_prompt(text: str) -> str:
    """从模型输出里摘出「提示词正文」。

    模型经常裹上引号、```围栏，或在思考末尾才给答案。清理顺序：
    去围栏 → 去首尾引号 → 过长时取最后一段像提示词的行。
    """
    value = str(text or '').strip()
    if not value:
        return ''
    if '```' in value:
        parts = [part for part in value.split('```') if part.strip()]
        if parts:
            value = parts[-1].strip()
            head, sep, rest = value.partition('\n')
            if sep and head.strip().lower() in ('prompt', 'text', 'markdown', ''):
                value = rest.strip()
    value = value.strip().strip('"').strip('“”').strip('「」').strip()
    if not value:
        return ''
    if len(value) > 800:
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        for line in reversed(lines):
            if len(line) >= 8 and not line.startswith(
                    ('我们', '需要', '一共', '好的', 'So ', 'Need ', 'Thus', 'Maybe')):
                return line[:600]
        return value[:600]
    return value


@router.post('/plan')
def plan(request_body: dict, _: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """长视频分段规划 + 积分预估。"""
    total = int((request_body or {}).get('total_seconds') or 0)
    model = str((request_body or {}).get('model') or '')
    if total <= 0:
        return {'ok': False, 'error': '请给出目标总时长'}
    if not model:
        rows = [row.model_id for row in _enabled_models(db) if is_video_model_id(row.model_id)]
        if not rows:
            return {'ok': False, 'error': '当前没有可用的视频模型'}
        model = rows[0]
    return {'ok': True, 'model': model, **media_service.plan_long_video(total, model, db)}


@router.post('/merge')
def merge(request_body: dict, _: bool = Depends(require_admin),
          db: Session = Depends(get_db)):
    """把选中的视频资产合并成一条长视频。"""
    key = _default_key(db)
    if key is None:
        return {'ok': False, 'error': '还没有可用的 API Key'}
    ids = (request_body or {}).get('asset_ids') or []
    if not isinstance(ids, list) or not ids:
        return {'ok': False, 'error': '请选择要合并的视频片段'}
    return media_service.merge_assets(
        db, api_key_id=key.id, asset_ids=[str(value) for value in ids],
        conversation_id=str((request_body or {}).get('conversation_id') or ''),
        prompt=str((request_body or {}).get('prompt') or ''))


@router.get('/capabilities')
def capabilities(_: bool = Depends(require_admin), db: Session = Depends(get_db)):
    """环境能力（ffmpeg 是否可用等），前端据此禁用按钮。"""
    return {'merge': media_service.video_merge.describe()}