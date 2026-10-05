"""超长录音切段（split）与跨段说话人对齐、重叠区去重（align）。

对齐测试都从一份“真实发言”出发，按各段的时间范围切出各段的识别结果（段内时间、各段自己的说话人编号，
被段边界截断的句子文字加“（截断）”），合并后应当和真实发言逐句一致：不丢句、不重复、没有截断的半句。
"""

from __future__ import annotations

import math
import random
import shutil
import subprocess
import wave
from array import array
from collections.abc import Callable
from pathlib import Path

import pytest

from app.meeting import split
from app.meeting.align import align_parts, merge_parts, normalize_single
from app.meeting.asr.base import AsrSegment
from app.meeting.media import detect_silences, probe, transcode_args
from tests.conftest import build_config

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")

Truth = list[tuple[int, int, str, str]]  # (开始, 结束, 真实说话人, 文字)，绝对时间


def build_truth(who: Callable[[int, int], str], end_ms: int, gap: bool = True) -> Truth:
    """按时间铺满发言：句长 4–6.5 秒，句间停顿 0.6–1.5 秒（gap=False 时句句相接）。"""
    out: Truth = []
    t = 1000
    i = 0
    while True:
        length = 4000 + (i * 700) % 2500
        if t + length > end_ms:
            return out
        person = who(i, t)
        out.append((t, t + length, person, f"{person}的第 {i} 句"))
        t += length + ((600 + (i * 300) % 900) if gap else 0)
        i += 1


def rotate(*people: str) -> Callable[[int, int], str]:
    return lambda i, _t: people[i % len(people)]


def make_parts(
    truth: Truth,
    spans: list[tuple[int, int]],
    label: Callable[[int, str, int], str] = lambda _p, who, _i: who,
    shift: Callable[[int], int] = lambda _p: 0,
) -> list[tuple[int, int, list[AsrSegment]]]:
    """label(段号, 真实说话人, 句号) 给出该段里服务商的编号；shift(段号) 模拟不同段的句子边界略有出入。"""
    parts = []
    for p, (lo, hi) in enumerate(spans):
        segments = []
        for i, (s, e, who, text) in enumerate(truth):
            s, e = s + shift(p), e + shift(p)
            if e <= lo or s >= hi:
                continue
            truncated = s < lo or e > hi
            segments.append(
                AsrSegment(max(s, lo) - lo, min(e, hi) - lo, label(p, who, i), text + ("（截断）" if truncated else ""))
            )
        parts.append((lo, hi - lo, segments))
    return parts


def assert_complete(out, truth: Truth, *, check_times: bool = True) -> None:
    assert [s.text for s in out] == [t[3] for t in truth]
    if check_times:
        assert [(s.start_ms, s.end_ms) for s in out] == [(t[0], t[1]) for t in truth]
    assert all(a.start_ms <= b.start_ms for a, b in zip(out, out[1:], strict=False))


def speaker_map(out, truth: Truth) -> dict[str, set[str]]:
    """真实说话人 → 合并结果里用到的编号。"""
    seen: dict[str, set[str]] = {}
    for seg, t in zip(out, truth, strict=True):
        seen.setdefault(t[2], set()).add(seg.speaker)
    return seen


def assert_consistent(out, truth: Truth, people: tuple[str, ...]) -> None:
    seen = speaker_map(out, truth)
    for person in people:
        assert len(seen[person]) == 1, (person, seen)
    labels = [next(iter(seen[p])) for p in people]
    assert len(set(labels)) == len(labels), seen


# ---------- 对齐 ----------


def test_empty_and_single_part_unchanged():
    assert merge_parts([]) == ([], [])
    segments = [
        AsrSegment(5000, 9000, "b", "后来的话"),
        AsrSegment(1000, 4000, "a", " 先说的话 "),
        AsrSegment(1, 2, "c", " "),
    ]
    out, notes = merge_parts([(60_000, 120_000, segments)])
    assert out == normalize_single(segments, 60_000)
    assert [(s.start_ms, s.speaker, s.text) for s in out] == [(61_000, "S1", "先说的话"), (65_000, "S2", "后来的话")]
    assert notes == []


