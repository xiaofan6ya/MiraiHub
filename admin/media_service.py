"""内置媒体服务：图片 / 视频生成、分段合并、资产落盘。

谁在用
------
* **智能体**（`admin/routers/agent.py`）把这些函数包成模型可调用的工具；
* **MCP 服务**（`admin/routers/mcp.py`）把它们暴露成 MCP 工具，
  让外部模型（Claude Desktop、Cline 等）也能调用同一套能力。

因此这里刻意**不依赖 FastAPI / Request**：只收「Key 是谁、要做什么」，
返回结构化结果。上层各自负责鉴权与形状转换。这样同一份生成逻辑不会
因为「智能体版本」和「MCP 版本」而漂移 —— 那种漂移最后一定表现为
「两边扣的积分不一样」。

积分记账
--------
一律走上游返回的 `usage.credit`，与 `/v1/images/*`、`/v1/videos` 同口径。
预估（`estimate_*`）只用于给用户提示，**绝不参与记账**。
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
from pathlib import Path
import re
import time
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx

from admin import agent as agent_meta
from admin import request_metrics, upstream_http, video_merge
from admin.config import settings
from admin.db import SessionLocal
from admin.models import Account, AgentAsset, ModelConfig, UsageLog
from admin.pool import POOL
from admin.request_scheduling import pool_budget, release_connection

_logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_VIDEO_BYTES = 512 * 1024 * 1024
#: 单个资产目录上限：超过就按最旧优先清理，避免磁盘无限增长。
ASSET_DIR_MAX_BYTES = 4 * 1024 * 1024 * 1024

#: 视频「每秒积分」经验系数，用于**预估**（不参与记账）。
#: 实测 kling-video-v3 / 720P：4 秒 51.43、6 秒 77.14 → 12.857 积分/秒。
#: 分辨率越高单价越高，这里给常见档位一个倍数。数字来自实测，
#: 换模型/改价后会有偏差，所以界面必须标注「仅供参考，以实际为准」。
VIDEO_CREDITS_PER_SECOND = 12.857
VIDEO_RESOLUTION_FACTORS = {'720P': 1.0, '768P': 1.0, '1080P': 1.8, '2K': 2.4, '4K': 4.0}
#: 生图单张经验值兜底（实测 hunyuan-image-alpha 约 5.71 积分/张，
#: 但 hunyuan-image-v3.0 实测 10.57 —— 模型间差异很大，所以真正用的是
#: `learnt_image_unit_cost()` 从历史用量里学到的按模型单价，这里只是兜底。
IMAGE_CREDITS_PER_UNIT = 5.71
#: 学习单价所需的最小样本数。样本太少时中位数不稳，宁可退回兜底值。
LEARN_MIN_SAMPLES = 2
#: 学习窗口（天）。
LEARN_WINDOW_DAYS = 30


def learnt_video_credits_per_second(db, model: str = '') -> float:
    """从历史用量里学习「每秒积分」。

    为什么不用固定常数：实测 hunyuan-image-alpha 与 hunyuan-image-v3.0 的
    单张成本差了近一倍，视频同理（不同模型/档位调价方式不同）。用**自己
    账本里的真实数据**比任何硬编码都准，也不用维护价格表。

    取中位数而不是均值：偶发的重试或异常大额不会把预估值带偏。
    指定模型时优先用**该模型**的样本 —— 模型之间的单价差异比样本量更重要，
    所以哪怕只有 1 个该模型的样本，也好过用别的模型的均值。
    """
    from statistics import median
    cutoff = datetime.utcnow() - timedelta(days=LEARN_WINDOW_DAYS)
    query = db.query(UsageLog).filter(
        UsageLog.use_case.in_(('videos/generations', 'agent/videos')),
        UsageLog.credits > 0,
        UsageLog.created_at >= cutoff)
    if model:
        query = query.filter(UsageLog.model == model)
    samples: list[float] = []
    for row in query.order_by(UsageLog.id.desc()).limit(60).all():
        meta = {}
        try:
            meta = json.loads(row.diagnostic_json or '{}')
        except (ValueError, TypeError):
            meta = {}
        seconds = _seconds_from_evidence(row, meta)
        if seconds:
            samples.append(float(row.credits) / seconds)
    if not samples:
        return VIDEO_CREDITS_PER_SECOND
    if len(samples) < LEARN_MIN_SAMPLES and model:
        # 该模型样本不足：用它自己的唯一估计（比全站均值更贴近这个模型）。
        return round(samples[0], 3)
    return round(median(samples), 3)


def _seconds_from_evidence(row, meta: dict) -> float:
    """从用量记录里推断这次生成的秒数。

    视频按次记账而不是按 token，没有单独的 seconds 列，所以按可用线索
    依次尝试：任务快照 → 诊断 JSON 里的 usage 字段。
    """
    for key in ('output_video_duration', 'seconds', 'duration_s'):
        value = meta.get(key) if isinstance(meta, dict) else None
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    usage = meta.get('usage') if isinstance(meta, dict) else None
    if isinstance(usage, dict):
        value = usage.get('output_video_duration')
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return 0.0


def learnt_image_unit_cost(db, model: str = '') -> float:
    """从历史用量里学习「单张图片积分」。

    与视频同理：指定模型时优先用该模型的样本。生图的模型间差异更大
    （hunyuan-image-alpha 约 5.71 / 张，hunyuan-image-v3.0 实测 10.57），
    用全站均值会系统性低估或高估。
    """
    from statistics import median
    cutoff = datetime.utcnow() - timedelta(days=LEARN_WINDOW_DAYS)
    query = db.query(UsageLog).filter(
        UsageLog.use_case.in_(('images/generations', 'images/edits', 'agent/images')),
        UsageLog.credits > 0,
        UsageLog.created_at >= cutoff)
    if model:
        query = query.filter(UsageLog.model == model)
    samples = [float(row.credits) for row in
               query.order_by(UsageLog.id.desc()).limit(60).all()]
    if not samples:
        return IMAGE_CREDITS_PER_UNIT
    if len(samples) < LEARN_MIN_SAMPLES and model:
        return round(samples[0], 2)
    return round(median(samples), 2)


def estimate_video_credits(seconds, resolution: str = '', model: str = '',
                          db=None) -> dict:
    """视频生成积分预估。

    单价优先从**自己账本**里学（`learnt_video_credits_per_second`），
    样本不足才退回实测常数。这比硬编码价格表可靠：上游调价后，
    预估值会随着真实用量自动跟上。
    """
    try:
        value = int(float(seconds or 0))
    except (TypeError, ValueError):
        value = 0
    value = max(0, value)
    per_second = learnt_video_credits_per_second(db, model) if db is not None \
        else VIDEO_CREDITS_PER_SECOND
    factor = VIDEO_RESOLUTION_FACTORS.get(str(resolution).upper(), 1.0)
    estimate = value * per_second * factor
    return {
        'seconds': value,
        'resolution': str(resolution or ''),
        'per_second': per_second,
        'factor': factor,
        'credits': round(estimate, 2),
        'credits_range': [round(estimate * 0.8, 2), round(estimate * 1.35, 2)],
        'model': model,
        'note': '按本网关历史真实用量估算，仅参考，实际以上游返回的 usage.credit 为准',
    }


def estimate_image_credits(count: int = 1, size: str = '', model: str = '',
                           db=None) -> dict:
    count = max(1, int(count or 1))
    # 大尺寸单价更高（像素量翻倍不等于价格翻倍，这里给保守倍数）。
    factor = 1.0
    match = re.fullmatch(r'\s*(\d{2,5})\s*[x×]\s*(\d{2,5})\s*', str(size or ''))
    if match:
        pixels = int(match.group(1)) * int(match.group(2))
        if pixels > 1024 * 1024:
            factor = 1.6
    unit = learnt_image_unit_cost(db, model) if db is not None else IMAGE_CREDITS_PER_UNIT
    estimate = count * unit * factor
    return {
        'count': count,
        'size': str(size or ''),
        'unit': unit,
        'credits': round(estimate, 2),
        'credits_range': [round(estimate * 0.7, 2), round(estimate * 1.6, 2)],
        'model': model,
        'note': '按本网关历史真实用量估算，仅参考，实际以上游返回的 usage.credit 为准',
    }


def data_dir() -> Path:
    """资产落盘目录。

    优先环境变量（桌面/安卓/容器各自注入），否则放到项目的 `admin/data`
    下 —— 与 `admin/data/upstream_error_messages.json` 同处，
    打包脚本已经会把该目录带进产物。
    """
    override = (os.getenv('WB_AGENT_ASSET_DIR') or '').strip()
    base = Path(override) if override else (
        Path(__file__).resolve().parent / 'data' / 'agent_assets')
    base.mkdir(parents=True, exist_ok=True)
    return base


# ---------------------------------------------------------------------------
# 积分预估
# ---------------------------------------------------------------------------

def video_capability(db, model: str) -> dict:
    """读模型声明的视频能力（同步模型目录时落在 upstream_metadata）。"""
    row = db.query(ModelConfig).filter(ModelConfig.model_id == model).first()
    if row is None:
        return {}
    try:
        meta = json.loads(row.upstream_metadata or '{}')
    except (ValueError, TypeError):
        return {}
    value = meta.get('videoGeneration') if isinstance(meta, dict) else None
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# 池与凭证（与 proxy 的轮转语义一致，但精简到「单次生成」需要的部分）
# ---------------------------------------------------------------------------

def _classify_payload(payload) -> tuple[bool, str]:
    """上游统一响应体：`{"code":0,"msg":"OK","data":{...}}`。"""
    if not isinstance(payload, dict):
        return False, '上游返回非 JSON 对象'
    code = payload.get('code', 0)
    if code in (0, '0', None):
        return True, ''
    return False, str(payload.get('msg') or f'上游返回 code={code}')


def _open_account_session(account, purpose: str = 'image_generation',
                          conversation_id: str = ''):
    """Build one outbound session with identity + trace headers.

    Media endpoints used to reuse a bare ``get_headers()`` set, which upstream
    reads as an anonymous caller: the credit-usage table then shows an empty
    使用端 column and invents a ``crb-`` requestId. Each call also needs its own
    ``X-Conversation-Request-ID`` because upstream de-duplicates on it.
    """
    from admin import upstream_identity as identity
    from admin.backend import AccountSession
    session = AccountSession(account.auth_json)
    extra = identity.identity_headers(purpose)
    extra.update(identity.trace_headers(conversation_id))
    session._gateway_headers = session.get_headers(extra=extra)
    return session


def _upstream_scope(account) -> dict:
    scope = {'_upstream_recovery': {'used': False}}
    upstream_http.select_account(scope, account)
    return scope


class _ScopeRequest:
    def __init__(self, scope):
        self.scope = scope


def _store_asset(db, *, api_key_id, conversation_id, kind, model, data: bytes,
                 mime: str, prompt: str, credits: float, meta: dict,
                 duration_s: float = 0) -> AgentAsset:
    """把媒体本体写盘并登记元数据。

    文件名用随机 ID 而不是原始名字：资产目录是**平铺**的，同名会互相覆盖；
    也不允许用户可控的文件名（路径穿越）。
    """
    asset_id = agent_meta.new_id(kind)
    suffix = {'image': '.png', 'video': '.mp4'}.get(kind, '.bin')
    file_name = f'{asset_id}{suffix}'
    target = data_dir() / file_name
    target.write_bytes(data)
    row = AgentAsset(
        asset_id=asset_id, api_key_id=api_key_id, conversation_id=conversation_id,
        kind=kind, model=model, file_name=file_name, mime=mime,
        bytes=len(data), duration_s=float(duration_s or 0), prompt=prompt[:4000],
        credits=float(credits or 0),
        meta_json=json.dumps(meta or {}, ensure_ascii=False),
        expires_at=agent_meta.asset_expiry(),
    )
    db.add(row)
    db.commit()
    _prune_assets()
    return row


def _prune_assets(limit_bytes: int = ASSET_DIR_MAX_BYTES) -> None:
    """资产目录超限时按最旧优先删除（连同数据库行）。"""
    try:
        base = data_dir()
        files = sorted((p for p in base.iterdir() if p.is_file()),
                       key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        if total <= limit_bytes:
            return
        removed: list[str] = []
        for path in files:
            if total <= limit_bytes:
                break
            size = path.stat().st_size
            try:
                path.unlink()
                total -= size
                removed.append(path.stem)
            except OSError:
                continue
        if removed:
            _logger.info('资产目录超限，已清理 %d 个旧资产', len(removed))
            with SessionLocal() as db:
                db.query(AgentAsset).filter(AgentAsset.asset_id.in_(removed)).delete(
                    synchronize_session=False)
                db.commit()
    except Exception:
        _logger.warning('清理资产目录失败', exc_info=True)


def asset_path(row: AgentAsset) -> Path:
    return data_dir() / row.file_name


# ---------------------------------------------------------------------------
# 生图
# ---------------------------------------------------------------------------

def _image_bytes_from_result(payload: dict) -> tuple[bytes, str]:
    """从上游图片结果里取二进制。

    上游通常回腾讯 COS 的签名 URL，偶尔回 b64_json。两种都要支持，
    但 URL 只允许 COS 域名 —— 否则这就是一个「按模型输出抓任意 URL」的 SSRF。
    """
    rows = payload.get('data') if isinstance(payload.get('data'), dict) else payload
    items = rows.get('data') if isinstance(rows, dict) else None
    if not isinstance(items, list) or not items:
        raise RuntimeError('上游没有返回图片')
    first = items[0] if isinstance(items[0], dict) else {}
    encoded = first.get('b64_json')
    if isinstance(encoded, str) and encoded.strip():
        raw = encoded.split(',', 1)[1] if encoded.startswith('data:') else encoded
        return base64.b64decode(raw), 'image/png'
    url = first.get('url')
    if not isinstance(url, str) or not url:
        raise RuntimeError('上游没有返回图片地址')
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or not host.endswith('.myqcloud.com')):
        raise RuntimeError('上游返回的图片地址不是受信任的 COS 域名')
    with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as client:
        response = client.get(url)
        response.raise_for_status()
        content = response.content
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise RuntimeError('上游图片为空或超过 20 MiB')
    return content, response.headers.get('content-type') or 'image/png'


def generate_image(db, *, api_key_id: int, model: str, prompt: str,
                   size: str = '', conversation_id: str = '') -> dict:
    """生成一张图片并落盘。返回含 `asset_id` 的结果字典。"""
    from admin.routers import proxy
    from admin.security import get_key_row_by_id

    key = get_key_row_by_id(db, api_key_id)
    if key is None:
        return {'ok': False, 'error': 'API Key 不存在或已失效'}
    body = {'model': model, 'prompt': prompt, 'n': 1, 'response_format': 'url'}
    if size:
        body['size'] = size

    budget = pool_budget(db, key, [model])
    tried: set[int] = set()
    last = '没有可用账号'
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
            session = _open_account_session(acc, 'image_generation', conversation_id)
            headers = dict(session._gateway_headers)
            release_connection(db)
            with httpx.Client(timeout=httpx.Timeout(300, connect=15),
                              limits=proxy.HTTP_LIMITS) as client:
                response = client.post(settings.BACKEND + '/v2/images/generations',
                                       headers=headers, json=body)
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            good, message = _classify_payload(payload)
            if response.is_success and good:
                usage = (payload.get('data') or {}).get('usage') \
                    if isinstance(payload.get('data'), dict) else {}
                usage = usage if isinstance(usage, dict) else {}
                credits = usage.get('credit', usage.get('credits'))
                credits = float(credits) if isinstance(credits, (int, float)) and math.isfinite(credits) else 0.0
                data, mime = _image_bytes_from_result(payload)
                row = _store_asset(
                    db, api_key_id=key.id, conversation_id=conversation_id, kind='image',
                    model=model, data=data, mime=mime, prompt=prompt, credits=credits,
                    meta={'size': size, 'usage': usage})
                proxy._note_success(db, acc)
                proxy._record_usage(key.id, acc.id, model, credits, None,
                                    use_case='agent/images', error_kind='success',
                                    prompt_tokens=usage.get('input_tokens'),
                                    completion_tokens=usage.get('output_tokens'))
                return {'ok': True, 'asset_id': row.asset_id, 'kind': 'image',
                        'model': model, 'credits': round(credits, 2),
                        'bytes': len(data), 'size': size or '默认',
                        'summary': f'已生成图片（{model}，{credits:.2f} 积分）'}
            last = message or f'HTTP {response.status_code}'
            kind = proxy._classify_error(response.status_code, json.dumps(payload, ensure_ascii=False))
            if response.status_code < 500 and kind in {
                    'session_dead', 'hard_credit', 'soft_rate', 'model_rate',
                    'model_block', 'account_fault'}:
                proxy._apply_account_policy(db, acc, kind, response.status_code, last, model=model)
                continue
            break
        except Exception as exc:
            last = str(exc)
            break
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
    return {'ok': False, 'error': f'生图失败：{last}'}


# ---------------------------------------------------------------------------
# 生视频（异步：提交 + 轮询）
# ---------------------------------------------------------------------------

def submit_video(db, *, api_key_id: int, model: str, prompt: str, seconds=None,
                 size: str = '', aspect_ratio: str = '', audio=None,
                 input_reference: str = '', conversation_id: str = '') -> dict:
    """向上游提交一次视频生成，返回上游任务 ID 与账号归属。"""
    from admin.routers import proxy
    from admin.routers.videos import _build_upstream_body, seconds_value, size_to_resolution
    from admin.security import get_key_row_by_id

    key = get_key_row_by_id(db, api_key_id)
    if key is None:
        return {'ok': False, 'error': 'API Key 不存在或已失效'}
    capability = video_capability(db, model)
    body = {'model': model, 'prompt': prompt}
    if seconds is not None:
        body['seconds'] = seconds
    if size:
        body['size'] = size
    if aspect_ratio:
        body['aspect_ratio'] = aspect_ratio
    if audio is not None:
        body['audio'] = audio
    if input_reference:
        body['input_reference'] = input_reference
    try:
        outgoing = _build_upstream_body(body, model, capability)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}

    budget = pool_budget(db, key, [model])
    tried: set[int] = set()
    last = '没有可用账号'
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
            session = _open_account_session(acc, 'video_generation', conversation_id)
            headers = dict(session._gateway_headers)
            release_connection(db)
            with httpx.Client(timeout=httpx.Timeout(90, connect=15),
                              limits=proxy.HTTP_LIMITS) as client:
                response = client.post(settings.BACKEND + '/v2/videos/generations',
                                       headers=headers, json=outgoing)
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            good, message = _classify_payload(payload)
            task_id = ''
            if response.is_success and good and isinstance(payload.get('data'), dict):
                task_id = str(payload['data'].get('id') or '')
            if task_id:
                proxy._note_success(db, acc)
                return {'ok': True, 'upstream_id': task_id, 'account_id': acc.id,
                        'model': model, 'resolved': {
                            'seconds': outgoing.get('seconds'),
                            'resolution': size_to_resolution(size or None, capability),
                            'aspect_ratio': (outgoing.get('extra_parameters') or {}).get('aspect_ratio', ''),
                        },
                        'summary': f'视频任务已提交（{task_id}）'}
            last = message or f'HTTP {response.status_code}'
            kind = proxy._classify_error(response.status_code, json.dumps(payload, ensure_ascii=False))
            if response.status_code < 500 and kind in {
                    'session_dead', 'hard_credit', 'soft_rate', 'model_rate',
                    'model_block', 'account_fault'}:
                proxy._apply_account_policy(db, acc, kind, response.status_code, last, model=model)
                continue
            break
        except Exception as exc:
            last = str(exc)
            break
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
    return {'ok': False, 'error': f'提交视频任务失败：{last}'}


def poll_video(db, *, upstream_id: str, account_id: int) -> dict:
    """查询一次上游视频任务状态。"""
    from admin.routers import proxy

    account = db.get(Account, account_id)
    if account is None:
        return {'ok': False, 'error': '生成该视频的账号已被删除'}
    session = None
    try:
        session = _open_account_session(account, 'video_generation')
        headers = dict(session._gateway_headers)
        release_connection(db)
        with httpx.Client(timeout=httpx.Timeout(45, connect=15),
                          limits=proxy.HTTP_LIMITS) as client:
            response = client.post(settings.BACKEND + '/v2/videos/tasks',
                                   headers=headers, json={'task_id': upstream_id})
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        good, message = _classify_payload(payload)
        if not response.is_success or not good:
            return {'ok': False, 'error': message or f'HTTP {response.status_code}',
                    'transient': True}
        data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
        status = str(data.get('status') or 'in_progress').lower()
        videos = [row for row in (data.get('data') or [])
                  if isinstance(row, dict) and isinstance(row.get('url'), str) and row['url']]
        usage = data.get('usage') if isinstance(data.get('usage'), dict) else {}
        credits = usage.get('credit')
        credits = float(credits) if isinstance(credits, (int, float)) and math.isfinite(credits) else 0.0
        return {'ok': True, 'status': status, 'url': videos[0]['url'] if videos else '',
                'credits': credits, 'usage': usage,
                'duration_s': usage.get('output_video_duration')}
    except Exception as exc:
        return {'ok': False, 'error': str(exc), 'transient': True}
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


def download_video(url: str) -> bytes:
    """下载上游视频（只接受 COS 域名）。"""
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.username or parsed.password
            or parsed.port not in (None, 443) or not host.endswith('.myqcloud.com')):
        raise RuntimeError('视频地址不是受信任的 COS 域名')
    chunks: list[bytes] = []
    total = 0
    with httpx.Client(timeout=httpx.Timeout(180, connect=20),
                      follow_redirects=False, trust_env=False) as client:
        with client.stream('GET', url) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_VIDEO_BYTES:
                    raise RuntimeError('视频超过 512 MiB 上限')
                chunks.append(chunk)
    if not chunks:
        raise RuntimeError('上游返回空视频')
    return b''.join(chunks)


def wait_video(db, *, upstream_id: str, account_id: int, timeout: float = 600,
               interval: float = 6.0, should_cancel=None) -> dict:
    """轮询到视频完成（或失败/超时）。

    同步等待是刻意的：智能体工具调用需要**拿到产物**才能继续推理/合并，
    异步返回一个 ID 会让模型不得不自己编排轮询。超时后返回 `pending`，
    由调用方决定是否延长。
    """
    started = time.time()
    last_error = ''
    while time.time() - started < timeout:
        if should_cancel and should_cancel():
            return {'ok': False, 'error': '已取消', 'cancelled': True}
        polled = poll_video(db, upstream_id=upstream_id, account_id=account_id)
        if not polled.get('ok'):
            last_error = polled.get('error', '')
            if not polled.get('transient'):
                return polled
        else:
            status = polled.get('status', '')
            if status in ('completed', 'succeeded', 'success'):
                return polled
            if status in ('failed', 'failure', 'error', 'cancelled', 'canceled'):
                return {'ok': False, 'error': '上游视频生成失败'}
        time.sleep(interval)
    return {'ok': False, 'pending': True,
            'error': f'等待 {int(timeout)} 秒仍未完成' + (f'（最近错误：{last_error}）' if last_error else '')}


def generate_video(db, *, api_key_id: int, model: str, prompt: str, seconds=None,
                   size: str = '', aspect_ratio: str = '', audio=None,
                   input_reference: str = '', conversation_id: str = '',
                   timeout: float = 600, should_cancel=None) -> dict:
    """一次完整的「提交 → 等待 → 下载 → 落盘」，供工具直接调用。"""
    from admin.routers import proxy

    submitted = submit_video(
        db, api_key_id=api_key_id, model=model, prompt=prompt, seconds=seconds,
        size=size, aspect_ratio=aspect_ratio, audio=audio,
        input_reference=input_reference, conversation_id=conversation_id)
    if not submitted.get('ok'):
        return submitted
    finished = wait_video(db, upstream_id=submitted['upstream_id'],
                         account_id=submitted['account_id'], timeout=timeout,
                         should_cancel=should_cancel)
    if not finished.get('ok'):
        if finished.get('pending'):
            # 已经计费的任务不能丢：把 ID 交给调用方，稍后可继续查询。
            return {**finished, 'upstream_id': submitted['upstream_id'],
                    'account_id': submitted['account_id'],
                    'hint': '任务已提交并计费，可用该 upstream_id 稍后重试查询'}
        return finished
    url = finished.get('url') or ''
    if not url:
        return {'ok': False, 'error': '任务完成但没有返回视频地址'}
    try:
        data = download_video(url)
    except Exception as exc:
        return {'ok': False, 'error': f'下载视频失败：{exc}'}
    credits = float(finished.get('credits') or 0)
    duration = finished.get('duration_s') or submitted.get('resolved', {}).get('seconds') or 0
    row = _store_asset(
        db, api_key_id=api_key_id, conversation_id=conversation_id, kind='video',
        model=model, data=data, mime='video/mp4', prompt=prompt, credits=credits,
        meta={'size': size, 'aspect_ratio': aspect_ratio,
              'upstream_id': submitted['upstream_id'], 'usage': finished.get('usage') or {}},
        duration_s=float(duration or 0))
    proxy._record_usage(api_key_id, submitted['account_id'], model, credits, None,
                        use_case='agent/videos', error_kind='success')
    return {'ok': True, 'asset_id': row.asset_id, 'kind': 'video', 'model': model,
            'credits': round(credits, 2), 'bytes': len(data),
            'duration_s': duration,
            'summary': f'已生成 {duration or "?"} 秒视频（{credits:.2f} 积分）'}


# ---------------------------------------------------------------------------
# 分段合并
# ---------------------------------------------------------------------------

def merge_assets(db, *, api_key_id: int, asset_ids, conversation_id: str = '',
                 prompt: str = '') -> dict:
    """把若干**已生成**的视频资产按顺序合并成一条。"""
    rows: list[AgentAsset] = []
    for asset_id in asset_ids or []:
        row = db.query(AgentAsset).filter(AgentAsset.asset_id == str(asset_id),
                                          AgentAsset.api_key_id == api_key_id).first()
        if row is None or row.kind != 'video':
            return {'ok': False, 'error': f'资产 {asset_id} 不存在或不是视频'}
        path = asset_path(row)
        if not path.is_file():
            return {'ok': False, 'error': f'资产 {asset_id} 的文件已丢失'}
        rows.append(row)
    if not rows:
        return {'ok': False, 'error': '没有可合并的视频资产'}
    if not video_merge.available():
        return {'ok': False, 'error': '当前环境没有 ffmpeg，无法合并视频'}

    asset_id = agent_meta.new_id('video')
    output = data_dir() / f'{asset_id}.mp4'
    try:
        info = video_merge.merge_videos([asset_path(row) for row in rows], output)
    except video_merge.MergeError as exc:
        return {'ok': False, 'error': str(exc)}
    data = output.read_bytes()
    meta = {'segments': [row.asset_id for row in rows], 'merge_mode': info.get('mode'),
            'source_durations': [row.duration_s for row in rows]}
    credits = sum(float(row.credits or 0) for row in rows)
    row = _store_asset(
        db, api_key_id=api_key_id, conversation_id=conversation_id, kind='video',
        model=rows[0].model, data=data, mime='video/mp4',
        prompt=prompt or rows[0].prompt, credits=0, meta=meta,
        duration_s=float(info.get('duration_s') or 0))
    return {'ok': True, 'asset_id': row.asset_id, 'kind': 'video',
            'segments': len(rows), 'duration_s': info.get('duration_s'),
            'mode': info.get('mode'), 'bytes': len(data),
            'source_credits': round(credits, 2),
            'summary': f'已合并 {len(rows)} 段，共 {info.get("duration_s")} 秒'}


def plan_long_video(total_seconds: int, model: str, db) -> dict:
    """给「目标总时长」算分段，并预估总积分。"""
    capability = video_capability(db, model)
    cap = int(capability.get('maxSeconds') or 15)
    minimum = int(capability.get('minSeconds') or 1)
    plan = video_merge.plan_segments(total_seconds, cap)
    # 太短的尾巴拼不出有意义的分镜，直接向上取整到模型最小秒数。
    plan = [max(minimum, value) if value < minimum else value for value in plan]
    per_second = learnt_video_credits_per_second(db, model)
    estimate = sum(value * per_second for value in plan)
    return {'plan': plan, 'segments': len(plan), 'max_seconds': cap,
            'min_seconds': minimum, 'per_second': per_second,
            'credits': round(estimate, 2),
            'credits_range': [round(estimate * 0.8, 2), round(estimate * 1.35, 2)],
            'note': '按本网关历史真实用量估算，仅参考，实际以上游 usage.credit 为准'}


def list_assets(db, *, api_key_id: int, kind: str = '', limit: int = 60) -> list[dict]:
    query = db.query(AgentAsset).filter(AgentAsset.api_key_id == api_key_id)
    if kind:
        query = query.filter(AgentAsset.kind == kind)
    rows = query.order_by(AgentAsset.id.desc()).limit(max(1, min(200, limit))).all()
    out = []
    for row in rows:
        out.append({
            'asset_id': row.asset_id, 'kind': row.kind, 'model': row.model,
            'bytes': row.bytes, 'duration_s': row.duration_s,
            'credits': round(float(row.credits or 0), 2),
            'prompt': (row.prompt or '')[:200],
            'created_at': row.created_at.isoformat() if row.created_at else '',
        })
    return out


def purge_expired(db=None) -> int:
    """清理过期资产（文件 + 行）。"""
    own = db is None
    if own:
        db = SessionLocal()
    try:
        cutoff = datetime.utcnow()
        rows = db.query(AgentAsset).filter(AgentAsset.expires_at.isnot(None),
                                           AgentAsset.expires_at < cutoff).all()
        removed = 0
        for row in rows:
            try:
                asset_path(row).unlink(missing_ok=True)
            except OSError:
                pass
            db.delete(row)
            removed += 1
        if removed:
            db.commit()
        return removed
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    finally:
        if own:
            db.close()