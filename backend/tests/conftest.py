from __future__ import annotations

import io
import json
import math
import struct
import time
import wave
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.config import Config, load_config
from app.main import create_app
from app.models import User
from app.security import hash_password

ADMIN = ("admin", "admin-password")


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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in statuses:
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} stuck in {job['status']}")


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


def make_wav(seconds: float = 20.0, rate: int = 16000) -> bytes:
    """一段 440 Hz 正弦波，ffmpeg 能正常转码。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = int(seconds * rate)
        samples = (int(8000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(frames))
        w.writeframes(b"".join(struct.pack("<h", v) for v in samples))
    return buf.getvalue()


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


def wait_meeting(client: TestClient, meeting_id: str, statuses: set[str], timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    meeting: dict = {}
    while time.monotonic() < deadline:
        meeting = client.get(f"/api/meetings/{meeting_id}").json()
        if meeting.get("status") in statuses:
            return meeting
        time.sleep(0.1)
    raise AssertionError(f"meeting {meeting_id} stuck in {meeting.get('status')}: {meeting.get('error')}")
