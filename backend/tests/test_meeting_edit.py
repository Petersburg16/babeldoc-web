"""会议记录的编辑接口：改逐字稿、改名与合并说话人、采纳建议、后台重跑。"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.meeting import processing
from app.models import Meeting, MeetingLlmModel, MeetingLlmPreset
from app.routers import meeting_edit
from tests.conftest import ADMIN, add_user, login, seed_meeting, user_id_of, wait_until
from tests.llm_fake import install_fake_llm, polish_reply, tidy

LINES = [
    ("S1", "嗯大家好我是张老师"),
    ("S2", "好的张老师我先说一下进展"),
    ("S3", "我补充一点数据的问题"),
    ("S2", "那个下周把对比图发给您"),
    ("S1", "好的就这样"),
]


def seed(app, username: str = ADMIN[0], **meeting: Any) -> str:
    speakers = {
        "S1": {"name": "", "guess": {"name": "张老师", "evidence": "我是张老师", "confidence": "high"}},
        "S2": {"name": "", "guess": {"name": "李明", "evidence": "", "confidence": "low"}},
        "S3": {"name": "王芳", "guess": {"name": "王五", "evidence": "", "confidence": "low"}},
    }
    for info in speakers.values():
        info["merged_into"] = None
    values: dict[str, Any] = {
        "duration_ms": 40_000,
        "transcript_state": "raw",
        "transcript_rev": 3,
        "minutes_state": "ready",
        "minutes_md": "# 纪要\n- [[S1]] 要求 [00:21] 发对比图",
        "minutes_rev": 3,
        "speakers": speakers,
        "progress": 100,
        **meeting,
    }
    segments = [(i * 7000, i * 7000 + 6000, speaker, text) for i, (speaker, text) in enumerate(LINES)]
    return seed_meeting(app, user_id_of(app, username), segments, **values)


def segments(client: TestClient, mid: str) -> list[dict]:
    return client.get(f"/api/meetings/{mid}/segments").json()


def detail(client: TestClient, mid: str) -> dict:
    return client.get(f"/api/meetings/{mid}").json()


def wait_op(client: TestClient, mid: str, timeout: float = 15) -> dict:
    return wait_until(
        lambda: detail(client, mid),
        lambda m: m["op"] is None,
        timeout=timeout,
        message=lambda m: f"op {m['op']} did not finish",
    )


def test_edit_text_and_revert(app, admin_client):
    mid = seed(app)
    resp = admin_client.patch(f"/api/meetings/{mid}/segments/1", json={"text": " 好的张老师，我先说一下进展。\n"})
    assert resp.status_code == 200, resp.text
    seg = resp.json()
    assert seg["text"] == "好的张老师，我先说一下进展。" and seg["edited"] is True
    assert seg["raw_text"] == LINES[1][1]
    m = detail(admin_client, mid)
    assert m["transcript_rev"] == 4 and m["minutes_stale"] is True

    same = admin_client.patch(f"/api/meetings/{mid}/segments/1", json={"text": "好的张老师，我先说一下进展。"})
    assert same.status_code == 200 and detail(admin_client, mid)["transcript_rev"] == 4, "没改动不加版本号"

    reverted = admin_client.post(f"/api/meetings/{mid}/segments/1/revert").json()
    assert reverted["text"] == LINES[1][1] and reverted["edited"] is False
    assert detail(admin_client, mid)["transcript_rev"] == 5

    assert admin_client.patch(f"/api/meetings/{mid}/segments/1", json={"text": "  \n "}).status_code == 400
    assert admin_client.patch(f"/api/meetings/{mid}/segments/1", json={}).status_code == 400
    assert admin_client.patch(f"/api/meetings/{mid}/segments/99", json={"text": "x"}).status_code == 404
    assert admin_client.patch(f"/api/meetings/{mid}/segments/1", json={"text": "x" * 5001}).status_code == 422


def test_edit_speaker(app, admin_client):
    mid = seed(app)
    seg = admin_client.patch(f"/api/meetings/{mid}/segments/2", json={"speaker": "S2"}).json()
    assert seg["speaker"] == "S2" and seg["asr_speaker"] == "S3" and seg["edited"] is False
    assert detail(admin_client, mid)["transcript_rev"] == 4

    seg = admin_client.patch(
        f"/api/meetings/{mid}/segments/0", json={"speaker": "new", "text": "大家好，我是张老师。"}
    ).json()
    assert seg["speaker"] == "S4" and seg["edited"] is True
    m = detail(admin_client, mid)
    assert m["speakers"]["S4"] == {"name": "", "guess": None, "merged_into": None, "merge_hint": None}
    assert m["transcript_rev"] == 5, "一次请求同时改文字和说话人只加一次"

    assert admin_client.patch(f"/api/meetings/{mid}/segments/0", json={"speaker": "S9"}).status_code == 404

    # 分给一位已经合并掉的说话人时，直接记到合并后的目标名下
    assert (
        admin_client.post(f"/api/meetings/{mid}/speakers/merge", json={"source": "S3", "target": "S1"}).status_code
        == 200
    )
    seg = admin_client.patch(f"/api/meetings/{mid}/segments/1", json={"speaker": "S3"}).json()
    assert seg["speaker"] == "S1"


def test_rename_merge_unmerge(app, admin_client):
    mid = seed(app)
    m = admin_client.post(f"/api/meetings/{mid}/speakers/S1/rename", json={"name": "  张 老师 "}).json()
    assert m["speakers"]["S1"]["name"] == "张 老师" and m["transcript_rev"] == 3 and m["minutes_stale"] is False
    assert m["minutes_md"].startswith("# 纪要"), "返回带纪要正文的详情"
    m = admin_client.post(f"/api/meetings/{mid}/speakers/S1/rename", json={"name": ""}).json()
    assert m["speakers"]["S1"]["name"] == ""
    assert admin_client.post(f"/api/meetings/{mid}/speakers/S9/rename", json={"name": "x"}).status_code == 404
    assert admin_client.post(f"/api/meetings/{mid}/speakers/S1/rename", json={"name": "x" * 33}).status_code == 422

    # 先手动把 S3 的那句改给 S1，再合并 S2 → S1
    admin_client.patch(f"/api/meetings/{mid}/segments/2", json={"speaker": "S1"})
    rev = detail(admin_client, mid)["transcript_rev"]
    m = admin_client.post(f"/api/meetings/{mid}/speakers/merge", json={"source": "S2", "target": "S1"}).json()
    assert m["speakers"]["S2"]["merged_into"] == "S1" and m["transcript_rev"] == rev, "合并不改版本号"
    assert [s["speaker"] for s in segments(admin_client, mid)] == ["S1", "S1", "S1", "S1", "S1"]

    bad = admin_client.post(f"/api/meetings/{mid}/speakers/merge", json={"source": "S1", "target": "S2"})
    assert bad.status_code == 400, "目标已合并到自己身上"
    again = admin_client.post(f"/api/meetings/{mid}/speakers/merge", json={"source": "S2", "target": "S3"})
    assert again.status_code == 400
    assert (
        admin_client.post(f"/api/meetings/{mid}/speakers/merge", json={"source": "S1", "target": "S1"}).status_code
        == 400
    )
    assert (
        admin_client.post(f"/api/meetings/{mid}/speakers/merge", json={"source": "S9", "target": "S1"}).status_code
        == 404
    )

    m = admin_client.post(f"/api/meetings/{mid}/speakers/unmerge", json={"speaker": "S2"}).json()
    assert m["speakers"]["S2"]["merged_into"] is None and m["transcript_rev"] == rev
    assert [s["speaker"] for s in segments(admin_client, mid)] == ["S1", "S2", "S1", "S2", "S1"], "按识别归属还原"
    assert admin_client.post(f"/api/meetings/{mid}/speakers/unmerge", json={"speaker": "S2"}).status_code == 400


def test_merge_chain_is_flattened(app, admin_client):
    mid = seed(app)
    admin_client.post(f"/api/meetings/{mid}/speakers/merge", json={"source": "S3", "target": "S2"})
    m = admin_client.post(f"/api/meetings/{mid}/speakers/merge", json={"source": "S2", "target": "S1"}).json()
    assert m["speakers"]["S3"]["merged_into"] == "S1" and m["speakers"]["S2"]["merged_into"] == "S1"
    admin_client.post(f"/api/meetings/{mid}/speakers/unmerge", json={"speaker": "S2"})
    assert [s["speaker"] for s in segments(admin_client, mid)] == ["S1", "S2", "S1", "S2", "S1"]
    m = admin_client.post(f"/api/meetings/{mid}/speakers/merge", json={"source": "S2", "target": "S3"}).json()
    assert m["speakers"]["S2"]["merged_into"] == "S1", "合并到已合并的说话人时，记到最终目标"


def test_merge_hint_follows_merge(app, admin_client):
    mid = seed(app)
    with app.state.ctx.Session() as db:
        m = db.get(Meeting, mid)
        speakers = {k: dict(v) for k, v in m.speakers.items()}
        speakers["S2"]["merge_hint"] = {"with": "S1", "reason": "同名"}
        m.speakers = speakers
        db.commit()
    m = admin_client.post(f"/api/meetings/{mid}/speakers/merge", json={"source": "S2", "target": "S1"}).json()
    assert m["speakers"]["S2"]["merge_hint"] is None


def test_accept_guesses(app, admin_client):
    mid = seed(app)
    m = admin_client.post(f"/api/meetings/{mid}/speakers/accept-guesses", json={"speakers": ["S2", "S9"]}).json()
    assert m["speakers"]["S2"]["name"] == "李明" and m["speakers"]["S1"]["name"] == ""
    m = admin_client.post(f"/api/meetings/{mid}/speakers/accept-guesses", json={"speakers": None}).json()
    assert m["speakers"]["S1"]["name"] == "张老师"
    assert m["speakers"]["S3"]["name"] == "王芳", "全部采纳不覆盖已确认的名字"
    m = admin_client.post(f"/api/meetings/{mid}/speakers/accept-guesses", json={"speakers": ["S3"]}).json()
    assert m["speakers"]["S3"]["name"] == "王五", "明确点名时采纳"
    assert m["transcript_rev"] == 3


def test_edit_is_private(app, admin_client):
    mid = seed(app)
    add_user(app, "bob", "bob-password")
    other = TestClient(app, headers={"X-Requested-With": "pytest"})
    login(other, "bob", "bob-password")
    assert other.patch(f"/api/meetings/{mid}/segments/0", json={"text": "x"}).status_code == 404
    assert other.post(f"/api/meetings/{mid}/speakers/S1/rename", json={"name": "x"}).status_code == 404
    assert other.post(f"/api/meetings/{mid}/ops/polish", json={}).status_code == 404
    bare = TestClient(app)
    bare.cookies = admin_client.cookies
    assert bare.patch(f"/api/meetings/{mid}/segments/0", json={"text": "x"}).status_code == 403


def test_op_conflicts(app, admin_client):
    mid = seed(app, status="processing")
    assert admin_client.post(f"/api/meetings/{mid}/ops/polish", json={}).status_code == 409
    mid = seed(app, op="minutes")
    resp = admin_client.post(f"/api/meetings/{mid}/ops/polish", json={})
    assert resp.status_code == 409 and "生成纪要" in resp.json()["detail"]
    mid = seed(app)
    assert admin_client.post(f"/api/meetings/{mid}/ops/translate", json={}).status_code == 404
    assert admin_client.post(f"/api/meetings/{mid}/ops/minutes", json={"template": "x"}).status_code == 422
    with app.state.ctx.Session() as db:
        for model in db.scalars(select(MeetingLlmModel)):
            model.enabled = False
        db.commit()
    resp = admin_client.post(f"/api/meetings/{mid}/ops/polish", json={})
    reason = resp.json()["detail"]
    assert resp.status_code == 400 and "“整理逐字稿”" in reason and "已停用" in reason, "写明哪个方案的哪个用途"
    assert detail(admin_client, mid)["op"] is None
    with app.state.ctx.Session() as db:
        for preset in db.scalars(select(MeetingLlmPreset)):
            preset.enabled = False
        db.commit()
    resp = admin_client.post(f"/api/meetings/{mid}/ops/minutes", json={})
    assert resp.status_code == 400 and "还没有配置" in resp.json()["detail"]
    m = detail(admin_client, mid)
    assert m["op"] is None and m["minutes_state"] == "ready"


def test_op_switch_preset_is_atomic(app, admin_client, monkeypatch):
    resp = admin_client.post(
        "/api/admin/meeting-llm/models",
        json={"name": "精细", "base_url": "https://example.invalid/v1", "api_key": "sk-x", "model": "gpt-6-astra"},
    )
    assert resp.status_code == 201, resp.text
    fine_model = resp.json()["id"]
    steps = {
        s: {"model_id": fine_model, "effort": "high" if s == "minutes" else "default"}
        for s in ("speakers", "polish", "minutes", "chat")
    }
    resp = admin_client.post("/api/admin/meeting-llm/presets", json={"name": "精细方案", "steps": steps})
    assert resp.status_code == 201, resp.text
    fine = resp.json()
    assert fine["is_default"] is False, "已有默认方案时新方案不抢默认"

    # 已经在跑别的操作：直接 409，方案不变
    busy = seed(app, op="minutes")
    resp = admin_client.post(f"/api/meetings/{busy}/ops/polish", json={"llm_preset_id": fine["id"]})
    assert resp.status_code == 409
    m = detail(admin_client, busy)
    assert m["llm_preset_id"] is None and m["model_name"] == "" and m["op"] == "minutes"

    # 检查都通过之后、占住操作之前被别的请求抢先：方案也不能只改一半
    mid = seed(app)
    real = meeting_edit.resolve_meeting_llm

    def racing(db, box, preset_id, step):
        with app.state.ctx.Session() as other:
            other.get(Meeting, mid).op = "speakers"
            other.commit()
        return real(db, box, preset_id, step)

    monkeypatch.setattr(meeting_edit, "resolve_meeting_llm", racing)
    resp = admin_client.post(f"/api/meetings/{mid}/ops/polish", json={"llm_preset_id": fine["id"]})
    assert resp.status_code == 409 and "正在处理其他操作" in resp.json()["detail"], "由占住操作的那条 UPDATE 拦下"
    monkeypatch.setattr(meeting_edit, "resolve_meeting_llm", real)
    with app.state.ctx.Session() as db:
        m = db.get(Meeting, mid)
        assert m.llm_preset_id is None and m.model_name == "" and m.transcript_state == "raw"
        m.op = None
        db.commit()

    assert admin_client.post(f"/api/meetings/{mid}/ops/polish", json={"llm_preset_id": 9999}).status_code == 400
    assert detail(admin_client, mid)["llm_preset_id"] is None

    calls = install_fake_llm(app, lambda messages, payload: polish_reply(messages))
    resp = admin_client.post(f"/api/meetings/{mid}/ops/polish", json={"llm_preset_id": fine["id"]})
    assert resp.status_code == 202, resp.text
    assert resp.json()["llm_preset_id"] == fine["id"] and resp.json()["model_name"] == "精细方案"
    m = wait_op(admin_client, mid)
    assert m["transcript_state"] == "polished" and m["llm_preset_id"] == fine["id"]
    assert calls and all(c["model"] == "gpt-6-astra" and "reasoning_effort" not in c for c in calls)


def test_op_polish_keeps_manual_edits(app, admin_client):
    install_fake_llm(app, lambda messages, payload: polish_reply(messages))
    mid = seed(app, transcript_state="polished")
    admin_client.patch(f"/api/meetings/{mid}/segments/3", json={"text": "下周把对比图发给您。"})
    resp = admin_client.post(f"/api/meetings/{mid}/ops/polish", json={})
    assert resp.status_code == 202, resp.text
    assert resp.json()["op"] == "polish" and resp.json()["transcript_state"] == "polishing"
    m = wait_op(admin_client, mid)
    assert m["transcript_state"] == "polished" and m["warning"] is None
    assert m["stage"] == "" and m["progress"] == 100
    texts = [s["text"] for s in segments(admin_client, mid)]
    assert texts[0] == tidy(LINES[0][1]) and texts[3] == "下周把对比图发给您。"
    assert m["minutes_stale"] is True


def test_op_speakers(app, admin_client):
    install_fake_llm(app, lambda messages, payload: "S1|张老师|high|我是张老师\nS2|李明|medium|好的张老师")
    mid = seed(app)
    with app.state.ctx.Session() as db:
        m = db.get(Meeting, mid)
        m.speakers = {k: {**v, "guess": None} for k, v in m.speakers.items()}
        db.commit()
    assert admin_client.post(f"/api/meetings/{mid}/ops/speakers", json={}).status_code == 202
    m = wait_op(admin_client, mid)
    assert m["speakers"]["S1"]["guess"]["name"] == "张老师" and m["speakers"]["S1"]["name"] == ""
    assert m["speakers"]["S3"]["guess"] is None, "已命名的不猜"
    assert m["tokens"] > 0


@pytest.fixture
def slow_minutes(monkeypatch):
    calls: list[dict[str, Any]] = []
    release = threading.Event()

    async def fake(manager, meeting_id, client, *, template=None, extra=None, on_progress=None):
        calls.append({"template": template, "extra": extra})
        while not release.is_set():
            await asyncio.sleep(0.02)
        with manager.Session() as db:
            m = db.get(Meeting, meeting_id)
            m.minutes_state = "ready"
            m.minutes_md = "# 新纪要"
            m.minutes_rev = m.transcript_rev
            db.commit()
        return None

    monkeypatch.setattr(processing.minutes, "generate_minutes", fake)
    return calls, release


def test_op_minutes_passes_params_and_blocks_others(app, admin_client, slow_minutes):
    calls, release = slow_minutes
    install_fake_llm(app, lambda messages, payload: polish_reply(messages))
    mid = seed(app)
    body = {"template": "group_speaker", "extra_instructions": "  重点写实验安排  "}
    resp = admin_client.post(f"/api/meetings/{mid}/ops/minutes", json=body)
    assert resp.status_code == 202 and resp.json()["minutes_state"] == "generating"
    wait_until(lambda: calls, timeout=5, interval=0.02)
    assert calls == [{"template": "group_speaker", "extra": "重点写实验安排"}]
    busy = admin_client.post(f"/api/meetings/{mid}/ops/polish", json={})
    assert busy.status_code == 409 and "生成纪要" in busy.json()["detail"]
    release.set()
    m = wait_op(admin_client, mid)
    assert m["minutes_state"] == "ready" and m["minutes_md"] == "# 新纪要" and m["minutes_stale"] is False
    assert admin_client.post(f"/api/meetings/{mid}/ops/polish", json={}).status_code == 202
    wait_op(admin_client, mid)


def test_op_minutes_without_template_uses_saved(app, admin_client, slow_minutes):
    calls, release = slow_minutes
    release.set()
    mid = seed(app)
    assert admin_client.post(f"/api/meetings/{mid}/ops/minutes").status_code == 202
    wait_op(admin_client, mid)
    assert calls == [{"template": None, "extra": None}]
