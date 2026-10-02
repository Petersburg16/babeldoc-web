"""开发用假引擎：只依赖标准库，按与 runner.py 相同的协议模拟进度并复制原文作为“译文”。

spec 里可放 "mock": {"seconds": 12, "fail_at": "Translate Paragraphs", "warning": "..."} 调整行为。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

STAGES = [
    ("Parse PDF and Create Intermediate Representation", 14.12),
    ("DetectScannedFile", 2.45),
    ("Parse Page Layout", 14.03),
    ("Parse Paragraphs", 6.26),
    ("Parse Formulas and Styles", 1.66),
    ("Automatic Term Extraction", 30.0),
    ("Translate Paragraphs", 46.96),
    ("Typesetting", 4.71),
    ("Add Fonts", 0.61),
    ("Generate drawing instructions", 1.96),
    ("Subset font", 0.92),
    ("Save PDF", 6.34),
]


def emit(event: str, **payload) -> None:
    print(json.dumps({"event": event, **payload}, ensure_ascii=False), flush=True)


def main() -> int:
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    mock = spec.get("mock") or {}
    seconds = float(mock.get("seconds", 12))
    opts = spec.get("options", {})
    emit("started", engine="mock", version="0", pid=os.getpid(), protocol=1)

    stages = [s for s in STAGES if opts.get("auto_extract_glossary", True) or s[0] != "Automatic Term Extraction"]
    total_weight = sum(w for _, w in stages)
    done = 0.0
    for name, weight in stages:
        if mock.get("fail_at") == name:
            print(f"mock failure injected at {name}", file=sys.stderr)
            emit("failed", kind="translate", message=f"模拟失败：{name}")
            return 1
        steps = 5
        for i in range(1, steps + 1):
            time.sleep(seconds * weight / total_weight / steps)
            overall = (done + weight * i / steps) / total_weight * 100
            emit("progress", overall=round(overall, 2), stage=name, current=i, total=steps, part=1, parts=1)
        done += weight
        print(f"stage done: {name}", file=sys.stderr)

    src = Path(spec["input"])
    out = Path(spec["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    stem, lang = src.stem, spec["lang_out"]
    files = {"mono": None, "dual": None, "glossary": None}
    if not opts.get("no_mono"):
        files["mono"] = str(shutil.copyfile(src, out / f"{stem}.no_watermark.{lang}.mono.pdf"))
    if not opts.get("no_dual"):
        files["dual"] = str(shutil.copyfile(src, out / f"{stem}.no_watermark.{lang}.dual.pdf"))
    if opts.get("auto_extract_glossary", True):
        glossary = out / f"{stem}.{lang}.glossary.csv"
        glossary.write_text("source,target,tgt_lng\nTransformer,Transformer,zh-CN\nattention,注意力,zh-CN\n", "utf-8")
        files["glossary"] = str(glossary)
    stats = {
        "total_tokens": 4321,
        "seconds": seconds,
        "peak_memory_mb": 0,
        "valid_chars": 12345,
        "tokens": {"total": 4321, "prompt": 3000, "completion": 1321, "cache_hit_prompt": 0},
        "term_tokens": {"total_tokens": 0},
        "calls": {"total": 40, "cached": 0, "api_ok": 40, "api_errors": 0},
    }
    emit("finished", files=files, stats=stats, warning=mock.get("warning"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