def test_three_parts_no_gap_no_duplicate_with_permuted_labels():
    truth = build_truth(rotate("张", "李", "王", "李", "张"), 400_000)
    spans = [(0, 160_000), (100_000, 280_000), (220_000, 400_000)]
    # 每段编号各不相同：第二段张、李互换，第三段整体轮换
    perms = [{"张": "0", "李": "1", "王": "2"}, {"张": "1", "李": "0", "王": "2"}, {"张": "2", "李": "0", "王": "1"}]
    parts = make_parts(truth, spans, label=lambda p, who, _i: perms[p][who])
    result = align_parts(parts)
    assert_complete(result.segments, truth)
    assert_consistent(result.segments, truth, ("张", "李", "王"))
    assert {s.speaker for s in result.segments} == {"S1", "S2", "S3"}
    assert result.segments[0].speaker == "S1"
    assert result.notes == ["录音较长，分成 3 段识别，跨段的说话人已按重叠部分自动对齐，可能有误，请检查"]
    assert result.merge_hints == {}


def test_swapped_labels_between_two_parts():
    truth = build_truth(rotate("张", "李"), 250_000)
    spans = [(0, 150_000), (90_000, 250_000)]
    parts = make_parts(
        truth, spans, label=lambda p, who, _i: ({"张": "A", "李": "B"} if p == 0 else {"张": "B", "李": "A"})[who]
    )
    out, notes = merge_parts(parts)
    assert_complete(out, truth)
    assert_consistent(out, truth, ("张", "李"))
    assert len(notes) == 1


def test_speaker_absent_in_overlap_becomes_new_speaker():
    # 王只在重叠区 [100 s, 160 s] 之外说话：没有依据配对，第二段里的王要当作新的说话人，并提示
    def who(i: int, t: int) -> str:
        if 85_000 <= t < 175_000:
            return ("张", "李")[i % 2]
        return ("张", "李", "王")[i % 3]

    truth = build_truth(who, 280_000)
    parts = make_parts(truth, [(0, 160_000), (100_000, 280_000)], label=lambda p, w, _i: f"{p}-{w}")
    result = align_parts(parts)
    assert_complete(result.segments, truth)
    seen: dict[str, set[str]] = {}
    for seg, t in zip(result.segments, truth, strict=True):
        seen.setdefault(f"{t[2]}{'前' if t[0] < 130_000 else '后'}", set()).add(seg.speaker)
    assert seen["张前"] == seen["张后"] and len(seen["张前"]) == 1
    assert seen["李前"] == seen["李后"] and len(seen["李前"]) == 1
    (early,), (late,) = seen["王前"], seen["王后"]
    assert early != late
    assert late not in seen["张前"] | seen["李前"]
    assert any(f"[[{late}]]" in note and "新的说话人" in note for note in result.notes)
    assert late not in result.merge_hints  # 重叠区里没有共同发言，不能凭空猜


def test_person_split_into_two_labels_pairs_one_and_hints_the_other():
    truth = build_truth(rotate("张", "李", "张"), 260_000)
    spans = [(0, 160_000), (100_000, 260_000)]

    def label(p: int, who: str, i: int) -> str:
        if p == 1 and who == "张":
            return "a1" if i % 4 < 2 else "a2"  # 后一段把张拆成了两个编号
        return {"张": "0", "李": "1"}[who] if p == 0 else "b"

    result = align_parts(make_parts(truth, spans, label=label))
    assert_complete(result.segments, truth)
    zhang_before = {s.speaker for s, t in zip(result.segments, truth, strict=True) if t[2] == "张" and t[0] < 100_000}
    zhang_after = {s.speaker for s, t in zip(result.segments, truth, strict=True) if t[2] == "张" and t[0] >= 160_000}
    assert zhang_before == {"S1"}
    assert "S1" in zhang_after and len(zhang_after) == 2
    (other,) = zhang_after - {"S1"}
    li_labels = {s.speaker for s, t in zip(result.segments, truth, strict=True) if t[2] == "李"}
    assert len(li_labels) == 1
    assert result.merge_hints[other]["with"] == "S1"
    assert "秒发言重合" in result.merge_hints[other]["reason"]
    assert any(f"[[{other}]] 与 [[S1]]" in note for note in result.notes)


