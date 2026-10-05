"""会议记录的对话：基于逐字稿和纪要问答，流式返回（见 M5）。"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/meetings", tags=["meetings"])
