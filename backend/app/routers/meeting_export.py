"""会议记录导出：Word、Markdown、纯文本、字幕（见 M7）。"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/meetings", tags=["meetings"])