def test_only_one_speaker_in_overlap():
    def who(i: int, t: int) -> str:
        return "张" if 90_000 <= t < 170_000 else ("张", "李")[i % 2]

    truth = build_truth(who, 260_000)
    parts = make_parts(truth, [(0, 160_000), (100_000, 260_000)], label=lambda p, w, _i: f"{w}{p}")
    result = align_parts(parts)
    assert_complete(result.segments, truth)
    seen = speaker_map(result.segments, truth)
    assert seen["张"] == {"S1"}  # 重叠区里的那个人正常配上
    li_before = {s.speaker for s, t in zip(result.segments, truth, strict=True) if t[2] == "李" and t[0] < 100_000}
    li_after = {s.speaker for s, t in zip(result.segments, truth, strict=True) if t[2] == "李" and t[0] >= 160_000}
    assert len(li_before) == len(li_after) == 1 and li_before != li_after
    assert any("新的说话人" in note for note in result.notes)


def test_abutting_sentences_with_shifted_boundaries():
    # 句句相接没有空隙，后一段的句子边界整体晚 150 ms：只能在句子边界上切，仍然不丢不重
    truth = build_truth(rotate("张", "李", "王"), 255_000, gap=False)
    parts = make_parts(truth, [(0, 160_000), (100_000, 260_000)], shift=lambda p: 150 * p)
    out, _ = merge_parts(parts)
    assert_complete(out, truth, check_times=False)
    assert_consistent(out, truth, ("张", "李", "王"))


def test_brief_shared_time_is_not_paired():
    # 后一段的 y 只有 5 秒和 X 重合，却在重叠区里说了 21 秒：占比太低，不配对、也不提示合并
    first = [AsrSegment(t, t + 5000, "X", f"X{t}") for t in range(100_000, 160_000, 6000)]
    second = [AsrSegment(50_000, 56_000, "y", "y1"), AsrSegment(65_000, 80_000, "y", "y2")]
    result = align_parts([(0, 200_000, first), (100_000, 200_000, second)])
    labels = {s.text: s.speaker for s in result.segments}
    assert labels["y2"] != labels["X100000"]
    assert result.merge_hints == {}


# ---------- 切点 ----------


def test_compute_spans_hard_cut_without_silence():
    spans = split.compute_spans(60_000, 25_000, [], overlap_ms=6_000, search_ms=3_000)
    assert spans == [(0, 19_500), (13_500, 33_000), (27_000, 46_500), (40_500, 60_000)]


def test_compute_spans_prefers_longest_silence_near_target():
    # 第一段理想终点 19.5 s，搜索 ±2.5 s：1 秒长的静音胜过更近的短静音，超出范围的部分不算长度
    silences = [(17_200, 17_500), (20_000, 21_000), (21_800, 23_000)]
    spans = split.compute_spans(60_000, 25_000, silences, overlap_ms=6_000, search_ms=3_000)
    assert spans[0] == (0, 20_500)
    assert spans[1][0] == 14_500  # 下一段起点附近没有静音，硬切在终点前 6 s


