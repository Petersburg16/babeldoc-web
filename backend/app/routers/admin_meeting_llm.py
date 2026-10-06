"""管理后台：会议记录用的大模型（连接、思考档位）与整理方案（4 个用途各用什么模型和参数）。

和翻译模型（/api/admin/models）完全分开。拉模型列表、检测档位、测试都走会议管理器的 HTTP 客户端，
测试时可换成假的大模型。
"""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..defaults import ensure_single_default
from ..deps import AppContext, CtxDep, DbDep, require_admin
from ..llm import LlmClient, LlmConfig, LlmError
from ..meeting.llm_config import (
    STEP_LABELS,
    STEPS,
    PresetSteps,
    StepConfig,
    build_config,
    builtin_efforts,
    check_steps,
    default_step,
    effective_effort,
    load_steps,
    normalize_levels,
    parse_valid_levels,
    step_model,
)
from ..meeting.schemas import (
    EffortDetectOut,
    LlmModelAdminOut,
    LlmModelIn,
    LlmModelPatch,
    LlmProbeIn,
    LlmTestIn,
    LlmTestOut,
    PresetAdminOut,
    PresetIn,
    PresetPatch,
    PresetTestIn,
)
from ..models import MeetingLlmModel, MeetingLlmPreset, ModelProfile
from ..openai_compat import auth_headers, endpoint, failure_text, fetch_models

router = APIRouter(prefix="/api/admin/meeting-llm", tags=["admin"], dependencies=[Depends(require_admin)])

TEST_MESSAGES = [{"role": "user", "content": "请只回复“连接正常”四个字。"}]
TEST_TIMEOUT = 120.0


# ---------- 会议模型 ----------


def _get_model(db: Session, model_id: int) -> MeetingLlmModel:
    m = db.get(MeetingLlmModel, model_id)
    if m is None:
        raise HTTPException(404, "会议模型不存在")
    return m


def _presets_using(db: Session) -> dict[int, list[str]]:
    used: dict[int, list[str]] = {}
    for preset in db.scalars(select(MeetingLlmPreset).order_by(MeetingLlmPreset.sort_order, MeetingLlmPreset.id)):
        steps = load_steps(preset.steps)
        for model_id in {getattr(steps, s).model_id for s in STEPS} - {None}:
            used.setdefault(model_id, []).append(preset.name)  # type: ignore[arg-type]
    return used


def _model_out(ctx: AppContext, m: MeetingLlmModel, used: dict[int, list[str]]) -> LlmModelAdminOut:
    return LlmModelAdminOut.of(m, ctx.secrets.decrypt(m.api_key_enc), used.get(m.id, []))


def _translation_model(db: Session, model_id: int) -> ModelProfile:
    source = db.get(ModelProfile, model_id)
    if source is None:
        raise HTTPException(400, "要复制的翻译模型不存在")
    return source


@router.get("/models")
def list_models(db: DbDep, ctx: CtxDep) -> list[LlmModelAdminOut]:
    used = _presets_using(db)
    rows = db.scalars(select(MeetingLlmModel).order_by(MeetingLlmModel.sort_order, MeetingLlmModel.id)).all()
    return [_model_out(ctx, m, used) for m in rows]


@router.post("/models", status_code=201)
def create_model(body: LlmModelIn, db: DbDep, ctx: CtxDep) -> LlmModelAdminOut:
    base_url, key_enc = body.base_url, ctx.secrets.encrypt(body.api_key.strip())
    if body.copy_from_model_id is not None:
        source = _translation_model(db, body.copy_from_model_id)
        base_url = base_url or source.base_url
        if not body.api_key.strip():
            key_enc = source.api_key_enc  # 同一把钥匙加密，直接复制密文
    levels = body.effort_levels if body.effort_levels is not None else builtin_efforts(body.model)
    m = MeetingLlmModel(
        name=body.name.strip(),
        description=body.description.strip(),
        base_url=base_url,
        api_key_enc=key_enc,
        model=body.model.strip(),
        effort_levels=levels,
        qps=body.qps,
        json_mode=body.json_mode,
        context_chars=body.context_chars,
        enabled=body.enabled,
        sort_order=body.sort_order,
    )
    db.add(m)
    db.flush()
    if body.create_preset:
        has_preset = db.scalar(select(MeetingLlmPreset.id).limit(1)) is not None
        steps = PresetSteps(**{s: default_step(s).model_copy(update={"model_id": m.id}) for s in STEPS})
        db.add(
            MeetingLlmPreset(
                name=m.name[:64],
                steps=steps.model_dump(mode="json"),
                is_default=not has_preset,
            )
        )
    db.commit()
    db.refresh(m)
    return _model_out(ctx, m, _presets_using(db))


