from __future__ import annotations

import io
import json
import math
import os
import shutil
import sys
import time
import wave
from array import array
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import select

from app.config import Config, load_config
from app.main import create_app
from app.models import Meeting, MeetingSegment, User
from app.security import hash_password, new_job_id

ADMIN = ("admin", "admin-password")
# gpt-6-astra 的内置思考档位表，也是中转报错里列出的可用档位
ASTRA = ["low", "medium", "high", "xhigh", "max"]
ASR_FIXTURES = Path(__file__).parent / "fixtures" / "asr"


def _found(tool: str) -> bool:
    """和 config.py 一样优先用 BDW_FFMPEG / BDW_FFPROBE 指定的程序。"""
    return shutil.which(os.environ.get(f"BDW_{tool.upper()}") or tool) is not None


needs_ffmpeg = pytest.mark.skipif(not (_found("ffmpeg") and _found("ffprobe")), reason="需要 ffmpeg 和 ffprobe")


def asr_fixture(name: str) -> Any:
    """识别服务的接口样例（fixtures/asr/*.json）。"""
    return json.loads((ASR_FIXTURES / name).read_text("utf-8"))


def wait_until[T](
    fetch: Callable[[], T],
    accept: Callable[[T], object] = bool,
    *,
    timeout: float = 20,
    interval: float = 0.05,
    message: str | Callable[[T], str] = "等待超时",
) -> T:
    """每隔 interval 秒取一次 fetch()，accept 通过就返回取到的值；超时抛 AssertionError。

    至少取一次；message 可以是函数，拿最后一次取到的值拼报错。"""
    deadline = time.monotonic() + timeout
    while True:
        value = fetch()
        if accept(value):
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(message(value) if callable(message) else message)
        time.sleep(interval)


def make_pdf(pages: int = 3) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def build_config(tmp_path, monkeypatch, mock_seconds: float = 0.4) -> Config:
    monkeypatch.setenv("BDW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BDW_ENGINE", "mock")
    monkeypatch.setenv("BDW_MOCK_SECONDS", str(mock_seconds))
    monkeypatch.setenv("BDW_FRONTEND_DIR", str(tmp_path / "no-frontend"))
    return load_config()


def add_user(app, username: str, password: str, role: str = "user", **extra) -> int:
    ctx = app.state.ctx
    with ctx.Session() as db:
        user = User(username=username, display_name=username, password_hash=hash_password(password), role=role, **extra)
        db.add(user)
        db.commit()
        return user.id


def user_id_of(app, username: str = ADMIN[0]) -> int:
    with app.state.ctx.Session() as db:
        return db.scalar(select(User.id).where(User.username == username))


def login(client: TestClient, username: str, password: str) -> None:
    client.cookies.clear()
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


def upload(client: TestClient, name: str = "paper.pdf", page_count: int = 3, **options):
    return client.post(
        "/api/jobs",
        files=[("files", (name, make_pdf(page_count), "application/pdf"))],
        data={"options": json.dumps(options)},
    )


def wait_status(client: TestClient, job_id: str, statuses: set[str], timeout: float = 20) -> dict:
    return wait_until(
        lambda: client.get(f"/api/jobs/{job_id}").json(),
        lambda job: job["status"] in statuses,
        timeout=timeout,
        interval=0.1,
        message=lambda job: f"job {job_id} stuck in {job['status']}",
    )


def update_settings(client: TestClient, **changes: Any):
    """先取系统设置、改几项再整份存回去；返回响应，由调用方判断成败（有的测试就是要它失败）。"""
    settings = client.get("/api/admin/settings").json()
    return client.put("/api/admin/settings", json={**settings, **changes})


@pytest.fixture
def config(tmp_path, monkeypatch) -> Config:
    return build_config(tmp_path, monkeypatch)


@pytest.fixture
def app(config):
    return create_app(config)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app, headers={"X-Requested-With": "pytest"}) as c:
        yield c


