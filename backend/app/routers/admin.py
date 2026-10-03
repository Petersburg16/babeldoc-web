from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

import psutil
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.orm import Session

from ..db import utcnow
from ..deps import AdminDep, AppContext, CtxDep, DbDep, require_admin
from ..engine import detect_engine_version
from ..job_ops import cancel_job, delete_job, retry_job
from ..llm_check import check_chat, list_remote_models
from ..models import JOB_ACTIVE, JOB_BILLABLE, AuthSession, Invite, Job, ModelProfile, User
from ..schemas import (
    AdminUserIn,
    AdminUserOut,
    AdminUserPatch,
    InviteIn,
    InviteOut,
    JobOut,
    JobPage,
    ModelAdminOut,
    ModelIn,
    ModelPatch,
    ModelProbeIn,
    ModelTestOut,
)
from ..security import hash_password, new_invite_code, new_password
from ..services import (
    TZ_OFFSET_HOURS,
    day_start,
    effective_quota,
    job_dir,
    month_start,
    queue_positions,
    remove_job_files,
)
from ..settings_store import SystemSettings, load_settings, save_settings

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

LOG_TAIL_BYTES = 200_000
DAILY_SPAN = 14


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


async def engine_version(ctx: AppContext) -> str:
    if ctx.manager.engine_version:
        return ctx.manager.engine_version
    if ctx.engine_version is None:
        ctx.engine_version = await asyncio.to_thread(detect_engine_version, ctx.config)
    return ctx.engine_version


@router.get("/stats")
async def stats(ctx: CtxDep) -> dict[str, Any]:
    version = await engine_version(ctx)
    return await asyncio.to_thread(_stats, ctx, version)


def _stats(ctx: AppContext, version: str) -> dict[str, Any]:
    now = utcnow()
    month0, today0 = month_start(now), day_start(now)
    with ctx.Session() as db:
        by_status = dict(
            db.execute(
                select(Job.status, func.count(Job.id)).where(Job.deleted_at.is_(None)).group_by(Job.status)
            ).all()
        )
        users_total = db.scalar(select(func.count(User.id))) or 0
        users_active = db.scalar(select(func.count(User.id)).where(User.is_active.is_(True))) or 0
        billable_pages = func.coalesce(func.sum(case((Job.status.in_(JOB_BILLABLE), Job.billed_pages), else_=0)), 0)
        month_jobs, month_pages, month_tokens = db.execute(
            select(func.count(Job.id), billable_pages, func.coalesce(func.sum(Job.tokens), 0)).where(
                Job.created_at >= month0
            )
        ).one()
        today_jobs = db.scalar(select(func.count(Job.id)).where(Job.created_at >= today0)) or 0

        since = today0 - timedelta(days=DAILY_SPAN - 1)
        day = func.date(Job.created_at, f"{TZ_OFFSET_HOURS:+d} hours").label("day")
        rows = db.execute(
            select(
                day,
                func.count(Job.id),
                func.sum(case((Job.status == "succeeded", 1), else_=0)),
                func.sum(case((Job.status == "failed", 1), else_=0)),
                billable_pages,
                func.coalesce(func.sum(Job.tokens), 0),
            )
            .where(Job.created_at >= since)
            .group_by(day)
        ).all()
        by_day = {r[0]: r for r in rows}
        daily = []
        for offset in range(DAILY_SPAN):
            key = (since + timedelta(days=offset, hours=TZ_OFFSET_HOURS)).date().isoformat()
            r = by_day.get(key)
            daily.append(
                {
                    "day": key,
                    "jobs": int(r[1]) if r else 0,
                    "succeeded": int(r[2] or 0) if r else 0,
                    "failed": int(r[3] or 0) if r else 0,
                    "pages": int(r[4] or 0) if r else 0,
                    "tokens": int(r[5] or 0) if r else 0,
                }
            )

        top_users = [
            {"username": name, "display_name": display or name, "pages": int(pages or 0), "jobs": int(count)}
            for name, display, pages, count in db.execute(
                select(User.username, User.display_name, billable_pages, func.count(Job.id))
                .join(Job, Job.user_id == User.id)
                .where(Job.created_at >= month0)
                .group_by(User.id)
                .order_by(billable_pages.desc())
                .limit(5)
            ).all()
        ]

        running = []
        for job_id, rj in list(ctx.manager.running.items()):
            job = db.get(Job, job_id)
            owner = db.get(User, rj.user_id)
            if job is None:
                continue
            running.append(
                {
                    "id": job.id,
                    "filename": job.filename,
                    "username": owner.username if owner else "?",
                    "progress": round(rj.progress, 1),
                    "stage": rj.stage,
                    "billed_pages": job.billed_pages,
                    "started_at": job.started_at,
                }
            )
        queued = [
            {
                "id": j.id,
                "filename": j.filename,
                "username": u,
                "billed_pages": j.billed_pages,
                "queued_at": j.queued_at,
            }
            for j, u in db.execute(
                select(Job, User.username)
                .join(User, User.id == Job.user_id)
                .where(Job.status == "queued", Job.deleted_at.is_(None))
                .order_by(Job.queued_at)
                .limit(20)
            ).all()
        ]
        failures = [
            {"id": j.id, "filename": j.filename, "username": u, "error": j.error, "finished_at": j.finished_at}
            for j, u in db.execute(
                select(Job, User.username)
                .join(User, User.id == Job.user_id)
                .where(Job.status == "failed")
                .order_by(Job.finished_at.desc())
                .limit(6)
            ).all()
        ]
        max_concurrent = load_settings(db).max_concurrent_jobs

    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(str(ctx.config.data_dir))
    load = os.getloadavg() if hasattr(os, "getloadavg") else None
    return {
        "jobs": {
            "by_status": by_status,
            "today": today_jobs,
            "month": int(month_jobs or 0),
            "month_pages": int(month_pages or 0),
            "month_tokens": int(month_tokens or 0),
        },
        "users": {"total": users_total, "active": users_active},
        "daily": daily,
        "top_users": top_users,
        "running": running,
        "queued": queued,
        "failures": failures,
        "engine": {
            "mode": ctx.config.engine,
            "version": version,
            "max_concurrent": max_concurrent,
            "subscribers": ctx.bus.subscriber_count(),
        },
        "system": {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "cpu_count": psutil.cpu_count() or 1,
            "load": list(load) if load else None,
            "memory_total": vm.total,
            "memory_used": vm.total - vm.available,
            "disk_total": disk.total,
            "disk_free": disk.free,
            "data_size": _dir_size(ctx.config.jobs_dir) if ctx.config.jobs_dir.exists() else 0,
        },
    }


