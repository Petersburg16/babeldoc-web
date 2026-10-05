"""ffmpeg / ffprobe 调用。和翻译引擎一样用 Popen + 线程读管道（Windows 下 uvicorn 的事件循环不支持 asyncio
子进程），独立进程组，取消时整组杀掉。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import signal
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

# 识别和回听共用一份：16 kHz 单声道 mp3，32 kbps 足够语音，5 小时约 72 MB
AUDIO_ARGS = ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k"]
AUDIO_NAME = "audio.mp3"


class MediaError(Exception):
    pass


@dataclass
class ProbeInfo:
    duration_ms: int
    has_audio: bool
    format_name: str


def _popen_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def probe(ffprobe: str, path: Path, timeout: float = 120) -> ProbeInfo:
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as e:
        raise MediaError("服务器上没有找到 ffprobe，请联系管理员安装 ffmpeg") from e
    except subprocess.TimeoutExpired as e:
        raise MediaError("读取录音信息超时") from e
    if out.returncode != 0:
        raise MediaError("无法读取这个文件，可能不是音视频文件或已损坏")
    try:
        info = json.loads(out.stdout.decode("utf-8", "replace"))
    except ValueError as e:
        raise MediaError("无法读取这个文件的音视频信息") from e
    streams = info.get("streams") or []
    fmt = info.get("format") or {}
    duration = _to_float(fmt.get("duration"))
    if duration is None:
        durations = [_to_float(s.get("duration")) for s in streams if s.get("codec_type") == "audio"]
        duration = max((d for d in durations if d), default=None)
    return ProbeInfo(
        duration_ms=int((duration or 0) * 1000),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        format_name=str(fmt.get("format_name") or ""),
    )


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class FfmpegProcess:
    """一次 ffmpeg 运行。stdout 用 -progress pipe:1 输出进度，stderr 追加到日志文件。"""

    def __init__(self, args: list[str], log_path: Path):
        self.log_handle: IO[bytes] = open(log_path, "ab")  # noqa: SIM115  由 close() 关闭
        try:
            self.proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=self.log_handle,
                stdin=subprocess.DEVNULL,
                **_popen_kwargs(),
            )
        except FileNotFoundError as e:
            self.log_handle.close()
            raise MediaError("服务器上没有找到 ffmpeg，请联系管理员安装") from e
        except Exception:
            self.log_handle.close()
            raise

    def kill(self) -> None:
        if self.proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True, check=False)
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

    async def wait(self, total_ms: int = 0, on_progress: Callable[[float], None] | None = None) -> int:
        """读完进度输出并等进程结束，返回退出码。on_progress 收到 0–1。"""
        stream = self.proc.stdout
        assert stream is not None
        while True:
            line = await asyncio.to_thread(stream.readline)
            if not line:
                break
            key, _, value = line.decode("ascii", "ignore").strip().partition("=")
            # out_time_us 是微秒；老版本的 out_time_ms 其实也是微秒
            if key in ("out_time_us", "out_time_ms") and total_ms > 0 and on_progress:
                micros = _to_float(value)
                if micros is not None and micros >= 0:
                    on_progress(min(1.0, micros / 1000 / total_ms))
        return await asyncio.to_thread(self.proc.wait)


def transcode_args(ffmpeg: str, src: Path, dst: Path) -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(src),
        *AUDIO_ARGS,
        "-progress",
        "pipe:1",
        "-nostats",
        str(dst),
    ]


def cut_args(ffmpeg: str, src: Path, dst: Path, start_ms: int, duration_ms: int) -> list[str]:
    """从已转好的 mp3 里截一段，直接复制音频帧（不重新编码），用于超长录音切段。"""
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-ss", f"{start_ms / 1000:.3f}", "-i", str(src), "-t", f"{duration_ms / 1000:.3f}",
        "-c", "copy", "-progress", "pipe:1", "-nostats", str(dst),
    ]  # fmt: skip


def tone(ffmpeg: str, dst: Path, seconds: int = 3) -> None:
    """生成一段测试音频（“测试连接”用来验证服务商能否拉到本站的文件）。"""
    try:
        out = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={seconds}",
                *AUDIO_ARGS,
                str(dst),
            ],  # fmt: skip
            capture_output=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError as e:
        raise MediaError("服务器上没有找到 ffmpeg") from e
    if out.returncode != 0:
        raise MediaError("生成测试音频失败：" + out.stderr.decode("utf-8", "replace")[-300:])


SILENCE = re.compile(rb"silence_(start|end): (-?[\d.]+)")


def detect_silences(ffmpeg: str, src: Path, noise_db: int = -35, min_seconds: float = 0.4) -> list[tuple[int, int]]:
    """返回静音区间 [(start_ms, end_ms)]，给切段选切点用。整段解码一遍，5 小时约一两分钟。"""
    try:
        out = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-i",
                str(src),
                "-af",
                f"silencedetect=noise={noise_db}dB:d={min_seconds}",
                "-f",
                "null",
                "-",
            ],  # fmt: skip
            capture_output=True,
            timeout=1800,
            check=False,
        )
    except FileNotFoundError as e:
        raise MediaError("服务器上没有找到 ffmpeg") from e
    spans: list[tuple[int, int]] = []
    start: float | None = None
    for kind, value in SILENCE.findall(out.stderr):
        t = float(value)
        if kind == b"start":
            start = max(0.0, t)
        elif start is not None:
            spans.append((int(start * 1000), int(t * 1000)))
            start = None
    return spans
