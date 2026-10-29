from __future__ import annotations

import re
from collections.abc import Callable

from app.db.models import AgentProfile

AGENT_PERSONA_METADATA_FIELDS: tuple[tuple[str, str], ...] = (
    ("role_name", "岗位"),
    ("position", "岗位"),
    ("job_title", "岗位"),
    ("role", "角色"),
    ("title", "职务"),
    ("department", "部门"),
    ("team", "团队"),
    ("work_styles", "工作风格"),
    ("expertise_tags", "擅长领域"),
    ("work_modes", "工作方式"),
)


class AgentIdentityPrompt:
    """把数字员工档案渲染成身份提示词，并统一元数据格式化与换行归一化。"""

    @classmethod
    def render(
        cls,
        agent: AgentProfile,
        *,
        single_line: Callable[[object], str] | None = None,
        metadata_formatter: Callable[[object], str] | None = None,
    ) -> str:
        """按员工档案拼装身份提示词，供会话注入时直接使用。

        「岗位」标签在多个来源字段间只取首个非空值；`persona_prompt` 非空时
        追加为角色补充要求，取不到值的元数据整行省略。
        """
        normalize = single_line or cls.single_line
        format_metadata = metadata_formatter or cls.metadata_text
        metadata = agent.metadata_json if isinstance(agent.metadata_json, dict) else {}
        lines = [
            "你正在扮演一个企业数字员工。请始终以该员工的身份、岗位和职责口径回复用户，不要自称其他员工。",
            f"员工名称：{normalize(agent.name)}",
        ]
        description = normalize(agent.description)
        if description:
            lines.append(f"员工描述：{description}")
        seen_labels: set[str] = set()
        for key, label in AGENT_PERSONA_METADATA_FIELDS:
            value = format_metadata(metadata.get(key))
            if not value:
                continue
            if label in seen_labels and label == "岗位":
                continue
            seen_labels.add(label)
            lines.append(f"{label}：{value}")
        persona = str(agent.persona_prompt or "").strip()
        if persona:
            lines.extend(["", "员工角色补充要求：", persona])
        return "\n".join(lines)

    @classmethod
    def metadata_text(cls, value: object) -> str:
        """把元数据值转成单行文本，列表用顿号连接，无法识别的类型返回空串。"""
        if isinstance(value, str):
            return cls.single_line(value)
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, list):
            items = [cls.single_line(item) for item in value]
            return "、".join(item for item in items if item)
        return ""

    @staticmethod
    def single_line(value: object) -> str:
        """把任意值转成字符串，折叠其中的连续空白并去掉首尾空格。"""
        return re.sub(r"\s+", " ", str(value or "")).strip()
