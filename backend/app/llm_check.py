"""管理后台的“测试连接 / 获取模型列表”（翻译模型）。直接用 httpx 调 OpenAI 兼容接口，不经过引擎。"""

from __future__ import annotations

import time
from typing import Any

import httpx

from .openai_compat import TIMEOUT, auth_headers, endpoint, error_text, failure_text, fetch_models
from .schemas import ModelTestOut


def check_chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    send_temperature: bool,
) -> ModelTestOut:
    if not api_key:
        return ModelTestOut(ok=False, error="还没有填写 API Key")
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "user", "content": "Translate into Simplified Chinese, output translation only: Hello, world!"}
        ],
        "max_tokens": 64,
    }
    if send_temperature:
        payload["temperature"] = 0
    started = time.monotonic()
    try:
        resp = httpx.post(
            endpoint(base_url, "/chat/completions"),
            json=payload,
            headers=auth_headers(api_key),
            timeout=TIMEOUT,
        )
    except Exception as e:  # 代理配置错误等也只当作连接失败汇报，不让接口 500
        return ModelTestOut(ok=False, error=failure_text("连接失败", e))
    latency = int((time.monotonic() - started) * 1000)
    if resp.status_code >= 400:
        return ModelTestOut(ok=False, status=resp.status_code, latency_ms=latency, error=error_text(resp))
    try:
        data = resp.json()
        reply = (data["choices"][0]["message"].get("content") or "").strip()
    except (ValueError, KeyError, IndexError, TypeError):
        return ModelTestOut(ok=False, status=resp.status_code, latency_ms=latency, error="返回内容不是 OpenAI 兼容格式")
    return ModelTestOut(
        ok=True, status=resp.status_code, latency_ms=latency, reply=reply[:200], usage=data.get("usage")
    )


def http_client() -> httpx.AsyncClient:
    """拉模型列表用的一次性客户端：翻译这边不借用会议管理器的客户端。测试里替换这个函数来注入假的传输层。"""
    return httpx.AsyncClient(timeout=TIMEOUT)


async def list_remote_models(base_url: str, api_key: str) -> list[str]:
    async with http_client() as http:
        return await fetch_models(http, base_url, api_key)
