"""OpenAI-compatible `/v1/videos` over the account pool and upstream `/v2/videos`.

OpenAI 官方的视频生成 API 是**异步任务**模式：

    POST /v1/videos                  → 创建任务（返回 id + status="queued"）
    GET  /v1/videos/{video_id}       → 轮询状态，直到 status="completed"
    GET  /v1/videos/{video_id}/content → 下载最终 MP4

上游（WorkBuddy 后端）的等价接口是：

    POST /v2/videos/generations      → {"data": {"id": ..., "status": "queued"}}
    POST /v2/videos/tasks            → {"task_id": ...} 轮询，completed 后 data[] 为视频 URL

两者形状差异全部收敛在本模块，对外只暴露 OpenAI 形状。几个刻意的取舍：

* **任务落库**。上游任务 ID 带账号归属（实测前缀是 uid），下载必须用同一个账号
  的凭证；而 OpenAI 的 `video_id` 会被客户端在之后任意时刻带着来轮询，所以
  「对外 ID → 内部任务 + 账号 + 上游 ID」的映射必须持久化，重启后仍可用。

* **按上游 usage.credit 实际积分记账**，不套文本模型的每千 token 倍率 ——
  与生图端点同口径。视频一次调用就是几十积分，用倍率估算会严重低估。

* **不在 POST 里等生成完成**。`POST /v1/videos` 只做到「上游已入队」，
  立刻返回 queued，避免让一次入队占着网关的请求时限（最长 160 秒），
  而真实生成可能要好几分钟。

* **未知结局不重发**。入队是计费动作：只要上游可能已经受理（超时、连接中断、
  5xx），一律不再换号重发，否则会重复扣费。只有**明确**的账号/配额拒绝才重试。
"""
import asyncio
import base64
import json
import math
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from admin import request_metrics, upstream_http
from admin.config import settings
from admin.db import get_db
from admin.models import Account, VideoTask
from admin.pool import POOL
from admin.request_scheduling import pool_budget, release_connection
from admin.routers import proxy
from admin.routers.audio import _AudioMultipartParser, _until_disconnect
from admin.security import GatewayRejected, check_quota, get_key_row

router = APIRouter(tags=['videos'])

MAX_INPUT_IMAGE_BYTES = 20 * 1024 * 1024
MAX_VIDEO_BYTES = 512 * 1024 * 1024
#: 任务保留时间：过期后清理行，避免表无限增长（上游签名 URL 通常 12 小时后失效）。
TASK_TTL_HOURS = 24

JSON_FIELDS = {'model', 'prompt', 'input_reference', 'seconds', 'size', 'size_ratio',
               'resolution', 'aspect_ratio', 'watermark', 'audio', 'seed', 'user',
               'n', 'response_format', 'reference_image', 'reference_images',
               'duration', 'width', 'height'}
#: 这些字段只影响输出文件名/元数据，不发给上游。
CLIENT_ONLY_FIELDS = {'user', 'n', 'response_format'}

#: OpenAI 的 `size` 是 "1280x720" 这种像素尺寸；上游用 720P/1080P/4K。
#: 这里按「短边像素」映射到上游最接近的档位，超过 1080 才用 4K。
#: 注意：并非所有模型都支持 4K（实测 kling 支持、minimax 只到 2K），
#: 因此映射结果仍要经过模型能力校验，不支持时回落到模型默认档位。
def size_to_resolution(size, capability) -> str:
    """把 OpenAI 的 `size` / 上游的 `resolution` 归一成上游档位。

    OpenAI 用像素尺寸（``"1280x720"``），上游用档位名（``720P`` / ``1080P`` /
    ``2K`` / ``4K``）。按**短边**映射：768x1280 与 1280x768 都落到同一档，
    竖屏视频不会被误判成更高的档位。候选档位只在模型自己声明的
    ``resolutions`` 里挑，避免发出上游不支持的分辨率。
    """
    supported = [str(v) for v in ((capability or {}).get('resolutions') or []) if str(v)]
    default = str((capability or {}).get('defaultResolution') or '')
    if not size:
        return default if default and (not supported or default in supported) \
            else (supported[0] if supported else '')
    text = str(size).strip().upper()
    # 已经是上游档位写法（720P / 1080P / 2K / 4K）就直接用。
    if text in supported:
        return text
    if text in ('2K', '4K') and supported:
        return text if text in supported else (default or supported[-1])
    width = height = 0
    # 统一小写再切：``1080X1920`` 这种大写 X 也必须能拆开。
    parts = [p for p in text.lower().replace('×', 'x').replace('*', 'x').split('x')
             if p.strip()]
    try:
        if len(parts) == 2:
            width, height = int(parts[0]), int(parts[1])
    except ValueError:
        return default or (supported[0] if supported else '')
    short = min(width, height) if width and height else 0
    if not short:
        return default or (supported[0] if supported else '')
    # 短边优先匹配（720x1280 / 1280x720 都应落到 720P）。
    for candidate, pixels in (('720P', 720), ('768P', 768), ('1080P', 1080),
                              ('2K', 1440), ('4K', 2160)):
        if candidate in supported and short <= pixels:
            return candidate
    if supported:
        return supported[-1]
    return default


