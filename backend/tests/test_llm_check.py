import httpx
import pytest

from app import llm_check


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
    def boom(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", boom)
    with pytest.raises(ValueError, match="连接失败"):
        llm_check.list_remote_models("http://127.0.0.1:9/v1", "")


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
