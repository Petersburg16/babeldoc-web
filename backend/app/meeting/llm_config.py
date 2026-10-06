"""会议用的大模型配置：会议模型（连接、思考档位）与整理方案（4 个用途各用哪个模型、什么参数）。

参数规则参考 Cherry Studio：
- 思考强度选“默认”就不发 reasoning_effort；选的档位模型不支持时就近映射，永远不发档位表里没有的档位
- 温度、Top-P、最大输出各带开关，关着就不发，由模型自己决定
- gpt-5 / gpt-6 / o 系列的输出上限字段是 max_completion_tokens
- 自定义参数最后合入请求体，但不能覆盖 model / messages / stream / stream_options
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..defaults import default_or_first
from ..llm import RESERVED, LlmConfig
from ..models import MeetingLlmModel, MeetingLlmPreset
from ..security import SecretBox

Step = Literal["speakers", "polish", "minutes", "chat"]
STEPS: tuple[Step, ...] = ("speakers", "polish", "minutes", "chat")
STEP_LABELS: dict[str, str] = {
    "speakers": "猜说话人",
    "polish": "整理逐字稿",
    "minutes": "生成纪要",
    "chat": "对话问答",
}
# 与 0.4.0 的写死值一致：没改过方案时行为不变。超时指多久没收到数据（流式时是两段之间的间隔）
DEFAULT_TIMEOUTS: dict[str, int] = {"speakers": 300, "polish": 300, "minutes": 900, "chat": 600}

# 思考强度从低到高；就近映射按这个顺序找
EFFORT_ORDER: tuple[str, ...] = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
EFFORT_LABELS: dict[str, str] = {
    "none": "关闭",
    "minimal": "极低",
    "low": "低",
    "medium": "中",
    "high": "高",
    "xhigh": "很高",
    "max": "最高",
}
# 内置档位表，取自 Cherry Studio 的 OpenAI 系列规则；按顺序第一条匹配生效。没匹配上的模型档位表为空，可在后台检测或手填
EFFORT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"^gpt-6[.-]1-sol(?:$|-)", ("low", "medium", "high", "xhigh", "max")),
    (r"^gpt-6-astra", ("low", "medium", "high", "xhigh", "max")),
    (r"^(?:o\d|gpt).*deep[-_]?research", ("medium",)),
    (r"^gpt-5[.-]1-codex-max", ("medium", "high", "xhigh")),
    (r"^gpt-5[.-]1-codex", ("medium", "high")),
    (r"^gpt-5[.-]1(?!\d)(?!.*chat)", ("none", "low", "medium", "high")),
    (r"^gpt-5-pro", ("high",)),
    (r"^gpt-5[.-]\d+-pro", ("medium", "high", "xhigh")),
    (r"^gpt-5-codex", ("low", "medium", "high")),
    (r"^gpt-5[.-]\d+-codex", ("low", "medium", "high", "xhigh")),
    (r"^gpt-5[.-]\d+(?!.*chat)", ("none", "low", "medium", "high", "xhigh")),
    (r"^gpt-5(?![.-]\d)(?!.*chat)", ("minimal", "low", "medium", "high")),
    (r"^gpt-6", ("none", "low", "medium", "high", "xhigh", "max")),
    (r"^gpt-oss", ("low", "medium", "high")),
    (r"^o1(?!-preview|-mini)|^o3|^o4", ("low", "medium", "high")),
)
# 这些模型的输出上限字段是 max_completion_tokens（思考 token 也算在里面）
_COMPLETION_TOKENS = re.compile(r"^(?:gpt-5|gpt-6|o\d)")
_PARAM_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")


def _bare(model: str) -> str:
    """去掉 “openai/” 之类的前缀再匹配。"""
    return model.strip().lower().rsplit("/", 1)[-1]


def builtin_efforts(model: str) -> list[str]:
    name = _bare(model)
    for pattern, levels in EFFORT_RULES:
        if re.search(pattern, name):
            return list(levels)
    return []


def max_tokens_field(model: str) -> str:
    return "max_completion_tokens" if _COMPLETION_TOKENS.search(_bare(model)) else "max_tokens"


def normalize_levels(levels: list[str]) -> list[str]:
    """只留认识的档位，去重并按从低到高排好。"""
    wanted = {str(x).strip().lower() for x in levels}
    return [e for e in EFFORT_ORDER if e in wanted]


def normalize_effort(v: str) -> str:
    """方案和测试里选的思考强度：转小写，只认“默认”和 EFFORT_ORDER 里的档位，不认识的抛 ValueError。"""
    v = (v or "default").strip().lower()
    if v != "default" and v not in EFFORT_ORDER:
        raise ValueError(f"未知的思考强度：{v}")
    return v


def effective_effort(selected: str, ladder: list[str]) -> str | None:
    """把方案里选的思考强度落到这个模型的档位表上；返回 None 表示不发 reasoning_effort。"""
    if not selected or selected == "default" or not ladder:
        return None
    if selected in ladder:
        return selected
    if selected == "none" or selected not in EFFORT_ORDER:
        # 模型没有“关闭”档：不发，由模型用默认强度（不能把“关闭”映射成“低”）
        return None
    want = EFFORT_ORDER.index(selected)
    # 就近映射，距离相同时取高的一档
    return min(ladder, key=lambda e: (abs(EFFORT_ORDER.index(e) - want), -EFFORT_ORDER.index(e)))


_VALID_LEVELS = re.compile(r"valid (?:levels|values)[^:：]*[:：]\s*([A-Za-z, _-]+)", re.I)


def parse_valid_levels(message: str) -> list[str]:
    """从中转的报错里取出可用档位，例如 'level "x" not supported, valid levels: low, medium, high'。"""
    match = _VALID_LEVELS.search(message or "")
    if not match:
        return []
    return normalize_levels(re.split(r"[\s,]+", match.group(1)))


# ---------- 方案里每个用途的配置 ----------


class FloatToggle(BaseModel):
    on: bool = False
    value: float = 1.0


class IntToggle(BaseModel):
    on: bool = False
    value: int = 4096


class CustomParam(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: Literal["string", "number", "boolean", "json"] = "string"
    value: str = Field(default="", max_length=4000)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not _PARAM_NAME.match(v):
            raise ValueError(f"参数名“{v}”只能用字母、数字、下划线、点和连字符，且以字母或下划线开头")
        if v in RESERVED:
            raise ValueError(f"“{v}”由本站自己填写，不能作为自定义参数")
        return v

    @model_validator(mode="after")
    def _value(self) -> CustomParam:
        parse_param(self)  # 解析不了就在保存时报错
        return self


def parse_param(p: CustomParam) -> Any:
    raw = p.value.strip()
    if p.type == "string":
        return p.value
    if p.type == "number":
        try:
            number = float(raw)
        except ValueError:
            raise ValueError(f"参数“{p.name}”不是数字") from None
        return int(number) if number.is_integer() and "." not in raw and "e" not in raw.lower() else number
    if p.type == "boolean":
        if raw.lower() in ("true", "1", "yes", "on"):
            return True
        if raw.lower() in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"参数“{p.name}”只能是 true 或 false")
    try:
        return json.loads(raw)
    except ValueError:
        raise ValueError(f"参数“{p.name}”不是合法的 JSON") from None


class StepConfig(BaseModel):
    model_id: int | None = None
    effort: str = "default"
    temperature: FloatToggle = Field(default_factory=lambda: FloatToggle(value=1.0))
    top_p: FloatToggle = Field(default_factory=lambda: FloatToggle(value=1.0))
    max_tokens: IntToggle = Field(default_factory=IntToggle)
    timeout_s: int = Field(default=300, ge=30, le=7200)
    params: list[CustomParam] = Field(default_factory=list, max_length=20)

    @field_validator("effort")
    @classmethod
    def _effort(cls, v: str) -> str:
        return normalize_effort(v)

    @model_validator(mode="after")
    def _ranges(self) -> StepConfig:
        if not 0 <= self.temperature.value <= 2:
            raise ValueError("模型温度要在 0 到 2 之间")
        if not 0 <= self.top_p.value <= 1:
            raise ValueError("Top-P 要在 0 到 1 之间")
        if not 1 <= self.max_tokens.value <= 1_000_000:
            raise ValueError("最大输出要在 1 到 1000000 之间")
        names = [p.name for p in self.params]
        if len(names) != len(set(names)):
            raise ValueError("自定义参数有重名")
        toggled = {"temperature": self.temperature.on, "top_p": self.top_p.on}
        toggled["max_tokens"] = toggled["max_completion_tokens"] = self.max_tokens.on
        clash = [n for n in names if toggled.get(n)]
        if clash:
            raise ValueError(f"自定义参数“{clash[0]}”和上面打开的开关重复了，请只保留一处")
        return self


def default_step(step: str) -> StepConfig:
    return StepConfig(timeout_s=DEFAULT_TIMEOUTS[step])


class PresetSteps(BaseModel):
    speakers: StepConfig = Field(default_factory=lambda: default_step("speakers"))
    polish: StepConfig = Field(default_factory=lambda: default_step("polish"))
    minutes: StepConfig = Field(default_factory=lambda: default_step("minutes"))
    chat: StepConfig = Field(default_factory=lambda: default_step("chat"))

    @model_validator(mode="before")
    @classmethod
    def _step_timeouts(cls, data: Any) -> Any:
        # 没写超时的用途用它自己的默认值（纪要 900、对话 600），而不是 StepConfig 通用的 300
        if isinstance(data, dict):
            return {
                k: {"timeout_s": DEFAULT_TIMEOUTS[k], **v} if k in DEFAULT_TIMEOUTS and isinstance(v, dict) else v
                for k, v in data.items()
            }
        return data


def load_steps(raw: Any) -> PresetSteps:
    """读库时宽松解析：某个用途的配置坏了就退回默认值，不让整个方案用不了。"""
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, StepConfig] = {}
    for step in STEPS:
        try:
            out[step] = StepConfig.model_validate({"timeout_s": DEFAULT_TIMEOUTS[step], **(raw.get(step) or {})})
        except (ValidationError, TypeError, ValueError):
            out[step] = default_step(step)
    return PresetSteps(**out)


def step_model(db: Session, steps: PresetSteps, step: str) -> tuple[StepConfig, MeetingLlmModel | None]:
    """方案里这个用途的配置和它选的模型（没选或已删除时为 None）。启用与否、档位由各调用方按场景自己检查。"""
    sc: StepConfig = getattr(steps, step)
    return sc, db.get(MeetingLlmModel, sc.model_id) if sc.model_id is not None else None


def check_steps(db: Session, steps: PresetSteps) -> None:
    """保存方案前的跨表校验：模型存在，思考强度在该模型的档位表里。出错抛 ValueError。"""
    for step in STEPS:
        sc, model = step_model(db, steps, step)
        label = STEP_LABELS[step]
        if sc.model_id is None:
            raise ValueError(f"请给“{label}”选一个模型")
        if model is None:
            raise ValueError(f"“{label}”选的模型不存在")
        ladder = normalize_levels(model.effort_levels or [])
        if sc.effort != "default" and sc.effort not in ladder:
            allowed = "、".join(EFFORT_LABELS[e] for e in ladder) or "无（这个模型没有思考档位）"
            raise ValueError(f"“{label}”的思考强度不在模型“{model.name}”的档位里，可选：{allowed}")


# ---------- 运行时：按会议和用途找到要用的模型与参数 ----------


def find_preset(db: Session, preset_id: int | None) -> MeetingLlmPreset | None:
    """会议自己的方案 → 默认方案 → 第一个启用的方案；方案被删或停用时往后退。"""
    enabled = select(MeetingLlmPreset).where(MeetingLlmPreset.enabled.is_(True))
    preset = db.scalar(enabled.where(MeetingLlmPreset.id == preset_id)) if preset_id is not None else None
    return preset or default_or_first(db, enabled, MeetingLlmPreset)


def build_config(model: MeetingLlmModel, box: SecretBox, sc: StepConfig, *, step: str, label: str) -> LlmConfig:
    ladder = normalize_levels(model.effort_levels or [])
    return LlmConfig(
        model_id=model.id,
        base_url=model.base_url,  # 留空、末尾斜杠由 LlmClient 拼地址时处理
        api_key=box.decrypt(model.api_key_enc),
        model=model.model,
        json_mode=model.json_mode,
        qps=max(1, model.qps),
        # 对话单独限流：纪要长时间思考时，提问不用排在它后面
        namespace="meeting-chat" if step == "chat" else "meeting",
        effort=effective_effort(sc.effort, ladder),
        temperature=sc.temperature.value if sc.temperature.on else None,
        top_p=sc.top_p.value if sc.top_p.on else None,
        max_tokens=sc.max_tokens.value if sc.max_tokens.on else None,
        max_tokens_field=max_tokens_field(model.model),
        extra={p.name: parse_param(p) for p in sc.params},
        timeout=float(sc.timeout_s),
        context_chars=model.context_chars,
        label=label,
    )


def resolve_meeting_llm(
    db: Session, box: SecretBox, preset_id: int | None, step: str
) -> tuple[LlmConfig | None, str | None]:
    """返回 (配置, None) 或 (None, 给用户看的原因)。只在方案这一层回退，不会悄悄换成参数不同的模型。"""
    preset = find_preset(db, preset_id)
    label = STEP_LABELS.get(step, step)
    if preset is None:
        return None, "管理员还没有配置会议记录用的大模型方案"
    sc, model = step_model(db, load_steps(preset.steps), step)
    if model is None:
        return None, f"整理方案“{preset.name}”没有给“{label}”配置可用的模型"
    if not model.enabled:
        return None, f"整理方案“{preset.name}”中“{label}”用的模型“{model.name}”已停用"
    return build_config(model, box, sc, step=step, label=f"{preset.name}·{label}"), None
