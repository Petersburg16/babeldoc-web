"""会议大模型步骤共用的小工具：记账。错误是否值得继续看 LlmError.fatal。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..llm import ChatResult, LlmClient, LlmError
from ..models import Meeting

if TYPE_CHECKING:
    from .manager import MeetingManager


def increment_meeting_tokens(db: Session, meeting_id: str, tokens: int) -> None:
    """用 SQL 原子加会议用量，只执行不提交：几块整理并发完成、对话和后台整理同时累加时不会互相覆盖。"""
    db.execute(
        update(Meeting).where(Meeting.id == meeting_id).values(tokens=Meeting.tokens + tokens),
        execution_options={"synchronize_session": False},
    )


def add_tokens(manager: MeetingManager, meeting_id: str, tokens: int) -> None:
    if tokens <= 0:
        return
    with manager.Session() as db:
        increment_meeting_tokens(db, meeting_id, tokens)
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
