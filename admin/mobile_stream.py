"""Keep mobile SSE readers alive and finish settlement before terminal events."""
import asyncio
import json
import re

import anyio
from starlette.responses import Response, StreamingResponse

from admin import request_metrics

_boundary = re.compile(rb'\r?\n\r?\n')
HEARTBEAT_INTERVAL = 5


class MobileResponse(Response):
    """Watch disconnects during preflight and keep slow first tokens alive.

    Preflight may still return an HTTP error before the first heartbeat. Once
    bytes have been sent, failures must use the protocol's SSE error event.
    """
    def __init__(self, source, prepare):
        super().__init__()
        self.source = source
        self.prepare = prepare

    async def __call__(self, scope, receive, send):
        stream = events(self.source, interval=HEARTBEAT_INTERVAL)
        response = None

        async def run():
            nonlocal response
            try:
                response = await self.prepare(stream)
                if isinstance(response, StreamingResponse):
                    # This outer task group already owns disconnect monitoring,
                    # including the wait before any response headers are sent.
                    await response.stream_response(send)
                else:
                    await response(scope, receive, send)
            finally:
                with anyio.CancelScope(shield=True):
                    if isinstance(response, StreamingResponse):
                        await response.body_iterator.aclose()
                    await stream.aclose()

        async with anyio.create_task_group() as group:
            async def produce():
                await run()
                group.cancel_scope.cancel()
            group.start_soon(produce)
            while True:
                if (await receive())['type'] == 'http.disconnect':
                    group.cancel_scope.cancel()
                    break


def _event(frame):
    event, data = '', []
    for line in frame.decode('utf-8', errors='replace').splitlines():
        if line.startswith('event:'):
            event = line[6:].strip()
        elif line.startswith('data:'):
            data.append(line[5:].lstrip())
    text = '\n'.join(data)
    if text == '[DONE]':
        return True, False
    try:
        obj = json.loads(text)
    except (TypeError, ValueError):
        return False, False
    if not isinstance(obj, dict):
        return False, False
    kind = event or obj.get('type', '')
    error = bool(obj.get('error')) or kind in ('error', 'response.failed')
    if error:
        request_metrics.mark_stream_error()
    terminal = error or kind in ('message_stop', 'response.completed', 'response.failed', 'response.incomplete')
    delta = obj.get('delta')
    meaningful = isinstance(delta, str) and bool(delta)
    if isinstance(delta, dict):
        meaningful = any(delta.get(k) for k in ('text', 'thinking', 'partial_json'))
    for choice in obj.get('choices') or []:
        if isinstance(choice, dict):
            delta = choice.get('delta') or {}
            if isinstance(delta, dict):
                meaningful = meaningful or any(delta.get(k) for k in ('content', 'reasoning_content', 'reasoning', 'tool_calls'))
    return terminal, meaningful


async def events(source, interval=5):
    """Send complete frames and heartbeats; drain settlement before termination."""
    pending, buffer, terminal = None, b'', []
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(source.__anext__())
            ready, _ = await asyncio.wait({pending}, timeout=interval)
            if not ready:
                yield b': keep-alive\n\n'
                continue
            task, pending = pending, None
            try:
                chunk = task.result()
            except StopAsyncIteration:
                break
            buffer += chunk.encode('utf-8') if isinstance(chunk, str) else chunk
            while True:
                boundary = _boundary.search(buffer)
                if boundary is None:
                    break
                frame, buffer = buffer[:boundary.end()], buffer[boundary.end():]
                ended, meaningful = _event(frame)
                if meaningful:
                    request_metrics.mark_first_delta()
                if ended:
                    terminal.append(frame)
                else:
                    yield frame
        if buffer:
            yield buffer
        for frame in terminal:
            request_metrics.mark_terminal()
            yield frame
    finally:
        # StreamingResponse uses level cancellation on disconnect. Shield
        # producer cleanup so the next request can reuse its slots and session.
        with anyio.CancelScope(shield=True):
            if pending is not None:
                pending.cancel()
                try:
                    await pending
                except (asyncio.CancelledError, StopAsyncIteration):
                    pass
            await source.aclose()