from __future__ import annotations

import json
import re
from collections.abc import Mapping

from app.capabilities.contracts import GeneralSkillPackage, KnowledgeSearchResult
from app.capabilities.errors import CapabilityErrorInfo

_EXTENSION_NAMESPACE = re.compile(r"^[a-z][a-z0-9_]*$")
_RESERVED_NAMESPACES = {"core", "staffdeck"}


class ContractViolation(AssertionError):
    """共享 Provider Contract Test Kit 在断言不满足时抛出的失败信号。"""


def assert_namespaced_extensions(extensions: Mapping[str, object]) -> None:
    """校验扩展字段：命名空间须为小写蛇形且不占用 core/staffdeck，值须是对象，整体可 JSON 序列化且不超 64 KiB。

    任一条不满足即抛 ContractViolation，错误信息带上违规的命名空间或具体原因。
    """
    for namespace in extensions:
        if not _EXTENSION_NAMESPACE.fullmatch(namespace) or namespace in _RESERVED_NAMESPACES:
            raise ContractViolation(
                f"extension namespace must be lowercase snake case: {namespace!r}"
            )
        if not isinstance(extensions[namespace], Mapping):
            raise ContractViolation("each extension namespace must contain an object")
    try:
        encoded = json.dumps(extensions, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ContractViolation("extensions must contain JSON values") from exc
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise ContractViolation("extensions exceed the 64 KiB contract limit")


def assert_knowledge_search_result(result: KnowledgeSearchResult) -> None:
    """校验 knowledge_search 返回结构：query_id 必填、outcome 为 complete/partial、hit_id 不重复且类型正确。

    hit_id 与 content 缺一不可；extensions 复用命名空间规则。全部通过时无返回值。
    """
    if not result.query_id:
        raise ContractViolation("Knowledge result query_id is required")
    if result.outcome not in {"complete", "partial"}:
        raise ContractViolation(f"unknown Knowledge outcome: {result.outcome!r}")
    assert_namespaced_extensions(result.extensions)
    hit_ids: set[str] = set()
    for item in result.items:
        if not item.hit_id or not item.content:
            raise ContractViolation("Knowledge hits require hit_id and content")
        if item.hit_id in hit_ids:
            raise ContractViolation(f"duplicate Knowledge hit_id: {item.hit_id}")
        hit_ids.add(item.hit_id)
        if item.score is not None and not isinstance(item.score, (int, float)):
            raise ContractViolation("Knowledge hit score must be numeric")
        if item.source_ref is not None and not isinstance(item.source_ref, str):
            raise ContractViolation("Knowledge hit source_ref must be a string")


def assert_general_skill_package(package: GeneralSkillPackage) -> None:
    """校验 General Skill 包：标识与入口字段非空、files 至少一项、路径唯一、size 非负且 entrypoint 指向包内文件。

    失败时 ContractViolation 列出缺失字段名或重复路径，便于提供方定位打包问题。
    """
    required = {
        "package_id": package.package_id,
        "slug": package.slug,
        "version": package.version,
        "digest": package.digest,
        "package_contract_version": package.package_contract_version,
        "skill_markdown": package.skill_markdown,
        "entrypoint": package.entrypoint,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ContractViolation(
            "General Skill package requires non-empty fields: " + ", ".join(missing)
        )
    if not package.files:
        raise ContractViolation("General Skill package requires files")
    paths: set[str] = set()
    for item in package.files:
        if not item.path or not isinstance(item.content, str):
            raise ContractViolation("General Skill files require path and string content")
        if item.path in paths:
            raise ContractViolation(f"duplicate General Skill file path: {item.path}")
        if item.size is not None and item.size < 0:
            raise ContractViolation("General Skill file size cannot be negative")
        paths.add(item.path)
    if package.entrypoint not in paths:
        raise ContractViolation("General Skill entrypoint must reference a package file")
    assert_namespaced_extensions(package.extensions)


def assert_provider_error(info: CapabilityErrorInfo) -> None:
    """校验能力调用失败时返回的错误体：code、message、request_id 必填，retryable 必须是布尔值。"""
    if not info.code or not info.message or not info.request_id:
        raise ContractViolation("Provider errors require code, message and request_id")
    if not isinstance(info.retryable, bool):
        raise ContractViolation("Provider error retryable must be boolean")
