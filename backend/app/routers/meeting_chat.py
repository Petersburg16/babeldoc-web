"""会议记录的对话：基于逐字稿和纪要问答，流式返回。

POST /chat 返回 text/event-stream：问题存好后立刻发 start，没有新内容时每 10 秒一行注释心跳（防代理超时断开），
然后逐段发 delta，回答完整存库后发 done；出错发 error。
长连接期间不占数据库会话（不用 DbDep）：开头用短会话读上下文、存问题，结尾另开会话存回答。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select, update

from ..deps import AppContext, CtxDep, DbDep, UserDep, current_user_id
from ..llm import LlmClient, LlmConfig, LlmError, StreamInfo
from ..meeting import chat
from ..meeting.llm_config import resolve_meeting_llm
from ..meeting.schemas import MessageOut
from ..models import Meeting, MeetingMessage, MeetingSegment
from ..settings_store import load_settings
from .meetings import own_meeting

router = APIRouter(prefix="/api/meetings", tags=["meetings"])
log = logging.getLogger("bdw.meetings.chat")

HEARTBEAT_SECONDS = 10.0
# 从库里取最近多少条消息来挑历史：10 轮问答，再给没有回答的问题留些余量
HISTORY_LOAD = chat.HISTORY_ROUNDS * 2 + 20

SaveAnswer = Callable[[str, int], Awaitable[MessageOut | None]]


class ChatIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def _content(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("问题不能为空")
        return v


@dataclass
class Prepared:
    user_message: MessageOut
    context: chat.ChatContext
    llm: LlmConfig


@router.get("/{meeting_id}/messages")
def list_messages(meeting_id: str, user: UserDep, db: DbDep) -> list[MessageOut]:
    own_meeting(db, user, meeting_id)
    rows = db.scalars(
        select(MeetingMessage).where(MeetingMessage.meeting_id == meeting_id).order_by(MeetingMessage.id)
    ).all()
    return [MessageOut.of(m) for m in rows]


@router.delete("/{meeting_id}/messages", status_code=204)
def clear_messages(meeting_id: str, user: UserDep, db: DbDep) -> None:
    own_meeting(db, user, meeting_id)
    db.execute(delete(MeetingMessage).where(MeetingMessage.meeting_id == meeting_id))
    db.commit()


@router.post("/{meeting_id}/chat")
async def chat_stream(
    meeting_id: str,
    body: ChatIn,
    user_id: Annotated[int, Depends(current_user_id)],
    ctx: CtxDep,
) -> StreamingResponse:
    http = ctx.meetings.http
    if http is None:
        raise HTTPException(503, "服务还在启动，请稍后再试")
    # 校验和存问题都在流开始前做完：出错时前端拿到的是普通的 JSON 错误响应
    prepared = await asyncio.to_thread(prepare, ctx, user_id, meeting_id, body.content)
    save: SaveAnswer = partial(_save_async, ctx, meeting_id, prepared.user_message.id)
    return StreamingResponse(
        sse_events(LlmClient(prepared.llm, http), prepared.context, prepared.user_message, save),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache, no-transform"},
    )


def prepare(ctx: AppContext, user_id: int, meeting_id: str, question: str) -> Prepared:
    with ctx.Session() as db:
        m = db.get(Meeting, meeting_id)
        if m is None or m.deleted_at is not None or m.user_id != user_id:
            raise HTTPException(404, "会议不存在")
        rows = db.execute(
            select(MeetingSegment.start_ms, MeetingSegment.speaker, MeetingSegment.text)
            .where(MeetingSegment.meeting_id == meeting_id)
            .order_by(MeetingSegment.idx)
        ).all()
        if not rows:
            raise HTTPException(409, "这场会议还没有逐字稿，识别完成后才能提问")
        llm, reason = resolve_meeting_llm(db, ctx.secrets, m.llm_preset_id, "chat")
        if llm is None:
            raise HTTPException(400, reason or "管理员还没有配置可用的大模型")
        recent = db.scalars(
            select(MeetingMessage)
            .where(MeetingMessage.meeting_id == meeting_id)
            .order_by(MeetingMessage.id.desc())
            .limit(HISTORY_LOAD)
        ).all()
        history = [(r.role, r.content) for r in reversed(recent)]
        budget = llm.context_chars or load_settings(db).meeting_context_chars
        info: dict[str, Any] = {
            "title": m.title,
            "duration_ms": m.duration_ms or 0,
            "speakers": dict(m.speakers or {}),
            "minutes_md": m.minutes_md,
            "minutes_stale": bool(m.minutes_md) and m.minutes_rev is not None and m.minutes_rev != m.transcript_rev,
        }
        message = MeetingMessage(meeting_id=meeting_id, role="user", content=question)
        db.add(message)
        db.commit()
        user_message = MessageOut.of(message)
    lines = [chat.Line(start_ms=r.start_ms, speaker=r.speaker, text=r.text) for r in rows]
    context = chat.build_context(**info, lines=lines, history=history, question=question, budget=budget)
    return Prepared(user_message=user_message, context=context, llm=llm)


def save_answer(ctx: AppContext, meeting_id: str, user_message_id: int, answer: str, tokens: int) -> MessageOut | None:
    """存回答并累加用量。会议被删或对话在回答期间被清空时不存，返回 None。"""
    with ctx.Session() as db:
        m = db.get(Meeting, meeting_id)
        if m is None or m.deleted_at is not None or db.get(MeetingMessage, user_message_id) is None:
            return None
        message = MeetingMessage(meeting_id=meeting_id, role="assistant", content=answer, tokens=tokens)
        db.add(message)
        # 用 SQL 自增：后台整理步骤可能同时在累加同一场会议的用量
        db.execute(
            update(Meeting).where(Meeting.id == meeting_id).values(tokens=Meeting.tokens + tokens),
            execution_options={"synchronize_session": False},
        )
        db.commit()
        out = MessageOut.of(message)
    ctx.meetings.publish(meeting_id)
    return out


async def _save_async(
    ctx: AppContext, meeting_id: str, user_message_id: int, answer: str, tokens: int
) -> MessageOut | None:
    return await asyncio.to_thread(save_answer, ctx, meeting_id, user_message_id, answer, tokens)


def _event(name: str, data: dict[str, Any]) -> str:
    # json.dumps 会把换行转义成 \n，一个事件只占一行 data
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _produce(
    client: LlmClient, messages: list[dict[str, str]], queue: asyncio.Queue[tuple[str, str]], info: StreamInfo
) -> None:
    """在独立的任务里向大模型取数，结果放进队列；这样等待时能发心跳，断开时取消这个任务就停止取数。"""
    try:
        async for text in client.stream(messages, info=info):
            queue.put_nowait(("delta", text))
    except LlmError as e:
        queue.put_nowait(("error", str(e)))
    except Exception as e:
        log.exception("meeting chat stream failed")
        queue.put_nowait(("error", f"内部错误：{e.__class__.__name__}"))
    else:
        queue.put_nowait(("end", ""))


async def sse_events(
    client: LlmClient, context: chat.ChatContext, user_message: MessageOut, save: SaveAnswer
) -> AsyncIterator[str]:
    queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
    info = StreamInfo()
    producer = asyncio.create_task(_produce(client, context.messages, queue, info), name="bdw-meeting-chat")
    parts: list[str] = []
    try:
        yield _event("start", {"user_message": user_message.model_dump(mode="json"), "excerpt": context.excerpt})
        while True:
            try:
                kind, value = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if kind == "delta":
                parts.append(value)
                yield _event("delta", {"text": value})
            elif kind == "error":
                yield _event("error", {"message": value})
                return
            else:
                break
        answer = "".join(parts).strip()
        if not answer:
            yield _event("error", {"message": "大模型没有返回内容，请重试"})
            return
        # 中转报了用量就用真实数（含思考 token），否则按字数估算
        saved = await save(answer, info.tokens or chat.estimate_tokens(context.messages, answer))
        if saved is None:
            yield _event("error", {"message": "会议或对话记录已被删除，这条回答没有保存"})
            return
        yield _event("done", {"message": saved.model_dump(mode="json")})
    finally:
        # 客户端断开时这里处在被取消的作用域里，不能再 await；producer 是独立任务，取消后会自己关掉连接
        producer.cancel()
