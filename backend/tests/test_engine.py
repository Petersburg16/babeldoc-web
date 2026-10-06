from __future__ import annotations

from sqlalchemy import select

from app.engine import build_spec, child_env
from app.models import Job, ModelProfile


def make_job(**options) -> Job:
    return Job(id="j1", user_id=1, filename="x.pdf", lang_in="en", lang_out="zh-CN", billed_pages=1, options=options)


def make_profile(id: int, base_url: str, model: str, **extra) -> ModelProfile:
    return ModelProfile(id=id, name=model, base_url=base_url, model=model, qps=3, json_mode=False, **extra)


def test_build_spec_adds_term_profile_only_when_given(tmp_path):
    main = make_profile(1, "https://a.invalid/v1", "m", send_temperature=True)
    term = make_profile(2, "https://b.invalid/v1", "t", send_temperature=False)
    plain = build_spec(job=make_job(), profile=main, job_dir=tmp_path, watermark_mode="no_watermark")
    assert "term" not in plain["model"]
    # 翻译缓存放在任务目录（不在每次运行前清空的 work/ 里），随任务文件一起删除
    assert plain["cache_db"] == str(tmp_path / "translation-cache.db")

    spec = build_spec(job=make_job(), profile=main, job_dir=tmp_path, watermark_mode="no_watermark", term_profile=term)
    assert spec["model"]["model"] == "m"
    assert spec["model"]["term"]["base_url"] == "https://b.invalid/v1"
    assert spec["model"]["term"]["model"] == "t"
    assert spec["model"]["term"]["send_temperature"] is False
    assert not {"reasoning", "thinking"} & (spec["model"].keys() | spec["model"]["term"].keys())


def test_child_env_passes_term_key_only_when_set(monkeypatch):
    monkeypatch.setenv("BDW_API_KEY", "parent-main")
    monkeypatch.setenv("BDW_TERM_API_KEY", "parent-term")
    env = child_env("main-key", "term-key")
    assert env["BDW_API_KEY"] == "main-key"
    assert env["BDW_TERM_API_KEY"] == "term-key"
    assert "BDW_TERM_API_KEY" not in child_env("main-key")


def test_term_profile_falls_back_when_removed(app, admin_client):
    ctx = app.state.ctx
    with ctx.Session() as db:
        main = db.scalars(select(ModelProfile)).first()
        removed = make_job(term_model_id=999, term_model_name="旧模型", auto_extract_glossary=True)
        term, note = ctx.manager.resolve_term_profile(db, removed, main)
        assert term is None
        assert "旧模型" in note
        assert ctx.manager.resolve_term_profile(db, make_job(term_model_id=main.id), main) == (None, None)
