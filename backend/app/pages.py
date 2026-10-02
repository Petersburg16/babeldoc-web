"""页码范围与 babeldoc 的 --pages 语义一致：1 起计，逗号分隔，"3-" 到末页，"-5" 从首页。"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"^(\d*)-(\d*)$|^(\d+)$")


def normalize_pages(spec: str | None) -> str | None:
    if spec is None:
        return None
    cleaned = re.sub(r"\s+", "", spec).replace("，", ",").replace("–", "-").replace("—", "-")
    return cleaned or None


def parse_pages(spec: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for token in spec.split(","):
        m = _TOKEN.match(token)
        if not m or token == "-":
            raise ValueError(f"无法识别的页码：{token or '（空）'}")
        if m.group(3):
            page = int(m.group(3))
            start, end = page, page
        else:
            start = int(m.group(1)) if m.group(1) else 1
            end = int(m.group(2)) if m.group(2) else -1
        if start < 1 or (end != -1 and end < start):
            raise ValueError(f"页码范围无效：{token}")
        ranges.append((start, end))
    return ranges


def count_pages(spec: str | None, total: int) -> int:
    if not spec:
        return total
    selected = set()
    for start, end in parse_pages(spec):
        last = total if end == -1 else min(end, total)
        selected.update(range(start, last + 1))
    return len(selected)
