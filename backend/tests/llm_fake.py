"""测试用的假大模型：拦截会议管理器发出的所有 HTTP 请求，/chat/completions 交给给定的函数回答。

用法：
    calls = install_fake_llm(app, lambda messages, payload: "回答")
    ...
    assert calls[0]["messages"][0]["role"] == "system"

reply 返回字符串即正常回答；要模拟思考 token、finish_reason="length" 等就返回 FakeReply；
抛 FakeLlmFailure(status) 模拟接口报错。流式请求（stream=true）按 SSE 分块返回：
带 stream_options.include_usage 时和 OpenAI 一样，最后一块正文带 finish_reason，
[DONE] 前再单独发一块用量（choices 为空）。

文件末尾是整理逐字稿的假回答（polish_reply 等），整理相关的测试共用。
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class FakeReply:
    text: str
    finish_reason: str = "stop"
    reasoning_tokens: int = 0
    tokens: int | None = None  # 总用量；None 时按字数估
    reasoning: str = ""  # 思考摘要（reasoning_content），不应进入正文


Reply = Callable[[list[dict[str, Any]], dict[str, Any]], str | FakeReply]


class FakeLlmFailure(Exception):
    def __init__(self, status: int = 500, message: str = "fake failure"):
        super().__init__(message)
        self.status = status
        self.message = message


def _chunks(text: str) -> list[str]:
    return [text[i : i + 7] for i in range(0, len(text), 7)] or [""]


def _data(event: dict[str, Any]) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False)


def plain_sse(text: str) -> bytes:
    """老式的流：只有正文块和 [DONE]，没有 finish_reason 和用量。"""
    lines = [_data({"choices": [{"delta": {"content": c}, "index": 0}]}) for c in _chunks(text)]
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode("utf-8")


def _usage(reply: FakeReply, payload: dict[str, Any]) -> dict[str, Any]:
    prompt = sum(len(str(m.get("content", ""))) for m in payload.get("messages", []))
    total = reply.tokens if reply.tokens is not None else len(reply.text) + prompt + reply.reasoning_tokens
    return {
        "prompt_tokens": prompt,
        "completion_tokens": max(0, total - prompt),
        "total_tokens": total,
        "completion_tokens_details": {"reasoning_tokens": reply.reasoning_tokens},
    }


def _sse_with_usage(reply: FakeReply, payload: dict[str, Any]) -> bytes:
    lines = []
    if reply.reasoning:
        lines.append(_data({"choices": [{"delta": {"reasoning_content": reply.reasoning}, "index": 0}]}))
    chunks = _chunks(reply.text)
    for i, c in enumerate(chunks):
        finish = reply.finish_reason if i == len(chunks) - 1 else None
        lines.append(_data({"choices": [{"delta": {"content": c}, "index": 0, "finish_reason": finish}]}))
    lines.append(_data({"choices": [], "usage": _usage(reply, payload)}))
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode("utf-8")


def install_transport(app, handler: Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]) -> None:
    """把会议管理器发请求用的传输层换成 handler（可以是 async 函数）；要看请求头、模拟 /models 等接口、
    模拟识别服务时直接用这个。测试里替换 manager.http 一律走这里。"""
    transport = httpx.MockTransport(handler)
    manager = app.state.ctx.meetings
    manager.transport = transport
    if manager.http is not None:  # 应用已经启动：换掉正在用的客户端
        manager.http = httpx.AsyncClient(transport=transport)


def install_fake_llm(app, reply: Reply) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/chat/completions"):
            return httpx.Response(404, json={"error": {"message": "not found"}})
        payload = json.loads(request.content or b"{}")
        calls.append(payload)
        try:
            answer = reply(payload.get("messages") or [], payload)
        except FakeLlmFailure as e:
            return httpx.Response(e.status, json={"error": {"message": e.message}})
        full = answer if isinstance(answer, FakeReply) else FakeReply(str(answer))
        if payload.get("stream"):
            if (payload.get("stream_options") or {}).get("include_usage"):
                content = _sse_with_usage(full, payload)
            else:
                content = plain_sse(full.text)
            return httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})
        message: dict[str, Any] = {"role": "assistant", "content": full.text}
        if full.reasoning:
            message["reasoning_content"] = full.reasoning
        return httpx.Response(
            200,
            json={
                "choices": [{"message": message, "finish_reason": full.finish_reason}],
                "usage": _usage(full, payload),
            },
        )

    install_transport(app, handler)
    return calls


# ---------- 整理逐字稿的假回答 ----------

FILLERS = ("嗯", "呃", "那个", "就是说")
TRANSCRIPT_LINE = re.compile(r"^#(\d+) \[(S\d+)\] (.*)$")


def tidy(text: str) -> str:
    for filler in FILLERS:
        text = text.replace(filler, "")
    text = text.strip("，, ")
    return text if text.endswith(("。", "？", "！")) else text + "。"


def numbered_lines(messages: list[dict[str, Any]]) -> list[tuple[int, str, str]]:
    """从整理请求的 <transcript> 块里读出 [(序号, 说话人, 原文)]。"""
    user = messages[-1]["content"]
    body = user.split("<transcript>")[1].split("</transcript>")[0]
    out = []
    for line in body.strip().splitlines():
        m = TRANSCRIPT_LINE.match(line)
        assert m, line
        out.append((int(m.group(1)), m.group(2), m.group(3)))
    return out


def polish_reply(messages: list[dict[str, Any]], transform=tidy) -> str:
    """整理请求的标准回答：每句按 transform 整理，编号照抄。"""
    return "\n".join(f"#{idx} {transform(text)}" for idx, _, text in numbered_lines(messages))
