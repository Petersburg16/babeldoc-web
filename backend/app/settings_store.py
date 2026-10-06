from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .languages import LANGUAGE_CODES
from .meeting.templates import DEFAULT_TEMPLATE, TemplateId
from .models import Setting


class SystemSettings(BaseModel):
    site_name: str = Field(default="BabelDOC Web", min_length=1, max_length=40)
    announcement: str = Field(default="", max_length=500)
    registration: Literal["invite", "open", "closed"] = "invite"
    max_concurrent_jobs: int = Field(default=1, ge=1, le=4)
    max_upload_mb: int = Field(default=50, ge=1, le=200)
    max_pages_per_job: int = Field(default=300, ge=1, le=2000)
    max_active_jobs_per_user: int = Field(default=5, ge=1, le=50)
    default_page_quota: int = Field(default=1000, ge=0)
    file_retention_days: int = Field(default=30, ge=1, le=365)
    default_lang_in: str = "en"
    default_lang_out: str = "zh-CN"
    watermark_mode: Literal["no_watermark", "watermarked", "both"] = "no_watermark"
    # 会议记录
    public_base_url: str = Field(default="", max_length=200)
    max_audio_upload_mb: int = Field(default=1024, ge=10, le=4096)
    max_audio_hours: int = Field(default=5, ge=1, le=6)
    meeting_context_chars: int = Field(default=120000, ge=10000, le=1000000)
    default_meeting_template: TemplateId = DEFAULT_TEMPLATE

    @field_validator("public_base_url")
    @classmethod
    def _public_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if v and not re.fullmatch(r"https?://[^/\s?#]+", v):
            raise ValueError("站点公网地址只填协议和域名，例如 https://example.com")
        return v

    @field_validator("default_lang_in", "default_lang_out")
    @classmethod
    def _known_language(cls, v: str) -> str:
        if v not in LANGUAGE_CODES:
            raise ValueError("不支持的语言")
        return v


def load_settings(db: Session) -> SystemSettings:
    stored = {row.key: row.value for row in db.scalars(select(Setting))}
    known = {k: v for k, v in stored.items() if k in SystemSettings.model_fields}
    try:
        return SystemSettings(**known)
    except ValueError:
        merged = SystemSettings().model_dump()
        for key, value in known.items():
            try:
                merged = SystemSettings(**{**merged, key: value}).model_dump()
            except ValueError:
                continue
        return SystemSettings(**merged)


def save_settings(db: Session, settings: SystemSettings) -> None:
    for key, value in settings.model_dump().items():
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
    db.commit()
