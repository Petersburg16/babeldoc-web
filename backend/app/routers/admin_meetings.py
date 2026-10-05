"""管理后台：语音识别服务、术语表。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..deps import AdminDep, AppContext, CtxDep, DbDep, require_admin
from ..meeting.asr import adapter_class, available_kinds
from ..meeting.asr.base import AsrAdapter
from ..meeting.schemas import (
    FieldSpecOut,
    GlossaryTermIn,
    GlossaryTermOut,
    ProviderAdminOut,
    ProviderCheckOut,
    ProviderIn,
    ProviderKindOut,
    ProviderPatch,
    load_secrets,
)
from ..models import AsrProvider, GlossaryTerm, Meeting

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _kinds(ctx: AppContext) -> dict[str, type[AsrAdapter]]:
    return {cls.kind: cls for cls in available_kinds(ctx.config.engine == "mock")}


def _get(db: Session, provider_id: int) -> AsrProvider:
    p = db.get(AsrProvider, provider_id)
    if p is None:
        raise HTTPException(404, "识别服务不存在")
    return p


def _fix_default(db: Session, prefer: AsrProvider | None = None) -> None:
    if prefer is not None and prefer.is_default:
        db.execute(update(AsrProvider).where(AsrProvider.id != prefer.id).values(is_default=False))
    if not db.scalar(select(AsrProvider.id).where(AsrProvider.is_default.is_(True), AsrProvider.enabled.is_(True))):
        first = db.scalar(
            select(AsrProvider).where(AsrProvider.enabled.is_(True)).order_by(AsrProvider.sort_order, AsrProvider.id)
        )
        db.execute(update(AsrProvider).values(is_default=False))
        if first is not None:
            first.is_default = True


def _split(cls: type[AsrAdapter], config: dict[str, str], secrets: dict[str, str]) -> tuple[dict, dict]:
    """只保留这类服务认识的字段，去掉首尾空白。"""
    keep_config = {f.key: config.get(f.key, "").strip() for f in cls.fields if not f.secret and f.key in config}
    keep_secrets = {
        f.key: secrets.get(f.key, "").strip() for f in cls.fields if f.secret and secrets.get(f.key, "").strip()
    }
    return keep_config, keep_secrets


def _check_required(cls: type[AsrAdapter], config: dict[str, str], secrets: dict[str, str]) -> None:
    missing = [
        f.label
        for f in cls.fields
        if f.required and not (secrets.get(f.key) if f.secret else (config.get(f.key) or f.default))
    ]
    if missing:
        raise HTTPException(400, "请填写：" + "、".join(missing))


@router.get("/asr/kinds")
def list_kinds(ctx: CtxDep) -> list[ProviderKindOut]:
    return [
        ProviderKindOut(
            kind=cls.kind,
            label=cls.label,
            description=cls.description,
            max_part_seconds=cls.capability.max_part_seconds,
            hotwords=cls.capability.hotwords,
            speaker_count=cls.capability.speaker_count,
            fields=[
                FieldSpecOut(
                    key=f.key,
                    label=f.label,
                    secret=f.secret,
                    required=f.required,
                    default=f.default,
                    placeholder=f.placeholder,
                    hint=f.hint,
                    options=[list(o) for o in f.options],
                )
                for f in cls.fields
            ],
        )
        for cls in _kinds(ctx).values()
    ]


@router.get("/asr/providers")
def list_providers(db: DbDep, ctx: CtxDep) -> list[ProviderAdminOut]:
    rows = db.scalars(select(AsrProvider).order_by(AsrProvider.sort_order, AsrProvider.id)).all()
    return [ProviderAdminOut.of(p, ctx.secrets) for p in rows]


@router.post("/asr/providers", status_code=201)
def create_provider(body: ProviderIn, db: DbDep, ctx: CtxDep) -> ProviderAdminOut:
    cls = _kinds(ctx).get(body.kind)
    if cls is None:
        raise HTTPException(400, "不支持的识别服务类型")
    config, secrets = _split(cls, body.config, body.secrets)
    _check_required(cls, config, secrets)
    p = AsrProvider(
        kind=body.kind,
        name=body.name.strip(),
        description=body.description.strip(),
        config=config,
        secret_enc=ctx.secrets.encrypt(json.dumps(secrets)) if secrets else "",
        enabled=body.enabled,
        is_default=body.is_default,
        sort_order=body.sort_order,
    )
    db.add(p)
    db.flush()
    _fix_default(db, p)
    db.commit()
    db.refresh(p)
    return ProviderAdminOut.of(p, ctx.secrets)


@router.patch("/asr/providers/{provider_id}")
def patch_provider(provider_id: int, body: ProviderPatch, db: DbDep, ctx: CtxDep) -> ProviderAdminOut:
    p = _get(db, provider_id)
    cls = adapter_class(p.kind)
    if cls is None:
        raise HTTPException(400, "不支持的识别服务类型")
    if body.name is not None:
        p.name = body.name.strip()
    if body.description is not None:
        p.description = body.description.strip()
    for name in ("enabled", "is_default", "sort_order"):
        value = getattr(body, name)
        if value is not None:
            setattr(p, name, value)
    config = dict(p.config or {})
    secrets = load_secrets(p, ctx.secrets)
    if body.config is not None:
        new_config, _ = _split(cls, body.config, {})
        config.update(new_config)
    if body.secrets is not None:
        _, new_secrets = _split(cls, {}, body.secrets)
        secrets.update(new_secrets)  # 留空的字段保持原值
    for key in body.clear_secrets:
        secrets.pop(key, None)
    _check_required(cls, config, secrets)
    p.config = config
    p.secret_enc = ctx.secrets.encrypt(json.dumps(secrets)) if secrets else ""
    _fix_default(db, p)
    db.commit()
    db.refresh(p)
    return ProviderAdminOut.of(p, ctx.secrets)


@router.delete("/asr/providers/{provider_id}", status_code=204)
def delete_provider(provider_id: int, db: DbDep) -> None:
    p = _get(db, provider_id)
    busy = db.scalar(
        select(Meeting.id).where(
            Meeting.provider_id == p.id,
            Meeting.deleted_at.is_(None),
            Meeting.status.in_(("uploading", "queued", "transcoding", "transcribing")),
        )
    )
    if busy:
        raise HTTPException(409, "还有会议正在使用这个识别服务，等它们识别完再删除")
    db.delete(p)
    db.flush()
    _fix_default(db)
    db.commit()


@router.post("/asr/providers/{provider_id}/check")
async def check_provider(provider_id: int, ctx: CtxDep) -> ProviderCheckOut:
    """只检查密钥（不花钱、几秒内返回）；完整测试见 /test。"""
    with ctx.Session() as db:
        p = _get(db, provider_id)
        adapter = ctx.meetings.adapter_for(p)
    try:
        result = await adapter.check_credentials()
    except NotImplementedError:
        return ProviderCheckOut(ok=False, message="这家服务的对接还没实现")
    except Exception as e:
        return ProviderCheckOut(ok=False, message=f"{e.__class__.__name__}: {e}"[:300])
    return ProviderCheckOut(ok=result.ok, message=result.message)


@router.post("/asr/providers/{provider_id}/test", status_code=202)
def test_provider(provider_id: int, admin: AdminDep, db: DbDep, ctx: CtxDep) -> dict[str, bool]:
    """完整测试：生成 3 秒测试音频，通过站点公网地址让服务商来拉取并识别。结果通过事件流推送（asr_test）。"""
    _get(db, provider_id)
    if not ctx.meetings.request_provider_test(provider_id, admin.id):
        raise HTTPException(409, "这个识别服务正在测试中")
    return {"started": True}


@router.get("/glossary")
def list_glossary(db: DbDep) -> list[GlossaryTermOut]:
    rows = db.scalars(select(GlossaryTerm).order_by(GlossaryTerm.term)).all()
    return [GlossaryTermOut.of(t) for t in rows]


@router.post("/glossary", status_code=201)
def create_term(body: GlossaryTermIn, admin: AdminDep, db: DbDep) -> GlossaryTermOut:
    t = GlossaryTerm(term=body.term, wrong_forms=body.wrong_forms, note=body.note.strip(), updated_by=admin.id)
    db.add(t)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, f"术语“{body.term}”已经在表里了") from e
    db.refresh(t)
    return GlossaryTermOut.of(t)


@router.patch("/glossary/{term_id}")
def patch_term(term_id: int, body: GlossaryTermIn, admin: AdminDep, db: DbDep) -> GlossaryTermOut:
    t = db.get(GlossaryTerm, term_id)
    if t is None:
        raise HTTPException(404, "术语不存在")
    t.term, t.wrong_forms, t.note, t.updated_by = body.term, body.wrong_forms, body.note.strip(), admin.id
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(409, f"术语“{body.term}”已经在表里了") from e
    db.refresh(t)
    return GlossaryTermOut.of(t)


@router.delete("/glossary/{term_id}", status_code=204)
def delete_term(term_id: int, db: DbDep) -> None:
    t = db.get(GlossaryTerm, term_id)
    if t is None:
        raise HTTPException(404, "术语不存在")
    db.delete(t)
    db.commit()
