from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from .config import Config
from .db import utcnow
from .events import EventBus
from .models import AuthSession, User
from .security import SESSION_COOKIE, LoginLimiter, SecretBox, hash_token
from .worker import JobManager

SESSION_REFRESH = timedelta(hours=1)
LOCAL_PROXIES = {"127.0.0.1", "::1", "localhost"}


@dataclass
class AppContext:
    config: Config
    engine: Engine
    Session: sessionmaker
    bus: EventBus
    secrets: SecretBox
    manager: JobManager
    limiter: LoginLimiter = field(default_factory=LoginLimiter)
    engine_version: str | None = None


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


CtxDep = Annotated[AppContext, Depends(get_ctx)]


def get_db(ctx: CtxDep) -> Iterator[Session]:
    with ctx.Session() as db:
        yield db


DbDep = Annotated[Session, Depends(get_db)]


def client_ip(request: Request) -> str:
    host = request.client.host if request.client else ""
    if host in LOCAL_PROXIES:
        for header in ("cf-connecting-ip", "x-real-ip"):
            value = request.headers.get(header)
            if value:
                return value.strip()[:64]
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    return host[:64]


def authenticate(db: Session, request: Request, ctx: AppContext) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "请先登录")
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    now = utcnow()
    if session is None or session.expires_at < now:
        raise HTTPException(401, "登录已过期，请重新登录")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "账号不可用，请联系管理员")
    if now - session.last_seen_at > SESSION_REFRESH:
        session.last_seen_at = now
        session.expires_at = now + timedelta(days=ctx.config.session_days)
        db.commit()
    request.state.session_id = session.id
    return user


def get_current_user(request: Request, db: DbDep, ctx: CtxDep) -> User:
    return authenticate(db, request, ctx)


UserDep = Annotated[User, Depends(get_current_user)]


def require_admin(user: UserDep) -> User:
    if not user.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return user


AdminDep = Annotated[User, Depends(require_admin)]


def current_user_id(request: Request, ctx: CtxDep) -> int:
    """给长连接（SSE）用：自己开关数据库会话，不把连接占到流结束。"""
    with ctx.Session() as db:
        return authenticate(db, request, ctx).id


def set_session_cookie(response: Response, token: str, config: Config) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=config.session_days * 86400,
        httponly=True,
        samesite="lax",
        secure=config.cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response, config: Config) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=config.cookie_secure, httponly=True, samesite="lax")