def seconds_value(value, capability):
    """把 OpenAI 的 `seconds`（字符串或数字）归一成上游整数秒。

    OpenAI 文档里的取值是 "4" / "8" / "12" 这种字符串；上游要的是整数，
    并且每个模型有自己的 minSeconds / maxSeconds 或 supportedSeconds 白名单。
    越界时**夹到最近的合法值**而不是报错：这是入队计费动作，宁可生成一个
    稍短的视频，也不要因为参数差 1 秒就整单失败。
    """
    cap = capability or {}
    allowed = [int(v) for v in (cap.get('supportedSeconds') or []) if isinstance(v, (int, float))]
    low = cap.get('minSeconds')
    high = cap.get('maxSeconds')
    default = cap.get('defaultSeconds')
    try:
        wanted = int(float(str(value).strip()))
    except (TypeError, ValueError):
        wanted = None
    if wanted is None:
        wanted = int(default) if isinstance(default, (int, float)) else (
            allowed[0] if allowed else (int(low) if isinstance(low, (int, float)) else None))
        return wanted
    if allowed:
        return min(allowed, key=lambda v: (abs(v - wanted), v))
    if isinstance(low, (int, float)):
        wanted = max(int(low), wanted)
    if isinstance(high, (int, float)):
        wanted = min(int(high), wanted)
    return max(1, wanted)


def error(status, message, code, param=None, type_='invalid_request_error'):
    body = {'message': message, 'type': type_, 'code': code}
    if param:
        body['param'] = param
    return JSONResponse(status_code=status, content={'error': body})


def _video_response(task: VideoTask) -> dict:
    """把库里的任务行渲染成 OpenAI 的 video 对象。"""
    try:
        request_body = json.loads(task.request_json or '{}')
    except (ValueError, TypeError):
        request_body = {}
    item = {
        'id': task.video_id,
        'object': 'video',
        'created_at': int(task.created_at.replace(tzinfo=None).timestamp()) if task.created_at else 0,
        'status': task.status,
        'model': task.model,
        'progress': int(task.progress or 0),
        'seconds': str(request_body.get('seconds') or ''),
        'size': request_body.get('size') or '',
        'prompt': request_body.get('_prompt_preview') or '',
    }
    if task.status == 'completed':
        item['completed_at'] = int(task.completed_at.replace(tzinfo=None).timestamp()) if task.completed_at else 0
        item['expires_at'] = int(task.expires_at.replace(tzinfo=None).timestamp()) if task.expires_at else 0
    if task.status == 'failed':
        item['error'] = {'code': task.error_code or 'generation_failed',
                         'message': task.error_message or '视频生成失败'}
    if task.credits:
        item['usage'] = {'credits': task.credits}
    return item


def _load_task(db, key, video_id) -> VideoTask | None:
    task = db.query(VideoTask).filter(VideoTask.video_id == str(video_id)).first()
    if task is None or task.api_key_id != key.id:
        # 别的 Key 的任务不暴露存在性，与图片端点同口径。
        return None
    return task


def _openai_status(upstream_status: str) -> str:
    value = str(upstream_status or '').lower()
    if value in ('completed', 'succeeded', 'success'):
        return 'completed'
    if value in ('failed', 'failure', 'error', 'cancelled', 'canceled'):
        return 'failed'
    if value in ('queued', 'pending', 'submitted'):
        return 'queued'
    return 'in_progress'


