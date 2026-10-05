from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.meeting.asr.base import AsrError, AsrSegment, SubmitOptions
from app.meeting.asr.tencent import (
    TencentMeetingAdapter,
    canonical_request,
    hotword_list,
    string_to_sign,
    tc3_headers,
    tc3_signature,
)

FIXTURES = Path(__file__).parent / "fixtures" / "asr"
SECRET_ID = "AKIDexample0000"
FAKE_KEY = "example-secret-key-not-real"
SECRETS = {"secret_id": SECRET_ID, "secret_key": FAKE_KEY}
# 签名已知答案：官方文档的示例把密钥打了码，只能逐步核对规范请求的哈希和待签字符串；最终签名是用腾讯云官方
# Python SDK（tencentcloud-sdk-python-common 的 Sign.sign_tc3）对同一待签字符串和上面的假密钥算出来的。
DOC_SIGNATURE = "52ea94d798a501ed05963782bc9a0762bace7d2fbb8923a049eb9d63f85edcc3"
# 对 asr 服务、DescribeTaskStatus、正文 {"TaskId":1}、时间戳 1700000000（UTC 2023-11-14）的签名
ASR_SIGNATURE = "5e8129aebc77a70caa794e5bd3ad94a03024cf5f7d27141349500ac3a46350d4"
ASR_AUTHORIZATION = (
    f"TC3-HMAC-SHA256 Credential={SECRET_ID}/2023-11-14/asr/tc3_request, "
    f"SignedHeaders=content-type;host;x-tc-action, Signature={ASR_SIGNATURE}"
)


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text("utf-8"))


def api_error(code: str, message: str = "示例错误") -> dict[str, Any]:
    return {"Response": {"Error": {"Code": code, "Message": message}, "RequestId": "req-0001"}}


class Server:
    """按顺序返回预设的应答（最后一个重复使用），记下收到的请求。"""

    def __init__(self, *replies: dict[str, Any] | httpx.Response | Exception):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)

    def body(self, i: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[i].content)


def run(
    server: Server,
    call: Callable[[TencentMeetingAdapter], Awaitable[Any]],
    *,
    config: dict[str, Any] | None = None,
    secrets: dict[str, str] | None = None,
) -> Any:
    async def main() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
            adapter = TencentMeetingAdapter(config or {}, dict(SECRETS if secrets is None else secrets), client)
            adapter.now = lambda: 1700000000
            return await call(adapter)

    return asyncio.run(main())


# ---------- 签名 ----------


def test_signature_matches_official_example_step_by_step():
    # 文档示例的正文是默认 json.dumps 的输出（中文转成 \u 转义、逗号冒号后带空格）
    payload = json.dumps({"Limit": 1, "Filters": [{"Values": ["未命名"], "Name": "instance-name"}]}).encode()
    assert hashlib.sha256(payload).hexdigest() == "35e9c5b0e3ae67532d3c9f17ead6c90222632e5b1ff7f6e89887f1398934f064"

    canonical = canonical_request("cvm.tencentcloudapi.com", "DescribeInstances", payload)
    assert canonical == (
        "POST\n/\n\n"
        "content-type:application/json; charset=utf-8\n"
        "host:cvm.tencentcloudapi.com\n"
        "x-tc-action:describeinstances\n\n"
        "content-type;host;x-tc-action\n"
        "35e9c5b0e3ae67532d3c9f17ead6c90222632e5b1ff7f6e89887f1398934f064"
    )
    assert (
        hashlib.sha256(canonical.encode()).hexdigest()
        == "7019a55be8395899b900fb5564e4200d984910f34794a27cb3fb7d10ff6a1e84"
    )

    to_sign = string_to_sign(1551113065, "cvm", canonical)
    assert to_sign == (
        "TC3-HMAC-SHA256\n1551113065\n2019-02-25/cvm/tc3_request\n"
        "7019a55be8395899b900fb5564e4200d984910f34794a27cb3fb7d10ff6a1e84"
    )
    assert tc3_signature(FAKE_KEY, "2019-02-25", "cvm", to_sign) == DOC_SIGNATURE


