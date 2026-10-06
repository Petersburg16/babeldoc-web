"""会议记录导出：Word、Markdown、纯文本、字幕。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select

from ..deps import CtxDep, current_user_id
from ..meeting.export import (
    MEDIA_TYPES,
    ExportContent,
    ExportData,
    ExportFormat,
    ExportSegment,
    NothingToExport,
    content_disposition,
    file_names,
    render,
    xml_safe,
)
from ..models import MeetingSegment
from .meetings import own_meeting

router = APIRouter(prefix="/api/meetings", tags=["meetings"])


@router.get("/{meeting_id}/export")
def export_meeting(
    meeting_id: str,
    user_id: Annotated[int, Depends(current_user_id)],
    ctx: CtxDep,
    fmt: Annotated[ExportFormat, Query(alias="format")],
    content: ExportContent = "both",
) -> Response:
    # 不用 UserDep/DbDep：先把数据读成普通对象再关会话，几千句的 Word 要渲染一两秒，不该一直占着 SQLite 的读事务
    with ctx.Session() as db:
        m = own_meeting(db, user_id, meeting_id)
        rows = db.scalars(
            select(MeetingSegment).where(MeetingSegment.meeting_id == meeting_id).order_by(MeetingSegment.idx)
        ).all()
        data = ExportData(
            title=xml_safe(m.title).strip() or "会议记录",
            created_at=m.created_at,
            duration_ms=m.duration_ms,
            provider_name=m.provider_name,
            speakers={
                k: {**v, "name": xml_safe(str(v.get("name") or ""))} if isinstance(v, dict) else v
                for k, v in (m.speakers or {}).items()
            },
            minutes_md=xml_safe(m.minutes_md) if m.minutes_md else m.minutes_md,
            segments=[ExportSegment(s.start_ms, s.end_ms, s.speaker, xml_safe(s.text)) for s in rows],
        )
    try:
        body = render(data, fmt, content)
    except NothingToExport as e:
        raise HTTPException(409, str(e)) from None
    name, fallback = file_names(data.title, fmt, content)
    return Response(
        content=body,
        media_type=MEDIA_TYPES[fmt],
        headers={"content-disposition": content_disposition(name, fallback)},
    )
