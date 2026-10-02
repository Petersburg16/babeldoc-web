from __future__ import annotations

import io
import json
import time
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
    return client
