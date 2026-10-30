"""视频分段合并：把多个短片段拼成一条完整长视频。

为什么需要
----------
上游视频模型单次最长 15 秒（`videoGeneration.maxSeconds`）。要做长视频只能
「分段生成 + 本地拼接」。拼接必须在本机做，因为：

  * 上游只负责生成片段，不提供拼接能力；
  * 片段是签名下载地址，会把整条链路拉长，本地拼接最快、最省积分；
  * 用户可能需要反复调整分镜顺序，本地拼接不产生额外费用。

实现选择
--------
用 ffmpeg（业界标准，单文件、无需 Python 绑定）。ffmpeg 的获取顺序：

  1. 环境变量 `WB_FFMPEG`（运维显式指定，最高优先级）；
  2. `imageio_ffmpeg` 自带的静态二进制（pip 依赖，跨平台，免装系统 ffmpeg）；
  3. 系统 PATH 里的 `ffmpeg`。

**优先 `-c copy` 无损拼接**：所有片段来自同一模型同一参数，编码一致，直接
复用码流最快、画质零损失。只有编码参数确实不一致（ffmpeg 报错）才退到
重编码 —— 重编码很慢且会掉画质，能不用就不用。

Android 上没有 ffmpeg 二进制，模块会明确报「不可用」，而不是给一个坏文件。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

_logger = logging.getLogger(__name__)

#: 单次合并的片段数上限。分镜再多也不该在一条 ffmpeg 命令里堆上百段：
#: 命令行长度、临时文件数和失败重试成本都会失控。
MAX_SEGMENTS = 40

#: 合并超时（秒）。纯 `-c copy` 几乎是 IO 速度；重编码按 15 秒片段估
#: 约 10~30 秒一段，这里给足余量但不无限等。
COPY_TIMEOUT = 180
RE_ENCODE_TIMEOUT = 1800

_FFMPEG_CACHE: dict[str, str | None] = {}


class MergeError(RuntimeError):
    """合并失败（片段缺失、ffmpeg 不可用、编码不兼容等）。"""


def ffmpeg_path() -> str | None:
    """定位 ffmpeg 可执行文件；找不到返回 None（调用方决定如何降级）。"""
    if 'path' in _FFMPEG_CACHE:
        return _FFMPEG_CACHE['path']
    found: str | None = None
    explicit = (os.getenv('WB_FFMPEG') or '').strip()
    if explicit:
        candidate = Path(explicit)
        if candidate.is_file():
            found = str(candidate)
        else:
            _logger.warning('WB_FFMPEG 指向的文件不存在：%s', explicit)
    if found is None:
        try:
            # imageio_ffmpeg 随 pip 安装，包含平台对应的静态二进制。
            import imageio_ffmpeg
            candidate = imageio_ffmpeg.get_ffmpeg_exe()
            if candidate and Path(candidate).is_file():
                found = candidate
        except Exception:
            pass
    if found is None:
        candidate = shutil.which('ffmpeg')
        if candidate:
            found = candidate
    _FFMPEG_CACHE['path'] = found
    return found


def available() -> bool:
    """当前环境能否合并视频（前端用来决定是否禁用「合并」按钮）。"""
    return ffmpeg_path() is not None


def probe_duration(path) -> float | None:
    """读取媒体时长（秒）；读不到返回 None。

    用 ffmpeg 自己解析而不是第三方库：合并前已经要依赖它，
    少一个依赖就少一处平台差异。
    """
    exe = ffmpeg_path()
    if not exe:
        return None
    result = _run([exe, '-hide_banner', '-i', str(path)], timeout=60, check=False)
    # ffmpeg 把媒体信息打到 stderr，格式形如 `Duration: 00:00:04.03, ...`
    for line in (result.stderr or '').splitlines():
        line = line.strip()
        if not line.startswith('Duration:'):
            continue
        raw = line.split('Duration:', 1)[1].split(',', 1)[0].strip()
        parts = raw.split(':')
        if len(parts) != 3:
            continue
        try:
            hours, minutes, seconds = (float(part) for part in parts)
        except ValueError:
            continue
        return round(hours * 3600 + minutes * 60 + seconds, 3)
    return None


def _run(command, timeout: float, check: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            [str(part) for part in command],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        raise MergeError(f'ffmpeg 执行超时（{timeout:g} 秒）') from None
    except OSError as exc:
        raise MergeError(f'无法启动 ffmpeg：{exc}') from None
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip().splitlines()
        tail = ' / '.join(detail[-3:])[:400] if detail else '无输出'
        raise MergeError(f'ffmpeg 失败（退出码 {result.returncode}）：{tail}')
    return result


def merge_videos(paths, output, *, re_encode_fallback: bool = True) -> dict:
    """把 `paths` 按顺序拼成 `output`。

    返回 `{"output": 路径, "segments": N, "duration_s": 秒, "mode": "copy"|"re-encode"}`。
    """
    exe = ffmpeg_path()
    if not exe:
        raise MergeError(
            '当前环境没有可用的 ffmpeg，无法合并视频。请安装 ffmpeg，'
            '或用环境变量 WB_FFMPEG 指定可执行文件路径。')

    files = [Path(p) for p in paths]
    if not files:
        raise MergeError('没有可合并的视频片段')
    if len(files) > MAX_SEGMENTS:
        raise MergeError(f'一次最多合并 {MAX_SEGMENTS} 个片段（当前 {len(files)} 个）')
    missing = [str(p) for p in files if not p.is_file() or p.stat().st_size == 0]
    if missing:
        raise MergeError('以下片段不存在或为空：' + '、'.join(missing[:5]))

    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    if len(files) == 1:
        # 单段不需要 ffmpeg：直接复制，避免多一次不必要的转封装。
        shutil.copy2(files[0], target)
        return {'output': str(target), 'segments': 1,
                'duration_s': probe_duration(target), 'mode': 'copy'}

    with tempfile.TemporaryDirectory(prefix='wb-merge-') as tmp:
        listing = Path(tmp) / 'segments.txt'
        # concat demuxer 的清单格式：每行 file '路径'，单引号需转义。
        listing.write_text(
            '\n'.join("file '" + str(p.resolve()).replace("'", "'\\''") + "'" for p in files)
            + '\n', encoding='utf-8')

        def attempt(mode: str) -> None:
            if mode == 'copy':
                command = [exe, '-hide_banner', '-loglevel', 'error', '-y',
                           '-f', 'concat', '-safe', '0', '-i', str(listing),
                           '-c', 'copy', '-movflags', '+faststart', str(target)]
                _run(command, COPY_TIMEOUT)
            else:
                command = [exe, '-hide_banner', '-loglevel', 'error', '-y',
                           '-f', 'concat', '-safe', '0', '-i', str(listing),
                           '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
                           '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
                           '-c:a', 'aac', '-b:a', '128k', str(target)]
                _run(command, RE_ENCODE_TIMEOUT)

        mode = 'copy'
        try:
            attempt('copy')
        except MergeError as exc:
            if not re_encode_fallback:
                raise
            # 编码参数不一致（分辨率/帧率/编码器不同）时无损拼接会失败，
            # 此时才付出重编码的代价。原始错误保留在日志里便于定位。
            _logger.info('无损拼接失败，改用重编码：%s', exc)
            mode = 're-encode'
            if target.exists():
                target.unlink()
            attempt(mode)

    size = target.stat().st_size if target.is_file() else 0
    if not size:
        raise MergeError('合并结束但没有产出文件')
    return {'output': str(target), 'segments': len(files),
            'duration_s': probe_duration(target), 'mode': mode, 'bytes': size}


def describe() -> dict:
    """当前环境能力（管理端 / 智能体用来提示用户）。"""
    exe = ffmpeg_path()
    return {'available': bool(exe), 'engine': exe or '',
            'max_segments': MAX_SEGMENTS}


def plan_segments(total_seconds: int, max_seconds: int) -> list[int]:
    """把目标总时长切成每段不超过 `max_seconds` 的分段时长。

    上游单次上限 15 秒，而用户想要 60 秒就得分成 4 段。这里按**尽量等长**
    切分而不是「前面全 15、最后剩 3」：后者会出现一个明显短于其它的尾巴，
    拼接后观感很碎。
    """
    total = max(1, int(total_seconds))
    cap = max(1, int(max_seconds))
    if total <= cap:
        return [total]
    count = (total + cap - 1) // cap
    base, remainder = divmod(total, count)
    # 余数摊到前几段，保证每段都 ≥1 秒且总和精确等于 total。
    return [base + (1 if index < remainder else 0) for index in range(count)]


def dumps(info: dict) -> str:
    """给工具结果用的稳定 JSON（避免 ensure_ascii 带来的转义噪音）。"""
    return json.dumps(info, ensure_ascii=False)