"""语音识别服务的统一接口。

每家服务一个适配器：submit 提交一段公网可下载的音频、拿到任务号；poll 查询（会被反复调用，必须幂等）；
parse 把服务商的原始结果变成统一的句子列表（纯函数，便于用文档里的样例测试）。HTTP 客户端由外部注入，
测试时换成 httpx.MockTransport。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

import httpx

ErrorKind = Literal["auth", "quota", "input", "provider", "network", "config"]


class AsrError(Exception):
    """kind 决定界面提示和是否值得重试：network/provider 一般可重试，auth/quota/input/config 不行。"""

    def __init__(self, message: str, kind: ErrorKind = "provider", *, retryable: bool | None = None):
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.retryable = retryable if retryable is not None else kind in ("network", "provider")


@dataclass(frozen=True)
class FieldSpec:
    """后台“语音识别”编辑表单的一个字段。secret=True 的存进加密的 secret_enc，其余存进 config。"""

    key: str
    label: str
    secret: bool = False
    required: bool = True
    default: str = ""
    placeholder: str = ""
    hint: str = ""
    options: tuple[tuple[str, str], ...] = ()  # 非空时渲染成下拉框：(值, 显示名)


@dataclass(frozen=True)
class Capability:
    max_part_seconds: int  # 单次请求能稳妥处理的时长（开分人），超过就切段
    max_bytes: int  # 单次请求的文件大小上限
    hotwords: bool = False  # 能否在请求里直接带热词
    speaker_count: bool = False  # 能否提示参会人数


@dataclass
class SubmitOptions:
    language: str = "zh"
    expected_speakers: int | None = None  # 只在整场一次提交时传；切段后各段人数不同，不要传
    hotwords: list[str] = field(default_factory=list)
    duration_ms: int = 0  # 这一段音频的时长，只有模拟服务用来造结果


@dataclass
class PollResult:
    state: Literal["pending", "done", "failed"]
    raw: Any = None  # done 时的原始结果（已下载好的 JSON），交给 parse
    error: str | None = None
    error_kind: ErrorKind = "provider"
    progress: float | None = None  # 服务商若报告进度，0–1


@dataclass
class AsrSegment:
    start_ms: int
    end_ms: int
    speaker: str  # 服务商给的说话人编号原样转成字符串，统一编号由上层完成
    text: str


@dataclass
class CheckResult:
    ok: bool
    message: str = ""


class AsrAdapter:
    kind: ClassVar[str]
    label: ClassVar[str]  # 后台显示的服务名
    description: ClassVar[str] = ""
    capability: ClassVar[Capability]
    fields: ClassVar[tuple[FieldSpec, ...]] = ()
    dev_only: ClassVar[bool] = False  # 只在模拟引擎（开发、测试）下出现
    poll_interval: ClassVar[tuple[float, float]] = (3.0, 20.0)  # 查询间隔：起始值和上限（逐次放大）
    # 提交后多少小时内能查到任务（0 表示足够长）。过期后任务号可能被复用，不能再查
    task_ttl_hours: ClassVar[float] = 0

    def __init__(self, config: dict[str, Any], secrets: dict[str, str], client: httpx.AsyncClient):
        self.config = config
        self.secrets = secrets
        self.client = client

    def setting(self, key: str) -> str:
        for spec in self.fields:
            if spec.key == key:
                source = self.secrets if spec.secret else self.config
                return str(source.get(key) or spec.default or "").strip()
        raise KeyError(key)

    async def submit(self, audio_url: str, opts: SubmitOptions) -> str:
        raise NotImplementedError

    async def poll(self, task_id: str) -> PollResult:
        raise NotImplementedError

    @staticmethod
    def parse(raw: Any) -> list[AsrSegment]:
        raise NotImplementedError

    async def check_credentials(self) -> CheckResult:
        """不花钱的凭据检查：例如查询一个不存在的任务，区分“鉴权失败”和“任务不存在”。"""
        raise NotImplementedError


def http_error(resp: httpx.Response, prefix: str) -> AsrError:
    """把 HTTP 错误码粗分成错误类型；正文截断后附在消息里，不含请求里的密钥。"""
    body = resp.text[:300]
    if resp.status_code in (401, 403):
        return AsrError(f"{prefix}鉴权失败（HTTP {resp.status_code}）：{body}", "auth")
    if resp.status_code == 429:
        return AsrError(f"{prefix}请求过于频繁或额度不足（HTTP 429）：{body}", "quota", retryable=True)
    if resp.status_code >= 500:
        return AsrError(f"{prefix}服务暂时不可用（HTTP {resp.status_code}）：{body}", "provider")
    return AsrError(f"{prefix}请求被拒绝（HTTP {resp.status_code}）：{body}", "input")
