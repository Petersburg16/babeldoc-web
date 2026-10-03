"""管理后台的“测试连接 / 获取模型列表”。直接用 httpx 调 OpenAI 兼容接口，不经过引擎。"""

from __future__ import annotations

import time
from typing import Any

import httpx

from .schemas import ModelTestOut

DEFAULT_BASE_URL = "https://api.openai.com/v1"
TIMEOUT = httpx.Timeout(60.0, connect=10.0)


def _endpoint(base_url: str, path: str) -> str:
    return (base_url or DEFAULT_BASE_URL).rstrip("/") + path


def _error_text(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300] or resp.reason_phrase
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)[:300]
    if isinstance(err, str):
        return err[:300]
    return str(data)[:300]


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
            _endpoint(base_url, "/chat/completions"),
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=TIMEOUT,
        )
    except Exception as e:  # 代理配置错误等也只当作连接失败汇报，不让接口 500
        return ModelTestOut(ok=False, error=f"连接失败：{e.__class__.__name__}: {e}"[:300])
    latency = int((time.monotonic() - started) * 1000)
    if resp.status_code >= 400:
        return ModelTestOut(ok=False, status=resp.status_code, latency_ms=latency, error=_error_text(resp))
    try:
        data = resp.json()
        reply = (data["choices"][0]["message"].get("content") or "").strip()
    except (ValueError, KeyError, IndexError, TypeError):
        return ModelTestOut(ok=False, status=resp.status_code, latency_ms=latency, error="返回内容不是 OpenAI 兼容格式")
    return ModelTestOut(
        ok=True, status=resp.status_code, latency_ms=latency, reply=reply[:200], usage=data.get("usage")
    )


def list_remote_models(base_url: str, api_key: str) -> list[str]:
    try:
        resp = httpx.get(
            _endpoint(base_url, "/models"),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=TIMEOUT,
        )
    except Exception as e:
        raise ValueError(f"连接失败：{e.__class__.__name__}: {e}"[:300]) from e
    if resp.status_code >= 400:
        raise ValueError(f"接口返回 {resp.status_code}：{_error_text(resp)}")
    try:
        items = resp.json().get("data") or []
    except (ValueError, AttributeError) as e:
        raise ValueError("返回内容不是 OpenAI 兼容格式") from e
    return sorted({str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")})
