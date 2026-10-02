from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..db import utcnow
from ..deps import AppContext, CtxDep, DbDep, UserDep, clear_session_cookie, client_ip, set_session_cookie
from ..models import AuthSession, Invite, User
from ..schemas import LoginIn, MeOut, PasswordIn, ProfileIn, RegisterIn
from ..security import (
    SESSION_COOKIE,
    hash_password,
    hash_token,
    new_session_token,
    password_needs_rehash,
    verify_password,
)
from ..services import me_out
from ..settings_store import load_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

_DUMMY_HASH = hash_password("timing-equalizer")


def start_session(db: Session, ctx: AppContext, request: Request, response: Response, user: User) -> MeOut:
    token = new_session_token()
    now = utcnow()
    db.add(
        AuthSession(
            token_hash=hash_token(token),
            user_id=user.id,
            expires_at=now + timedelta(days=ctx.config.session_days),
            ip=client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:255],
        )
    )
    user.last_login_at = now
    db.commit()
    set_session_cookie(response, token, ctx.config)
    return me_out(db, user)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: DbDep, ctx: CtxDep) -> MeOut:
    username = body.username.strip().lower()
    keys = (f"ip:{client_ip(request)}", f"user:{username}")
    if ctx.limiter.blocked(*keys):
        raise HTTPException(429, "尝试次数过多，请 10 分钟后再试")
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        verify_password(_DUMMY_HASH, body.password)
        ctx.limiter.fail(*keys)
        raise HTTPException(401, "用户名或密码错误")
    if not verify_password(user.password_hash, body.password):
        ctx.limiter.fail(*keys)
        raise HTTPException(401, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(403, "账号已被停用，请联系管理员")
    ctx.limiter.reset(*keys)
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    return start_session(db, ctx, request, response, user)


@router.post("/register")
def register(body: RegisterIn, request: Request, response: Response, db: DbDep, ctx: CtxDep) -> MeOut:
    settings = load_settings(db)
    ip_key = f"register:{client_ip(request)}"
    if settings.registration == "closed":
        raise HTTPException(403, "当前未开放注册，请联系管理员")
    if ctx.limiter.blocked(ip_key):
        raise HTTPException(429, "尝试次数过多，请稍后再试")
    invite = None
    if settings.registration == "invite":
        code = (body.invite_code or "").strip().upper()
        invite = db.scalar(select(Invite).where(Invite.code == code)) if code else None
        now = utcnow()
        if (
            invite is None
            or invite.revoked
            or invite.used_count >= invite.max_uses
            or (invite.expires_at is not None and invite.expires_at < now)
        ):
            ctx.limiter.fail(ip_key)
            raise HTTPException(400, "邀请码无效、已过期或已用完")
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(409, "用户名已被占用")
    if invite is not None:
        claimed = db.execute(
            update(Invite)
            .where(Invite.id == invite.id, Invite.used_count < Invite.max_uses, Invite.revoked.is_(False))
            .values(used_count=Invite.used_count + 1)
        ).rowcount
        if not claimed:
            db.rollback()
            raise HTTPException(400, "邀请码已用完")
    user = User(
        username=body.username,
        display_name=body.display_name.strip() or body.username,
        password_hash=hash_password(body.password),
        role="user",
        invite_id=invite.id if invite else None,
    )
    db.add(user)
    db.flush()
    return start_session(db, ctx, request, response, user)


@router.post("/logout")
def logout(request: Request, response: Response, db: DbDep, ctx: CtxDep) -> dict[str, bool]:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        db.commit()
    clear_session_cookie(response, ctx.config)
    return {"ok": True}


@router.get("/me")
def me(user: UserDep, db: DbDep) -> MeOut:
    return me_out(db, user)


@router.patch("/me")
def update_me(body: ProfileIn, user: UserDep, db: DbDep) -> MeOut:
    user.display_name = body.display_name.strip() or user.username
    db.commit()
    return me_out(db, user)


@router.post("/password")
def change_password(body: PasswordIn, request: Request, user: UserDep, db: DbDep) -> dict[str, bool]:
    if not verify_password(user.password_hash, body.old_password):
        raise HTTPException(400, "当前密码不正确")
    user.password_hash = hash_password(body.new_password)
    current = getattr(request.state, "session_id", None)
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id != current))
    db.commit()
    return {"ok": True}
