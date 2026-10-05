from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..models import AsrProvider, GlossaryTerm, Meeting, MeetingMessage, MeetingSegment
from ..security import SecretBox, mask_secret
from .asr import adapter_class
from .templates import TEMPLATE_IDS


class PartOut(BaseModel):
    index: int
    state: str
    offset_ms: int
    duration_ms: int


class MeetingOut(BaseModel):
    id: str
    title: str
    status: str
    stage: str
    progress: float
    transcript_state: str
    minutes_state: str
    minutes_stale: bool
    op: str | None
    error: str | None
    error_kind: str | None
    warning: str | None
    filename: str
    file_size: int
    duration_ms: int
    language: str
    provider_id: int | None
    provider_kind: str
    provider_name: str
    model_id: int | None
    model_name: str
    template: str
    extra_instructions: str
    expected_speakers: int | None
    asr_seconds: int
    speakers: dict[str, Any]
    parts: list[PartOut]
    tokens: int
    audio_available: bool
    audio_expires_at: datetime | None
    minutes_template: str | None
    minutes_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def of(cls, m: Meeting, *, retention_days: int, audio_exists: bool) -> MeetingOut:
        expires = None
        if audio_exists and not m.audio_purged and m.finished_at is not None:
            expires = m.finished_at + timedelta(days=retention_days)
        return cls(
            id=m.id,
            title=m.title,
            status=m.status,
            stage=m.stage or "",
            progress=round(m.progress or 0, 2),
            transcript_state=m.transcript_state,
            minutes_state=m.minutes_state,
            minutes_stale=bool(m.minutes_md) and m.minutes_rev is not None and m.minutes_rev != m.transcript_rev,
            op=m.op,
            error=m.error,
            error_kind=m.error_kind,
            warning=m.warning,
            filename=m.filename,
            file_size=m.file_size,
            duration_ms=m.duration_ms,
            language=m.language,
            provider_id=m.provider_id,
            provider_kind=m.provider_kind,
            provider_name=m.provider_name,
            model_id=m.model_id,
            model_name=m.model_name,
            template=m.template,
            extra_instructions=m.extra_instructions,
            expected_speakers=m.expected_speakers,
            asr_seconds=m.asr_seconds,
            speakers=m.speakers or {},
            parts=[
                PartOut(
                    index=int(p.get("index", i)),
                    state=str(p.get("state", "pending")),
                    offset_ms=int(p.get("offset_ms", 0)),
                    duration_ms=int(p.get("duration_ms", 0)),
                )
                for i, p in enumerate(m.asr_parts or [])
            ],
            tokens=m.tokens,
            audio_available=audio_exists and not m.audio_purged,
            audio_expires_at=expires,
            minutes_template=m.minutes_template,
            minutes_at=m.minutes_at,
            created_at=m.created_at,
            started_at=m.started_at,
            finished_at=m.finished_at,
        )


class MeetingDetailOut(MeetingOut):
    minutes_md: str | None = None


class MeetingPage(BaseModel):
    items: list[MeetingOut]
    total: int


class SegmentOut(BaseModel):
    idx: int
    start_ms: int
    end_ms: int
    speaker: str
    asr_speaker: str
    text: str
    raw_text: str
    edited: bool

    @classmethod
    def of(cls, s: MeetingSegment) -> SegmentOut:
        return cls(
            idx=s.idx,
            start_ms=s.start_ms,
            end_ms=s.end_ms,
            speaker=s.speaker,
            asr_speaker=s.asr_speaker,
            text=s.text,
            raw_text=s.raw_text,
            edited=s.edited,
        )


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime

    @classmethod
    def of(cls, m: MeetingMessage) -> MessageOut:
        return cls(id=m.id, role=m.role, content=m.content, created_at=m.created_at)


class MeetingCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    title: str = Field(default="", max_length=200)
    provider_id: int | None = None
    model_id: int | None = None
    template: str = Field(default="", max_length=32)
    extra_instructions: str = Field(default="", max_length=2000)
    expected_speakers: int | None = Field(default=None, ge=1, le=50)
    language: str = Field(default="zh", max_length=16)

    @field_validator("template")
    @classmethod
    def _template(cls, v: str) -> str:
        if v and v not in TEMPLATE_IDS:
            raise ValueError("未知的纪要模板")
        return v

    @field_validator("language")
    @classmethod
    def _language(cls, v: str) -> str:
        if v not in ("zh", "en", "auto"):
            raise ValueError("录音语言只能是 zh、en 或 auto")
        return v


