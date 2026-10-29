from __future__ import annotations

from dataclasses import dataclass

from app.db.models import Skill, Tool


@dataclass(frozen=True)
class SopToolAuthorization:
    """由已校验并展开的当前 SOP 节点推导出的服务端授权集合。

    绝不能用工具参数或请求元数据填充该上下文：Harness 在完成嵌套校验与展开后
    才会提供其执行的 Skill。
    """

    tenant_id: str
    parent_skill_id: str | None
    step_id: str | None
    skill_ids: frozenset[str]
    tool_refs: frozenset[str]

    def permits_skill(self, tool: Tool) -> bool:
        """判断工具的允许技能白名单与当前授权集合是否有交集；未声明白名单视为开放。"""
        return not tool.allowed_skills_json or bool(
            self.skill_ids.intersection(
                str(value).strip() for value in tool.allowed_skills_json if str(value).strip()
            )
        )

    def explicitly_allows(self, tool: Tool) -> bool:
        """判断当前步骤是否按工具 id 或 name 显式引用了该工具。"""
        return bool(self.tool_refs.intersection({tool.id, tool.name}))


def current_sop_tool_authorization(
    tenant_id: str,
    skill: Skill | None,
    step_id: str | None,
) -> SopToolAuthorization:
    """汇总当前 SOP 步骤的可用技能 id 与工具引用，构造服务端授权快照。

    skill 归属租户与 tenant_id 不一致时抛 ValueError；task_request_compiler 在函数内延迟导入。
    """
    from app.core.task_request_compiler import (
        current_step_authorization_skill_ids,
        current_step_capability_refs,
    )

    if skill is not None and skill.tenant_id != tenant_id:
        raise ValueError("SOP authorization tenant mismatch")
    return SopToolAuthorization(
        tenant_id=tenant_id,
        parent_skill_id=skill.skill_id if skill is not None else None,
        step_id=step_id,
        skill_ids=frozenset(current_step_authorization_skill_ids(skill, step_id)),
        tool_refs=frozenset(current_step_capability_refs(skill, step_id)["tool_ids"]),
    )
