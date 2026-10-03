"""PDF 工具运行时资源的锁定与同步（本机开发和服务器部署共用）。

- lock（开发机）：读 frontend/pdf-assets.json（手写清单）、frontend/package-lock.json 和 node_modules，
  下载清单里的网址资源算出哈希和压缩后大小，写出 frontend/pdf-assets.lock.json。锁文件入库，是资源的唯一事实源。
- sync（开发机/服务器）：按锁文件从 npm 源和固定网址下载、逐个校验 sha256，生成 .br/.gz 预压缩副本，
  每个引擎原子地落到 <目标目录>/<引擎>/<版本>/。版本只由该引擎自己的文件决定，升级一个引擎不影响其他引擎的缓存。
  已有版本里哈希相同的文件、--seed 目录里同名同哈希的文件直接硬链接复用（例如 BabelDOC 已下载的字体）。
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

import brotli

NPM_REGISTRY = "https://registry.npmjs.org/"
# 这些格式本身已压缩，再压缩收益很小
# wheel 也是 zip，但 numpy 之类里面有大量未压缩数据，是否值得压缩交给实测比例判断
NO_COMPRESS = {".gz", ".br", ".png", ".jpg", ".jpeg", ".webp", ".woff2", ".7z"}
LOCK_NAME = ".lock.json"
# 9 级 brotli 比 11 级只大一成多，速度快约 20 倍（soffice.data 7.5 s 对 152 s），服务器上也能现做
BROTLI = {"quality": 9, "lgwin": 22}
CHUNK = 1 << 20

Log = Callable[[str], None]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    for chunk in _chunks(path):
        h.update(chunk)
    return h.hexdigest()


def _chunks(path: Path) -> Iterator[bytes]:
    with path.open("rb") as f:
        yield from iter(lambda: f.read(CHUNK), b"")


def write_gzip(src: Path, dest: Path) -> int:
    with src.open("rb") as f, gzip.GzipFile(dest, "wb", compresslevel=9, mtime=0) as out:
        shutil.copyfileobj(f, out, CHUNK)
    return dest.stat().st_size


def write_brotli(src: Path, dest: Path) -> int:
    compressor = brotli.Compressor(**BROTLI)
    with dest.open("wb") as out:
        for chunk in _chunks(src):
            out.write(compressor.process(chunk))
        out.write(compressor.finish())
    return dest.stat().st_size


def compressible(path: Path, raw: int) -> bool:
    return raw >= 1024 and path.suffix.lower() not in NO_COMPRESS


def fetch(url: str, dest: Path, attempts: int = 3) -> None:
    """下载到 dest（先写临时文件再改名），走环境变量里的 HTTP(S) 代理。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for i in range(attempts):
        tmp = dest.with_name(dest.name + ".part")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "babeldoc-web-asset-sync"})
            with urllib.request.urlopen(req, timeout=60) as resp, tmp.open("wb") as out:
                shutil.copyfileobj(resp, out, CHUNK)
            os.replace(tmp, dest)
            return
        except Exception as exc:  # 网络错误重试
            last = exc
            tmp.unlink(missing_ok=True)
            time.sleep(1 + i * 2)
    raise RuntimeError(f"下载失败：{url}：{last}")


def tarball_url(name: str, version: str, registry: str = NPM_REGISTRY) -> str:
    return f"{registry.rstrip('/')}/{name}/-/{name.split('/')[-1]}-{version}.tgz"


def fetch_source(source: dict, dest: Path, zip_cache: Path) -> None:
    """按网址下载；带 member 时网址是 zip 包，下载后只解出该文件。"""
    if "member" not in source:
        fetch(source["url"], dest)
        return
    archive = zip_cache / (hashlib.sha256(source["url"].encode()).hexdigest()[:24] + ".zip")
    if not archive.is_file():
        fetch(source["url"], archive)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf, zf.open(source["member"]) as src, dest.open("wb") as out:
        shutil.copyfileobj(src, out, CHUNK)


