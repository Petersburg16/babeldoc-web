"""阿里云通义听悟：音视频文件离线转写（新版接口 2023-09-30），只用它的转写与说话人分离。"""

from __future__ import annotations

from typing import Any

from .base import AsrAdapter, AsrSegment, Capability, CheckResult, FieldSpec, PollResult, SubmitOptions


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
        raise NotImplementedError

    async def poll(self, task_id: str) -> PollResult:
        raise NotImplementedError

    @staticmethod
    def parse(raw: Any) -> list[AsrSegment]:
        raise NotImplementedError

    async def check_credentials(self) -> CheckResult:
        raise NotImplementedError
