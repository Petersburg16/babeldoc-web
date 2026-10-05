"""把各段的识别结果合成一份逐字稿：时间加上段偏移、统一说话人编号（S1、S2…）。

多段时（超长录音切段，见 split.py），相邻两段有几分钟重叠：
- 说话人配对：同一段声音在两段里各识别一遍，同一个人在重叠区的说话时间应当大段重合。按共同说话时长
  从大到小一对一配对（贪心），配不上的当作新的说话人，不自动合并，只给提示。
- 去重：在重叠区中部找一个两段都没人说话的空隙作为切点，前一段只留切点之前的句子、后一段只留之后的，
  这样既不丢句也不重复。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .asr.base import AsrSegment

MIN_SHARED_MS = 5_000  # 重叠区里同时说话至少这么久才配对
# 共同说话时长还要占“两人里在重叠区说得少的那位”的一半以上：换人时两段的句子边界不完全一致，
# 次数多了会累积出几秒的假重合；用较小者作分母，一人被拆成两个编号时真配对不受影响
MIN_SHARED_RATIO = 0.5
EDGE_MS = 20_000  # 去重切点避开重叠区两端：段首段尾的句子可能被截断、缺上下文


@dataclass
class MergedSegment:
    start_ms: int
    end_ms: int
    speaker: str  # 统一编号 S1、S2…
    text: str


@dataclass
class Alignment:
    segments: list[MergedSegment]
    notes: list[str]  # 给用户看的提示，说话人写成占位符 [[S3]]
    # 可能是同一人的说话人：{"S5": {"with": "S2", "reason": "..."}}，键是编号较大的那位
    merge_hints: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(slots=True)
class _Seg:
    start: int
    end: int
    speaker: str  # 该段内服务商给的编号
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
    result = align_parts(parts)
    return result.segments, result.notes


def align_parts(parts: list[tuple[int, int, list[AsrSegment]]]) -> Alignment:
    """merge_parts 的完整版，另外返回结构化的“可能是同一人”提示。"""
    ordered = sorted(parts, key=lambda p: p[0])
    if not ordered:
        return Alignment([], [])
    if len(ordered) == 1:
        offset, _, segments = ordered[0]
        return Alignment(normalize_single(segments, offset), [])

    pieces: list[list[_Seg]] = []
    for offset, _, segments in ordered:
        items = [
            _Seg(s.start_ms + offset, s.end_ms + offset, s.speaker, s.text.strip()) for s in segments if s.text.strip()
        ]
        items.sort(key=lambda s: (s.start, s.end))
        pieces.append(items)
    count = len(pieces)

    # 每段的“服务商编号 → 全局编号”，全局编号是内部整数，最后再按首次出现顺序改成 S1、S2…
    labels: list[dict[str, int]] = [{} for _ in range(count)]
    next_id = 0
    for seg in pieces[0]:
        if seg.speaker not in labels[0]:
            labels[0][seg.speaker] = next_id
            next_id += 1
    created: set[int] = set()  # 后面各段里没配上、新开的全局编号
    hints: list[tuple[int, int, int, int]] = []  # (全局编号, 可能同一人的全局编号, 重合毫秒, 前一段序号)
    cuts: list[int] = []
    for i in range(count - 1):
        a = ordered[i + 1][0]
        b = ordered[i][0] + ordered[i][1]
        prev, cur = pieces[i], pieces[i + 1]
        pairs, leftovers = _pair(prev, cur, a, b)
        for seg in cur:
            if seg.speaker in labels[i + 1]:
                continue
            if seg.speaker in pairs:
                labels[i + 1][seg.speaker] = labels[i][pairs[seg.speaker]]
            else:
                labels[i + 1][seg.speaker] = next_id
                created.add(next_id)
                next_id += 1
        for side, own, other, shared in leftovers:
            if side == "cur":
                hints.append((labels[i + 1][own], labels[i][other], shared, i))
            else:
                hints.append((labels[i][own], labels[i + 1][other], shared, i))
        cuts.append(_cut_point(prev, cur, a, b, cuts[-1] + 1 if cuts else None))

    kept: list[tuple[int, int, int, str, int]] = []
    for i, items in enumerate(pieces):
        low = cuts[i - 1] if i > 0 else None
        high = cuts[i] if i < count - 1 else None
        for seg in items:
            # 按句子中点归属：切点落在空隙里时就是“前一段留切点前、后一段留切点后”
            doubled = seg.start + seg.end
            if low is not None and doubled < 2 * low:
                continue
            if high is not None and doubled >= 2 * high:
                continue
            kept.append((seg.start, seg.end, labels[i][seg.speaker], seg.text, i))
    kept.sort(key=lambda t: (t[0], t[1], t[4]))

    final: dict[int, str] = {}
    segments: list[MergedSegment] = []
    for start, end, gid, text, _ in kept:
        if gid not in final:
            final[gid] = f"S{len(final) + 1}"
        segments.append(MergedSegment(start, end, final[gid], text))

    notes = [f"录音较长，分成 {count} 段识别，跨段的说话人已按重叠部分自动对齐，可能有误，请检查"]
    fresh = sorted((final[g] for g in created if g in final), key=_number)
    if fresh:
        notes.append(
            "、".join(f"[[{s}]]" for s in fresh)
            + " 在分段的重叠部分没能和前一段的说话人对上，已作为新的说话人：可能是中途加入的人，"
            "也可能和已有的说话人是同一人"
        )
    merge_hints: dict[str, dict[str, str]] = {}
    for own, other, shared, i in sorted(hints, key=lambda h: -h[2]):
        if own not in final or other not in final or own == other:
            continue
        later, earlier = sorted((final[own], final[other]), key=_number, reverse=True)
        if later in merge_hints:
            continue
        merge_hints[later] = {
            "with": earlier,
            "reason": f"分段识别时第 {i + 1}、{i + 2} 段的重叠部分有 {round(shared / 1000)} 秒发言重合",
        }
    if merge_hints:
        notes.append(
            "可能是同一人："
            + "、".join(f"[[{k}]] 与 [[{v['with']}]]" for k, v in merge_hints.items())
            + "，可在说话人栏里合并"
        )
    return Alignment(segments, notes, merge_hints)


def _number(label: str) -> int:
    return int(label[1:])


def _talk(items: list[_Seg], a: int, b: int) -> dict[str, int]:
    talk: dict[str, int] = defaultdict(int)
    for s in items:
        lo, hi = max(s.start, a), min(s.end, b)
        if hi > lo:
            talk[s.speaker] += hi - lo
    return talk


def _pair(prev: list[_Seg], cur: list[_Seg], a: int, b: int) -> tuple[dict[str, str], list[tuple[str, str, str, int]]]:
    """在重叠区 [a, b] 里配对说话人。

    返回 ({后一段编号: 前一段编号}, 没配上但有强候选的 [(哪一段, 自己, 候选, 重合毫秒)])。"""
    if b <= a:
        return {}, []
    left = [s for s in prev if s.end > a and s.start < b]
    right = [s for s in cur if s.end > a and s.start < b]
    talk_left, talk_right = _talk(left, a, b), _talk(right, a, b)
    shared: dict[tuple[str, str], int] = defaultdict(int)
    for p in left:
        for c in right:
            lo, hi = max(p.start, c.start, a), min(p.end, c.end, b)
            if hi > lo:
                shared[(p.speaker, c.speaker)] += hi - lo
    strong = sorted(
        (
            (ms, x, y)
            for (x, y), ms in shared.items()
            if ms >= MIN_SHARED_MS and ms >= MIN_SHARED_RATIO * min(talk_left[x], talk_right[y])
        ),
        key=lambda t: (-t[0], t[1], t[2]),
    )
    pairs: dict[str, str] = {}
    taken: set[str] = set()
    for _, x, y in strong:
        if x not in taken and y not in pairs:
            pairs[y] = x
            taken.add(x)
    leftovers: list[tuple[str, str, str, int]] = []
    seen: set[tuple[str, str]] = set()
    for ms, x, y in strong:
        # 只记每位没配上的人最强的那个候选（候选已经和别人配上了，比如一人被拆成两个编号）
        if y not in pairs and ("cur", y) not in seen:
            seen.add(("cur", y))
            leftovers.append(("cur", y, x, ms))
        if x not in taken and ("prev", x) not in seen:
            seen.add(("prev", x))
            leftovers.append(("prev", x, y, ms))
    return pairs, leftovers


def _cut_point(prev: list[_Seg], cur: list[_Seg], a: int, b: int, floor: int | None) -> int:
    """重叠区 [a, b] 里的去重切点。优先取中部两段都没人说话的最长空隙的中点；连续说话没有空隙时，
    取切开的句子最少的句子边界。floor：三段以上时，这个切点不能早于上一个切点。"""
    if floor is not None:
        a = max(a, floor)
    if b <= a:
        return a
    items = [s for s in (*prev, *cur) if s.end > a and s.start < b]
    middle = (a + b) // 2
    edge = min(EDGE_MS, (b - a) // 4)
    lo, hi = a + edge, b - edge

    busy = sorted((max(s.start, lo), min(s.end, hi)) for s in items if min(s.end, hi) > max(s.start, lo))
    best: tuple[tuple[int, int], int] | None = None
    cursor = lo
    for start, end in [*busy, (hi, hi)]:
        if start > cursor:
            mid = (cursor + start) // 2
            key = (start - cursor, -abs(mid - middle))
            if best is None or key > best[0]:
                best = (key, mid)
        cursor = max(cursor, end)
    if best is not None:
        return best[1]

    candidates = {t for s in items for t in (s.start, s.end) if lo <= t <= hi} | {lo, hi}

    def cost(t: int) -> tuple[int, int]:
        # 被切开的句子里“分错边”的那部分时长（按中点归属，错的是较短的一侧）
        wrong = sum(min(t - s.start, s.end - t) for s in items if s.start < t < s.end)
        return wrong, abs(t - middle)

    return min(sorted(candidates), key=cost)
