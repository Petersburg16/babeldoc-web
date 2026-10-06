"""会议用的大模型配置：请求体组装、思考档位映射、方案校验与回退、流式调用的重试与结束判断、数据库迁移。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app import llm
from app.db import MIGRATIONS, Base, init_db, make_engine, make_sessionmaker
from app.llm import LlmClient, LlmConfig, LlmError, StreamInfo, strip_think
from app.meeting import processing
from app.meeting.llm_config import (
    STEPS,
    CustomParam,
    PresetSteps,
    StepConfig,
    builtin_efforts,
    default_step,
    effective_effort,
    load_steps,
    max_tokens_field,
    normalize_levels,
    parse_param,
    parse_valid_levels,
    resolve_meeting_llm,
)
from app.meeting.llm_config import build_config as build_llm_config
from app.models import Meeting, MeetingLlmModel, MeetingLlmPreset, ModelProfile, User
from tests.conftest import ASTRA

MESSAGES = [{"role": "user", "content": "你好"}]
PLAIN_BOX = SimpleNamespace(decrypt=lambda value: value)  # build_llm_config 只用到 decrypt


def llm_cfg(**extra: Any) -> LlmConfig:
    values: dict[str, Any] = {
        "model_id": 1,
        "base_url": "https://llm.invalid/v1",
        "api_key": "k",
        "model": "m",
        **extra,
    }
    return LlmConfig(**values)


def meeting_model(model: str = "m", levels: list[str] | None = None, **extra: Any) -> MeetingLlmModel:
    values: dict[str, Any] = {
        "id": 7,
        "name": "会议模型",
        "base_url": "https://llm.invalid/v1/",
        "api_key_enc": "k",
        "model": model,
        "effort_levels": levels or [],
        "qps": 3,
        "json_mode": False,
        "context_chars": None,
        "enabled": True,
        **extra,
    }
    return MeetingLlmModel(**values)


def payload_for(sc: StepConfig, model: MeetingLlmModel | None = None, step: str = "minutes", **kwargs: Any) -> dict:
    cfg = build_llm_config(model or meeting_model(), PLAIN_BOX, sc, step=step, label="测试")
    return LlmClient(cfg, None).payload(MESSAGES, **kwargs)  # type: ignore[arg-type]


# ---------- 请求体 ----------


def test_default_payload_sends_only_model_and_messages():
    assert payload_for(StepConfig()) == {"model": "m", "messages": MESSAGES}
    assert payload_for(StepConfig(), meeting_model("gpt-6-astra", ASTRA)) == {
        "model": "gpt-6-astra",
        "messages": MESSAGES,
    }
    streamed = payload_for(StepConfig(), stream=True)
    assert streamed == {"model": "m", "messages": MESSAGES, "stream": True, "stream_options": {"include_usage": True}}


def test_effort_level_and_projection():
    astra = meeting_model("gpt-6-astra", ASTRA)
    assert payload_for(StepConfig(effort="high"), astra)["reasoning_effort"] == "high"
    assert payload_for(StepConfig(effort="minimal"), astra)["reasoning_effort"] == "low", "就近换成档位表里的"
    assert "reasoning_effort" not in payload_for(StepConfig(effort="none"), astra), "没有“关闭”档就不发"
    assert "reasoning_effort" not in payload_for(StepConfig(effort="high")), "模型没有档位表时不发"
    with_none = meeting_model("gpt-6", ["none", "low", "high"])
    assert payload_for(StepConfig(effort="none"), with_none)["reasoning_effort"] == "none"

    assert effective_effort("medium", ["low", "high"]) == "high", "距离相同取高的一档"
    assert effective_effort("xhigh", ["low", "medium"]) == "medium"
    assert effective_effort("max", ["none", "low"]) == "low"
    assert effective_effort("minimal", ["none", "high"]) == "none", "按档位距离就近，“关闭”也算一档"
    assert effective_effort("default", ASTRA) is None and effective_effort("high", []) is None
    assert effective_effort("turbo", ASTRA) is None


def test_toggles_and_max_tokens_field():
    sc = StepConfig(temperature={"on": True, "value": 0.2}, top_p={"on": True, "value": 0.9})
    payload = payload_for(sc)
    assert payload["temperature"] == 0.2 and payload["top_p"] == 0.9 and "max_tokens" not in payload
    off = StepConfig(temperature={"on": False, "value": 0.2}, top_p={"on": False, "value": 0.9})
    assert payload_for(off) == {"model": "m", "messages": MESSAGES}, "开关关着就不发，值保留给界面"

    limited = StepConfig(max_tokens={"on": True, "value": 2000})
    assert payload_for(limited)["max_tokens"] == 2000
    for name in ("gpt-5", "gpt-5.2", "gpt-6-astra", "o3", "o4-mini", "openai/gpt-5"):
        payload = payload_for(limited, meeting_model(name))
        assert payload["max_completion_tokens"] == 2000 and "max_tokens" not in payload, name
    assert max_tokens_field("gpt-4o") == "max_tokens" and max_tokens_field("deepseek-chat") == "max_tokens"


def test_json_mode_only_when_model_and_step_want_it():
    json_model = meeting_model(json_mode=True)
    assert payload_for(StepConfig(), json_model, json_object=True)["response_format"] == {"type": "json_object"}
    assert "response_format" not in payload_for(StepConfig(), json_model)
    assert "response_format" not in payload_for(StepConfig(), json_object=True)


def test_custom_params_typed_and_reserved():
    params = [
        {"name": "n_int", "type": "number", "value": "3"},
        {"name": "n_float", "type": "number", "value": " 0.5 "},
        {"name": "n_exp", "type": "number", "value": "1e3"},
        {"name": "flag", "type": "boolean", "value": "TRUE"},
        {"name": "off", "type": "boolean", "value": "off"},
        {"name": "obj", "type": "json", "value": '{"a": [1, 2]}'},
        {"name": "verbosity", "type": "string", "value": " low "},
        {"name": "temperature", "type": "number", "value": "0.7"},  # 开关关着时可以用自定义参数发
    ]
    payload = payload_for(StepConfig(params=params))
    assert payload["n_int"] == 3 and isinstance(payload["n_int"], int)
    assert payload["n_float"] == 0.5 and payload["n_exp"] == 1000.0 and isinstance(payload["n_exp"], float)
    assert payload["flag"] is True and payload["off"] is False
    assert payload["obj"] == {"a": [1, 2]} and payload["verbosity"] == " low " and payload["temperature"] == 0.7

    # 就算绕过校验塞进了保留键，也不能覆盖本站填写的值
    client = LlmClient(llm_cfg(extra={"model": "evil", "messages": [], "stream": False, "stream_options": {}}), None)  # type: ignore[arg-type]
    assert client.payload(MESSAGES) == {"model": "m", "messages": MESSAGES}
    streamed = client.payload(MESSAGES, stream=True)
    assert streamed["stream"] is True and streamed["stream_options"] == {"include_usage": True}
    assert parse_param(CustomParam(name="x", type="json", value="null")) is None


@pytest.mark.parametrize(
    ("step", "message"),
    [
        ({"params": [{"name": "model", "value": "x"}]}, "不能作为自定义参数"),
        ({"params": [{"name": "stream_options", "type": "json", "value": "{}"}]}, "不能作为自定义参数"),
        ({"params": [{"name": "1abc", "value": "x"}]}, "只能用字母"),
        ({"params": [{"name": "obj", "type": "json", "value": "{bad"}]}, "不是合法的 JSON"),
        ({"params": [{"name": "n", "type": "number", "value": "abc"}]}, "不是数字"),
        ({"params": [{"name": "b", "type": "boolean", "value": "maybe"}]}, "true 或 false"),
        ({"params": [{"name": "a", "value": "1"}, {"name": "a", "value": "2"}]}, "重名"),
        (
            {"temperature": {"on": True, "value": 0.5}, "params": [{"name": "temperature", "value": "1"}]},
            "重复",
        ),
        (
            {"max_tokens": {"on": True, "value": 100}, "params": [{"name": "max_completion_tokens", "value": "1"}]},
            "重复",
        ),
        ({"temperature": {"on": False, "value": 2.5}}, "0 到 2"),
        ({"top_p": {"on": True, "value": 1.5}}, "0 到 1"),
        ({"max_tokens": {"on": True, "value": 0}}, "1 到 1000000"),
        ({"timeout_s": 5}, "timeout_s"),
        ({"effort": "turbo"}, "未知的思考强度"),
    ],
)
def test_step_config_validation(step, message):
    with pytest.raises(ValidationError) as info:
        StepConfig.model_validate(step)
    assert message in str(info.value)


def test_step_config_normalizes():
    sc = StepConfig(effort=" HIGH ", params=[{"name": " verbosity ", "value": "low"}])
    assert sc.effort == "high" and sc.params[0].name == "verbosity"
    assert StepConfig(effort="").effort == "default"
    assert [getattr(PresetSteps(), s).timeout_s for s in STEPS] == [300, 300, 900, 600]
    partial = PresetSteps.model_validate({s: {"model_id": 1} for s in STEPS})
    assert [getattr(partial, s).timeout_s for s in STEPS] == [300, 300, 900, 600], "没写超时用各用途的默认值"


def test_load_steps_tolerates_garbage():
    for raw in (None, "garbage", [], {"speakers": None}):
        assert load_steps(raw) == PresetSteps()
    steps = load_steps(
        {
            "speakers": "not a dict",
            "polish": {"model_id": 3, "temperature": {"on": True, "value": 9}},
            "minutes": {"model_id": 3, "effort": "high", "unknown": 1},
            "chat": {"model_id": 4, "params": [{"name": "model", "value": "x"}]},
        }
    )
    assert steps.speakers == default_step("speakers")
    assert steps.polish == default_step("polish"), "坏掉的用途整个退回默认值"
    assert steps.minutes.model_id == 3 and steps.minutes.effort == "high" and steps.minutes.timeout_s == 900
    assert steps.chat == default_step("chat")


def test_effort_tables_and_relay_message():
    assert builtin_efforts("gpt-6-astra") == ASTRA
    assert builtin_efforts("GPT-6") == ["none", "low", "medium", "high", "xhigh", "max"]
    assert builtin_efforts("openai/gpt-5") == ["minimal", "low", "medium", "high"]
    assert builtin_efforts("m") == [] and builtin_efforts("gpt-4o") == []
    assert normalize_levels(["HIGH", "low", "low", "bogus"]) == ["low", "high"]

    message = 'level "bdw-probe" not supported, valid levels: low, medium, high, xhigh, max'
    assert parse_valid_levels(message) == ASTRA
    assert parse_valid_levels(json.dumps({"error": {"message": message}})) == ASTRA, "报错原样包在 JSON 里也能读"
    assert parse_valid_levels("Invalid value. Valid values are: high, low") == ["low", "high"]
    assert parse_valid_levels("model not found") == [] and parse_valid_levels("") == []


# ---------- 方案的回退 ----------


def add_model(db, name: str, model: str, **extra: Any) -> int:
    row = MeetingLlmModel(name=name, base_url="https://llm.invalid/v1", api_key_enc="", model=model, **extra)
    db.add(row)
    db.flush()
    return row.id


def add_preset(db, name: str, model_ids: dict[str, int], **extra: Any) -> int:
    steps = {s: {"model_id": model_ids[s], "timeout_s": 120 + i} for i, s in enumerate(STEPS)}
    steps["minutes"]["effort"] = "xhigh"
    row = MeetingLlmPreset(name=name, steps=steps, **extra)
    db.add(row)
    db.flush()
    return row.id


def test_resolve_meeting_llm_fallback_chain(app):
    ctx = app.state.ctx
    with ctx.Session() as db:
        assert resolve_meeting_llm(db, ctx.secrets, None, "polish") == (
            None,
            "管理员还没有配置会议记录用的大模型方案",
        )
        a = add_model(db, "模型甲", "model-a")
        b = add_model(db, "模型乙", "model-b", effort_levels=["low", "medium", "high"], qps=2, context_chars=50_000)
        fast = add_preset(db, "快速", dict.fromkeys(STEPS, a), is_default=True, sort_order=1)
        fine = add_preset(db, "精细", dict.fromkeys(STEPS, b), sort_order=0)
        off = add_preset(db, "停用的", dict.fromkeys(STEPS, b), enabled=False)
        db.commit()

        cfg, reason = resolve_meeting_llm(db, ctx.secrets, fine, "minutes")
        assert reason is None and cfg is not None
        assert (cfg.model, cfg.label, cfg.namespace) == ("model-b", "精细·生成纪要", "meeting")
        assert cfg.effort == "high", "xhigh 不在档位表里，就近换成 high"
        assert (cfg.qps, cfg.context_chars, cfg.timeout, cfg.model_id) == (2, 50_000, 122.0, b)
        chat_cfg, _ = resolve_meeting_llm(db, ctx.secrets, fine, "chat")
        assert chat_cfg.namespace == "meeting-chat" and chat_cfg.effort is None

        for preset_id in (None, fast, off, 9999):
            cfg, _ = resolve_meeting_llm(db, ctx.secrets, preset_id, "polish")
            assert cfg.model == "model-a" and cfg.label == "快速·整理逐字稿", preset_id
        assert resolve_meeting_llm(db, ctx.secrets, fast, "minutes")[0].effort is None, "模型甲没有档位表"

        db.get(MeetingLlmPreset, fast).is_default = False
        db.commit()
        assert resolve_meeting_llm(db, ctx.secrets, None, "polish")[0].model == "model-b", "没有默认方案时用排在最前的"

        # 用途的模型停用了：说清楚是哪个方案的哪个用途，不悄悄换模型
        db.get(MeetingLlmModel, a).enabled = False
        db.commit()
        cfg, reason = resolve_meeting_llm(db, ctx.secrets, fast, "minutes")
        assert cfg is None and reason == "整理方案“快速”中“生成纪要”用的模型“模型甲”已停用"
        db.get(MeetingLlmPreset, fast).steps = {"polish": {"model_id": 9999}}
        db.commit()
        cfg, reason = resolve_meeting_llm(db, ctx.secrets, fast, "polish")
        assert cfg is None and reason == "整理方案“快速”没有给“整理逐字稿”配置可用的模型"

        db.get(MeetingLlmPreset, fast).enabled = False
        db.get(MeetingLlmPreset, fine).enabled = False
        db.commit()
        assert resolve_meeting_llm(db, ctx.secrets, fine, "chat")[0] is None


def test_legacy_meeting_uses_default_preset(app):
    """0.4.0 的会议只记了翻译模型（model_id），整理时按默认方案，不再用翻译模型。"""
    ctx = app.state.ctx
    with ctx.Session() as db:
        user = User(username="u", password_hash="x")
        translation = ModelProfile(name="翻译模型", model="translate-model", is_default=True)
        db.add_all([user, translation])
        db.flush()
        meeting_model_id = add_model(db, "会议模型", "meeting-model")
        add_preset(db, "默认", dict.fromkeys(STEPS, meeting_model_id), is_default=True)
        db.add(
            Meeting(
                id="legacy000001",
                user_id=user.id,
                title="老会议",
                filename="a.wav",
                status="done",
                model_id=translation.id,
                model_name="翻译模型",
            )
        )
        db.commit()

    manager = SimpleNamespace(Session=ctx.Session, secrets=ctx.secrets, http=object())
    client, reason = processing._client(manager, "legacy000001", "polish")  # type: ignore[arg-type]
    assert reason is None and client.cfg.model == "meeting-model" and client.cfg.label == "默认·整理逐字稿"
    assert processing._client(manager, "nope", "polish") == (None, None), "会议已删除"


# ---------- 调用：流式、重试、结束判断 ----------


def sse(*events: dict[str, Any] | str, done: bool = True) -> bytes:
    lines = [e if isinstance(e, str) else "data: " + json.dumps(e, ensure_ascii=False) for e in events]
    if done:
        lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode("utf-8")


def delta(content: str, finish: str | None = None) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": {"content": content}, "finish_reason": finish}]}


def usage(total: int, reasoning: int = 0) -> dict[str, Any]:
    return {
        "choices": [],
        "usage": {"total_tokens": total, "completion_tokens_details": {"reasoning_tokens": reasoning}},
    }


def stream_response(body: bytes) -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


def run_with(handler, fn, **extra: Any):
    """在新的事件循环里，用假的传输层建一个客户端跑 fn(client)。"""

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await fn(LlmClient(llm_cfg(**extra), http))

    return asyncio.run(main())


async def collect_stream(client: LlmClient, info: StreamInfo | None = None) -> str:
    return "".join([t async for t in client.stream(MESSAGES, info=info)])


@pytest.fixture
def no_sleep(monkeypatch) -> list[float]:
    """重试的退避不真的等；记下等了多久。"""
    delays: list[float] = []

    async def fake_sleep(delay: float, result: Any = None) -> Any:
        delays.append(delay)
        return result

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return delays


def test_stream_read_timeout_is_not_retried(no_sleep):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(LlmError) as info:
        run_with(handler, collect_stream, timeout=42.0)
    assert len(requests) == 1 and not no_sleep, "对方可能已经在计费：读超时不重发"
    assert "42 秒" in str(info.value) and not info.value.retryable
    assert json.loads(requests[0].content)["stream"] is True

    requests.clear()
    with pytest.raises(LlmError):
        run_with(handler, lambda client: client.chat(MESSAGES))
    assert len(requests) == 1, "非流式也一样"


def test_stream_retries_connect_errors(no_sleep):
    requests: list[httpx.Request] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) < 3:
            raise httpx.ConnectError("refused", request=request)
        return stream_response(sse(delta("你好", "stop")))

    assert run_with(flaky, collect_stream) == "你好"
    assert len(requests) == 3 and len(no_sleep) == 2

    requests.clear()

    def down(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LlmError) as info:
        run_with(down, collect_stream)
    assert len(requests) == llm.RETRIES and info.value.retryable

    requests.clear()

    def busy_then_ok(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return stream_response(sse(delta("好", "stop")))

    assert run_with(busy_then_ok, collect_stream) == "好" and len(requests) == 2, "开始输出前的 5xx 可以重试"


@pytest.mark.parametrize("error", [httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectError])
def test_stream_never_retries_after_output_started(no_sleep, error):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)

        async def body():
            yield sse(delta("第一段"), done=False)
            raise error("connection lost")

        return httpx.Response(200, content=body(), headers={"content-type": "text/event-stream"})

    got: list[str] = []

    async def consume(client: LlmClient) -> None:
        async for part in client.stream(MESSAGES):
            got.append(part)

    with pytest.raises(LlmError) as info:
        run_with(handler, consume)
    assert got == ["第一段"] and len(requests) == 1 and not no_sleep, "已经出字：对方在计费，断了也不重发"
    assert "连接中断" in str(info.value) and not info.value.retryable


@pytest.mark.parametrize("status, delays", [(429, [6, 12]), (503, [2, 4])])
@pytest.mark.parametrize("call", ["chat", "stream"])
def test_busy_backoff_same_for_chat_and_stream(no_sleep, call, status, delays):
    requests: list[httpx.Request] = []
    whole = {"choices": [{"message": {"content": "好"}, "finish_reason": "stop"}]}

    def busy_twice(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) < 3:
            return httpx.Response(status, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json=whole)

    run = collect_stream if call == "stream" else (lambda client: client.chat(MESSAGES))
    run_with(busy_twice, run)
    assert len(requests) == 3 and no_sleep == delays, "429 退避乘 3，5xx 不乘"

    requests.clear()
    no_sleep.clear()

    def busy(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": "slow down"}})

    with pytest.raises(LlmError) as info:
        run_with(busy, run)
    assert len(requests) == llm.RETRIES and no_sleep == delays
    assert info.value.retryable and info.value.status == status and "slow down" in str(info.value)


def test_stream_usage_chunk_and_finish_reason():
    body = sse(
        {"choices": [{"index": 0, "delta": {"reasoning_content": "想一想"}}]},
        delta("第一段"),
        ": keep-alive",
        delta("第二段", "stop"),
        usage(321, 100),
    )
    info = StreamInfo()
    text = run_with(lambda r: stream_response(body), lambda client: collect_stream(client, info))
    assert text == "第一段第二段", "思考内容不产出"
    assert (info.tokens, info.reasoning_tokens, info.finish_reason) == (321, 100, "stop")


def test_stream_without_done_or_finish_reason_is_incomplete():
    cut = sse(delta("写到一半"), done=False)
    with pytest.raises(LlmError) as info:
        run_with(lambda r: stream_response(cut), collect_stream)
    assert "没有正常结束" in str(info.value)

    finished = sse(delta("写完了", "stop"), done=False)
    assert run_with(lambda r: stream_response(finished), collect_stream) == "写完了", "有结束原因就算完整"
    old_style = sse(delta("老中转"))
    assert run_with(lambda r: stream_response(old_style), collect_stream) == "老中转", "有 [DONE] 也算完整"


def test_stream_error_event_and_plain_json_fallback():
    error = sse({"error": {"message": "quota exceeded"}}, done=False)
    with pytest.raises(LlmError, match="quota exceeded"):
        run_with(lambda r: stream_response(error), collect_stream)

    whole = {
        "choices": [{"message": {"content": "整段回复"}, "finish_reason": "stop"}],
        "usage": {"total_tokens": 9},
    }
    info = StreamInfo()
    text = run_with(lambda r: httpx.Response(200, json=whole), lambda client: collect_stream(client, info))
    assert text == "整段回复" and info.tokens == 9 and info.finish_reason == "stop", "中转不支持流式时直接给整段"

    bare = {"choices": [{"message": {"content": "整段回复"}}]}
    info = StreamInfo()
    run_with(lambda r: httpx.Response(200, json=bare), lambda client: collect_stream(client, info))
    chat = run_with(lambda r: httpx.Response(200, json=bare), lambda client: client.chat(MESSAGES))
    assert info.finish_reason is None and chat.finish_reason is None, "缺结束原因时流式和非流式都保持 None"
    failed = {"error": {"message": "quota exceeded"}}
    with pytest.raises(LlmError, match="大模型返回错误：quota exceeded"):
        run_with(lambda r: httpx.Response(200, json=failed), lambda client: client.chat(MESSAGES))


def test_collect_returns_text_tokens_and_finish_reason():
    body = sse(delta("<think>先想"), delta("一想</think>\n\n"), delta("答案", "length"), usage(50, 20))
    result = run_with(lambda r: stream_response(body), lambda client: client.collect(MESSAGES))
    assert (result.text, result.tokens, result.reasoning_tokens) == ("答案", 50, 20)
    assert result.finish_reason == "length", "有正文的截断交给调用方处理"


def test_length_without_text_is_not_retryable():
    body = sse(delta("", "length"), usage(4096, 4096))
    with pytest.raises(LlmError) as info:
        run_with(lambda r: stream_response(body), lambda client: client.collect(MESSAGES))
    assert info.value.no_retry and "最大输出" in str(info.value)

    whole = {"choices": [{"message": {"content": "  "}, "finish_reason": "length"}], "usage": {"total_tokens": 9}}
    with pytest.raises(LlmError) as info:
        run_with(lambda r: httpx.Response(200, json=whole), lambda client: client.chat(MESSAGES))
    assert info.value.no_retry


def test_chat_strips_think_and_reports_reasoning():
    whole = {
        "choices": [
            {
                "message": {"content": "<think>思考</think>回答", "reasoning_content": "摘要"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"total_tokens": 30, "completion_tokens_details": {"reasoning_tokens": 12}},
    }
    result = run_with(lambda r: httpx.Response(200, json=whole), lambda client: client.chat(MESSAGES))
    assert (result.text, result.tokens, result.reasoning_tokens, result.finish_reason) == ("回答", 30, 12, "stop")


def test_strip_think_only_leading_block():
    assert strip_think("<think>想一想</think>\n\n答案") == "答案"
    assert strip_think("  <Thinking>多行\n思考</Thinking>答案") == "答案"
    assert strip_think("答案里提到 <think>标签</think> 不动") == "答案里提到 <think>标签</think> 不动"
    assert strip_think("<think>没写完的思考") == ""
    assert strip_think("<think>a</thought>答案") == "", "标签不配对时按没写完处理"
    assert strip_think("普通回答") == "普通回答"


def test_limiter_key_includes_namespace_and_qps():
    async def main():
        base = llm_cfg(model_id=5, qps=2)
        same = llm._limiter(base)
        assert llm._limiter(llm_cfg(model_id=5, qps=2)) is same
        assert llm._limiter(llm_cfg(model_id=5, qps=2, namespace="meeting-chat")) is not same, "对话单独排队"
        assert llm._limiter(llm_cfg(model_id=5, qps=3)) is not same, "改了 QPS 换新的信号量"
        assert llm._limiter(llm_cfg(model_id=6, qps=2)) is not same
        assert llm._limiter(llm_cfg(model_id=5, qps=20))._value == 8, "同时最多 8 个"

    asyncio.run(main())


# ---------- 数据库迁移 ----------


def columns(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}


def user_version(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(text("PRAGMA user_version")).scalar()


def test_migration_on_fresh_db(tmp_path):
    engine = make_engine(tmp_path / "fresh.db")
    try:
        init_db(engine)
        assert "llm_preset_id" in columns(engine, "meetings")
        assert {"meeting_llm_models", "meeting_llm_presets"} <= set(Base.metadata.tables)
        assert user_version(engine) == MIGRATIONS[-1][0]
        init_db(engine)  # 每次启动都会跑
        # 版本号被重置（例如从备份恢复的库）、列却已经在：加列前先查，不会报重复列
        with engine.begin() as conn:
            conn.execute(text("PRAGMA user_version = 0"))
        init_db(engine)
        assert user_version(engine) == MIGRATIONS[-1][0]
    finally:
        engine.dispose()


@pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 35), reason="造老库要用 SQLite 3.35 的 DROP COLUMN")
def test_migration_upgrades_old_db(tmp_path):
    engine = make_engine(tmp_path / "old.db")
    try:
        # 造一个 0.4.0 的库：表都在，但 meetings 没有 llm_preset_id，版本号是 0
        Base.metadata.create_all(engine)
        Session = make_sessionmaker(engine)
        with Session() as db:
            user = User(username="u", password_hash="x")
            db.add(user)
            db.flush()
            db.add(Meeting(id="old000000001", user_id=user.id, title="老会议", filename="a.wav", model_name="翻译模型"))
            db.commit()
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE meetings DROP COLUMN llm_preset_id"))
            conn.execute(text("PRAGMA user_version = 0"))
        assert "llm_preset_id" not in columns(engine, "meetings")

        init_db(engine)
        assert "llm_preset_id" in columns(engine, "meetings") and user_version(engine) == MIGRATIONS[-1][0]
        init_db(engine)
        with Session() as db:
            m = db.get(Meeting, "old000000001")
            assert m.title == "老会议" and m.model_name == "翻译模型" and m.llm_preset_id is None
            m.llm_preset_id = 3
            db.commit()
    finally:
        engine.dispose()
