from __future__ import annotations

import re
import shutil
from datetime import timedelta
from pathlib import Path
from typing import IO, Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError
from pypdf import PdfReader
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..deps import AppContext, CtxDep, DbDep, UserDep
from ..job_ops import cancel_job, check_capacity, default_model, delete_job, retry_job
from ..languages import LANGUAGE_CODES
from ..models import JOB_ACTIVE, Job, ModelProfile, User
from ..pages import count_pages, normalize_pages, parse_pages
from ..schemas import JobOptions, JobOut, JobPage
from ..security import new_job_id
from ..services import download_name, job_dir, queue_positions, result_file
from ..settings_store import load_settings
from .meta import MAX_FILES_PER_UPLOAD

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

GLOSSARY_MAX_BYTES = 1024 * 1024
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_filename(raw: str | None) -> str:
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = _CONTROL.sub("", name).strip()
    return name[:200] or "document.pdf"


def copy_limited(src: IO[bytes], dst: Path, limit: int, name: str) -> int:
    size = 0
    with dst.open("wb") as out:
        while chunk := src.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"{name} 超过单文件大小上限 {limit // 1024 // 1024} MB")
            out.write(chunk)
    if size == 0:
        raise HTTPException(400, f"{name} 是空文件")
    return size


def inspect_pdf(path: Path, name: str) -> int:
    with path.open("rb") as fh:
        head = fh.read(1024)
    if b"%PDF-" not in head:
        raise HTTPException(400, f"{name} 不是有效的 PDF 文件")
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise HTTPException(400, f"{name} 设置了打开密码，请先移除密码再上传")
        pages = len(reader.pages)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"{name} 无法解析，文件可能已损坏") from e
    if pages == 0:
        raise HTTPException(400, f"{name} 没有任何页面")
    return pages


def read_glossary(upload: UploadFile) -> bytes:
    data = upload.file.read(GLOSSARY_MAX_BYTES + 1)
    if len(data) > GLOSSARY_MAX_BYTES:
        raise HTTPException(400, "术语表不能超过 1 MB")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise HTTPException(400, "术语表编码无法识别，请保存为 UTF-8 的 CSV")
    header = text.splitlines()[0].lower() if text.strip() else ""
    columns = {c.strip().strip('"') for c in header.split(",")}
    if not {"source", "target"} <= columns:
        raise HTTPException(400, "术语表需为 CSV，表头至少包含 source,target 两列")
    return text.encode("utf-8")


