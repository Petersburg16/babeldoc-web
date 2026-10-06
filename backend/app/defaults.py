"""“默认项”的通用规则：翻译模型、语音识别服务、会议整理方案三张表都有 enabled / is_default / sort_order。

只共享算法，表各管各的（会议用的模型和翻译模型保持分开）。
"""

from __future__ import annotations

from sqlalchemy import Select, select, update
from sqlalchemy.orm import Session

from .models import AsrProvider, MeetingLlmPreset, ModelProfile

# 有 enabled / is_default / sort_order 三列的表
Choosable = ModelProfile | AsrProvider | MeetingLlmPreset


def ensure_single_default[T: Choosable](db: Session, model: type[T], prefer: T | None = None) -> None:
    """始终只有一个启用的默认项（有启用的行时）。

    prefer 刚被设成默认时清掉其余行的默认标记；没有启用的默认项时，把按 (sort_order, id) 排第一的启用行设为默认。
    """
    if prefer is not None and prefer.is_default:
        db.execute(update(model).where(model.id != prefer.id).values(is_default=False))
    enabled = model.enabled.is_(True)
    if db.scalar(select(model.id).where(enabled, model.is_default.is_(True))) is None:
        db.execute(update(model).values(is_default=False))
        first = db.scalar(select(model).where(enabled).order_by(model.sort_order, model.id))
        if first is not None:
            first.is_default = True


def default_or_first[T: Choosable](db: Session, query: Select[tuple[T]], model: type[T]) -> T | None:
    """query 是调用方已经过滤好的可用行（至少要求 enabled）：先找默认项，没有就按 (sort_order, id) 取第一个。"""
    return db.scalar(query.where(model.is_default.is_(True))) or db.scalar(query.order_by(model.sort_order, model.id))