def _admin_user_out(db: Session, user: User, month_pages: dict[int, int], totals: dict[int, int]) -> AdminUserOut:
    settings = load_settings(db)
    return AdminUserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        role=user.role,
        is_active=user.is_active,
        page_quota=user.page_quota,
        effective_quota=effective_quota(user, settings),
        note=user.note,
        month_pages=int(month_pages.get(user.id, 0) or 0),
        total_jobs=int(totals.get(user.id, 0) or 0),
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _usage_maps(db: Session) -> tuple[dict[int, int], dict[int, int]]:
    pages = dict(
        db.execute(
            select(Job.user_id, func.sum(Job.billed_pages))
            .where(Job.created_at >= month_start(), Job.status.in_(JOB_BILLABLE))
            .group_by(Job.user_id)
        ).all()
    )
    totals = dict(
        db.execute(select(Job.user_id, func.count(Job.id)).where(Job.deleted_at.is_(None)).group_by(Job.user_id)).all()
    )
    return pages, totals


def _get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")
    return user


def _active_admins(db: Session) -> int:
    return db.scalar(select(func.count(User.id)).where(User.role == "admin", User.is_active.is_(True))) or 0


@router.get("/users")
def list_users(db: DbDep) -> list[AdminUserOut]:
    pages, totals = _usage_maps(db)
    users = db.scalars(select(User).order_by(User.created_at)).all()
    return [_admin_user_out(db, u, pages, totals) for u in users]


