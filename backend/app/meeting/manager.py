"""会议记录的后台处理。

每场会议一个驱动协程，按主状态往前推：queued → transcoding → transcribing → processing → done。
不占翻译任务的并发名额：转码一次只跑一个，等服务商结果时只是轮询，大模型步骤同一时间只处理一场会议。
服务重启后按数据库里的状态续上：已经提交给服务商的分段接着查同一个任务号，不重复提交（避免重复计费）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import secrets
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from ..config import Config
from ..db import utcnow
from ..events import EventBus
from ..models import AsrProvider, GlossaryTerm, Meeting, MeetingSegment
from ..security import SecretBox
from ..settings_store import load_settings
from . import processing, split
from .align import Alignment, MergedSegment, align_parts
from .asr import adapter_class
from .asr.base import AsrAdapter, AsrError, SubmitOptions
from .media import AUDIO_NAME, FfmpegProcess, MediaError, probe, tone, transcode_args
from .schemas import MeetingOut, load_secrets

log = logging.getLogger("bdw.meetings")

PERSIST_INTERVAL = 3.0
HOUSEKEEPING_INTERVAL = 1800
UPLOAD_TTL = timedelta(hours=24)
TOKEN_TTL = timedelta(hours=24)  # 听悟要求下载地址至少有效 3 小时
POLL_TIMEOUT = timedelta(hours=8)
MAX_TRANSIENT = 6  # 网络或服务端临时错误连续重试的次数
PROBE_TIMEOUT = 240
PROBE_ID = "probe"
# ffprobe 认出这些格式时拒绝：播放列表、拼接列表等会让 ffmpeg 去读服务器上的其他文件
UNSAFE_FORMATS = {"hls", "concat", "image2", "tty", "lavfi", "ffmetadata", "sdp", "rtp", "rtsp", "applehttp", "data"}
# 进度区间：转码 0–10，识别 10–60，大模型 60–100
TRANSCODE_SPAN = (0.0, 10.0)
ASR_SPAN = (10.0, 60.0)

T = TypeVar("T")


class StageError(Exception):
    def __init__(self, message: str, kind: str = "internal"):
        super().__init__(message)
        self.message = message
        self.kind = kind


@dataclass
class Probe:
    path: Path
    expires: float
    hits: list[str] = field(default_factory=list)


def source_name(filename: str) -> str:
    """上传原件在磁盘上的名字，保留扩展名方便 ffmpeg 识别格式。"""
    suffix = Path(filename).suffix.lower()
    if not suffix or len(suffix) > 8 or not suffix[1:].isalnum():
        suffix = ".bin"
    return "source" + suffix


class MeetingManager:
    def __init__(self, config: Config, session_factory: sessionmaker, bus: EventBus, secrets_box: SecretBox):
        self.config = config
        self.Session = session_factory
        self.bus = bus
        self.secrets = secrets_box
        self.transport: httpx.AsyncBaseTransport | None = None  # 测试时注入假的服务商和大模型
        self.http: httpx.AsyncClient | None = None
        self.tasks: dict[str, asyncio.Task] = {}
        self.op_tasks: dict[str, asyncio.Task] = {}
        self.cancel_requested: set[str] = set()
        self.discard_requested: set[str] = set()
        self.ffmpeg: dict[str, FfmpegProcess] = {}
        self.live: dict[str, tuple[float, str]] = {}
        self.owners: dict[str, int] = {}
        self.probes: dict[str, Probe] = {}
        self._last_persist: dict[str, float] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._housekeeping_task: asyncio.Task | None = None
        self._test_tasks: dict[int, asyncio.Task] = {}
        self.transcode_sem: asyncio.Semaphore | None = None
        self.llm_sem: asyncio.Semaphore | None = None

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.config.meetings_dir.mkdir(parents=True, exist_ok=True)
        self.transcode_sem = asyncio.Semaphore(1)
        self.llm_sem = asyncio.Semaphore(1)
        self.http = httpx.AsyncClient(
            transport=self.transport, timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True
        )
        for meeting_id in self._recover():
            self.launch(meeting_id)
        self._housekeeping_task = asyncio.create_task(self._housekeeping(), name="bdw-meeting-housekeeping")

    async def stop(self) -> None:
        if self._housekeeping_task:
            self._housekeeping_task.cancel()
        pending = [*self.tasks.values(), *self.op_tasks.values(), *self._test_tasks.values()]
        for proc in list(self.ffmpeg.values()):
            proc.kill()
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait(pending, timeout=15)
        if self._housekeeping_task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._housekeeping_task
        if self.http:
            await self.http.aclose()

    def meeting_dir(self, meeting_id: str) -> Path:
        return self.config.meetings_dir / meeting_id

    def _recover(self) -> list[str]:
        resume: list[str] = []
        with self.Session() as db:
            rows = db.scalars(
                select(Meeting).where(
                    Meeting.deleted_at.is_(None),
                    Meeting.status.in_(("queued", "transcoding", "transcribing", "processing", "done")),
                )
            ).all()
            for m in rows:
                if m.op:
                    # 服务重启打断了完成后的单项重跑：清掉标记，让用户重新点
                    m.op = None
                    if m.transcript_state == "polishing":
                        m.transcript_state = "partial"
                    if m.minutes_state == "generating":
                        m.minutes_state = "failed"
                    set_warning(m, "restart", "服务重启打断了正在进行的处理，请重新操作")
                if m.status == "done":
                    continue
                if m.status == "transcoding":
                    m.status = "queued"
                    with contextlib.suppress(OSError):
                        (self.meeting_dir(m.id) / "audio.part.mp3").unlink()
                if m.status == "processing":
                    if m.transcript_state == "polishing":
                        m.transcript_state = "partial"
                    if m.minutes_state == "generating":
                        m.minutes_state = "none"
                resume.append(m.id)
            db.commit()
        if resume:
            log.warning("resuming %d meetings after restart", len(resume))
        return resume

    # ---------- 线程安全的入口（接口在线程池里调用） ----------

    def _call(self, fn: Callable[..., Any], *args: Any) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            fn(*args)
        else:
            self._loop.call_soon_threadsafe(fn, *args)

    def request_launch(self, meeting_id: str) -> None:
        self._call(self.launch, meeting_id)

    def request_cancel(self, meeting_id: str) -> None:
        self._call(self._cancel, meeting_id)

    def request_discard(self, meeting_id: str) -> None:
        """会议被删除：停掉正在跑的处理，再删目录。"""
        self._call(self._discard, meeting_id)

    def is_running(self, meeting_id: str) -> bool:
        return meeting_id in self.tasks or meeting_id in self.op_tasks

    def launch(self, meeting_id: str) -> None:
        task = self.tasks.get(meeting_id)
        if task and not task.done():
            return
        assert self._loop is not None
        self.tasks[meeting_id] = self._loop.create_task(self._drive(meeting_id), name=f"bdw-meeting-{meeting_id}")

    def _cancel(self, meeting_id: str) -> None:
        task = self.tasks.get(meeting_id)
        if task and not task.done():
            self.cancel_requested.add(meeting_id)
            proc = self.ffmpeg.get(meeting_id)
            if proc:
                proc.kill()
            task.cancel()
            return
        # 没在跑（例如刚完成上传、驱动还没起来）：直接改状态
        with self.Session() as db:
            m = db.get(Meeting, meeting_id)
            if m and m.status in ("queued", "transcoding", "transcribing", "processing"):
                m.status = "canceled"
                m.finished_at = utcnow()
                m.stage = ""
                db.commit()
        self.publish(meeting_id)

    def _discard(self, meeting_id: str) -> None:
        running = [t for t in (self.tasks.get(meeting_id), self.op_tasks.get(meeting_id)) if t and not t.done()]
        if running:
            self.discard_requested.add(meeting_id)
            proc = self.ffmpeg.get(meeting_id)
            if proc:
                proc.kill()
            for task in running:
                task.cancel()
            return
        self.remove_files(meeting_id)

    def remove_files(self, meeting_id: str) -> None:
        shutil.rmtree(self.meeting_dir(meeting_id), ignore_errors=True)

    # ---------- 驱动 ----------

    async def _drive(self, meeting_id: str) -> None:
        try:
            while True:
                status = self._status(meeting_id)
                if status in ("queued", "transcoding"):
                    await self._transcode(meeting_id)
                elif status == "transcribing":
                    await self._transcribe(meeting_id)
                elif status == "processing":
                    await self._process(meeting_id)
                else:
                    break
        except asyncio.CancelledError:
            if meeting_id in self.cancel_requested and meeting_id not in self.discard_requested:
                self._update(meeting_id, status="canceled", finished_at=utcnow(), stage="")
            raise
        except StageError as e:
            self._fail(meeting_id, e.message, e.kind)
        except Exception as e:
            log.exception("meeting %s failed", meeting_id)
            self._fail(meeting_id, f"内部错误：{e.__class__.__name__}: {e}"[:500], "internal")
        finally:
            self.tasks.pop(meeting_id, None)
            self.cancel_requested.discard(meeting_id)
            self.live.pop(meeting_id, None)
            self._last_persist.pop(meeting_id, None)
            proc = self.ffmpeg.pop(meeting_id, None)
            if proc:
                proc.kill()
                proc.close()
            if meeting_id in self.discard_requested and meeting_id not in self.op_tasks:
                self.discard_requested.discard(meeting_id)
                self.remove_files(meeting_id)
            else:
                self.publish(meeting_id)

    def _status(self, meeting_id: str) -> str | None:
        with self.Session() as db:
            m = db.get(Meeting, meeting_id)
            if m is None or m.deleted_at is not None:
                return None
            self.owners[meeting_id] = m.user_id
            return m.status

    def _fail(self, meeting_id: str, message: str, kind: str) -> None:
        self._update(meeting_id, status="failed", error=message[:2000], error_kind=kind, finished_at=utcnow())
        log.warning("meeting %s failed (%s): %s", meeting_id, kind, message)

    # ---------- 转码 ----------

    async def _transcode(self, meeting_id: str) -> None:
        directory = self.meeting_dir(meeting_id)
        with self.Session() as db:
            m = db.get(Meeting, meeting_id)
            assert m is not None
            src = directory / source_name(m.filename)
            m.status = "transcoding"
            m.stage = "transcode"
            m.progress = 0
            m.error = None
            m.error_kind = None
            m.attempts = (m.attempts or 0) + 1
            m.started_at = m.started_at or utcnow()
            settings = load_settings(db)
            provider_kind = m.provider_kind
            db.commit()
        self.publish(meeting_id)
        if not src.is_file():
            raise StageError("上传的原文件已经不在了，请重新上传", "input")
        try:
            info = await asyncio.to_thread(probe, self.config.ffprobe, src)
        except MediaError as e:
            raise StageError(str(e), "input") from e
        if not info.has_audio:
            raise StageError("这个文件里没有音频", "input")
        if any(name in UNSAFE_FORMATS for name in info.format_name.split(",")):
            raise StageError("不支持这种文件格式，请上传普通的音频或视频文件", "input")
        limit_ms = settings.max_audio_hours * 3600 * 1000
        if info.duration_ms > limit_ms + 60_000:
            raise StageError(f"录音时长 {_clock(info.duration_ms)} 超过了上限 {settings.max_audio_hours} 小时", "input")
        tmp = directory / "audio.part.mp3"
        assert self.transcode_sem is not None
        async with self.transcode_sem:
            try:
                proc = FfmpegProcess(transcode_args(self.config.ffmpeg, src, tmp), directory / "media.log")
            except MediaError as e:
                raise StageError(str(e), "internal") from e
            self.ffmpeg[meeting_id] = proc
            # 正常转码远快于实时；卡住的 ffmpeg 不能一直占着全站唯一的转码名额
            limit_s = max(600.0, info.duration_ms / 1000 * 0.5 + 300)
            try:
                code = await asyncio.wait_for(
                    proc.wait(
                        info.duration_ms,
                        lambda r: self._progress(meeting_id, _span(TRANSCODE_SPAN, r), "transcode"),
                    ),
                    timeout=limit_s,
                )
            except TimeoutError as e:
                proc.kill()
                raise StageError(f"转码超过 {int(limit_s // 60)} 分钟仍未完成，文件可能有问题", "input") from e
            finally:
                self.ffmpeg.pop(meeting_id, None)
                proc.close()
        if code != 0:
            raise StageError(f"转码失败（ffmpeg 退出码 {code}），文件可能已损坏", "input")
        audio = directory / AUDIO_NAME
        os.replace(tmp, audio)
        try:
            out_info = await asyncio.to_thread(probe, self.config.ffprobe, audio)
        except MediaError as e:
            raise StageError(str(e), "internal") from e
        duration = out_info.duration_ms or info.duration_ms
        if duration <= 0:
            raise StageError("读不出录音时长，文件可能已损坏", "input")
        if duration > limit_ms + 60_000:
            # 有的文件头里写的时长不准（如没有 Xing 头的 VBR MP3），转码后的时长才可靠
            with contextlib.suppress(OSError):
                audio.unlink()
            raise StageError(f"录音时长 {_clock(duration)} 超过了上限 {settings.max_audio_hours} 小时", "input")
        parts = await self._plan_parts(meeting_id, audio, duration, provider_kind)
        self._update(
            meeting_id,
            duration_ms=duration,
            asr_parts=parts,
            status="transcribing",
            stage="asr_submit",
            progress=ASR_SPAN[0],
        )

    async def _plan_parts(
        self, meeting_id: str, audio: Path, duration_ms: int, provider_kind: str
    ) -> list[dict[str, Any]]:
        cls = adapter_class(provider_kind)
        if cls is None:
            raise StageError("这场会议选用的识别服务类型已不存在", "config")
        cap = cls.capability
        size = audio.stat().st_size
        if duration_ms <= cap.max_part_seconds * 1000 and size <= cap.max_bytes:
            return [_new_part(0, 0, duration_ms, AUDIO_NAME)]
        self._progress(meeting_id, TRANSCODE_SPAN[1], "split")
        try:
            planned = await asyncio.to_thread(
                split.plan_parts, self.config, audio, duration_ms, cap.max_part_seconds * 1000
            )
        except MediaError as e:
            raise StageError(str(e), "internal") from e
        return [_new_part(int(p["index"]), int(p["offset_ms"]), int(p["duration_ms"]), str(p["file"])) for p in planned]

    # ---------- 识别 ----------

    def adapter_for(self, provider: AsrProvider) -> AsrAdapter:
        cls = adapter_class(provider.kind)
        if cls is None:
            raise StageError(f"不认识的识别服务类型：{provider.kind}", "config")
        assert self.http is not None
        return cls(provider.config or {}, load_secrets(provider, self.secrets), self.http)

    def audio_url(self, base: str, meeting_id: str, index: int, token: str) -> str:
        return f"{base or 'http://mock.invalid'}/api/public/meeting-audio/{meeting_id}/{index}/{token}.mp3"

    def hotwords(self, limit: int = 128) -> list[str]:
        with self.Session() as db:
            return [t.term for t in db.scalars(select(GlossaryTerm).order_by(GlossaryTerm.id)).all()][:limit]

    async def _transcribe(self, meeting_id: str) -> None:
        with self.Session() as db:
            m = db.get(Meeting, meeting_id)
            assert m is not None
            provider = db.get(AsrProvider, m.provider_id) if m.provider_id else None
            if provider is None or not provider.enabled:
                raise StageError("这场会议选用的识别服务已被删除或停用，请联系管理员", "config")
            adapter = self.adapter_for(provider)
            base = load_settings(db).public_base_url
            parts = [dict(p) for p in m.asr_parts or []]
            language = m.language
            expected = m.expected_speakers
            m.stage = "asr_wait" if any(p.get("task_id") for p in parts) else "asr_submit"
            db.commit()
        if not base and adapter.kind != "mock":
            raise StageError("管理员还没有设置站点公网地址，识别服务拉不到录音", "config")
        if not parts:
            raise StageError("没有可识别的音频分段，请重试", "internal")
        cap = adapter.capability
        hotwords = self.hotwords() if cap.hotwords else []
        single = len(parts) == 1
        progress: dict[int, float] = {int(p["index"]): (1.0 if p.get("state") == "done" else 0.0) for p in parts}

        def report() -> None:
            ratio = sum(progress.values()) / len(progress)
            self._progress(meeting_id, _span(ASR_SPAN, ratio), "asr_wait")

        async def run(part: dict[str, Any]) -> None:
            opts = SubmitOptions(
                language=language,
                expected_speakers=expected if single and cap.speaker_count else None,
                hotwords=hotwords,
                duration_ms=int(part.get("duration_ms") or 0),
            )

            def on_progress(ratio: float) -> None:
                progress[int(part["index"])] = ratio
                report()

            await self._run_part(meeting_id, adapter, base, int(part["index"]), opts, on_progress)

        try:
            async with asyncio.TaskGroup() as group:
                for part in parts:
                    group.create_task(run(part))
        except BaseExceptionGroup as eg:
            # 某一段失败时其余分段会被取消；只把第一个真正的错误报出去
            raise eg.exceptions[0] from None

        self._progress(meeting_id, ASR_SPAN[1], "asr_merge")
        alignment = await asyncio.to_thread(self._merge_results, meeting_id, adapter)
        merged, notes = alignment.segments, alignment.notes
        if not merged:
            raise StageError("识别结果是空的：录音里可能没有人声，或音量太小", "input")
        await asyncio.to_thread(self._store_segments, meeting_id, merged, alignment.merge_hints)
        with self.Session() as db:
            m = db.get(Meeting, meeting_id)
            assert m is not None
            m.asr_seconds = math.ceil((m.duration_ms or 0) / 1000)
            m.asr_parts = [{**p, "token": None, "token_exp": None} for p in m.asr_parts or []]
            m.status = "processing"
            m.stage = "speakers"
            m.progress = ASR_SPAN[1]
            set_warning(m, "asr", "；".join(notes) if notes else None)
            src = self.meeting_dir(m.id) / source_name(m.filename)
            db.commit()
        # 识别成功后原件和切段文件就用不到了（回听用转好的 audio.mp3），WAV 原件可能上 GB
        with contextlib.suppress(OSError):
            src.unlink()
        for part_file in self.meeting_dir(meeting_id).glob(split.PART_PATTERN):
            with contextlib.suppress(OSError):
                part_file.unlink()
        self.publish(meeting_id)

    async def _run_part(
        self,
        meeting_id: str,
        adapter: AsrAdapter,
        base: str,
        index: int,
        opts: SubmitOptions,
        on_progress: Callable[[float], None],
    ) -> None:
        part = self._part(meeting_id, index)
        raw_name = f"asr-{index}.json"
        if (self.meeting_dir(meeting_id) / raw_name).is_file():
            # 结果已经落盘（可能是写完后、改状态前被打断）：不必再查
            if part.get("state") != "done":
                self._update_part(meeting_id, index, state="done", raw=raw_name, error=None)
            on_progress(1.0)
            return
        task_id = part.get("task_id")
        if not task_id and part.get("state") == "submitting":
            # 上次正在提交时服务中断：服务商可能已经建好任务，自动重提有重复计费的风险，交给用户决定
            self._update_part(meeting_id, index, state="failed", final=True, error="提交时服务中断")
            raise StageError(
                "上次提交识别时服务中断，服务商可能已经收到任务；为免重复计费没有自动重新提交，确认后请点重试",
                "provider",
            )
        if not task_id:
            token = secrets.token_urlsafe(32)
            self._update_part(
                meeting_id, index, state="submitting", token=token, token_exp=_iso(utcnow() + TOKEN_TTL), error=None
            )
            url = self.audio_url(base, meeting_id, index, token)
            try:
                task_id = await self._submit(lambda: adapter.submit(url, opts))
            except AsrError as e:
                # 提交被拒或不确定是否送达：没有任务号可续查，重试时只能重新提交
                self._update_part(meeting_id, index, state="failed", final=True, error=str(e))
                raise StageError(f"提交识别失败：{e}", _kind(e.kind)) from e
            self._update_part(
                meeting_id, index, state="submitted", task_id=task_id, submitted_at=_iso(utcnow()), final=False
            )
            self.publish(meeting_id)
        else:
            # 重启后续查：服务商可能还要来拉音频，令牌过期就顺延（地址已经交给服务商了，不能换）
            exp = _parse_iso(part.get("token_exp"))
            if part.get("token") and (exp is None or exp < utcnow() + timedelta(hours=1)):
                self._update_part(meeting_id, index, token_exp=_iso(utcnow() + TOKEN_TTL))
            if part.get("state") != "submitted":
                self._update_part(meeting_id, index, state="submitted")
        submitted = _parse_iso(self._part(meeting_id, index).get("submitted_at")) or utcnow()
        ttl = timedelta(hours=adapter.task_ttl_hours) if adapter.task_ttl_hours else POLL_TIMEOUT
        if utcnow() - submitted > min(ttl, POLL_TIMEOUT):
            # 先判断再查询：过期的任务号在有的服务商那里会被复用，查到的可能是别人的结果
            self._update_part(meeting_id, index, state="failed", final=True, error="任务已过期")
            raise StageError("服务商那边的识别任务已过期，请点重试重新识别", "provider")
        low, high = adapter.poll_interval
        delay = low
        transient = 0
        started = time.monotonic()
        expected_ms = max(1, int(opts.duration_ms or 0))
        while True:
            try:
                result = await adapter.poll(task_id)
                transient = 0
            except (AsrError, httpx.HTTPError) as e:
                retryable = e.retryable if isinstance(e, AsrError) else True
                if retryable and transient < MAX_TRANSIENT:
                    transient += 1
                    await asyncio.sleep(min(high, low * 2**transient))
                    continue
                kind = e.kind if isinstance(e, AsrError) else "network"
                # input 类（例如结果地址失效）说明这个任务的结果拿不回来了；其余（断网、鉴权）重试时续查同一个任务
                self._update_part(meeting_id, index, state="failed", final=kind == "input", error=str(e))
                raise StageError(f"查询识别结果失败：{e}", _kind(kind)) from e
            if result.state == "done":
                path = self.meeting_dir(meeting_id) / raw_name
                data = json.dumps(result.raw, ensure_ascii=False)
                await asyncio.to_thread(path.write_text, data, "utf-8")
                self._update_part(meeting_id, index, state="done", raw=raw_name, error=None)
                on_progress(1.0)
                return
            if result.state == "failed":
                self._update_part(meeting_id, index, state="failed", final=True, error=result.error)
                raise StageError(f"识别失败：{result.error or '服务商没有给出原因'}", _kind(result.error_kind))
            if result.progress is not None:
                on_progress(max(0.0, min(0.99, result.progress)))
            else:
                # 服务商不报进度时按经验估一个（实际处理通常远快于音频时长），封顶 95%
                elapsed_ms = (time.monotonic() - started) * 1000
                on_progress(min(0.95, elapsed_ms / (expected_ms * 0.15 + 30_000)))
            if utcnow() - submitted > min(ttl, POLL_TIMEOUT):
                self._update_part(meeting_id, index, state="failed", final=True, error="超时")
                raise StageError("识别超时：服务商长时间没有返回结果", "provider")
            await asyncio.sleep(delay)
            delay = min(high, delay * 1.5)

    async def _submit(self, call: Callable[[], Awaitable[T]]) -> T:
        """提交只在确定服务商没收到时重试：连不上，或被限流（429）。超时、5xx 时任务可能已经建好，
        再提交会重复计费，所以直接报错，由用户决定是否重试。"""
        attempt = 0
        while True:
            try:
                return await call()
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                if attempt >= 3:
                    raise AsrError(f"连不上识别服务：{e.__class__.__name__}: {e}", "network") from e
            except httpx.HTTPError as e:
                raise AsrError(
                    f"提交时网络中断（{e.__class__.__name__}），服务商可能已经收到任务；为免重复计费没有自动重试",
                    "network",
                ) from e
            except AsrError as e:
                if e.kind != "quota" or not e.retryable or attempt >= 3:
                    raise
            attempt += 1
            await asyncio.sleep(2**attempt)

    def _merge_results(self, meeting_id: str, adapter: AsrAdapter) -> Alignment:
        parts = self._parts(meeting_id)
        loaded = []
        for p in sorted(parts, key=lambda x: int(x["offset_ms"])):
            raw = json.loads((self.meeting_dir(meeting_id) / str(p["raw"])).read_text("utf-8"))
            loaded.append((int(p["offset_ms"]), int(p["duration_ms"]), adapter.parse(raw)))
        return align_parts(loaded)

    def _store_segments(
        self, meeting_id: str, merged: list[MergedSegment], hints: dict[str, dict[str, str]] | None = None
    ) -> None:
        with self.Session() as db:
            m = db.get(Meeting, meeting_id)
            if m is None or m.deleted_at is not None:
                return
            db.execute(delete(MeetingSegment).where(MeetingSegment.meeting_id == meeting_id))
            speakers: dict[str, Any] = {}
            for i, seg in enumerate(merged):
                speakers.setdefault(seg.speaker, {"name": "", "guess": None, "merged_into": None, "merge_hint": None})
                db.add(
                    MeetingSegment(
                        meeting_id=meeting_id,
                        idx=i,
                        start_ms=seg.start_ms,
                        end_ms=seg.end_ms,
                        asr_speaker=seg.speaker,
                        speaker=seg.speaker,
                        raw_text=seg.text,
                        text=seg.text,
                    )
                )
            # 切段对齐时配不上的说话人：提示“可能和谁是同一人”；source 标明来源，大模型猜名字时不会清掉
            for sid, hint in (hints or {}).items():
                if sid in speakers:
                    speakers[sid]["merge_hint"] = {**hint, "source": "align"}
            m.speakers = speakers
            m.transcript_state = "raw"
            m.transcript_rev = (m.transcript_rev or 0) + 1
            db.commit()

    # ---------- 大模型 ----------

    async def _process(self, meeting_id: str) -> None:
        self._progress(meeting_id, ASR_SPAN[1], "speakers")
        assert self.llm_sem is not None
        async with self.llm_sem:
            warnings = await processing.run_pipeline_steps(self, meeting_id)
        with self.Session() as db:
            m = db.get(Meeting, meeting_id)
            assert m is not None
            m.status = "done"
            m.stage = ""
            m.progress = 100
            m.finished_at = utcnow()
            for key, text in warnings.items():
                set_warning(m, key, text)
            db.commit()

    def request_op(self, meeting_id: str, op: str, params: dict[str, Any]) -> None:
        self._call(self._launch_op, meeting_id, op, params)

    def _launch_op(self, meeting_id: str, op: str, params: dict[str, Any]) -> None:
        assert self._loop is not None
        self.op_tasks[meeting_id] = self._loop.create_task(
            self._run_op(meeting_id, op, params), name=f"bdw-meeting-op-{meeting_id}"
        )

    async def _run_op(self, meeting_id: str, op: str, params: dict[str, Any]) -> None:
        warning: str | None = None
        with self.Session() as db:
            m = db.get(Meeting, meeting_id)
            if m is not None:
                # 重跑开始：清掉这一步和“被打断”“跳过了”这类旧提示，结束后按结果重新写
                for key in (op, "restart", "pipeline"):
                    set_warning(m, key, None)
                db.commit()
        try:
            assert self.llm_sem is not None
            async with self.llm_sem:
                warning = await processing.run_op(self, meeting_id, op, params)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("meeting %s op %s failed", meeting_id, op)
            warning = f"处理失败：{e.__class__.__name__}: {e}"[:500]
        finally:
            self.op_tasks.pop(meeting_id, None)
            self.live.pop(meeting_id, None)
            self._last_persist.pop(meeting_id, None)
            if meeting_id in self.discard_requested and meeting_id not in self.tasks:
                self.discard_requested.discard(meeting_id)
                self.remove_files(meeting_id)
            else:
                with self.Session() as db:
                    m = db.get(Meeting, meeting_id)
                    if m is not None:
                        m.op = None
                        set_warning(m, op, warning)
                        db.commit()
                self.publish(meeting_id)

    # ---------- 状态与事件 ----------

    def _update(self, meeting_id: str, **values: Any) -> None:
        with self.Session() as db:
            m = db.get(Meeting, meeting_id)
            if m is None:
                return
            for key, value in values.items():
                setattr(m, key, value)
            db.commit()
        if "progress" in values or "stage" in values:
            self.live.pop(meeting_id, None)
        self.publish(meeting_id)

    def _parts(self, meeting_id: str) -> list[dict[str, Any]]:
        with self.Session() as db:
            m = db.get(Meeting, meeting_id)
            return [dict(p) for p in (m.asr_parts if m else [])]

    def _part(self, meeting_id: str, index: int) -> dict[str, Any]:
        for p in self._parts(meeting_id):
            if int(p.get("index", -1)) == index:
                return p
        raise StageError(f"找不到第 {index + 1} 段音频", "internal")

    def _update_part(self, meeting_id: str, index: int, **values: Any) -> None:
        # 只在事件循环线程里调用，读改写之间没有 await，不会和其他分段的更新交错
        with self.Session() as db:
            m = db.get(Meeting, meeting_id)
            if m is None:
                return
            m.asr_parts = [({**p, **values} if int(p.get("index", -1)) == index else p) for p in m.asr_parts or []]
            db.commit()

    def _progress(self, meeting_id: str, value: float, stage: str) -> None:
        previous = self.live.get(meeting_id)
        self.live[meeting_id] = (value, stage)
        owner = self.owners.get(meeting_id)
        if owner is not None:
            self.bus.publish(
                owner, {"type": "meeting_progress", "id": meeting_id, "progress": round(value, 2), "stage": stage}
            )
        now = time.monotonic()
        if previous is None or previous[1] != stage or now - self._last_persist.get(meeting_id, 0) >= PERSIST_INTERVAL:
            self._last_persist[meeting_id] = now
            with self.Session() as db:
                m = db.get(Meeting, meeting_id)
                if m is not None:
                    m.progress = value
                    m.stage = stage
                    db.commit()

    def set_progress(self, meeting_id: str, value: float, stage: str) -> None:
        """给大模型处理步骤用：value 是 0–1，映射到 60–100。"""
        self._progress(meeting_id, _span((ASR_SPAN[1], 100.0), value), stage)

    def meeting_out(self, m: Meeting, retention_days: int | None = None) -> MeetingOut:
        if retention_days is None:
            with self.Session() as db:
                retention_days = load_settings(db).file_retention_days
        out = MeetingOut.of(
            m, retention_days=retention_days, audio_exists=(self.meeting_dir(m.id) / AUDIO_NAME).is_file()
        )
        live = self.live.get(m.id)
        if live and m.status not in ("done", "failed", "canceled"):
            out.progress, out.stage = round(live[0], 2), live[1]
        return out

    def publish(self, meeting_id: str) -> None:
        with self.Session() as db:
            m = db.get(Meeting, meeting_id)
            if m is None or m.deleted_at is not None:
                return
            self.owners[meeting_id] = m.user_id
            payload = self.meeting_out(m, load_settings(db).file_retention_days).model_dump(mode="json")
            user_id = m.user_id
        self.bus.publish(user_id, {"type": "meeting", "meeting": payload})

    # ---------- 定期清理 ----------

    async def _housekeeping(self) -> None:
        await asyncio.sleep(60)
        while True:
            try:
                await asyncio.to_thread(self.purge)
            except Exception:
                log.exception("meeting housekeeping failed")
            await asyncio.sleep(HOUSEKEEPING_INTERVAL)

    def purge(self) -> tuple[int, int]:
        """删掉 24 小时没传完的上传，以及过了保留期的录音（文字保留）。返回 (上传数, 录音数)。"""
        now = utcnow()
        stale_uploads: list[tuple[str, int]] = []
        expired: list[str] = []
        with self.Session() as db:
            cutoff = now - timedelta(days=load_settings(db).file_retention_days)
            for m in db.scalars(
                select(Meeting).where(Meeting.status == "uploading", Meeting.created_at < now - UPLOAD_TTL)
            ):
                stale_uploads.append((m.id, m.user_id))
                db.delete(m)
            rows = db.scalars(
                select(Meeting).where(
                    Meeting.audio_purged.is_(False),
                    Meeting.deleted_at.is_(None),
                    Meeting.status.in_(("done", "failed", "canceled")),
                )
            ).all()
            for m in rows:
                if m.id in self.tasks or m.id in self.op_tasks:
                    continue
                if (m.finished_at or m.created_at) < cutoff:
                    m.audio_purged = True
                    expired.append(m.id)
            db.commit()
        orphans: list[Path] = []
        if self.config.meetings_dir.is_dir():
            with self.Session() as db:
                alive = set(db.scalars(select(Meeting.id).where(Meeting.deleted_at.is_(None))).all())
            for directory in self.config.meetings_dir.iterdir():
                if directory.is_dir() and not directory.name.startswith("_") and directory.name not in alive:
                    orphans.append(directory)
        for directory in orphans:
            if directory.name not in self.tasks and directory.name not in self.op_tasks:
                shutil.rmtree(directory, ignore_errors=True)
        for meeting_id, user_id in stale_uploads:
            self.remove_files(meeting_id)
            self.bus.publish(user_id, {"type": "meeting_removed", "id": meeting_id})
        for meeting_id in expired:
            directory = self.meeting_dir(meeting_id)
            for path in directory.glob("*"):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                elif path.suffix != ".json" and path.name != "media.log":
                    with contextlib.suppress(OSError):
                        path.unlink()
        if stale_uploads or expired:
            log.info("purged %d stale uploads and audio of %d meetings", len(stale_uploads), len(expired))
        return len(stale_uploads), len(expired)

    # ---------- 后台“测试连接”：生成一段测试音频，走真实的公网地址让服务商来拉 ----------

    def request_provider_test(self, provider_id: int, user_id: int) -> bool:
        if provider_id in self._test_tasks and not self._test_tasks[provider_id].done():
            return False
        self._call(self._launch_test, provider_id, user_id)
        return True

    def _launch_test(self, provider_id: int, user_id: int) -> None:
        assert self._loop is not None
        self._test_tasks[provider_id] = self._loop.create_task(self._provider_test(provider_id, user_id))

    def probe_file(self, token: str) -> Probe | None:
        probe_ = self.probes.get(token)
        if probe_ is None or probe_.expires < time.time() or not probe_.path.is_file():
            return None
        return probe_

    async def _provider_test(self, provider_id: int, user_id: int) -> None:
        steps: list[dict[str, Any]] = []
        ok = False
        token = secrets.token_urlsafe(32)
        directory = self.config.meetings_dir / f"_{PROBE_ID}"
        path = directory / f"{token}.mp3"

        def step(name: str, passed: bool, message: str) -> None:
            steps.append({"name": name, "ok": passed, "message": message})
            self.bus.publish(user_id, {"type": "asr_test", "provider_id": provider_id, "done": False, "steps": steps})

        try:
            with self.Session() as db:
                provider = db.get(AsrProvider, provider_id)
                if provider is None:
                    step("读取配置", False, "识别服务不存在")
                    return
                adapter = self.adapter_for(provider)
                base = load_settings(db).public_base_url
            check = await adapter.check_credentials()
            step("检查密钥", check.ok, check.message)
            if not check.ok:
                return
            if not base and adapter.kind != "mock":
                step("站点公网地址", False, "请先在“系统设置”里填写站点公网地址")
                return
            directory.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(tone, self.config.ffmpeg, path, 3)
            self.probes[token] = Probe(path=path, expires=time.time() + PROBE_TIMEOUT + 60)
            url = self.audio_url(base, PROBE_ID, 0, token)
            if base:
                assert self.http is not None
                try:
                    head = await self.http.head(url, timeout=20)
                    ranged = await self.http.get(url, headers={"Range": "bytes=0-99"}, timeout=20)
                    self_ok = head.status_code == 200 and ranged.status_code == 206
                    step(
                        "本站公网地址自查",
                        self_ok,
                        f"HEAD {head.status_code}（长度 {head.headers.get('content-length', '?')}），"
                        f"分段下载 {ranged.status_code}",
                    )
                except httpx.HTTPError as e:
                    step("本站公网地址自查", False, f"访问失败：{e.__class__.__name__}: {e}")
                self.probes[token].hits.clear()
            task_id = await adapter.submit(url, SubmitOptions(duration_ms=3000))
            deadline = time.monotonic() + PROBE_TIMEOUT
            low, high = adapter.poll_interval
            delay = low
            while True:
                result = await adapter.poll(task_id)
                if result.state != "pending":
                    break
                if time.monotonic() > deadline:
                    step("服务商拉取并识别测试音频", False, f"{PROBE_TIMEOUT} 秒内没有完成，任务号 {task_id}")
                    return
                await asyncio.sleep(delay)
                delay = min(high, delay * 1.5)
            hits = len(self.probes[token].hits)
            if result.state == "done":
                step("服务商拉取并识别测试音频", True, f"完成（服务商来拉取了 {hits} 次）")
                ok = True
            else:
                step("服务商拉取并识别测试音频", False, f"{result.error or '识别失败'}（服务商来拉取了 {hits} 次）")
        except AsrError as e:
            step("调用服务商", False, str(e))
        except (MediaError, httpx.HTTPError) as e:
            step("调用服务商", False, f"{e.__class__.__name__}: {e}")
        except NotImplementedError:
            step("调用服务商", False, "这家服务的对接还没实现")
        except Exception as e:
            log.exception("provider test failed")
            step("调用服务商", False, f"内部错误：{e.__class__.__name__}: {e}")
        finally:
            self.probes.pop(token, None)
            with contextlib.suppress(OSError):
                path.unlink()
            self._test_tasks.pop(provider_id, None)
            self.bus.publish(
                user_id, {"type": "asr_test", "provider_id": provider_id, "done": True, "ok": ok, "steps": steps}
            )


def _new_part(index: int, offset_ms: int, duration_ms: int, file: str) -> dict[str, Any]:
    return {
        "index": index,
        "offset_ms": offset_ms,
        "duration_ms": duration_ms,
        "file": file,
        "state": "pending",
        "task_id": None,
        "token": None,
        "token_exp": None,
        "submitted_at": None,
        "raw": None,
        "error": None,
        "final": False,
    }


def _span(span: tuple[float, float], ratio: float) -> float:
    return span[0] + (span[1] - span[0]) * max(0.0, min(1.0, ratio))


def _kind(kind: str) -> str:
    return {"network": "provider"}.get(kind, kind)


def set_warning(m: Meeting, key: str, text: str | None) -> None:
    """按来源写警告（text 为空表示清掉这一条），同时更新给界面显示的 warning 文字。"""
    warnings = {k: v for k, v in (m.warnings or {}).items() if v}
    if text:
        warnings[key] = text
    else:
        warnings.pop(key, None)
    m.warnings = warnings
    m.warning = "\n".join(warnings.values()) or None


def _clock(ms: int) -> str:
    seconds = ms // 1000
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
