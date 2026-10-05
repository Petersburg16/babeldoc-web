"""异步的 OpenAI 兼容大模型客户端（会议记录的整理、纪要、对话用；翻译走引擎子进程，不用这里）。

与翻译一致：不发思考/推理参数；temperature 按模型配置决定是否发送；只有模型开了 JSON 模式才要求 JSON 输出。
HTTP 客户端由外部传入（会议管理器持有，测试时注入假的传输层）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ModelProfile
from .security import SecretBox

log = logging.getLogger("bdw.llm")

DEFAULT_BASE_URL = "https://api.openai.com/v1"
RETRIES = 3

_limiters: dict[tuple[int, int], asyncio.Semaphore] = {}


class LlmError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


@dataclass(frozen=True)
class LlmConfig:
    profile_id: int
    name: str
    base_url: str
    api_key: str
    model: str
    send_temperature: bool
    json_mode: bool
    qps: int


@dataclass
class ChatResult:
    text: str
    tokens: int
    finish_reason: str | None


def resolve_model(db: Session, box: SecretBox, model_id: int | None) -> LlmConfig | None:
    """优先用指定的模型；它被删除或停用时退回默认模型；都没有返回 None。"""
    enabled = select(ModelProfile).where(ModelProfile.enabled.is_(True))
    profile = db.scalar(enabled.where(ModelProfile.id == model_id)) if model_id is not None else None
    profile = profile or db.scalar(enabled.where(ModelProfile.is_default.is_(True)))
    profile = profile or db.scalar(enabled.order_by(ModelProfile.sort_order, ModelProfile.id))
    if profile is None:
        return None
    return LlmConfig(
        profile_id=profile.id,
        name=profile.name,
        base_url=(profile.base_url or DEFAULT_BASE_URL).rstrip("/"),
        api_key=box.decrypt(profile.api_key_enc),
        model=profile.model,
        send_temperature=profile.send_temperature,
        json_mode=profile.json_mode,
        qps=max(1, profile.qps),
    )


def _limiter(cfg: LlmConfig) -> asyncio.Semaphore:
    """同一个模型同时进行的请求数不超过它的 QPS 设置（粗略近似）。"""
    key = (id(asyncio.get_running_loop()), cfg.profile_id)  # 信号量绑定事件循环，测试里每个应用一个循环
    sem = _limiters.get(key)
    if sem is None:
        sem = _limiters[key] = asyncio.Semaphore(min(cfg.qps, 8))
    return sem


def _error_text(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300] or resp.reason_phrase
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)[:300]
    return str(err or data)[:300]


class LlmClient:
    def __init__(self, cfg: LlmConfig, http: httpx.AsyncClient):
        self.cfg = cfg
        self.http = http

    def _payload(
        self, messages: list[dict[str, str]], temperature: float, max_tokens: int | None, json_object: bool
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.cfg.model, "messages": messages}
        if self.cfg.send_temperature:
            payload["temperature"] = temperature
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if json_object and self.cfg.json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_object: bool = False,
        timeout: float = 300,
    ) -> ChatResult:
        """一次完整回复。网络错误、429、5xx 自动重试，其余错误直接抛 LlmError。"""
        payload = self._payload(messages, temperature, max_tokens, json_object)
        url = f"{self.cfg.base_url}/chat/completions"
        attempt = 0
        while True:
            attempt += 1
            try:
                async with _limiter(self.cfg):
                    resp = await self.http.post(url, json=payload, headers=self._headers(), timeout=timeout)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
                if attempt >= RETRIES:
                    raise LlmError(f"连接大模型失败：{e.__class__.__name__}: {e}"[:300], retryable=True) from e
                await asyncio.sleep(2**attempt)
                continue
            except httpx.TimeoutException as e:
                # 请求已经发出：对方多半还在生成，这里重发只会重复计费、长时间占着大模型锁；交给调用方决定
                raise LlmError(f"大模型在 {int(timeout)} 秒内没有回复完：{e.__class__.__name__}") from e
            except httpx.HTTPError as e:
                raise LlmError(f"大模型连接中断：{e.__class__.__name__}: {e}"[:300]) from e
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= RETRIES:
                    raise LlmError(
                        f"大模型接口返回 {resp.status_code}：{_error_text(resp)}",
                        retryable=True,
                        status=resp.status_code,
                    )
                await asyncio.sleep(2**attempt * (3 if resp.status_code == 429 else 1))
                continue
            if resp.status_code >= 400:
                raise LlmError(f"大模型接口返回 {resp.status_code}：{_error_text(resp)}", status=resp.status_code)
            try:
                data = resp.json()
                choice = data["choices"][0]
                text = choice["message"].get("content") or ""
            except (ValueError, KeyError, IndexError, TypeError) as e:
                raise LlmError("大模型返回的内容不是 OpenAI 兼容格式") from e
            usage = data.get("usage") or {}
            return ChatResult(
                text=str(text),
                tokens=int(usage.get("total_tokens") or 0),
                finish_reason=choice.get("finish_reason"),
            )

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        timeout: float = 600,
    ) -> AsyncIterator[str]:
        """流式回复，逐段产出文本。开始输出前遇到网络错误、429、5xx 会重试；输出中途出错直接抛 LlmError。"""
        payload = {**self._payload(messages, temperature, max_tokens, False), "stream": True}
        url = f"{self.cfg.base_url}/chat/completions"
        attempt = 0
        started = False  # 已经产出过文本就不能再重试，否则内容会重复
        while True:
            attempt += 1
            try:
                async with (
                    _limiter(self.cfg),
                    self.http.stream("POST", url, json=payload, headers=self._headers(), timeout=timeout) as resp,
                ):
                    if resp.status_code == 429 or resp.status_code >= 500:
                        await resp.aread()
                        if attempt < RETRIES:
                            raise _Retry
                        raise LlmError(f"大模型接口返回 {resp.status_code}：{_error_text(resp)}", retryable=True)
                    if resp.status_code >= 400:
                        await resp.aread()
                        raise LlmError(f"大模型接口返回 {resp.status_code}：{_error_text(resp)}")
                    if "text/event-stream" not in resp.headers.get("content-type", ""):
                        # 中转不支持流式时会直接返回整段回复
                        await resp.aread()
                        try:
                            data = resp.json()
                        except ValueError as e:
                            raise LlmError("大模型返回的内容不是 OpenAI 兼容格式") from e
                        if isinstance(data, dict) and data.get("error"):
                            raise LlmError(f"大模型返回错误：{_error_text(resp)}")
                        try:
                            text = data["choices"][0]["message"].get("content") or ""
                        except (KeyError, IndexError, TypeError) as e:
                            raise LlmError("大模型返回的内容不是 OpenAI 兼容格式") from e
                        if text:
                            started = True
                            yield str(text)
                        return
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            event = json.loads(data)
                        except ValueError:
                            continue
                        if isinstance(event, dict) and event.get("error"):
                            err = event["error"]
                            message = err.get("message") if isinstance(err, dict) else err
                            raise LlmError(f"大模型返回错误：{str(message)[:300]}")
                        try:
                            delta = event["choices"][0].get("delta") or {}
                        except (KeyError, IndexError, TypeError):
                            continue
                        text = delta.get("content")
                        if text:
                            started = True
                            yield str(text)
                    return
            except _Retry:
                await asyncio.sleep(2**attempt)
            except httpx.HTTPError as e:
                if started or attempt >= RETRIES:
                    raise LlmError(f"连接大模型失败：{e.__class__.__name__}: {e}"[:300], retryable=True) from e
                await asyncio.sleep(2**attempt)


class _Retry(Exception):
    pass
