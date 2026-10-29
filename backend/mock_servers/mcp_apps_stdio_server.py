#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from typing import Any


RESOURCE_URI = "ui://staffdeck/demo-card"
MIME_TYPE = "text/html;profile=mcp-app"


def main() -> None:
    """从 stdin 逐行读取 JSON-RPC 请求，按 method 分派并写出模拟 MCP App 响应。"""
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            client_extensions = ((request.get("params") or {}).get("capabilities") or {}).get(
                "extensions", {}
            )
            _respond(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {},
                        "extensions": (
                            {"io.modelcontextprotocol/ui": {}}
                            if "io.modelcontextprotocol/ui" in client_extensions
                            else {}
                        ),
                    },
                    "serverInfo": {"name": "StaffDeck-mock-apps", "version": "0.1.0"},
                },
            )
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            _respond(
                request_id,
                {
                    "tools": [
                        {
                            "name": "render_card",
                            "title": "Render demo card",
                            "description": "Return a card payload with an MCP App view.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"message": {"type": "string"}},
                            },
                            "annotations": {"readOnlyHint": True},
                            "_meta": {
                                "ui": {
                                    "resourceUri": RESOURCE_URI,
                                    "visibility": ["model", "app"],
                                }
                            },
                        }
                    ]
                },
            )
        elif method == "tools/call":
            message = str(((request.get("params") or {}).get("arguments") or {}).get("message") or "")
            _respond(
                request_id,
                {
                    "content": [{"type": "text", "text": message}],
                    "structuredContent": {"message": message},
                    "_meta": {"ui": {"render": True}},
                    "isError": False,
                },
            )
        elif method == "resources/read":
            _respond(
                request_id,
                {
                    "contents": [
                        {
                            "uri": RESOURCE_URI,
                            "mimeType": MIME_TYPE,
                            "text": "<!doctype html><html><body><main id='app'>Demo App</main></body></html>",
                            "_meta": {
                                "ui": {
                                    "csp": {"connectDomains": ["https://example.com"]},
                                    "permissions": ["clipboard-write", "camera"],
                                }
                            },
                        }
                    ]
                },
            )
        elif request_id is not None:
            _respond(request_id, {})


def _respond(request_id: Any, result: Any) -> None:
    """把结果序列化为单行 JSON-RPC 2.0 响应写入 stdout，并立即 flush 以便逐条消费。"""
    print(
        json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, ensure_ascii=False),
        flush=True,
    )


if __name__ == "__main__":
    main()
