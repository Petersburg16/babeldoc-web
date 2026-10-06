"""任务调度：控制并发、按用户公平取队、驱动引擎子进程、推送进度、服务重启时把中断的任务放回队列。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from .config import Config
from .db import utcnow
from .engine import EngineProcess, build_spec, child_env, read_events, spawn
from .events import EventBus
from .models import JOB_FINISHED, AuthSession, Job, ModelProfile
from .schemas import JobOut
from .security import SecretBox
from .services import job_dir, queue_positions, remove_job_files
from .settings_store import load_settings

log = logging.getLogger("bdw.worker")

PERSIST_INTERVAL = 3.0
HOUSEKEEPING_INTERVAL = 1800


@dataclass
class Outcome:
    status: str
    error: str | None = None
    kind: str | None = None
    files: dict[str, str] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    warning: str | None = None
    tokens: int = 0


@dataclass
class RunningJob:
    job_id: str
    user_id: int
    task: asyncio.Task | None = None
    process: EngineProcess | None = None
    cancel_requested: bool = False
    interrupted: bool = False
    progress: float = 0.0
    stage: str = ""
    last_persist: float = 0.0


class JobManager:
    def __init__(self, config: Config, session_factory: sessionmaker, bus: EventBus, secrets: SecretBox):
        self.config = config
        self.Session = session_factory
        self.bus = bus
        self.secrets = secrets
        self.running: dict[str, RunningJob] = {}
        self.engine_version: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._recover()
        self._tasks = [
            asyncio.create_task(self._scheduler(), name="bdw-scheduler"),
            asyncio.create_task(self._housekeeping(), name="bdw-housekeeping"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        pending = []
        for rj in list(self.running.values()):
            rj.interrupted = True
            if rj.process:
                rj.process.kill()
            if rj.task:
                pending.append(rj.task)
        if pending:
            await asyncio.wait(pending, timeout=15)

    def wake(self) -> None:
        if self._loop and self._wake and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._wake.set)

    def cancel(self, job_id: str) -> bool:
        rj = self.running.get(job_id)
        if rj is None:
            return False
        rj.cancel_requested = True
        if rj.process:
            rj.process.kill()
        return True

    def live_progress(self, job_id: str) -> tuple[float, str] | None:
        rj = self.running.get(job_id)
        return (rj.progress, rj.stage) if rj else None

    def _recover(self) -> None:
        with self.Session() as db:
            stale = db.scalars(select(Job).where(Job.status == "running")).all()
            for job in stale:
                job.status = "queued"
                job.progress = 0
                job.stage = ""
                job.started_at = None
                shutil.rmtree(job_dir(self.config.jobs_dir, job.id) / "work", ignore_errors=True)
            db.commit()
        if stale:
            log.warning("requeued %d jobs interrupted by restart", len(stale))

    async def _scheduler(self) -> None:
        assert self._wake is not None
        while True:
            try:
                self._schedule()
            except Exception:
                log.exception("scheduler pass failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=5)
            self._wake.clear()

    def _schedule(self) -> None:
        started = False
        with self.Session() as db:
            limit = load_settings(db).max_concurrent_jobs
            while len(self.running) < limit:
                candidates = db.execute(
                    select(Job.id, Job.user_id)
                    .where(Job.status == "queued", Job.deleted_at.is_(None))
                    .order_by(Job.queued_at, Job.id)
                    .limit(200)
                ).all()
                if not candidates:
                    break
                busy = Counter(rj.user_id for rj in self.running.values())
                job_id, user_id = min(candidates, key=lambda row: busy[row.user_id])
                claimed = db.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == "queued")
                    .values(
                        status="running",
                        started_at=utcnow(),
                        finished_at=None,
                        progress=0,
                        stage="",
                        error=None,
                        error_kind=None,
                        warning=None,
                        attempts=Job.attempts + 1,
                    )
                ).rowcount
                db.commit()
                if not claimed:
                    continue
                rj = RunningJob(job_id=job_id, user_id=user_id)
                self.running[job_id] = rj
                rj.task = asyncio.create_task(self._run(rj), name=f"bdw-job-{job_id}")
                started = True
                self.publish_job(job_id)
        if started:
            self.broadcast_queue()

    async def _run(self, rj: RunningJob) -> None:
        outcome: Outcome | None = None
        try:
            outcome = await self._execute(rj)
        except asyncio.CancelledError:
            rj.interrupted = True
        except Exception as e:
            log.exception("job %s crashed", rj.job_id)
            outcome = Outcome("failed", error=f"内部错误：{e}", kind="internal")
        finally:
            if rj.process:
                rj.process.kill()
                rj.process.close()
            if rj.interrupted and not rj.cancel_requested:
                outcome = None
            elif rj.cancel_requested:
                outcome = Outcome("canceled")
            self._finalize(rj, outcome)
            self.running.pop(rj.job_id, None)
            self.wake()

    async def _execute(self, rj: RunningJob) -> Outcome | None:
        directory = job_dir(self.config.jobs_dir, rj.job_id)
        with self.Session() as db:
            job = db.get(Job, rj.job_id)
            if job is None or job.deleted_at is not None:
                return Outcome("canceled")
            profile = db.get(ModelProfile, job.model_id) if job.model_id else None
            if profile is None or not profile.enabled:
                return Outcome("failed", error="所选模型已被删除或停用，请换一个模型重试", kind="preflight")
            if not (directory / "input.pdf").is_file():
                return Outcome("failed", error="原文件已被清理，无法重新翻译", kind="input")
            settings = load_settings(db)
            term_profile, term_note = self.resolve_term_profile(db, job, profile)
            spec = build_spec(
                job=job,
                profile=profile,
                job_dir=directory,
                watermark_mode=settings.watermark_mode,
                term_profile=term_profile,
                mock=self._mock_spec(job) if self.config.engine == "mock" else None,
            )
            api_key = self.secrets.decrypt(profile.api_key_enc)
            term_key = self.secrets.decrypt(term_profile.api_key_enc) if term_profile else ""

        shutil.rmtree(directory / "out", ignore_errors=True)
        shutil.rmtree(directory / "work", ignore_errors=True)
        (directory / "out").mkdir(parents=True, exist_ok=True)
        (directory / "work").mkdir(parents=True, exist_ok=True)
        spec_path = directory / "spec.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        log_path = directory / "engine.log"
        with log_path.open("ab") as fh:
            fh.write(f"\n===== attempt at {utcnow().isoformat()} =====\n".encode())
            if term_note:
                fh.write(f"{term_note}\n".encode())

        env = child_env(api_key, term_key)
        rj.process = await asyncio.to_thread(spawn, self.config, spec_path, log_path, env)
        if rj.cancel_requested or rj.interrupted:
            rj.process.kill()

        finished: dict[str, Any] | None = None
        failed: dict[str, Any] | None = None
        async for event in read_events(rj.process):
            kind = event.get("event")
            if kind == "progress":
                self._on_progress(rj, event)
            elif kind == "started":
                self.engine_version = str(event.get("version") or self.engine_version)
            elif kind == "finished":
                finished = event
            elif kind == "failed":
                failed = event
        code = await asyncio.to_thread(rj.process.proc.wait)

        if rj.cancel_requested or rj.interrupted:
            return None
        if finished is not None:
            return self._success(directory, finished)
        if failed is not None:
            return Outcome(
                "failed", error=str(failed.get("message") or "翻译失败"), kind=failed.get("kind") or "translate"
            )
        hint = "，可能是内存不足被系统终止" if code in (-9, 137) else ""
        return Outcome("failed", error=f"翻译引擎异常退出（退出码 {code}）{hint}", kind="internal")

    def _success(self, directory: Path, event: dict[str, Any]) -> Outcome:
        base = directory.resolve()
        files: dict[str, str] = {}
        for key, raw in (event.get("files") or {}).items():
            if not raw:
                continue
            path = Path(raw).resolve()
            if base in path.parents and path.is_file():
                files[key] = path.relative_to(base).as_posix()
        if not files.get("mono") and not files.get("dual"):
            return Outcome("failed", error="引擎报告完成，但没有找到输出文件", kind="internal")
        stats = event.get("stats") or {}
        tokens = int(stats.get("total_tokens") or (stats.get("tokens") or {}).get("total") or 0)
        return Outcome("succeeded", files=files, stats=stats, warning=event.get("warning"), tokens=tokens)

    def resolve_term_profile(
        self, db: Session, job: Job, profile: ModelProfile
    ) -> tuple[ModelProfile | None, str | None]:
        """任务单独选的术语提取模型；重试时主模型可能已换成同一个，被删或停用就退回翻译模型。"""
        opts = job.options or {}
        term_id = opts.get("term_model_id")
        if not term_id or term_id == profile.id or not opts.get("auto_extract_glossary", True):
            return None, None
        term = db.get(ModelProfile, term_id)
        if term is None or not term.enabled:
            name = opts.get("term_model_name") or term_id
            return None, f"术语提取模型「{name}」已被删除或停用，改用翻译模型提取术语"
        return term, None

    def _mock_spec(self, job: Job) -> dict[str, Any]:
        name = job.filename.lower()
        return {
            "seconds": self.config.mock_seconds,
            "fail_at": "Translate Paragraphs" if "fail" in name else None,
            "warning": "（模拟）3 次翻译请求失败，部分段落可能保留了原文" if "warn" in name else None,
        }

    def _on_progress(self, rj: RunningJob, event: dict[str, Any]) -> None:
        try:
            progress = max(0.0, min(100.0, float(event.get("overall") or 0)))
        except (TypeError, ValueError):
            progress = rj.progress
        stage = str(event.get("stage") or "")[:128]
        stage_changed = stage != rj.stage
        rj.progress, rj.stage = progress, stage
        self.bus.publish(
            rj.user_id,
            {
                "type": "progress",
                "id": rj.job_id,
                "progress": round(progress, 2),
                "stage": stage,
                "current": event.get("current"),
                "total": event.get("total"),
                "part": event.get("part"),
                "parts": event.get("parts"),
            },
        )
        now = time.monotonic()
        if stage_changed or now - rj.last_persist >= PERSIST_INTERVAL:
            rj.last_persist = now
            with self.Session() as db:
                db.execute(update(Job).where(Job.id == rj.job_id).values(progress=progress, stage=stage))
                db.commit()

    def _finalize(self, rj: RunningJob, outcome: Outcome | None) -> None:
        directory = job_dir(self.config.jobs_dir, rj.job_id)
        shutil.rmtree(directory / "work", ignore_errors=True)
        with self.Session() as db:
            job = db.get(Job, rj.job_id)
            if job is None:
                return
            if job.deleted_at is not None:
                job.status = "canceled" if job.status == "running" else job.status
                db.commit()
                remove_job_files(self.config.jobs_dir, job.id)
                return
            if outcome is None:
                job.status = "queued"
                job.progress = 0
                job.stage = ""
                job.started_at = None
            else:
                job.status = outcome.status
                job.finished_at = utcnow()
                job.stage = ""
                if outcome.status == "succeeded":
                    job.progress = 100
                    job.result = {"files": outcome.files, "stats": outcome.stats}
                    job.tokens = outcome.tokens
                    job.warning = outcome.warning
                elif outcome.status == "failed":
                    job.error = outcome.error
                    job.error_kind = outcome.kind
            db.commit()
        self.publish_job(rj.job_id)
        self.broadcast_queue()

    def publish_job(self, job_id: str) -> None:
        with self.Session() as db:
            job = db.get(Job, job_id)
            if job is None or job.deleted_at is not None:
                return
            position = queue_positions(db).get(job_id) if job.status == "queued" else None
            payload = JobOut.of(job, position=position).model_dump(mode="json")
            user_id = job.user_id
        self.bus.publish(user_id, {"type": "job", "job": payload})

    def broadcast_queue(self) -> None:
        with self.Session() as db:
            rows = db.execute(
                select(Job.id, Job.user_id)
                .where(Job.status == "queued", Job.deleted_at.is_(None))
                .order_by(Job.queued_at, Job.id)
            ).all()
        per_user: dict[int, dict[str, int]] = defaultdict(dict)
        for index, row in enumerate(rows):
            per_user[row.user_id][row.id] = index
        for user_id, positions in per_user.items():
            self.bus.publish(user_id, {"type": "queue", "positions": positions})

    async def _housekeeping(self) -> None:
        await asyncio.sleep(60)
        while True:
            try:
                await asyncio.to_thread(self.purge_expired)
            except Exception:
                log.exception("housekeeping failed")
            await asyncio.sleep(HOUSEKEEPING_INTERVAL)

    def purge_expired(self) -> int:
        with self.Session() as db:
            settings = load_settings(db)
            cutoff = utcnow() - timedelta(days=settings.file_retention_days)
            jobs = db.scalars(
                select(Job).where(
                    Job.files_purged.is_(False),
                    Job.finished_at.is_not(None),
                    Job.finished_at < cutoff,
                    Job.status.in_(JOB_FINISHED),
                )
            ).all()
            for job in jobs:
                remove_job_files(self.config.jobs_dir, job.id)
                job.files_purged = True
            db.execute(delete(AuthSession).where(AuthSession.expires_at < utcnow()))
            db.commit()
        if jobs:
            log.info("purged files of %d expired jobs", len(jobs))
        return len(jobs)
