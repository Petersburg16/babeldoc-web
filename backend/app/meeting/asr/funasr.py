"""阿里云百炼 Fun-ASR 录音文件识别（异步任务 + 说话人分离）。

POST {base}/services/audio/asr/transcription 提交（头 X-DashScope-Async: enable），GET {base}/tasks/{id} 查询；
任务成功后每个文件给一个 transcription_url（带签名的 OSS 地址，24 小时有效），要下载下来才是识别结果。
"""

from __future__ import annotations

import uuid
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

# Fun-ASR 只认 language_hints 的第一个值；auto 不传，让模型自己判断语种
LANGUAGE_HINTS = {"zh": ["zh"], "en": ["en"]}
# 没检测到有效人声，或人声全是语气词：服务商按失败返回，对我们来说是“空结果”，交给上层提示
NO_SPEECH_CODES = {"SUCCESS_WITH_NO_VALID_FRAGMENT", "ASR_RESPONSE_HAVE_NO_WORDS"}

# 欠费、额度、预算类：有的是 HTTP 400/403，必须先于按状态码判断
QUOTA_CODES = {
    "Arrearage",
    "AllocationQuota.FreeTierOnly",
    "CommodityNotPurchased",
    "PrepaidBillOverdue",
    "PostpaidBillOverdue",
    "BudgetLimitExceeded",
    "isv.OUT_OF_SERVICE",
}
AUTH_CODES = {"InvalidApiKey", "invalid_api_key", "AccessDenied", "access_denied", "NOT AUTHORIZED"}
CONFIG_CODES = {"ModelNotFound", "model_not_found", "WorkSpaceNotFound", "NotFound"}

# 子任务失败码 → (错误类型, 给用户看的说明)，取自百炼错误码文档的录音文件识别部分
SUBTASK_ERRORS: dict[str, tuple[ErrorKind, str]] = {
    "FILE_DOWNLOAD_FAILED": (
        "config",
        "服务商下载录音失败，请确认站点公网地址能从外网访问，且没有被防火墙或反向代理拦截",
    ),
    "FILE_404_NOT_FOUND": ("config", "服务商下载录音时得到 404，录音地址可能已失效"),
    "FILE_403_FORBIDDEN": ("config", "服务商下载录音时被拒绝（403），请检查防火墙或反向代理是否拦截了公开音频地址"),
    "FILE_SERVER_ERROR": ("config", "服务商下载录音时本站返回了错误，请稍后重试"),
    "CONTENT_LENGTH_CHECK_FAILED": ("config", "服务商下载到的录音长度与 Content-Length 不一致"),
    "REQUEST_INVALID_FILE_URL_VALUE": ("config", "录音地址不被服务商接受，请检查站点公网地址"),
    "FILE_CHECK_FAILED": ("input", "录音格式不被服务商支持"),
    "FILE_TOO_LARGE": ("input", "录音文件超过服务商的 2 GB 上限"),
    "FILE_NORMALIZE_FAILED": ("input", "录音文件可能已损坏"),
    "FILE_PARSE_FAILED": ("input", "录音文件解析失败，可能已损坏"),
    "DECODE_ERROR": ("input", "服务商解码录音失败"),
    "AUDIO_DURATION_TOO_LONG": ("input", "录音超过服务商的 12 小时上限"),
    "NO_VALID_AUDIO_ERROR": ("input", "录音无效，请检查格式和采样率"),
    "FILE_TRANS_TASK_EXPIRED": ("provider", "识别任务已过期，请重新识别"),
}


