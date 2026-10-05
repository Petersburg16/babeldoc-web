from __future__ import annotations

import json

from app.models import AsrProvider
from tests.conftest import add_mock_provider


def test_provider_secrets_are_masked(app, admin_client):
    kinds = {k["kind"]: k for k in admin_client.get("/api/admin/asr/kinds").json()}
    assert {"aliyun_funasr", "aliyun_tingwu", "tencent_meeting", "mock"} <= set(kinds)
    assert any(f["secret"] for f in kinds["tencent_meeting"]["fields"])

    resp = admin_client.post(
        "/api/admin/asr/providers",
        json={
            "kind": "tencent_meeting",
            "name": "腾讯",
            "config": {"region": "ap-guangzhou", "engine": "16k_zh_en_meeting", "bogus": "x"},
            "secrets": {"secret_id": "AKIDabcdef123456", "secret_key": "verysecretkey-9876"},
        },
    )
    assert resp.status_code == 201, resp.text
    out = resp.json()
    text = json.dumps(out, ensure_ascii=False)
    assert "verysecretkey-9876" not in text and "AKIDabcdef123456" not in text
    assert out["secrets_set"] == {"secret_id": True, "secret_key": True}
    assert out["config"] == {"region": "ap-guangzhou", "engine": "16k_zh_en_meeting"}
    assert out["is_default"] is True
    with app.state.ctx.Session() as db:
        stored = db.get(AsrProvider, out["id"]).secret_enc
    assert "verysecretkey" not in stored

    # 留空的密钥保持不变，单独清空某个字段
    patched = admin_client.patch(
        f"/api/admin/asr/providers/{out['id']}", json={"name": "腾讯会议", "secrets": {"secret_id": ""}}
    ).json()
    assert patched["name"] == "腾讯会议" and patched["secrets_set"]["secret_id"] is True
    resp = admin_client.patch(f"/api/admin/asr/providers/{out['id']}", json={"clear_secrets": ["secret_key"]})
    assert resp.status_code == 400 and "SecretKey" in resp.json()["detail"]

    listed = admin_client.get("/api/admin/asr/providers").json()
    assert "verysecretkey" not in json.dumps(listed)


def test_provider_validation_and_default(admin_client):
    missing = admin_client.post(
        "/api/admin/asr/providers", json={"kind": "aliyun_funasr", "name": "百炼", "secrets": {}}
    )
    assert missing.status_code == 400 and "API Key" in missing.json()["detail"]
    assert admin_client.post("/api/admin/asr/providers", json={"kind": "nope", "name": "x"}).status_code == 400
    first = add_mock_provider(admin_client)
    second = admin_client.post(
        "/api/admin/asr/providers",
        json={"kind": "mock", "name": "第二个", "is_default": True, "secrets": {"api_key": "k"}},
    ).json()
    providers = {p["id"]: p for p in admin_client.get("/api/admin/asr/providers").json()}
    assert providers[second["id"]]["is_default"] and not providers[first]["is_default"]
    assert admin_client.delete(f"/api/admin/asr/providers/{second['id']}").status_code == 204
    assert admin_client.get("/api/admin/asr/providers").json()[0]["is_default"] is True


def test_provider_check(admin_client):
    provider_id = add_mock_provider(admin_client)
    assert admin_client.post(f"/api/admin/asr/providers/{provider_id}/check").json()["ok"] is True
    admin_client.patch(f"/api/admin/asr/providers/{provider_id}", json={"secrets": {"api_key": "bad"}})
    assert admin_client.post(f"/api/admin/asr/providers/{provider_id}/check").json()["ok"] is False


def test_glossary_crud(admin_client, app, client):
    body = {"term": "激光雷达", "wrong_forms": ["激光leader", " ", "激光leader"]}
    resp = admin_client.post("/api/admin/glossary", json=body)
    assert resp.status_code == 201, resp.text
    term = resp.json()
    assert term["wrong_forms"] == ["激光leader"]
    assert admin_client.post("/api/admin/glossary", json={"term": "激光雷达"}).status_code == 409
    patched = admin_client.patch(f"/api/admin/glossary/{term['id']}", json={"term": "相干测风激光雷达", "note": "设备"})
    assert patched.json()["term"] == "相干测风激光雷达"
    assert [t["term"] for t in admin_client.get("/api/admin/glossary").json()] == ["相干测风激光雷达"]
    assert app.state.ctx.meetings.hotwords() == ["相干测风激光雷达"]
    assert admin_client.delete(f"/api/admin/glossary/{term['id']}").status_code == 204


def test_public_base_url_setting(admin_client):
    settings = admin_client.get("/api/admin/settings").json()
    bad = admin_client.put("/api/admin/settings", json={**settings, "public_base_url": "example.com/path"})
    assert bad.status_code == 422
    ok = admin_client.put("/api/admin/settings", json={**settings, "public_base_url": "https://example.com/"})
    assert ok.status_code == 200 and ok.json()["public_base_url"] == "https://example.com"
