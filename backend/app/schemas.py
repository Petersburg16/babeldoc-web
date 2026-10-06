from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field

from .models import Invite, Job, ModelProfile, User
from .security import mask_secret

USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]{3,32}$")
FILE_ORDER = ("dual", "mono", "glossary")


def normalize_username(v: str) -> str:
    v = v.strip().lower()
    if not USERNAME_PATTERN.match(v):
        raise ValueError("用户名只能包含小写字母、数字、下划线、点和短横线，长度 3-32")
    return v


def _check_base_url(v: str) -> str:
    v = v.strip().rstrip("/")
    if v and not v.startswith(("http://", "https://")):
        raise ValueError("接口地址需以 http:// 或 https:// 开头")
    return v


Username = Annotated[str, Field(min_length=3, max_length=40), AfterValidator(normalize_username)]
BaseUrl = Annotated[str, Field(max_length=255), AfterValidator(_check_base_url)]


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str

    @classmethod
    def of(cls, user: User) -> UserOut:
        return cls(id=user.id, username=user.username, display_name=user.display_name or user.username, role=user.role)


class UsageOut(BaseModel):
    month_pages: int
    quota: int
    month_tokens: int
    total_jobs: int


class MeOut(BaseModel):
    user: UserOut
    usage: UsageOut


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RegisterIn(BaseModel):
    username: Username
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=64)
    invite_code: str | None = Field(default=None, max_length=32)


class ProfileIn(BaseModel):
    display_name: str = Field(max_length=64)


class PasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=128)


class JobOptions(BaseModel):
    lang_in: str = "en"
    lang_out: str = "zh-CN"
    model_id: int | None = None
    term_model_id: int | None = None
    pages: str | None = Field(default=None, max_length=128)
    output: Literal["both", "dual", "mono"] = "both"
    dual_mode: Literal["side_by_side", "alternating"] = "side_by_side"
    dual_translate_first: bool = False
    translate_table_text: bool = False
    enhance_compatibility: bool = False
    ocr_workaround: bool = False
    auto_enable_ocr_workaround: bool = True
    skip_scanned_detection: bool = False
    primary_font_family: Literal["serif", "sans-serif", "script"] | None = None
    only_include_translated_page: bool = False
    auto_extract_glossary: bool = True
    custom_system_prompt: str | None = Field(default=None, max_length=2000)


class JobOut(BaseModel):
    id: str
    status: str
    filename: str
    file_size: int
    page_count: int
    pages: str | None
    billed_pages: int
    lang_in: str
    lang_out: str
    model_name: str
    options: dict[str, Any]
    progress: float
    stage: str
    error: str | None
    error_kind: str | None
    warning: str | None
    files: list[str]
    stats: dict[str, Any] | None
    tokens: int
    attempts: int
    queue_position: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    files_purged: bool
    username: str | None = None

    @classmethod
    def of(cls, job: Job, *, position: int | None = None, username: str | None = None) -> JobOut:
        files: list[str] = []
        stats = None
        if job.result:
            produced = job.result.get("files") or {}
            if not job.files_purged:
                files = [k for k in FILE_ORDER if produced.get(k)]
            raw = job.result.get("stats") or {}
            stats = {
                "seconds": raw.get("seconds"),
                "total_tokens": raw.get("total_tokens"),
                "calls": raw.get("calls"),
                "valid_chars": raw.get("valid_chars"),
            }
        if not job.files_purged:
            files.append("original")
        return cls(
            id=job.id,
            status=job.status,
            filename=job.filename,
            file_size=job.file_size,
            page_count=job.page_count,
            pages=job.pages,
            billed_pages=job.billed_pages,
            lang_in=job.lang_in,
            lang_out=job.lang_out,
            model_name=job.model_name,
            options=job.options or {},
            progress=round(job.progress or 0, 2),
            stage=job.stage or "",
            error=job.error,
            error_kind=job.error_kind,
            warning=job.warning,
            files=files,
            stats=stats,
            tokens=job.tokens or 0,
            attempts=job.attempts or 0,
            queue_position=position if job.status == "queued" else None,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            files_purged=job.files_purged,
            username=username,
        )


class JobPage(BaseModel):
    items: list[JobOut]
    total: int


class ModelPublicOut(BaseModel):
    id: int
    name: str
    description: str
    is_default: bool


