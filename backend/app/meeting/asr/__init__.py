"""语音识别服务注册表。新增一家服务：写一个 AsrAdapter 子类，加进 ADAPTERS。"""

from __future__ import annotations

from .base import AsrAdapter
from .funasr import FunAsrAdapter
from .mock import MockAdapter
from .tencent import TencentMeetingAdapter
from .tingwu import TingwuAdapter

ADAPTERS: dict[str, type[AsrAdapter]] = {
    cls.kind: cls for cls in (FunAsrAdapter, TingwuAdapter, TencentMeetingAdapter, MockAdapter)
}


def adapter_class(kind: str) -> type[AsrAdapter] | None:
    return ADAPTERS.get(kind)


def available_kinds(dev: bool) -> list[type[AsrAdapter]]:
    """模拟服务只在模拟引擎（开发、测试）下提供。"""
    return [cls for cls in ADAPTERS.values() if dev or not cls.dev_only]
