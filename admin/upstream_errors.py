"""Keep upstream evidence separate from gateway decisions and client UI text."""
from __future__ import annotations

import json
from pathlib import Path
import re


_MESSAGES = json.loads((Path(__file__).with_name('data') / 'upstream_error_messages.json').read_text(encoding='utf-8'))


class UpstreamError(RuntimeError):
    def __init__(self, context: str, status: int, body: str):
        self.http_status = status
        self.raw_body = body
        self.upstream = error_info(body, status)
        super().__init__(f'{context}: HTTP {status} / {body}')


def error_info(body: str, status: int = 0) -> dict:
    raw = body or ''
    text = raw
    for prefix in ('[estimated] ', '[upstream] '):
        text = text.removeprefix(prefix)
    candidates = [text] + [line[5:].strip() for line in text.splitlines() if line.startswith('data:')]
    # Backend exceptions prefix the actual JSON response with method/path/HTTP.
    if not text.lstrip().startswith(('{', '[')) and not text.startswith(('data:', 'event:')):
        start = text.find('{')
        if start >= 0:
            candidates.append(text[start:])

    def visit(obj, depth=0):
        if not isinstance(obj, dict) or depth > 5 or 'choices' in obj:
            return None
        code = obj.get('code')
        if code is not None and not isinstance(code, bool) and str(code) not in ('', '0', '200'):
            return obj
        for key in ('error', 'data'):
            found = visit(obj.get(key), depth + 1)
            if found:
                return found
        if any(isinstance(obj.get(k), str) for k in ('msg', 'message', 'detail', 'details')):
            return obj
        return None

    obj = None
    for candidate in candidates:
        try:
            obj = visit(json.loads(candidate))
        except (ValueError, TypeError):
            continue
        if obj:
            break
    code = str(obj['code']) if obj and obj.get('code') is not None else None
    if code is None and text.lstrip().startswith('{') and '"choices"' not in text:
        # Legacy cooldown reasons were limited to 255 characters and may end
        # inside a JSON string. Recover only their explicit envelope code.
        codes = re.findall(r'"code"\s*:\s*"?(-?\d+)"?', text)
        code = next((c for c in codes if c not in ('0', '200')), None)
    message = next((obj[k] for k in ('msg', 'message', 'detail', 'details')
                    if isinstance(obj.get(k), str)), None) if obj else None
    display = obj.get('displayMsg') if obj else None
    client = _MESSAGES.get(code or str(status or http_status(raw)), {})
    return {'http_status': status or http_status(raw) or None, 'code': code,
            'message': message, 'display_message': display, 'raw': raw,
            'client_message': client.get('message'), 'client_description': client.get('description')}


def http_status(message: str) -> int:
    # Never mistake a port, retry duration or a business code for HTTP status.
    match = re.search(r'\bHTTP(?:/\d(?:\.\d)?)?\s+(\d{3})\b', message or '', re.I)
    return int(match[1]) if match else 0


def stored_info(value: str, fallback: str = '') -> dict:
    try:
        result = json.loads(value or '')
        if isinstance(result, dict):
            return result
    except (ValueError, TypeError):
        pass
    return error_info(fallback)