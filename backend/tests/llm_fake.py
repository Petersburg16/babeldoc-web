"""测试用的假大模型：拦截会议管理器发出的所有 HTTP 请求，/chat/completions 交给给定的函数回答。

用法：
    calls = install_fake_llm(app, lambda messages, payload: "回答")
    ...
    assert calls[0]["messages"][0]["role"] == "system"

reply 返回字符串即正常回答；抛 FakeLlmFailure(status) 模拟接口报错。流式请求（stream=true）按 SSE 分块返回。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

Reply = Callable[[list[dict[str, Any]], dict[str, Any]], str]


class FakeLlmFailure(Exception):
    def __init__(self, status: int = 500, message: str = "fake failure"):
        super().__init__(message)
        self.status = status
        self.message = message


def _sse(text: str) -> bytes:
    chunks = [text[i : i + 7] for i in range(0, len(text), 7)] or [""]
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": c}, "index": 0}]}, ensure_ascii=False) for c in chunks
    ]
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode("utf-8")


def install_fake_llm(app, reply: Reply) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/chat/completions"):
            return httpx.Response(404, json={"error": {"message": "not found"}})
        payload = json.loads(request.content or b"{}")
        calls.append(payload)
        try:
            text = reply(payload.get("messages") or [], payload)
        except FakeLlmFailure as e:
            return httpx.Response(e.status, json={"error": {"message": e.message}})
        if payload.get("stream"):
            return httpx.Response(200, content=_sse(text), headers={"content-type": "text/event-stream"})
        usage = {"total_tokens": len(text) + sum(len(str(m.get("content", ""))) for m in payload.get("messages", []))}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": usage,
            },
        )

    transport = httpx.MockTransport(handler)
    manager = app.state.ctx.meetings
    manager.transport = transport
    if manager.http is not None:  # 应用已经启动：换掉正在用的客户端
        manager.http = httpx.AsyncClient(transport=transport)
    return calls
