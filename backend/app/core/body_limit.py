"""上传请求体大小预检中间件。

FastAPI 在进入端点前就会解析 multipart 请求体,因此 Content-Length 检查只能放在
ASGI 中间件里:超限请求在读取 body 之前直接返回 413,绝不 await receive。
未声明长度(分片传输)的请求放行,仍由端点内的单文件校验兜底。
"""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

MAX_CHAT_ATTACHMENT_BYTES = 12 * 1024 * 1024
MAX_CHAT_ATTACHMENTS = 8
CHAT_ATTACHMENTS_PATH = "/api/chat/attachments"
# multipart 边界、文件头与表单字段的固定开销;预检只拦明显超限的请求
CHAT_UPLOAD_BODY_OVERHEAD_BYTES = 1024 * 1024
CHAT_UPLOAD_BODY_LIMIT = (
    MAX_CHAT_ATTACHMENT_BYTES * MAX_CHAT_ATTACHMENTS + CHAT_UPLOAD_BODY_OVERHEAD_BYTES
)
REJECT_OVERSIZED_DETAIL = "请求体超过上传大小限制"


class BodySizeLimitMiddleware:
    """在 multipart 解析前拒绝声明长度超限的聊天附件上传。"""

    def __init__(self, app: ASGIApp) -> None:
        """保存下游 ASGI 应用。"""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """命中上传端点且声明长度超限时直接回 413,否则原样转发。"""
        if not self._should_check(scope):
            await self.app(scope, receive, send)
            return
        content_length = self._content_length(scope)
        if content_length is not None and content_length > CHAT_UPLOAD_BODY_LIMIT:
            response = JSONResponse(
                {"detail": REJECT_OVERSIZED_DETAIL}, status_code=413
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    @staticmethod
    def _should_check(scope: Scope) -> bool:
        """只检查聊天附件上传的 POST 请求。"""
        return (
            scope.get("type") == "http"
            and scope.get("method") == "POST"
            and scope.get("path") == CHAT_ATTACHMENTS_PATH
        )

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        """解析 content-length 头;缺失或非法时返回 None 表示放行。"""
        for name, value in scope.get("headers") or []:
            if name.lower() != b"content-length":
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        return None