class ModelAdminOut(BaseModel):
    id: int
    name: str
    description: str
    base_url: str
    model: str
    term_model: str | None
    qps: int
    pool_max_workers: int | None
    send_temperature: bool
    json_mode: bool
    enabled: bool
    is_default: bool
    sort_order: int
    api_key_set: bool
    api_key_masked: str
    updated_at: datetime

    @classmethod
    def of(cls, m: ModelProfile, api_key: str) -> ModelAdminOut:
        return cls(
            id=m.id,
            name=m.name,
            description=m.description,
            base_url=m.base_url,
            model=m.model,
            term_model=m.term_model,
            qps=m.qps,
            pool_max_workers=m.pool_max_workers,
            send_temperature=m.send_temperature,
            json_mode=m.json_mode,
            enabled=m.enabled,
            is_default=m.is_default,
            sort_order=m.sort_order,
            api_key_set=bool(api_key),
            api_key_masked=mask_secret(api_key),
            updated_at=m.updated_at,
        )


class ModelIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=255)
    base_url: BaseUrl = ""
    api_key: str = Field(default="", max_length=512)
    model: str = Field(min_length=1, max_length=128)
    term_model: str | None = Field(default=None, max_length=128)
    qps: int = Field(default=4, ge=1, le=100)
    pool_max_workers: int | None = Field(default=None, ge=1, le=128)
    send_temperature: bool = True
    json_mode: bool = False
    enabled: bool = True
    is_default: bool = False
    sort_order: int = 0


class ModelPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    base_url: BaseUrl | None = None
    api_key: str | None = Field(default=None, max_length=512)
    clear_api_key: bool = False
    model: str | None = Field(default=None, min_length=1, max_length=128)
    term_model: str | None = Field(default=None, max_length=128)
    qps: int | None = Field(default=None, ge=1, le=100)
    pool_max_workers: int | None = Field(default=None, ge=1, le=128)
    send_temperature: bool | None = None
    json_mode: bool | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    sort_order: int | None = None


class ModelProbeIn(BaseModel):
    base_url: BaseUrl = ""
    api_key: str = Field(default="", max_length=512)
    model_id: int | None = None


class ModelTestOut(BaseModel):
    ok: bool
    latency_ms: int | None = None
    status: int | None = None
    reply: str | None = None
    error: str | None = None
    usage: dict[str, Any] | None = None


class InviteOut(BaseModel):
    id: int
    code: str
    note: str
    max_uses: int
    used_count: int
    expires_at: datetime | None
    revoked: bool
    created_at: datetime
    state: Literal["active", "used", "expired", "revoked"]

    @classmethod
    def of(cls, inv: Invite, now: datetime) -> InviteOut:
        if inv.revoked:
            state = "revoked"
        elif inv.used_count >= inv.max_uses:
            state = "used"
        elif inv.expires_at and inv.expires_at < now:
            state = "expired"
        else:
            state = "active"
        return cls(
            id=inv.id,
            code=inv.code,
            note=inv.note,
            max_uses=inv.max_uses,
            used_count=inv.used_count,
            expires_at=inv.expires_at,
            revoked=inv.revoked,
            created_at=inv.created_at,
            state=state,
        )


class InviteIn(BaseModel):
    note: str = Field(default="", max_length=255)
    max_uses: int = Field(default=1, ge=1, le=1000)
    expires_in_days: int | None = Field(default=7, ge=1, le=365)


class AdminUserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    is_active: bool
    page_quota: int | None
    effective_quota: int
    note: str
    month_pages: int
    total_jobs: int
    created_at: datetime
    last_login_at: datetime | None
    month_audio_seconds: int = 0


class AdminUserIn(BaseModel):
    username: Username
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=64)
    role: Literal["user", "admin"] = "user"
    page_quota: int | None = Field(default=None, ge=0)
    note: str = Field(default="", max_length=255)


class AdminUserPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=64)
    role: Literal["user", "admin"] | None = None
    is_active: bool | None = None
    page_quota: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=255)


class MetaOut(BaseModel):
    site_name: str
    announcement: str
    registration: str
    needs_setup: bool
    languages: list[dict[str, str]]
    default_lang_in: str
    default_lang_out: str
    max_upload_mb: int
    max_pages_per_job: int
    max_files: int
    file_retention_days: int
    engine: str
    version: str