@router.post("/users", status_code=201)
def create_user(body: AdminUserIn, db: DbDep) -> AdminUserOut:
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(409, "用户名已被占用")
    user = User(
        username=body.username,
        display_name=body.display_name.strip() or body.username,
        password_hash=hash_password(body.password),
        role=body.role,
        page_quota=body.page_quota,
        note=body.note,
    )
    db.add(user)
    db.commit()
    return _admin_user_out(db, user, {}, {})


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: AdminUserPatch, admin: AdminDep, db: DbDep) -> AdminUserOut:
    user = _get_user(db, user_id)
    fields = body.model_fields_set
    if user.id == admin.id and (
        ("role" in fields and body.role != "admin") or ("is_active" in fields and body.is_active is False)
    ):
        raise HTTPException(400, "不能停用自己或取消自己的管理员身份")
    demoting = user.is_admin and (
        ("role" in fields and body.role == "user") or ("is_active" in fields and body.is_active is False)
    )
    if demoting and _active_admins(db) <= 1:
        raise HTTPException(400, "至少要保留一个可用的管理员")
    if "display_name" in fields and body.display_name is not None:
        user.display_name = body.display_name.strip() or user.username
    if "role" in fields and body.role:
        user.role = body.role
    if "is_active" in fields and body.is_active is not None:
        user.is_active = body.is_active
        if not body.is_active:
            db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    if "page_quota" in fields:
        user.page_quota = body.page_quota
    if "note" in fields and body.note is not None:
        user.note = body.note
    db.commit()
    pages, totals = _usage_maps(db)
    return _admin_user_out(db, user, pages, totals)


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, db: DbDep) -> dict[str, str]:
    user = _get_user(db, user_id)
    password = new_password()
    user.password_hash = hash_password(password)
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.commit()
    return {"password": password}


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, admin: AdminDep, db: DbDep, ctx: CtxDep) -> None:
    user = _get_user(db, user_id)
    if user.id == admin.id:
        raise HTTPException(400, "不能删除自己")
    if user.is_admin and _active_admins(db) <= 1:
        raise HTTPException(400, "至少要保留一个可用的管理员")
    job_ids = db.scalars(select(Job.id).where(Job.user_id == user.id)).all()
    for job_id in job_ids:
        ctx.manager.cancel(job_id)
    db.delete(user)
    db.commit()
    for job_id in job_ids:
        remove_job_files(ctx.config.jobs_dir, job_id)
    ctx.manager.broadcast_queue()


@router.get("/invites")
def list_invites(db: DbDep) -> list[InviteOut]:
    now = utcnow()
    return [InviteOut.of(i, now) for i in db.scalars(select(Invite).order_by(Invite.created_at.desc())).all()]


@router.post("/invites", status_code=201)
def create_invite(body: InviteIn, admin: AdminDep, db: DbDep) -> InviteOut:
    code = new_invite_code()
    while db.scalar(select(Invite.id).where(Invite.code == code)):
        code = new_invite_code()
    now = utcnow()
    invite = Invite(
        code=code,
        note=body.note.strip(),
        max_uses=body.max_uses,
        expires_at=now + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
        created_by=admin.id,
    )
    db.add(invite)
    db.commit()
    return InviteOut.of(invite, now)


@router.post("/invites/{invite_id}/revoke")
def revoke_invite(invite_id: int, db: DbDep) -> InviteOut:
    invite = db.get(Invite, invite_id)
    if invite is None:
        raise HTTPException(404, "邀请码不存在")
    invite.revoked = True
    db.commit()
    return InviteOut.of(invite, utcnow())


@router.delete("/invites/{invite_id}", status_code=204)
def delete_invite(invite_id: int, db: DbDep) -> None:
    invite = db.get(Invite, invite_id)
    if invite is None:
        raise HTTPException(404, "邀请码不存在")
    db.delete(invite)
    db.commit()


def _model_out(ctx: AppContext, m: ModelProfile) -> ModelAdminOut:
    return ModelAdminOut.of(m, ctx.secrets.decrypt(m.api_key_enc))


def _get_model(db: Session, model_id: int) -> ModelProfile:
    m = db.get(ModelProfile, model_id)
    if m is None:
        raise HTTPException(404, "模型不存在")
    return m


def _fix_default(db: Session, prefer: ModelProfile | None = None) -> None:
    if prefer is not None and prefer.is_default:
        db.execute(update(ModelProfile).where(ModelProfile.id != prefer.id).values(is_default=False))
    has_default = db.scalar(
        select(ModelProfile.id).where(ModelProfile.is_default.is_(True), ModelProfile.enabled.is_(True))
    )
    if not has_default:
        first = db.scalar(
            select(ModelProfile)
            .where(ModelProfile.enabled.is_(True))
            .order_by(ModelProfile.sort_order, ModelProfile.id)
        )
        db.execute(update(ModelProfile).values(is_default=False))
        if first is not None:
            first.is_default = True


@router.get("/models")
def list_models(db: DbDep, ctx: CtxDep) -> list[ModelAdminOut]:
    rows = db.scalars(select(ModelProfile).order_by(ModelProfile.sort_order, ModelProfile.id)).all()
    return [_model_out(ctx, m) for m in rows]


@router.post("/models", status_code=201)
def create_model(body: ModelIn, db: DbDep, ctx: CtxDep) -> ModelAdminOut:
    data = body.model_dump(exclude={"api_key"})
    m = ModelProfile(**data, api_key_enc=ctx.secrets.encrypt(body.api_key.strip()))
    db.add(m)
    db.flush()
    _fix_default(db, m)
    db.commit()
    db.refresh(m)
    return _model_out(ctx, m)


