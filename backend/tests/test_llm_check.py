import asyncio

import httpx
import pytest

from app import llm_check

TRANSLATION_KEY = "sk-test-1234567890"  # conftest 建的翻译模型


def fake_relay(monkeypatch, handler) -> None:
    """拉模型列表的一次性客户端换成假的传输层。"""
    monkeypatch.setattr(llm_check, "http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_chat_reports_any_transport_error(monkeypatch):
    def boom(*args, **kwargs):
        raise ImportError("Using SOCKS proxy, but the 'socksio' package is not installed")

    monkeypatch.setattr(httpx, "post", boom)
    out = llm_check.check_chat(base_url="https://relay.invalid/v1", api_key="sk-x", model="m", send_temperature=True)
    assert out.ok is False
    assert "socksio" in out.error


def test_chat_requires_key():
    out = llm_check.check_chat(base_url="", api_key="", model="m", send_temperature=True)
    assert out.ok is False


def test_probe_wraps_errors(monkeypatch):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    fake_relay(monkeypatch, boom)
    with pytest.raises(ValueError, match="连接失败"):
        asyncio.run(llm_check.list_remote_models("http://127.0.0.1:9/v1", ""))


def test_probe_endpoint_lists_models(monkeypatch, admin_client):
    seen: list[httpx.Request] = []

    def relay(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("authorization") == "Bearer sk-bad":
            return httpx.Response(401, json={"error": {"message": "invalid api key"}})
        return httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}, {"id": "b"}, {"object": "model"}]})

    fake_relay(monkeypatch, relay)
    main = admin_client.get("/api/admin/models").json()[0]
    resp = admin_client.post(
        "/api/admin/models/probe", json={"base_url": "https://relay.example/v1/", "model_id": main["id"]}
    )
    assert resp.status_code == 200 and resp.json() == {"models": ["a", "b"]}, "去重排序"
    assert str(seen[-1].url) == "https://relay.example/v1/models"
    assert seen[-1].headers["authorization"] == f"Bearer {TRANSLATION_KEY}", "Key 留空时用已保存的"

    resp = admin_client.post("/api/admin/models/probe", json={"api_key": "sk-bad"})
    assert str(seen[-1].url) == "https://api.openai.com/v1/models", "地址留空用 OpenAI 官方地址"
    assert resp.status_code == 400 and resp.json()["detail"] == "接口返回 401：invalid api key"


def test_chat_parses_openai_reply(monkeypatch):
    def fake_post(url, json, headers, timeout):
        assert url == "https://relay.example/v1/chat/completions"
        assert json["temperature"] == 0
        assert "thinking" not in json and "reasoning" not in json and "reasoning_effort" not in json
        return httpx.Response(
            200, json={"choices": [{"message": {"content": " 你好，世界！ "}}], "usage": {"total_tokens": 9}}
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    out = llm_check.check_chat(base_url="https://relay.example/v1/", api_key="sk-x", model="m", send_temperature=True)
    assert out.ok and out.reply == "你好，世界！" and out.usage == {"total_tokens": 9}
