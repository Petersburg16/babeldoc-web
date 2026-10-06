"""上传文件的公共小工具：翻译任务和会议记录都用。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import IO

from fastapi import HTTPException

MAX_FILES_PER_UPLOAD = 10  # 翻译一次最多上传几个文件
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_filename(raw: str | None) -> str:
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = _CONTROL.sub("", name).strip()
    return name[:200] or "document.pdf"


def copy_limited(src: IO[bytes], dst: Path, limit: int, name: str) -> int:
    size = 0
    with dst.open("wb") as out:
        while chunk := src.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"{name} 超过单文件大小上限 {limit // 1024 // 1024} MB")
            out.write(chunk)
    if size == 0:
        raise HTTPException(400, f"{name} 是空文件")
    return size