def test_request_headers_known_answer():
    headers = tc3_headers(
        SECRET_ID, FAKE_KEY, action="DescribeTaskStatus", payload=b'{"TaskId":1}', timestamp=1700000000
    )
    assert headers["Authorization"] == ASR_AUTHORIZATION
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["Host"] == "asr.tencentcloudapi.com"
    assert headers["X-TC-Action"] == "DescribeTaskStatus"
    assert headers["X-TC-Version"] == "2019-06-14"
    assert headers["X-TC-Timestamp"] == "1700000000"
    assert "X-TC-Region" not in headers
    assert tc3_headers("a", "b", action="X", payload=b"{}", timestamp=1, region="ap-guangzhou")["X-TC-Region"] == (
        "ap-guangzhou"
    )


def test_adapter_sends_signed_request_end_to_end():
    server = Server(api_error("FailedOperation.NoSuchTask", "task not exist"))
    result = run(server, lambda a: a.check_credentials())
    assert result.ok and result.message == "密钥有效"

    req = server.requests[0]
    assert req.method == "POST" and str(req.url) == "https://asr.tencentcloudapi.com"
    # 签名覆盖的正文必须与发送的逐字节一致：紧凑 JSON，TaskId 是整数
    assert req.content == b'{"TaskId":1}'
    assert req.headers["Authorization"] == ASR_AUTHORIZATION
    assert req.headers["Content-Type"] == "application/json; charset=utf-8"
    assert req.headers["Host"] == "asr.tencentcloudapi.com"
    assert req.headers["X-TC-Action"] == "DescribeTaskStatus"
    assert req.headers["X-TC-Version"] == "2019-06-14"
    assert req.headers["X-TC-Timestamp"] == "1700000000"
    assert req.headers["X-TC-Region"] == "ap-shanghai"  # 没配置时用默认地域


# ---------- 提交 ----------


def test_submit_request_body():
    server = Server(fixture("tencent_create.json"))
    opts = SubmitOptions(expected_speakers=4, hotwords=["风电功率", "TCSF-Net", "风电功率", "a|b,c"])
    task_id = run(server, lambda a: a.submit("https://example.org/a.mp3", opts), config={"region": "ap-guangzhou"})
    assert task_id == "1000000286"

    req = server.requests[0]
    assert req.headers["X-TC-Action"] == "CreateRecTask"
    assert req.headers["X-TC-Region"] == "ap-guangzhou"
    assert server.body() == {
        "EngineModelType": "16k_zh_en_meeting",
        "ChannelNum": 1,
        "SpeakerDiarization": 1,
        "SpeakerNumber": 0,  # 16k 引擎不能指定人数，即使用户填了预计人数
        "ResTextFormat": 2,
        "SourceType": 0,
        "Url": "https://example.org/a.mp3",
        "ConvertNumMode": 1,
        "HotwordList": "风电功率|10,TCSF-Net|10,abc|10",
    }
    # 中文热词直接以 UTF-8 发送，签名按同一串字节计算
    assert "风电功率".encode() in req.content


def test_submit_uses_configured_engine_and_omits_empty_hotwords():
    server = Server(fixture("tencent_create.json"))
    run(server, lambda a: a.submit("https://example.org/a.mp3", SubmitOptions()), config={"engine": "16k_zh_en_2.0"})
    body = server.body()
    assert body["EngineModelType"] == "16k_zh_en_2.0"
    assert "HotwordList" not in body


def test_submit_without_task_id_is_provider_error():
    server = Server({"Response": {"Data": {}, "RequestId": "r"}})
    with pytest.raises(AsrError) as e:
        run(server, lambda a: a.submit("https://example.org/a.mp3", SubmitOptions()))
    assert e.value.kind == "provider"


def test_hotword_list_limits():
    assert hotword_list([]) == ""
    assert hotword_list(["  ", "|", ",", " 风电 "]) == "风电|10"
    # 文档：单个热词不超过 30 个字符（最多 10 个汉字），超长的整个跳过而不是截断
    ten = "一二三四五六七八九十"
    assert hotword_list([ten, ten + "一", "x" * 31, "y" * 30]) == f"{ten}|10,{'y' * 30}|10"
    words = [f"词{i}" for i in range(200)]
    entries = hotword_list(words).split(",")
    assert len(entries) == 128
    assert entries[0] == "词0|10" and entries[-1] == "词127|10"


