from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from ..deps import current_user_id

router = APIRouter(tags=["events"])

HEARTBEAT_SECONDS = 15


@router.get("/api/events", response_class=EventSourceResponse)
async def events(request: Request, user_id: Annotated[int, Depends(current_user_id)]) -> AsyncIterable[ServerSentEvent]:
    bus = request.app.state.ctx.bus
    queue = bus.subscribe(user_id)
    try:
        yield ServerSentEvent(event="hello", data={"ok": True}, retry=3000)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                yield ServerSentEvent(comment="ping")
                continue
            yield ServerSentEvent(event=event["type"], data=event)
    finally:
        bus.unsubscribe(user_id, queue)
