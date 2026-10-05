"""把各段的识别结果合成一份逐字稿：时间加上段偏移、统一说话人编号（S1、S2…）。

多段时还要在重叠区里配对说话人并去掉重复的句子（见 M6）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .asr.base import AsrSegment


@dataclass
class MergedSegment:
    start_ms: int
    end_ms: int
    speaker: str  # 统一编号 S1、S2…
    text: str


def normalize_single(segments: list[AsrSegment], offset_ms: int = 0) -> list[MergedSegment]:
    """单段：按首次出现的顺序把服务商的说话人编号映射成 S1、S2…，并按开始时间排序。"""
    ordered = sorted(segments, key=lambda s: (s.start_ms, s.end_ms))
    mapping: dict[str, str] = {}
    out: list[MergedSegment] = []
    for seg in ordered:
        text = seg.text.strip()
        if not text:
            continue
        if seg.speaker not in mapping:
            mapping[seg.speaker] = f"S{len(mapping) + 1}"
        out.append(MergedSegment(seg.start_ms + offset_ms, seg.end_ms + offset_ms, mapping[seg.speaker], text))
    return out


def merge_parts(parts: list[tuple[int, int, list[AsrSegment]]]) -> tuple[list[MergedSegment], list[str]]:
    """parts: [(offset_ms, duration_ms, 该段识别结果)]，按 offset 排好。返回合并结果和给用户看的提示。"""
    if len(parts) == 1:
        offset, _, segments = parts[0]
        return normalize_single(segments, offset), []
    raise NotImplementedError("多段合并见 M6")
