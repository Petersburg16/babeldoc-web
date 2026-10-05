import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from app.meeting.asr.base import AsrError, AsrSegment, SubmitOptions
from app.meeting.asr.tingwu import (
    PROBE_TASK_ID,
    TingwuAdapter,
    canonical_request,
    signed_headers,
)

FIXTURES = Path(__file__).parent / "fixtures" / "asr"
HOST = "tingwu.cn-beijing.aliyuncs.com"
TASK_ID = "e8adc0b3bc4b42d898fcadb0a1710635"
RESULT_URL = "http://speech-swap.oss-cn-zhangjiakou.aliyuncs.com/tingwu/output/transcription.json"
# 阿里云签名文档里的示例密钥，不是真的
SECRETS = {"access_key_id": "YourAccessKeyId", "access_key_secret": "YourAccessKeySecret"}
CONFIG = {"app_key": "test-app-key"}


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text("utf-8"))


def run(handler, fn, *, config=None, secrets=None):
    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = TingwuAdapter(
                CONFIG if config is None else config, SECRETS if secrets is None else secrets, client
            )
            return await fn(adapter)

    return asyncio.run(main())


def assert_signed(request: httpx.Request, action: str) -> None:
    """用请求自己的 date、nonce 重算一遍签名，和发出去的 Authorization 必须一致。"""
    assert request.headers["host"] == HOST
    assert request.headers["x-acs-action"] == action
    assert request.headers["x-acs-version"] == "2023-09-30"
    assert request.headers["x-acs-content-sha256"] == hashlib.sha256(request.content).hexdigest()
    query = dict(request.url.params)
    expected = signed_headers(
        request.method,
        HOST,
        request.url.path,
        query,
        request.content,
        action=action,
        key_id=SECRETS["access_key_id"],
        key_secret=SECRETS["access_key_secret"],
        content_type=request.headers.get("content-type"),
        date=request.headers["x-acs-date"],
        nonce=request.headers["x-acs-signature-nonce"],
    )
    assert request.headers["authorization"] == expected["Authorization"]


# ---------- 签名 ----------


def test_signature_matches_official_example():
    """阿里云“V3 版本请求体&签名机制”文档里的固定示例。"""
    empty = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    query = {"ImageId": "win2019_1809_x64_dtc_zh-cn_40G_alibase_20230811.vhd", "RegionId": "cn-shanghai"}
    headers = signed_headers(
        "POST",
        "ecs.cn-shanghai.aliyuncs.com",
        "/",
        query,
        b"",
        action="RunInstances",
        version="2014-05-26",
        key_id="YourAccessKeyId",
        key_secret="YourAccessKeySecret",
        date="2023-10-26T10:22:32Z",
        nonce="3156853299f313e23d1673dc12e1703d",
    )
    text, signed = canonical_request("POST", "/", query, headers, empty)
    assert text == (
        "POST\n/\n"
        "ImageId=win2019_1809_x64_dtc_zh-cn_40G_alibase_20230811.vhd&RegionId=cn-shanghai\n"
        "host:ecs.cn-shanghai.aliyuncs.com\n"
        "x-acs-action:RunInstances\n"
        f"x-acs-content-sha256:{empty}\n"
        "x-acs-date:2023-10-26T10:22:32Z\n"
        "x-acs-signature-nonce:3156853299f313e23d1673dc12e1703d\n"
        "x-acs-version:2014-05-26\n"
        "\n"
        "host;x-acs-action;x-acs-content-sha256;x-acs-date;x-acs-signature-nonce;x-acs-version\n"
        f"{empty}"
    )
    assert hashlib.sha256(text.encode()).hexdigest() == (
        "7ea06492da5221eba5297e897ce16e55f964061054b7695beedaac1145b1e259"
    )
    assert headers["Authorization"] == (
        "ACS3-HMAC-SHA256 Credential=YourAccessKeyId,"
        "SignedHeaders=host;x-acs-action;x-acs-content-sha256;x-acs-date;x-acs-signature-nonce;x-acs-version,"
        "Signature=06563a9e1b43f5dfe96b81484da74bceab24a1d853912eee15083a6f0f3283c0"
    )