# ---------- 查询 ----------


@pytest.mark.parametrize("name", ["tencent_status_waiting.json", "tencent_status_doing.json"])
def test_poll_pending(name):
    server = Server(fixture(name))
    result = run(server, lambda a: a.poll("522931820"))
    assert result.state == "pending" and result.progress is None
    assert server.body() == {"TaskId": 522931820}
    assert server.requests[0].headers["X-TC-Action"] == "DescribeTaskStatus"


def test_poll_done_keeps_whole_data_object():
    data = fixture("tencent_status_success.json")
    server = Server(data)
    result = run(server, lambda a: a.poll("9266418"))
    assert result.state == "done"
    assert result.raw == data["Response"]["Data"]
    # 管理器把 raw 存成 JSON 文件再读回来解析
    stored = json.loads(json.dumps(result.raw, ensure_ascii=False))
    assert TencentMeetingAdapter.parse(stored) == [AsrSegment(20, 2380, "0", "腾讯云语音识别欢迎您。")]


def test_poll_failed_download_points_to_public_url():
    result = run(Server(fixture("tencent_status_failed.json")), lambda a: a.poll("522931820"))
    assert result.state == "failed" and result.error_kind == "config"
    assert "公网地址" in (result.error or "") and "Failed to download audio file!" in (result.error or "")


def test_poll_failed_other_reason():
    data = fixture("tencent_status_failed.json")
    data["Response"]["Data"]["ErrorMsg"] = "audio decode failed"
    result = run(Server(data), lambda a: a.poll("522931820"))
    assert result.state == "failed" and result.error_kind == "provider"
    assert "audio decode failed" in (result.error or "")


def test_poll_unknown_status_raises():
    data = fixture("tencent_status_doing.json")
    data["Response"]["Data"]["Status"] = 7
    with pytest.raises(AsrError) as e:
        run(Server(data), lambda a: a.poll("522931820"))
    assert e.value.kind == "provider"


def test_poll_expired_task_fails_without_retry():
    result = run(Server(api_error("FailedOperation.NoSuchTask")), lambda a: a.poll("522931820"))
    assert result.state == "failed" and "24 小时" in (result.error or "")


def test_poll_invalid_task_id_skips_request():
    server = Server(fixture("tencent_status_doing.json"))
    result = run(server, lambda a: a.poll("not-a-number"))
    assert result.state == "failed" and result.error_kind == "input"
    assert server.requests == []


def test_poll_auth_error_raises():
    with pytest.raises(AsrError) as e:
        run(Server(fixture("tencent_error_auth.json")), lambda a: a.poll("1"))
    assert e.value.kind == "auth" and not e.value.retryable


# ---------- 错误码 ----------


@pytest.mark.parametrize(
    ("code", "kind", "retryable"),
    [
        ("AuthFailure.SignatureFailure", "auth", False),
        ("AuthFailure.SecretIdNotFound", "auth", False),
        ("AuthFailure.SignatureExpire", "auth", False),
        ("AuthFailure.UnauthorizedOperation", "auth", False),
        ("UnauthorizedOperation", "auth", False),
        ("FailedOperation.CheckAuthInfoFailed", "auth", False),
        ("IpNotInWhitelist", "auth", False),
        ("RequestLimitExceeded", "quota", True),
        ("RequestLimitExceeded.UinLimitExceeded", "quota", True),
        ("LimitExceeded", "quota", False),
        ("FailedOperation.ServiceIsolate", "quota", False),
        ("FailedOperation.UserHasNoAmount", "quota", False),
        ("FailedOperation.UserHasNoFreeAmount", "quota", False),
        ("InvalidParameter", "input", False),
        ("InvalidParameterValue.ErrorInvalidUrl", "input", False),
        ("MissingParameter", "input", False),
        ("UnknownParameter", "input", False),
        ("FailedOperation.ErrorDownFile", "config", False),
        ("InternalError.ErrorDownFile", "config", False),
        ("FailedOperation.UserNotRegistered", "config", False),
        ("FailedOperation.ErrorRecognize", "provider", True),
        ("InternalError", "provider", True),
        ("ServiceUnavailable", "provider", True),
    ],
)
def test_error_code_mapping(code, kind, retryable):
    with pytest.raises(AsrError) as e:
        run(Server(api_error(code, "detail message")), lambda a: a.submit("https://example.org/a.mp3", SubmitOptions()))
    assert e.value.kind == kind
    assert e.value.retryable is retryable
    message = str(e.value)
    assert code in message and "detail message" in message and "req-0001" in message


