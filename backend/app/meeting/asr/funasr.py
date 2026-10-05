"""阿里云百炼 Fun-ASR 录音文件识别（异步任务 + 说话人分离）。"""

from __future__ import annotations

from typing import Any

from .base import AsrAdapter, AsrSegment, Capability, CheckResult, FieldSpec, PollResult, SubmitOptions


class FunAsrAdapter(AsrAdapter):
    kind = "aliyun_funasr"
    label = "阿里云百炼 Fun-ASR"
    description = "录音文件识别，开启说话人分离；单段建议不超过 2 小时，更长的录音自动切段。"
    # 官方建议开分人时音频不超过 2 小时，留几分钟余量
    capability = Capability(max_part_seconds=7000, max_bytes=2 * 1024**3, hotwords=False, speaker_count=True)
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
            hint="北京：https://dashscope.aliyuncs.com/api/v1；新加坡：https://dashscope-intl.aliyuncs.com/api/v1",
        ),
        FieldSpec(
            "model",
            "模型",
            default="fun-asr",
            hint="默认 fun-asr；也可以填 paraformer-v2 等支持说话人分离的录音文件识别模型",
        ),
    )

    async def submit(self, audio_url: str, opts: SubmitOptions) -> str:
        raise NotImplementedError

    async def poll(self, task_id: str) -> PollResult:
        raise NotImplementedError

    @staticmethod
    def parse(raw: Any) -> list[AsrSegment]:
        raise NotImplementedError

    async def check_credentials(self) -> CheckResult:
        raise NotImplementedError
