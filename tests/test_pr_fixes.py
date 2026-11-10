"""验证 PR #22 与 PR #20 的修复（合并前验证，合完可留作回归）。

PR #22: `_catalog_for_platform` 默认平台账号全灭时返回 `tried`，不再 NameError。
PR #20: anthropic 中继每个 SSE 事件都带 `event:` 名字行。
"""
import asyncio
import json
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from admin.db import Base
from admin.models import Account
from admin.routers import models as models_router
from admin.platform import protocol_bridge as bridge
from admin.platform import registry
from admin.platform.shared import exclude_identity


def frames(*payloads):
    """把 OpenAI chunk 序列包成内层 SSE 字节帧（与 test_protocol_bridge 同款）。"""
    out = []
    for p in payloads:
        if p == '[DONE]':
            out.append(b'data: [DONE]\n\n')
        else:
            out.append(('data: %s\n\n' % json.dumps(p, ensure_ascii=False)).encode())
    return out


class _FakeInner:
    """模拟内层 StreamingResponse：只提供 body_iterator。"""

    def __init__(self, chunks):
        self.body_iterator = self._gen(chunks)

    async def _gen(self, chunks):
        for c in chunks:
            yield c


def _collect(relay_response):
    async def _run():
        out = []
        async for piece in relay_response.body_iterator:
            if isinstance(piece, (bytes, bytearray)):
                out.append(bytes(piece))
            else:
                out.append(str(piece).encode('utf-8'))
        return b"".join(out)
    return asyncio.run(_run())


class ModelSyncTried(unittest.TestCase):
    """PR #22：默认平台全部账号取不到目录 → 200 + tried 列表（原为 NameError 500）。"""

    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add_all([Account(uid=f'u{i}', auth_json='{}', balance_remain=10,
                                 status='active')
                         for i in range(3)])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    @contextmanager
    def _slot(self, account, db):
        yield None

    def test_all_accounts_fail_returns_tried_not_nameerror(self):
        with patch.object(models_router, '_catalog_of',
                          side_effect=RuntimeError('上游目录不可用')), \
             patch('admin.maintenance.account_slot', self._slot):
            # 旧代码在这里抛 NameError: name 'tried' is not defined
            result = models_router._catalog_for_platform(
                self.db, registry.DEFAULT_PLATFORM)
        self.assertEqual(result.get('models'), [])
        self.assertIn('reason', result)
        tried = result.get('tried')
        self.assertIsInstance(tried, list)
        self.assertEqual(len(tried), 3)


class AnthropicSseEventLine(unittest.TestCase):
    """PR #20：anthropic 中继的每个事件都必须带 `event:` 名字行。"""

    def test_every_event_carries_event_line(self):
        # 内层是 OpenAI chunk 流：思考 → 正文 → 结束 → 用量 → [DONE]
        chunks = frames(
            {'choices': [{'delta': {'reasoning_content': 'thinking...'}}]},
            {'choices': [{'delta': {'content': 'hi'}}]},
            {'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
            {'usage': {'prompt_tokens': 1, 'completion_tokens': 1,
                       'total_tokens': 2}, 'choices': []},
            '[DONE]',
        )
        resp = bridge.anthropic_stream_relay(_FakeInner(chunks), 'm', prefix='t')
        raw = _collect(resp).decode('utf-8')
        events = [ln for ln in raw.splitlines() if ln.startswith('event:')]
        self.assertGreaterEqual(len(events), 5, raw)
        for expected in ('message_start', 'content_block_start', 'content_block_delta',
                         'content_block_stop', 'message_delta', 'message_stop'):
            self.assertIn('event: %s' % expected, events, raw)


class AccountIdentityExclusion(unittest.TestCase):
    """换号必须排除同 UID 的重复数据库记录。"""

    def test_excludes_duplicate_upstream_identity(self):
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            first = Account(uid='same-upstream', auth_json='{}', status='active')
            duplicate = Account(uid='same-upstream', auth_json='{}', status='active')
            other = Account(uid='other-upstream', auth_json='{}', status='active')
            db.add_all([first, duplicate, other])
            db.commit()
            db.refresh(first)
            db.refresh(duplicate)

            excluded = set()
            exclude_identity(db, excluded, first)

        self.assertEqual(excluded, {first.id, duplicate.id})
        engine.dispose()


if __name__ == '__main__':
    unittest.main()
