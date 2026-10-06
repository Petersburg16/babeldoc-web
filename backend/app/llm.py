"""异步的 OpenAI 兼容大模型客户端（会议记录的整理、纪要、对话用；翻译走引擎子进程，不用这里）。

请求参数全部来自 LlmConfig（由 meeting/llm_config.py 按整理方案生成）：思考强度、温度、Top-P、最大输出
都是没配置就不发；只有模型开了 JSON 模式且调用方需要时才要求 JSON 输出。
HTTP 客户端由外部传入（会议管理器持有，测试时注入假的传输层）。

重试的红线：请求可能已经被对方收下并计费时不重发。只有连不上（ConnectError / ConnectTimeout / PoolTimeout）
和 429、5xx 才重试；读超时、流式输出开始之后的错误都直接交给调用方。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx

from .openai_compat import auth_headers, endpoint, error_text, failure_text

log = logging.getLogger("bdw.llm")

RETRIES = 3
DEFAULT_TIMEOUT = 300.0
RESERVED = frozenset({"model", "messages", "stream", "stream_options"})
# 只有放在回复最开头的 <think> 块才当作思考内容剥掉；正文中间出现的同名标签可能是原文
_THINK = re.compile(r"^\s*<(think|thinking|thought|reasoning)>(.*?)(?:</\1>|\Z)", re.S | re.I)
_CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)

_limiters: dict[tuple[Any, ...], asyncio.Semaphore] = {}


class LlmError(Exception):
    """retryable：重试几次后仍连不上或被限流（调用方据此判断要不要放弃后面的请求）；
    no_retry：同样的请求再发一遍也是同样结果，不要在本地重试（例如思考把输出额度用完了）。"""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status: int | None = None,
        no_retry: bool = False,
        tokens: int = 0,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.no_retry = no_retry
        self.tokens = tokens  # 出错前对方已经计费的 token（例如思考用完了输出额度）

    @property
    def fatal(self) -> bool:
        """密钥无效、模型不存在、重试后仍连不上：后面的请求也会一样失败，没必要接着发。"""
        if self.status in (401, 403, 404):
            return True
        return self.status is None and self.retryable


@dataclass(frozen=True)
class LlmConfig:
    model_id: int  # 会议模型（meeting_llm_models）的 id，限流和“致命错误后跳过同一模型”按它区分
    base_url: str
    api_key: str
    model: str
    json_mode: bool = False
    qps: int = 3
    # 限流的命名空间：对话和后台整理分开排队
    namespace: str = "meeting"
    effort: str | None = None  # 已落到模型档位表上的 reasoning_effort；None 不发
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    max_tokens_field: str = "max_tokens"
    extra: dict[str, Any] = field(default_factory=dict)  # 自定义参数，最后合入请求体
    timeout: float | None = None  # 多久没收到数据算超时
    context_chars: int | None = None  # 覆盖系统设置的上下文预算
    label: str = ""  # 例如“精细·生成纪要”，用于日志


@dataclass
class ChatResult:
    text: str
    tokens: int
    finish_reason: str | None
    reasoning_tokens: int = 0


@dataclass
class StreamInfo:
    """流式调用结束后由 stream() 填写。"""

    tokens: int = 0
    reasoning_tokens: int = 0
    finish_reason: str | None = None


def strip_think(text: str) -> str:
    match = _THINK.match(text)
    return text[match.end() :].lstrip() if match else text


def _limiter(cfg: LlmConfig) -> asyncio.Semaphore:
    """同一个模型同时进行的请求数不超过它的 QPS 设置（粗略近似）。改了 QPS 换一个新的信号量，不用重启。"""
    # 信号量绑定事件循环，测试里每个应用一个循环
    key = (id(asyncio.get_running_loop()), cfg.namespace, cfg.model_id, cfg.qps)
    sem = _limiters.get(key)
    if sem is None:
        sem = _limiters[key] = asyncio.Semaphore(min(cfg.qps, 8))
    return sem


def _usage(data: dict[str, Any]) -> tuple[int, int]:
    usage = data.get("usage") or {}
    details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    return int(usage.get("total_tokens") or 0), int(details.get("reasoning_tokens") or 0)


def _check_length(text: str, finish_reason: str | None, tokens: int) -> None:
    if finish_reason == "length" and not text.strip():
        raise LlmError(
            "大模型的输出额度在思考阶段就用完了，没有给出正文：请调高这个用途的“最大输出”或降低思考强度",
            no_retry=True,
            tokens=tokens,
        )


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    """接口报错时：429、5xx 还能重试就返回要等的秒数，否则（4xx、次数用完）抛 LlmError。"""
    status = resp.status_code
    retryable = status == 429 or status >= 500
    if not retryable or attempt >= RETRIES:
        raise LlmError(f"大模型接口返回 {status}：{error_text(resp)}", retryable=retryable, status=status)
    return 2**attempt * (3 if status == 429 else 1)


def _whole_reply(resp: httpx.Response) -> ChatResult:
    """解析非流式的整段回复（思考块还没去掉）。缺结束原因时保持 None：调用方只判断是不是 "length"。"""
    try:
        data = resp.json()
    except ValueError as e:
        raise LlmError("大模型返回的内容不是 OpenAI 兼容格式") from e
    if isinstance(data, dict) and data.get("error"):
        raise LlmError(f"大模型返回错误：{error_text(resp)}")
    try:
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
    except (KeyError, IndexError, TypeError) as e:
        raise LlmError("大模型返回的内容不是 OpenAI 兼容格式") from e
    tokens, reasoning = _usage(data)
    return ChatResult(
        text=str(text), tokens=tokens, finish_reason=choice.get("finish_reason"), reasoning_tokens=reasoning
    )


class LlmClient:
    def __init__(self, cfg: LlmConfig, http: httpx.AsyncClient):
        self.cfg = cfg
        self.http = http

    def payload(self, messages: list[dict[str, str]], *, json_object: bool = False, stream: bool = False) -> dict:
        cfg = self.cfg
        payload: dict[str, Any] = {"model": cfg.model, "messages": messages}
        if cfg.effort:
            payload["reasoning_effort"] = cfg.effort
        if cfg.temperature is not None:
            payload["temperature"] = cfg.temperature
        if cfg.top_p is not None:
            payload["top_p"] = cfg.top_p
        if cfg.max_tokens:
            payload[cfg.max_tokens_field] = cfg.max_tokens
        if json_object and cfg.json_mode:
            payload["response_format"] = {"type": "json_object"}
        for key, value in cfg.extra.items():
            if key not in RESERVED:
                payload[key] = value
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _timeout(self, timeout: float | None) -> httpx.Timeout:
        read = timeout or self.cfg.timeout or DEFAULT_TIMEOUT
        return httpx.Timeout(connect=15.0, read=read, write=60.0, pool=30.0)

    @asynccontextmanager
    async def _send(
        self, payload: dict[str, Any], limit: httpx.Timeout, *, waiting: str
    ) -> AsyncIterator[httpx.Response]:
        """发出请求，交出状态正常、正文还没读的回复，调用方在 async with 里读正文；chat() 和 stream() 共用。

        请求和读正文时 httpx 的错误都在这里转成 LlmError。重试只发生在交出回复之前：连不上（对方没收到），
        或者对方回了 429、5xx。读超时、交出回复之后的任何错误都不重试：请求多半已经在计费。
        waiting 是读超时报错里的说法，例如“没有回复完”。
        """
        url = endpoint(self.cfg.base_url, "/chat/completions")
        attempt = 0
        while True:
            attempt += 1
            opened = False
            try:
                async with (
                    _limiter(self.cfg),
                    self.http.stream(
                        "POST", url, json=payload, headers=auth_headers(self.cfg.api_key), timeout=limit
                    ) as resp,
                ):
                    if resp.status_code < 400:
                        opened = True
                        yield resp
                        return
                    await resp.aread()
                    delay = _retry_delay(resp, attempt)
            except httpx.HTTPError as e:
                connect = not opened and isinstance(e, _CONNECT_ERRORS)
                if connect and attempt < RETRIES:
                    await asyncio.sleep(2**attempt)
                    continue
                if connect:
                    raise LlmError(failure_text("连接大模型失败", e), retryable=True) from e
                if isinstance(e, httpx.TimeoutException):
                    # 请求已经发出：对方多半还在思考或生成，这里重发只会重复计费、长时间占着大模型锁；交给调用方决定
                    raise LlmError(f"大模型在 {int(limit.read or 0)} 秒内{waiting}：{e.__class__.__name__}") from e
                raise LlmError(failure_text("大模型连接中断", e)) from e
            await asyncio.sleep(delay)

    async def chat(
        self, messages: list[dict[str, str]], *, json_object: bool = False, timeout: float | None = None
    ) -> ChatResult:
        """一次完整回复（非流式）。连不上、429、5xx 自动重试，其余错误直接抛 LlmError。"""
        payload = self.payload(messages, json_object=json_object)
        async with self._send(payload, self._timeout(timeout), waiting="没有回复完") as resp:
            await resp.aread()
        result = _whole_reply(resp)
        result.text = strip_think(result.text)
        _check_length(result.text, result.finish_reason, result.tokens)
        if result.reasoning_tokens:
            log.info(
                "%s: %d tokens (%d reasoning)", self.cfg.label or self.cfg.model, result.tokens, result.reasoning_tokens
            )
        return result

    async def collect(
        self, messages: list[dict[str, str]], *, json_object: bool = False, timeout: float | None = None
    ) -> ChatResult:
        """用流式请求取完整回复：长输出只要求两段之间不超时，同时拿到用量和结束原因。"""
        info = StreamInfo()
        parts = [text async for text in self.stream(messages, info=info, json_object=json_object, timeout=timeout)]
        text = strip_think("".join(parts))
        _check_length(text, info.finish_reason, info.tokens)
        if info.reasoning_tokens:
            log.info(
                "%s: %d tokens (%d reasoning)", self.cfg.label or self.cfg.model, info.tokens, info.reasoning_tokens
            )
        return ChatResult(
            text=text, tokens=info.tokens, finish_reason=info.finish_reason, reasoning_tokens=info.reasoning_tokens
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        info: StreamInfo | None = None,
        json_object: bool = False,
        timeout: float | None = None,
    ) -> AsyncIterator[str]:
        """流式回复，逐段产出正文（思考内容不产出）。结束后把用量和结束原因写进 info。

        只有连不上、429、5xx 才重试，而且只在开始输出前；读超时不重试（中转要等思考完才发第一个字节，
        超时时请求多半已经在计费）。连接在 [DONE] 和结束原因都没到时就断开，按回复不完整报错。
        """
        info = info if info is not None else StreamInfo()
        payload = self.payload(messages, json_object=json_object, stream=True)
        async with self._send(payload, self._timeout(timeout), waiting="没有新的输出") as resp:
            if "text/event-stream" not in resp.headers.get("content-type", ""):
                # 中转不支持流式时会直接返回整段回复
                await resp.aread()
                whole = _whole_reply(resp)
                info.tokens, info.reasoning_tokens, info.finish_reason = (
                    whole.tokens,
                    whole.reasoning_tokens,
                    whole.finish_reason,
                )
                if whole.text:
                    yield whole.text
                return
            done = False
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    done = True
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("error"):
                    err = event["error"]
                    message = err.get("message") if isinstance(err, dict) else err
                    raise LlmError(f"大模型返回错误：{str(message)[:300]}")
                if event.get("usage"):
                    # 用量单独一块，choices 为空
                    info.tokens, info.reasoning_tokens = _usage(event)
                for choice in event.get("choices") or []:
                    if not isinstance(choice, dict):
                        continue
                    if choice.get("finish_reason"):
                        info.finish_reason = choice["finish_reason"]
                    text = (choice.get("delta") or {}).get("content")
                    if text:
                        yield str(text)
            if not done and info.finish_reason is None:
                raise LlmError("大模型的回复没有正常结束（连接提前断开），内容可能不完整")
