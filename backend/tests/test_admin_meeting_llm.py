"""管理后台的会议模型与整理方案（/api/admin/meeting-llm），以及成员端选方案。"""

from __future__ import annotations

from typing import Any

import httpx

from app.models import Meeting, MeetingLlmModel
from tests.conftest import ASTRA, add_mock_provider, add_user, login
from tests.llm_fake import FakeLlmFailure, FakeReply, install_fake_llm, install_transport

BASE = "/api/admin/meeting-llm"
TRANSLATION_KEY = "sk-test-1234567890"  # conftest 建的翻译模型
MEETING_KEY = "sk-meeting-1234567890"  # conftest 建的会议模型
RELAY_400 = 'level "bdw-probe" not supported, valid levels: low, medium, high, xhigh, max'


def translation_id(client) -> int:
    return client.get("/api/admin/models").json()[0]["id"]


def models(client) -> list[dict[str, Any]]:
    resp = client.get(f"{BASE}/models")
    assert resp.status_code == 200, resp.text
    return resp.json()


def presets(client) -> list[dict[str, Any]]:
    resp = client.get(f"{BASE}/presets")
    assert resp.status_code == 200, resp.text
    return resp.json()


def create_model(client, **body: Any) -> dict[str, Any]:
    resp = client.post(f"{BASE}/models", json={"name": "旗舰", "copy_from_model_id": translation_id(client), **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


def steps_for(model_id: int, **overrides: dict[str, Any]) -> dict[str, Any]:
    steps = {s: {"model_id": model_id} for s in ("speakers", "polish", "minutes", "chat")}
    for step, extra in overrides.items():
        steps[step].update(extra)
    return steps


def stored_key(app, model_id: int) -> str:
    ctx = app.state.ctx
    with ctx.Session() as db:
        return ctx.secrets.decrypt(db.get(MeetingLlmModel, model_id).api_key_enc)


# ---------- 会议模型 ----------


def test_list_models_never_returns_key(app, admin_client):
    resp = admin_client.get(f"{BASE}/models")
    assert MEETING_KEY not in resp.text
    [model] = resp.json()
    assert model["api_key_set"] is True and model["api_key_masked"] and model["api_key_masked"] != MEETING_KEY
    assert model["effort_levels"] == [] and model["used_by"] == ["会议测试模型"]
    assert model["context_chars"] is None and model["qps"] == 3 and model["json_mode"] is False


def test_create_copies_connection_on_server(app, admin_client):
    resp = admin_client.post(
        f"{BASE}/models",
        json={"name": "旗舰", "model": "gpt-6-astra", "copy_from_model_id": translation_id(admin_client)},
    )
    assert resp.status_code == 201, resp.text
    assert TRANSLATION_KEY not in resp.text
    model = resp.json()
    assert model["base_url"] == "https://example.invalid/v1" and model["api_key_set"] is True
    assert model["effort_levels"] == ASTRA and model["builtin_effort_levels"] == ASTRA, "按模型名用内置档位表"
    assert stored_key(app, model["id"]) == TRANSLATION_KEY
    assert model["used_by"] == []

    # 自己填了的以填的为准
    own = create_model(
        admin_client, name="自填", model="m", base_url="https://other.invalid/v1", api_key="sk-own-12345"
    )
    assert own["base_url"] == "https://other.invalid/v1" and stored_key(app, own["id"]) == "sk-own-12345"
    assert own["effort_levels"] == []
    levels = create_model(admin_client, name="手填档位", model="m", effort_levels=["HIGH", "low", "low"])
    assert levels["effort_levels"] == ["low", "high"]

    bad = admin_client.post(f"{BASE}/models", json={"name": "x", "model": "m", "effort_levels": ["turbo"]})
    assert bad.status_code == 422
    missing = admin_client.post(f"{BASE}/models", json={"name": "x", "model": "m", "copy_from_model_id": 9999})
    assert missing.status_code == 400 and "翻译模型不存在" in missing.json()["detail"]
    assert len(models(admin_client)) == 4


def test_create_preset_with_model(app, admin_client):
    model = create_model(admin_client, model="gpt-6-astra", create_preset=True)
    preset = next(p for p in presets(admin_client) if p["name"] == "旗舰")
    assert preset["is_default"] is False, "已经有默认方案时不抢"
    assert all(preset["steps"][s]["model_id"] == model["id"] for s in ("speakers", "polish", "minutes", "chat"))
    assert all(preset["steps"][s]["effort"] == "default" for s in ("speakers", "polish", "minutes", "chat"))
    assert [preset["steps"][s]["timeout_s"] for s in ("speakers", "polish", "minutes", "chat")] == [300, 300, 900, 600]
    assert preset["problems"] == []


def test_patch_model(app, admin_client):
    model = create_model(admin_client, model="m", api_key="sk-first-12345")
    url = f"{BASE}/models/{model['id']}"
    resp = admin_client.patch(url, json={"name": " 改名 ", "qps": 5, "json_mode": True, "context_chars": 50_000})
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["name"] == "改名" and out["qps"] == 5 and out["json_mode"] is True and out["context_chars"] == 50_000
    assert stored_key(app, model["id"]) == "sk-first-12345", "不填 Key 就不动"

    assert admin_client.patch(url, json={"context_chars": None}).json()["context_chars"] is None, "可以清空"
    admin_client.patch(url, json={"api_key": "sk-second-12345"})
    assert stored_key(app, model["id"]) == "sk-second-12345"
    out = admin_client.patch(url, json={"clear_api_key": True}).json()
    assert out["api_key_set"] is False and stored_key(app, model["id"]) == ""
    out = admin_client.patch(url, json={"copy_from_model_id": translation_id(admin_client)}).json()
    assert out["api_key_set"] is True and stored_key(app, model["id"]) == TRANSLATION_KEY
    assert admin_client.patch(url, json={"effort_levels": ["max", "LOW"]}).json()["effort_levels"] == ["low", "max"]
    assert admin_client.patch(f"{BASE}/models/9999", json={"name": "x"}).status_code == 404


def test_delete_model_in_use_is_refused(app, admin_client):
    [used] = models(admin_client)
    resp = admin_client.delete(f"{BASE}/models/{used['id']}")
    assert resp.status_code == 409 and "会议测试模型" in resp.json()["detail"]
    spare = create_model(admin_client, model="m")
    assert admin_client.delete(f"{BASE}/models/{spare['id']}").status_code == 204
    assert admin_client.delete(f"{BASE}/models/{spare['id']}").status_code == 404
    assert [m["id"] for m in models(admin_client)] == [used["id"]]


# ---------- 检测思考档位、测试 ----------


def test_detect_efforts_from_relay_error(app, admin_client):
    def relay(messages, payload):
        raise FakeLlmFailure(400, RELAY_400)

    calls = install_fake_llm(app, relay)
    model = create_model(admin_client, model="gpt-6")
    resp = admin_client.post(f"{BASE}/models/detect-efforts", json={"model_id": model["id"]})
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["detected"] == ASTRA and out["builtin"] == ["none", *ASTRA]
    assert out["suggested"] == ["none", *ASTRA], "中转不列 none 但内置表有：补上"
    assert calls[-1]["model"] == "gpt-6" and calls[-1]["reasoning_effort"] == "bdw-probe"
    assert calls[-1]["max_completion_tokens"] == 1, "万一对方直接回答，也只花一个 token"

    resp = admin_client.post(
        f"{BASE}/models/detect-efforts",
        json={"model": "gpt-6-astra", "copy_from_model_id": translation_id(admin_client)},
    )
    assert resp.json()["suggested"] == ASTRA and calls[-1]["model"] == "gpt-6-astra"


def test_detect_efforts_fallbacks(app, admin_client):
    install_fake_llm(app, lambda messages, payload: "hi")
    resp = admin_client.post(f"{BASE}/models/detect-efforts", json={"model": "gpt-6-astra"})
    out = resp.json()
    assert resp.status_code == 200 and out["detected"] == [] and out["suggested"] == ASTRA
    assert "内置表" in out["message"]

    def unauthorized(messages, payload):
        raise FakeLlmFailure(401, "invalid api key")

    install_fake_llm(app, unauthorized)
    resp = admin_client.post(f"{BASE}/models/detect-efforts", json={"model": "gpt-6-astra"})
    assert resp.status_code == 400 and "拒绝了 Key" in resp.json()["detail"]
    resp = admin_client.post(f"{BASE}/models/detect-efforts", json={})
    assert resp.status_code == 400 and "模型名" in resp.json()["detail"]


def test_probe_models_and_saved_keys(app, admin_client):
    seen: list[httpx.Request] = []

    def relay(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("authorization") == "Bearer sk-bad":
            return httpx.Response(401, json={"error": {"message": "invalid api key"}})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-6"}, {"id": "gpt-5.6"}, {"id": "gpt-6"}, {}]})
        return httpx.Response(400, json={"error": {"message": RELAY_400}})

    install_transport(app, relay)
    [saved] = models(admin_client)
    resp = admin_client.post(f"{BASE}/models/probe", json={"model_id": saved["id"]})
    assert resp.status_code == 200 and resp.json() == {"models": ["gpt-5.6", "gpt-6"]}, "去重排序"
    assert str(seen[-1].url) == "https://example.invalid/v1/models"
    assert seen[-1].headers["authorization"] == f"Bearer {MEETING_KEY}", "Key 留空时用已保存的会议模型的"

    resp = admin_client.post(f"{BASE}/models/probe", json={"copy_from_model_id": translation_id(admin_client)})
    assert resp.status_code == 200 and seen[-1].headers["authorization"] == f"Bearer {TRANSLATION_KEY}"
    resp = admin_client.post(f"{BASE}/models/probe", json={"model_id": saved["id"], "api_key": "sk-typed-123"})
    assert resp.status_code == 200 and seen[-1].headers["authorization"] == "Bearer sk-typed-123", "填了的以填的为准"

    resp = admin_client.post(
        f"{BASE}/models/probe", json={"base_url": "https://other.invalid/v1/", "api_key": "sk-bad"}
    )
    assert str(seen[-1].url) == "https://other.invalid/v1/models"
    assert resp.status_code == 400 and resp.json()["detail"] == "接口返回 401：invalid api key"

    resp = admin_client.post(
        f"{BASE}/models/detect-efforts", json={"model": "gpt-6", "copy_from_model_id": translation_id(admin_client)}
    )
    assert resp.status_code == 200 and resp.json()["detected"] == ASTRA
    assert str(seen[-1].url) == "https://example.invalid/v1/chat/completions"
    assert seen[-1].headers["authorization"] == f"Bearer {TRANSLATION_KEY}", "检测档位也用复制来的 Key"


def test_model_test_reports_usage_and_sent_params(app, admin_client):
    calls = install_fake_llm(app, lambda m, p: FakeReply("连接正常", reasoning_tokens=12, tokens=40, reasoning="嗯"))
    model = create_model(admin_client, model="gpt-6-astra")
    url = f"{BASE}/models/{model['id']}/test"
    out = admin_client.post(url, json={"effort": "high"}).json()
    assert out["ok"] is True and out["reply"] == "连接正常" and out["error"] is None
    assert (out["tokens"], out["reasoning_tokens"], out["finish_reason"]) == (40, 12, "stop")
    assert out["sent"] == {"model": "gpt-6-astra", "reasoning_effort": "high"} and out["latency_ms"] >= 0
    assert calls[-1] == {**out["sent"], "messages": calls[-1]["messages"]}, "报告的就是实际发出的参数"

    assert admin_client.post(url, json={}).json()["sent"] == {"model": "gpt-6-astra"}
    out = admin_client.post(url, json={"effort": "minimal"}).json()
    assert out["sent"]["reasoning_effort"] == "low", "就近映射"
    resp = admin_client.post(url, json={"effort": "none"})
    assert resp.status_code == 400 and "档位表" in resp.json()["detail"]
    assert admin_client.post(url, json={"effort": "turbo"}).status_code == 422
    assert admin_client.post(url, json={"effort": " HIGH "}).json()["sent"]["reasoning_effort"] == "high"
    assert admin_client.post(f"{BASE}/models/9999/test", json={}).status_code == 404

    def unauthorized(messages, payload):
        raise FakeLlmFailure(401, "invalid api key")

    install_fake_llm(app, unauthorized)
    out = admin_client.post(url, json={}).json()
    assert out["ok"] is False and "401" in out["error"] and "invalid api key" in out["error"]

    install_fake_llm(app, lambda m, p: FakeReply("", finish_reason="length", reasoning_tokens=1))
    out = admin_client.post(url, json={}).json()
    assert out["ok"] is False and "最大输出" in out["error"]


# ---------- 整理方案 ----------


def test_preset_validation(app, admin_client):
    model = create_model(admin_client, model="gpt-6-astra")
    plain = models(admin_client)[0]  # conftest 的模型，没有档位表

    def create(steps: dict[str, Any], **extra: Any):
        return admin_client.post(f"{BASE}/presets", json={"name": "方案", "steps": steps, **extra})

    resp = create(steps_for(model["id"], minutes={"effort": "minimal"}))
    assert resp.status_code == 400 and "“生成纪要”的思考强度不在模型“旗舰”的档位里" in resp.json()["detail"]
    resp = create(steps_for(plain["id"], chat={"effort": "low"}))
    assert resp.status_code == 400 and "没有思考档位" in resp.json()["detail"]
    resp = create(steps_for(9999))
    assert resp.status_code == 400 and "不存在" in resp.json()["detail"]
    resp = create({"speakers": {"model_id": model["id"]}})
    assert resp.status_code == 400 and "请给“整理逐字稿”选一个模型" in resp.json()["detail"]
    resp = create(steps_for(model["id"], chat={"params": [{"name": "stream", "type": "boolean", "value": "true"}]}))
    assert resp.status_code == 422
    resp = create(steps_for(model["id"], polish={"temperature": {"on": True, "value": 3}}))
    assert resp.status_code == 422
    assert len(presets(admin_client)) == 1, "校验失败的都没有存下"


def test_preset_crud_keeps_single_default(app, admin_client):
    [first] = presets(admin_client)
    assert first["is_default"] is True
    model = create_model(admin_client, model="gpt-6-astra")
    steps = steps_for(
        model["id"],
        minutes={"effort": "high", "max_tokens": {"on": True, "value": 30_000}},
        polish={"effort": "low", "timeout_s": 120},
    )
    resp = admin_client.post(f"{BASE}/presets", json={"name": "精细", "steps": steps, "is_default": True})
    assert resp.status_code == 201, resp.text
    fine = resp.json()
    assert fine["steps"]["minutes"]["max_tokens"] == {"on": True, "value": 30_000}
    assert fine["steps"]["polish"]["timeout_s"] == 120 and fine["steps"]["chat"]["timeout_s"] == 600
    assert [p["is_default"] for p in presets(admin_client)] == [False, True], "新的默认方案顶掉旧的"
    assert models(admin_client)[1]["used_by"] == ["精细"]

    resp = admin_client.patch(f"{BASE}/presets/{first['id']}", json={"is_default": True, "name": " 快速 "})
    assert resp.status_code == 200 and resp.json()["name"] == "快速"
    assert [(p["name"], p["is_default"]) for p in presets(admin_client)] == [("快速", True), ("精细", False)]

    # 停用默认方案：默认挪到还启用的方案上
    admin_client.patch(f"{BASE}/presets/{first['id']}", json={"enabled": False})
    assert [(p["enabled"], p["is_default"]) for p in presets(admin_client)] == [(False, False), (True, True)]
    admin_client.patch(f"{BASE}/presets/{first['id']}", json={"enabled": True})

    bad = admin_client.patch(
        f"{BASE}/presets/{fine['id']}", json={"steps": steps_for(model["id"], chat={"effort": "x"})}
    )
    assert bad.status_code == 422
    assert admin_client.patch(f"{BASE}/presets/9999", json={"name": "x"}).status_code == 404

    # 删除默认方案：剩下的启用方案成为默认
    assert admin_client.delete(f"{BASE}/presets/{fine['id']}").status_code == 204
    assert [(p["name"], p["is_default"]) for p in presets(admin_client)] == [("快速", True)]
    assert admin_client.delete(f"{BASE}/presets/{fine['id']}").status_code == 404
    assert admin_client.delete(f"{BASE}/models/{model['id']}").status_code == 204, "方案删了，模型就能删"


def test_preset_problems(app, admin_client):
    [model] = models(admin_client)
    admin_client.patch(f"{BASE}/models/{model['id']}", json={"enabled": False})
    [preset] = presets(admin_client)
    assert len(preset["problems"]) == 4 and "已停用" in preset["problems"][0]


def test_preset_step_test_uses_real_params(app, admin_client):
    calls = install_fake_llm(app, lambda m, p: FakeReply("连接正常", reasoning_tokens=7, tokens=30))
    model = create_model(admin_client, model="gpt-6-astra")
    chat = {
        "effort": "medium",
        "temperature": {"on": True, "value": 0.5},
        "top_p": {"on": False, "value": 0.9},
        "max_tokens": {"on": True, "value": 2000},
        "params": [{"name": "verbosity", "type": "string", "value": "low"}],
    }
    resp = admin_client.post(f"{BASE}/presets", json={"name": "精细", "steps": steps_for(model["id"], chat=chat)})
    preset = resp.json()
    out = admin_client.post(f"{BASE}/presets/{preset['id']}/test", json={"step": "chat"}).json()
    assert out["ok"] is True and (out["tokens"], out["reasoning_tokens"], out["finish_reason"]) == (30, 7, "stop")
    assert out["sent"] == {
        "model": "gpt-6-astra",
        "reasoning_effort": "medium",
        "temperature": 0.5,
        "max_completion_tokens": 2000,
        "verbosity": "low",
    }
    assert {k: v for k, v in calls[-1].items() if k != "messages"} == out["sent"]
    assert admin_client.post(f"{BASE}/presets/{preset['id']}/test", json={"step": "polish"}).json()["sent"] == {
        "model": "gpt-6-astra"
    }
    assert admin_client.post(f"{BASE}/presets/{preset['id']}/test", json={"step": "x"}).status_code == 422
    assert admin_client.post(f"{BASE}/presets/9999/test", json={"step": "chat"}).status_code == 404


def test_non_admin_is_forbidden(app, admin_client, client):
    add_user(app, "bob", "bob-password")
    login(client, "bob", "bob-password")  # 共用同一个客户端，从这里起是 bob
    assert client.get(f"{BASE}/models").status_code == 403
    assert client.get(f"{BASE}/presets").status_code == 403
    assert client.post(f"{BASE}/models", json={"name": "x", "model": "m"}).status_code == 403
    assert client.post(f"{BASE}/models/detect-efforts", json={"model": "m"}).status_code == 403
    assert client.delete(f"{BASE}/presets/1").status_code == 403


# ---------- 成员端：选方案 ----------


def test_meeting_options_and_create_with_preset(app, admin_client):
    add_mock_provider(admin_client)
    [default] = presets(admin_client)
    model = create_model(admin_client, model="gpt-6-astra")
    fine = admin_client.post(
        f"{BASE}/presets", json={"name": "精细", "description": "纪要用旗舰模型", "steps": steps_for(model["id"])}
    ).json()
    off = admin_client.post(f"{BASE}/presets", json={"name": "停用", "steps": steps_for(model["id"]), "enabled": False})
    assert off.status_code == 201

    options = admin_client.get("/api/meetings/options").json()
    assert options["presets"] == [
        {"id": default["id"], "name": "会议测试模型", "description": default["description"], "is_default": True},
        {"id": fine["id"], "name": "精细", "description": "纪要用旗舰模型", "is_default": False},
    ], "只列启用的方案，不暴露模型和参数"

    def create(**extra: Any):
        return admin_client.post("/api/meetings", json={"filename": "组会.wav", "size": 1000, **extra})

    resp = create(llm_preset_id=fine["id"])
    assert resp.status_code == 201, resp.text
    meeting = resp.json()["meeting"]
    assert meeting["llm_preset_id"] == fine["id"] and meeting["model_name"] == "精细" and meeting["model_id"] is None
    plain = create().json()["meeting"]
    assert plain["llm_preset_id"] == default["id"] and plain["model_name"] == "会议测试模型", "没选用默认方案"
    for bad in (9999, off.json()["id"]):
        resp = create(llm_preset_id=bad)
        assert resp.status_code == 400 and "整理方案不可用" in resp.json()["detail"]

    resp = admin_client.patch(f"/api/meetings/{plain['id']}", json={"llm_preset_id": fine["id"]})
    assert resp.status_code == 200 and resp.json()["llm_preset_id"] == fine["id"]
    with app.state.ctx.Session() as db:
        m = db.get(Meeting, plain["id"])
        assert m.llm_preset_id == fine["id"] and m.model_name == "精细"
    resp = admin_client.patch(f"/api/meetings/{plain['id']}", json={"llm_preset_id": 9999})
    assert resp.status_code == 400

    # 一个方案都没有：识别照常，整理时再提示
    for p in presets(admin_client):
        admin_client.delete(f"{BASE}/presets/{p['id']}")
    bare = create()
    assert bare.status_code == 201 and bare.json()["meeting"]["llm_preset_id"] is None
    assert admin_client.get("/api/meetings/options").json()["presets"] == []
