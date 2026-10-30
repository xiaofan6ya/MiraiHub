"""Short-lived catalog cache for discovery and automatic model selection."""
from collections import OrderedDict
import hashlib
import threading
import time
from model_metadata import is_media_model

_cache = OrderedDict()
_lock = threading.Lock()
TTL_SECONDS = 300
#: 缓存条目上限（按凭证+后端 URL 分片，避免账号多时无限堆积）
_CACHE_MAX = 128


def get_models(auth_json, backend_url, fetch):
    # Scope by credential/backend, without retaining raw credentials in memory.
    key = (backend_url, hashlib.sha256(auth_json.encode()).digest())
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached and now < cached[0]:
            _cache.move_to_end(key)
            return cached[1]
    rows = fetch()
    if not rows:
        return rows
    with _lock:
        _cache[key] = (time.monotonic() + TTL_SECONDS, rows)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return rows


def status():
    """缓存观测（/api/stats/memory 汇总用）。"""
    with _lock:
        return {"name": "model_catalog", "entries": len(_cache),
                "max_entries": _CACHE_MAX, "ttl_seconds": TTL_SECONDS}


def auto_model(auth_json, backend_url, fetch):
    rows = get_models(auth_json, backend_url, fetch)
    models = [row for row in rows if row.get('id') and row['id'].lower() != 'auto'
              and not is_media_model(row['id'])]
    if not models:
        return None
    free = [row for row in models if not row.get('credits') or
            'x0.00' in str(row['credits']) or 'free' in str(row['credits']).lower()]
    return (free or models)[0]['id']