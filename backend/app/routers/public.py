"""给语音识别服务商拉取录音用的公开地址：不需要登录，靠地址里的一次性令牌鉴权。

令牌每段一个、随机 32 字节、带过期时间，会议离开“识别中”就作废；不对一律 404。
每次拉取都记日志（IP、Range、UA），排查服务商拉不到文件时用。
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ..db import utcnow
from ..deps import CtxDep, client_ip
from ..meeting.manager import PROBE_ID, parse_iso
from ..models import Meeting

router = APIRouter(tags=["public"])
log = logging.getLogger("bdw.public")

NO_STORE = {"cache-control": "private, no-store, no-transform"}


@router.api_route("/api/public/meeting-audio/{meeting_id}/{index}/{token}.mp3", methods=["GET", "HEAD"])
def meeting_audio(meeting_id: str, index: int, token: str, request: Request, ctx: CtxDep) -> FileResponse:
    ip = client_ip(request)
    log.info(
        "audio fetch meeting=%s part=%s method=%s ip=%s range=%s ua=%s",
        meeting_id,
        index,
        request.method,
        ip,
        request.headers.get("range", "-"),
        request.headers.get("user-agent", "-")[:120],
    )
    if meeting_id == PROBE_ID:
        probe = ctx.meetings.probe_file(token)
        if probe is None:
            raise HTTPException(404, "Not Found")
        probe.hits.append(ip)
        return FileResponse(probe.path, media_type="audio/mpeg", headers=NO_STORE)
    with ctx.Session() as db:
        m = db.get(Meeting, meeting_id)
        if m is None or m.deleted_at is not None or m.status != "transcribing":
            raise HTTPException(404, "Not Found")
        part = next((p for p in m.asr_parts or [] if int(p.get("index", -1)) == index), None)
    if part is None or not part.get("token") or not secrets.compare_digest(str(part["token"]), token):
        raise HTTPException(404, "Not Found")
    expires = parse_iso(part.get("token_exp"))
    if expires is None or expires < utcnow():
        raise HTTPException(404, "Not Found")
    path = ctx.meetings.meeting_dir(meeting_id) / str(part.get("file") or "")
    if not path.is_file():
        raise HTTPException(404, "Not Found")
    return FileResponse(path, media_type="audio/mpeg", headers=NO_STORE)
