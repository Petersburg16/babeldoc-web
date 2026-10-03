"""PDF 工具的运行时资源（WASM、OCR 语言包、字体）：由本站分发，不走任何第三方 CDN。

路径为 /pdf-assets/<引擎>/<版本>/<文件>，版本由该引擎所有文件的哈希决定（见 frontend/pdf-assets.lock.json），
内容不变路径就不变，所以可以永久缓存。目录由 `python -m app.cli pdf-assets sync` 按锁文件生成，
同目录下的 .br / .gz 是预压缩副本，按 Accept-Encoding 选用。
"""

from __future__ import annotations

import re
import stat
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse

# no-transform：让 Cloudflare 原样转发预压缩内容并保留 Content-Length，不再二次压缩
CACHE_IMMUTABLE = "public, max-age=31536000, immutable, no-transform"

MEDIA_TYPES = {
    ".wasm": "application/wasm",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".json": "application/json",
    ".data": "application/octet-stream",
    ".traineddata": "application/octet-stream",
    ".bin": "application/octet-stream",
    ".bcmap": "application/octet-stream",
    ".pfb": "application/octet-stream",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".ttc": "font/collection",
    ".woff2": "font/woff2",
    ".zip": "application/zip",
    ".whl": "application/zip",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
    ".icc": "application/vnd.iccprofile",
    ".xcu": "application/xml",
    ".ps": "application/postscript",
    ".txt": "text/plain; charset=utf-8",
}

ENCODINGS = (("br", ".br"), ("gzip", ".gz"))
# 每一段只允许常见文件名字符：挡掉 ..、隐藏文件、反斜杠、盘符和空字符
SEGMENT = re.compile(r"[A-Za-z0-9_@+-][A-Za-z0-9._@+-]*")


def media_type(path: Path) -> str:
    return MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def lookup(root: Path, rel: str) -> tuple[Path, object] | None:
    parts = rel.split("/")
    if not rel or not all(SEGMENT.fullmatch(p) for p in parts):
        return None
    path = root.joinpath(*parts)
    # 预压缩副本不单独对外；没有原文件的 .gz（个别库会直接请求 .gz）照常提供
    if path.suffix in {".br", ".gz"} and path.with_suffix("").is_file():
        return None
    try:
        path.resolve(strict=True).relative_to(root)
        st = path.stat()
    except (OSError, ValueError):
        return None
    return (path, st) if stat.S_ISREG(st.st_mode) else None


def accepted_encodings(header: str) -> set[str]:
    out: set[str] = set()
    for item in header.split(","):
        name, *params = (p.strip() for p in item.split(";"))
        q = 1.0
        for param in params:
            key, _, value = param.partition("=")
            if key.strip().lower() == "q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
        if name and q > 0:
            out.add(name.lower())
    return out


def mount(app: FastAPI, root: Path) -> None:
    root = root.resolve()

    @app.api_route("/pdf-assets/{rel:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def pdf_asset(rel: str, request: Request) -> Response:
        found = lookup(root, rel)
        if found is None:
            raise HTTPException(404, "资源不存在")
        path, st = found
        kind = media_type(path)
        headers = {"cache-control": CACHE_IMMUTABLE, "vary": "accept-encoding"}
        # 带 Range 的请求按原文件处理，区间总是指原始字节
        if "range" not in request.headers:
            accepted = accepted_encodings(request.headers.get("accept-encoding", ""))
            for encoding, suffix in ENCODINGS:
                packed = path.with_name(path.name + suffix)
                if encoding in accepted and packed.is_file():
                    path, st = packed, packed.stat()
                    headers["content-encoding"] = encoding
                    break
        response = FileResponse(path, media_type=kind, headers=headers, stat_result=st)  # type: ignore[arg-type]
        etags = [t.strip().removeprefix("W/") for t in request.headers.get("if-none-match", "").split(",")]
        if response.headers["etag"] in etags:
            kept = {k: response.headers[k] for k in ("etag", "cache-control", "vary")}
            return Response(status_code=304, headers=kept)
        return response
