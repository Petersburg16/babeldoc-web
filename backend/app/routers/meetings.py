"""会议记录：分片上传、列表、详情、逐字稿、回听、取消、重试、删除。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..deps import AppContext, CtxDep, DbDep, UserDep
from ..meeting import split
from ..meeting.asr import available_kinds
from ..meeting.manager import source_name
from ..meeting.media import AUDIO_NAME
from ..meeting.schemas import (
    MeetingCreate,
    MeetingDetailOut,
    MeetingOptionsOut,
    MeetingOut,
    MeetingPage,
    MeetingPatch,
    MeetingUploadOut,
    ProviderPublicOut,
    SegmentOut,
    TemplateOut,
)
from ..meeting.templates import TEMPLATES
from ..models import AsrProvider, Meeting, MeetingMessage, MeetingSegment, ModelProfile, User
from ..security import new_job_id
from ..settings_store import load_settings
from .jobs import clean_filename

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

PART_SIZE = 8 * 1024 * 1024  # 分片上传每片 8 MB：远低于 Cloudflare 单请求 100 MB 的上限，断了重传也便宜
AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wma", ".amr", ".aiff", ".aif", ".caf",
    ".webm", ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".3gp", ".mpeg", ".mpg", ".ts", ".flv", ".wmv",
}  # fmt: skip


def dev_mode(ctx: AppContext) -> bool:
    return ctx.config.engine == "mock"


def own_meeting(db: Session, user: User, meeting_id: str) -> Meeting:
    m = db.get(Meeting, meeting_id)
    if m is None or m.deleted_at is not None or m.user_id != user.id:
        raise HTTPException(404, "会议不存在")
    return m


def detail_out(ctx: AppContext, db: Session, m: Meeting) -> MeetingDetailOut:
    base = ctx.meetings.meeting_out(m, load_settings(db).file_retention_days)
    return MeetingDetailOut(**base.model_dump(), minutes_md=m.minutes_md)


def usable_provider(db: Session, ctx: AppContext, provider_id: int | None) -> AsrProvider:
    kinds = {cls.kind for cls in available_kinds(dev_mode(ctx))}
    query = select(AsrProvider).where(AsrProvider.enabled.is_(True), AsrProvider.kind.in_(kinds))
    if provider_id is not None:
        provider = db.scalar(query.where(AsrProvider.id == provider_id))
        if provider is None:
            raise HTTPException(400, "所选的识别服务不可用")
        return provider
    provider = db.scalar(query.where(AsrProvider.is_default.is_(True))) or db.scalar(
        query.order_by(AsrProvider.sort_order, AsrProvider.id)
    )
    if provider is None:
        raise HTTPException(400, "管理员还没有配置语音识别服务")
    return provider


def usable_model(db: Session, model_id: int | None) -> ModelProfile | None:
    enabled = select(ModelProfile).where(ModelProfile.enabled.is_(True))
    if model_id is not None:
        model = db.scalar(enabled.where(ModelProfile.id == model_id))
        if model is None:
            raise HTTPException(400, "所选的大模型不可用")
        return model
    return db.scalar(enabled.where(ModelProfile.is_default.is_(True))) or db.scalar(
        enabled.order_by(ModelProfile.sort_order, ModelProfile.id)
    )


@router.get("/options")
def options(user: UserDep, db: DbDep, ctx: CtxDep) -> MeetingOptionsOut:
    settings = load_settings(db)
    kinds = {cls.kind: cls for cls in available_kinds(dev_mode(ctx))}
    rows = db.scalars(
        select(AsrProvider)
        .where(AsrProvider.enabled.is_(True), AsrProvider.kind.in_(kinds))
        .order_by(AsrProvider.sort_order, AsrProvider.id)
    ).all()
    providers = []
    for p in rows:
        cls = kinds[p.kind]
        providers.append(
            ProviderPublicOut(
                id=p.id,
                name=p.name,
                kind=p.kind,
                label=cls.label,
                description=p.description or cls.description,
                is_default=p.is_default,
                max_part_seconds=cls.capability.max_part_seconds,
                hotwords=cls.capability.hotwords,
                speaker_count=cls.capability.speaker_count,
            )
        )
    return MeetingOptionsOut(
        providers=providers,
        templates=[TemplateOut(id=t.id, name=t.name, description=t.description) for t in TEMPLATES],
        default_template=settings.default_meeting_template,
        max_audio_upload_mb=settings.max_audio_upload_mb,
        max_audio_hours=settings.max_audio_hours,
        retention_days=settings.file_retention_days,
        split_supported=split.SUPPORTED,
    )


@router.get("")
def list_meetings(
    user: UserDep,
    db: DbDep,
    ctx: CtxDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MeetingPage:
    base = select(Meeting).where(Meeting.user_id == user.id, Meeting.deleted_at.is_(None))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by(Meeting.created_at.desc()).limit(limit).offset(offset)).all()
    retention = load_settings(db).file_retention_days
    return MeetingPage(items=[ctx.meetings.meeting_out(m, retention) for m in rows], total=total)


@router.post("", status_code=201)
def create_meeting(body: MeetingCreate, user: UserDep, db: DbDep, ctx: CtxDep) -> MeetingUploadOut:
    settings = load_settings(db)
    filename = clean_filename(body.filename)
    if Path(filename).suffix.lower() not in AUDIO_EXTENSIONS:
        raise HTTPException(400, f"{filename} 不是支持的音视频格式")
    limit = settings.max_audio_upload_mb * 1024 * 1024
    if body.size > limit:
        raise HTTPException(413, f"文件超过上限 {settings.max_audio_upload_mb} MB")
    provider = usable_provider(db, ctx, body.provider_id)
    model = usable_model(db, body.model_id)
    meeting = Meeting(
        id=new_job_id(),
        user_id=user.id,
        title=(body.title.strip() or Path(filename).stem)[:200],
        filename=filename,
        file_size=body.size,
        language=body.language,
        provider_id=provider.id,
        provider_kind=provider.kind,
        provider_name=provider.name,
        model_id=model.id if model else None,
        model_name=model.name if model else "",
        template=body.template or settings.default_meeting_template,
        extra_instructions=body.extra_instructions.strip(),
        expected_speakers=body.expected_speakers,
    )
    directory = ctx.meetings.meeting_dir(meeting.id)
    (directory / "upload").mkdir(parents=True, exist_ok=True)
    db.add(meeting)
    db.commit()
    parts = max(1, -(-body.size // PART_SIZE))
    ctx.meetings.publish(meeting.id)
    return MeetingUploadOut(
        meeting=ctx.meetings.meeting_out(meeting, settings.file_retention_days), part_size=PART_SIZE, parts=parts
    )


def _expected_part_size(total: int, index: int) -> int:
    parts = max(1, -(-total // PART_SIZE))
    if index < 0 or index >= parts:
        return -1
    return PART_SIZE if index < parts - 1 else total - PART_SIZE * (parts - 1)


@router.put("/{meeting_id}/upload/{index}")
async def upload_part(meeting_id: str, index: int, request: Request, user: UserDep, ctx: CtxDep) -> dict[str, int]:
    with ctx.Session() as db:
        m = own_meeting(db, user, meeting_id)
        if m.status != "uploading":
            raise HTTPException(409, "这场会议已经上传完成")
        expected = _expected_part_size(m.file_size, index)
    if expected < 0:
        raise HTTPException(400, "分片序号不对")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > expected:
            raise HTTPException(400, "分片大小不对")
    if len(data) != expected:
        raise HTTPException(400, f"分片大小不对：应为 {expected} 字节，收到 {len(data)} 字节")
    directory = ctx.meetings.meeting_dir(meeting_id) / "upload"
    if not directory.is_dir():
        raise HTTPException(409, "上传已失效，请重新上传")
    final = directory / f"{index:05d}.part"
    tmp = directory / f"{index:05d}.tmp"

    def write() -> None:
        tmp.write_bytes(data)
        os.replace(tmp, final)

    await asyncio.to_thread(write)
    return {"index": index, "size": len(data)}


@router.post("/{meeting_id}/upload/complete")
def complete_upload(meeting_id: str, user: UserDep, db: DbDep, ctx: CtxDep) -> MeetingOut:
    m = own_meeting(db, user, meeting_id)
    if m.status != "uploading":
        raise HTTPException(409, "这场会议已经上传完成")
    directory = ctx.meetings.meeting_dir(meeting_id)
    upload = directory / "upload"
    parts = max(1, -(-m.file_size // PART_SIZE))
    missing = [
        i
        for i in range(parts)
        if not (upload / f"{i:05d}.part").is_file()
        or (upload / f"{i:05d}.part").stat().st_size != _expected_part_size(m.file_size, i)
    ]
    if missing:
        raise HTTPException(400, f"还有 {len(missing)} 个分片没有传完")
    target = directory / source_name(m.filename)
    with target.open("wb") as out:
        for i in range(parts):
            with (upload / f"{i:05d}.part").open("rb") as src:
                shutil.copyfileobj(src, out, 1024 * 1024)
    shutil.rmtree(upload, ignore_errors=True)
    m.status = "queued"
    m.stage = ""
    db.commit()
    ctx.meetings.request_launch(meeting_id)
    return ctx.meetings.meeting_out(m, load_settings(db).file_retention_days)


@router.get("/{meeting_id}")
def get_meeting(meeting_id: str, user: UserDep, db: DbDep, ctx: CtxDep) -> MeetingDetailOut:
    return detail_out(ctx, db, own_meeting(db, user, meeting_id))


@router.patch("/{meeting_id}")
def patch_meeting(meeting_id: str, body: MeetingPatch, user: UserDep, db: DbDep, ctx: CtxDep) -> MeetingDetailOut:
    m = own_meeting(db, user, meeting_id)
    if body.title is not None:
        m.title = body.title.strip()
    if body.template is not None:
        m.template = body.template
    if body.extra_instructions is not None:
        m.extra_instructions = body.extra_instructions.strip()
    if body.model_id is not None:
        model = usable_model(db, body.model_id)
        assert model is not None
        m.model_id, m.model_name = model.id, model.name
    db.commit()
    ctx.meetings.publish(m.id)
    return detail_out(ctx, db, m)


@router.get("/{meeting_id}/segments")
def list_segments(meeting_id: str, user: UserDep, db: DbDep) -> list[SegmentOut]:
    own_meeting(db, user, meeting_id)
    rows = db.scalars(
        select(MeetingSegment).where(MeetingSegment.meeting_id == meeting_id).order_by(MeetingSegment.idx)
    ).all()
    return [SegmentOut.of(s) for s in rows]


@router.api_route("/{meeting_id}/audio", methods=["GET", "HEAD"])
def meeting_audio(meeting_id: str, user: UserDep, db: DbDep, ctx: CtxDep) -> FileResponse:
    m = own_meeting(db, user, meeting_id)
    path = ctx.meetings.meeting_dir(m.id) / AUDIO_NAME
    if m.audio_purged or not path.is_file():
        raise HTTPException(404, "录音已过保留期清理")
    return FileResponse(path, media_type="audio/mpeg", headers={"cache-control": "private, no-cache, no-transform"})


@router.post("/{meeting_id}/cancel")
def cancel_meeting(meeting_id: str, user: UserDep, db: DbDep, ctx: CtxDep) -> MeetingOut:
    m = own_meeting(db, user, meeting_id)
    if m.status not in ("queued", "transcoding", "transcribing", "processing"):
        raise HTTPException(409, "这场会议已经结束，无法取消")
    ctx.meetings.request_cancel(meeting_id)
    return ctx.meetings.meeting_out(m, load_settings(db).file_retention_days)


@router.post("/{meeting_id}/retry")
def retry_meeting(meeting_id: str, user: UserDep, db: DbDep, ctx: CtxDep) -> MeetingOut:
    m = own_meeting(db, user, meeting_id)
    if m.status not in ("failed", "canceled"):
        raise HTTPException(409, "只有失败或已取消的会议可以重试")
    if ctx.meetings.is_running(meeting_id):
        raise HTTPException(409, "这场会议还在收尾，请稍后再试")
    if m.audio_purged:
        raise HTTPException(409, "录音已过保留期清理，无法重试")
    directory = ctx.meetings.meeting_dir(meeting_id)
    has_segments = bool(db.scalar(select(func.count(MeetingSegment.id)).where(MeetingSegment.meeting_id == meeting_id)))
    if has_segments:
        m.status = "processing"
    elif (directory / AUDIO_NAME).is_file() and m.asr_parts:
        # 已经识别完的分段保留；其余分段重新提交（服务商那边失败的任务查不回来了）
        reset = {"state": "pending", "task_id": None, "token": None, "token_exp": None, "submitted_at": None}
        m.asr_parts = [
            p
            if p.get("state") == "done" and p.get("raw") and (directory / str(p["raw"])).is_file()
            else {**p, **reset, "error": None}
            for p in m.asr_parts
        ]
        m.status = "transcribing"
    elif (directory / source_name(m.filename)).is_file():
        m.status = "queued"
    else:
        raise HTTPException(409, "原文件已经不在了，请重新上传")
    m.error = None
    m.error_kind = None
    m.finished_at = None
    m.stage = ""
    db.commit()
    ctx.meetings.request_launch(meeting_id)
    return ctx.meetings.meeting_out(m, load_settings(db).file_retention_days)


@router.delete("/{meeting_id}", status_code=204)
def delete_meeting(meeting_id: str, user: UserDep, db: DbDep, ctx: CtxDep) -> None:
    m = own_meeting(db, user, meeting_id)
    if m.status == "uploading":
        db.delete(m)
    else:
        # 保留这一行用来统计识别用量，内容全部删掉
        m.deleted_at = utcnow()
        m.minutes_md = None
        m.speakers = {}
        m.extra_instructions = ""
        db.execute(delete(MeetingSegment).where(MeetingSegment.meeting_id == meeting_id))
        db.execute(delete(MeetingMessage).where(MeetingMessage.meeting_id == meeting_id))
    db.commit()
    ctx.meetings.request_discard(meeting_id)
    with contextlib.suppress(Exception):
        if not ctx.meetings.is_running(meeting_id):
            ctx.meetings.remove_files(meeting_id)