def test_canonical_query_uses_rfc3986():
    text, _ = canonical_request("GET", "/", {"b": "a b*~", "a": "中"}, {"host": "x"}, "h")
    assert text.split("\n")[2] == "a=%E4%B8%AD&b=a%20b%2A~"


# ---------- 提交 ----------


@pytest.mark.parametrize(
    ("language", "expected", "source", "count"),
    [("zh", 4, "cn", 4), ("en", None, "en", 0), ("auto", None, "auto", 0)],
)
def test_submit_sends_signed_create_task(language, expected, source, count):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=fixture("tingwu_create_task.json"))

    opts = SubmitOptions(language=language, expected_speakers=expected)
    task_id = run(handler, lambda a: a.submit("https://example.org/a.mp3", opts))
    assert task_id == TASK_ID
    (request,) = seen
    assert request.method == "PUT"
    assert str(request.url) == f"https://{HOST}/openapi/tingwu/v2/tasks?type=offline"
    assert request.headers["content-type"] == "application/json"
    assert_signed(request, "CreateTask")
    body = json.loads(request.content)
    assert body["AppKey"] == "test-app-key"
    assert body["Input"]["SourceLanguage"] == source
    assert body["Input"]["FileUrl"] == "https://example.org/a.mp3"
    assert body["Input"]["TaskKey"].startswith("bdw-")
    assert body["Parameters"] == {"Transcription": {"DiarizationEnabled": True, "Diarization": {"SpeakerCount": count}}}


def test_submit_requires_app_key_and_access_key():
    def handler(request):
        raise AssertionError("不该发请求")

    with pytest.raises(AsrError) as e:
        run(handler, lambda a: a.submit("https://example.org/a.mp3", SubmitOptions()), config={})
    assert e.value.kind == "config"
    with pytest.raises(AsrError) as e:
        run(handler, lambda a: a.submit("https://example.org/a.mp3", SubmitOptions()), secrets={})
    assert e.value.kind == "config"


def test_submit_without_task_id_is_provider_error():
    def handler(request):
        return httpx.Response(200, json={"Code": "0", "Data": {}, "RequestId": "r"})

    with pytest.raises(AsrError) as e:
        run(handler, lambda a: a.submit("https://example.org/a.mp3", SubmitOptions()))
    assert e.value.kind == "provider" and not e.value.retryable


def test_custom_endpoint_port_is_signed_as_sent():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=fixture("tingwu_create_task.json"))

    config = {**CONFIG, "endpoint": "http://127.0.0.1:8080/"}
    run(handler, lambda a: a.submit("https://example.org/a.mp3", SubmitOptions()), config=config)
    assert seen[0].headers["host"] == "127.0.0.1:8080"
    assert str(seen[0].url) == "http://127.0.0.1:8080/openapi/tingwu/v2/tasks?type=offline"


# ---------- 查询 ----------


def test_poll_ongoing_is_pending():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=fixture("tingwu_task_ongoing.json"))

    result = run(handler, lambda a: a.poll(TASK_ID))
    assert result.state == "pending" and result.progress is None
    (request,) = seen
    assert request.method == "GET"
    assert str(request.url) == f"https://{HOST}/openapi/tingwu/v2/tasks/{TASK_ID}"
    assert "content-type" not in request.headers
    assert request.content == b""
    assert_signed(request, "GetTaskInfo")


def test_poll_completed_downloads_transcription():
    transcription = fixture("tingwu_transcription.json")
    downloads: list[httpx.Request] = []

    def handler(request):
        if request.url.host == HOST:
            return httpx.Response(200, json=fixture("tingwu_task_completed.json"))
        assert str(request.url) == RESULT_URL
        downloads.append(request)
        return httpx.Response(200, json=transcription)

    result = run(handler, lambda a: a.poll(TASK_ID))
    assert result.state == "done"
    assert result.raw == transcription
    # 结果链接是 OSS 临时地址，不能把听悟的签名带过去
    assert "authorization" not in downloads[0].headers
    assert not any(k.startswith("x-acs-") for k in downloads[0].headers)


