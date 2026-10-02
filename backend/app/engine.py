"""以子进程运行 engine/runner.py（或开发用的 mock_runner.py），按 JSON lines 协议读取事件。

用 Popen + 线程读管道而不是 asyncio 子进程：Windows 下 uvicorn 用 SelectorEventLoop，不支持 asyncio 子进程。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from .config import Config
from .models import Job, ModelProfile

log = logging.getLogger("bdw.engine")

AUTO_SPLIT_THRESHOLD = 80
AUTO_SPLIT_PAGES = 50


def build_spec(
    *,
    job: Job,
    profile: ModelProfile,
    job_dir: Path,
    watermark_mode: str,
    skip_translation: bool = False,
    mock: dict[str, Any] | None = None,
) -> dict[str, Any]:
    opts = job.options or {}
    glossary = job_dir / "glossary.csv"
    options = {
        "pages": job.pages,
        "no_dual": opts.get("output") == "mono",
        "no_mono": opts.get("output") == "dual",
        "use_alternating_pages_dual": opts.get("dual_mode") == "alternating",
        "dual_translate_first": bool(opts.get("dual_translate_first")),
        "translate_table_text": bool(opts.get("translate_table_text")),
        "enhance_compatibility": bool(opts.get("enhance_compatibility")),
        "ocr_workaround": bool(opts.get("ocr_workaround")),
        "auto_enable_ocr_workaround": bool(opts.get("auto_enable_ocr_workaround", True)),
        "skip_scanned_detection": bool(opts.get("skip_scanned_detection")),
        "primary_font_family": opts.get("primary_font_family"),
        "only_include_translated_page": bool(opts.get("only_include_translated_page")),
        "auto_extract_glossary": bool(opts.get("auto_extract_glossary", True)),
        "custom_system_prompt": opts.get("custom_system_prompt") or None,
        "glossary_files": [str(glossary)] if glossary.exists() else [],
        "max_pages_per_part": AUTO_SPLIT_PAGES if job.billed_pages > AUTO_SPLIT_THRESHOLD else None,
        "watermark_mode": watermark_mode,
    }
    spec: dict[str, Any] = {
        "job_id": job.id,
        "input": str(job_dir / "input.pdf"),
        "output_dir": str(job_dir / "out"),
        "working_dir": str(job_dir / "work"),
        "lang_in": job.lang_in,
        "lang_out": job.lang_out,
        "skip_translation": skip_translation,
        "model": {
            "base_url": profile.base_url,
            "model": profile.model,
            "term_model": profile.term_model,
            "qps": profile.qps,
            "pool_max_workers": profile.pool_max_workers,
            "send_temperature": profile.send_temperature,
            "json_mode": profile.json_mode,
            "thinking": profile.thinking,
            "reasoning": profile.reasoning,
        },
        "options": options,
    }
    if mock is not None:
        spec["mock"] = mock
    return spec


def engine_command(config: Config) -> list[str]:
    if config.engine == "mock":
        return [sys.executable, str(config.engine_dir / "mock_runner.py")]
    return [str(config.engine_python), str(config.engine_dir / "runner.py")]


def child_env(api_key: str = "") -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("BDW_")}
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if api_key:
        env["BDW_API_KEY"] = api_key
    return env


@dataclass
class EngineProcess:
    proc: subprocess.Popen
    log_handle: IO[bytes]

    def kill(self) -> None:
        if self.proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            with contextlib.suppress(OSError):
                self.proc.kill()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            if self.proc.stdout:
                self.proc.stdout.close()
        with contextlib.suppress(Exception):
            self.log_handle.close()


def spawn(config: Config, spec_path: Path, log_path: Path, env: dict[str, str]) -> EngineProcess:
    log_handle = open(log_path, "ab")  # noqa: SIM115  生命周期由 EngineProcess.close 管理
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            [*engine_command(config), str(spec_path)],
            stdout=subprocess.PIPE,
            stderr=log_handle,
            stdin=subprocess.DEVNULL,
            env=env,
            cwd=str(spec_path.parent),
            **kwargs,
        )
    except Exception:
        log_handle.close()
        raise
    return EngineProcess(proc=proc, log_handle=log_handle)


async def read_events(ep: EngineProcess) -> AsyncIterator[dict[str, Any]]:
    stream = ep.proc.stdout
    assert stream is not None
    while True:
        line = await asyncio.to_thread(stream.readline)
        if not line:
            return
        try:
            event = json.loads(line)
        except ValueError:
            with contextlib.suppress(Exception):
                ep.log_handle.write(b"[stdout] " + line)
            continue
        if isinstance(event, dict) and "event" in event:
            yield event


def detect_engine_version(config: Config) -> str:
    if config.engine == "mock":
        return "mock"
    try:
        out = subprocess.run(
            [str(config.engine_python), "-c", "import importlib.metadata as m; print(m.version('babeldoc'))"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("cannot detect babeldoc version: %s", e)
        return "unavailable"
    return out.stdout.strip() or "unavailable"
