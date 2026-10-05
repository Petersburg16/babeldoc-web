"""根据称呼和自我介绍猜说话人的名字。

只写建议（speakers[编号].guess），不改用户确认过的名字；两位被猜成同一个人时给后一位写 merge_hint，
提示可能是同一人（长录音切段、说话人分离都可能把一个人拆成两个编号），由用户确认后再合并。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..llm import LlmClient
from ..models import Meeting, MeetingSegment
from . import prompts
from .llmcall import ask

if TYPE_CHECKING:
    from .manager import MeetingManager

log = logging.getLogger("bdw.meetings.speakers")

OPENING_LINES = 8  # 会议开头几句：寒暄、点名、自我介绍多在这里
FIRST_LINES = 5  # 每位待猜说话人的前几句
EXCERPT_CHARS = 6000
LINE_CHARS = 160
MAX_NAME = 20

SELF_CUES = re.compile(r"我是|我叫|我姓|本人是|自我介绍|\b(?:I'm|I am|my name is|this is)\b", re.IGNORECASE)
ADDRESS_CUES = re.compile(
    r"老师|教授|师兄|师姐|师弟|师妹|同学|博士|主任|院长|所长|经理|总监|老板|"
    r"你说|您说|你来|您来|请你|请您|请大家|有请|下面请|接下来请|轮到|谢谢"
)
CONFIDENCE = {"high": "high", "medium": "medium", "low": "low", "高": "high", "中": "medium", "低": "low"}
NOT_A_NAME = {"未知", "无", "不确定", "不详", "不清楚", "unknown", "none", "null", "n/a", "?", "？"}
_QUOTES = "\"'“”‘’「」『』《》()（）[]【】<>*`"
_SPEAKER_ID = re.compile(r"S\d+", re.IGNORECASE)


@dataclass(frozen=True)
class Guess:
    name: str
    confidence: str
    evidence: str


@dataclass(frozen=True)
class Row:
    speaker: str
    text: str


def speaker_number(sid: str) -> int:
    return int(sid[1:]) if sid[1:].isdigit() else 10**6


def lock_meeting(db: Session, meeting_id: str) -> None:
    """先发一条空更新拿到 SQLite 的写锁，之后在同一事务里读改写 speakers 这个 JSON 列就不会和别人互相覆盖。"""
    db.execute(
        update(Meeting)
        .where(Meeting.id == meeting_id)
        .values(op=Meeting.op)
        .execution_options(synchronize_session=False)
    )


def pick_excerpt(rows: list[Row], candidates: set[str], budget: int = EXCERPT_CHARS) -> list[str]:
    """挑出最可能透露身份的句子：会议开头、自我介绍、每人的前几句、带称呼的句子及其前后句。"""
    priority: list[int] = list(range(min(OPENING_LINES, len(rows))))

    def window(i: int) -> list[int]:
        return [j for j in (i - 1, i, i + 1) if 0 <= j < len(rows)]

    for i, row in enumerate(rows):
        if row.speaker in candidates and SELF_CUES.search(row.text):
            priority += window(i)
    firsts: dict[str, list[int]] = {sid: [] for sid in candidates}
    for i, row in enumerate(rows):
        bucket = firsts.get(row.speaker)
        if bucket is not None and len(bucket) < FIRST_LINES:
            bucket.append(i)
    for rank in range(FIRST_LINES):  # 轮流取，避免预算被第一位说话人用完
        priority += [b[rank] for b in firsts.values() if rank < len(b)]
    for i, row in enumerate(rows):
        if ADDRESS_CUES.search(row.text) and any(rows[j].speaker in candidates for j in window(i)):
            priority += window(i)

    chosen: set[int] = set()
    used = 0
    for i in priority:
        if i in chosen:
            continue
        cost = min(len(rows[i].text), LINE_CHARS) + 8
        if used + cost > budget:
            break
        chosen.add(i)
        used += cost
    out: list[str] = []
    previous = -1
    for i in sorted(chosen):
        if previous >= 0 and i != previous + 1:
            out.append("……")
        text = rows[i].text[:LINE_CHARS] + ("…" if len(rows[i].text) > LINE_CHARS else "")
        out.append(f"[{rows[i].speaker}] {prompts.fence(text)}")
        previous = i
    return out


def clean_name(value: Any) -> str:
    name = str(value or "").strip().strip(_QUOTES).strip()
    if not name or "\n" in name or len(name) > MAX_NAME:
        return ""
    if name.lower() in NOT_A_NAME or "说话人" in name or _SPEAKER_ID.fullmatch(name):
        return ""
    return name


def _json_items(text: str) -> list[tuple[str, Any, Any, Any]] | None:
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        return None
    end = max(text.rfind("}"), text.rfind("]"))
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    if isinstance(data, dict) and isinstance(data.get("speakers"), list | dict):
        data = data["speakers"]
    items: list[tuple[str, Any, Any, Any]] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                sid = item.get("id") or item.get("speaker") or ""
                items.append((str(sid), item.get("name"), item.get("confidence"), item.get("evidence")))
    elif isinstance(data, dict):
        for sid, item in data.items():
            if isinstance(item, dict):
                items.append((str(sid), item.get("name"), item.get("confidence"), item.get("evidence")))
            else:
                items.append((str(sid), item, None, None))
    else:
        return None
    return items


def _line_items(text: str) -> list[tuple[str, Any, Any, Any]]:
    items: list[tuple[str, Any, Any, Any]] = []
    for raw in text.splitlines():
        parts = [p.strip() for p in re.split(r"[|｜]", raw.strip().lstrip("-*•· ").strip())]
        if len(parts) < 2:
            continue
        sid = _SPEAKER_ID.search(parts[0])
        if not sid:
            continue
        confidence = parts[2] if len(parts) > 2 else None
        evidence = "|".join(parts[3:]) if len(parts) > 3 else None
        items.append((sid.group(0), parts[1], confidence, evidence))
    return items


def parse_guesses(text: str, candidates: set[str]) -> dict[str, Guess]:
    """宽松解析：先试 JSON，不行再按“编号|名字|把握|依据”逐行解析。不认识的编号、不像名字的一律忽略。"""
    items = _json_items(text)
    if not items:
        items = _line_items(text)
    out: dict[str, Guess] = {}
    for sid, name, confidence, evidence in items:
        sid = sid.strip().upper()
        if sid not in candidates or sid in out:
            continue
        cleaned = clean_name(name)
        if not cleaned:
            continue
        level = CONFIDENCE.get(str(confidence or "").strip().lower(), "low")
        out[sid] = Guess(name=cleaned, confidence=level, evidence=str(evidence or "").strip().strip(_QUOTES)[:200])
    return out


def apply_merge_hints(speakers: dict[str, dict[str, Any]], candidates: set[str]) -> None:
    """名字相同（含与已确认的名字相同）的，给后一位写 merge_hint 指向前一位。"""

    def key(name: str) -> str:
        return re.sub(r"\s+", "", name).lower()

    order = sorted(speakers, key=speaker_number)
    claimed: dict[str, str] = {}
    for sid in order:
        info = speakers[sid]
        name = str(info.get("name") or "").strip()
        if name and not info.get("merged_into"):
            claimed.setdefault(key(name), sid)
    for sid in order:
        info = speakers[sid]
        if sid not in candidates or info.get("name") or info.get("merged_into"):
            continue
        guess = info.get("guess")
        if not guess:
            continue
        other = claimed.setdefault(key(guess["name"]), sid)
        if other != sid:
            info["merge_hint"] = {
                "with": other,
                "reason": f"两位都被认为是「{guess['name']}」，可能是同一个人被分成了两个编号，确认后可以合并",
            }


async def guess_speakers(manager: MeetingManager, meeting_id: str, client: LlmClient) -> int:
    """给还没命名的说话人猜名字，返回猜出的人数。大模型出错时抛 LlmError，由调用方汇总成警告。"""
    loaded = await asyncio.to_thread(_load, manager, meeting_id)
    if loaded is None:
        return 0
    speakers, rows = loaded
    candidates = [
        sid
        for sid in sorted(speakers, key=speaker_number)
        if not str(speakers[sid].get("name") or "").strip() and not speakers[sid].get("merged_into")
    ]
    if not candidates or not rows:
        return 0
    known = {
        sid: prompts.fence(str(info["name"]).strip())
        for sid, info in sorted(speakers.items(), key=lambda kv: speaker_number(kv[0]))
        if str(info.get("name") or "").strip() and not info.get("merged_into")
    }
    excerpt = pick_excerpt(rows, set(candidates))
    json_mode = client.cfg.json_mode
    messages = [
        {"role": "system", "content": prompts.speakers_system(json_mode)},
        {"role": "user", "content": prompts.speakers_user(candidates, known, excerpt)},
    ]
    result = await ask(manager, meeting_id, client, messages, json_object=json_mode)
    guesses = parse_guesses(result.text, set(candidates))
    await asyncio.to_thread(_store, manager, meeting_id, set(candidates), guesses)
    manager.publish(meeting_id)
    log.info("meeting %s: guessed %d of %d speakers", meeting_id, len(guesses), len(candidates))
    return len(guesses)


def _load(manager: MeetingManager, meeting_id: str) -> tuple[dict[str, dict[str, Any]], list[Row]] | None:
    with manager.Session() as db:
        m = db.get(Meeting, meeting_id)
        if m is None or m.deleted_at is not None:
            return None
        speakers = {k: dict(v) for k, v in (m.speakers or {}).items() if isinstance(v, dict)}
        rows = [
            Row(speaker, text.strip())
            for speaker, text in db.execute(
                select(MeetingSegment.speaker, MeetingSegment.text)
                .where(MeetingSegment.meeting_id == meeting_id)
                .order_by(MeetingSegment.idx)
            ).all()
            if text.strip()
        ]
    return speakers, rows


def _store(manager: MeetingManager, meeting_id: str, candidates: set[str], guesses: dict[str, Guess]) -> None:
    with manager.Session() as db:
        lock_meeting(db, meeting_id)
        m = db.get(Meeting, meeting_id)
        if m is None or m.deleted_at is not None:
            return
        db.refresh(m)
        speakers = {k: dict(v) for k, v in (m.speakers or {}).items() if isinstance(v, dict)}
        touched: set[str] = set()
        for sid in candidates:
            info = speakers.get(sid)
            # 大模型思考期间用户已经命名或合并的，不再动
            if info is None or str(info.get("name") or "").strip() or info.get("merged_into"):
                continue
            guess = guesses.get(sid)
            info["guess"] = asdict(guess) if guess else None
            if (info.get("merge_hint") or {}).get("source") != "align":  # 切段对齐留下的提示保留
                info["merge_hint"] = None
            touched.add(sid)
        apply_merge_hints(speakers, touched)
        m.speakers = speakers
        db.commit()
