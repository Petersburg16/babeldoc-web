"""会议大模型步骤共用的小工具：记账、判断错误是否值得继续。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import update

from ..llm import ChatResult, LlmClient, LlmError
from ..models import Meeting

if TYPE_CHECKING:
    from .manager import MeetingManager


def add_tokens(manager: MeetingManager, meeting_id: str, tokens: int) -> None:
    if tokens <= 0:
        return
    # 原子加：几块整理并发完成时不会互相覆盖
    with manager.Session() as db:
        db.execute(update(Meeting).where(Meeting.id == meeting_id).values(tokens=Meeting.tokens + tokens))
        db.commit()


async def ask(
    manager: MeetingManager, meeting_id: str, client: LlmClient, messages: list[dict[str, str]], **kwargs: Any
) -> ChatResult:
    try:
        result = await client.chat(messages, **kwargs)
    except LlmError as e:
        add_tokens(manager, meeting_id, e.tokens)  # 失败了也可能已经计费
        raise
    add_tokens(manager, meeting_id, result.tokens)
    return result


def is_fatal(error: LlmError) -> bool:
    """密钥无效、模型不存在、重试后仍连不上：后面的请求也会一样失败，没必要接着发。"""
    if error.status in (401, 403, 404):
        return True
    return error.status is None and error.retryable