@router.patch("/models/{model_id}")
def patch_model(model_id: int, body: ModelPatch, db: DbDep, ctx: CtxDep) -> ModelAdminOut:
    m = _get_model(db, model_id)
    for name in body.model_fields_set - {"api_key", "clear_api_key"}:
        value = getattr(body, name)
        if value is None and name in {"name", "model", "base_url", "description", "qps", "enabled", "is_default"}:
            continue
        if name in {"send_temperature", "json_mode", "sort_order"} and value is None:
            continue
        setattr(m, name, value)
    if body.clear_api_key:
        m.api_key_enc = ""
    elif body.api_key:
        m.api_key_enc = ctx.secrets.encrypt(body.api_key.strip())
    _fix_default(db, m)
    db.commit()
    db.refresh(m)
    return _model_out(ctx, m)


@router.delete("/models/{model_id}", status_code=204)
def delete_model(model_id: int, db: DbDep) -> None:
    m = _get_model(db, model_id)
    db.delete(m)
    db.flush()
    _fix_default(db)
    db.commit()


@router.post("/models/{model_id}/test")
def test_model(model_id: int, db: DbDep, ctx: CtxDep) -> ModelTestOut:
    m = _get_model(db, model_id)
    return check_chat(
        base_url=m.base_url,
        api_key=ctx.secrets.decrypt(m.api_key_enc),
        model=m.model,
        send_temperature=m.send_temperature,
    )


@router.post("/models/probe")
def probe_models(body: ModelProbeIn, db: DbDep, ctx: CtxDep) -> dict[str, list[str]]:
    api_key = body.api_key.strip()
    if not api_key and body.model_id:
        api_key = ctx.secrets.decrypt(_get_model(db, body.model_id).api_key_enc)
    try:
        return {"models": list_remote_models(body.base_url, api_key)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/jobs")
def list_all_jobs(
    db: DbDep,
    ctx: CtxDep,
    status: Annotated[Literal["all", "active", "succeeded", "failed"], Query()] = "all",
    user_id: Annotated[int | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobPage:
    conditions = [Job.deleted_at.is_(None)]
    if status == "active":
        conditions.append(Job.status.in_(JOB_ACTIVE))
    elif status == "succeeded":
        conditions.append(Job.status == "succeeded")
    elif status == "failed":
        conditions.append(Job.status.in_(("failed", "canceled")))
    if user_id:
        conditions.append(Job.user_id == user_id)
    if q:
        like = f"%{q.strip()}%"
        conditions.append(or_(Job.filename.ilike(like), Job.id == q.strip(), User.username.ilike(like)))
    base = select(Job, User.username).join(User, User.id == Job.user_id).where(*conditions)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(base.order_by(Job.created_at.desc()).limit(limit).offset(offset)).all()
    positions = queue_positions(db)
    items = []
    for job, username in rows:
        out = JobOut.of(job, position=positions.get(job.id), username=username)
        live = ctx.manager.live_progress(job.id) if job.status == "running" else None
        if live:
            out.progress, out.stage = round(live[0], 2), live[1]
        items.append(out)
    return JobPage(items=items, total=total)


def _get_job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.deleted_at is not None:
        raise HTTPException(404, "任务不存在")
    return job


@router.post("/jobs/{job_id}/cancel")
def admin_cancel(job_id: str, db: DbDep, ctx: CtxDep) -> dict[str, bool]:
    cancel_job(db, ctx, _get_job(db, job_id))
    return {"ok": True}


@router.post("/jobs/{job_id}/retry")
def admin_retry(job_id: str, db: DbDep, ctx: CtxDep) -> dict[str, bool]:
    retry_job(db, ctx, _get_job(db, job_id))
    return {"ok": True}


@router.delete("/jobs/{job_id}", status_code=204)
def admin_delete(job_id: str, db: DbDep, ctx: CtxDep) -> None:
    delete_job(db, ctx, _get_job(db, job_id))


@router.get("/jobs/{job_id}/log")
def job_log(job_id: str, db: DbDep, ctx: CtxDep) -> dict[str, str]:
    job = _get_job(db, job_id)
    path = job_dir(ctx.config.jobs_dir, job.id) / "engine.log"
    if not path.is_file():
        return {"log": ""}
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > LOG_TAIL_BYTES:
            fh.seek(size - LOG_TAIL_BYTES)
        data = fh.read()
    return {"log": data.decode("utf-8", errors="replace")}


@router.get("/settings")
def get_settings(db: DbDep) -> SystemSettings:
    return load_settings(db)


@router.put("/settings")
def put_settings(body: SystemSettings, db: DbDep, ctx: CtxDep) -> SystemSettings:
    save_settings(db, body)
    ctx.manager.wake()
    return load_settings(db)
