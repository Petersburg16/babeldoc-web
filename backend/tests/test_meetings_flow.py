from __future__ import annotations

import shutil
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import utcnow
from app.main import create_app
from app.models import Meeting, MeetingSegment
from tests.conftest import (
    ADMIN,
    add_mock_provider,
    add_user,
    build_config,
    login,
    make_wav,
    upload_audio,
    wait_meeting,
)

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")


def test_meeting_runs_to_done(app, admin_client):
    add_mock_provider(admin_client)
    meeting = upload_audio(admin_client, make_wav(30), title="周三组会", expected_speakers=3)
    assert meeting["status"] in {"queued", "transcoding", "transcribing"}
    done = wait_meeting(admin_client, meeting["id"], {"done", "failed"})
    assert done["status"] == "done", done["error"]
    assert done["title"] == "周三组会"
    assert done["transcript_state"] == "raw"
    assert 29_000 <= done["duration_ms"] <= 31_000
    assert done["asr_seconds"] >= 29
    assert done["audio_available"] and done["audio_expires_at"]
    assert [p["state"] for p in done["parts"]] == ["done"]
    assert set(done["speakers"]) == {"S1", "S2", "S3"}

    segments = admin_client.get(f"/api/meetings/{meeting['id']}/segments").json()
    assert segments and segments[0]["speaker"] == "S1"
    assert segments[0]["text"] == segments[0]["raw_text"]
    assert [s["idx"] for s in segments] == list(range(len(segments)))

    audio = admin_client.get(f"/api/meetings/{meeting['id']}/audio", headers={"Range": "bytes=0-99"})
    assert audio.status_code == 206 and len(audio.content) == 100
    assert audio.headers["content-type"] == "audio/mpeg"

    directory = app.state.ctx.meetings.meeting_dir(meeting["id"])
    assert (directory / "audio.mp3").is_file()
    assert not list(directory.glob("source.*")), "识别成功后原件应删除"

    page = admin_client.get("/api/meetings").json()
    assert page["total"] == 1 and page["items"][0]["id"] == meeting["id"]


def test_chunked_upload_edge_cases(app, admin_client, client, monkeypatch):
    monkeypatch.setattr("app.routers.meetings.PART_SIZE", 64 * 1024)
    add_mock_provider(admin_client)
    data = make_wav(6)  # 约 192 KB → 3 片
    created = admin_client.post("/api/meetings", json={"filename": "a.wav", "size": len(data)}).json()
    mid, size, parts = created["meeting"]["id"], created["part_size"], created["parts"]
    assert parts == 3 and created["meeting"]["status"] == "uploading"

    # 分片大小不对
    assert admin_client.put(f"/api/meetings/{mid}/upload/0", content=data[:100]).status_code == 400
    # 序号越界
    assert admin_client.put(f"/api/meetings/{mid}/upload/9", content=data[:size]).status_code == 400
    # 缺 X-Requested-With
    bare = TestClient(app)
    bare.cookies = admin_client.cookies
    assert bare.put(f"/api/meetings/{mid}/upload/0", content=data[:size]).status_code == 403
    # 乱序上传、重复上传同一片
    for i in (2, 0, 0):
        chunk = data[i * size : (i + 1) * size]
        assert admin_client.put(f"/api/meetings/{mid}/upload/{i}", content=chunk).status_code == 200
    resp = admin_client.post(f"/api/meetings/{mid}/upload/complete")
    assert resp.status_code == 400 and "1 个分片" in resp.json()["detail"]
    assert admin_client.put(f"/api/meetings/{mid}/upload/1", content=data[size : 2 * size]).status_code == 200
    assert admin_client.post(f"/api/meetings/{mid}/upload/complete").status_code == 200
    assert wait_meeting(admin_client, mid, {"done", "failed"})["status"] == "done"

    # 别人的会议看不到
    add_user(app, "bob", "bob-password")
    login(client, "bob", "bob-password")
    assert client.get(f"/api/meetings/{mid}").status_code == 404
    assert client.get(f"/api/meetings/{mid}/audio").status_code == 404
    assert client.get("/api/meetings").json()["total"] == 0


def test_create_validates_input(admin_client):
    assert admin_client.post("/api/meetings", json={"filename": "a.wav", "size": 10}).status_code == 400  # 没配服务
    add_mock_provider(admin_client)
    assert admin_client.post("/api/meetings", json={"filename": "a.pdf", "size": 10}).status_code == 400
    huge = admin_client.post("/api/meetings", json={"filename": "a.wav", "size": 5 * 1024**3})
    assert huge.status_code == 413
    bad_template = {"filename": "a.wav", "size": 10, "template": "x"}
    assert admin_client.post("/api/meetings", json=bad_template).status_code == 422
    options = admin_client.get("/api/meetings/options").json()
    assert options["providers"][0]["kind"] == "mock"
    assert options["templates"][0]["id"] == "group_topic"


