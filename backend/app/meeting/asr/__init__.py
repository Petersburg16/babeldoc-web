"""语音识别服务注册表。新增一家服务：写一个 AsrAdapter 子类，加进 ADAPTERS。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .base import AsrAdapter
from .funasr import FunAsrAdapter
from .mock import MockAdapter
from .tencent import TencentMeetingAdapter
from .tingwu import TingwuAdapter

if TYPE_CHECKING:
    from ...models import AsrProvider
    from ...security import SecretBox

ADAPTERS: dict[str, type[AsrAdapter]] = {
    cls.kind: cls for cls in (FunAsrAdapter, TingwuAdapter, TencentMeetingAdapter, MockAdapter)
}


def adapter_class(kind: str) -> type[AsrAdapter] | None:
    return ADAPTERS.get(kind)


def available_kinds(dev: bool) -> list[type[AsrAdapter]]:
    """模拟服务只在模拟引擎（开发、测试）下提供。"""
    return [cls for cls in ADAPTERS.values() if dev or not cls.dev_only]


def load_secrets(provider: AsrProvider, box: SecretBox) -> dict[str, str]:
    """解开识别服务的机密字段（整体加密成一段 JSON 存在 secret_enc）；解不开或格式不对时当作没填。"""
    raw = box.decrypt(provider.secret_enc)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
