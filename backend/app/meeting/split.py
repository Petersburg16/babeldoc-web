"""超长录音切段：在静音处切，相邻段重叠几分钟，供说话人对齐使用。

占位实现：暂不支持切段。真正的实现见 M6。
"""

from __future__ import annotations

from typing import Any

from ..config import Config

SUPPORTED = False


def plan_parts(config: Config, audio_path: Any, duration_ms: int, max_part_ms: int) -> list[dict[str, Any]]:
    """返回 [{index, offset_ms, duration_ms, file}]，file 是相对会议目录的文件名（切好的音频）。"""
    raise NotImplementedError