def test_poll_completed_without_result_fails():
    def handler(request):
        data = fixture("tingwu_task_completed.json")
        data["Data"]["Result"] = {}
        return httpx.Response(200, json=data)

    result = run(handler, lambda a: a.poll(TASK_ID))
    assert result.state == "failed" and "没有返回转写结果" in result.error


def test_poll_expired_result_link_is_not_retried():
    def handler(request):
        if request.url.host == HOST:
            return httpx.Response(200, json=fixture("tingwu_task_completed.json"))
        return httpx.Response(403, text="<Error>AccessDenied</Error>")

    with pytest.raises(AsrError) as e:
        run(handler, lambda a: a.poll(TASK_ID))
    assert e.value.kind == "provider" and not e.value.retryable


def test_poll_failed_maps_error_code():
    def handler(request):
        return httpx.Response(200, json=fixture("tingwu_task_failed.json"))

    result = run(handler, lambda a: a.poll(TASK_ID))
    assert result.state == "failed"
    assert result.error_kind == "input"
    assert "TSC.AudioFileLink" in result.error and "公网地址" in result.error


def test_poll_invalid_task_fails():
    def handler(request):
        return httpx.Response(200, json={"Code": "0", "Data": {"TaskId": TASK_ID, "TaskStatus": "INVALID"}})

    result = run(handler, lambda a: a.poll(TASK_ID))
    assert result.state == "failed" and "无效" in result.error


def test_poll_unknown_status_raises_retryable():
    def handler(request):
        return httpx.Response(200, json={"Code": "0", "Data": {"TaskId": TASK_ID, "TaskStatus": "PAUSED"}})

    with pytest.raises(AsrError) as e:
        run(handler, lambda a: a.poll(TASK_ID))
    assert e.value.kind == "provider" and e.value.retryable


# ---------- 错误映射 ----------


@pytest.mark.parametrize(
    ("status", "code", "kind", "retryable"),
    [
        (404, "InvalidAccessKeyId.NotFound", "auth", False),
        (400, "InvalidAccessKeyId.Inactive", "auth", False),
        (400, "SignatureDoesNotMatch", "auth", False),
        (400, "IncompleteSignature", "auth", False),
        (403, "Forbidden.RAM", "auth", False),
        (400, "BRK.InvalidAppKey", "auth", False),
        (400, "BRK.ServiceLinkedRoleNotExist", "auth", False),
        (400, "BRK.InvalidTenant", "quota", False),
        (400, "BRK.InvalidService", "quota", False),
        (400, "BRK.OverdueTenant", "quota", False),
        (400, "BRK.OverdueService", "quota", False),
        (400, "Throttling.User", "quota", True),
        (400, "InvalidTimeStamp.Expired", "config", False),
        (400, "BRK.InvalidLanguage", "input", False),
        (400, "SignatureNonceUsed", "provider", True),
        (500, "ServerError", "provider", True),
        (503, "ServiceUnavailable", "provider", True),
        # 听悟也可能用 HTTP 200 带非零 Code 报错
        (200, "BRK.InvalidTenant", "quota", False),
    ],
)
def test_error_codes_are_classified_by_body_code(status, code, kind, retryable):
    def handler(request):
        return httpx.Response(status, json={"RequestId": "REQ-1", "Code": code, "Message": "something"})

    with pytest.raises(AsrError) as e:
        run(handler, lambda a: a.submit("https://example.org/a.mp3", SubmitOptions()))
    assert e.value.kind == kind
    assert e.value.retryable is retryable
    assert code in str(e.value) and "REQ-1" in str(e.value)


def test_auth_fixture_at_http_404_is_auth_error():
    def handler(request):
        return httpx.Response(404, json=fixture("tingwu_error_auth.json"))

    with pytest.raises(AsrError) as e:
        run(handler, lambda a: a.poll(TASK_ID))
    assert e.value.kind == "auth" and "AccessKey ID 不存在" in str(e.value)


