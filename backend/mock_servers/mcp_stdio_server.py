#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from typing import Any


def main() -> None:
    """按 stdio 读取 JSON-RPC 行，响应 MCP 初始化、工具列表与工具调用请求。"""
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            _respond(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "StaffDeck-mock-mcp", "version": "0.1.0"},
                },
            )
            continue
        if method == "notifications/initialized":
            continue
        if method == "tools/list":
            _respond(
                request_id,
                {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Return the input text and its length.",
                            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                        },
                        {
                            "name": "sum",
                            "description": "Sum a list of numbers.",
                            "inputSchema": {"type": "object", "properties": {"numbers": {"type": "array"}}},
                        },
                        {
                            "name": "product_lookup",
                            "description": "Look up demo product price data.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"product_id": {"type": "string"}, "product_name": {"type": "string"}},
                            },
                        },
                    ]
                },
            )
            continue
        if method == "tools/call":
            _call_tool(request_id, request.get("params") or {})
            continue
        if request_id is not None:
            _error(request_id, -32601, f"Unsupported method: {method}")


def _call_tool(request_id: Any, params: dict[str, Any]) -> None:
    """按工具名分发 echo、sum 与 product_lookup，未知工具返回工具级错误。"""
    name = str(params.get("name") or "")
    arguments = params.get("arguments") or {}
    if name == "echo":
        text = str(arguments.get("text") or "")
        _tool_result(request_id, {"text": text, "length": len(text)})
        return
    if name == "sum":
        numbers = arguments.get("numbers")
        if not isinstance(numbers, list) or not all(_is_number(item) for item in numbers):
            _tool_error(request_id, "sum requires numeric array argument: numbers")
            return
        _tool_result(request_id, {"numbers": numbers, "total": sum(numbers), "count": len(numbers)})
        return
    if name == "product_lookup":
        product_id = str(arguments.get("product_id") or arguments.get("product_name") or "").strip().lower()
        catalog = {
            "a1": {"product_id": "A1", "display_name": "A1 标准商品", "price": 129.0, "currency": "CNY"},
            "a3": {"product_id": "A3", "display_name": "A3 高阶商品", "price": 239.0, "currency": "CNY"},
        }
        item = catalog.get(product_id)
        _tool_result(request_id, {"found": bool(item), **(item or {"query": product_id})})
        return
    _tool_error(request_id, f"Unknown tool: {name}")


def _is_number(value: Any) -> bool:
    """判断值是否为非布尔数值，避免 bool 被当作数字参与求和。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _tool_result(request_id: Any, data: Any) -> None:
    """把工具执行数据序列化为 MCP 文本内容结果返回。"""
    _respond(
        request_id,
        {
            "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
            "isError": False,
        },
    )


def _tool_error(request_id: Any, message: str) -> None:
    """返回携带错误文案且 isError 为真的工具结果。"""
    _respond(request_id, {"content": [{"type": "text", "text": message}], "isError": True})


def _respond(request_id: Any, result: Any) -> None:
    """向 stdout 输出一条 JSON-RPC 成功响应并立即刷新缓冲区。"""
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, ensure_ascii=False), flush=True)


def _error(request_id: Any, code: int, message: str) -> None:
    """向 stdout 输出一条 JSON-RPC 协议错误响应并立即刷新缓冲区。"""
    print(
        json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}),
        flush=True,
    )


if __name__ == "__main__":
    main()
