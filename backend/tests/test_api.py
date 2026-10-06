from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import MIGRATIONS, init_db, make_engine, make_sessionmaker, utcnow
from app.main import create_app
from app.models import (
    JOB_ACTIVE,
    JOB_BILLABLE,
    JOB_FINISHED,
    JOB_RETRYABLE,
    MEETING_ACTIVE,
    MEETING_FINISHED,
    MEETING_RETRYABLE,
    Job,
    User,
)
from tests.conftest import (
    ADMIN,
    add_user,
    build_config,
    login,
    make_pdf,
    update_settings,
    upload,
    wait_status,
    wait_until,
)


def test_meta_reports_setup_and_languages(client):
    meta = client.get("/api/meta").json()
    assert meta["needs_setup"] is True
    assert meta["engine"] == "mock"
    assert meta["file_retention_days"] == 30
    assert {"en", "zh-CN"} <= {lang["code"] for lang in meta["languages"]}


def test_login_logout_and_session(app, client):
    add_user(app, "alice", "alice-password")
    assert client.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
    login(client, "ALICE", "alice-password")
    me = client.get("/api/auth/me").json()
    assert me["user"]["username"] == "alice"
    assert me["usage"]["quota"] == 1000
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_cache_headers_for_spa_and_assets(tmp_path, monkeypatch):
    config = build_config(tmp_path, monkeypatch)
    (config.frontend_dir / "assets").mkdir(parents=True)
    (config.frontend_dir / "index.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    (config.frontend_dir / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    with TestClient(create_app(config)) as c:
        assert c.get("/", headers={"Accept": "text/html"}).headers["cache-control"] == "no-cache, no-transform"
        assert "immutable" in c.get("/assets/index-abc123.js").headers["cache-control"]
        assert c.get("/assets/missing.js").headers["cache-control"] == "no-cache, no-transform"
        assert c.get("/api/meta").headers["cache-control"] == "no-cache, no-transform"


def test_write_requests_need_csrf_header(app):
    with TestClient(app) as bare:
        resp = bare.post("/api/auth/login", json={"username": "x", "password": "y"})
    assert resp.status_code == 403


def test_login_rate_limit(app, client):
    add_user(app, "bob", "bob-password")
    for _ in range(8):
        client.post("/api/auth/login", json={"username": "bob", "password": "nope"})
    resp = client.post("/api/auth/login", json={"username": "bob", "password": "bob-password"})
    assert resp.status_code == 429


def test_invite_registration(admin_client):
    invite = admin_client.post("/api/admin/invites", json={"note": "朋友", "max_uses": 1}).json()
    admin_client.cookies.clear()
    bad = admin_client.post(
        "/api/auth/register", json={"username": "carol", "password": "carol-pass", "invite_code": "WRONG"}
    )
    assert bad.status_code == 400
    ok = admin_client.post(
        "/api/auth/register",
        json={"username": "Carol", "password": "carol-pass", "invite_code": invite["code"].lower()},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["user"]["username"] == "carol"
    admin_client.cookies.clear()
    again = admin_client.post(
        "/api/auth/register", json={"username": "dave", "password": "dave-pass1", "invite_code": invite["code"]}
    )
    assert again.status_code == 400


def test_job_lifecycle_with_mock_engine(app, admin_client):
    add_user(app, "erin", "erin-password")
    login(admin_client, "erin", "erin-password")
    resp = upload(admin_client, "Attention Is All You Need.pdf", page_count=3)
    assert resp.status_code == 201, resp.text
    job = resp.json()[0]
    assert job["status"] == "queued"
    assert job["billed_pages"] == 3
    done = wait_status(admin_client, job["id"], {"succeeded", "failed"})
    assert done["status"] == "succeeded", done
    assert done["files"] == ["dual", "mono", "glossary", "original"]
    assert done["tokens"] == 4321

    pdf = admin_client.get(f"/api/jobs/{job['id']}/files/dual")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
    assert "zh-CN.dual.pdf" in pdf.headers["content-disposition"]

    me = admin_client.get("/api/auth/me").json()
    assert me["usage"]["month_pages"] == 3

    assert admin_client.delete(f"/api/jobs/{job['id']}").status_code == 204
    assert admin_client.get(f"/api/jobs/{job['id']}").status_code == 404
    assert not (app.state.ctx.config.jobs_dir / job["id"]).exists()


def test_delete_job_scrubs_content(app, admin_client):
    resp = upload(admin_client, "secret-fail.pdf", custom_system_prompt="未公开项目的术语")
    job_id = resp.json()[0]["id"]
    assert wait_status(admin_client, job_id, {"failed", "succeeded"})["status"] == "failed"
    failures = admin_client.get("/api/admin/stats").json()["failures"]
    assert [f["id"] for f in failures] == [job_id]

    assert admin_client.delete(f"/api/jobs/{job_id}").status_code == 204
    with app.state.ctx.Session() as db:
        job = db.get(Job, job_id)
        # 只留计费和统计要用的数字
        assert job.filename == "（已删除）"
        assert job.error is None and job.warning is None
        assert "custom_system_prompt" not in job.options
        assert job.billed_pages == 3 and job.deleted_at is not None
    # 后台概览的“最近失败”不再列出已删除的任务
    assert admin_client.get("/api/admin/stats").json()["failures"] == []


def test_migration_scrubs_previously_deleted_jobs(tmp_path):
    engine = make_engine(tmp_path / "old.db")
    try:
        init_db(engine)
        Session = make_sessionmaker(engine)
        with Session() as db:
            db.add(User(id=1, username="u", display_name="u", password_hash="x"))
            db.commit()
            for job_id, deleted in (("gone", utcnow()), ("kept", None)):
                db.add(
                    Job(
                        id=job_id,
                        user_id=1,
                        filename=f"{job_id}.pdf",
                        lang_in="en",
                        lang_out="zh-CN",
                        billed_pages=2,
                        options={"custom_system_prompt": "p", "output": "dual"},
                        error="boom",
                        deleted_at=deleted,
                    )
                )
            db.commit()
        with engine.begin() as conn:
            conn.execute(text("PRAGMA user_version = 1"))  # 上一版的库
        init_db(engine)
        with Session() as db:
            gone, kept = db.get(Job, "gone"), db.get(Job, "kept")
            assert (gone.filename, gone.error, gone.options) == ("（已删除）", None, {"output": "dual"})
            assert (kept.filename, kept.error, kept.options["custom_system_prompt"]) == ("kept.pdf", "boom", "p")
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA user_version")).scalar() == MIGRATIONS[-1][0]
    finally:
        engine.dispose()


def test_failure_then_retry(app, admin_client):
    resp = upload(admin_client, "will-fail.pdf")
    job_id = resp.json()[0]["id"]
    failed = wait_status(admin_client, job_id, {"failed", "succeeded"})
    assert failed["status"] == "failed"
    assert "模拟失败" in failed["error"]
    retried = admin_client.post(f"/api/jobs/{job_id}/retry")
    assert retried.status_code == 200
    again = wait_status(admin_client, job_id, {"failed", "succeeded"})
    assert again["attempts"] == 2


def test_term_model_selection(app, admin_client):
    main = admin_client.get("/api/admin/models").json()[0]
    term_model = {
        "name": "术语模型",
        "base_url": "https://other.invalid/v1",
        "api_key": "sk-term-0987654321",
        "model": "t",
    }
    term = admin_client.post("/api/admin/models", json=term_model).json()
    assert upload(admin_client, term_model_id=9999).status_code == 400

    same = upload(admin_client, "same.pdf", term_model_id=main["id"]).json()[0]
    off = upload(admin_client, "off.pdf", term_model_id=term["id"], auto_extract_glossary=False).json()[0]
    chosen = upload(admin_client, "chosen.pdf", term_model_id=term["id"]).json()[0]
    assert "term_model_id" not in same["options"]
    assert "term_model_id" not in off["options"]
    assert chosen["options"]["term_model_name"] == "术语模型"

    for job in (same, off, chosen):
        assert wait_status(admin_client, job["id"], {"succeeded", "failed"})["status"] == "succeeded"
    with app.state.ctx.Session() as db:
        echoed = {j["id"]: db.get(Job, j["id"]).result["stats"]["term"] for j in (same, off, chosen)}
    assert echoed[chosen["id"]] == {"spec": True, "key": True}
    assert echoed[same["id"]] == echoed[off["id"]] == {"spec": False, "key": False}

    admin_client.patch(f"/api/admin/models/{term['id']}", json={"enabled": False})
    assert upload(admin_client, term_model_id=term["id"]).status_code == 400


def test_cancel_running_job(tmp_path, monkeypatch):
    config = build_config(tmp_path, monkeypatch, mock_seconds=30)
    app = create_app(config)
    with TestClient(app, headers={"X-Requested-With": "pytest"}) as client:
        add_user(app, *ADMIN, role="admin")
        login(client, *ADMIN)
        client.post("/api/admin/models", json={"name": "m", "api_key": "k", "model": "m"})
        job_id = upload(client).json()[0]["id"]
        wait_status(client, job_id, {"running"})
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
        assert wait_status(client, job_id, {"canceled", "failed", "succeeded"})["status"] == "canceled"


def test_queue_and_page_rules(app, admin_client):
    add_user(app, "frank", "frank-password", page_quota=4)
    login(admin_client, "frank", "frank-password")
    bad_range = upload(admin_client, pages="abc")
    assert bad_range.status_code == 400
    out_of_range = upload(admin_client, pages="5-9")
    assert out_of_range.status_code == 400
    ranged = upload(admin_client, pages="2-")
    assert ranged.status_code == 201
    assert ranged.json()[0]["billed_pages"] == 2
    over_quota = upload(admin_client)
    assert over_quota.status_code == 400
    assert "额度" in over_quota.json()["detail"]
    not_pdf = admin_client.post(
        "/api/jobs", files=[("files", ("notes.pdf", b"hello", "application/pdf"))], data={"options": "{}"}
    )
    assert not_pdf.status_code == 400


def test_admin_endpoints_need_admin(app, client):
    add_user(app, "gina", "gina-password")
    login(client, "gina", "gina-password")
    assert client.get("/api/admin/stats").status_code == 403


def test_admin_models_mask_keys_and_stats(admin_client):
    models = admin_client.get("/api/admin/models").json()
    assert models[0]["api_key_set"] is True
    assert "1234567890" not in models[0]["api_key_masked"]
    assert models[0]["is_default"] is True
    stats = admin_client.get("/api/admin/stats").json()
    assert len(stats["daily"]) == 14
    assert stats["engine"]["mode"] == "mock"


def test_admin_cannot_lock_out_last_admin(admin_client):
    me = admin_client.get("/api/auth/me").json()["user"]
    resp = admin_client.patch(f"/api/admin/users/{me['id']}", json={"role": "user"})
    assert resp.status_code == 400


def test_settings_roundtrip(admin_client):
    saved = update_settings(admin_client, site_name="我的翻译站", max_concurrent_jobs=2).json()
    assert saved["site_name"] == "我的翻译站"
    assert admin_client.get("/api/meta").json()["site_name"] == "我的翻译站"


def test_running_jobs_are_requeued_after_restart(tmp_path, monkeypatch):
    config = build_config(tmp_path, monkeypatch)
    first = create_app(config)
    user_id = add_user(first, "henry", "henry-password")
    with first.state.ctx.Session() as db:
        db.add(
            Job(
                id="stale1",
                user_id=user_id,
                status="running",
                filename="x.pdf",
                lang_in="en",
                lang_out="zh-CN",
                started_at=utcnow(),
            )
        )
        db.commit()
    second = create_app(config)

    def load() -> Job:
        with second.state.ctx.Session() as db:
            return db.get(Job, "stale1")

    with TestClient(second):
        job = wait_until(load, lambda j: j.status == "failed", timeout=10, interval=0.1)
    assert job.attempts == 1
    assert "模型" in job.error


def test_make_pdf_helper_is_valid():
    assert make_pdf(2).startswith(b"%PDF")


def test_status_constants_keep_stored_values():
    """状态取值存在库里、前端也按它们判断：这些常量只是换个写法，值不能变。"""
    assert set(JOB_ACTIVE) == {"queued", "running"}
    assert set(JOB_BILLABLE) == {"queued", "running", "succeeded"}
    assert set(JOB_FINISHED) == {"succeeded", "failed", "canceled"}
    assert set(JOB_RETRYABLE) == {"failed", "canceled"}
    assert set(MEETING_ACTIVE) == {"queued", "transcoding", "transcribing", "processing"}
    assert set(MEETING_FINISHED) == {"done", "failed", "canceled"}
    assert set(MEETING_RETRYABLE) == {"failed", "canceled"}
