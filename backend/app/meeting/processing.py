"""识别完成后的大模型处理：猜说话人名字、分块整理逐字稿、生成纪要，以及完成后的单项重跑。

约定：这里的函数自己处理大模型错误，返回给用户看的警告文字（没有就 None），不向外抛异常；
只有任务被取消时让 CancelledError 传出去。识别结果已经可用，大模型哪一步失败都不影响会议完成。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy import update

from ..llm import LlmClient, LlmError
from ..models import Meeting
from . import minutes, polish, speakers
from .llm_config import resolve_meeting_llm
from .llmcall import is_fatal

if TYPE_CHECKING:
    from .manager import MeetingManager

log = logging.getLogger("bdw.meetings.processing")

OPS = ("speakers", "polish", "minutes")
# 进度区间（0–1，由 manager.set_progress 映射到整体进度的 60–100）
SPEAKERS_SPAN = (0.0, 0.1)
POLISH_SPAN = (0.1, 0.7)
MINUTES_SPAN = (0.7, 1.0)


def _client(manager: MeetingManager, meeting_id: str, step: str) -> tuple[LlmClient | None, str | None]:
    """按会议的整理方案取这个用途的客户端；返回 (客户端, None) 或 (None, 原因)。会议已删除时返回 (None, None)。"""
    with manager.Session() as db:
        m = db.get(Meeting, meeting_id)
        if m is None or m.deleted_at is not None:
            return None, None
        cfg, reason = resolve_meeting_llm(db, manager.secrets, m.llm_preset_id, step)
    if cfg is None:
        return None, reason
    assert manager.http is not None
    return LlmClient(cfg, manager.http), None


def _reporter(
    manager: MeetingManager, meeting_id: str, span: tuple[float, float], stage: str
) -> Callable[[float], None]:
    def report(ratio: float) -> None:
        manager.set_progress(meeting_id, span[0] + (span[1] - span[0]) * max(0.0, min(1.0, ratio)), stage)

    return report


async def run_pipeline_steps(manager: MeetingManager, meeting_id: str) -> dict[str, str | None]:
    """识别刚完成时跑一遍：说话人识别 → 整理 → 纪要。

    按步骤返回警告（键为 speakers / polish / minutes / pipeline），manager._process 按键写进 Meeting.warnings。

    每个步骤按整理方案用各自的模型；某个模型报“致命”错误（密钥无效、模型不存在、连不上）后，
    只跳过后面用同一个模型的步骤，用别的模型的步骤照常进行。
    """
    warnings: dict[str, str | None] = {}
    steps = (
        ("speakers", SPEAKERS_SPAN, "识别说话人"),
        ("polish", POLISH_SPAN, "整理逐字稿"),
        ("minutes", MINUTES_SPAN, "生成纪要"),
    )
    dead: set[int] = set()
    skipped: list[str] = []
    unavailable: dict[str, str] = {}
    for step, span, label in steps:
        client, reason = _client(manager, meeting_id, step)
        if client is None:
            if reason is None:  # 会议已删除
                return warnings
            unavailable[step] = reason
            warnings[step] = f"{label}没有进行：{reason}"
            continue
        if client.cfg.profile_id in dead:
            skipped.append(label)
            continue
        manager.set_progress(meeting_id, span[0], step)
        warning, fatal = await _step(manager, meeting_id, client, step, span, label, {})
        warnings[step] = warning
        if fatal:
            dead.add(client.cfg.profile_id)
    if skipped:
        warnings["pipeline"] = f"大模型暂时不可用，跳过了{'、'.join(skipped)}，稍后可以在会议页面重新操作"
    if len(unavailable) == len(steps) and len(set(unavailable.values())) == 1:
        # 原因都一样（多半是还没有配置方案）：合成一条，别重复三遍
        warnings = {"pipeline": f"逐字稿未整理：{next(iter(unavailable.values()))}"}
    _settle_minutes(manager, meeting_id)
    return warnings


async def run_op(manager: MeetingManager, meeting_id: str, op: str, params: dict[str, Any]) -> str | None:
    """会议完成后用户触发的单项重跑。op 取值见 OPS。

    接口在占住 op 时已经把 transcript_state 改成 polishing、或把 minutes_state 改成 generating，
    原来的状态放在 params["previous"] 里，没跑成时据此恢复。
    """
    previous = params.get("previous")
    try:
        if op not in OPS:
            return f"未知的操作：{op}"
        client, reason = _client(manager, meeting_id, op)
        if client is None:
            return f"无法处理：{reason}" if reason else None
        label = {"speakers": "识别说话人", "polish": "整理逐字稿", "minutes": "生成纪要"}[op]
        manager.set_progress(meeting_id, 0.0, op)
        warning, _ = await _step(manager, meeting_id, client, op, (0.0, 1.0), label, params)
        return warning
    finally:
        # 被取消或没跑成时，把接口预先设好的“进行中”状态改回来；整体进度也恢复成完成时的样子
        _restore(manager, meeting_id, op, previous)


async def _step(
    manager: MeetingManager,
    meeting_id: str,
    client: LlmClient,
    step: str,
    span: tuple[float, float],
    label: str,
    params: dict[str, Any],
) -> tuple[str | None, bool]:
    """跑一步，返回 (警告, 是否不用再试后面的步骤)。"""
    report = _reporter(manager, meeting_id, span, step)
    try:
        if step == "speakers":
            await speakers.guess_speakers(manager, meeting_id, client)
            report(1.0)
            return None, False
        if step == "polish":
            result = await polish.polish_meeting(
                manager, meeting_id, client, previous=params.get("previous"), on_progress=report
            )
            return result.warning, result.fatal is not None
        if step == "minutes":
            template = params.get("template")
            extra = params.get("extra_instructions")
            warning = await minutes.generate_minutes(
                manager, meeting_id, client, template=template, extra=extra, on_progress=report
            )
            return warning, False
        return f"未知的步骤：{step}", False
    except LlmError as e:
        log.warning("meeting %s: %s failed: %s", meeting_id, step, e)
        return f"{label}失败：{e}", is_fatal(e)
    except Exception as e:
        # 识别结果已经可用：程序错误也只记成警告，不让整场会议失败
        log.exception("meeting %s: %s crashed", meeting_id, step)
        return f"{label}时出错：{e.__class__.__name__}: {e}"[:300], False


def _settle_minutes(manager: MeetingManager, meeting_id: str) -> None:
    """纪要步骤出错退出、却还标着“生成中”时改成失败，免得界面一直转圈。"""
    with manager.Session() as db:
        db.execute(
            update(Meeting)
            .where(Meeting.id == meeting_id, Meeting.minutes_state == "generating")
            .values(minutes_state="failed")
            .execution_options(synchronize_session=False)
        )
        db.commit()


def _restore(manager: MeetingManager, meeting_id: str, op: str, previous: Any) -> None:
    with manager.Session() as db:
        m = db.get(Meeting, meeting_id)
        if m is None:
            return
        if op == "polish" and m.transcript_state == "polishing":
            m.transcript_state = previous if previous in ("raw", "polished", "partial") else "raw"
        if op == "minutes" and m.minutes_state == "generating":
            # 没开始生成（如没有模型）或中途出错时旧纪要还在，恢复原状态；原来就没有纪要的记成失败
            m.minutes_state = previous if previous in ("ready", "failed") else "failed"
        if m.status == "done":
            m.stage = ""
            m.progress = 100
        db.commit()