class FunAsrAdapter(AsrAdapter):
    kind = "aliyun_funasr"
    label = "阿里云百炼 Fun-ASR"
    description = "录音文件识别，开启说话人分离；单段建议不超过 2 小时，更长的录音自动切段。"
    # 官方建议开分人时音频不超过 2 小时，留几分钟余量
    capability = Capability(max_part_seconds=7000, max_bytes=2 * 1024**3, hotwords=False, speaker_count=True)
    task_ttl_hours = 23  # 任务和结果地址只保留 24 小时
    fields = (
        FieldSpec(
            "api_key",
            "API Key",
            secret=True,
            placeholder="sk-…",
            hint="百炼控制台的 API Key；北京和新加坡的 Key 不通用",
        ),
        FieldSpec(
            "base_url",
            "接口地址",
            default="https://dashscope.aliyuncs.com/api/v1",
            hint="北京：https://dashscope.aliyuncs.com/api/v1；新加坡：https://dashscope-intl.aliyuncs.com/api/v1；"
            "也可以填业务空间专属域名，同样以 /api/v1 结尾",
        ),
        FieldSpec(
            "model",
            "模型",
            default="fun-asr",
            hint="默认 fun-asr；也可以填 paraformer-v2 等支持说话人分离的录音文件识别模型",
        ),
    )

    async def submit(self, audio_url: str, opts: SubmitOptions) -> str:
        body = {
            "model": self.setting("model"),
            "input": {"file_urls": [audio_url]},
            # 新版业务空间域名要求必须带 parameters，这里总是带着
            "parameters": self._parameters(opts),
        }
        headers = {**self._auth(), "Content-Type": "application/json", "X-DashScope-Async": "enable"}
        resp = await self.client.post(self._url("/services/audio/asr/transcription"), json=body, headers=headers)
        if resp.status_code != 200:
            raise _api_error(resp)
        output = _output(resp)
        task_id = str(output.get("task_id") or "").strip()
        if not task_id:
            # 不重试：服务商可能已经建了任务，重提会重复计费
            raise AsrError(
                f"服务商没有返回任务号{_detail(output) or '：' + resp.text[:300]}", "provider", retryable=False
            )
        return task_id

    async def poll(self, task_id: str) -> PollResult:
        resp = await self.client.get(self._task_url(task_id), headers=self._auth())
        if resp.status_code != 200:
            raise _api_error(resp)
        output = _output(resp)
        status = str(output.get("task_status") or "").upper()
        if status in ("PENDING", "RUNNING"):
            return PollResult("pending")
        if status == "UNKNOWN":
            return PollResult(
                "failed", error="服务商查不到这个任务：任务号不存在，或已超过 24 小时被清理", error_kind="provider"
            )
        if status not in ("SUCCEEDED", "FAILED"):
            return PollResult("failed", error=f"任务状态异常：{status or '空'}{_detail(output)}", error_kind="provider")

        # 只要有一个子任务成功整体就是 SUCCEEDED，所以两种终态都逐个看子任务
        raws: list[dict[str, Any]] = []
        no_speech = False
        for result in output.get("results") or []:
            if not isinstance(result, dict):
                continue
            code = str(result.get("code") or "")
            if code in NO_SPEECH_CODES:
                no_speech = True
            elif str(result.get("subtask_status") or "").upper() == "SUCCEEDED" and result.get("transcription_url"):
                raws.append(await self._download(str(result["transcription_url"])))
            else:
                # 每次只提交一个文件，任何一个子任务失败都意味着这一段没有结果
                kind, message = _subtask_error(result)
                return PollResult("failed", error=message, error_kind=kind)
        if raws or no_speech:
            return PollResult("done", raw=raws)
        if str(output.get("code") or "") in NO_SPEECH_CODES:
            return PollResult("done", raw=[])
        if output.get("code"):
            kind, message = _subtask_error(output)
            return PollResult("failed", error=message, error_kind=kind)
        return PollResult("failed", error=f"服务商没有返回识别结果（任务状态 {status}）", error_kind="provider")

    @staticmethod
    def parse(raw: Any) -> list[AsrSegment]:
        """raw 是 poll 下载到的识别结果列表（每个文件一个），也接受单个结果对象（文档样例）。"""
        results = raw if isinstance(raw, list) else [raw]
        segments: list[AsrSegment] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            for transcript in result.get("transcripts") or []:
                if not isinstance(transcript, dict):
                    continue
                for s in transcript.get("sentences") or []:
                    if not isinstance(s, dict):
                        continue
                    text = str(s.get("text") or "").strip()
                    if not text:
                        continue
                    start = _ms(s.get("begin_time"))
                    end = max(start, _ms(s.get("end_time")))
                    # 只有开了说话人分离才有 speaker_id，从 0 开始编号
                    speaker = s.get("speaker_id")
                    segments.append(AsrSegment(start, end, "0" if speaker is None else str(speaker), text))
        return segments

    async def check_credentials(self) -> CheckResult:
        """查询一个随机的任务号：密钥有效时服务商回 200 和 UNKNOWN（任务不存在），无效时回 401。
        实测网关先验密钥再看路径，所以 404 只说明地址不对，不能算通过。"""
        try:
            headers = self._auth()
            url = self._task_url(str(uuid.uuid4()))
        except AsrError as e:
            return CheckResult(False, str(e))
        try:
            resp = await self.client.get(url, headers=headers, timeout=20)
        except httpx.HTTPError as e:
            return CheckResult(False, f"连不上接口地址：{e.__class__.__name__}: {e}"[:300])
        if resp.status_code == 404:
            return CheckResult(False, f"接口地址不对，应以 /api/v1 结尾（HTTP 404）：{resp.text[:200]}")
        if resp.status_code != 200:
            return CheckResult(False, str(_api_error(resp)))
        try:
            output = _output(resp)
        except AsrError as e:
            return CheckResult(False, str(e))
        if str(output.get("task_status") or "").upper() == "UNKNOWN":
            return CheckResult(True, "API Key 有效（用不存在的任务号查询，服务商回复任务不存在）")
        return CheckResult(True, "API Key 有效")

    # ---------- 内部 ----------

    def _auth(self) -> dict[str, str]:
        key = self.setting("api_key")
        if not key:
            raise AsrError("还没有填写 API Key", "config")
        return {"Authorization": f"Bearer {key}"}

    def _url(self, path: str) -> str:
        base = self.setting("base_url").rstrip("/")
        if not base.startswith(("https://", "http://")):
            raise AsrError("接口地址要以 https:// 开头，例如 https://dashscope.aliyuncs.com/api/v1", "config")
        return base + path

    def _task_url(self, task_id: str) -> str:
        return self._url(f"/tasks/{quote(task_id, safe='')}")

    @staticmethod
    def _parameters(opts: SubmitOptions) -> dict[str, Any]:
        params: dict[str, Any] = {"diarization_enabled": True}
        hints = LANGUAGE_HINTS.get(opts.language)
        if hints:
            params["language_hints"] = hints
        # 官方取值范围 2–100，超出范围就让模型自己判断人数
        n = opts.expected_speakers
        if n is not None and 2 <= n <= 100:
            params["speaker_count"] = n
        return params

    async def _download(self, url: str) -> dict[str, Any]:
        # 签名的 OSS 地址，不能带百炼的鉴权头
        resp = await self.client.get(url)
        if resp.status_code in (403, 404):
            raise AsrError(
                f"识别结果链接已失效或无权访问（HTTP {resp.status_code}），服务商的结果只保留 24 小时",
                "provider",
                retryable=False,
            )
        if resp.status_code != 200:
            raise AsrError(f"下载识别结果失败（HTTP {resp.status_code}）", "provider")
        try:
            data = resp.json()
        except ValueError:
            raise AsrError("下载到的识别结果不是有效的 JSON", "provider", retryable=False) from None
        if not isinstance(data, dict):
            raise AsrError("下载到的识别结果格式不对", "provider", retryable=False)
        return data


