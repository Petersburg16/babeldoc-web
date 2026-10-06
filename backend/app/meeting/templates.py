"""内置纪要模板。outline 是纪要的标题骨架，生成时交给大模型按它组织内容。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import AfterValidator, Field


@dataclass(frozen=True)
class MinutesTemplate:
    id: str
    name: str
    description: str
    outline: str


TEMPLATES: tuple[MinutesTemplate, ...] = (
    MinutesTemplate(
        "group_topic",
        "组会·按议题",
        "按讨论的议题整理要点、结论和待定事项，适合大多数组会。",
        """# 会议纪要：<会议主题>
（时间、时长、参会人）

## 概要
3–5 句话概括这次会议。

## 议题
### 1. <议题名>
- 讨论要点（每条带时间戳）
- 结论（如有）
- 待定事项（如有）

## 待办
| 事项 | 负责人 | 截止 | 时间戳 |

## 下次会议
（原文提到才写）""",
    ),
    MinutesTemplate(
        "group_speaker",
        "组会·按发言人",
        "按每个人的汇报整理进展、问题、老师意见和下一步，适合轮流汇报的组会。",
        """# 会议纪要：<会议主题>
（时间、时长、参会人）

## 概要

## 各人汇报
### <发言人>
- 进展
- 遇到的问题
- 老师/他人的意见
- 下一步

## 共同讨论与决定

## 待办
| 事项 | 负责人 | 截止 | 时间戳 |""",
    ),
    MinutesTemplate(
        "project",
        "项目讨论",
        "围绕目标、进度、风险和决策整理，适合项目推进会。",
        """# 会议纪要：<会议主题>
（时间、时长、参会人）

## 概要
## 目标与范围
## 当前进度
## 问题与风险
## 决定
## 待办
| 事项 | 负责人 | 截止 | 时间戳 |""",
    ),
    MinutesTemplate(
        "interview",
        "访谈",
        "按问题整理受访者的回答和观点，适合访谈、答辩、讲座问答。",
        """# 访谈记录：<主题>
（时间、时长、参与人）

## 概要
## 问答
### 问：<问题>
- 答：要点（带时间戳）

## 主要观点
## 后续跟进""",
    ),
    MinutesTemplate(
        "general",
        "通用",
        "概要、要点、结论和待办，适合其他类型的会议。",
        """# 会议纪要：<会议主题>
（时间、时长、参会人）

## 概要
## 主要内容
## 结论
## 待办
| 事项 | 负责人 | 截止 | 时间戳 |""",
    ),
)

TEMPLATE_IDS = tuple(t.id for t in TEMPLATES)
DEFAULT_TEMPLATE = TEMPLATES[0].id


def check_template_id(v: str) -> str:
    if v not in TEMPLATE_IDS:
        raise ValueError("未知的纪要模板")
    return v


# 请求体和系统设置里的纪要模板：只能选内置模板
TemplateId = Annotated[str, Field(max_length=32), AfterValidator(check_template_id)]


def get_template(template_id: str | None) -> MinutesTemplate:
    for t in TEMPLATES:
        if t.id == template_id:
            return t
    return TEMPLATES[0]