def _upstream_scope(request, account) -> dict:
    """为「不属于任何客户端请求」的上游调用（轮询等）准备最小 scope。

    `upstream_http.client()` 只读 `request.scope` 里的三样东西：
    `_upstream_recovery`、`_upstream_account_key`（发车队列的账号键）和
    `app`（共享连接池）。轮询/下载时没有真实请求对象可继承，就按这几项构造：
    账号键用 `upstream_http.select_account` 的同一套口径，保证同一账号的所有
    上游发送都排在同一条发车道上。
    """
    scope = dict(getattr(request, 'scope', None) or {})
    scope.setdefault('_upstream_recovery', {'used': False})
    upstream_http.select_account(scope, account)
    return scope


class _ScopeRequest:
    """把裸 scope 包成 `upstream_http.client()` 能接受的最小请求对象。

    只需要 `.scope`；刻意不实现 `receive`/`send`，避免被误当作可读正文的
    真实请求。
    """

    def __init__(self, scope):
        self.scope = scope


@asynccontextmanager
async def _video_form(request):
    parser = _AudioMultipartParser(request.headers, request.stream(), max_files=1, max_fields=12)
    try:
        yield await parser.parse()
    finally:
        for handle in parser._files_to_close_on_error:
            handle.close()


async def _payload(request) -> dict:
    """解析创建任务的入参（JSON 或 multipart 上传参考图）。"""
    multipart = request.headers.get('content-type', '').lower().startswith('multipart/form-data')
    if multipart:
        received = 0

        async def receive():
            nonlocal received
            message = await request.receive()
            received += len(message.get('body', b''))
            if received > MAX_INPUT_IMAGE_BYTES + 1024 * 1024:
                raise MultiPartException('video_body_too_large')
            return message

        async with _video_form(Request(request.scope, receive)) as form:
            result = dict(form.multi_items())
            upload = result.get('input_reference')
            if isinstance(upload, UploadFile):
                content = await upload.read(MAX_INPUT_IMAGE_BYTES + 1)
                if not content or len(content) > MAX_INPUT_IMAGE_BYTES:
                    raise ValueError('参考图片不能为空，且不能超过 20 MiB')
                result.pop('input_reference')
                result['input_reference'] = ('data:' + (upload.content_type or 'image/png')
                                            + ';base64,' + base64.b64encode(content).decode('ascii'))
    else:
        try:
            result = json.loads(await request.body())
        except (ValueError, UnicodeError):
            raise ValueError('无效的 JSON 请求') from None
    if not isinstance(result, dict):
        raise ValueError('请求体必须为 JSON 对象')
    unknown = set(result) - JSON_FIELDS
    if unknown:
        raise ValueError('不支持的参数：' + ', '.join(sorted(unknown)))
    return result


def _with_task_hint(db, exc: ValueError) -> ValueError:
    """给「没指定模型」的错误补上当前可用的视频模型，省得调用方翻文档。"""
    message = str(exc)
    if '生视频模型' not in message:
        return exc
    from model_metadata import is_video_model_id
    from admin.models import ModelConfig
    try:
        ids = [row.model_id for row in db.query(ModelConfig).filter(ModelConfig.enabled == 1).all()
               if is_video_model_id(row.model_id)]
    except Exception:
        ids = []
    if ids:
        return ValueError(message + '（当前可用：' + '、'.join(ids[:3]) + '）')
    return exc


