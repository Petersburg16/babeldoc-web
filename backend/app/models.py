from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, utcnow

JOB_ACTIVE = ("queued", "running")
JOB_BILLABLE = ("queued", "running", "succeeded")
# 会议记录的主状态里，这些表示还在处理（上传中不算：没传完的由清理任务收尾）
MEETING_ACTIVE = ("queued", "transcoding", "transcribing", "processing")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")
    is_active: Mapped[bool] = mapped_column(default=True)
    page_quota: Mapped[int | None] = mapped_column(default=None)
    note: Mapped[str] = mapped_column(String(255), default="")
    invite_id: Mapped[int | None] = mapped_column(ForeignKey("invites.id", ondelete="SET NULL"), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(default=None)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class AuthSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    note: Mapped[str] = mapped_column(String(255), default="")
    max_uses: Mapped[int] = mapped_column(default=1)
    used_count: Mapped[int] = mapped_column(default=0)
    expires_at: Mapped[datetime | None] = mapped_column(default=None)
    revoked: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ModelProfile(Base):
    __tablename__ = "model_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(255), default="")
    base_url: Mapped[str] = mapped_column(String(255), default="")
    api_key_enc: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(128))
    term_model: Mapped[str | None] = mapped_column(String(128), default=None)
    qps: Mapped[int] = mapped_column(default=4)
    pool_max_workers: Mapped[int | None] = mapped_column(default=None)
    send_temperature: Mapped[bool] = mapped_column(default=True)
    json_mode: Mapped[bool] = mapped_column(default=False)
    # 已停用：翻译不开思考（开了就不能发 temperature=0，公式标记会不稳），保留列只为免迁移
    thinking: Mapped[str | None] = mapped_column(String(16), default=None)
    reasoning: Mapped[str | None] = mapped_column(String(32), default=None)
    enabled: Mapped[bool] = mapped_column(default=True)
    is_default: Mapped[bool] = mapped_column(default=False)
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True, default="queued")
    filename: Mapped[str] = mapped_column(String(255))
    file_size: Mapped[int] = mapped_column(default=0)
    page_count: Mapped[int] = mapped_column(default=0)
    pages: Mapped[str | None] = mapped_column(String(128), default=None)
    billed_pages: Mapped[int] = mapped_column(default=0)
    lang_in: Mapped[str] = mapped_column(String(16))
    lang_out: Mapped[str] = mapped_column(String(16))
    model_id: Mapped[int | None] = mapped_column(ForeignKey("model_profiles.id", ondelete="SET NULL"), default=None)
    model_name: Mapped[str] = mapped_column(String(64), default="")
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    progress: Mapped[float] = mapped_column(default=0.0)
    stage: Mapped[str] = mapped_column(String(128), default="")
    error: Mapped[str | None] = mapped_column(Text, default=None)
    error_kind: Mapped[str | None] = mapped_column(String(16), default=None)
    warning: Mapped[str | None] = mapped_column(Text, default=None)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    tokens: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    queued_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)
    files_purged: Mapped[bool] = mapped_column(default=False)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class AsrProvider(Base):
    """语音识别服务（会议记录用）。机密字段整体加密成一段 JSON 存在 secret_enc。"""

    __tablename__ = "asr_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(255), default="")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    secret_enc: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    is_default: Mapped[bool] = mapped_column(default=False)
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    # 主状态：uploading → queued → transcoding → transcribing → processing → done（或 failed / canceled）
    status: Mapped[str] = mapped_column(String(16), index=True, default="uploading")
    stage: Mapped[str] = mapped_column(String(128), default="")
    progress: Mapped[float] = mapped_column(default=0.0)
    # 子状态：done 之后的重跑只改这些，不动主状态
    transcript_state: Mapped[str] = mapped_column(String(16), default="none")  # none/raw/polishing/polished/partial
    minutes_state: Mapped[str] = mapped_column(String(16), default="none")  # none/generating/ready/failed
    op: Mapped[str | None] = mapped_column(String(32), default=None)  # 正在跑的后台操作，同一场会议只跑一个
    error: Mapped[str | None] = mapped_column(Text, default=None)
    error_kind: Mapped[str | None] = mapped_column(String(16), default=None)
    warning: Mapped[str | None] = mapped_column(Text, default=None)  # warnings 拼起来的文字，给界面直接显示
    # 按来源分开存的警告（asr、speakers、polish、minutes……）：某一步重跑时只清掉它自己的旧警告
    warnings: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    filename: Mapped[str] = mapped_column(String(255))
    file_size: Mapped[int] = mapped_column(default=0)
    duration_ms: Mapped[int] = mapped_column(default=0)
    language: Mapped[str] = mapped_column(String(16), default="zh")
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("asr_providers.id", ondelete="SET NULL"), default=None)
    provider_kind: Mapped[str] = mapped_column(String(32), default="")
    provider_name: Mapped[str] = mapped_column(String(64), default="")
    model_id: Mapped[int | None] = mapped_column(ForeignKey("model_profiles.id", ondelete="SET NULL"), default=None)
    model_name: Mapped[str] = mapped_column(String(64), default="")
    template: Mapped[str] = mapped_column(String(32), default="group_topic")
    extra_instructions: Mapped[str] = mapped_column(Text, default="")
    expected_speakers: Mapped[int | None] = mapped_column(default=None)
    # 识别分段：[{index, offset_ms, duration_ms, file, token, token_exp, state, task_id, error, raw, ...}]
    asr_parts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    asr_seconds: Mapped[int] = mapped_column(default=0)
    # 说话人：{"S1": {"name": "", "guess": {...} | None, "merged_into": None}}
    speakers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    minutes_md: Mapped[str | None] = mapped_column(Text, default=None)
    minutes_template: Mapped[str | None] = mapped_column(String(32), default=None)
    minutes_at: Mapped[datetime | None] = mapped_column(default=None)
    # 逐字稿内容每改一次加 1；纪要记下生成时的版本，不一致就提示纪要已过期
    transcript_rev: Mapped[int] = mapped_column(default=0)
    minutes_rev: Mapped[int | None] = mapped_column(default=None)
    tokens: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    audio_purged: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    # 每次写库都更新：前端据此丢掉比手里旧的数据（接口返回值和事件流可能乱序到达）
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)


class MeetingSegment(Base):
    __tablename__ = "meeting_segments"
    __table_args__ = (Index("ix_meeting_segments_meeting_idx", "meeting_id", "idx", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    idx: Mapped[int]
    start_ms: Mapped[int]
    end_ms: Mapped[int]
    asr_speaker: Mapped[str] = mapped_column(String(16))  # 识别给的说话人，合并后也不变，用于撤销
    speaker: Mapped[str] = mapped_column(String(16))
    raw_text: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    edited: Mapped[bool] = mapped_column(default=False)


class MeetingMessage(Base):
    __tablename__ = "meeting_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class GlossaryTerm(Base):
    """全站共用的术语表：正确写法 + 常见听错的写法。只有管理员能改。"""

    __tablename__ = "glossary_terms"

    id: Mapped[int] = mapped_column(primary_key=True)
    term: Mapped[str] = mapped_column(String(64), unique=True)
    wrong_forms: Mapped[list[str]] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(String(255), default="")
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
