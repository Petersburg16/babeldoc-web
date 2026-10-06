"""会议记录的编辑与重跑：改逐字稿、改名与合并说话人、重新整理、重新生成纪要。

逐字稿的文字或说话人归属每改一次 transcript_rev 加 1（纪要据此判断是否过期）；
改名、合并只是换显示的名字（纪要里写的是 [[S3]] 占位符），不动 transcript_rev。
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..deps import CtxDep, DbDep, UserDep
from ..meeting.llm_config import resolve_meeting_llm
from ..meeting.processing import OP_LABELS, OPS
from ..meeting.schemas import MeetingDetailOut, MeetingOut, SegmentOut
from ..meeting.speakers import lock_meeting, speaker_number
from ..meeting.templates import TemplateId
from ..models import Meeting, MeetingSegment, User
from .meetings import detail_out, own_meeting, usable_preset

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

MAX_TEXT = 5000
MAX_NAME = 32


class SegmentPatch(BaseModel):
    text: str | None = Field(default=None, max_length=MAX_TEXT)
    speaker: str | None = Field(default=None, min_length=1, max_length=16)


class RenameIn(BaseModel):
    name: str = Field(default="", max_length=MAX_NAME)


class MergeIn(BaseModel):
    source: str = Field(min_length=1, max_length=16)
    target: str = Field(min_length=1, max_length=16)


class UnmergeIn(BaseModel):
    speaker: str = Field(min_length=1, max_length=16)


class AcceptGuessesIn(BaseModel):
    speakers: list[str] | None = None


class OpIn(BaseModel):
    template: TemplateId | None = None
    extra_instructions: str | None = Field(default=None, max_length=2000)
    # 换整理方案：之后的整理、纪要、对话都按新方案
    llm_preset_id: int | None = None


def _locked(db: Session, user: User, meeting_id: str) -> Meeting:
    """取会议并拿到写锁，后面读改写 speakers 时不会和后台的说话人识别互相覆盖。"""
    m = own_meeting(db, user, meeting_id)
    lock_meeting(db, meeting_id)
    db.refresh(m)
    return m


def _speakers(m: Meeting) -> dict[str, dict[str, Any]]:
    # 拷贝一份再改，最后整体赋回去：SQLAlchemy 不追踪 JSON 列的就地修改
    return {k: dict(v) for k, v in (m.speakers or {}).items() if isinstance(v, dict)}


def _known(speakers: dict[str, dict[str, Any]], sid: str) -> dict[str, Any]:
    info = speakers.get(sid)
    if info is None:
        raise HTTPException(404, "说话人不存在")
    return info


def _resolve(speakers: dict[str, dict[str, Any]], sid: str) -> str:
    current = sid
    for _ in range(len(speakers) + 1):
        target = speakers.get(current, {}).get("merged_into")
        if not target or target == current or target not in speakers:
            break
        current = target
    return current


def _segment(db: Session, meeting_id: str, idx: int) -> MeetingSegment:
    seg = db.scalar(select(MeetingSegment).where(MeetingSegment.meeting_id == meeting_id, MeetingSegment.idx == idx))
    if seg is None:
        raise HTTPException(404, "这句话不存在")
    return seg


def _bump_rev(db: Session, meeting_id: str) -> None:
    # 原子加 1：后台整理同时在改逐字稿时不会丢版本号
    db.execute(
        update(Meeting)
        .where(Meeting.id == meeting_id)
        .values(transcript_rev=Meeting.transcript_rev + 1)
        .execution_options(synchronize_session=False)
    )


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff]")


def _clean_text(text: str) -> str:
    return re.sub(r"\s*[\r\n]+\s*", " ", _CONTROL.sub(" ", text)).strip()


@router.patch("/{meeting_id}/segments/{idx}")
def edit_segment(meeting_id: str, idx: int, body: SegmentPatch, user: UserDep, db: DbDep, ctx: CtxDep) -> SegmentOut:
    if body.text is None and body.speaker is None:
        raise HTTPException(400, "没有要修改的内容")
    m = _locked(db, user, meeting_id)
    seg = _segment(db, meeting_id, idx)
    changed = False
    if body.text is not None:
        text = _clean_text(body.text)
        if not text:
            raise HTTPException(400, "内容不能为空")
        if text != seg.text:
            seg.text = text
            seg.edited = True
            changed = True
    if body.speaker is not None:
        speakers = _speakers(m)
        if body.speaker == "new":
            number = max((speaker_number(k) for k in speakers if k[1:].isdigit()), default=0) + 1
            target = f"S{number}"
            speakers[target] = {"name": "", "guess": None, "merged_into": None}
            m.speakers = speakers
        else:
            _known(speakers, body.speaker)
            target = _resolve(speakers, body.speaker)
        if target != seg.speaker:
            seg.speaker = target
            changed = True
    if changed:
        _bump_rev(db, meeting_id)
    db.commit()
    db.refresh(seg)
    ctx.meetings.publish(meeting_id)
    return SegmentOut.of(seg)


@router.post("/{meeting_id}/segments/{idx}/revert")
def revert_segment(meeting_id: str, idx: int, user: UserDep, db: DbDep, ctx: CtxDep) -> SegmentOut:
    _locked(db, user, meeting_id)
    seg = _segment(db, meeting_id, idx)
    if seg.text != seg.raw_text or seg.edited:
        seg.text = seg.raw_text
        seg.edited = False
        _bump_rev(db, meeting_id)
    db.commit()
    db.refresh(seg)
    ctx.meetings.publish(meeting_id)
    return SegmentOut.of(seg)


@router.post("/{meeting_id}/speakers/merge")
def merge_speakers(meeting_id: str, body: MergeIn, user: UserDep, db: DbDep, ctx: CtxDep) -> MeetingDetailOut:
    m = _locked(db, user, meeting_id)
    speakers = _speakers(m)
    source = _known(speakers, body.source)
    _known(speakers, body.target)
    if source.get("merged_into"):
        raise HTTPException(400, "这位说话人已经合并过了，请先撤销")
    target = _resolve(speakers, body.target)
    if target == body.source:
        raise HTTPException(400, "不能合并到自己")
    db.execute(
        update(MeetingSegment)
        .where(MeetingSegment.meeting_id == meeting_id, MeetingSegment.speaker == body.source)
        .values(speaker=target)
        .execution_options(synchronize_session=False)
    )
    source["merged_into"] = target
    source["merge_hint"] = None
    for sid, info in speakers.items():
        # 之前合并到 source 的直接改指向最终目标，撤销合并时各回各的
        if info.get("merged_into") == body.source:
            info["merged_into"] = target
        hint = info.get("merge_hint")
        if isinstance(hint, dict) and hint.get("with") == body.source:
            info["merge_hint"] = None if sid == target else {**hint, "with": target}
    m.speakers = speakers
    db.commit()
    ctx.meetings.publish(meeting_id)
    return detail_out(ctx, db, m)


@router.post("/{meeting_id}/speakers/unmerge")
def unmerge_speaker(meeting_id: str, body: UnmergeIn, user: UserDep, db: DbDep, ctx: CtxDep) -> MeetingDetailOut:
    m = _locked(db, user, meeting_id)
    speakers = _speakers(m)
    info = _known(speakers, body.speaker)
    if not info.get("merged_into"):
        raise HTTPException(400, "这位说话人没有被合并")
    target = _resolve(speakers, body.speaker)
    # 只还原识别时就属于这位的句子；手动改过归属的句子保持不动
    db.execute(
        update(MeetingSegment)
        .where(
            MeetingSegment.meeting_id == meeting_id,
            MeetingSegment.asr_speaker == body.speaker,
            MeetingSegment.speaker == target,
        )
        .values(speaker=body.speaker)
        .execution_options(synchronize_session=False)
    )
    info["merged_into"] = None
    m.speakers = speakers
    _bump_rev(db, meeting_id)
    db.commit()
    ctx.meetings.publish(meeting_id)
    return detail_out(ctx, db, m)


@router.post("/{meeting_id}/speakers/accept-guesses")
def accept_guesses(meeting_id: str, body: AcceptGuessesIn, user: UserDep, db: DbDep, ctx: CtxDep) -> MeetingDetailOut:
    m = _locked(db, user, meeting_id)
    speakers = _speakers(m)
    if body.speakers is None:
        # 全部采纳时不覆盖已经命名的
        wanted = [sid for sid, info in speakers.items() if not str(info.get("name") or "").strip()]
    else:
        wanted = body.speakers
    for sid in wanted:
        info = speakers.get(sid)
        guess = info.get("guess") if info else None
        if info is not None and isinstance(guess, dict) and str(guess.get("name") or "").strip():
            info["name"] = str(guess["name"]).strip()[:MAX_NAME]
    m.speakers = speakers
    db.commit()
    ctx.meetings.publish(meeting_id)
    return detail_out(ctx, db, m)


@router.post("/{meeting_id}/speakers/{speaker}/rename")
def rename_speaker(
    meeting_id: str, speaker: str, body: RenameIn, user: UserDep, db: DbDep, ctx: CtxDep
) -> MeetingDetailOut:
    m = _locked(db, user, meeting_id)
    speakers = _speakers(m)
    info = _known(speakers, speaker)
    info["name"] = " ".join(_CONTROL.sub(" ", body.name).split())  # 空字符串表示取消命名，显示回“说话人 N”
    m.speakers = speakers
    db.commit()
    ctx.meetings.publish(meeting_id)
    return detail_out(ctx, db, m)


@router.post("/{meeting_id}/ops/{op}", status_code=202)
def start_op(meeting_id: str, op: str, user: UserDep, db: DbDep, ctx: CtxDep, body: OpIn | None = None) -> MeetingOut:
    if op not in OPS:
        raise HTTPException(404, "未知的操作")
    body = body or OpIn()
    m = own_meeting(db, user, meeting_id)
    if m.status != "done":
        raise HTTPException(409, "会议还没处理完，请稍后再试")
    if m.op:
        raise HTTPException(409, f"正在{OP_LABELS.get(m.op, '处理')}，请等它完成")
    params: dict[str, Any] = {}
    values: dict[str, Any] = {"op": op}
    preset_id = m.llm_preset_id
    if body.llm_preset_id is not None:
        preset = usable_preset(db, body.llm_preset_id)
        assert preset is not None
        preset_id = preset.id
        # 和占住 op 写在同一条 UPDATE 里：撞上 409 时方案不会只改了一半
        values.update(llm_preset_id=preset.id, model_name=preset.name)
    _, reason = resolve_meeting_llm(db, ctx.secrets, preset_id, op)
    if reason is not None:
        raise HTTPException(400, reason)
    if op == "polish":
        params["previous"] = m.transcript_state
        values["transcript_state"] = "polishing"
    elif op == "minutes":
        params["previous"] = m.minutes_state
        values["minutes_state"] = "generating"
        if body.template is not None:
            params["template"] = body.template
        if body.extra_instructions is not None:
            params["extra_instructions"] = body.extra_instructions.strip()
    # 原子地占住 op：两个请求同时到达时只有一个能成功
    claimed = db.execute(
        update(Meeting)
        .where(
            Meeting.id == meeting_id,
            Meeting.op.is_(None),
            Meeting.status == "done",
            Meeting.deleted_at.is_(None),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "这场会议正在处理其他操作，请稍后再试")
    db.commit()
    db.refresh(m)
    ctx.meetings.request_op(meeting_id, op, params)
    ctx.meetings.publish(meeting_id)
    return ctx.meetings.meeting_out(m)
