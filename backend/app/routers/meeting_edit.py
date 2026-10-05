"""会议记录的编辑与重跑：改逐字稿、改名与合并说话人、重新整理、重新生成纪要（见 M4）。"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/meetings", tags=["meetings"])