def _output(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        data = None
    output = data.get("output") if isinstance(data, dict) else None
    if not isinstance(output, dict):
        raise AsrError(f"接口返回的内容不像百炼的应答，请检查接口地址：{resp.text[:200]}", "config")
    return output


def _code_message(data: Any) -> tuple[str, str]:
    if not isinstance(data, dict):
        return "", ""
    return str(data.get("code") or "").strip(), str(data.get("message") or "").strip()[:200]


def _detail(data: Any, status: int | None = None) -> str:
    """（HTTP 401，InvalidApiKey：Invalid API-key provided.）；信息和错误码相同时只写一次。"""
    code, message = _code_message(data)
    parts = [f"HTTP {status}"] if status is not None else []
    if code and message and message != code:
        parts.append(f"{code}：{message}")
    elif code or message:
        parts.append(code or message)
    return f"（{'，'.join(parts)}）" if parts else ""


def _api_error(resp: httpx.Response) -> AsrError:
    """百炼接口的错误正文是 {"code", "message", "request_id"}；没有 code 的（网关、代理页面）按状态码粗分。"""
    try:
        data = resp.json()
    except ValueError:
        data = None
    code, _ = _code_message(data)
    if not code:
        return http_error(resp, "百炼接口")
    status = resp.status_code
    detail = _detail(data, status)
    if code in QUOTA_CODES:
        return AsrError(f"账户欠费、余额不足或额度已用完{detail}", "quota")
    if code.startswith("Throttling") or status == 429:
        return AsrError(f"请求过于频繁，触发了服务商限流{detail}", "quota", retryable=True)
    if status in (401, 403) or code in AUTH_CODES:
        return AsrError(f"API Key 无效、没有权限，或与接口地址不属于同一地域{detail}", "auth")
    if status == 404 or code in CONFIG_CODES:
        return AsrError(f"模型名或接口地址不对{detail}", "config")
    if status >= 500:
        return AsrError(f"服务商暂时不可用{detail}", "provider")
    if status == 400 or code.startswith(("InvalidParameter", "InvalidFile")):
        return AsrError(f"请求参数不被服务商接受{detail}", "input")
    return http_error(resp, "百炼接口")


def _subtask_error(result: dict[str, Any]) -> tuple[ErrorKind, str]:
    code, _ = _code_message(result)
    kind, hint = SUBTASK_ERRORS.get(code, ("provider", "服务商识别失败"))
    detail = _detail(result) or f"（子任务状态 {result.get('subtask_status') or '未知'}）"
    return kind, f"{hint}{detail}"


def _ms(value: Any) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0
