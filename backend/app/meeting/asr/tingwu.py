"""阿里云通义听悟：音视频文件离线转写（新版接口 2023-09-30），只用它的转写与说话人分离。

接口是 ROA 风格（PUT/GET /openapi/tingwu/v2/tasks），用 ACS3-HMAC-SHA256 签名，这里自己实现，不引入阿里云 SDK。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

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

API_VERSION = "2023-09-30"
ALGORITHM = "ACS3-HMAC-SHA256"
TASKS_PATH = "/openapi/tingwu/v2/tasks"
# 站内语言 → 听悟 SourceLanguage；auto 是文档里的“自动语种识别”，只在离线转写可用
LANGUAGES = {"zh": "cn", "en": "en", "auto": "auto"}
# 检查密钥时查询的任务号：格式像真的，但不可能存在
PROBE_TASK_ID = "0" * 32

_AUDIO_URL_HINT = "服务商拉不到录音：请确认站点公网地址能从外网访问，且 Cloudflare 没有拦截 /api/public/"
# 错误码 → (类型, 给管理员看的说明)；码表见听悟“公共错误码”“常见错误码”与阿里云 OpenAPI 网关错误码
_CODES: dict[str, tuple[ErrorKind, str]] = {
    "InvalidAccessKeyId.NotFound": ("auth", "AccessKey ID 不存在"),
    "InvalidAccessKeyId.Inactive": ("auth", "AccessKey 已被禁用"),
    "SignatureDoesNotMatch": ("auth", "签名不匹配，请检查 AccessKey Secret"),
    "IncompleteSignature": ("auth", "签名不完整，请检查 AccessKey"),
    "MissingSecurityToken": ("auth", "这个 AccessKey 需要 STS 令牌，请改用 RAM 用户的长期 AccessKey"),
    "NoPermission": ("auth", "这个 AccessKey 没有调用通义听悟的权限，请在 RAM 里授权"),
    "BRK.InvalidAppKey": ("auth", "AppKey 无效，请到听悟控制台“项目管理”核对"),
    "BRK.ServiceLinkedRoleNotExist": (
        "auth",
        "缺少服务关联角色 AliyunServiceRoleForTingwuPaaS，请在听悟控制台完成授权",
    ),
    "BRK.InvalidService": ("quota", "账号没有开通通义听悟服务"),
    "BRK.InvalidTenant": ("quota", "账号没有开通通义听悟服务或已欠费"),
    "BRK.OverdueTenant": ("quota", "账号已欠费"),
    "BRK.OverdueService": ("quota", "账号的服务额度已用完"),
    "PRE.AudioDurationQuotaLimit": ("quota", "免费额度的识别时长已用完"),
    "TSC.AudioFileLink": ("input", _AUDIO_URL_HINT),
    "TSC.FileError": ("input", _AUDIO_URL_HINT),
    "TSC.ContentLengthCheckFailed": (
        "input",
        "录音地址返回的文件长度校验失败，请确认公开音频地址的 HEAD 请求带有正确的 Content-Length",
    ),
    "TSC.AudioDuration": ("input", "录音超过 6 小时"),
    "TSC.AudioFileSize": ("input", "录音超过 6 GB"),
    "TSC.AudioFormat": ("input", "录音格式不受支持"),
    "TSC.AudioSampleRate": ("input", "录音采样率不受支持"),
    "TSC.FileType": ("input", "录音文件已损坏或类型不对"),
    "SignatureNonceUsed": ("provider", "签名随机数重复，稍后重试"),
}
# 按前缀归类的错误码（先查精确码表）
_PREFIXES: tuple[tuple[str, ErrorKind, str], ...] = (
    ("InvalidAccessKeyId", "auth", "AccessKey 无效"),
    ("InvalidSecurityToken", "auth", "安全令牌无效"),
    ("Forbidden", "auth", "这个 AccessKey 没有调用通义听悟的权限，请在 RAM 里授权"),
    ("InvalidTimeStamp", "config", "服务器时钟与阿里云相差超过 15 分钟，请校准服务器时间"),
    ("Throttling", "quota", "请求过于频繁，被服务商限流"),
    ("BRK.", "input", "请求参数被拒绝"),
    ("TSC.", "input", "录音文件有问题"),
    ("InvalidParameter", "input", "请求参数被拒绝"),
    ("Missing", "input", "请求缺少参数"),
)


def classify(code: str) -> tuple[ErrorKind, str, bool | None]:
    """返回 (类型, 说明, 是否可重试)；None 表示按类型的默认值。"""
    if code in _CODES:
        kind, hint = _CODES[code]
        return kind, hint, None
    for prefix, kind, hint in _PREFIXES:
        if code.startswith(prefix):
            # 限流过一会儿就好，值得重试；其余额度类错误要人去处理
            return kind, hint, True if prefix == "Throttling" else None
    return "provider", "服务商出错", None


# ---------- ACS3-HMAC-SHA256 签名 ----------


def _encode(value: str) -> str:
    """RFC 3986：只保留字母数字和 -_.~，其余（含空格、*）一律百分号编码。"""
    return quote(value, safe="-_.~")


def canonical_query(query: dict[str, str]) -> str:
    return "&".join(f"{_encode(k)}={_encode(v)}" for k, v in sorted(query.items()))


def canonical_request(
    method: str, uri: str, query: dict[str, str], headers: dict[str, str], payload_hash: str
) -> tuple[str, str]:
    """返回 (规范请求, SignedHeaders)。参与签名的头：host、content-type 和全部 x-acs-*。"""
    signed = {
        k.lower(): v.strip()
        for k, v in headers.items()
        if k.lower() in ("host", "content-type") or k.lower().startswith("x-acs-")
    }
    names = sorted(signed)
    canonical_headers = "".join(f"{name}:{signed[name]}\n" for name in names)
    signed_headers = ";".join(names)
    text = "\n".join([method.upper(), uri, canonical_query(query), canonical_headers, signed_headers, payload_hash])
    return text, signed_headers


def authorization(
    method: str,
    uri: str,
    query: dict[str, str],
    headers: dict[str, str],
    payload_hash: str,
    key_id: str,
    key_secret: str,
) -> str:
    text, signed_headers = canonical_request(method, uri, query, headers, payload_hash)
    string_to_sign = f"{ALGORITHM}\n{hashlib.sha256(text.encode()).hexdigest()}"
    signature = hmac.new(key_secret.encode(), string_to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{ALGORITHM} Credential={key_id},SignedHeaders={signed_headers},Signature={signature}"


def signed_headers(
    method: str,
    host: str,
    uri: str,
    query: dict[str, str],
    payload: bytes,
    *,
    action: str,
    key_id: str,
    key_secret: str,
    version: str = API_VERSION,
    content_type: str | None = None,
    date: str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """生成一次请求要带的全部头（含 Authorization）。date、nonce 可指定，便于用文档里的固定示例测试。"""
    headers = {
        "host": host,
        "x-acs-action": action,
        "x-acs-version": version,
        "x-acs-date": date or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "x-acs-signature-nonce": nonce or uuid.uuid4().hex,
        "x-acs-content-sha256": hashlib.sha256(payload).hexdigest(),
    }
    if content_type:
        headers["content-type"] = content_type
    headers["Authorization"] = authorization(
        method, uri, query, headers, headers["x-acs-content-sha256"], key_id, key_secret
    )
    return headers


# ---------- 适配器 ----------


class TingwuAdapter(AsrAdapter):
    kind = "aliyun_tingwu"
    label = "阿里云通义听悟"
    description = "音视频文件离线转写，开启说话人分离；单个文件最长 6 小时。只用转写结果，纪要由本站的大模型生成。"
    capability = Capability(max_part_seconds=21000, max_bytes=6 * 1024**3, hotwords=False, speaker_count=True)
    fields = (
        FieldSpec("access_key_id", "AccessKey ID", secret=True),
        FieldSpec("access_key_secret", "AccessKey Secret", secret=True),
        FieldSpec("app_key", "AppKey", hint="听悟控制台“项目管理”里的 AppKey"),
        FieldSpec("endpoint", "接口地址", default="https://tingwu.cn-beijing.aliyuncs.com", hint="目前只有北京地域"),
    )

    async def submit(self, audio_url: str, opts: SubmitOptions) -> str:
        app_key = self.setting("app_key")
        if not app_key:
            raise AsrError("通义听悟：还没有填写 AppKey", "config")
        speakers = opts.expected_speakers if opts.expected_speakers and 0 < opts.expected_speakers <= 100 else 0
        body = {
            "AppKey": app_key,
            "Input": {
                "SourceLanguage": LANGUAGES.get(opts.language, "auto"),
                "FileUrl": audio_url,
                "TaskKey": f"bdw-{uuid.uuid4().hex[:16]}",
            },
            # SpeakerCount=0 表示人数不定；不传 Diarization 则不区分说话人
            "Parameters": {"Transcription": {"DiarizationEnabled": True, "Diarization": {"SpeakerCount": speakers}}},
        }
        data = await self._call("PUT", TASKS_PATH, action="CreateTask", query={"type": "offline"}, body=body)
        task_id = str((data.get("Data") or {}).get("TaskId") or "")
        if not task_id:
            raise AsrError("通义听悟提交成功但没有返回任务号", "provider", retryable=False)
        return task_id

    async def poll(self, task_id: str) -> PollResult:
        data = await self._call("GET", f"{TASKS_PATH}/{_encode(task_id)}", action="GetTaskInfo")
        info = data.get("Data") or {}
        status = str(info.get("TaskStatus") or "").upper()
        if status == "ONGOING":
            return PollResult("pending")
        if status == "COMPLETED":
            url = (info.get("Result") or {}).get("Transcription")
            if not url:
                return PollResult("failed", error="任务已完成，但服务商没有返回转写结果")
            return PollResult("done", raw=await self._download(str(url)))
        if status in ("FAILED", "INVALID"):
            code = str(info.get("ErrorCode") or "")
            message = str(info.get("ErrorMessage") or "")
            if code:
                kind, hint, _ = classify(code)
                return PollResult("failed", error=f"{hint}（错误码 {code}：{message}）", error_kind=kind)
            if status == "INVALID":
                return PollResult("failed", error="服务商认为这个任务无效（可能已过期或不存在）")
            return PollResult("failed", error=message or None)
        raise AsrError(f"通义听悟返回了未知的任务状态：{status or '空'}", "provider")

    @staticmethod
    def parse(raw: Any) -> list[AsrSegment]:
        """Paragraphs[] 是同一说话人的连续段落，Words[] 带所属句子号；同一段落里按 SentenceId 拼回句子。"""
        if not isinstance(raw, dict):
            return []
        body = raw.get("Transcription", raw)
        paragraphs = body.get("Paragraphs") if isinstance(body, dict) else None
        out: list[AsrSegment] = []
        for para in paragraphs or []:
            speaker = para.get("SpeakerId")
            speaker = "0" if speaker in (None, "") else str(speaker)
            sentences: dict[Any, list[dict[str, Any]]] = {}
            for word in para.get("Words") or []:
                sentences.setdefault(word.get("SentenceId"), []).append(word)
            for words in sentences.values():
                text = _join_words(str(w.get("Text") or "") for w in words)
                if not text:
                    continue
                starts = [int(w["Start"]) for w in words if w.get("Start") is not None]
                ends = [int(w["End"]) for w in words if w.get("End") is not None]
                start = min(starts, default=0)
                out.append(AsrSegment(start_ms=start, end_ms=max(ends, default=start), speaker=speaker, text=text))
        return out

    async def check_credentials(self) -> CheckResult:
        missing = [spec.label for spec in self.fields if spec.required and not self.setting(spec.key)]
        if missing:
            return CheckResult(False, f"请先填写：{'、'.join(missing)}")
        try:
            await self._call("GET", f"{TASKS_PATH}/{PROBE_TASK_ID}", action="GetTaskInfo")
        except httpx.HTTPError as e:
            return CheckResult(False, f"连不上接口地址：{e.__class__.__name__}: {e}")
        except AsrError as e:
            if e.kind in ("auth", "quota", "config") and not e.retryable:
                return CheckResult(False, str(e))
            if e.kind != "input":
                return CheckResult(False, f"暂时无法验证：{e}")
            # 其余参数类错误只可能是“任务号不存在”之类，说明签名和账号都过了
        return CheckResult(True, "AccessKey 有效（AppKey 要到提交识别任务时才会校验，可用“完整测试”验证）")

    # ---------- 请求 ----------

    async def _call(
        self,
        method: str,
        path: str,
        *,
        action: str,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key_id, key_secret = self.setting("access_key_id"), self.setting("access_key_secret")
        if not key_id or not key_secret:
            raise AsrError("通义听悟：还没有填写 AccessKey", "config")
        query = query or {}
        try:
            endpoint = httpx.URL(self.setting("endpoint").rstrip("/"))
        except httpx.InvalidURL:
            endpoint = None
        if endpoint is None or endpoint.scheme not in ("http", "https") or not endpoint.host:
            raise AsrError("通义听悟：接口地址要写完整，例如 https://tingwu.cn-beijing.aliyuncs.com", "config")
        uri = endpoint.path.rstrip("/") + path
        qs = canonical_query(query)
        # 查询串自己拼好，保证发出去的和签名用的完全一致
        url = endpoint.copy_with(raw_path=(uri + (f"?{qs}" if qs else "")).encode("ascii"))
        # 签名要对发出去的字节算摘要，所以自己序列化，不用 httpx 的 json=
        payload = b"" if body is None else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        headers = signed_headers(
            method,
            url.netloc.decode("ascii"),
            uri,
            query,
            payload,
            action=action,
            key_id=key_id,
            key_secret=key_secret,
            content_type="application/json" if body is not None else None,
        )
        resp = await self.client.request(method, url, headers=headers, content=payload or None)
        return _read(resp, f"通义听悟 {action} ")

    async def _download(self, url: str) -> Any:
        """结果文件是 OSS 的临时链接（保留 30 天），直接下载，不带签名。"""
        resp = await self.client.get(url)
        if resp.status_code >= 500:
            raise AsrError(f"下载通义听悟的转写结果失败（HTTP {resp.status_code}）", "provider")
        if not resp.is_success:
            raise AsrError(
                f"下载通义听悟的转写结果失败（HTTP {resp.status_code}），结果链接可能已过期",
                "provider",
                retryable=False,
            )
        try:
            return resp.json()
        except ValueError as e:
            raise AsrError("通义听悟的转写结果不是有效的 JSON", "provider", retryable=False) from e


def _read(resp: httpx.Response, prefix: str) -> dict[str, Any]:
    """先看正文里的 Code，再看 HTTP 状态：AccessKey 不存在是 404、签名错误是 400，只看状态会误判成参数错误。"""
    try:
        body = resp.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        if resp.is_success:
            raise AsrError(f"{prefix}返回的不是 JSON（HTTP {resp.status_code}）", "provider")
        err = http_error(resp, prefix)
        if err.kind == "input":
            # 阿里云网关的错误都是 JSON；不是 JSON 的 4xx 多半是接口地址填错了
            raise AsrError(
                f"{prefix}失败：接口地址可能不对（HTTP {resp.status_code}，返回的不是阿里云接口的 JSON）", "config"
            )
        raise err
    code = body.get("Code")
    if code is not None and str(code) not in ("", "0"):
        code = str(code)
        kind, hint, retryable = classify(code)
        message = str(body.get("Message") or "")[:200]
        request_id = body.get("RequestId")
        detail = f"错误码 {code}：{message}" + (f"；请求号 {request_id}" if request_id else "")
        raise AsrError(f"{prefix}失败：{hint}（{detail}）", kind, retryable=retryable)
    if not resp.is_success:
        raise http_error(resp, prefix)
    return body


def _needs_space(prev: str, nxt: str) -> bool:
    """英文、数字之间要空格；中文和标点直接相连。"""
    if prev.isspace() or nxt.isspace() or not (nxt.isascii() and nxt.isalnum()):
        return False
    if prev.isascii() and prev.isalnum():
        return True
    return prev in ",;:!?" or (prev == "." and nxt.isalpha())


def _join_words(parts: Any) -> str:
    text = ""
    for part in parts:
        if not part:
            continue
        if text and _needs_space(text[-1], part[0]):
            text += " "
        text += part
    return re.sub(r"\s+", " ", text).strip()
