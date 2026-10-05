"""生成会议纪要（占位实现，见 M4/M5 的纪要部分）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..llm import LlmClient
    from .manager import MeetingManager


async def generate_minutes(
    manager: MeetingManager,
    meeting_id: str,
    client: LlmClient,
    *,
    template: str | None = None,
    extra: str | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> str | None:
    """按模板生成纪要，写回 Meeting.minutes_md 等字段。返回给用户看的警告（没有就 None），不抛大模型错误。

    template / extra 为 None 时用会议上保存的模板和补充要求；不为 None 时先保存到会议上再生成。
    """
    return None
