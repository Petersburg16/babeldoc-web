"""启动：uvicorn app.main:create_app --factory（开发加 --reload，生产见 deploy/）。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psutil
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__
from .config import Config, load_config
from .db import init_db, make_engine, make_sessionmaker
from .deps import AppContext
from .events import EventBus
from .routers import admin, auth, jobs, meta, stream
from .security import SecretBox
from .worker import JobManager

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"x-frame-options", b"SAMEORIGIN"),
]


class GuardMiddleware:
    """写操作必须带 X-Requested-With（配合 SameSite=Lax 防 CSRF），并给所有响应补安全头。"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        needs_header = scope["method"] not in SAFE_METHODS and scope["path"].startswith("/api/")
        if needs_header and not any(name == b"x-requested-with" for name, _ in scope["headers"]):
            await JSONResponse({"detail": "请求缺少 X-Requested-With 头"}, status_code=403)(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {name.lower() for name, _ in message.get("headers", [])}
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], *(h for h in SECURITY_HEADERS if h[0] not in existing)]
            await send(message)

        await self.app(scope, receive, send_with_headers)


def build_context(config: Config) -> AppContext:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.jobs_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(config.db_path)
    init_db(engine)
    session_factory = make_sessionmaker(engine)
    bus = EventBus()
    secrets = SecretBox(config.data_dir / "secret.key")
    manager = JobManager(config, session_factory, bus, secrets)
    return AppContext(config=config, engine=engine, Session=session_factory, bus=bus, secrets=secrets, manager=manager)


def create_app(config: Config | None = None) -> FastAPI:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = config or load_config()
    ctx = build_context(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ctx.bus.bind_loop(asyncio.get_running_loop())
        psutil.cpu_percent(interval=None)
        await ctx.manager.start()
        logging.getLogger("bdw").info(
            "BabelDOC Web %s started: engine=%s data=%s", __version__, config.engine, config.data_dir
        )
        try:
            yield
        finally:
            await ctx.manager.stop()
            ctx.engine.dispose()

    app = FastAPI(
        title="BabelDOC Web",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if config.dev else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if config.dev else None,
    )
    app.state.ctx = ctx
    app.add_middleware(GuardMiddleware)
    for module in (auth, meta, jobs, stream, admin):
        app.include_router(module.router)

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def api_not_found(path: str) -> None:
        raise HTTPException(404, "接口不存在")

    if (config.frontend_dir / "index.html").is_file():
        app.frontend("/", directory=config.frontend_dir, fallback="index.html", check_dir=False)
    return app