@router.patch("/models/{model_id}")
def patch_model(model_id: int, body: LlmModelPatch, db: DbDep, ctx: CtxDep) -> LlmModelAdminOut:
    m = _get_model(db, model_id)
    fields = body.model_fields_set - {"api_key", "clear_api_key", "copy_from_model_id"}
    for name in fields:
        value = getattr(body, name)
        if value is None and name != "context_chars":
            continue
        setattr(m, name, value.strip() if isinstance(value, str) else value)
    if body.copy_from_model_id is not None:
        source = _translation_model(db, body.copy_from_model_id)
        if "base_url" not in fields or not body.base_url:
            m.base_url = source.base_url
        if not body.api_key:
            m.api_key_enc = source.api_key_enc
    if body.clear_api_key:
        m.api_key_enc = ""
    elif body.api_key:
        m.api_key_enc = ctx.secrets.encrypt(body.api_key.strip())
    db.commit()
    db.refresh(m)
    return _model_out(ctx, m, _presets_using(db))


@router.delete("/models/{model_id}", status_code=204)
def delete_model(model_id: int, db: DbDep) -> None:
    m = _get_model(db, model_id)
    users = _presets_using(db).get(m.id)
    if users:
        raise HTTPException(409, f"方案“{'、'.join(users)}”还在用这个模型，请先改掉方案里的选择")
    db.delete(m)
    db.commit()


def _probe_credentials(db: Session, ctx: AppContext, body: LlmProbeIn) -> tuple[str, str]:
    base_url, api_key = body.base_url, body.api_key.strip()
    if body.model_id is not None:
        saved = _get_model(db, body.model_id)
        base_url = base_url or saved.base_url
        api_key = api_key or ctx.secrets.decrypt(saved.api_key_enc)
    if body.copy_from_model_id is not None:
        source = _translation_model(db, body.copy_from_model_id)
        base_url = base_url or source.base_url
        api_key = api_key or ctx.secrets.decrypt(source.api_key_enc)
    return base_url, api_key


@router.post("/models/probe")
async def probe_models(body: LlmProbeIn, db: DbDep, ctx: CtxDep) -> dict[str, list[str]]:
    base_url, api_key = _probe_credentials(db, ctx, body)
    try:
        return {"models": await fetch_models(_http(ctx), base_url, api_key)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/models/detect-efforts")
async def detect_efforts(body: LlmProbeIn, db: DbDep, ctx: CtxDep) -> EffortDetectOut:
    """发一个不存在的档位，从 400 报错里读出可用档位（中转会列出来）；读不出就只给内置表。

    请求带 max_completion_tokens=1：万一对方不校验档位直接回答，也只花一个 token。
    """
    model = body.model.strip()
    if not model and body.model_id is not None:
        model = _get_model(db, body.model_id).model
    if not model:
        raise HTTPException(400, "请先填写模型名")
    base_url, api_key = _probe_credentials(db, ctx, body)
    builtin = builtin_efforts(model)
    http = _http(ctx)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "reasoning_effort": "bdw-probe",
        "max_completion_tokens": 1,
    }
    try:
        resp = await http.post(
            endpoint(base_url, "/chat/completions"), json=payload, headers=auth_headers(api_key), timeout=60.0
        )
    except Exception as e:
        raise HTTPException(400, failure_text("连接失败", e)) from e
    text = resp.text[:1000]
    detected = parse_valid_levels(text) if resp.status_code == 400 else []
    if detected:
        # 中转的档位表通常不含 none，但会放行；内置表说这个模型有“关闭”档时补上
        suggested = normalize_levels(detected + (["none"] if "none" in builtin else []))
        message = f"接口接受的档位：{'、'.join(detected)}"
    elif resp.status_code in (401, 403):
        raise HTTPException(400, f"接口拒绝了 Key（HTTP {resp.status_code}）")
    else:
        suggested = builtin
        message = f"接口没有列出可用档位（HTTP {resp.status_code}），先按内置表填写，可以手动修改"
    return EffortDetectOut(detected=detected, builtin=builtin, suggested=suggested, message=message)


def _http(ctx: AppContext) -> httpx.AsyncClient:
    http = ctx.meetings.http
    if http is None:
        raise HTTPException(503, "服务还在启动，请稍后再试")
    return http


async def _run_test(ctx: AppContext, cfg: LlmConfig) -> LlmTestOut:
    client = LlmClient(cfg, _http(ctx))
    sent = {k: v for k, v in client.payload(TEST_MESSAGES).items() if k not in ("messages",)}
    started = time.monotonic()
    try:
        result = await client.chat(TEST_MESSAGES, timeout=min(cfg.timeout or TEST_TIMEOUT, TEST_TIMEOUT))
    except LlmError as e:
        return LlmTestOut(ok=False, error=str(e), latency_ms=int((time.monotonic() - started) * 1000), sent=sent)
    return LlmTestOut(
        ok=bool(result.text.strip()),
        latency_ms=int((time.monotonic() - started) * 1000),
        reply=result.text.strip()[:200],
        error=None if result.text.strip() else "大模型没有返回内容",
        tokens=result.tokens,
        reasoning_tokens=result.reasoning_tokens,
        finish_reason=result.finish_reason,
        sent=sent,
    )