def _media_url(value, field):
    """参考图/参考视频必须是 HTTPS 或 data: URL。

    刻意不接受任意 URL 拉取：网关不会替调用方去内网取资源
    （与生图端点的 `image_url` 校验同口径）。
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} 必须为非空字符串')
    text = value.strip()
    if text.startswith('data:'):
        if ';base64,' not in text or ',' not in text:
            raise ValueError(f'{field} 的 data URL 必须带 ;base64,')
        return text
    parsed = urlsplit(text)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f'{field} 必须为 HTTPS 地址或 data: URL')
    return text


def _capability(db, model: str) -> dict:
    """该模型的视频能力（来自上游目录，同步时落库在 upstream_metadata）。"""
    from admin.models import ModelConfig
    row = db.query(ModelConfig).filter(ModelConfig.model_id == model).first()
    if row is None:
        return {}
    try:
        meta = json.loads(row.upstream_metadata or '{}')
    except (ValueError, TypeError):
        return {}
    value = meta.get('videoGeneration') if isinstance(meta, dict) else None
    return value if isinstance(value, dict) else {}


def _build_upstream_body(body: dict, model: str, capability: dict) -> dict:
    """OpenAI 参数 → 上游 /v2/videos/generations 请求体。

    上游字段（实测）：model / prompt / seconds / watermark / image_url /
    last_image_url / extra_parameters{resolution, aspect_ratio, enable_audio}。
    """
    cap = capability or {}
    prompt = body.get('prompt')
    reference = body.get('input_reference') or body.get('reference_image')
    if reference is None and isinstance(body.get('reference_images'), list):
        reference = (body['reference_images'] or [None])[0]
    if not isinstance(prompt, str) or not prompt.strip():
        if not reference:
            raise ValueError('prompt 必须为非空字符串（纯图生视频也建议给出描述）')
        prompt = ''
    max_len = int(cap.get('maxPromptLength') or 0)
    if max_len and len(prompt) > max_len:
        raise ValueError(f'prompt 超过该模型上限 {max_len} 字符')
    outgoing = {'model': model, 'prompt': prompt, 'watermark': True}
    if reference:
        outgoing['image_url'] = _media_url(reference, 'input_reference')
    if body.get('last_image'):
        outgoing['last_image_url'] = _media_url(body['last_image'], 'last_image')
    seconds = seconds_value(body.get('seconds', body.get('duration')), cap)
    if seconds:
        outgoing['seconds'] = seconds
    extra = {}
    resolution = size_to_resolution(body.get('resolution') or body.get('size'), cap)
    if resolution:
        extra['resolution'] = resolution
    ratio = body.get('aspect_ratio') or body.get('size_ratio')
    supported_ratios = [str(v) for v in (cap.get('aspectRatios') or []) if str(v)]
    if ratio and (not supported_ratios or ratio in supported_ratios):
        extra['aspect_ratio'] = ratio
    elif not reference and supported_ratios:
        # 文生视频必须给比例：上游缺省值不一定是模型自带默认。
        default_ratio = cap.get('defaultAspectRatio')
        extra['aspect_ratio'] = default_ratio if default_ratio in supported_ratios else supported_ratios[0]
    audio = body.get('audio')
    if audio is not None:
        mode = cap.get('audioMode')
        if mode == 'unsupported' and audio:
            raise ValueError(f'模型 {model} 不支持生成音频')
        if mode == 'always-on' and audio is False:
            raise ValueError(f'模型 {model} 总是生成音频，无法关闭')
        extra['enable_audio'] = bool(audio)
    elif cap.get('audioMode') == 'always-on' or cap.get('defaultEnableAudio') is True:
        extra['enable_audio'] = True
    if extra:
        outgoing['extra_parameters'] = extra
    return outgoing


@router.post('/v1/videos')
async def create_video(request: Request, db: Session = Depends(get_db),
                       x_api_key: str | None = Header(default=None, alias='X-API-Key'),
                       authorization: str | None = Header(default=None)):
    token = x_api_key or (authorization[7:].strip()
                          if authorization and authorization.startswith('Bearer ') else '')
    key = await proxy._blocking_call(get_key_row, db, token) if token else None
    if key is None:
        return error(401, '缺少或无效 API Key', 'invalid_api_key', type_='authentication_error')
    try:
        check_quota(key)
        request.scope['_gateway_key_id'] = key.id
        await proxy._blocking_call(release_connection, db)
        try:
            body = await _payload(request)
        except MultiPartException as exc:
            return error(413 if str(exc) == 'video_body_too_large' else 400,
                         '视频请求过大或上传格式错误', 'invalid_video_upload')
        except (ValueError, StarletteHTTPException) as exc:
            return error(400, str(exc), 'invalid_video_request')

        model = body.get('model')
        if not isinstance(model, str) or not model.strip() or model == 'auto':
            return error(400, str(_with_task_hint(
                db, ValueError('请指定已启用的生视频模型，例如 kling-video-v3'))),
                'invalid_model', param='model')
        from model_metadata import is_video_model
        if not is_video_model(model):
            return error(400, f"模型 '{model}' 不是视频模型；请使用 /v1/chat/completions "
                              '或 /v1/images/generations', 'invalid_model', param='model')

        allowed = await proxy._blocking_call(proxy._key_allowed_models, db, key)
        resolved = await proxy._blocking_call(proxy._pick_best_model, db, model, allowed)
        if resolved is None:
            return (proxy._model_access_error(key, model) if allowed is not None
                    else error(400, '模型不存在或已禁用', 'model_not_found'))
        record = request_metrics.current.get()
        if record is not None:
            record['key_id'] = key.id
            record['requested_model'] = model
        await proxy._blocking_call(release_connection, db)
        return await _until_disconnect(request, _create(request, db, key, body, model))
    except GatewayRejected as exc:
        await proxy._record_gateway_rejection(exc, key.id, request)
        return JSONResponse(status_code=exc.status_code, content=exc.payload())
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)


async def _create(request, db, key, body, model):
    started = time.perf_counter()
    capability = await proxy._blocking_call(_capability, db, model)
    try:
        outgoing = _build_upstream_body(body, model, capability)
    except ValueError as exc:
        return error(400, str(exc), 'invalid_video_request')
    budget = await proxy._blocking_call(pool_budget, db, key, [model])
    tried = set()
    status, kind = 503, 'no_account'
    while budget.available():
        acc = await proxy._select_account_with_wait(db, exclude_ids=tried, model=model,
                                                    account_id=key.account_id, key_id=key.id)
        if acc is None:
            break
        sess = None
        held = True
        try:
            budget.consume()
            peers = await proxy._blocking_call(
                lambda: db.query(Account.id).filter(Account.uid == acc.uid).all()) if acc.uid else []
            tried.update([acc.id, *(row[0] for row in peers)])
            request_metrics.mark_attempt(acc.id, model)
            upstream_http.select_account(request.scope, acc)
            sess = await proxy._account_session_async(db, acc)
            if sess is None:
                continue
            extra = await proxy._blocking_call(proxy._upstream_extra_headers, request,
                                               'video_generation')
            headers = await proxy._blocking_call(proxy._headers_for_attempt, sess, extra)
            await proxy._blocking_call(release_connection, db)
            # 入队是计费动作：只要结果未知（超时/断连/5xx）就不许再发一次。
            async with upstream_http.client(request, httpx.Timeout(60, connect=10),
                                            proxy.HTTP_LIMITS, retry_budget=budget) as client:
                response = await client.post(settings.BACKEND + '/v2/videos/generations',
                                             headers=headers, json=outgoing)
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            task_id = None
            if isinstance(payload, dict) and payload.get('code', 0) in (0, '0', None):
                data = payload.get('data')
                if isinstance(data, dict):
                    task_id = data.get('id')
            if task_id:
                await proxy._blocking_call(proxy._note_success, db, acc)
                row = await proxy._blocking_call(
                    _store_task, db, key, acc, model, str(task_id), body, outgoing)
                # 上游已入队 → 释放账号在途租约，生成在上游队列里进行。
                POOL.release(acc.uid or '')
                held = False
                log_id = await proxy._blocking_call(
                    proxy._record_usage, key.id, acc.id, model, 0.0, None,
                    client_ip=proxy._client_ip(request),
                    use_case='videos/generations', error_kind='success',
                    latency_ms=int((time.perf_counter() - started) * 1000))
                if log_id:
                    await proxy._blocking_call(_attach_usage_row, db, row.id, log_id)
                return JSONResponse(status_code=200, content=_video_response(row))
            status = response.status_code if response.status_code >= 400 else 502
            detail = json.dumps(payload, ensure_ascii=False)[:65536]
            kind = proxy._classify_error(status, detail)
            request_metrics.mark_upstream_error(kind, status)
            if response.status_code < 500 and kind in {
                    'session_dead', 'hard_credit', 'soft_rate', 'model_rate', 'model_block',
                    'account_fault'}:
                await proxy._blocking_call(
                    proxy._apply_account_policy, db, acc, kind, status, detail, model=model,
                    reset_at=proxy._error_reset_at(response.headers, detail))
                continue
            break
        except httpx.TransportError:
            status, kind = 502, 'transport'
            break
        finally:
            try:
                if sess is not None:
                    sess.close()
            finally:
                if held:
                    POOL.release(acc.uid or '')
    status = 503 if kind == 'no_account' else status if status in (400, 413, 422, 429) else 502
    return error(status, '创建视频任务失败，请检查模型参数或稍后重试', 'video_creation_failed',
                 type_='server_error' if status >= 500 else 'invalid_request_error')


def _attach_usage_row(db, task_row_id, log_id) -> None:
    """把入队时写下的用量日志 id 记到任务上，供完成时回填真实积分。"""
    try:
        db.query(VideoTask).filter(VideoTask.id == task_row_id).update(
            {VideoTask.usage_log_id: int(log_id)}, synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()


def _store_task(db, key, acc, model, upstream_id, body, outgoing) -> VideoTask:
    """落库「对外 video_id → 上游任务 + 账号」的映射。

    prompt 只保留 200 字预览（与图片端点的 `revised_prompt` 同性质），
    避免把整段用户内容写进任务表。
    """
    request_snapshot = {
        'seconds': outgoing.get('seconds'),
        'size': (outgoing.get('extra_parameters') or {}).get('resolution') or body.get('size') or '',
        'aspect_ratio': (outgoing.get('extra_parameters') or {}).get('aspect_ratio') or '',
        'image_to_video': bool(outgoing.get('image_url')),
        '_prompt_preview': str(body.get('prompt') or '')[:200],
    }
    row = VideoTask(
        video_id='video_' + secrets.token_urlsafe(18).replace('-', '').replace('_', ''),
        api_key_id=key.id, account_id=acc.id, model=model, status='queued', progress=0,
        upstream_id=upstream_id,
        request_json=json.dumps(request_snapshot, ensure_ascii=False),
        result_json='{}',
        expires_at=datetime.utcnow() + timedelta(hours=TASK_TTL_HOURS),
    )
    db.add(row)
    db.commit()
    return row


@router.get('/v1/videos/{video_id}')
async def retrieve_video(video_id: str, request: Request, db: Session = Depends(get_db),
                         x_api_key: str | None = Header(default=None, alias='X-API-Key'),
                         authorization: str | None = Header(default=None)):
    token = x_api_key or (authorization[7:].strip()
                          if authorization and authorization.startswith('Bearer ') else '')
    key = await proxy._blocking_call(get_key_row, db, token) if token else None
    if key is None:
        return error(401, '缺少或无效 API Key', 'invalid_api_key', type_='authentication_error')
    task = await proxy._blocking_call(_load_task, db, key, video_id)
    if task is None:
        return error(404, f'视频任务 {video_id} 不存在', 'video_not_found', type_='not_found')
    if task.status in ('queued', 'in_progress'):
        task = await _refresh(db, task, request)
    return JSONResponse(content=_video_response(task))


async def _refresh(db, task: VideoTask, request=None) -> VideoTask:
    """向对应账号的上游查询一次任务状态；失败不改变本地状态。

    ``request`` 在轮询/下载入口是真实请求，会带上网关准入信息；后台自检可以
    不传。无论传不传，都必须**新建一个 scope 并写入账号键**：上游发车队列
    （`admin/upstream_dispatch.py::before_send`）要求每次上游发送都带
    `_upstream_account_key`，否则直接 503「上游账号尚未确定」。这里曾经传
    `None` 给 `upstream_http.client()`，于是轮询必然失败 —— 实测现象就是
    「创建任务成功、状态永远查不出来」。
    """
    from admin.models import Account
    acc = await proxy._blocking_call(lambda: db.get(Account, task.account_id))
    if acc is None:
        task.status = 'failed'
        task.error_code = 'account_missing'
        task.error_message = '生成该视频的账号已被删除'
        await proxy._blocking_call(db.commit)
        return task
    sess = None
    try:
        sess = await proxy._account_session_async(db, acc)
        if sess is None:
            return task
        # 轮询同样是一次上游调用：带上归属头，并给这一次轮询独立的 trace。
        poll_extra = await proxy._blocking_call(
            proxy._upstream_extra_headers, request, 'video_generation')
        headers = await proxy._blocking_call(proxy._headers_for_attempt, sess, poll_extra)
        # 轮询是纯网络等待，先把数据库连接交回池子（与对话/生图端点同口径），
        # 否则一次轮询会一直占着连接直到上游响应。
        await proxy._blocking_call(release_connection, db)
        scope = _upstream_scope(request, acc)
        async with upstream_http.client(_ScopeRequest(scope), httpx.Timeout(30, connect=10),
                                        proxy.HTTP_LIMITS) as client:
            response = await client.post(settings.BACKEND + '/v2/videos/tasks',
                                         headers=headers, json={'task_id': task.upstream_id})
        if not response.is_success:
            return task
        payload = response.json()
        data = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return task
        mapped = _openai_status(data.get('status'))
        task.status = mapped
        if mapped == 'queued':
            task.progress = max(int(task.progress or 0), 5)
        elif mapped == 'in_progress':
            task.progress = max(int(task.progress or 0), 50)
        elif mapped == 'completed':
            task.progress = 100
            videos = [row for row in (data.get('data') or [])
                      if isinstance(row, dict) and isinstance(row.get('url'), str) and row['url']]
            task.result_json = json.dumps({'url': videos[0]['url'] if videos else ''},
                                          ensure_ascii=False)
            if not videos:
                task.status = 'failed'
                task.error_code = 'empty_result'
                task.error_message = '上游标记完成但没有返回视频地址'
            usage = data.get('usage') if isinstance(data.get('usage'), dict) else {}
            credits = usage.get('credit')
            task.credits = (float(credits) if type(credits) in (int, float)
                            and math.isfinite(credits) and credits >= 0 else 0.0)
            task.completed_at = datetime.utcnow()
            await _bill(task, acc)
        elif mapped == 'failed':
            task.error_code = 'generation_failed'
            task.error_message = '上游视频生成失败，积分是否退回以上游规则为准'
            task.completed_at = datetime.utcnow()
        await proxy._blocking_call(db.commit)
        await proxy._blocking_call(proxy._note_success, db, acc)
    except (httpx.HTTPError, ValueError):
        # 查询失败不改变本地状态：客户端可以继续轮询，不能因为一次抖动就判失败。
        pass
    finally:
        if sess is not None:
            sess.close()
    return task


async def _bill(task: VideoTask, acc) -> None:
    """按上游返回的 usage.credit 记账（与生图端点同口径）。

    创建任务时写过一条 credits=0 的「入队」用量记录；这里把它**回填**成真实
    积分，而不是再插一行 —— 一次生成在账本里只能有一条，否则统计会翻倍。
    回填失败（例如那行已被清理）才补插一条，保证积分不丢。
    """
    credits = float(task.credits or 0)
    updated = 0
    if task.usage_log_id:
        def _update():
            from admin import db as db_module
            from admin.models import ApiKey, UsageLog
            # 走模块属性而不是本地名字：测试替换、多进程热更新都依赖这一层间接。
            db = db_module.SessionLocal()
            try:
                row = db.query(UsageLog).filter(UsageLog.id == task.usage_log_id).first()
                if row is None:
                    return 0
                if credits:
                    db.query(ApiKey).filter(ApiKey.id == task.api_key_id).update(
                        {ApiKey.credit_used: (ApiKey.credit_used or 0) + credits},
                        synchronize_session=False)
                    account = db.query(Account).filter(Account.id == task.account_id).first()
                    if account is not None:
                        account.balance_remain = max(0, int(account.balance_remain or 0) - int(credits))
                row.credits = credits
                row.error_kind = 'success'
                db.commit()
                return 1
            except Exception:
                db.rollback()
                return 0
            finally:
                db.close()
        updated = await proxy._blocking_call(_update)
    if updated:
        return
    await proxy._blocking_call(
        proxy._record_usage, task.api_key_id, task.account_id, task.model, credits, None,
        use_case='videos/generations', error_kind='success',
        latency_ms=0)


@router.get('/v1/videos/{video_id}/content')
async def download_video(video_id: str, request: Request, db: Session = Depends(get_db),
                         x_api_key: str | None = Header(default=None, alias='X-API-Key'),
                         authorization: str | None = Header(default=None)):
    token = x_api_key or (authorization[7:].strip()
                          if authorization and authorization.startswith('Bearer ') else '')
    key = await proxy._blocking_call(get_key_row, db, token) if token else None
    if key is None:
        return error(401, '缺少或无效 API Key', 'invalid_api_key', type_='authentication_error')
    task = await proxy._blocking_call(_load_task, db, key, video_id)
    if task is None:
        return error(404, f'视频任务 {video_id} 不存在', 'video_not_found', type_='not_found')
    if task.status in ('queued', 'in_progress'):
        task = await _refresh(db, task, request)
    if task.status != 'completed':
        return error(409, '视频尚未生成完成' if task.status != 'failed' else '视频生成失败',
                     'video_not_ready')
    try:
        url = (json.loads(task.result_json or '{}') or {}).get('url') or ''
    except (ValueError, TypeError):
        url = ''
    if not url:
        return error(502, '任务已完成但没有可下载的视频地址', 'video_unavailable',
                     type_='server_error')
    parsed = urlsplit(url)
    # 只允许腾讯 COS 的回源地址：签名 URL 里带 AK/签名，绝不把它当通用下载器使用。
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.username or parsed.password
            or parsed.port not in (None, 443) or not host.endswith('.myqcloud.com')):
        return error(502, '上游返回的视频地址不支持安全下载', 'video_unavailable',
                     type_='server_error')
    chunks: list[bytes] = []
    total = 0
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15),
                                     follow_redirects=False, trust_env=False) as client:
            async with client.stream('GET', url) as response:
                if not response.is_success:
                    return error(502, '视频下载失败，签名地址可能已过期', 'video_download_failed',
                                 type_='server_error')
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_VIDEO_BYTES:
                        return error(502, '视频超过网关单次下载上限', 'video_too_large',
                                     type_='server_error')
                    chunks.append(chunk)
    except httpx.HTTPError:
        return error(502, '视频下载失败，请稍后重试', 'video_download_failed', type_='server_error')
    if not chunks:
        return error(502, '上游返回空视频', 'video_download_failed', type_='server_error')
    return Response(content=b''.join(chunks), media_type='video/mp4', headers={
        'Content-Disposition': f'attachment; filename="{task.video_id}.mp4"',
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
    })


@router.get('/v1/videos')
async def list_videos(db: Session = Depends(get_db), limit: int = 20, after: str = '',
                      x_api_key: str | None = Header(default=None, alias='X-API-Key'),
                      authorization: str | None = Header(default=None)):
    token = x_api_key or (authorization[7:].strip()
                          if authorization and authorization.startswith('Bearer ') else '')
    key = await proxy._blocking_call(get_key_row, db, token) if token else None
    if key is None:
        return error(401, '缺少或无效 API Key', 'invalid_api_key', type_='authentication_error')
    size = max(1, min(100, int(limit or 20)))
    await proxy._blocking_call(maybe_purge, db)
    query = db.query(VideoTask).filter(VideoTask.api_key_id == key.id)
    if after:
        previous = db.query(VideoTask).filter(VideoTask.video_id == str(after),
                                              VideoTask.api_key_id == key.id).first()
        if previous is not None:
            query = query.filter(VideoTask.id < previous.id)
    rows = query.order_by(VideoTask.id.desc()).limit(size).all()
    return {
        'object': 'list',
        'data': [_video_response(row) for row in rows],
        'has_more': len(rows) == size,
        'last_id': rows[-1].video_id if rows else None,
    }


@router.delete('/v1/videos/{video_id}')
async def delete_video(video_id: str, db: Session = Depends(get_db),
                       x_api_key: str | None = Header(default=None, alias='X-API-Key'),
                       authorization: str | None = Header(default=None)):
    token = x_api_key or (authorization[7:].strip()
                          if authorization and authorization.startswith('Bearer ') else '')
    key = await proxy._blocking_call(get_key_row, db, token) if token else None
    if key is None:
        return error(401, '缺少或无效 API Key', 'invalid_api_key', type_='authentication_error')
    task = await proxy._blocking_call(_load_task, db, key, video_id)
    if task is None:
        return error(404, f'视频任务 {video_id} 不存在', 'video_not_found', type_='not_found')
    row_id = task.id
    db.query(VideoTask).filter(VideoTask.id == row_id).delete(synchronize_session=False)
    await proxy._blocking_call(db.commit)
    return {'id': video_id, 'object': 'video.deleted', 'deleted': True}


def purge_expired(db=None) -> int:
    """清理过期任务行。返回删除条数。

    由创建/列表入口按小时节流调用，不额外挂调度任务：任务表只在有人用视频
    接口时才增长，跟着真实流量清理比固定定时更省事，也不会在没人用的实例上
    反复扫表。
    """
    own = db is None
    if own:
        from admin import db as db_module
        db = db_module.SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=TASK_TTL_HOURS)
        deleted = db.query(VideoTask).filter(VideoTask.created_at < cutoff).delete(
            synchronize_session=False)
        db.commit()
        return int(deleted or 0)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    finally:
        if own:
            db.close()


#: 上次清理时间（秒级时间戳）。放在进程内存里足够：清理是幂等的。
_last_purge = 0.0


def maybe_purge(db) -> None:
    global _last_purge
    now = time.time()
    if now - _last_purge < 3600:
        return
    _last_purge = now
    purge_expired(db)