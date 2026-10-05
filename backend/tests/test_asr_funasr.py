"""阿里云百炼 Fun-ASR 适配器：请求形状、轮询各状态、错误分类、凭据检查与结果解析。

接口样例取自官方文档（fixtures/asr/funasr_*.json）；鉴权失败的正文是对官方接口用无效 Key 实测得到的。
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.meeting.asr.base import AsrError, AsrSegment, SubmitOptions
from app.meeting.asr.funasr import FunAsrAdapter
from tests.conftest import make_wav, upload_audio, wait_meeting
from tests.llm_fake import install_fake_llm

FIXTURES = Path(__file__).parent / "fixtures" / "asr"
DEFAULT_BASE = "https://dashscope.aliyuncs.com/api/v1"
KEY = "sk-test-0123456789"
AUDIO_URL = "https://site.example/api/public/meeting-audio/m1/0/tok.mp3"
RESULT_URL_PREFIX = "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/"

Handler = Callable[[httpx.Request], httpx.Response]


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text("utf-8"))


def run(
    handler: Handler,
    call: Callable[[FunAsrAdapter], Awaitable[Any]],
    config: dict[str, Any] | None = None,
    secrets: dict[str, str] | None = None,
) -> tuple[Any, list[httpx.Request]]:
    """用 MockTransport 驱动一次适配器调用，返回结果和收到的全部请求。"""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    async def main() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as client:
            adapter = FunAsrAdapter(config or {}, {"api_key": KEY} if secrets is None else secrets, client)
            return await call(adapter)

    return asyncio.run(main()), seen


def run_error(handler: Handler, call: Callable[[FunAsrAdapter], Awaitable[Any]], **kw: Any) -> AsrError:
    with pytest.raises(AsrError) as info:
        run(handler, call, **kw)
    return info.value


def submit(opts: SubmitOptions | None = None) -> Callable[[FunAsrAdapter], Awaitable[str]]:
    return lambda a: a.submit(AUDIO_URL, opts or SubmitOptions())


def poll(task_id: str = "task-1") -> Callable[[FunAsrAdapter], Awaitable[Any]]:
    return lambda a: a.poll(task_id)


def task(status: str, **output: Any) -> dict[str, Any]:
    return {"request_id": "r-1", "output": {"task_id": "task-1", "task_status": status, **output}}


def provider(task_body: dict[str, Any], result: Any = None, result_status: int = 200) -> Handler:
    """假的百炼：查询接口回 task_body，OSS 结果链接回 result。"""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(RESULT_URL_PREFIX):
            return httpx.Response(result_status, json=result)
        if request.method == "GET" and request.url.path.startswith("/api/v1/tasks/"):
            return httpx.Response(200, json=task_body)
        return httpx.Response(500, text="unexpected request")

    return handler


# ---------- 提交 ----------


def test_submit_request_shape() -> None:
    task_id, seen = run(
        lambda r: httpx.Response(200, json=load("funasr_submit.json")),
        submit(SubmitOptions(language="zh", expected_speakers=4, hotwords=["风场"])),
    )
    assert task_id == "c2e5d63b-96e1-4607-bb91-************"
    assert len(seen) == 1
    req = seen[0]
    assert req.method == "POST"
    assert str(req.url) == f"{DEFAULT_BASE}/services/audio/asr/transcription"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert req.headers["content-type"] == "application/json"
    assert req.headers["x-dashscope-async"] == "enable"
    # 不支持请求内热词，hotwords 不发
    assert json.loads(req.content) == {
        "model": "fun-asr",
        "input": {"file_urls": [AUDIO_URL]},
        "parameters": {"diarization_enabled": True, "language_hints": ["zh"], "speaker_count": 4},
    }


@pytest.mark.parametrize(
    ("language", "expected", "params"),
    [
        ("en", None, {"diarization_enabled": True, "language_hints": ["en"]}),
        ("auto", 3, {"diarization_enabled": True, "speaker_count": 3}),
        ("zh", 1, {"diarization_enabled": True, "language_hints": ["zh"]}),
        ("zh", 2, {"diarization_enabled": True, "language_hints": ["zh"], "speaker_count": 2}),
        ("zh", 100, {"diarization_enabled": True, "language_hints": ["zh"], "speaker_count": 100}),
        ("zh", 101, {"diarization_enabled": True, "language_hints": ["zh"]}),
    ],
)
def test_submit_language_and_speaker_count(language: str, expected: int | None, params: dict[str, Any]) -> None:
    _, seen = run(
        lambda r: httpx.Response(200, json=load("funasr_submit.json")),
        submit(SubmitOptions(language=language, expected_speakers=expected)),
    )
    assert json.loads(seen[0].content)["parameters"] == params


def test_submit_uses_configured_base_and_model() -> None:
    config = {"base_url": "https://ws-1.ap-southeast-1.maas.example/api/v1/ ", "model": "paraformer-v2"}
    _, seen = run(lambda r: httpx.Response(200, json=load("funasr_submit.json")), submit(), config=config)
    assert str(seen[0].url) == "https://ws-1.ap-southeast-1.maas.example/api/v1/services/audio/asr/transcription"
    assert json.loads(seen[0].content)["model"] == "paraformer-v2"


def test_submit_without_key_or_bad_base_sends_nothing() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        raise AssertionError("不该发请求")

    err = run_error(handler, submit(), secrets={})
    assert err.kind == "config" and not err.retryable
    err = run_error(handler, submit(), config={"base_url": "dashscope.aliyuncs.com/api/v1"})
    assert err.kind == "config"


@pytest.mark.parametrize(
    ("status", "body", "kind", "retryable"),
    [
        (401, load("funasr_error_invalid_key.json"), "auth", False),
        (403, {"code": "AccessDenied.Unpurchased", "message": "Access to model denied."}, "auth", False),
        (
            400,
            {"code": "Arrearage", "message": "Access denied, please make sure your account is in good standing."},
            "quota",
            False,
        ),
        (
            403,
            {"code": "AllocationQuota.FreeTierOnly", "message": "The free tier of the model has been exhausted."},
            "quota",
            False,
        ),
        (429, {"code": "Throttling.RateQuota", "message": "Requests rate limit exceeded."}, "quota", True),
        (400, {"code": "InvalidParameter", "message": "Required parameter(s) missing or invalid."}, "input", False),
        (404, {"code": "ModelNotFound", "message": "Model can not be found."}, "config", False),
        (500, {"code": "InternalError", "message": "Internal server error!"}, "provider", True),
        (502, None, "provider", True),
    ],
)
def test_submit_errors(status: int, body: Any, kind: str, retryable: bool) -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        if body is None:
            return httpx.Response(status, text="<html>Bad Gateway</html>")
        return httpx.Response(status, json=body)

    err = run_error(handler, submit())
    assert err.kind == kind
    assert err.retryable is retryable
    assert KEY not in str(err)
    if body is not None:
        assert body["code"] in str(err) and f"HTTP {status}" in str(err)


def test_submit_without_task_id_is_not_retried() -> None:
    err = run_error(
        lambda r: httpx.Response(200, json={"request_id": "r", "output": {"task_status": "PENDING"}}), submit()
    )
    assert err.kind == "provider" and not err.retryable


# ---------- 查询 ----------


@pytest.mark.parametrize("status", ["PENDING", "RUNNING"])
def test_poll_pending(status: str) -> None:
    result, seen = run(provider(task(status)), poll("c2e5d63b-96e1-4607-bb91-abc"))
    assert result.state == "pending"
    assert result.progress is None
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert str(seen[0].url) == f"{DEFAULT_BASE}/tasks/c2e5d63b-96e1-4607-bb91-abc"
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"


def test_poll_succeeded_downloads_results_without_auth() -> None:
    result_json = load("funasr_result_diarized.json")
    result, seen = run(provider(load("funasr_task_succeeded.json"), result_json), poll())
    assert result.state == "done"
    assert result.raw == [result_json]
    download = seen[1]
    assert str(download.url).startswith(RESULT_URL_PREFIX)
    # 签名链接里的 %3A 原样保留，否则 OSS 签名对不上
    assert "15%3A11" in str(download.url)
    assert "authorization" not in download.headers
    # 存盘后再读回来也能解析
    segments = FunAsrAdapter.parse(json.loads(json.dumps(result.raw, ensure_ascii=False)))
    assert [s.speaker for s in segments] == ["0", "1", "0"]


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED"])
@pytest.mark.parametrize("code", ["SUCCESS_WITH_NO_VALID_FRAGMENT", "ASR_RESPONSE_HAVE_NO_WORDS"])
def test_poll_no_speech_is_empty_result(status: str, code: str) -> None:
    body = task(status, results=[{"file_url": AUDIO_URL, "code": code, "message": code, "subtask_status": "FAILED"}])
    result, seen = run(provider(body), poll())
    assert result.state == "done"
    assert result.raw == []
    assert FunAsrAdapter.parse(result.raw) == []
    assert len(seen) == 1


def test_poll_no_speech_reported_on_task() -> None:
    body = task("FAILED", code="SUCCESS_WITH_NO_VALID_FRAGMENT", message="no valid fragment")
    result, _ = run(provider(body), poll())
    assert result.state == "done" and result.raw == []


def test_poll_subtask_download_failed() -> None:
    result, seen = run(provider(load("funasr_task_failed.json")), poll())
    assert result.state == "failed"
    assert result.error_kind == "config"
    assert "FILE_DOWNLOAD_FAILED" in result.error
    assert "站点公网地址" in result.error
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("code", "kind"),
    [("FILE_TOO_LARGE", "input"), ("FILE_TRANS_TASK_EXPIRED", "provider"), ("SOMETHING_NEW", "provider")],
)
def test_poll_task_failed_with_code(code: str, kind: str) -> None:
    body = task("FAILED", code=code, message="detail from provider")
    result, _ = run(provider(body), poll())
    assert result.state == "failed"
    assert result.error_kind == kind
    assert code in result.error and "detail from provider" in result.error


def test_poll_succeeded_without_results_fails() -> None:
    result, _ = run(provider(task("SUCCEEDED", results=[])), poll())
    assert result.state == "failed" and result.error_kind == "provider"


def test_poll_unknown_task() -> None:
    result, _ = run(provider(task("UNKNOWN")), poll())
    assert result.state == "failed"
    assert result.error_kind == "provider"
    assert "24 小时" in result.error


def test_poll_auth_error_raises() -> None:
    err = run_error(lambda r: httpx.Response(401, json=load("funasr_error_invalid_key.json")), poll())
    assert err.kind == "auth" and not err.retryable
    assert "InvalidApiKey" in str(err)


def test_poll_server_error_is_retryable() -> None:
    err = run_error(lambda r: httpx.Response(503, text="busy"), poll())
    assert err.kind == "provider" and err.retryable


@pytest.mark.parametrize(("status", "retryable"), [(403, False), (404, False), (500, True)])
def test_poll_result_download_errors(status: int, retryable: bool) -> None:
    err = run_error(provider(load("funasr_task_succeeded.json"), {"Code": "AccessDenied"}, status), poll())
    assert err.kind == "provider"
    assert err.retryable is retryable


def test_poll_result_not_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(RESULT_URL_PREFIX):
            return httpx.Response(200, text="<Error/>")
        return httpx.Response(200, json=load("funasr_task_succeeded.json"))

    err = run_error(handler, poll())
    assert err.kind == "provider" and not err.retryable


def test_poll_non_dashscope_response_is_config_error() -> None:
    err = run_error(lambda r: httpx.Response(200, text="<html>hello</html>"), poll())
    assert err.kind == "config"


# ---------- 解析 ----------


def test_parse_doc_sample() -> None:
    raw = load("funasr_result.json")
    expected = [AsrSegment(100, 3820, "0", "Hello world, 这里是阿里巴巴语音实验室。")]
    assert FunAsrAdapter.parse(raw) == expected
    assert FunAsrAdapter.parse([raw]) == expected


def test_parse_diarized_skips_empty_and_keeps_order() -> None:
    segments = FunAsrAdapter.parse([load("funasr_result_diarized.json")])
    assert segments == [
        AsrSegment(300, 4100, "0", "大家好，今天先过一下进度。"),
        AsrSegment(4600, 11250, "1", "我这周把风场数据重新清洗了一遍。"),
        AsrSegment(12500, 20900, "0", "好，下周把对比图发给我。"),
    ]


def test_parse_tolerates_missing_fields() -> None:
    raw = {
        "transcripts": [
            {"sentences": [{"begin_time": 500, "end_time": 300, "text": " 没有说话人编号 "}, {"text": "缺时间"}, None]},
            None,
        ]
    }
    assert FunAsrAdapter.parse([raw, None, {}]) == [
        AsrSegment(500, 500, "0", "没有说话人编号"),
        AsrSegment(0, 0, "0", "缺时间"),
    ]
    assert FunAsrAdapter.parse([]) == []


# ---------- 凭据检查 ----------


def test_check_credentials_ok_on_unknown_task() -> None:
    result, seen = run(lambda r: httpx.Response(200, json=task("UNKNOWN")), lambda a: a.check_credentials())
    assert result.ok
    assert len(seen) == 1
    req = seen[0]
    assert req.method == "GET"
    assert req.url.path.startswith("/api/v1/tasks/") and len(req.url.path) > len("/api/v1/tasks/")
    assert req.headers["authorization"] == f"Bearer {KEY}"


def test_check_credentials_invalid_key() -> None:
    result, _ = run(
        lambda r: httpx.Response(401, json=load("funasr_error_invalid_key.json")), lambda a: a.check_credentials()
    )
    assert not result.ok
    assert "API Key" in result.message and "InvalidApiKey" in result.message
    assert KEY not in result.message


def test_check_credentials_wrong_address() -> None:
    result, _ = run(lambda r: httpx.Response(404, text="not found"), lambda a: a.check_credentials())
    assert not result.ok and "接口地址" in result.message
    result, _ = run(lambda r: httpx.Response(200, text="<html></html>"), lambda a: a.check_credentials())
    assert not result.ok and "接口地址" in result.message


def test_check_credentials_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    result, _ = run(handler, lambda a: a.check_credentials())
    assert not result.ok and "连不上" in result.message


def test_check_credentials_without_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("不该发请求")

    result, seen = run(handler, lambda a: a.check_credentials(), secrets={})
    assert not result.ok and "API Key" in result.message
    assert seen == []


# ---------- 接进会议流程 ----------


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_funasr_in_meeting_pipeline(app, admin_client) -> None:
    """管理器经 Fun-ASR 适配器提交、查询、下载并落库：结果 JSON 存盘再读回，说话人编号映射成 S1、S2。"""
    settings = admin_client.get("/api/admin/settings").json()
    resp = admin_client.put("/api/admin/settings", json={**settings, "public_base_url": "https://site.example"})
    assert resp.status_code == 200, resp.text
    resp = admin_client.post(
        "/api/admin/asr/providers",
        json={
            "kind": "aliyun_funasr",
            "name": "百炼",
            "config": {"base_url": "https://asr.example/api/v1"},
            "secrets": {"api_key": KEY},
        },
    )
    assert resp.status_code == 201, resp.text
    provider_id = resp.json()["id"]

    install_fake_llm(app, lambda messages, payload: "")
    manager = app.state.ctx.meetings
    llm = manager.transport
    assert isinstance(llm, httpx.MockTransport)
    submitted: list[dict[str, Any]] = []
    result_json = load("funasr_result_diarized.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(RESULT_URL_PREFIX):
            return httpx.Response(200, json=result_json)
        if request.url.host != "asr.example":
            return llm.handler(request)
        if request.method == "POST":
            submitted.append(json.loads(request.content))
            return httpx.Response(200, json=load("funasr_submit.json"))
        return httpx.Response(200, json=load("funasr_task_succeeded.json"))

    transport = httpx.MockTransport(handler)
    manager.transport = transport
    manager.http = httpx.AsyncClient(transport=transport)

    meeting = upload_audio(admin_client, make_wav(25), provider_id=provider_id, expected_speakers=2)
    mid = meeting["id"]
    # 只关心识别这一步；之后的大模型整理由别的测试覆盖
    meeting = wait_meeting(admin_client, mid, {"processing", "done", "failed", "canceled"})
    assert [p["state"] for p in meeting["parts"]] == ["done"], meeting.get("error")
    assert len(submitted) == 1
    body = submitted[0]
    assert body["input"]["file_urls"][0].startswith(f"https://site.example/api/public/meeting-audio/{mid}/0/")
    assert body["parameters"] == {"diarization_enabled": True, "language_hints": ["zh"], "speaker_count": 2}

    stored = json.loads((manager.meeting_dir(mid) / "asr-0.json").read_text("utf-8"))
    assert stored == [result_json]
    segments = admin_client.get(f"/api/meetings/{mid}/segments").json()
    assert [(s["speaker"], s["raw_text"]) for s in segments] == [
        ("S1", "大家好，今天先过一下进度。"),
        ("S2", "我这周把风场数据重新清洗了一遍。"),
        ("S1", "好，下周把对比图发给我。"),
    ]
    assert segments[1]["start_ms"] == 4600 and segments[1]["end_ms"] == 11250