def own_job(db: Session, user: User, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.deleted_at is not None or job.user_id != user.id:
        raise HTTPException(404, "任务不存在")
    return job


def to_out(ctx: AppContext, job: Job, position: int | None = None) -> JobOut:
    out = JobOut.of(job, position=position)
    live = ctx.manager.live_progress(job.id) if job.status == "running" else None
    if live:
        out.progress, out.stage = round(live[0], 2), live[1]
    return out


@router.get("")
def list_jobs(
    user: UserDep,
    db: DbDep,
    ctx: CtxDep,
    status: Annotated[Literal["all", "active", "succeeded", "failed"], Query()] = "all",
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobPage:
    conditions = [Job.user_id == user.id, Job.deleted_at.is_(None)]
    if status == "active":
        conditions.append(Job.status.in_(JOB_ACTIVE))
    elif status == "succeeded":
        conditions.append(Job.status == "succeeded")
    elif status == "failed":
        conditions.append(Job.status.in_(("failed", "canceled")))
    total = db.scalar(select(func.count(Job.id)).where(*conditions)) or 0
    jobs = db.scalars(select(Job).where(*conditions).order_by(Job.created_at.desc()).limit(limit).offset(offset)).all()
    positions = queue_positions(db)
    return JobPage(items=[to_out(ctx, j, positions.get(j.id)) for j in jobs], total=total)


@router.post("", status_code=201)
def create_jobs(
    user: UserDep,
    db: DbDep,
    ctx: CtxDep,
    files: Annotated[list[UploadFile], File()],
    options: Annotated[str, Form()] = "{}",
    glossary: Annotated[UploadFile | None, File()] = None,
) -> list[JobOut]:
    try:
        opts = JobOptions.model_validate_json(options)
    except ValidationError as e:
        raise HTTPException(400, f"选项格式错误：{e.errors()[0].get('msg', '')}") from e
    if opts.lang_in not in LANGUAGE_CODES or opts.lang_out not in LANGUAGE_CODES:
        raise HTTPException(400, "不支持的语言")
    if opts.lang_in == opts.lang_out:
        raise HTTPException(400, "源语言和目标语言不能相同")
    if not files:
        raise HTTPException(400, "请选择要翻译的 PDF")
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(400, f"一次最多上传 {MAX_FILES_PER_UPLOAD} 个文件")

    profile = db.get(ModelProfile, opts.model_id) if opts.model_id else default_model(db)
    if profile is None or not profile.enabled:
        raise HTTPException(400, "所选模型不可用" if opts.model_id else "管理员还没有配置可用的翻译模型")
    term_profile = None
    if opts.term_model_id and opts.auto_extract_glossary and opts.term_model_id != profile.id:
        term_profile = db.get(ModelProfile, opts.term_model_id)
        if term_profile is None or not term_profile.enabled:
            raise HTTPException(400, "所选术语提取模型不可用")

    pages = normalize_pages(opts.pages)
    if pages:
        try:
            parse_pages(pages)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    glossary_bytes = read_glossary(glossary) if glossary is not None and glossary.filename else None
    settings = load_settings(db)
    max_bytes = settings.max_upload_mb * 1024 * 1024
    staged: list[tuple[str, str, int, int, int]] = []
    created: list[Path] = []
    try:
        for upload in files:
            name = clean_filename(upload.filename)
            if not name.lower().endswith(".pdf"):
                raise HTTPException(400, f"{name} 不是 PDF 文件")
            job_id = new_job_id()
            directory = job_dir(ctx.config.jobs_dir, job_id)
            directory.mkdir(parents=True)
            created.append(directory)
            size = copy_limited(upload.file, directory / "input.pdf", max_bytes, name)
            page_count = inspect_pdf(directory / "input.pdf", name)
            billed = count_pages(pages, page_count)
            if billed == 0:
                raise HTTPException(400, f"{name}：页码范围超出了文档页数（共 {page_count} 页）")
            if billed > settings.max_pages_per_job:
                limit = settings.max_pages_per_job
                raise HTTPException(
                    400, f"{name}：需翻译 {billed} 页，超过单个任务上限 {limit} 页，可用页码范围分批翻译"
                )
            if glossary_bytes:
                (directory / "glossary.csv").write_bytes(glossary_bytes)
            staged.append((job_id, name, size, page_count, billed))

        check_capacity(db, user, sum(s[4] for s in staged), len(staged))
        now = utcnow()
        stored_options = opts.model_dump(exclude={"model_id", "term_model_id", "pages", "lang_in", "lang_out"})
        if term_profile is not None:
            stored_options.update(term_model_id=term_profile.id, term_model_name=term_profile.name)
        jobs = []
        for index, (job_id, name, size, page_count, billed) in enumerate(staged):
            job = Job(
                id=job_id,
                user_id=user.id,
                status="queued",
                filename=name,
                file_size=size,
                page_count=page_count,
                pages=pages,
                billed_pages=billed,
                lang_in=opts.lang_in,
                lang_out=opts.lang_out,
                model_id=profile.id,
                model_name=profile.name,
                options=stored_options,
                created_at=now,
                queued_at=now + timedelta(microseconds=index),
            )
            db.add(job)
            jobs.append(job)
        db.commit()
    except BaseException:
        db.rollback()
        for directory in created:
            shutil.rmtree(directory, ignore_errors=True)
        raise

    ctx.manager.wake()
    ctx.manager.broadcast_queue()
    positions = queue_positions(db)
    return [to_out(ctx, job, positions.get(job.id)) for job in jobs]


@router.get("/{job_id}")
def get_job(job_id: str, user: UserDep, db: DbDep, ctx: CtxDep) -> JobOut:
    job = own_job(db, user, job_id)
    return to_out(ctx, job, queue_positions(db).get(job.id))


@router.post("/{job_id}/cancel")
def cancel(job_id: str, user: UserDep, db: DbDep, ctx: CtxDep) -> JobOut:
    job = own_job(db, user, job_id)
    cancel_job(db, ctx, job)
    db.refresh(job)
    return to_out(ctx, job)


@router.post("/{job_id}/retry")
def retry(job_id: str, user: UserDep, db: DbDep, ctx: CtxDep) -> JobOut:
    job = own_job(db, user, job_id)
    retry_job(db, ctx, job)
    db.refresh(job)
    return to_out(ctx, job, queue_positions(db).get(job.id))


@router.delete("/{job_id}", status_code=204)
def remove(job_id: str, user: UserDep, db: DbDep, ctx: CtxDep) -> None:
    job = own_job(db, user, job_id)
    delete_job(db, ctx, job)


@router.get("/{job_id}/files/{kind}")
def download(
    job_id: str,
    kind: Literal["dual", "mono", "glossary", "original"],
    user: UserDep,
    db: DbDep,
    ctx: CtxDep,
    inline: bool = False,
) -> FileResponse:
    job = own_job(db, user, job_id)
    path = result_file(ctx.config.jobs_dir, job, kind)
    if path is None:
        raise HTTPException(404, "文件不存在或已过期清理")
    return FileResponse(
        path,
        media_type="text/csv; charset=utf-8" if kind == "glossary" else "application/pdf",
        filename=download_name(job, kind),
        content_disposition_type="inline" if inline else "attachment",
    )