def test_public_audio_token(app, admin_client):
    add_mock_provider(admin_client, delay_seconds="3")
    meeting = upload_audio(admin_client, make_wav(5))
    mid = meeting["id"]
    deadline = time.monotonic() + 20
    part: dict = {}
    while time.monotonic() < deadline:
        with app.state.ctx.Session() as db:
            m = db.get(Meeting, mid)
            if m.status == "transcribing" and m.asr_parts and m.asr_parts[0].get("task_id"):
                part = dict(m.asr_parts[0])
                break
        time.sleep(0.05)
    assert part.get("token"), "应该已经提交并生成了令牌"
    url = f"/api/public/meeting-audio/{mid}/0/{part['token']}.mp3"
    anon = TestClient(app)
    full = anon.get(url)
    assert full.status_code == 200 and full.headers["content-type"] == "audio/mpeg"
    assert "no-store" in full.headers["cache-control"]
    head = anon.head(url)
    assert head.status_code == 200 and head.headers["content-length"] == str(len(full.content))
    ranged = anon.get(url, headers={"Range": "bytes=10-19"})
    assert ranged.status_code == 206 and ranged.content == full.content[10:20]
    assert anon.get(f"/api/public/meeting-audio/{mid}/0/{'x' * 43}.mp3").status_code == 404
    assert anon.get(f"/api/public/meeting-audio/{mid}/1/{part['token']}.mp3").status_code == 404
    wait_meeting(admin_client, mid, {"done"})
    assert anon.get(url).status_code == 404, "识别完成后令牌作废"


def test_failure_and_retry(admin_client):
    provider_id = add_mock_provider(admin_client, fail="poll")
    meeting = upload_audio(admin_client, make_wav(5))
    failed = wait_meeting(admin_client, meeting["id"], {"failed", "done"})
    assert failed["status"] == "failed" and "识别失败" in failed["error"]
    assert failed["error_kind"] == "provider"
    resp = admin_client.patch(f"/api/admin/asr/providers/{provider_id}", json={"config": {"fail": ""}})
    assert resp.status_code == 200, resp.text
    assert admin_client.post(f"/api/meetings/{meeting['id']}/retry").status_code == 200
    done = wait_meeting(admin_client, meeting["id"], {"failed", "done"})
    assert done["status"] == "done", done["error"]


def test_auth_error_is_reported(admin_client):
    provider_id = add_mock_provider(admin_client)
    admin_client.patch(f"/api/admin/asr/providers/{provider_id}", json={"secrets": {"api_key": "bad"}})
    meeting = upload_audio(admin_client, make_wav(3))
    failed = wait_meeting(admin_client, meeting["id"], {"failed", "done"})
    assert failed["status"] == "failed" and failed["error_kind"] == "auth"


def test_cancel_and_delete(app, admin_client):
    add_mock_provider(admin_client, delay_seconds="30")
    meeting = upload_audio(admin_client, make_wav(5))
    mid = meeting["id"]
    wait_meeting(admin_client, mid, {"transcribing"})
    assert admin_client.post(f"/api/meetings/{mid}/cancel").status_code == 200
    assert wait_meeting(admin_client, mid, {"canceled"})["status"] == "canceled"
    directory = app.state.ctx.meetings.meeting_dir(mid)
    assert directory.exists()
    assert admin_client.delete(f"/api/meetings/{mid}").status_code == 204
    assert admin_client.get(f"/api/meetings/{mid}").status_code == 404
    deadline = time.monotonic() + 5
    while directory.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not directory.exists()


def test_resume_polling_after_restart(tmp_path, monkeypatch):
    config = build_config(tmp_path, monkeypatch)
    app1 = create_app(config)
    with TestClient(app1, headers={"X-Requested-With": "pytest"}) as c1:
        add_user(app1, *ADMIN, role="admin")
        login(c1, *ADMIN)
        add_mock_provider(c1, delay_seconds="4")
        mid = upload_audio(c1, make_wav(4))["id"]
        deadline = time.monotonic() + 20
        task_id = None
        while time.monotonic() < deadline and not task_id:
            with app1.state.ctx.Session() as db:
                parts = db.get(Meeting, mid).asr_parts
                task_id = parts[0].get("task_id") if parts else None
            time.sleep(0.05)
        assert task_id
    app2 = create_app(config)
    with TestClient(app2, headers={"X-Requested-With": "pytest"}) as c2:
        login(c2, *ADMIN)
        done = wait_meeting(c2, mid, {"done", "failed"})
        assert done["status"] == "done", done["error"]
        with app2.state.ctx.Session() as db:
            assert db.get(Meeting, mid).asr_parts[0]["task_id"] == task_id, "重启后应续查同一个任务，不重新提交"


def test_purge_keeps_text(app, admin_client):
    add_mock_provider(admin_client)
    mid = upload_audio(admin_client, make_wav(4))["id"]
    wait_meeting(admin_client, mid, {"done"})
    with app.state.ctx.Session() as db:
        m = db.get(Meeting, mid)
        m.finished_at = utcnow() - timedelta(days=31)
        db.commit()
    assert app.state.ctx.meetings.purge() == (0, 1)
    directory = app.state.ctx.meetings.meeting_dir(mid)
    assert not (directory / "audio.mp3").exists()
    assert list(directory.glob("asr-*.json")), "识别原始结果（文字）保留"
    meeting = admin_client.get(f"/api/meetings/{mid}").json()
    assert meeting["audio_available"] is False and meeting["audio_expires_at"] is None
    with app.state.ctx.Session() as db:
        assert db.query(MeetingSegment).filter_by(meeting_id=mid).count() > 0
    assert admin_client.get(f"/api/meetings/{mid}/audio").status_code == 404
    assert admin_client.post(f"/api/meetings/{mid}/retry").status_code == 409


def test_stale_upload_is_cleaned(app, admin_client):
    add_mock_provider(admin_client)
    created = admin_client.post("/api/meetings", json={"filename": "a.wav", "size": 100}).json()
    mid = created["meeting"]["id"]
    with app.state.ctx.Session() as db:
        db.get(Meeting, mid).created_at = utcnow() - timedelta(hours=25)
        db.commit()
    assert app.state.ctx.meetings.purge() == (1, 0)
    assert admin_client.get(f"/api/meetings/{mid}").status_code == 404
    assert not app.state.ctx.meetings.meeting_dir(mid).exists()
