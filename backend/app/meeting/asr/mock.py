"""开发和测试用的模拟识别服务：不联网，按音频时长造一段多人对话。任务号里带着提交时间和时长，
所以服务重启后照样能“查询”到结果。"""

from __future__ import annotations

import time
import uuid
from typing import Any

from .base import AsrAdapter, AsrError, AsrSegment, Capability, CheckResult, FieldSpec, PollResult, SubmitOptions

LINES = [
    ("S0", "嗯那个大家好，我是张老师，今天我们主要过一下几个人的进展。"),
    ("S1", "好的张老师，我是李明，我先说一下，就是说这周我把那个风场的数据，呃，重新清洗了一遍。"),
    ("S1", "然后发现测风塔有两天的数据是缺的，我用相邻站点插补了。"),
    ("S0", "插补的方法要在论文里写清楚，下周把对比图发给我。"),
    ("S2", "我是王芳，我这边模型训练还在跑，那个验证集的误差，误差比上次低了大概百分之八。"),
    ("S0", "嗯，不错，那个消融实验什么时候能做完？"),
    ("S2", "预计下周三之前吧，下周三。"),
    ("S1", "张老师，还有一个问题，就是服务器的显存不太够用。"),
    ("S0", "这个我来协调，你们先把批大小调小一点。"),
    ("S2", "好的，那我这边先这样。"),
]


class MockAdapter(AsrAdapter):
    kind = "mock"
    label = "模拟服务（开发用）"
    description = "不联网，按录音时长生成一段示例对话，用于开发和测试。"
    capability = Capability(max_part_seconds=6 * 3600, max_bytes=2 * 1024**3, hotwords=True, speaker_count=True)
    fields = (
        FieldSpec("delay_seconds", "模拟处理耗时（秒）", required=False, default="1"),
        FieldSpec("api_key", "模拟密钥", secret=True, required=False, hint="填 bad 可以模拟鉴权失败"),
        FieldSpec(
            "fail", "模拟失败", required=False, options=(("", "不失败"), ("submit", "提交时失败"), ("poll", "识别失败"))
        ),
    )
    dev_only = True
    poll_interval = (0.1, 0.3)

    async def submit(self, audio_url: str, opts: SubmitOptions) -> str:
        if self.setting("api_key") == "bad":
            raise AsrError("模拟服务：密钥无效", "auth")
        if self.setting("fail") == "submit":
            raise AsrError("模拟服务：提交失败", "provider", retryable=False)
        return f"mock-{uuid.uuid4().hex[:12]}-{int(time.time() * 1000)}-{max(0, opts.duration_ms)}"

    async def poll(self, task_id: str) -> PollResult:
        try:
            _, _, started, duration = task_id.split("-")
            started_ms, duration_ms = int(started), int(duration)
        except ValueError:
            return PollResult("failed", error="模拟服务：任务号无效", error_kind="input")
        delay = float(self.setting("delay_seconds") or 0)
        elapsed = time.time() - started_ms / 1000
        if elapsed < delay:
            return PollResult("pending", progress=min(0.99, elapsed / delay if delay else 1))
        if self.setting("fail") == "poll":
            return PollResult("failed", error="模拟服务：识别失败", error_kind="provider")
        return PollResult("done", raw={"duration_ms": duration_ms, "sentences": _sentences(duration_ms)})

    @staticmethod
    def parse(raw: Any) -> list[AsrSegment]:
        return [
            AsrSegment(start_ms=int(s["begin"]), end_ms=int(s["end"]), speaker=str(s["spk"]), text=str(s["text"]))
            for s in raw.get("sentences", [])
            if str(s.get("text", "")).strip()
        ]

    async def check_credentials(self) -> CheckResult:
        if self.setting("api_key") == "bad":
            return CheckResult(False, "模拟服务：密钥无效")
        return CheckResult(True, "模拟服务无需联网")


def _sentences(duration_ms: int) -> list[dict[str, Any]]:
    """每句 6 秒、句间 1 秒，循环示例对话直到铺满时长（至少一句）。"""
    out: list[dict[str, Any]] = []
    t = 500
    i = 0
    while t + 1000 <= max(duration_ms, 7500):
        spk, text = LINES[i % len(LINES)]
        end = min(t + 6000, max(duration_ms, 7000))
        out.append({"begin": t, "end": end, "spk": spk, "text": text})
        t = end + 1000
        i += 1
    return out
