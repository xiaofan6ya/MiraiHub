"""Android loopback HTTP policy; desktop and hosted Uvicorn are unaffected."""
import asyncio
from collections import deque
import time
import os
import sys
import threading

import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol


class LoopHealth:
    def __init__(self, runtime=None):
        self.samples = deque(maxlen=240)
        self.stalls = deque(maxlen=12)
        self.runtime = runtime
        self.runtime_state = {}
        self.last_tick = time.perf_counter()
        self.last_cpu = time.process_time()
        self.thread_id = threading.get_ident()
        self.closed = threading.Event()
        self.worker = None

    def start(self):
        self.worker = threading.Thread(target=self._sample, daemon=True, name='mobile-loop-watchdog')
        self.worker.start()

    def stop(self):
        self.closed.set()

    def _sample(self):
        last_runtime, sampled_tick = 0, None
        while not self.closed.wait(.5):
            now = time.perf_counter()
            if self.runtime is not None and now-last_runtime >= 5:
                try:
                    import json
                    self.runtime_state = json.loads(self.runtime.runtimeDiagnostics())
                except Exception:
                    pass
                last_runtime = now
            tick = self.last_tick
            if now-tick < 2 or sampled_tick == tick:
                continue
            # Filenames/function names only: never capture source lines, frame
            # locals, request content, headers, account data or credentials.
            frame = sys._current_frames().get(self.thread_id)
            stack = []
            while frame is not None and len(stack) < 10:
                stack.append(f'{os.path.basename(frame.f_code.co_filename)}:{frame.f_lineno} {frame.f_code.co_name}')
                frame = frame.f_back
            self.stalls.append({'observed_at': now, 'paused_ms': round((now-tick)*1000, 1),
                                'process_cpu_ms': round(max(0, time.process_time()-self.last_cpu)*1000, 1),
                                'stack': stack, 'android': dict(self.runtime_state)})
            sampled_tick = tick

    async def watch(self):
        while True:
            began = time.perf_counter()
            self.last_tick = began
            self.last_cpu = time.process_time()
            await asyncio.sleep(.25)
            ended = time.perf_counter()
            self.samples.append((began, ended, max(0, ended - began - .25)))

    def since(self, began):
        result = {'event_loop_lag_ms': round(max((lag for start, end, lag in tuple(self.samples)
                                                 if end >= began), default=0) * 1000, 1)}
        if self.runtime_state:
            result['android_runtime'] = dict(self.runtime_state)
        evidence = [{k:v for k,v in item.items() if k != 'observed_at'}
                    for item in tuple(self.stalls) if item['observed_at'] >= began]
        if evidence:
            result['event_loop_stalls'] = evidence[-3:]
        return result


class MobileServer(uvicorn.Server):
    async def serve(self, sockets=None):
        self.config.mobile_loop_health = LoopHealth(getattr(self.config, 'mobile_runtime', None))
        self.config.mobile_loop_health.start()
        monitor = asyncio.create_task(self.config.mobile_loop_health.watch())
        try:
            await super().serve(sockets=sockets)
        finally:
            self.config.mobile_loop_health.stop()
            monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                pass


class MobileH11Protocol(H11Protocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        original_app = self.app
        self.first_byte_at = None

        async def mobile_app(scope, receive, send):
            # Only API connections are short lived. A localhost TCP handshake
            # avoids stale sockets in Android HttpURLConnection's idle pool.
            is_api = scope.get('path', '').startswith('/v1/')
            async def mobile_send(message):
                if is_api and message['type'] == 'http.response.start':
                    headers = [(k, v) for k, v in message.get('headers', []) if k.lower() != b'connection']
                    message = {**message, 'headers': headers + [(b'connection', b'close')]}
                await send(message)
            await original_app(scope, receive, mobile_send)
        self.app = mobile_app

    def data_received(self, data):
        if self.first_byte_at is None:
            self.first_byte_at = time.perf_counter()
        super().data_received(data)

    def handle_events(self):
        super().handle_events()
        scope = getattr(self, 'scope', None)
        if scope is not None and '_mobile_http' not in scope:
            now = time.perf_counter()
            health = getattr(self.config, 'mobile_loop_health', None)
            scope['_mobile_http'] = {'received_at': self.first_byte_at or now,
                                     'headers_at': now, 'health': health}

    def on_response_complete(self):
        self.first_byte_at = None
        super().on_response_complete()
