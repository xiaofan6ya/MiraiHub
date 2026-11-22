"""Keep deployment device-identity switches out of the test process.

``admin.config`` loads the repository ``.env`` on import, so a deployment that
enabled ``QIMEI_SNAPSHOT_PATH`` / ``QIMEI_MANAGED_IDENTITY`` / a generator route
would otherwise change the behaviour under test. Tests that need these values
set them explicitly (``patch.dict`` / ``setUp``); everything else starts clean.
"""
from __future__ import annotations

import os

import pytest

#: 只清「部署开关」：数据库、账号、日志之类的配置照常从 .env 生效。
_SWITCHES = (
    "QIMEI_STATE_DIR",
    "QIMEI_TABLE_PATH",
    "QIMEI_POOL_PATH",
    "QIMEI_SNAPSHOT_PATH",
    "QIMEI_MANAGED_IDENTITY",
    "QIMEI_MAX_OWNERS_PER_DEVICE",
    "QIMEI_GEN_URL",
    "QIMEI_GEN_CMD",
    "QIMEI_GEN_SHELL",
    "QIMEI_GEN_TIMEOUT",
    "QIMEI_GEN_SUDO",
    "QIMEI_PYTHON_GENERATOR_ENABLED",
    "QIMEI_PYTHON_GENERATOR_CONFIG",
    "MARVIS_QIMEI36",
    "MARVIS_USKEY_BACKEND",
    "MARVIS_USKEY_SERVICE",
    "MARVIS_USKEY_FORCE_SIDECAR",
)


def _clear() -> dict:
    saved = {}
    for name in _SWITCHES:
        if name in os.environ:
            saved[name] = os.environ.pop(name)
    return saved


_clear()


@pytest.fixture(autouse=True)
def _deployment_env_isolation():
    """每个用例前后都回到「没开任何设备身份开关」的状态。"""
    saved = _clear()
    try:
        yield
    finally:
        _clear()
        os.environ.update(saved)
