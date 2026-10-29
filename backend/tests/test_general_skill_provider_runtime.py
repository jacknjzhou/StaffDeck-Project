from app.capabilities.local_general_skill import (
    package_from_row,
)
from app.db.models import GeneralSkill


class RecordingCatalog:
    """记录调用参数的技能目录替身，用于断言提供方读取包时传入的上下文与资源引用。"""

    provider_id = "recording_catalog"

    def __init__(self, package):
        """保存待返回的技能包并初始化调用记录容器。"""
        self.package = package
        self.calls = []

    def get_package(self, context, resource_ref):
        """记录本次调用的上下文与资源引用，并返回预置技能包。"""
        self.calls.append((context, resource_ref))
        return self.package


def _skill() -> GeneralSkill:
    """构造已发布的天气技能记录，供包构建相关测试复用。"""
    return GeneralSkill(
        id="genskill_weather",
        tenant_id="tenant_demo",
        slug="weather",
        name="天气",
        description="查询天气",
        skill_markdown="# Weather",
        skill_files_json=[
            {
                "path": "SKILL.md",
                "content": "# Weather",
                "size": 9,
                "mime_type": "text/markdown",
            }
        ],
        status="published",
    )


def test_provider_package_pin_rejects_content_drift() -> None:
    """验证技能内容被改写后重新构建包会产生不同摘要，从而识别内容漂移。"""
    skill = _skill()
    first = package_from_row(skill)
    skill.skill_markdown = "# Changed"
    second = package_from_row(skill)

    assert first.digest != second.digest