def test_signature_expire_mentions_clock():
    with pytest.raises(AsrError) as e:
        run(Server(api_error("AuthFailure.SignatureExpire")), lambda a: a.poll("1"))
    assert "服务器时间" in str(e.value)


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (httpx.Response(502, text="bad gateway"), "provider"),
        (httpx.Response(403, text="forbidden"), "auth"),
        (httpx.Response(200, text="<html>not json</html>"), "provider"),
        (httpx.Response(200, json={"unexpected": True}), "provider"),
    ],
)
def test_http_level_errors(response, kind):
    with pytest.raises(AsrError) as e:
        run(Server(response), lambda a: a.submit("https://example.org/a.mp3", SubmitOptions()))
    assert e.value.kind == kind


# ---------- 凭据检查 ----------


@pytest.mark.parametrize(
    ("reply", "ok", "fragment"),
    [
        (fixture("tencent_error_auth.json"), False, "鉴权失败"),
        (api_error("AuthFailure.SecretIdNotFound"), False, "SecretIdNotFound"),
        (api_error("FailedOperation.ServiceIsolate"), False, "欠费"),
        (api_error("FailedOperation.UserNotRegistered"), False, "未开通"),
        (api_error("FailedOperation.NoSuchTask"), True, "密钥有效"),
        (api_error("InvalidParameter"), True, "密钥有效"),
        (fixture("tencent_status_failed.json"), True, "密钥有效"),
        (httpx.Response(502, text="bad gateway"), False, "502"),
        (httpx.ConnectError("refused"), False, "连不上腾讯云"),
    ],
)
def test_check_credentials(reply, ok, fragment):
    result = run(Server(reply), lambda a: a.check_credentials())
    assert result.ok is ok
    assert fragment in result.message


def test_check_credentials_without_keys_skips_request():
    server = Server(api_error("FailedOperation.NoSuchTask"))
    result = run(server, lambda a: a.check_credentials(), secrets={"secret_id": "AKIDx"})
    assert not result.ok and server.requests == []


# ---------- 解析 ----------


def test_parse_meeting_result():
    data = fixture("tencent_status_meeting.json")["Response"]["Data"]
    segments = TencentMeetingAdapter.parse(data)
    assert segments == [
        AsrSegment(480, 5120, "0", "大家好，今天我们过一下进展。"),
        AsrSegment(5900, 11300, "1", "我先说，风场数据已经重新清洗了一遍。"),
        AsrSegment(12000, 15200, "0", "好的。"),
        AsrSegment(16100, 21400, "2", "模型误差比上次低了 8%。"),
    ]


def test_parse_tolerates_wrapper_and_empty_results():
    wrapped = fixture("tencent_status_success.json")
    assert TencentMeetingAdapter.parse(wrapped) == TencentMeetingAdapter.parse(wrapped["Response"]["Data"])
    assert TencentMeetingAdapter.parse(fixture("tencent_status_failed.json")["Response"]["Data"]) == []
    assert TencentMeetingAdapter.parse({"ResultDetail": None}) == []
    assert TencentMeetingAdapter.parse({}) == []
    # 关闭说话人分离时没有 SpeakerId，统一记成 0 号
    assert TencentMeetingAdapter.parse({"ResultDetail": [{"FinalSentence": "你好", "StartMs": 1, "EndMs": 2}]}) == [
        AsrSegment(1, 2, "0", "你好")
    ]
    detail = [{"FinalSentence": "你好", "StartMs": 1, "EndMs": 2, "SpeakerId": None}]
    assert TencentMeetingAdapter.parse({"ResultDetail": detail})[0].speaker == "0"