def test_compute_spans_invariants():
    rng = random.Random(7)
    for _ in range(300):
        max_part = rng.randint(20_000, 200_000)
        duration = rng.randint(max_part + 1, max_part * 6)
        overlap = rng.randint(0, max_part // 2)
        silences = []
        t = rng.randint(0, 3000)
        while t < duration:
            length = rng.randint(300, 4000)
            silences.append((t, t + length))
            t += length + rng.randint(500, 30_000)
        spans = split.compute_spans(duration, max_part, silences, overlap_ms=overlap, search_ms=rng.randint(0, 60_000))
        assert spans[0][0] == 0 and spans[-1][1] == duration
        assert all(0 < end - start <= max_part for start, end in spans)
        effective = min(overlap, int(max_part * split.FILL) // 3)
        for (s1, e1), (s2, _) in zip(spans, spans[1:], strict=False):
            assert s1 < s2 < e1 or effective == 0
            assert effective * 0.75 - 1 <= e1 - s2 <= effective * 1.25 + 1


def test_plan_parts_short_audio_is_not_cut(tmp_path, monkeypatch):
    config = build_config(tmp_path, monkeypatch)
    parts = split.plan_parts(config, tmp_path / "audio.mp3", 30_000, 60_000)
    assert parts == [{"index": 0, "offset_ms": 0, "duration_ms": 30_000, "file": "audio.mp3"}]


# ---------- 真实 ffmpeg ----------


def write_wav(path: Path, pattern: list[tuple[float, bool]], rate: int = 16000) -> None:
    """pattern: [(秒数, 是否有声)]，有声是 440 Hz 正弦波，无声是全零。"""
    samples = array("h")
    for seconds, sound in pattern:
        n = int(seconds * rate)
        if sound:
            samples.extend(int(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(n))
        else:
            samples.extend([0] * n)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


def to_mp3(ffmpeg: str, wav: Path) -> Path:
    mp3 = wav.parent / "audio.mp3"
    out = subprocess.run(transcode_args(ffmpeg, wav, mp3), capture_output=True, check=False)
    assert out.returncode == 0, out.stderr[-500:]
    return mp3


@needs_ffmpeg
def test_plan_parts_cuts_in_silences(tmp_path, monkeypatch):
    config = build_config(tmp_path, monkeypatch)
    meeting_dir = tmp_path / "m"
    meeting_dir.mkdir()
    write_wav(meeting_dir / "src.wav", [(2.5, True), (1.0, False)] * 17 + [(2.5, True)])
    audio = to_mp3(config.ffmpeg, meeting_dir / "src.wav")
    (meeting_dir / "part-09.mp3").write_bytes(b"stale")  # 上次切剩的文件要清掉
    duration = probe(config.ffprobe, audio).duration_ms
    assert 61_500 <= duration <= 62_500

    kwargs = {"overlap_ms": 6_000, "search_ms": 3_000, "min_silence": 0.5}
    parts = split.plan_parts(config, audio, duration, 25_000, **kwargs)

    assert [p["index"] for p in parts] == list(range(len(parts))) and len(parts) >= 3
    assert [p["file"] for p in parts] == [f"part-{i:02d}.mp3" for i in range(len(parts))]
    assert not (meeting_dir / "part-09.mp3").exists()
    assert parts[0]["offset_ms"] == 0
    assert parts[-1]["offset_ms"] + parts[-1]["duration_ms"] == duration
    assert all(p["duration_ms"] <= 25_000 for p in parts)
    silences = detect_silences(config.ffmpeg, audio, split.NOISE_DB, 0.5)
    for prev, nxt in zip(parts, parts[1:], strict=False):
        end, start = prev["offset_ms"] + prev["duration_ms"], nxt["offset_ms"]
        assert 4_500 <= end - start <= 7_500
        for t in (end, start):
            assert any(s <= t <= e for s, e in silences), (t, silences)

    for p in parts:
        path = meeting_dir / p["file"]
        assert path.is_file()
        assert abs(probe(config.ffprobe, path).duration_ms - p["duration_ms"]) <= 200
        # 段内静音的位置加上偏移，应当对得上原录音里的静音（检查 -ss 截取的准确度）
        for s, e in detect_silences(config.ffmpeg, path, split.NOISE_DB, 0.5):
            if s <= 50 or e >= p["duration_ms"] - 50:
                continue
            assert any(
                abs(S - (s + p["offset_ms"])) <= 150 and abs(E - (e + p["offset_ms"])) <= 150 for S, E in silences
            )


@needs_ffmpeg
def test_plan_parts_hard_cut_without_silence(tmp_path, monkeypatch):
    config = build_config(tmp_path, monkeypatch)
    write_wav(tmp_path / "src.wav", [(60.0, True)])
    audio = to_mp3(config.ffmpeg, tmp_path / "src.wav")
    duration = probe(config.ffprobe, audio).duration_ms
    kwargs = {"overlap_ms": 6_000, "search_ms": 3_000}
    parts = split.plan_parts(config, audio, duration, 25_000, **kwargs)
    expected = split.compute_spans(duration, 25_000, [], **kwargs)
    assert [(p["offset_ms"], p["offset_ms"] + p["duration_ms"]) for p in parts] == expected
    for p in parts:
        assert abs(probe(config.ffprobe, tmp_path / p["file"]).duration_ms - p["duration_ms"]) <= 200
