"""腾讯云录音文件识别（CreateRecTask），默认用会议引擎 16k_zh_en_meeting，开启说话人分离。"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from .base import (
    AsrAdapter,
    AsrError,
    AsrSegment,
    Capability,
    CheckResult,
    ErrorKind,
    FieldSpec,
    PollResult,
    SubmitOptions,
    http_error,
)

HOST = "asr.tencentcloudapi.com"
ENDPOINT = f"https://{HOST}"
SERVICE = "asr"
VERSION = "2019-06-14"
# 签名里的 Content-Type 必须与实际发送的逐字节一致
CONTENT_TYPE = "application/json; charset=utf-8"
MAX_HOTWORDS = 128
HOTWORD_WEIGHT = 10
HOTWORD_MAX_BYTES = 30  # 文档：单个热词不超过 30 个字符（最多 10 个汉字），按 UTF-8 字节计
DOWNLOAD_HINT = "腾讯云下载不到录音：请确认站点公网地址能从外网直接下载（不能被人机验证等拦截）"


def canonical_request(host: str, action: str, payload: bytes) -> str:
    """规范请求串：POST、无查询串，签 content-type、host、x-tc-action 三个头（值转小写）。"""
    headers = f"content-type:{CONTENT_TYPE}\nhost:{host}\nx-tc-action:{action.lower()}\n"
    return "\n".join(["POST", "/", "", headers, "content-type;host;x-tc-action", hashlib.sha256(payload).hexdigest()])


def string_to_sign(timestamp: int, service: str, canonical: str) -> str:
    date = datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d")
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"TC3-HMAC-SHA256\n{timestamp}\n{date}/{service}/tc3_request\n{digest}"


def tc3_signature(secret_key: str, date: str, service: str, to_sign: str) -> str:
    def mac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    signing = mac(mac(mac(("TC3" + secret_key).encode("utf-8"), date), service), "tc3_request")
    return hmac.new(signing, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


def tc3_headers(
    secret_id: str,
    secret_key: str,
    *,
    action: str,
    payload: bytes,
    timestamp: int,
    region: str = "",
    host: str = HOST,
    service: str = SERVICE,
    version: str = VERSION,
) -> dict[str, str]:
    """签名方法 v3（TC3-HMAC-SHA256）的全部请求头；payload 必须就是随后发送的那串字节。"""
    date = datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d")
    to_sign = string_to_sign(timestamp, service, canonical_request(host, action, payload))
    signature = tc3_signature(secret_key, date, service, to_sign)
    headers = {
        "Content-Type": CONTENT_TYPE,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Version": version,
        "X-TC-Timestamp": str(timestamp),
        "Authorization": (
            f"TC3-HMAC-SHA256 Credential={secret_id}/{date}/{service}/tc3_request, "
            f"SignedHeaders=content-type;host;x-tc-action, Signature={signature}"
        ),
    }
    if region:
        headers["X-TC-Region"] = region
    return headers


def hotword_list(words: list[str]) -> str:
    """临时热词“词|权重”用逗号连接；| 和 , 是分隔符，从词里去掉；超长的词整个跳过（截断会变成别的词）。"""
    out: list[str] = []
    seen: set[str] = set()
    for word in words:
        # 文档：热词不能含标点、特殊字符和空格；TCSF-Net 这类词去掉连字符后仍能命中发音
        w = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", word)
        if not w or w in seen or len(w.encode("utf-8")) > HOTWORD_MAX_BYTES:
            continue
        seen.add(w)
        out.append(f"{w}|{HOTWORD_WEIGHT}")
        if len(out) >= MAX_HOTWORDS:
            break
    return ",".join(out)


def _classify(code: str) -> tuple[ErrorKind, bool | None, str]:
    """错误码 → (类型, 是否可重试, 提示)。列表见“语音识别 错误码”文档；没列到的按服务商错误处理。"""
    if code == "AuthFailure.SignatureExpire":
        return "auth", None, "腾讯云签名过期：服务器时间与标准时间相差超过 5 分钟"
    if code in ("IpInBlacklist", "IpNotInWhitelist"):
        return "auth", None, "腾讯云拒绝了本服务器的 IP：该密钥或账号限制了来源 IP"
    if code.startswith(("AuthFailure", "UnauthorizedOperation")) or code == "FailedOperation.CheckAuthInfoFailed":
        return "auth", None, "腾讯云鉴权失败：请检查 SecretId、SecretKey，以及该密钥是否有语音识别权限"
    if code.startswith("RequestLimitExceeded"):
        return "quota", True, "腾讯云请求过于频繁"
    if code.startswith(("LimitExceeded", "ResourceInsufficient")) or code in (
        "FailedOperation.ServiceIsolate",
        "FailedOperation.UserHasNoAmount",
        "FailedOperation.UserHasNoFreeAmount",
    ):
        return "quota", None, "腾讯云账号欠费或识别额度已用完"
    if code.endswith("ErrorDownFile"):
        return "config", None, DOWNLOAD_HINT
    if code in ("FailedOperation.UserNotRegistered", "UnsupportedRegion"):
        return "config", None, "腾讯云语音识别服务未开通或地域不支持，请在腾讯云控制台检查"
    if code.startswith(("InvalidParameter", "MissingParameter", "UnknownParameter", "RequestSizeLimitExceeded")):
        return "input", None, "腾讯云拒绝了请求参数"
    return "provider", None, "腾讯云接口返回错误"


class TencentApiError(AsrError):
    """接口返回的 Response.Error；保留原始错误码，调用方据此区分“任务不存在”等情况。"""

    def __init__(self, code: str, message: str, request_id: str = ""):
        kind, retryable, hint = _classify(code)
        rid = f"，RequestId {request_id}" if request_id else ""
        super().__init__(f"{hint}（{code}：{message[:300]}{rid}）", kind, retryable=retryable)
        self.code = code


class TencentMeetingAdapter(AsrAdapter):
    kind = "tencent_meeting"
    label = "腾讯云会议引擎"
    description = "录音文件识别的多人会议引擎，自动区分说话人（最多 20 人）；单个文件最长 5 小时。"
    # 官方上限 5 小时，留几分钟余量
    capability = Capability(max_part_seconds=17700, max_bytes=1024**3, hotwords=True, speaker_count=False)
    task_ttl_hours = 23  # 任务只保留 24 小时，跨天后 TaskId 可能重复
    fields = (
        FieldSpec("secret_id", "SecretId", secret=True),
        FieldSpec("secret_key", "SecretKey", secret=True),
        FieldSpec("region", "地域", default="ap-shanghai", required=False),
        FieldSpec(
            "engine",
            "识别引擎",
            default="16k_zh_en_meeting",
            options=(("16k_zh_en_meeting", "会议引擎（多人会议，推荐）"), ("16k_zh_en_2.0", "中英大模型 2.0")),
        ),
    )
    # 签名时间戳的来源；测试里换成固定值以复现已知签名
    now: Callable[[], float] = staticmethod(time.time)

    async def _call(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        """调用一个云 API，返回 Response 对象；接口错误（HTTP 200 + Response.Error）抛 AsrError。"""
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = tc3_headers(
            self.setting("secret_id"),
            self.setting("secret_key"),
            action=action,
            payload=payload,
            timestamp=int(self.now()),
            region=self.setting("region"),
        )
        resp = await self.client.post(ENDPOINT, content=payload, headers=headers)
        if resp.status_code != 200:
            raise http_error(resp, f"腾讯云 {action} ")
        try:
            data = resp.json()["Response"]
        except (ValueError, KeyError, TypeError) as e:
            raise AsrError(f"腾讯云 {action} 返回了无法解析的内容：{resp.text[:200]}", "provider") from e
        error = data.get("Error")
        if error:
            raise TencentApiError(
                str(error.get("Code", "")), str(error.get("Message", "")), str(data.get("RequestId", ""))
            )
        return data

    async def submit(self, audio_url: str, opts: SubmitOptions) -> str:
        body: dict[str, Any] = {
            "EngineModelType": self.setting("engine"),
            "ChannelNum": 1,
            "SpeakerDiarization": 1,
            "SpeakerNumber": 0,  # 16k 引擎不支持指定人数，0 为自动（最多 20 人）
            "ResTextFormat": 2,  # 0 没有 ResultDetail；2 是带标点的句子级结果
            "SourceType": 0,
            "Url": audio_url,
            "ConvertNumMode": 1,
        }
        hotwords = hotword_list(opts.hotwords)
        if hotwords:
            body["HotwordList"] = hotwords
        try:
            data = await self._call("CreateRecTask", body)
        except TencentApiError as e:
            if not hotwords or not e.code.startswith("InvalidParameter"):
                raise
            body.pop("HotwordList")
            data = await self._call("CreateRecTask", body)
        try:
            return str(int(data["Data"]["TaskId"]))
        except (KeyError, TypeError, ValueError) as e:
            raise AsrError(f"腾讯云没有返回任务号：{json.dumps(data, ensure_ascii=False)[:200]}", "provider") from e

    async def poll(self, task_id: str) -> PollResult:
        try:
            tid = int(task_id)
        except ValueError:
            return PollResult("failed", error=f"任务号无效：{task_id}", error_kind="input")
        try:
            data = await self._call("DescribeTaskStatus", {"TaskId": tid})
        except TencentApiError as e:
            if e.code == "FailedOperation.NoSuchTask":
                return PollResult("failed", error="腾讯云找不到这个任务：任务号无效或已过期（只保留 24 小时）")
            raise
        task = data.get("Data") or {}
        status = task.get("Status")
        if status in (0, 1):
            return PollResult("pending")
        if status == 2:
            return PollResult("done", raw=task)
        if status == 3:
            msg = str(task.get("ErrorMsg") or "").strip() or "未给出原因"
            if "download" in msg.lower() or "下载" in msg:
                return PollResult("failed", error=f"{DOWNLOAD_HINT}（{msg}）", error_kind="config")
            return PollResult("failed", error=f"腾讯云识别失败：{msg}")
        raise AsrError(f"腾讯云返回了未知的任务状态：{status!r}", "provider")

    @staticmethod
    def parse(raw: Any) -> list[AsrSegment]:
        if isinstance(raw, dict) and "Response" in raw:
            raw = (raw.get("Response") or {}).get("Data") or {}
        out: list[AsrSegment] = []
        for s in (raw or {}).get("ResultDetail") or []:
            text = str(s.get("FinalSentence") or "").strip()
            if not text:
                continue
            speaker = s.get("SpeakerId")
            out.append(
                AsrSegment(
                    start_ms=int(s.get("StartMs") or 0),
                    end_ms=int(s.get("EndMs") or 0),
                    speaker=str(speaker if speaker is not None else 0),
                    text=text,
                )
            )
        return out

    async def check_credentials(self) -> CheckResult:
        if not self.setting("secret_id") or not self.setting("secret_key"):
            return CheckResult(False, "还没有填写 SecretId 和 SecretKey")
        try:
            # 查一个不存在的任务：鉴权失败与“任务不存在”能区分开，而且不产生费用
            await self._call("DescribeTaskStatus", {"TaskId": 1})
        except TencentApiError as e:
            # 鉴权通过后才会报“任务不存在”“参数错误”这类业务错误
            if e.kind in ("auth", "config", "quota"):
                return CheckResult(False, str(e))
            return CheckResult(True, "密钥有效")
        except AsrError as e:
            # HTTP 层错误或返回内容解析不了，说明不了密钥是否有效
            return CheckResult(False, str(e))
        except httpx.HTTPError as e:
            return CheckResult(False, f"连不上腾讯云：{e.__class__.__name__}: {e}")
        return CheckResult(True, "密钥有效")