# ---------------------------------------------------------------- lock


def _expand(item: dict, node_modules: Path) -> Iterator[tuple[str, dict, Path | None]]:
    """把清单里的一项展开成 (引擎内路径, 来源描述, 本地文件)；网址来源的本地文件稍后下载。"""
    if "npm" in item:
        pkg_dir = node_modules / item["npm"]
        if "dir" in item:
            src_dir = pkg_dir / item["dir"]
            if not src_dir.is_dir():
                raise SystemExit(f"{item['npm']} 里没有目录 {item['dir']}")
            include = item.get("include")
            for f in sorted(p for p in src_dir.rglob("*") if p.is_file()):
                rel = f.relative_to(src_dir).as_posix()
                if include and not any(rel.endswith(s) for s in include):
                    continue
                source = {"npm": item["npm"], "file": f"{item['dir'].rstrip('/')}/{rel}"}
                yield f"{item['path'].rstrip('/')}/{rel}".lstrip("/"), source, f
        else:
            f = pkg_dir / item["file"]
            if not f.is_file():
                raise SystemExit(f"{item['npm']} 里没有文件 {item['file']}")
            yield item["path"], {"npm": item["npm"], "file": item["file"]}, f
    elif "url" in item:
        source = {"url": item["url"]}
        if "member" in item:  # 发布包是 zip，只取其中一个文件
            source["member"] = item["member"]
        yield item["path"], source, None
    else:
        raise SystemExit(f"清单项缺少 npm 或 url：{item}")


def _measure(local: Path, cache: dict, scratch: Path) -> dict:
    """算 sha256 和预压缩后的大小；按哈希缓存，未变化的大文件不用重新压缩。"""
    digest = sha256_file(local)
    if digest not in cache:
        raw = local.stat().st_size
        sizes = {"gzip": 0, "br": 0}
        if compressible(local, raw):
            sizes["gzip"] = write_gzip(local, scratch / "x.gz")
            sizes["br"] = write_brotli(local, scratch / "x.br")
            if sizes["br"] >= raw * 0.95:
                sizes = {"gzip": 0, "br": 0}
        cache[digest] = {"bytes": raw, **sizes}
    return {"sha256": digest, **cache[digest]}


