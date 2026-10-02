from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from .. import __version__
from ..deps import CtxDep, DbDep, UserDep
from ..languages import LANGUAGES
from ..models import ModelProfile, User
from ..schemas import MetaOut, ModelPublicOut
from ..settings_store import load_settings

MAX_FILES_PER_UPLOAD = 10

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/meta")
def meta(db: DbDep, ctx: CtxDep) -> MetaOut:
    settings = load_settings(db)
    has_users = bool(db.scalar(select(func.count(User.id))))
    return MetaOut(
        site_name=settings.site_name,
        announcement=settings.announcement,
        registration=settings.registration,
        needs_setup=not has_users,
        languages=LANGUAGES,
        default_lang_in=settings.default_lang_in,
        default_lang_out=settings.default_lang_out,
        max_upload_mb=settings.max_upload_mb,
        max_pages_per_job=settings.max_pages_per_job,
        max_files=MAX_FILES_PER_UPLOAD,
        engine=ctx.config.engine,
        version=__version__,
    )


@router.get("/models")
def list_models(user: UserDep, db: DbDep) -> list[ModelPublicOut]:
    rows = db.scalars(
        select(ModelProfile).where(ModelProfile.enabled.is_(True)).order_by(ModelProfile.sort_order, ModelProfile.id)
    ).all()
    return [ModelPublicOut(id=m.id, name=m.name, description=m.description, is_default=m.is_default) for m in rows]
