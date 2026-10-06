from __future__ import annotations

import asyncio
import contextlib
import threading
from collections import defaultdict
from typing import Any


class EventBus:
    """按用户分发的进程内事件总线，供 SSE 推送翻译任务与会议记录的事件。publish 可在任意线程调用。"""

    def __init__(self, queue_size: int = 200):
        self._subs: dict[int, set[asyncio.Queue]] = defaultdict(set)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None
        self._queue_size = queue_size

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._loop_thread = threading.get_ident()

    def subscribe(self, user_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subs[user_id].add(q)
        return q

    def unsubscribe(self, user_id: int, q: asyncio.Queue) -> None:
        subs = self._subs.get(user_id)
        if subs:
            subs.discard(q)
            if not subs:
                self._subs.pop(user_id, None)

    def subscriber_count(self) -> int:
        return sum(len(s) for s in self._subs.values())

    def publish(self, user_id: int, event: dict[str, Any]) -> None:
        if self._loop is None:
            return
        if threading.get_ident() == self._loop_thread:
            self._deliver(user_id, event)
        elif not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._deliver, user_id, event)

    def _deliver(self, user_id: int, event: dict[str, Any]) -> None:
        for q in list(self._subs.get(user_id, ())):
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
            q.put_nowait(event)
