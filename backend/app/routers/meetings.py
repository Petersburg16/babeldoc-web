"""会议记录：分片上传、列表、详情、逐字稿、回听、取消、重试、删除。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..db import utcnow
from ..defaults import default_or_first
from ..deps import AppContext, CtxDep, DbDep, UserDep
from ..meeting.asr import available_kinds
from ..meeting.llm_config import find_preset
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
    PresetPublicOut,
    ProviderPublicOut,
    SegmentOut,
    TemplateOut,
)
from ..meeting.templates import TEMPLATES
from ..models import MEETING_ACTIVE, AsrProvider, Meeting, MeetingLlmPreset, MeetingMessage, MeetingSegment, User
from ..security import new_job_id
from ..settings_store import load_settings
from .jobs import clean_filename

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

CONTROL = re.compile(r"[\x00-\x1f\x7f\ufffe\uffff]")
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
    provider = default_or_first(db, query, AsrProvider)
    if provider is None:
        raise HTTPException(400, "管理员还没有配置语音识别服务")
    return provider


def usable_preset(db: Session, preset_id: int | None) -> MeetingLlmPreset | None:
    """指定了就必须可用；没指定用默认方案（都没有返回 None：识别照常进行，整理时再提示）。"""
    if preset_id is not None:
        preset = db.scalar(
            select(MeetingLlmPreset).where(MeetingLlmPreset.id == preset_id, MeetingLlmPreset.enabled.is_(True))
        )
        if preset is None:
            raise HTTPException(400, "所选的整理方案不可用")
        return preset
    return find_preset(db, None)


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
        split_supported=True,  # 超长录音一律自动切段；字段保留给前端
        presets=[
            PresetPublicOut(id=p.id, name=p.name, description=p.description, is_default=p.is_default)
            for p in db.scalars(
                select(MeetingLlmPreset)
                .where(MeetingLlmPreset.enabled.is_(True))
                .order_by(MeetingLlmPreset.sort_order, MeetingLlmPreset.id)
            )
        ],
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
    if not user.is_admin:
        active = db.scalar(
            select(func.count(Meeting.id)).where(
                Meeting.user_id == user.id,
                Meeting.deleted_at.is_(None),
                Meeting.status.in_(("uploading", *MEETING_ACTIVE)),
            )
        )
        if (active or 0) >= settings.max_active_jobs_per_user:
            raise HTTPException(
                400, f"同时上传或处理中的会议最多 {settings.max_active_jobs_per_user} 场，请等前面的完成"
            )
    ensure_disk(ctx, body.size)
    provider = usable_provider(db, ctx, body.provider_id)
    preset = usable_preset(db, body.llm_preset_id)
    meeting = Meeting(
        id=new_job_id(),
        user_id=user.id,
        title=(" ".join(CONTROL.sub(" ", body.title).split()) or Path(filename).stem)[:200],
        filename=filename,
        file_size=body.size,
        language=body.language,
        provider_id=provider.id,
        provider_kind=provider.kind,
        provider_name=provider.name,
        llm_preset_id=preset.id if preset else None,
        model_name=preset.name if preset else "",
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


def ensure_disk(ctx: AppContext, size: int) -> None:
    """拼接分片时分片和原件同时存在，再加转码输出，至少留 2 倍文件大小加 1 GB 余量；磁盘写满会连带数据库写不进去。"""
    ctx.meetings.config.meetings_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(ctx.meetings.config.meetings_dir).free
    if free < size * 2 + 1024**3:
        raise HTTPException(507, "服务器磁盘空间不足，请联系管理员")


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
        m.title = " ".join(CONTROL.sub(" ", body.title).split()) or m.title
    if body.template is not None:
        m.template = body.template
    if body.extra_instructions is not None:
        m.extra_instructions = body.extra_instructions.strip()
    if body.llm_preset_id is not None:
        preset = usable_preset(db, body.llm_preset_id)
        assert preset is not None
        m.llm_preset_id, m.model_name = preset.id, preset.name
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
    parts = m.asr_parts or []
    if has_segments:
        status = "processing"
    elif (directory / AUDIO_NAME).is_file() and parts:
        parts = [retry_part(p, directory) for p in parts]
        status = "transcribing"
    elif (directory / source_name(m.filename)).is_file():
        status = "queued"
    else:
        raise HTTPException(409, "原文件已经不在了，请重新上传")
    kept = {k: v for k, v in (m.warnings or {}).items() if k == "asr" and status == "processing" and v}
    # 条件更新：两台设备同时点重试时只有一个生效，另一个不会覆盖驱动刚写入的令牌
    changed = db.execute(
        update(Meeting)
        .where(Meeting.id == meeting_id, Meeting.status.in_(("failed", "canceled")))
        .values(
            status=status,
            asr_parts=parts,
            error=None,
            error_kind=None,
            finished_at=None,
            stage="",
            warning="\n".join(kept.values()) or None,
            warnings=kept,
        )
    ).rowcount
    db.commit()
    if not changed:
        raise HTTPException(409, "这场会议的状态已经变了，请刷新后再试")
    ctx.meetings.request_launch(meeting_id)
    db.refresh(m)
    return ctx.meetings.meeting_out(m, load_settings(db).file_retention_days)


def retry_part(part: dict, directory: Path) -> dict:
    """重试时一个分段怎么处理：结果已在盘上的算完成；服务商明确失败（final）或根本没提交上的重新提交；
    其余（断网、被一起取消、我方出错）保留任务号，接着查同一个任务，不重复计费。"""
    raw = str(part.get("raw") or f"asr-{part.get('index', 0)}.json")
    if (directory / raw).is_file():
        return {**part, "state": "done", "raw": raw, "error": None}
    if part.get("final") or not part.get("task_id"):
        return {
            **part,
            "state": "pending",
            "task_id": None,
            "token": None,
            "token_exp": None,
            "submitted_at": None,
            "error": None,
            "final": False,
        }
    return {**part, "state": "submitted", "error": None}


@router.delete("/{meeting_id}", status_code=204)
def delete_meeting(meeting_id: str, user: UserDep, db: DbDep, ctx: CtxDep) -> None:
    m = own_meeting(db, user, meeting_id)
    if m.status == "uploading":
        db.delete(m)
    else:
        # 保留这一行用来统计识别用量，内容全部删掉（标题、文件名本身也可能敏感）
        m.deleted_at = utcnow()
        m.title = "（已删除）"
        m.filename = ""
        m.minutes_md = None
        m.speakers = {}
        m.extra_instructions = ""
        m.error = None
        m.warning = None
        m.warnings = {}
        m.asr_parts = []
        db.execute(delete(MeetingSegment).where(MeetingSegment.meeting_id == meeting_id))
        db.execute(delete(MeetingMessage).where(MeetingMessage.meeting_id == meeting_id))
    db.commit()
    # 让同一用户的其他标签页、设备也把这条会议从列表里拿掉
    ctx.bus.publish(user.id, {"type": "meeting_removed", "id": meeting_id})
    ctx.meetings.request_discard(meeting_id)
    with contextlib.suppress(Exception):
        if not ctx.meetings.is_running(meeting_id):
            ctx.meetings.remove_files(meeting_id)