@pytest.fixture
def admin_client(app, client) -> TestClient:
    add_user(app, *ADMIN, role="admin")
    login(client, *ADMIN)
    resp = client.post(
        "/api/admin/models",
        json={
            "name": "测试模型",
            "base_url": "https://example.invalid/v1",
            "api_key": "sk-test-1234567890",
            "model": "m",
        },
    )
    assert resp.status_code == 201, resp.text
    # 会议记录用自己的大模型配置：一个会议模型 + 四个用途都用它的默认方案
    resp = client.post(
        "/api/admin/meeting-llm/models",
        json={
            "name": "会议测试模型",
            "base_url": "https://example.invalid/v1",
            "api_key": "sk-meeting-1234567890",
            "model": "m",
            "create_preset": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return client


MOCK_PROVIDER = {"kind": "mock", "name": "模拟识别", "config": {"delay_seconds": "0.3"}, "secrets": {"api_key": "k"}}


def wav_bytes(pattern: list[tuple[float, bool]], rate: int = 16000) -> bytes:
    """16 位单声道 WAV。pattern: [(秒数, 是否有声)]，有声是 440 Hz 正弦波，无声是全零。"""
    samples = array("h")
    for seconds, sound in pattern:
        n = int(seconds * rate)
        if sound:
            samples.extend(int(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(n))
        else:
            samples.extend([0] * n)
    if sys.byteorder == "big":  # WAV 是小端
        samples.byteswap()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def make_wav(seconds: float = 20.0, rate: int = 16000) -> bytes:
    """一段 440 Hz 正弦波，ffmpeg 能正常转码。"""
    return wav_bytes([(seconds, True)], rate)


def add_mock_provider(client: TestClient, **config) -> int:
    body = {**MOCK_PROVIDER, "config": {**MOCK_PROVIDER["config"], **config}}
    resp = client.post("/api/admin/asr/providers", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def upload_audio(client: TestClient, data: bytes, filename: str = "组会.wav", **options) -> dict:
    resp = client.post("/api/meetings", json={"filename": filename, "size": len(data), **options})
    assert resp.status_code == 201, resp.text
    created = resp.json()
    part_size = created["part_size"]
    meeting_id = created["meeting"]["id"]
    for i in range(created["parts"]):
        chunk = data[i * part_size : (i + 1) * part_size]
        put = client.put(f"/api/meetings/{meeting_id}/upload/{i}", content=chunk)
        assert put.status_code == 200, put.text
    done = client.post(f"/api/meetings/{meeting_id}/upload/complete")
    assert done.status_code == 200, done.text
    return done.json()


def seed_meeting(app, user_id: int, segments, **fields: Any) -> str:
    """直接在库里造一场识别完的会议，返回会议号。

    segments：[(开始毫秒, 结束毫秒, 说话人, 识别原文[, 整理后的文字[, 用户改过]])]，整理后的文字省略或为 None 时同原文；
    fields：Meeting 的其他字段，覆盖下面的默认值。说话人表默认按编号排好、都还没起名。"""
    meeting_id = new_job_id()
    values: dict[str, Any] = {"title": "组会", "filename": "a.wav", "status": "done", **fields}
    if values.get("speakers") is None:
        ids = sorted({seg[2] for seg in segments}, key=lambda s: int(s[1:]))
        values["speakers"] = {s: {"name": "", "guess": None, "merged_into": None} for s in ids}
    with app.state.ctx.Session() as db:
        db.add(Meeting(id=meeting_id, user_id=user_id, **values))
        db.flush()
        for i, (start, end, speaker, raw, *rest) in enumerate(segments):
            text = rest[0] if rest and rest[0] is not None else raw
            edited = bool(rest[1]) if len(rest) > 1 else False
            db.add(
                MeetingSegment(
                    meeting_id=meeting_id,
                    idx=i,
                    start_ms=start,
                    end_ms=end,
                    asr_speaker=speaker,
                    speaker=speaker,
                    raw_text=raw,
                    text=text,
                    edited=edited,
                )
            )
        db.commit()
    return meeting_id


def wait_meeting(client: TestClient, meeting_id: str, statuses: set[str], timeout: float = 30) -> dict:
    return wait_until(
        lambda: client.get(f"/api/meetings/{meeting_id}").json(),
        lambda meeting: meeting.get("status") in statuses,
        timeout=timeout,
        interval=0.1,
        message=lambda meeting: f"meeting {meeting_id} stuck in {meeting.get('status')}: {meeting.get('error')}",
    )
