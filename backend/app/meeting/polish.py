"""分块整理逐字稿：语音识别出原文 → 大模型补标点、去口头禅和重复、按术语表纠正专有名词。

每块约 3000 字，尽量在说话人切换处切开，附上前两句作只读上下文。大模型逐行输出“#序号 整理后文字”，
程序逐句校验，任何对不上的地方都退回原文：宁可少整理，也不能让大模型改掉或编出会议内容。
用户手动改过的句子（edited=1）既不送去整理，也不会被覆盖。
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from ..llm import LlmClient, LlmError
from ..models import Meeting, MeetingSegment
from . import glossary, prompts
from .llmcall import ask, is_fatal

if TYPE_CHECKING:
    from .manager import MeetingManager

log = logging.getLogger("bdw.meetings.polish")

CHUNK_CHARS = 3000
SPLIT_AFTER = 0.6  # 块长到上限的 60% 以后，遇到说话人切换就切
CONTEXT_LINES = 2
MAX_PARALLEL = 3
ATTEMPTS = 2  # 整块失败重试一次
RATIO_MIN, RATIO_MAX = 0.4, 1.3
# 同音纠错、术语统一会换几个字；超过 max(4 字, 三成) 的新字视为添加了内容
MAX_NEW_CHARS, NEW_CHAR_SHARE = 4, 0.3

_OUT_LINE = re.compile(r"^\s*[#＃]\s*(\d+)\s*[:：.、]?\s*(.*)$")
_SPEAKER_TAG = re.compile(r"^\[\s*S\d+\s*\]\s*")
# 模型偶尔会写成 S1：、[[S1]]、【S1】这类变体；只在原句不是这样开头时才去掉
_LOOSE_TAG = re.compile(
    r"^(?:\[\[?|【|\(|（)?\s*S\d+\s*(?:\]\]?|】|\)|）)?\s*[:：]\s*|^(?:\[\[|【)\s*S\d+\s*(?:\]\]|】)\s*"
)
_STRAY_TAG = re.compile(r"</?\s*(?:transcript|context)\s*>", re.IGNORECASE)


@dataclass(frozen=True)
class Line:
    idx: int
    speaker: str
    text: str


@dataclass
class PolishReport:
    total: int = 0  # 块数
    failed: int = 0
    changed: int = 0  # 实际改动的句数
    rejected: int = 0  # 校验没通过、保留原样的句数
    state: str = "raw"
    warning: str | None = None
    fatal: LlmError | None = None  # 密钥无效、连不上等：后续步骤也不用再试了


def make_chunks(lines: list[Line], limit: int = CHUNK_CHARS) -> list[list[Line]]:
    chunks: list[list[Line]] = []
    current: list[Line] = []
    size = 0
    for line in lines:
        if current:
            switch = line.speaker != current[-1].speaker
            if size + len(line.text) > limit or (switch and size >= limit * SPLIT_AFTER):
                chunks.append(current)
                current, size = [], 0
        current.append(line)
        size += len(line.text)
    if current:
        chunks.append(current)
    return chunks


def content_length(text: str) -> int:
    """不算空白和标点符号：补标点会让句子变长，但不算添加内容。"""
    return sum(1 for ch in text if not ch.isspace() and unicodedata.category(ch)[0] not in "PS")


def content_chars(text: str) -> Counter[str]:
    return Counter(ch for ch in text if not ch.isspace() and unicodedata.category(ch)[0] not in "PS")


def added_content(raw: str, polished: str) -> bool:
    """整理结果里出现了原句没有的字，而且超出同音纠错、术语统一能解释的量。"""
    new = content_chars(polished) - content_chars(raw)
    total = content_length(polished)
    return sum(new.values()) > max(MAX_NEW_CHARS, NEW_CHAR_SHARE * total)


def acceptable(raw: str, polished: str) -> bool:
    if not polished.strip():
        return False
    before, after = content_length(raw), content_length(polished)
    if before == 0:
        return False
    return RATIO_MIN <= after / before <= RATIO_MAX and not added_content(raw, polished)


def parse_output(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for raw in text.splitlines():
        m = _OUT_LINE.match(raw)
        if not m:
            continue  # 代码块标记、前言之类的杂行直接忽略；真漏了句子会在序号校验里发现
        body = _STRAY_TAG.sub("", m.group(2))
        body = _SPEAKER_TAG.sub("", body.strip()).strip()
        out.append((int(m.group(1)), body))
    return out


def check_output(lines: list[Line], output: str) -> tuple[dict[int, str], int] | None:
    """返回 (序号 → 采用的整理结果, 被拒绝的句数)；序号集合对不上返回 None，表示整块作废。

    被拒绝的句子不在结果里，也就不会被写回：首次整理时保留识别原文，重新整理时保留上一次的结果。
    """
    parsed = parse_output(output)
    numbers = [i for i, _ in parsed]
    if len(numbers) != len(set(numbers)) or set(numbers) != {line.idx for line in lines}:
        return None
    by_idx = dict(parsed)
    accepted: dict[int, str] = {}
    grew: list[int] = []  # 因为“多出内容”被拒的句子在块内的位置
    for pos, line in enumerate(lines):
        polished = by_idx[line.idx]
        if not _LOOSE_TAG.match(line.text):
            polished = _LOOSE_TAG.sub("", polished, count=1).strip()
        if acceptable(line.text, polished):
            accepted[line.idx] = polished
        elif content_length(polished) > content_length(line.text) or added_content(line.text, polished):
            grew.append(pos)
    # 一句被塞进了别处的文字，相邻句往往就少了这段：一起保留原样，免得悄悄丢字或重复
    for pos in grew:
        for near in (pos - 1, pos + 1):
            if 0 <= near < len(lines):
                accepted.pop(lines[near].idx, None)
    return accepted, len(lines) - len(accepted)


async def polish_meeting(
    manager: MeetingManager,
    meeting_id: str,
    client: LlmClient,
    *,
    previous: str | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> PolishReport:
    """整理整场会议。previous 是开始前的 transcript_state（重跑时接口已经先改成 polishing，由它传进来）。"""
    loaded = await asyncio.to_thread(_load, manager, meeting_id)
    if loaded is None:
        return PolishReport()
    state_before, segments, terms = loaded
    if previous is None or previous in ("polishing", "none"):
        previous = state_before if state_before not in ("polishing", "none") else "raw"
    manager.publish(meeting_id)

    lines = [Line(s.idx, s.speaker, s.raw_text.strip()) for s in segments if not s.edited and s.raw_text.strip()]
    chunks = make_chunks(lines, CHUNK_CHARS)
    position = {s.idx: i for i, s in enumerate(segments)}
    report = PolishReport(total=len(chunks))
    succeeded = 0
    last_error = ""
    finished = False

    async def run(chunk: list[Line], sem: asyncio.Semaphore) -> None:
        nonlocal succeeded, last_error
        async with sem:
            if report.fatal is not None:
                outcome: tuple[dict[int, str], int] | LlmError = report.fatal
            else:
                start = position[chunk[0].idx]
                context = [(s.speaker, s.text) for s in segments[max(0, start - CONTEXT_LINES) : start] if s.text]
                text = "".join(line.text for line in chunk)
                system = prompts.polish_system(glossary.glossary_section(glossary.relevant_terms(terms, text)))
                try:
                    outcome = await _polish_chunk(manager, meeting_id, client, system, context, chunk)
                except Exception as e:  # 程序错误也只算这一块失败，不连累整场会议
                    log.exception("meeting %s: polishing chunk at #%d crashed", meeting_id, chunk[0].idx)
                    outcome = LlmError(f"{e.__class__.__name__}: {e}")
            if isinstance(outcome, LlmError):
                report.failed += 1
                last_error = last_error or str(outcome)
                if is_fatal(outcome):
                    report.fatal = outcome
            else:
                accepted, rejected = outcome
                report.rejected += rejected
                changed = await asyncio.to_thread(_write, manager, meeting_id, accepted)
                report.changed += changed
                succeeded += 1
                if changed:
                    manager.publish(meeting_id)
            if on_progress:
                on_progress((succeeded + report.failed) / max(1, report.total))

    try:
        sem = asyncio.Semaphore(MAX_PARALLEL)
        await asyncio.gather(*(run(chunk, sem) for chunk in chunks))
        finished = True
    finally:
        if not finished:
            report.failed = report.total - succeeded
        report.state = _final_state(previous, report.total, succeeded, bool(segments))
        # 被取消时也要把“整理中”改回来；这里不能再 await
        _set_state(manager, meeting_id, report.state)
        manager.publish(meeting_id)

    if report.failed:
        if previous == "polished":
            report.warning = f"重新整理时有 {report.failed} / {report.total} 块失败，这些部分保留了上一次的整理结果"
        elif report.failed == report.total:
            report.warning = f"整理逐字稿失败，保留了识别原文（{last_error}）"
        else:
            report.warning = (
                f"逐字稿有 {report.failed} / {report.total} 块整理失败，这些部分保留了识别原文，可稍后重新整理"
            )
    log.info(
        "meeting %s polished: %d chunks, %d failed, %d lines changed, %d rejected",
        meeting_id,
        report.total,
        report.failed,
        report.changed,
        report.rejected,
    )
    return report


async def _polish_chunk(
    manager: MeetingManager,
    meeting_id: str,
    client: LlmClient,
    system: str,
    context: list[tuple[str, str]],
    chunk: list[Line],
) -> tuple[dict[int, str], int] | LlmError:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompts.polish_user(context, [(x.idx, x.speaker, x.text) for x in chunk])},
    ]
    error = LlmError("大模型的输出和逐字稿的句子对不上")
    for _ in range(ATTEMPTS):
        try:
            result = await ask(manager, meeting_id, client, messages, temperature=0)
        except LlmError as e:
            if is_fatal(e):
                return e
            error = e
            continue
        checked = check_output(chunk, result.text)
        if checked is not None:
            return checked
        log.warning(
            "meeting %s: chunk at #%d came back with mismatched lines (finish_reason=%s)",
            meeting_id,
            chunk[0].idx,
            result.finish_reason,
        )
    return error


def _final_state(previous: str, total: int, succeeded: int, has_segments: bool) -> str:
    if not has_segments:
        return previous
    if succeeded == total or previous == "polished":
        # 重新整理失败的块保留着上一次的整理结果，整体仍是“已整理”
        return "polished"
    if succeeded == 0:
        return previous
    return "partial"


def _load(manager: MeetingManager, meeting_id: str) -> tuple[str, list[MeetingSegment], list[glossary.Term]] | None:
    with manager.Session() as db:
        m = db.get(Meeting, meeting_id)
        if m is None or m.deleted_at is not None:
            return None
        state = m.transcript_state
        m.transcript_state = "polishing"
        db.commit()
        segments = list(
            db.scalars(
                select(MeetingSegment).where(MeetingSegment.meeting_id == meeting_id).order_by(MeetingSegment.idx)
            ).all()
        )
        terms = glossary.load_terms(db)
        db.expunge_all()
    return state, segments, terms


def _write(manager: MeetingManager, meeting_id: str, accepted: dict[int, str]) -> int:
    changed = 0
    with manager.Session() as db:
        for idx, text in accepted.items():
            result = db.execute(
                update(MeetingSegment)
                .where(
                    MeetingSegment.meeting_id == meeting_id,
                    MeetingSegment.idx == idx,
                    MeetingSegment.edited.is_(False),  # 用户手动改过的句子永远不覆盖（哪怕是整理途中改的）
                    MeetingSegment.text != text,
                )
                .values(text=text)
                .execution_options(synchronize_session=False)
            )
            changed += result.rowcount or 0
        if changed:
            db.execute(
                update(Meeting)
                .where(Meeting.id == meeting_id)
                .values(transcript_rev=Meeting.transcript_rev + 1)
                .execution_options(synchronize_session=False)
            )
        db.commit()
    return changed


def _set_state(manager: MeetingManager, meeting_id: str, state: str) -> None:
    with manager.Session() as db:
        db.execute(
            update(Meeting)
            .where(Meeting.id == meeting_id)
            .values(transcript_state=state)
            .execution_options(synchronize_session=False)
        )
        db.commit()