@pytest.mark.parametrize(("status", "kind"), [(502, "provider"), (404, "config"), (403, "auth"), (200, "provider")])
def test_non_json_responses(status, kind):
    def handler(request):
        return httpx.Response(status, text="<html>oops</html>")

    with pytest.raises(AsrError) as e:
        run(handler, lambda a: a.poll(TASK_ID))
    assert e.value.kind == kind


# ---------- 检查密钥 ----------


def test_check_credentials_ok_when_probe_task_is_invalid():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"Code": "0", "Data": {"TaskId": PROBE_TASK_ID, "TaskStatus": "INVALID"}})

    result = run(handler, lambda a: a.check_credentials())
    assert result.ok and "AppKey" in result.message
    assert seen[0].url.path == f"/openapi/tingwu/v2/tasks/{PROBE_TASK_ID}"
    assert_signed(seen[0], "GetTaskInfo")


def test_check_credentials_ok_when_task_not_found_error():
    # 不存在的任务号具体回什么码文档没写；参数类错误都说明签名和账号已经通过
    def handler(request):
        return httpx.Response(400, json={"RequestId": "r", "Code": "TSC.TaskNotFound", "Message": "not found"})

    assert run(handler, lambda a: a.check_credentials()).ok


@pytest.mark.parametrize(
    ("status", "code", "text"),
    [
        (404, "InvalidAccessKeyId.NotFound", "AccessKey ID 不存在"),
        (400, "SignatureDoesNotMatch", "AccessKey Secret"),
        (400, "BRK.InvalidService", "没有开通"),
        (400, "InvalidTimeStamp.Expired", "时钟"),
        (500, "ServerError", "暂时无法验证"),
        (400, "Throttling.User", "暂时无法验证"),
    ],
)
def test_check_credentials_reports_failures(status, code, text):
    def handler(request):
        return httpx.Response(status, json={"RequestId": "r", "Code": code, "Message": "m"})

    result = run(handler, lambda a: a.check_credentials())
    assert not result.ok and text in result.message


def test_check_credentials_requires_fields():
    def handler(request):
        raise AssertionError("不该发请求")

    result = run(handler, lambda a: a.check_credentials(), config={}, secrets={})
    assert not result.ok
    assert "AccessKey ID" in result.message and "AppKey" in result.message


@pytest.mark.parametrize("endpoint", ["tingwu.cn-beijing.aliyuncs.com", "http://[::1"])
def test_bad_endpoint_is_config_error(endpoint):
    def handler(request):
        raise AssertionError("不该发请求")

    result = run(handler, lambda a: a.check_credentials(), config={**CONFIG, "endpoint": endpoint})
    assert not result.ok and "接口地址" in result.message


def test_check_credentials_network_error():
    def handler(request):
        raise httpx.ConnectError("refused")

    result = run(handler, lambda a: a.check_credentials())
    assert not result.ok and "连不上" in result.message


# ---------- 解析 ----------


def test_parse_joins_words_into_sentences():
    segments = TingwuAdapter.parse(fixture("tingwu_transcription.json"))
    assert segments == [
        AsrSegment(4970, 6900, "1", "您好，我是张老师。"),
        AsrSegment(7200, 9300, "1", "我们用Python 3跑一下。"),
        AsrSegment(9800, 10900, "2", "好的，没问题。"),
        AsrSegment(11000, 12000, "2", "Let's see, OK."),
        AsrSegment(12700, 13900, "1", "下周交v2版本。"),
    ]


def test_parse_tolerates_missing_fields():
    assert TingwuAdapter.parse(None) == []
    assert TingwuAdapter.parse({"Transcription": {}}) == []
    raw = {
        "Paragraphs": [
            {
                "Words": [
                    {"SentenceId": 1, "Start": 100, "End": 300, "Text": "hello"},
                    {"SentenceId": 1, "Text": "world"},
                ]
            }
        ]
    }
    assert TingwuAdapter.parse(raw) == [AsrSegment(100, 300, "0", "hello world")]