class MeetingUploadOut(BaseModel):
    meeting: MeetingOut
    part_size: int
    parts: int


class MeetingPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    template: str | None = Field(default=None, max_length=32)
    extra_instructions: str | None = Field(default=None, max_length=2000)
    model_id: int | None = None

    @field_validator("template")
    @classmethod
    def _template(cls, v: str | None) -> str | None:
        if v is not None and v not in TEMPLATE_IDS:
            raise ValueError("未知的纪要模板")
        return v


class ProviderPublicOut(BaseModel):
    id: int
    name: str
    kind: str
    label: str
    description: str
    is_default: bool
    max_part_seconds: int
    hotwords: bool
    speaker_count: bool


class TemplateOut(BaseModel):
    id: str
    name: str
    description: str


class MeetingOptionsOut(BaseModel):
    providers: list[ProviderPublicOut]
    templates: list[TemplateOut]
    default_template: str
    max_audio_upload_mb: int
    max_audio_hours: int
    retention_days: int
    split_supported: bool


# ---------- 管理后台 ----------


class FieldSpecOut(BaseModel):
    key: str
    label: str
    secret: bool
    required: bool
    default: str
    placeholder: str
    hint: str
    options: list[list[str]]


class ProviderKindOut(BaseModel):
    kind: str
    label: str
    description: str
    max_part_seconds: int
    hotwords: bool
    speaker_count: bool
    fields: list[FieldSpecOut]


class ProviderAdminOut(BaseModel):
    id: int
    kind: str
    kind_label: str
    name: str
    description: str
    config: dict[str, Any]
    secrets_set: dict[str, bool]
    secrets_masked: dict[str, str]
    enabled: bool
    is_default: bool
    sort_order: int
    updated_at: datetime

    @classmethod
    def of(cls, p: AsrProvider, box: SecretBox) -> ProviderAdminOut:
        secrets = load_secrets(p, box)
        cls_ = adapter_class(p.kind)
        secret_keys = [f.key for f in cls_.fields if f.secret] if cls_ else list(secrets)
        return cls(
            id=p.id,
            kind=p.kind,
            kind_label=cls_.label if cls_ else p.kind,
            name=p.name,
            description=p.description,
            config=p.config or {},
            secrets_set={k: bool(secrets.get(k)) for k in secret_keys},
            secrets_masked={k: mask_secret(secrets.get(k, "")) for k in secret_keys},
            enabled=p.enabled,
            is_default=p.is_default,
            sort_order=p.sort_order,
            updated_at=p.updated_at,
        )


def load_secrets(p: AsrProvider, box: SecretBox) -> dict[str, str]:
    raw = box.decrypt(p.secret_enc)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


class ProviderIn(BaseModel):
    kind: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=255)
    config: dict[str, str] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    is_default: bool = False
    sort_order: int = 0


class ProviderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    config: dict[str, str] | None = None
    secrets: dict[str, str] | None = None  # 只更新填了的字段；留空的保持不变
    clear_secrets: list[str] = Field(default_factory=list)
    enabled: bool | None = None
    is_default: bool | None = None
    sort_order: int | None = None


class ProviderCheckOut(BaseModel):
    ok: bool
    message: str


class GlossaryTermOut(BaseModel):
    id: int
    term: str
    wrong_forms: list[str]
    note: str
    updated_at: datetime

    @classmethod
    def of(cls, t: GlossaryTerm) -> GlossaryTermOut:
        return cls(id=t.id, term=t.term, wrong_forms=list(t.wrong_forms or []), note=t.note, updated_at=t.updated_at)


class GlossaryTermIn(BaseModel):
    term: str = Field(min_length=1, max_length=64)
    wrong_forms: list[str] = Field(default_factory=list, max_length=20)
    note: str = Field(default="", max_length=255)

    @field_validator("term")
    @classmethod
    def _term(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("术语不能为空")
        return v

    @field_validator("wrong_forms")
    @classmethod
    def _wrong(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for item in v:
            item = item.strip()[:64]
            if item and item not in out:
                out.append(item)
        return out
