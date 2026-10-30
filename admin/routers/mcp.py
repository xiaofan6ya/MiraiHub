"""MCP 服务：把内置媒体能力（生图 / 生视频 / 分段合并）暴露给外部模型。

为什么要有 MCP
-------------
用户要的是「长视频靠分段合并」，而合并能力如果没有一个标准协议出口，
就只能被内置智能体用。接了 MCP 之后，Claude Desktop、Cline、Cherry Studio
这类支持 MCP 的客户端可以直接调用同一套工具 —— 模型自己就能编排
「规划分镜 → 逐段生成 → 合并成片」，不需要用户在网页里点。

协议
----
JSON-RPC 2.0 over HTTP，实现 MCP 的 streamable-http 传输里最常用的子集：

    POST /mcp   {"jsonrpc":"2.0","id":1,"method":"initialize",...}
                {"jsonrpc":"2.0","id":2,"method":"tools/list"}
                {"jsonrpc":"2.0","id":3,"method":"tools/call",
                 "params":{"name":"merge_videos","arguments":{...}}}

只实现了 `initialize` / `tools/list` / `tools/call` / `ping`。这四个就是
工具型 MCP 服务实际用到的全部方法；resources、prompts、sampling 需要
更长的会话语义，而这里的工具都是「一次调用出结果」，不需要。

鉴权
----
MCP 客户端无法使用后台的会话 Cookie，所以这里用 **API Key**：
`Authorization: Bearer <Key>` 或 `X-API-Key: <Key>`。语义与 `/v1/*` 一致
（配额、分组、账号绑定都生效），因此不会成为绕过配额的后门。
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from admin import agent as agent_meta
from admin import media_service
from admin.db import get_db
from admin.models import ModelConfig
from admin.security import check_quota, get_key_row
from model_metadata import is_image_model, is_video_model_id

_logger = logging.getLogger(__name__)

router = APIRouter(tags=['mcp'])

PROTOCOL_VERSION = '2025-06-18'
SERVER_NAME = 'miraihub-media'
SERVER_VERSION = '1.0.0'

TOOL_DEFINITIONS = [
    {
        'name': 'generate_image',
        'description': '用文字描述生成一张图片。返回 asset_id，可通过 asset 资源地址预览。',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'prompt': {'type': 'string', 'description': '画面描述'},
                'model': {'type': 'string', 'description': '可选，生图模型 ID'},
                'size': {'type': 'string', 'description': '可选尺寸，如 1024x1024'},
            },
            'required': ['prompt'],
        },
    },
    {
        'name': 'generate_video',
        'description': ('用文字描述生成一段短视频（单段上限通常 15 秒）。'
                        '该调用会阻塞到生成完成，可能耗时数分钟。'),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'prompt': {'type': 'string', 'description': '分镜描述'},
                'model': {'type': 'string', 'description': '可选，视频模型 ID'},
                'seconds': {'type': 'integer', 'description': '时长（秒）'},
                'resolution': {'type': 'string', 'description': '分辨率，如 720P / 1080P'},
                'aspect_ratio': {'type': 'string', 'description': '画面比例，如 16:9 / 9:16'},
            },
            'required': ['prompt'],
        },
    },
    {
        'name': 'plan_long_video',
        'description': ('把目标总时长拆成多个不超过模型上限的分镜时长，并预估积分。'
                        '制作超过 15 秒的成片时先用它规划。'),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'total_seconds': {'type': 'integer', 'description': '目标总时长（秒）'},
                'model': {'type': 'string', 'description': '可选，视频模型 ID'},
            },
            'required': ['total_seconds'],
        },
    },
    {
        'name': 'merge_videos',
        'description': ('把多个已生成的视频片段按顺序合并成一条长视频（本地 ffmpeg 无损拼接）。'
                        '这是制作长视频的最后一步。'),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'asset_ids': {'type': 'array', 'items': {'type': 'string'},
                              'description': '按播放顺序排列的 asset_id 列表'},
                'prompt': {'type': 'string', 'description': '成片标题或说明（可选）'},
            },
            'required': ['asset_ids'],
        },
    },
    {
        'name': 'list_assets',
        'description': '列出本 Key 已生成的图片与视频资产（可取到 asset_id 用于合并）。',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'kind': {'type': 'string', 'enum': ['', 'image', 'video']},
                'limit': {'type': 'integer'},
            },
        },
    },
    {
        'name': 'list_models',
        'description': '列出当前可用的对话 / 生图 / 视频模型。',
        'inputSchema': {
            'type': 'object',
            'properties': {'kind': {'type': 'string',
                                    'enum': ['all', 'chat', 'image', 'video']}},
        },
    },
]


def _rpc_result(request_id, result) -> dict:
    return {'jsonrpc': '2.0', 'id': request_id, 'result': result}


def _rpc_error(request_id, code: int, message: str) -> dict:
    return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': code, 'message': message}}


def _tools_for(db: Session) -> list[dict]:
    rows = db.query(ModelConfig).filter(ModelConfig.enabled == 1).all()
    has_image = any(is_image_model(row.model_id) for row in rows)
    has_video = any(is_video_model_id(row.model_id) for row in rows)
    allowed = {'list_models', 'list_assets'}
    if has_image:
        allowed.add('generate_image')
    if has_video:
        allowed.update({'generate_video', 'plan_long_video', 'merge_videos'})
    return [tool for tool in TOOL_DEFINITIONS if tool['name'] in allowed]


def _text_result(payload: dict) -> dict:
    """MCP 的 tools/call 结果：正文为文本块，结构化数据放 structuredContent。"""
    return {'content': [{'type': 'text',
                         'text': json.dumps(payload, ensure_ascii=False, indent=1)}],
            'structuredContent': payload,
            'isError': not bool(payload.get('ok', True))}


def _run_tool(db, key, name: str, arguments: dict) -> dict:
    rows = db.query(ModelConfig).filter(ModelConfig.enabled == 1).all()
    image_models = [row.model_id for row in rows if is_image_model(row.model_id)]
    video_models = [row.model_id for row in rows if is_video_model_id(row.model_id)]
    chat_models = [row.model_id for row in rows if not is_image_model(row.model_id)
                   and not is_video_model_id(row.model_id)]
    args = arguments if isinstance(arguments, dict) else {}

    if name == 'list_models':
        return {'ok': True, 'chat': chat_models, 'image': image_models,
                'video': video_models,
                'merge_available': media_service.video_merge.available()}
    if name == 'list_assets':
        items = media_service.list_assets(db, api_key_id=key.id,
                                         kind=str(args.get('kind') or ''),
                                         limit=int(args.get('limit') or 20))
        return {'ok': True, 'items': items, 'count': len(items)}
    if name == 'generate_image':
        if not image_models:
            return {'ok': False, 'error': '当前没有可用的生图模型'}
        model = args.get('model') if args.get('model') in image_models else image_models[0]
        return media_service.generate_image(
            db, api_key_id=key.id, model=model,
            prompt=str(args.get('prompt') or ''), size=str(args.get('size') or ''))
    if name == 'generate_video':
        if not video_models:
            return {'ok': False, 'error': '当前没有可用的视频模型'}
        model = args.get('model') if args.get('model') in video_models else video_models[0]
        return media_service.generate_video(
            db, api_key_id=key.id, model=model,
            prompt=str(args.get('prompt') or ''),
            seconds=args.get('seconds'), size=str(args.get('resolution') or ''),
            aspect_ratio=str(args.get('aspect_ratio') or ''))
    if name == 'plan_long_video':
        if not video_models:
            return {'ok': False, 'error': '当前没有可用的视频模型'}
        model = args.get('model') if args.get('model') in video_models else video_models[0]
        try:
            total = int(args.get('total_seconds') or 0)
        except (TypeError, ValueError):
            return {'ok': False, 'error': 'total_seconds 必须是整数'}
        if total <= 0:
            return {'ok': False, 'error': 'total_seconds 必须大于 0'}
        return {'ok': True, 'model': model,
                **media_service.plan_long_video(total, model, db)}
    if name == 'merge_videos':
        ids = args.get('asset_ids')
        if not isinstance(ids, list) or not ids:
            return {'ok': False, 'error': 'asset_ids 必须是非空数组'}
        return media_service.merge_assets(db, api_key_id=key.id,
                                          asset_ids=[str(value) for value in ids],
                                          prompt=str(args.get('prompt') or ''))
    return {'ok': False, 'error': f'未知工具：{name}',
            'available': [tool['name'] for tool in _tools_for(db)]}


@router.post('/mcp')
async def mcp_endpoint(request: Request, db: Session = Depends(get_db),
                       x_api_key: str | None = Header(default=None, alias='X-API-Key'),
                       authorization: str | None = Header(default=None)):
    token = x_api_key or (authorization[7:].strip()
                          if authorization and authorization.startswith('Bearer ') else '')
    key = get_key_row(db, token) if token else None
    if key is None:
        return JSONResponse(status_code=401, content=_rpc_error(
            None, -32001, '缺少或无效 API Key（Authorization: Bearer <Key>）'))
    try:
        check_quota(key)
    except Exception as exc:
        status = getattr(exc, 'status_code', 403)
        return JSONResponse(status_code=status, content=_rpc_error(
            None, -32002, getattr(exc, 'detail', 'Key 不可用')))

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_rpc_error(None, -32700, '解析失败'))
    if isinstance(payload, list):
        # 批量请求（JSON-RPC 允许），逐条处理。
        responses = [await _handle(entry, db, key) for entry in payload]
        return JSONResponse([item for item in responses if item is not None])
    result = await _handle(payload, db, key)
    if result is None:
        return Response(status_code=202)
    return JSONResponse(result)


async def _handle(payload, db, key):
    if not isinstance(payload, dict):
        return _rpc_error(None, -32600, '无效的 JSON-RPC 请求')
    request_id = payload.get('id')
    method = str(payload.get('method') or '')
    params = payload.get('params') if isinstance(payload.get('params'), dict) else {}

    if method == 'initialize':
        return _rpc_result(request_id, {
            'protocolVersion': PROTOCOL_VERSION,
            'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION},
            'capabilities': {'tools': {'listChanged': False}},
            'instructions': ('MiraiHub 媒体工具：生成图片/视频、规划长视频分镜、'
                             '把多段视频合并成一条成片。'
                             '制作超过 15 秒的视频请先 plan_long_video，'
                             '再逐段 generate_video，最后 merge_videos。'),
        })
    if method in ('notifications/initialized', 'initialized'):
        # 通知不需要响应。
        return None
    if method == 'ping':
        return _rpc_result(request_id, {})
    if method == 'tools/list':
        return _rpc_result(request_id, {'tools': _tools_for(db)})
    if method == 'tools/call':
        name = str(params.get('name') or '')
        arguments = params.get('arguments')
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            outcome = _run_tool(db, key, name, arguments)
        except Exception as exc:
            _logger.exception('MCP 工具 %s 执行失败', name)
            outcome = {'ok': False, 'error': f'工具执行失败：{exc}'}
        return _rpc_result(request_id, _text_result(outcome))
    return _rpc_error(request_id, -32601, f'不支持的方法：{method}')


@router.get('/mcp')
def mcp_probe(_: bool = Depends(lambda: True)):
    """给客户端/运维一个可读的能力说明（GET 不承担 RPC）。"""
    return {'server': SERVER_NAME, 'version': SERVER_VERSION,
            'protocol': PROTOCOL_VERSION,
            'transport': 'streamable-http (POST /mcp)',
            'auth': 'Authorization: Bearer <API Key> 或 X-API-Key: <API Key>',
            'tools': [tool['name'] for tool in TOOL_DEFINITIONS]}