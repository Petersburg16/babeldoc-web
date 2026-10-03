from __future__ import annotations

import gzip

import brotli
from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import build_config

WASM = b"\0asm" + b"x" * 2000


def make_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("BDW_PDF_ASSETS_DIR", str(tmp_path / "assets"))
    config = build_config(tmp_path, monkeypatch)
    root = config.pdf_assets_dir / "qpdf" / "v1"
    root.mkdir(parents=True)
    (root / "qpdf.wasm").write_bytes(WASM)
    (root / "qpdf.wasm.gz").write_bytes(gzip.compress(WASM))
    (root / "qpdf.wasm.br").write_bytes(brotli.compress(WASM))
    (root / "qpdf.js").write_text("export default 1", encoding="utf-8")
    (root / "chi_sim.traineddata.gz").write_bytes(gzip.compress(b"lang"))
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    return TestClient(create_app(config))


def test_serves_assets_with_types_and_long_cache(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        resp = c.get("/pdf-assets/qpdf/v1/qpdf.js")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/javascript")
        assert resp.headers["cache-control"] == "public, max-age=31536000, immutable, no-transform"
        assert resp.headers["cross-origin-embedder-policy"] == "require-corp"
        assert resp.headers["cross-origin-opener-policy"] == "same-origin"
        again = c.get("/pdf-assets/qpdf/v1/qpdf.js", headers={"If-None-Match": resp.headers["etag"]})
        assert again.status_code == 304


def test_picks_precompressed_variant(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        url = "/pdf-assets/qpdf/v1/qpdf.wasm"
        br = c.get(url, headers={"Accept-Encoding": "gzip, deflate, br"})
        assert br.headers["content-encoding"] == "br"
        assert br.headers["content-type"] == "application/wasm"
        assert br.headers["vary"] == "accept-encoding"
        assert br.content == WASM  # 客户端自动解码
        gz = c.get(url, headers={"Accept-Encoding": "br;q=0, gzip"})
        assert gz.headers["content-encoding"] == "gzip"
        plain = c.get(url, headers={"Accept-Encoding": "identity"})
        assert "content-encoding" not in plain.headers
        assert int(plain.headers["content-length"]) == len(WASM)
        ranged = c.get(url, headers={"Accept-Encoding": "br", "Range": "bytes=0-3"})
        assert ranged.status_code == 206
        assert "content-encoding" not in ranged.headers


def test_literal_gz_without_twin_is_served(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        resp = c.get("/pdf-assets/qpdf/v1/chi_sim.traineddata.gz", headers={"Accept-Encoding": "identity"})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/gzip"


def test_rejects_traversal_hidden_and_sibling_paths(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        for bad in (
            "/pdf-assets/../secret.txt",
            "/pdf-assets/qpdf/../../secret.txt",
            "/pdf-assets/qpdf/v1/qpdf.wasm.gz",
            "/pdf-assets/qpdf/v1/qpdf.wasm.br",
            "/pdf-assets/qpdf/v1/",
            "/pdf-assets/qpdf/.hidden",
            "/pdf-assets/qpdf/v1/missing.wasm",
            "/pdf-assets/qpdf/v1/a%5Cb",
        ):
            resp = c.get(bad)
            assert resp.status_code == 404, bad
            assert "immutable" not in resp.headers.get("cache-control", ""), bad


def test_isolation_headers_skip_job_files(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        assert c.get("/api/meta").headers["cross-origin-embedder-policy"] == "require-corp"
        resp = c.get("/api/jobs/abc/files/dual")
        assert "cross-origin-embedder-policy" not in resp.headers
        assert "cross-origin-opener-policy" not in resp.headers
        assert resp.headers["cross-origin-resource-policy"] == "same-origin"
