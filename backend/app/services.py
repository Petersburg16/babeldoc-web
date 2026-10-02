from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import utcnow
from .models import JOB_BILLABLE, Job, User
from .schemas import MeOut, UsageOut, UserOut
from .settings_store import SystemSettings, load_settings

TZ_OFFSET_HOURS = int(os.environ.get("BDW_TZ_OFFSET_HOURS", "8"))
LOCAL_TZ = timezone(timedelta(hours=TZ_OFFSET_HOURS))


def month_start(now: datetime | None = None) -> datetime:
    """按本地时区（默认东八区）算本月起点，返回 UTC。"""
    local = (now or utcnow()).astimezone(LOCAL_TZ)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def day_start(now: datetime | None = None) -> datetime:
    local = (now or utcnow()).astimezone(LOCAL_TZ)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def month_pages(db: Session, user_id: int) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(Job.billed_pages), 0)).where(
            Job.user_id == user_id,
            Job.created_at >= month_start(),
            Job.status.in_(JOB_BILLABLE),
        )
    )
    return int(total or 0)


def effective_quota(user: User, settings: SystemSettings) -> int:
    if user.is_admin:
        return 0
    return user.page_quota if user.page_quota is not None else settings.default_page_quota


def me_out(db: Session, user: User) -> MeOut:
    settings = load_settings(db)
    month_tokens = db.scalar(
        select(func.coalesce(func.sum(Job.tokens), 0)).where(Job.user_id == user.id, Job.created_at >= month_start())
    )
    total_jobs = db.scalar(select(func.count(Job.id)).where(Job.user_id == user.id, Job.deleted_at.is_(None)))
    return MeOut(
        user=UserOut.of(user),
        usage=UsageOut(
            month_pages=month_pages(db, user.id),
            quota=effective_quota(user, settings),
            month_tokens=int(month_tokens or 0),
            total_jobs=int(total_jobs or 0),
        ),
    )


def queue_positions(db: Session) -> dict[str, int]:
    ids = db.scalars(
        select(Job.id).where(Job.status == "queued", Job.deleted_at.is_(None)).order_by(Job.queued_at, Job.id)
    ).all()
    return {job_id: index for index, job_id in enumerate(ids)}


def job_dir(jobs_root: Path, job_id: str) -> Path:
    return jobs_root / job_id


def remove_job_files(jobs_root: Path, job_id: str) -> None:
    shutil.rmtree(job_dir(jobs_root, job_id), ignore_errors=True)


def result_file(jobs_root: Path, job: Job, kind: str) -> Path | None:
    if job.files_purged:
        return None
    base = job_dir(jobs_root, job.id).resolve()
    if kind == "original":
        path = base / "input.pdf"
    else:
        rel = ((job.result or {}).get("files") or {}).get(kind)
        if not rel:
            return None
        path = (base / rel).resolve()
        if base not in path.parents:
            return None
    return path if path.is_file() else None


def download_name(job: Job, kind: str) -> str:
    stem = Path(job.filename).stem or "document"
    return {
        "dual": f"{stem}.{job.lang_out}.dual.pdf",
        "mono": f"{stem}.{job.lang_out}.pdf",
        "glossary": f"{stem}.glossary.csv",
        "original": job.filename if job.filename.lower().endswith(".pdf") else f"{stem}.pdf",
    }[kind]
