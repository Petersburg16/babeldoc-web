"""超长录音切段：在静音处切，相邻段重叠几分钟，供说话人对齐使用（见 align.merge_parts）。

段长按“剩余时长平均分”定，每段不超过单次上限的约 90%；段尾和下一段的段首都尽量落在静音中点，
避免把一个字切成两半。整段只解码一次找静音，再用 -c copy 截出各段（不重新编码，几秒一段）。
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Any

from ..config import Config
from .media import MediaError, cut_args, detect_silences

SUPPORTED = True

OVERLAP_MS = 240_000  # 相邻段重叠 4 分钟：对齐说话人要靠重叠区里足够多的发言
FILL = 0.9  # 每段目标长度占单次上限的比例，留余量
SEARCH_MS = 60_000  # 在目标切点前后多远的范围里找静音
NOISE_DB = -35
MIN_SILENCE = 0.4  # 秒
PART_PATTERN = "part-*.mp3"


def part_name(index: int) -> str:
    return f"part-{index:02d}.mp3"


def plan_parts(
    config: Config,
    audio_path: Any,
    duration_ms: int,
    max_part_ms: int,
    *,
    overlap_ms: int = OVERLAP_MS,
    fill: float = FILL,
    search_ms: int = SEARCH_MS,
    noise_db: int = NOISE_DB,
    min_silence: float = MIN_SILENCE,
) -> list[dict[str, Any]]:
    """返回 [{index, offset_ms, duration_ms, file}]，file 是相对会议目录的文件名（切好的音频）。

    同步执行（解码整段找静音要一两分钟），调用方放在线程里跑。
    """
    audio = Path(audio_path)
    if duration_ms <= max_part_ms:
        # 管理器在文件过大时也会调过来；按时长不用切，就整段交出去
        return [{"index": 0, "offset_ms": 0, "duration_ms": duration_ms, "file": audio.name}]
    silences = detect_silences(config.ffmpeg, audio, noise_db, min_silence)
    spans = compute_spans(duration_ms, max_part_ms, silences, overlap_ms=overlap_ms, fill=fill, search_ms=search_ms)
    directory = audio.parent
    # 重试时先清掉上次切出的文件，免得多出来的旧分段留在目录里
    for old in directory.glob(PART_PATTERN):
        old.unlink(missing_ok=True)
    parts: list[dict[str, Any]] = []
    for index, (start, end) in enumerate(spans):
        name = part_name(index)
        _cut(config.ffmpeg, audio, directory / name, start, end - start)
        parts.append({"index": index, "offset_ms": start, "duration_ms": end - start, "file": name})
    return parts


def compute_spans(
    duration_ms: int,
    max_part_ms: int,
    silences: list[tuple[int, int]],
    *,
    overlap_ms: int = OVERLAP_MS,
    fill: float = FILL,
    search_ms: int = SEARCH_MS,
) -> list[tuple[int, int]]:
    """只算切点不动文件：返回各段 [(start_ms, end_ms)]，相邻段有重叠，首段从 0 开始、末段到 duration_ms。"""
    if duration_ms <= max_part_ms:
        return [(0, duration_ms)]
    target = max(1, int(max_part_ms * fill))
    # 重叠太大会让段数失控，至多占目标段长的三分之一
    overlap = max(0, min(overlap_ms, target // 3))
    window = max(0, min(search_ms, max_part_ms - target))
    start_window = max(0, min(search_ms, overlap // 4))
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        remaining = duration_ms - start
        if remaining <= target:
            spans.append((start, duration_ms))
            return spans
        # 剩下的平均分成 k 段（每段 ≤ target），避免最后剩一小截
        k = max(2, math.ceil((remaining - overlap) / max(1, target - overlap)))
        ideal = start + (remaining + (k - 1) * overlap) // k
        lo = max(ideal - window, start + overlap + overlap // 2 + 1)
        hi = min(ideal + window, start + max_part_ms, duration_ms - 1)
        end = _pick_cut(silences, lo, hi, ideal)
        spans.append((start, end))
        ideal_next = end - overlap
        next_start = _pick_cut(silences, ideal_next - start_window, ideal_next + start_window, ideal_next)
        start = max(start + 1, next_start)


def _pick_cut(silences: list[tuple[int, int]], lo: int, hi: int, target: int) -> int:
    """在 [lo, hi] 里挑最长的静音（按落在范围内的部分算），取其中点；一样长时取离目标近的；没有就硬切在目标处。"""
    lo = min(lo, hi)  # hi 是硬上限（单次时长），范围退化时以它为准
    best: tuple[tuple[int, float], int] | None = None
    for s, e in silences:
        a, b = max(s, lo), min(e, hi)
        if b <= a:
            continue
        mid = (a + b) // 2
        key = (b - a, -abs(mid - target))
        if best is None or key > best[0]:
            best = (key, mid)
    if best is not None:
        return best[1]
    return min(max(target, lo), hi)


def _cut(ffmpeg: str, src: Path, dst: Path, start_ms: int, duration_ms: int) -> None:
    args = cut_args(ffmpeg, src, dst, start_ms, duration_ms)
    # 不写 Xing/LAME 头：截到文件末尾时 ffmpeg 会把原文件的尾部填充写进新头，解码时开头被多裁掉约 0.25 秒，
    # 段内时间整体错位（实测 ffmpeg 8.1）。恒定码率的 mp3 没有这个头也能正常解码和计算时长。
    args[-1:-1] = ["-write_xing", "0"]
    try:
        out = subprocess.run(
            args,
            capture_output=True,
            timeout=600,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as e:
        raise MediaError("服务器上没有找到 ffmpeg") from e
    except subprocess.TimeoutExpired as e:
        raise MediaError("切分录音超时") from e
    if out.returncode != 0 or not dst.is_file() or dst.stat().st_size == 0:
        tail = out.stderr.decode("utf-8", "replace")[-300:]
        raise MediaError(f"切分录音失败（ffmpeg 退出码 {out.returncode}）：{tail}")