@router.post("/models/{model_id}/test")
async def test_model(model_id: int, body: LlmTestIn, db: DbDep, ctx: CtxDep) -> LlmTestOut:
    m = _get_model(db, model_id)
    step = StepConfig(effort=body.effort, timeout_s=int(TEST_TIMEOUT))
    ladder = normalize_levels(m.effort_levels or [])
    if body.effort != "default" and effective_effort(body.effort, ladder) is None:
        raise HTTPException(400, "这个模型的档位表里没有所选的思考强度")
    cfg = build_config(m, ctx.secrets, step, step="speakers", label=f"{m.name}·测试")
    return await _run_test(ctx, cfg)


# ---------- 整理方案 ----------


def _get_preset(db: Session, preset_id: int) -> MeetingLlmPreset:
    p = db.get(MeetingLlmPreset, preset_id)
    if p is None:
        raise HTTPException(404, "整理方案不存在")
    return p


def _problems(db: Session, steps: PresetSteps) -> list[str]:
    out = []
    for step in STEPS:
        sc, model = step_model(db, steps, step)
        label = STEP_LABELS[step]
        if model is None:
            out.append(f"“{label}”没有可用的模型")
        elif not model.enabled:
            out.append(f"“{label}”用的模型“{model.name}”已停用")
        elif sc.effort != "default" and sc.effort not in normalize_levels(model.effort_levels or []):
            out.append(f"“{label}”的思考强度不在模型“{model.name}”的档位里，发送时会就近换成可用的档位")
    return out


def _preset_out(db: Session, p: MeetingLlmPreset) -> PresetAdminOut:
    steps = load_steps(p.steps)
    return PresetAdminOut(
        id=p.id,
        name=p.name,
        description=p.description,
        steps=steps,
        is_default=p.is_default,
        enabled=p.enabled,
        sort_order=p.sort_order,
        problems=_problems(db, steps),
        updated_at=p.updated_at,
    )


def _validate(db: Session, steps: PresetSteps) -> None:
    try:
        check_steps(db, steps)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/presets")
def list_presets(db: DbDep) -> list[PresetAdminOut]:
    rows = db.scalars(select(MeetingLlmPreset).order_by(MeetingLlmPreset.sort_order, MeetingLlmPreset.id)).all()
    return [_preset_out(db, p) for p in rows]


@router.post("/presets", status_code=201)
def create_preset(body: PresetIn, db: DbDep) -> PresetAdminOut:
    _validate(db, body.steps)
    p = MeetingLlmPreset(
        name=body.name.strip(),
        description=body.description.strip(),
        steps=body.steps.model_dump(mode="json"),
        is_default=body.is_default,
        enabled=body.enabled,
        sort_order=body.sort_order,
    )
    db.add(p)
    db.flush()
    ensure_single_default(db, MeetingLlmPreset, p)
    db.commit()
    db.refresh(p)
    return _preset_out(db, p)


@router.patch("/presets/{preset_id}")
def patch_preset(preset_id: int, body: PresetPatch, db: DbDep) -> PresetAdminOut:
    p = _get_preset(db, preset_id)
    if body.steps is not None:
        _validate(db, body.steps)
        p.steps = body.steps.model_dump(mode="json")
    for name in body.model_fields_set - {"steps"}:
        value = getattr(body, name)
        if value is not None:
            setattr(p, name, value.strip() if isinstance(value, str) else value)
    ensure_single_default(db, MeetingLlmPreset, p)
    db.commit()
    db.refresh(p)
    return _preset_out(db, p)


@router.delete("/presets/{preset_id}", status_code=204)
def delete_preset(preset_id: int, db: DbDep) -> None:
    """用过这个方案的会议之后按默认方案处理（会议上只存了方案号，不加外键）。"""
    p = _get_preset(db, preset_id)
    db.delete(p)
    db.flush()
    ensure_single_default(db, MeetingLlmPreset)
    db.commit()


@router.post("/presets/{preset_id}/test")
async def test_preset(preset_id: int, body: PresetTestIn, db: DbDep, ctx: CtxDep) -> LlmTestOut:
    """按方案里这个用途的实际参数发一句话（最多等 2 分钟）。"""
    p = _get_preset(db, preset_id)
    sc, model = step_model(db, load_steps(p.steps), body.step)
    if model is None:
        raise HTTPException(400, f"“{STEP_LABELS[body.step]}”没有可用的模型")
    cfg = build_config(model, ctx.secrets, sc, step=body.step, label=f"{p.name}·{STEP_LABELS[body.step]}")
    return await _run_test(ctx, cfg)