def build_lock(
    manifest_path: Path, package_lock_path: Path, node_modules: Path, cache_dir: Path, log: Log = print
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    package_lock = json.loads(package_lock_path.read_text(encoding="utf-8"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    sizes_file = cache_dir / "sizes.json"
    sizes = json.loads(sizes_file.read_text(encoding="utf-8")) if sizes_file.is_file() else {}
    npm: dict[str, dict] = {}
    engines: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(dir=cache_dir) as scratch:
        for engine_id, engine in manifest["engines"].items():
            files: dict[str, dict] = {}
            for item in engine["files"]:
                for path, source, local in _expand(item, node_modules):
                    if local is None:
                        key = hashlib.sha256(source["url"].encode()).hexdigest()[:24]
                        local = cache_dir / "urls" / key / Path(path).name
                        if not local.is_file():
                            log(f"下载 {source['url']}")
                            fetch_source(source, local, cache_dir / "zips")
                    else:
                        name = source["npm"]
                        entry = package_lock.get("packages", {}).get(f"node_modules/{name}")
                        if not entry or "version" not in entry:
                            raise SystemExit(f"package-lock.json 里没有 {name}，先 npm install")
                        npm[name] = {"version": entry["version"], "integrity": entry["integrity"]}
                    if path in files:
                        raise SystemExit(f"引擎 {engine_id} 里路径重复：{path}")
                    files[path] = {**_measure(local, sizes, Path(scratch)), **source}
                    if item.get("lazy"):
                        files[path]["lazy"] = True
            digest = hashlib.sha256(json.dumps({p: f["sha256"] for p, f in sorted(files.items())}).encode())
            wire = [f["br"] or f["gzip"] or f["bytes"] for f in files.values() if not f.get("lazy")]
            # 按需零散加载的文件（例如按浏览器能力三选一的 OCR 核心）只按最大的一个估算首次下载量
            lazy = [f["br"] or f["gzip"] or f["bytes"] for f in files.values() if f.get("lazy")]
            engines[engine_id] = {
                "label": engine["label"],
                "version": digest.hexdigest()[:12],
                "bytes": sum(f["bytes"] for f in files.values()),
                "transfer": sum(wire) + max(lazy, default=0),
                "files": dict(sorted(files.items())),
            }
            if engine.get("lazy"):
                engines[engine_id]["lazy"] = True
    sizes_file.write_text(json.dumps(sizes), encoding="utf-8")
    return {
        "$comment": "由 python -m app.cli pdf-assets lock 生成，不要手改；清单见 pdf-assets.json",
        "engines": engines,
        "npm": dict(sorted(npm.items())),
    }


# ---------------------------------------------------------------- sync


def _verify_integrity(path: Path, integrity: str) -> None:
    algo, _, expected = integrity.partition("-")
    h = hashlib.new(algo)
    for chunk in _chunks(path):
        h.update(chunk)
    if base64.b64encode(h.digest()).decode() != expected:
        raise RuntimeError(f"{path.name} 完整性校验失败")


def _link_or_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def _installed(root: Path) -> dict[str, Path]:
    """所有已落盘引擎版本里按 sha256 索引的文件，用来硬链接复用。"""
    known: dict[str, Path] = {}
    for lock_file in root.glob(f"*/*/{LOCK_NAME}"):
        try:
            files = json.loads(lock_file.read_text(encoding="utf-8")).get("files", {})
        except (OSError, ValueError):
            continue
        for path, meta in files.items():
            candidate = lock_file.parent / path
            if candidate.is_file() and candidate.stat().st_size == meta["bytes"]:
                known.setdefault(meta["sha256"], candidate)
    return known


def _seed_index(seeds: Iterable[Path]) -> dict[str, list[Path]]:
    by_name: dict[str, list[Path]] = {}
    for seed in seeds:
        if seed.is_dir():
            for f in seed.iterdir():
                if f.is_file():
                    by_name.setdefault(f.name, []).append(f)
    return by_name


class _Syncer:
    def __init__(self, lock: dict, root: Path, seeds: Iterable[Path], registry: str, log: Log):
        self.lock = lock
        self.root = root
        self.registry = registry
        self.log = log
        self.known = _installed(root)
        self.seeds = _seed_index(seeds)
        self.cache = root / ".cache"

    def engine(self, engine_id: str, spec: dict) -> bool:
        target = self.root / engine_id / spec["version"]
        if (target / LOCK_NAME).is_file():
            return False
        if target.exists():  # 上次中断留下的半成品
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{spec['version']}-", dir=target.parent))
        try:
            self._fill(staging, spec["files"])
            (staging / LOCK_NAME).write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
            for d in [staging, *(p for p in staging.rglob("*") if p.is_dir())]:
                d.chmod(0o755)
            os.replace(staging, target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        for path, meta in spec["files"].items():
            self.known.setdefault(meta["sha256"], target / path)
        self.log(f"  {engine_id} {spec['version']}：{len(spec['files'])} 个文件")
        return True

    def _fill(self, staging: Path, files: dict[str, dict]) -> None:
        by_package: dict[str, list[tuple[str, dict]]] = {}
        for path, meta in files.items():
            dest = staging / path
            src = self._reusable(path, meta)
            if src is not None:
                _link_or_copy(src, dest)
                for suffix in (".br", ".gz"):
                    packed = src.with_name(src.name + suffix)
                    if packed.is_file():
                        _link_or_copy(packed, dest.with_name(dest.name + suffix))
            elif "npm" in meta:
                by_package.setdefault(meta["npm"], []).append((path, meta))
            else:
                self.log(f"下载 {meta['url']}")
                fetch_source(meta, dest, self.cache / "zips")
        for name, items in by_package.items():
            self._extract(name, items, staging)
        for path, meta in files.items():
            dest = staging / path
            if dest.stat().st_size != meta["bytes"] or sha256_file(dest) != meta["sha256"]:
                raise RuntimeError(f"校验失败：{path}")
            if meta.get("br") and not dest.with_name(dest.name + ".br").is_file():
                write_brotli(dest, dest.with_name(dest.name + ".br"))
            if meta.get("gzip") and not dest.with_name(dest.name + ".gz").is_file():
                write_gzip(dest, dest.with_name(dest.name + ".gz"))

    def _reusable(self, path: str, meta: dict) -> Path | None:
        src = self.known.get(meta["sha256"])
        if src is not None and src.is_file():
            return src
        for cand in self.seeds.get(Path(path).name, []):
            if cand.stat().st_size == meta["bytes"] and sha256_file(cand) == meta["sha256"]:
                return cand
        return None

    def _extract(self, name: str, items: list[tuple[str, dict]], staging: Path) -> None:
        info = self.lock["npm"][name]
        tgz = self.cache / "npm" / f"{name.replace('/', '__')}-{info['version']}.tgz"
        if not tgz.is_file():
            self.log(f"下载 {name}@{info['version']}")
            fetch(tarball_url(name, info["version"], self.registry), tgz)
        _verify_integrity(tgz, info["integrity"])
        wanted: dict[str, list[str]] = {}
        for path, meta in items:
            wanted.setdefault("package/" + meta["file"], []).append(path)
        with tarfile.open(tgz, "r:gz") as tar:
            for member in tar:
                paths = wanted.pop(member.name, None)
                if not paths or not member.isfile():
                    continue
                src = tar.extractfile(member)
                assert src is not None
                first = staging / paths[0]
                first.parent.mkdir(parents=True, exist_ok=True)
                with src, first.open("wb") as out:
                    shutil.copyfileobj(src, out, CHUNK)
                for extra in paths[1:]:
                    _link_or_copy(first, staging / extra)
        if wanted:
            raise RuntimeError(f"{name}@{info['version']} 里缺少：{', '.join(sorted(wanted))}")


def sync(
    lock_path: Path,
    dest_root: Path,
    seeds: Iterable[Path] = (),
    registry: str | None = None,
    keep: int = 2,
    log: Log = print,
) -> list[str]:
    """返回新落盘的引擎；每个引擎只保留最近 keep 个版本（当前版本总会保留）。"""
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    dest_root.mkdir(parents=True, exist_ok=True)
    registry = registry or os.environ.get("BDW_NPM_REGISTRY") or NPM_REGISTRY
    syncer = _Syncer(lock, dest_root.resolve(), seeds, registry, log)
    created = [eid for eid, spec in lock["engines"].items() if syncer.engine(eid, spec)]
    # npm 包只在生成新版本时用到，留着只占磁盘
    shutil.rmtree(syncer.cache, ignore_errors=True)
    for engine_id, spec in lock["engines"].items():
        _prune(dest_root / engine_id, keep, spec["version"])
    log(f"PDF 工具资源已就绪：{len(lock['engines'])} 个引擎，新增 {len(created)} 个")
    return created


def _prune(engine_dir: Path, keep: int, current: str) -> None:
    if not engine_dir.is_dir():
        return
    versions = sorted(
        (d for d in engine_dir.iterdir() if d.is_dir() and (d / LOCK_NAME).is_file()),
        key=lambda d: (d.name == current, (d / LOCK_NAME).stat().st_mtime),
        reverse=True,
    )
    for old in versions[max(keep, 1) :]:
        shutil.rmtree(old, ignore_errors=True)
    for tmp in engine_dir.glob(".*-*"):
        if tmp.is_dir() and time.time() - tmp.stat().st_mtime > 3600:
            shutil.rmtree(tmp, ignore_errors=True)
