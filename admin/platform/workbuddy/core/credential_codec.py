"""Read and write WorkBuddy's encrypted .info fields.

The installed Electron binary supplies its own build key in Node mode. Keys
stay in memory; no bundled keys, GUI launch, or key files are required.
"""
from __future__ import annotations

import base64
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess


class CredentialDecodeError(ValueError):
    """Only fixed, credential-free messages may cross the import boundary."""


_KEY_SCRIPT = """
try {
  const crypto = require('node:crypto');
  const payload = JSON.parse(process._linkedBinding('electron_browser_workbuddy_storage').loggerGet());
  if (payload.version !== 1 || typeof payload.atRestSecretKey !== 'string') throw Error();
  const raw = Buffer.from(payload.atRestSecretKey, 'base64');
  if (raw.length !== 32 || raw.toString('base64') !== payload.atRestSecretKey || raw.every(x => x === 0)) throw Error();
  const key = crypto.createHash('sha256').update(payload.atRestSecretKey, 'utf8').digest();
  const keyId = crypto.createHash('sha256').update(key).digest('hex').slice(0, 16);
  process.stdout.write(JSON.stringify({keyId, key: key.toString('base64')}));
} catch (_) { process.exitCode = 1; }
"""


def _base64(value):
    if not isinstance(value, str):
        raise ValueError()
    decoded = base64.b64decode(value, validate=True)
    if base64.b64encode(decoded).decode('ascii') != value:
        raise ValueError()
    return decoded


@lru_cache(maxsize=4)
def _installed_key(executable: str, mtime: int, size: int) -> tuple[str, bytes]:
    # File identity invalidates the cache after a WorkBuddy upgrade.
    env = dict(os.environ, ELECTRON_RUN_AS_NODE='1')
    env.pop('NODE_OPTIONS', None)
    try:
        result = subprocess.run([executable, '-e', _KEY_SCRIPT], env=env,
                                capture_output=True, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or len(result.stdout) > 4096:
            raise ValueError()
        value = json.loads(result.stdout)
        key = _base64(value['key'])
        key_id = hashlib.sha256(key).hexdigest()[:16]
        if len(key) != 32 or value['keyId'] != key_id:
            raise ValueError()
        return key_id, key
    except Exception:
        raise CredentialDecodeError('无法读取本机客户端的凭证解密能力，请更新 WorkBuddy 或使用手机号授权登录') from None


def _local_key() -> tuple[str, bytes]:
    configured = os.getenv('WORKBUDDY_CREDENTIAL_KEY_FILE')
    key_file = Path(configured) if configured else Path(__file__).resolve().parents[1] / '.workbuddy' / 'credential-key.json'
    if configured or key_file.exists():
        try:
            if key_file.stat().st_size > 4096:
                raise ValueError()
            value = json.loads(key_file.read_text(encoding='utf-8'))
            key = _base64(value['key'])
            key_id = hashlib.sha256(key).hexdigest()[:16]
            if len(key) != 32 or value['keyId'] != key_id or not any(key):
                raise ValueError()
            return key_id, key
        except Exception:
            raise CredentialDecodeError('凭证加密运行配置无效，请管理员重新配置官方客户端加密能力') from None
    if os.name != 'nt' or os.getenv('WORKBUDDY_ANDROID') == '1':
        raise CredentialDecodeError('未配置官方客户端加密能力，请管理员配置 WORKBUDDY_CREDENTIAL_KEY_FILE；也可使用明文备份或手机号授权登录')
    from wb_install import WB
    install = WB.install_dir()
    executable = Path(install) / 'WorkBuddy.exe' if install else None
    if executable is None or not executable.is_file():
        raise CredentialDecodeError('这是新版客户端加密凭证，未找到本机 WorkBuddy；请配置客户端安装目录或使用手机号授权登录')
    stat = executable.stat()
    return _installed_key(str(executable), stat.st_mtime_ns, stat.st_size)


def _aad(key_id: str) -> bytes:
    def text(value):
        encoded = value.encode('utf-8')
        return struct.pack('>I', len(encoded)) + encoded
    # Official sym-v1 field framing (WBEV1), suite 1, no sequence/final.
    return (b'WB-AAD\0\x01' + text('WBEV1') + text('sym-v1') +
            struct.pack('>I', 1) + text(key_id) + b'\x02\x00\x00')


def _open_field(wrapper: dict, key_id: str, key: bytes) -> str:
    if wrapper.get('scheme') is not None:
        raise CredentialDecodeError('此凭证使用暂不支持的高级加密格式，请使用手机号授权登录')
    try:
        if set(wrapper) != {'$wbEncrypted', 'envelope'} or wrapper['$wbEncrypted'] != 1:
            raise ValueError()
        envelope = json.loads(_base64(wrapper['envelope']))
        if set(envelope) != {'suite', 'keyId', 'nonce', 'authTag', 'ciphertext'} or type(envelope['suite']) is not int or envelope['suite'] != 1:
            raise ValueError()
        if envelope['keyId'] != key_id:
            raise CredentialDecodeError('凭证加密版本与本机 WorkBuddy 不匹配，请更新客户端后重新导入，或使用手机号授权登录')
        nonce, tag, ciphertext = (_base64(envelope[name]) for name in ('nonce', 'authTag', 'ciphertext'))
        if len(nonce) != 12 or len(tag) != 16:
            raise ValueError()
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).decrypt(nonce, ciphertext + tag, _aad(key_id)).decode('utf-8')
    except CredentialDecodeError:
        raise
    except ImportError:
        raise CredentialDecodeError('解密凭证缺少 cryptography 依赖，请安装 requirements.txt') from None
    except Exception:
        raise CredentialDecodeError('加密凭证损坏或校验失败，请重新导出客户端凭证或使用手机号授权登录') from None


