"""术语表注入提示词：给出正确写法和常见的听错写法，帮大模型把识别错的专有名词改回来。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import GlossaryTerm

# 术语表可能很长，每次只带一部分：先带本段文字里出现过的，再按录入顺序补满
MAX_TERMS = 80
_UNSAFE = re.compile(r"[\r\n\t<>「」]+")


@dataclass(frozen=True)
class Term:
    term: str
    wrong_forms: tuple[str, ...]
    note: str


def load_terms(db: Session) -> list[Term]:
    rows = db.scalars(select(GlossaryTerm).order_by(GlossaryTerm.id)).all()
    out: list[Term] = []
    for t in rows:
        term = _clean(t.term)
        if not term:
            continue
        wrong = tuple(w for w in (_clean(x) for x in (t.wrong_forms or [])) if w and w != term)
        out.append(Term(term=term, wrong_forms=wrong, note=_clean(t.note)[:80]))
    return out


def _clean(value: str | None) -> str:
    # 术语由管理员录入，但仍按不可信文本处理：去掉换行和尖括号，免得冒充提示词的结构
    return _UNSAFE.sub(" ", value or "").strip()[:64]


def relevant_terms(terms: list[Term], text: str, limit: int = MAX_TERMS) -> list[Term]:
    if len(terms) <= limit:
        return terms
    lowered = text.lower()
    hit = [t for t in terms if any(w.lower() in lowered for w in (t.term, *t.wrong_forms))]
    rest = [t for t in terms if t not in hit]
    return (hit + rest)[:limit]


def glossary_section(terms: list[Term]) -> str:
    """渲染成提示词里的一节；没有术语时返回空字符串。"""
    if not terms:
        return ""
    lines = [
        "术语表：会议里提到下列术语时一律使用正确写法。“常被听成”是语音识别常见的错误写法，"
        "只有结合上下文能确认指的就是这个术语时才替换，不要强行套用。"
    ]
    for t in terms:
        line = f"- 「{t.term}」"
        if t.wrong_forms:
            line += " 常被听成：" + "、".join(f"「{w}」" for w in t.wrong_forms)
        if t.note:
            line += f"（{t.note}）"
        lines.append(line)
    return "\n".join(lines)
