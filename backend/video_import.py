"""Offline recording extraction and OCR jobs. No transaction writes or cloud APIs."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from fastapi import HTTPException
from PIL import Image

from backend.ocr_parser import ocr_image, parse_trade_text

MAX_VIDEO_BYTES = 200 * 1024 * 1024
MAX_DURATION = 180
SAMPLE_FPS = 2
MAX_FRAMES = MAX_DURATION * SAMPLE_FPS + 1
MAX_RECORDS = 3000
RETENTION_SECONDS = 30 * 60
JOB_TIMEOUT_SECONDS = 15 * 60
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.m4v'}
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='video-ocr')
_lock = threading.RLock()
_jobs = {}


class VideoLimitError(ValueError):
    pass


@dataclass
class VideoJob:
    id: str
    root: Path
    source: Path
    cancel: threading.Event = field(default_factory=threading.Event)
    status: str = 'queued'
    phase: str = '等待处理'
    processed: int = 0
    total: int = 0
    skipped: int = 0
    duration: float = 0
    latest_frame: dict = None
    frames: dict = field(default_factory=dict)
    result: dict = None
    error: str = ''
    timer: object = None


def require_tools():
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise HTTPException(503, '录屏导入需要本机安装 FFmpeg 和 ffprobe；截图导入仍可使用')
    return ffmpeg, ffprobe


def run_command(command, timeout, cancel):
    """Bound the decoder runtime and stop it when the client cancels."""
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        deadline = time.monotonic() + timeout
        while True:
            if cancel.is_set() or time.monotonic() >= deadline:
                process.kill()
                process.communicate()
                raise ValueError('处理已取消' if cancel.is_set() else '视频解码超时，请缩短录屏后重试')
            try:
                stdout, stderr = process.communicate(timeout=.5)
                if process.returncode != 0:
                    raise ValueError('无法解码视频，请使用可正常播放的 MP4/MOV/M4V 录屏文件')
                return stdout
            except subprocess.TimeoutExpired:
                continue


def probe_video(path, ffprobe, cancel):
    raw = run_command([ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
                       '-select_streams', 'v:0', '-show_entries', 'stream=width,height,duration:format=duration',
                       '-of', 'json', str(path)], 15, cancel)
    info = json.loads(raw)
    streams = info.get('streams', [])
    if not streams:
        raise ValueError('文件中没有可识别的视频画面')
    stream = streams[0]
    try:
        duration = float(info.get('format', {}).get('duration') or stream.get('duration'))
        pixels = int(stream['width']) * int(stream['height'])
    except (TypeError, ValueError, KeyError):
        raise ValueError('无法读取视频时长或分辨率，请重新导出录屏')
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('视频时长无效')
    if duration > MAX_DURATION:
        raise ValueError('单段录屏最长 3 分钟，请分段上传')
    if pixels <= 0 or pixels > 16_777_216:
        raise ValueError('视频分辨率过大，请使用手机普通清晰度录屏')
    return duration


def extract_frames(job, ffmpeg):
    target = job.root / 'frame-%04d.png'
    scale = "scale=w='min(iw,2560)':h='min(ih,2560)':force_original_aspect_ratio=decrease"
    common = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-protocol_whitelist', 'file,pipe']
    run_command(common + ['-i', str(job.source), '-map', '0:v:0', '-an', '-sn', '-dn',
                         '-vf', f'fps={SAMPLE_FPS}:start_time=0,{scale}', '-frames:v', str(MAX_FRAMES - 1),
                         '-threads', '1', str(target)], 120, job.cancel)
    paths = sorted(job.root.glob('frame-*.png'))
    samples = [(index, index / SAMPLE_FPS, path) for index, path in enumerate(paths)]
    # Sample the end explicitly, even when it falls between half-second steps.
    final_path = job.root / 'last.png'
    final_time = max(0, job.duration - .05)
    # Decode the last second and overwrite one image. Seeking directly after
    # the final frame's timestamp can produce no image on low-frame-rate clips.
    run_command(common + ['-ss', str(max(0, job.duration - 1)), '-i', str(job.source), '-map', '0:v:0',
                         '-an', '-sn', '-dn', '-vf', scale, '-fps_mode', 'passthrough',
                         '-update', '1', '-threads', '1', str(final_path)], 30, job.cancel)
    if final_path.exists():
        samples.append((len(samples), final_time, final_path))
    if not samples:
        raise ValueError('未能从录屏中提取画面')
    return samples


def pixel_fingerprint(path):
    # Exact decoded pixels only: small amount/date changes must not be discarded
    # just because the overall screen still looks similar.
    with Image.open(path) as image:
        rgb = image.convert('RGB')
        return hashlib.sha256(str(rgb.size).encode() + rgb.tobytes()).digest()


def record_key(record):
    """Only complete, minute-anchored records can be linked across adjacent frames."""
    if not all(record.get(key) is not None for key in ('date', 'symbol', 'business_type', 'cash_flow', 'trade_time')):
        return None
    return (record['date'], record['symbol'], record['business_type'], record['cash_flow'], record['trade_time'])


def add_observations(groups, records, frame_id, timestamp, previous):
    """Preserve same-frame multiplicity; only link repeated adjacent observations.

    The UI requires an explicit keep/skip choice for linked groups and can
    expand every observation. Matching date/amount alone never discards a trade.
    """
    occurrences = defaultdict(int)
    current = {}
    for index, record in enumerate(records):
        key = record_key(record)
        slot = (key, occurrences[key]) if key is not None else None
        occurrences[key] += 1
        observation = {'frame_id': frame_id, 'timestamp': timestamp, 'record_index': index}
        if slot is not None and slot in previous:
            group_index = previous[slot]
            groups[group_index]['observations'].append(observation)
        else:
            group_index = len(groups)
            groups.append({**record, 'observations': [observation]})
        if slot is not None:
            current[slot] = group_index
    return current


def public_frame(job, frame_id, timestamp, **extra):
    return {'id': frame_id, 'timestamp': round(timestamp, 2),
            'preview_url': f'/api/ocr/video/{job.id}/frames/{frame_id}', **extra}


def flag_amount_conflicts(groups):
    identities = defaultdict(set)
    for row in groups:
        key = record_key(row)
        if key is not None:
            identities[key[:3] + key[4:]].add(row['cash_flow'])
    for row in groups:
        key = record_key(row)
        if key is not None and len(identities[key[:3] + key[4:]]) > 1:
            row['video_conflict'] = True


def update(job, **changes):
    with _lock:
        for name, value in changes.items():
            setattr(job, name, value)


def process_job(job):
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    try:
        ffmpeg, ffprobe = require_tools()
        update(job, status='running', phase='读取录屏信息')
        duration = probe_video(job.source, ffprobe, job.cancel)
        update(job, duration=duration, phase='提取画面')
        samples = extract_frames(job, ffmpeg)
        update(job, total=len(samples), phase='本机识别画面')
        groups, frames, warnings = [], [], []
        previous, previous_pixels = {}, None
        empty, failed, observations = 0, 0, 0
        for number, (frame_id, timestamp, path) in enumerate(samples, 1):
            if job.cancel.is_set():
                return
            if time.monotonic() >= deadline:
                raise VideoLimitError('本段录屏处理超过 15 分钟，请缩短录屏后重试')
            pixels = pixel_fingerprint(path)
            if pixels == previous_pixels:
                job.skipped += 1
                path.unlink(missing_ok=True)
                update(job, processed=number)
                continue
            previous_pixels = pixels
            with _lock:
                job.frames[frame_id] = path
            try:
                result = ocr_image(path.read_bytes())
                if not result.get('text'):
                    raise ValueError(result.get('error') or '未识别出文字')
                parsed = parse_trade_text(result['text'], include_incomplete=True)
                records = parsed.get('records', [])
                info = public_frame(job, frame_id, timestamp, raw_text=result['text'], engine=result['engine'], count=len(records))
                if not records:
                    empty += 1
                observations += len(records)
                if observations > 30_000:
                    raise VideoLimitError('录屏内容过多，请缩短录屏并分段上传')
                previous = add_observations(groups, records, frame_id, timestamp, previous)
                if len(groups) > MAX_RECORDS:
                    raise VideoLimitError('本段录屏超过 3000 条候选记录，请分段上传')
            except VideoLimitError:
                raise
            except Exception as exc:
                failed += 1
                previous = {}  # Do not link records across an unreadable gap.
                info = public_frame(job, frame_id, timestamp, raw_text='', error=str(exc)[:300], count=0)
                warnings.append({'frame_id': frame_id, 'timestamp': round(timestamp, 2), 'error': str(exc)[:300]})
            frames.append(info)
            update(job, processed=number, latest_frame=info)
        if job.cancel.is_set():
            return
        if not groups:
            raise ValueError('未从录屏中提取到交易记录，请慢速滚动并确保文字清晰，或改用截图导入')
        for record in groups:
            record['video_repeats'] = len(record['observations'])
        flag_amount_conflicts(groups)
        result = {'records': groups, 'frames': frames, 'duration': duration,
                  'sampled_frames': len(samples), 'identical_frames_skipped': job.skipped,
                  'empty_frames': empty, 'failed_frames': failed, 'warnings': warnings,
                  'observation_count': observations}
        update(job, result=result, status='done', phase='识别完成')
    except Exception as exc:
        update(job, status='failed', phase='识别失败', error=exc.detail if isinstance(exc, HTTPException) else str(exc)[:400])
    finally:
        job.source.unlink(missing_ok=True)
        if job.cancel.is_set():
            shutil.rmtree(job.root, ignore_errors=True)
        else:
            timer = threading.Timer(RETENTION_SECONDS, delete_job, args=(job.id,))
            timer.daemon = True
            job.timer = timer
            timer.start()


def create_job(root, source):
    require_tools()
    job_id = uuid.uuid4().hex
    job = VideoJob(id=job_id, root=Path(root), source=Path(source))
    with _lock:
        if any(item.status in ('queued', 'running') for item in _jobs.values()):
            raise HTTPException(409, '已有录屏正在识别，请等待完成后再上传下一段')
        _jobs[job_id] = job
    _executor.submit(process_job, job)
    return job_id


def get_job(job_id):
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(410, '录屏预览已关闭或过期，请重新上传')
        return job


def job_snapshot(job_id):
    with _lock:
        job = get_job(job_id)
        return {'job_id': job.id, 'status': job.status, 'phase': job.phase,
                'processed': job.processed, 'total': job.total, 'skipped': job.skipped,
                'duration': job.duration, 'latest_frame': job.latest_frame,
                'error': job.error, 'result': job.result}


def frame_path(job_id, frame_id):
    job = get_job(job_id)
    with _lock:
        path = job.frames.get(frame_id)
    if path is None or not path.is_file():
        raise HTTPException(404, '该画面不存在或已过期')
    return path


def delete_job(job_id):
    with _lock:
        job = _jobs.pop(job_id, None)
        if job is None:
            return
        job.cancel.set()
        if job.timer:
            job.timer.cancel()
        finished = job.status in ('done', 'failed')
    if finished:
        shutil.rmtree(job.root, ignore_errors=True)


async def receive_video(file):
    filename = file.filename or ''
    suffix = Path(filename).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        raise HTTPException(400, '录屏仅支持 MP4、MOV、M4V 格式')
    require_tools()
    root = Path(tempfile.mkdtemp(prefix='xirr-video-'))
    source = root / ('recording' + suffix)
    try:
        size = 0
        with source.open('wb') as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_VIDEO_BYTES:
                    raise HTTPException(413, '单段录屏最大 200MB，请缩短录屏或降低录制分辨率')
                output.write(chunk)
        if size == 0:
            raise HTTPException(400, '录屏文件为空')
        return create_job(root, source)
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise
    finally:
        await file.close()


def shutdown_jobs():
    with _lock:
        ids = list(_jobs)
    for job_id in ids:
        delete_job(job_id)
