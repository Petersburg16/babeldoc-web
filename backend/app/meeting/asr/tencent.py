"""腾讯云录音文件识别（CreateRecTask），默认用会议引擎 16k_zh_en_meeting，开启说话人分离。"""

from __future__ import annotations

from typing import Any

from .base import AsrAdapter, AsrSegment, Capability, CheckResult, FieldSpec, PollResult, SubmitOptions


class TencentMeetingAdapter(AsrAdapter):
    kind = "tencent_meeting"
    label = "腾讯云会议引擎"
    description = "录音文件识别的多人会议引擎，自动区分说话人（最多 20 人）；单个文件最长 5 小时。"
    # 官方上限 5 小时，留几分钟余量
    capability = Capability(max_part_seconds=17700, max_bytes=1024**3, hotwords=True, speaker_count=False)
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

    async def submit(self, audio_url: str, opts: SubmitOptions) -> str:
        raise NotImplementedError

    async def poll(self, task_id: str) -> PollResult:
        raise NotImplementedError

    @staticmethod
    def parse(raw: Any) -> list[AsrSegment]:
        raise NotImplementedError

    async def check_credentials(self) -> CheckResult:
        raise NotImplementedError
