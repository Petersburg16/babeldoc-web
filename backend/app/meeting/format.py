"""会议记录共用的小工具：说话人编号、时间与时长的写法、提示词里的标签中和。

说话人和时间这几个与前端 lib/meeting/format.ts 的同名用途对应（resolveSpeaker、clock、spoken），
规则改动时两边一起改。纪要、对话、导出、说话人识别和编辑接口都从这里取，不再各写一份。
"""

from __future__ import annotations

import functools
import re
from typing import Any

MAX_MERGE_HOPS = 20  # 沿合并关系最多找几次（防环）
UNNUMBERED = 1_000_000  # 不是“S 加数字”的编号排在最后
LONG_MS = 3600 * 1000  # 超过 1 小时的会议整场都用 h:mm:ss


# ---------- 说话人 ----------


def resolve_speaker(speakers: dict[str, Any], sid: str, *, known_only: bool = False) -> str:
    """沿 merged_into 找到最终的说话人编号（防环）。

    known_only：目标不在说话人表里就停在当前这位。编辑接口用它，免得把句子改到一个不存在的编号名下。
    """
    current = sid
    for _ in range(MAX_MERGE_HOPS):
        info = speakers.get(current)
        target = info.get("merged_into") if isinstance(info, dict) else None
        if not target or target == current or (known_only and target not in speakers):
            break
        current = str(target)
    return current


def speaker_number(sid: str) -> int:
    """S3 → 3；不是“S 加数字”的返回 UNNUMBERED。"""
    digits = sid[1:] if sid[:1] == "S" else ""
    return int(digits) if digits.isdigit() else UNNUMBERED


def speaker_sort_key(sid: str) -> tuple[int, str]:
    """按编号数字排序：S2 在 S10 前面。"""
    return speaker_number(sid), sid


def copy_speakers(speakers: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    # 拷贝一份再改，最后整体赋回去：SQLAlchemy 不追踪 JSON 列的就地修改
    return {k: dict(v) for k, v in (speakers or {}).items() if isinstance(v, dict)}


def new_speaker_entry() -> dict[str, Any]:
    """说话人表里新编号的初始内容（识别结果入库、手动新建说话人都用）。"""
    return {"name": "", "guess": None, "merged_into": None, "merge_hint": None}


# ---------- 时间 ----------


def is_long(ms: int) -> bool:
    return ms > LONG_MS


def clock(ms: int, long: bool = False) -> str:
    """会议内的绝对时间：mm:ss；long 为真或超过 1 小时时用 h:mm:ss（小时不补零）。

    一小时以上的会议整场都传 long=True，同一场会议只有一种写法，模型照抄时不会混。
    """
    total = max(0, int(ms) // 1000)
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if long or h else f"{m:02d}:{s:02d}"


def spoken(ms: int) -> str:
    """时长的口语说法：45 秒、35 分钟、1 小时 20 分。

    先舍入到整分钟再拆小时，不会写成“1 小时 60 分”。与前端 spoken() 的区别：前端 10 分钟以内还会写“N 分 M 秒”。
    """
    total = round(ms / 1000)
    if total < 60:
        return f"{total} 秒"
    hours, minutes = divmod(round(total / 60), 60)
    if not hours:
        return f"{minutes} 分钟"
    return f"{hours} 小时 {minutes} 分" if minutes else f"{hours} 小时"


# ---------- 提示词 ----------


@functools.cache
def _tag_pattern(tags: tuple[str, ...]) -> re.Pattern[str]:
    names = "|".join(re.escape(t) for t in tags)
    return re.compile(rf"<(\s*/?\s*(?:{names})\b)(\s*>)?", re.IGNORECASE)


def neutralize(text: str, tags: tuple[str, ...]) -> str:
    """正文里出现的 <transcript>、</transcript> 之类会让模型以为数据提前结束，把尖括号换成全角。

    tags 是这段提示词自己用来包裹数据的标签名；没写完的 <transcript 也换掉开头的尖括号。
    """
    return _tag_pattern(tags).sub(lambda m: "＜" + m.group(1) + (m.group(2) or "").replace(">", "＞"), text)
