from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile

import brotli
import pytest

from app import pdf_asset_sync as sync_mod

WASM = b"\0asm" + bytes(range(256)) * 40
JS = b"export const x = 1;\n" * 200
FONT = b"\0\1\0\0" + b"glyf" * 3000


def quiet(_msg: str) -> None:
    pass


def npm_tarball(dest, files: dict[str, bytes]) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo("package/" + name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(buf.getvalue())
    return "sha512-" + base64.b64encode(hashlib.sha512(buf.getvalue()).digest()).decode()


def meta(data: bytes, **source) -> dict:
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "gzip": 1, "br": 1, **source}


def engine(version: str, files: dict) -> dict:
    return {"label": "x", "version": version, "bytes": 0, "transfer": 0, "files": files}


@pytest.fixture
def world(tmp_path):
    registry = tmp_path / "registry"
    tgz = registry / "@x" / "engine" / "-" / "engine-1.0.0.tgz"
    integrity = npm_tarball(tgz, {"dist/e.wasm": WASM, "dist/e.js": JS})
    font_src = tmp_path / "remote" / "Font.ttf"
    font_src.parent.mkdir()
    font_src.write_bytes(FONT)
    lock = {
        "engines": {
            "qpdf": engine(
                "e1",
                {
                    "e.wasm": meta(WASM, npm="@x/engine", file="dist/e.wasm"),
                    "sub/e.js": meta(JS, npm="@x/engine", file="dist/e.js"),
                },
            ),
            "fonts": engine("f1", {"Font.ttf": meta(FONT, url=font_src.as_uri())}),
        },
        "npm": {"@x/engine": {"version": "1.0.0", "integrity": integrity}},
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    return {
        "tmp": tmp_path,
        "registry": registry.as_uri() + "/",
        "lock": lock,
        "lock_path": lock_path,
        "font": font_src,
        "dest": tmp_path / "assets",
    }


def run(world, **kw):
    kw.setdefault("registry", world["registry"])
    return sync_mod.sync(world["lock_path"], world["dest"], log=quiet, **kw)


def save(world):
    world["lock_path"].write_text(json.dumps(world["lock"]), encoding="utf-8")


def test_sync_downloads_verifies_and_precompresses(world):
    assert run(world) == ["qpdf", "fonts"]
    qpdf = world["dest"] / "qpdf" / "e1"
    assert (qpdf / "e.wasm").read_bytes() == WASM
    assert brotli.decompress((qpdf / "sub" / "e.js.br").read_bytes()) == JS
    assert (qpdf / "sub" / "e.js.gz").is_file()
    assert (world["dest"] / "fonts" / "f1" / "Font.ttf").read_bytes() == FONT
    assert not (world["dest"] / ".cache").exists()
    assert run(world) == []


def test_engine_bump_keeps_other_engines_and_prunes(world):
    run(world)
    world["font"].unlink()  # 网址失效也能从已有版本复用
    for i in range(2, 5):
        world["lock"]["engines"]["fonts"]["version"] = f"f{i}"
        save(world)
        assert run(world, registry="file:///nonexistent/") == ["fonts"]
    assert sorted(p.name for p in (world["dest"] / "fonts").iterdir()) == ["f3", "f4"]
    assert [p.name for p in (world["dest"] / "qpdf").iterdir()] == ["e1"]
    assert (world["dest"] / "fonts" / "f4" / "Font.ttf.br").is_file()


def test_seed_directory_is_used_when_hash_matches(world):
    seed = world["tmp"] / "seed"
    seed.mkdir()
    (seed / "Font.ttf").write_bytes(FONT)
    world["font"].unlink()
    run(world, seeds=[seed])
    assert (world["dest"] / "fonts" / "f1" / "Font.ttf").read_bytes() == FONT


def test_bad_integrity_leaves_nothing_behind(world):
    world["lock"]["npm"]["@x/engine"]["integrity"] = "sha512-" + base64.b64encode(b"0" * 64).decode()
    save(world)
    with pytest.raises(RuntimeError, match="完整性校验失败"):
        run(world)
    assert not list((world["dest"] / "qpdf").iterdir())


def test_tampered_download_is_rejected(world):
    world["font"].write_bytes(FONT + b"x")
    with pytest.raises(RuntimeError):
        run(world)
    assert not (world["dest"] / "fonts" / "f1").exists()


def test_build_lock_from_manifest(tmp_path):
    nm = tmp_path / "node_modules"
    (nm / "@x" / "engine" / "dist" / "sub").mkdir(parents=True)
    (nm / "@x" / "engine" / "dist" / "e.wasm").write_bytes(WASM)
    (nm / "@x" / "engine" / "dist" / "sub" / "a.bcmap").write_bytes(JS)
    font = tmp_path / "Font.ttf"
    font.write_bytes(FONT)
    manifest = {
        "engines": {
            "qpdf": {"label": "加解密", "files": [{"path": "q.wasm", "npm": "@x/engine", "file": "dist/e.wasm"}]},
            "render": {"label": "渲染", "files": [{"path": "cmaps/", "npm": "@x/engine", "dir": "dist/sub"}]},
            "fonts": {"label": "字体", "files": [{"path": "Font.ttf", "url": font.as_uri()}]},
        }
    }
    (tmp_path / "m.json").write_text(json.dumps(manifest), encoding="utf-8")
    pkg_lock = {"packages": {"node_modules/@x/engine": {"version": "1.0.0", "integrity": "sha512-abc"}}}
    (tmp_path / "pl.json").write_text(json.dumps(pkg_lock), encoding="utf-8")
    lock = sync_mod.build_lock(tmp_path / "m.json", tmp_path / "pl.json", nm, tmp_path / "cache", log=quiet)
    assert set(lock["engines"]) == {"qpdf", "render", "fonts"}
    assert list(lock["engines"]["render"]["files"]) == ["cmaps/a.bcmap"]
    q = lock["engines"]["qpdf"]
    assert q["files"]["q.wasm"]["sha256"] == hashlib.sha256(WASM).hexdigest()
    assert q["files"]["q.wasm"]["br"] > 0 and q["transfer"] < q["bytes"]
    assert lock["npm"]["@x/engine"]["version"] == "1.0.0"
    again = sync_mod.build_lock(tmp_path / "m.json", tmp_path / "pl.json", nm, tmp_path / "cache", log=quiet)
    assert again == lock


def test_zip_member_source(tmp_path):
    import zipfile

    archive = tmp_path / "release.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Font-v1/Fang.ttf", FONT)
        zf.writestr("README.md", "x")
    dest = tmp_path / "out" / "Fang.ttf"
    sync_mod.fetch_source({"url": archive.as_uri(), "member": "Font-v1/Fang.ttf"}, dest, tmp_path / "zips")
    assert dest.read_bytes() == FONT
