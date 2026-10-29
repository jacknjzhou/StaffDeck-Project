from __future__ import annotations

from copy import deepcopy
from typing import Any


class JSONPatchError(ValueError):
    """JSON Patch 应用失败时抛出的异常；消息指明出错的指针、下标或操作位置。"""


def _tokens(pointer: str) -> list[str]:
    """把 JSON Pointer 拆成路径片段，并还原其中的 ~1 与 ~0 转义；根指针返回空列表。"""
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise JSONPatchError("JSON Pointer must start with '/'.")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _index(token: str, size: int, *, append: bool = False) -> int:
    """把数组下标片段转成整数并做越界校验；append 为真时 "-" 表示末尾追加位。"""
    if append and token == "-":
        return size
    try:
        value = int(token)
    except ValueError as exc:
        raise JSONPatchError(f"Invalid array index: {token}") from exc
    if value < 0 or value >= size + (1 if append else 0):
        raise JSONPatchError(f"Array index out of range: {token}")
    return value


def _resolve(document: Any, pointer: str) -> Any:
    """沿指针读取子文档；中间任一段缺失或下标非法时抛 JSONPatchError。"""
    current = document
    for token in _tokens(pointer):
        if isinstance(current, list):
            current = current[_index(token, len(current))]
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise JSONPatchError(f"Path does not exist: {pointer}")
    return current


def _parent(document: Any, pointer: str) -> tuple[Any, str]:
    """返回指针所指向值的父容器与最后一段片段；根路径没有父级，直接报错。"""
    tokens = _tokens(pointer)
    if not tokens:
        raise JSONPatchError("The document root has no parent.")
    current = document
    for token in tokens[:-1]:
        if isinstance(current, list):
            current = current[_index(token, len(current))]
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise JSONPatchError(f"Path does not exist: {pointer}")
    return current, tokens[-1]


def _add(document: Any, path: str, value: Any) -> Any:
    """在 path 处写入 value 的深拷贝：数组按下标插入，字典覆盖或新增键。

    空 path 表示整体替换文档，此时返回新值而非原容器；容器类型不支持时报错。
    """
    if path == "":
        return deepcopy(value)
    parent, token = _parent(document, path)
    if isinstance(parent, list):
        parent.insert(_index(token, len(parent), append=True), deepcopy(value))
    elif isinstance(parent, dict):
        parent[token] = deepcopy(value)
    else:
        raise JSONPatchError(f"Cannot add at path: {path}")
    return document


def _remove(document: Any, path: str) -> tuple[Any, Any]:
    """删除 path 处的值并返回 (文档, 被删值)；删除根路径时文档位置返回 None。"""
    if path == "":
        old = deepcopy(document)
        return None, old
    parent, token = _parent(document, path)
    if isinstance(parent, list):
        old = parent.pop(_index(token, len(parent)))
    elif isinstance(parent, dict) and token in parent:
        old = parent.pop(token)
    else:
        raise JSONPatchError(f"Path does not exist: {path}")
    return document, old


def apply_json_patch(document: Any, operations: list[dict[str, Any]]) -> Any:
    """按顺序应用 RFC 6902 风格的补丁操作，返回深拷贝后的新文档，入参保持不变。

    replace 要求目标已存在，add 允许新增键或在下标 "-" 处追加；test 失败即中断。
    未知 op、缺失 path/value/from 等格式问题统一抛 JSONPatchError。
    """
    result = deepcopy(document)
    for position, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise JSONPatchError(f"Patch operation {position} must be an object.")
        op = str(operation.get("op") or "")
        path = operation.get("path")
        if not isinstance(path, str):
            raise JSONPatchError(f"Patch operation {position} requires path.")
        if op == "add":
            if "value" not in operation:
                raise JSONPatchError("add requires value.")
            result = _add(result, path, operation["value"])
        elif op == "remove":
            result, _ = _remove(result, path)
        elif op == "replace":
            if "value" not in operation:
                raise JSONPatchError("replace requires value.")
            _resolve(result, path)
            result, _ = _remove(result, path)
            result = _add(result, path, operation["value"])
        elif op in {"move", "copy"}:
            source = operation.get("from")
            if not isinstance(source, str):
                raise JSONPatchError(f"{op} requires from.")
            value = deepcopy(_resolve(result, source))
            if op == "move":
                result, _ = _remove(result, source)
            result = _add(result, path, value)
        elif op == "test":
            if _resolve(result, path) != operation.get("value"):
                raise JSONPatchError(f"test failed at path: {path}")
        else:
            raise JSONPatchError(f"Unsupported patch operation: {op}")
    return result