def normalize_credential(raw: str) -> str:
    """Keep legacy JSON unchanged; fully decode encrypted fields before validation."""
    try:
        data = json.loads(raw.lstrip('\ufeff'))
    except (ValueError, TypeError):
        return raw  # The existing validator reports malformed credentials.
    key_material = None
    changed = False

    def visit(value, depth=0):
        nonlocal key_material, changed
        if depth > 32:
            raise CredentialDecodeError('凭证结构嵌套过深，请使用完整的客户端 .info 文件')
        if isinstance(value, dict):
            if '$wbEncrypted' in value:
                if key_material is None:
                    key_material = _local_key()
                changed = True
                return _open_field(value, *key_material)
            return {k: visit(v, depth + 1) for k, v in value.items()}
        if isinstance(value, list):
            return [visit(v, depth + 1) for v in value]
        return value

    decoded = visit(data)
    return json.dumps(decoded, ensure_ascii=False) if changed else raw.lstrip('\ufeff')


def encrypt_credential(raw: str) -> str:
    """Official standard field framing; fresh nonce per field and per export."""
    data = json.loads(normalize_credential(raw))
    if not isinstance(data, dict) or not isinstance(data.get('auth'), dict):
        raise CredentialDecodeError('凭证结构无效，无法导出客户端文件')
    key_id, key = _local_key()
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    cipher = AESGCM(key)

    def protect(area, names):
        if not isinstance(area, dict):
            return
        for name in names:
            value = area.get(name)
            if not isinstance(value, str) or not value:
                continue
            nonce = os.urandom(12)
            sealed = cipher.encrypt(nonce, value.encode('utf-8'), _aad(key_id))
            envelope = {'suite': 1, 'keyId': key_id,
                        'nonce': base64.b64encode(nonce).decode('ascii'),
                        'authTag': base64.b64encode(sealed[-16:]).decode('ascii'),
                        'ciphertext': base64.b64encode(sealed[:-16]).decode('ascii')}
            area[name] = {'$wbEncrypted': 1, 'envelope': base64.b64encode(
                json.dumps(envelope, separators=(',', ':')).encode('utf-8')).decode('ascii')}

    protect(data['auth'], ('accessToken', 'refreshToken'))
    profile_fields = ('phoneNumber', 'nickname', 'departmentFullName')
    protect(data.get('account'), profile_fields)
    for group in ('accounts', 'allAccounts'):
        for account in data.get(group, []) if isinstance(data.get(group), list) else []:
            protect(account, profile_fields)
    return json.dumps(data, ensure_ascii=False)