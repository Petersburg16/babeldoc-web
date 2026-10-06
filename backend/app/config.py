from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"BDW_{name}", default)


def _flag(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def default_engine_python() -> Path:
    venv = PROJECT_DIR / "engine" / ".venv"
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


@dataclass(frozen=True)
class Config:
    data_dir: Path
    engine: str
    engine_python: Path
    engine_dir: Path
    frontend_dir: Path
    pdf_assets_dir: Path
    cookie_secure: bool
    session_days: int
    dev: bool
    mock_seconds: float = 12.0
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def meetings_dir(self) -> Path:
        return self.data_dir / "meetings"


def load_config() -> Config:
    engine = (_env("ENGINE", "mock") or "mock").strip().lower()
    if engine not in {"mock", "babeldoc"}:
        raise ValueError(f"BDW_ENGINE 只能是 mock 或 babeldoc，当前为 {engine!r}")
    data_dir = Path(_env("DATA_DIR") or BACKEND_DIR / "data").resolve()
    return Config(
        data_dir=data_dir,
        engine=engine,
        engine_python=Path(_env("ENGINE_PYTHON") or default_engine_python()),
        engine_dir=Path(_env("ENGINE_DIR") or PROJECT_DIR / "engine").resolve(),
        frontend_dir=Path(_env("FRONTEND_DIR") or PROJECT_DIR / "frontend" / "dist").resolve(),
        pdf_assets_dir=Path(_env("PDF_ASSETS_DIR") or data_dir / "pdf-assets").resolve(),
        cookie_secure=_flag("COOKIE_SECURE", False),
        session_days=int(_env("SESSION_DAYS", "30") or 30),
        dev=_flag("DEV", False),
        mock_seconds=float(_env("MOCK_SECONDS", "12") or 12),
        ffmpeg=_env("FFMPEG") or shutil.which("ffmpeg") or "ffmpeg",
        ffprobe=_env("FFPROBE") or shutil.which("ffprobe") or "ffprobe",
    )
