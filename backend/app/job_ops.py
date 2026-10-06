from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.orm import Session

from .db import utcnow
from .defaults import default_or_first
from .deps import AppContext
from .models import JOB_ACTIVE, Job, ModelProfile, User
from .schemas import JobOut
from .services import effective_quota, job_dir, month_pages, remove_job_files
from .settings_store import load_settings

# 任务列表的状态筛选；“失败”一栏也包括已取消的
JobFilter = Literal["all", "active", "succeeded", "failed"]


def default_model(db: Session) -> ModelProfile | None:
    return default_or_first(db, select(ModelProfile).where(ModelProfile.enabled.is_(True)), ModelProfile)


def status_conditions(status: JobFilter) -> list[ColumnElement[bool]]:
    if status == "active":
        return [Job.status.in_(JOB_ACTIVE)]
    if status == "succeeded":
        return [Job.status == "succeeded"]
    if status == "failed":
        return [Job.status.in_(("failed", "canceled"))]
    return []


def job_out(ctx: AppContext, job: Job, position: int | None = None, username: str | None = None) -> JobOut:
    """接口输出；进行中的任务用内存里的实时进度覆盖库里（几秒才写一次）的值。"""
    out = JobOut.of(job, position=position, username=username)
    live = ctx.manager.live_progress(job.id) if job.status == "running" else None
    if live:
        out.progress, out.stage = round(live[0], 2), live[1]
    return out


def check_capacity(db: Session, owner: User, new_pages: int, new_jobs: int) -> None:
    settings = load_settings(db)
    active = db.scalar(
        select(func.count(Job.id)).where(Job.user_id == owner.id, Job.status.in_(JOB_ACTIVE), Job.deleted_at.is_(None))
    )
    if (active or 0) + new_jobs > settings.max_active_jobs_per_user and not owner.is_admin:
        raise HTTPException(
            400, f"同时排队或进行中的任务最多 {settings.max_active_jobs_per_user} 个，请等前面的任务完成"
        )
    quota = effective_quota(owner, settings)
    if quota:
        used = month_pages(db, owner.id)
        if used + new_pages > quota:
            raise HTTPException(400, f"本月页数额度不足：已用 {used} / {quota} 页，本次需要 {new_pages} 页")


def cancel_job(db: Session, ctx: AppContext, job: Job) -> None:
    if job.status == "queued":
        changed = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == "queued")
            .values(status="canceled", finished_at=utcnow(), stage="", progress=0)
        ).rowcount
        db.commit()
        if changed:
            ctx.manager.publish_job(job.id)
            ctx.manager.broadcast_queue()
            return
        db.refresh(job)
    if job.status == "running" and ctx.manager.cancel(job.id):
        return
    raise HTTPException(409, "任务已经结束，无法取消")


def retry_job(db: Session, ctx: AppContext, job: Job) -> None:
    if job.status not in ("failed", "canceled"):
        raise HTTPException(409, "只有失败或已取消的任务可以重试")
    if job.files_purged or not (job_dir(ctx.config.jobs_dir, job.id) / "input.pdf").is_file():
        raise HTTPException(409, "原文件已过期清理，请重新上传")
    profile = db.get(ModelProfile, job.model_id) if job.model_id else None
    if profile is None or not profile.enabled:
        profile = default_model(db)
        if profile is None:
            raise HTTPException(400, "当前没有可用的翻译模型，请联系管理员")
    owner = db.get(User, job.user_id)
    if owner is None:
        raise HTTPException(404, "任务所属用户不存在")
    check_capacity(db, owner, job.billed_pages, 1)
    now = utcnow()
    job.status = "queued"
    job.queued_at = now
    job.model_id = profile.id
    job.model_name = profile.name
    job.progress = 0
    job.stage = ""
    job.error = None
    job.error_kind = None
    job.warning = None
    job.result = None
    job.tokens = 0
    job.started_at = None
    job.finished_at = None
    db.commit()
    ctx.manager.wake()
    ctx.manager.publish_job(job.id)
    ctx.manager.broadcast_queue()


def delete_job(db: Session, ctx: AppContext, job: Job) -> None:
    was_running = job.status == "running"
    now = utcnow()
    job.deleted_at = now
    if job.status == "queued":
        job.status = "canceled"
        job.finished_at = now
    db.commit()
    if not (was_running and ctx.manager.cancel(job.id)):
        remove_job_files(ctx.config.jobs_dir, job.id)
    ctx.manager.broadcast_queue()
