"""识别完成后的大模型处理：猜说话人名字、分块整理逐字稿、生成纪要，以及完成后的单项重跑。

占位实现：只把逐字稿标成“未整理”。真正的实现见 M4（说话人识别、整理、纪要）。
约定：这里的函数自己处理大模型错误，返回给用户看的警告文字（没有就 None），不向外抛异常；
只有任务被取消时让 CancelledError 传出去。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .manager import MeetingManager

OPS = ("speakers", "polish", "minutes")


async def run_pipeline(manager: MeetingManager, meeting_id: str) -> str | None:
    """识别刚完成时跑一遍：说话人识别 → 整理 → 纪要。"""
    return None


async def run_op(manager: MeetingManager, meeting_id: str, op: str, params: dict[str, Any]) -> str | None:
    """会议完成后用户触发的单项重跑。op 取值见 OPS。"""
    return None
