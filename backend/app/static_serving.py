"""前后端同源部署时托管前端静态资源，single_port_app 与 server_app 共用。

从 single_port_app.py 的静态挂载与 SPA 回退段落原样抽出，使服务端进程也能托管
管理端/用户端前端，而无需引入桌面端专有的代理（PilotDeck / site-chat / LLM relay）。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from app import paths

logger = logging.getLogger("staffdeck.static")
ROOT_DIR = paths.resource_dir()
# frozen: dist 被收集到 _MEIPASS/frontend-enterprise/dist
# dev:    resource_dir()==backend/，需回到仓库根找 frontend-enterprise
ENTERPRISE_DIST = (
    ROOT_DIR / "frontend-enterprise" / "dist"
    if paths.is_frozen()
    else ROOT_DIR.parent / "frontend-enterprise" / "dist"
)
SPA_INDEX_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
FRONTEND_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".mjs": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".wasm": "application/wasm",
}


class FrontendStaticFiles(StaticFiles):
    """托管 Vite 构建产物，并按扩展名强制稳定的 MIME 类型，规避 Windows 上的类型误判。"""

    def file_response(
        self,
        full_path: os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> FileResponse:
        """返回静态文件响应，按扩展名覆写 Content-Type；检测类型与预期不符时记告警。"""
        response = super().file_response(full_path, stat_result, scope, status_code)
        suffix = Path(full_path).suffix.lower()
        media_type = FRONTEND_CONTENT_TYPES.get(suffix)
        if media_type:
            detected_media_type = response.headers.get("Content-Type")
            response.headers["Content-Type"] = media_type
            detected_base_type = (detected_media_type or "").partition(";")[0].strip().lower()
            allowed_base_types = {media_type.partition(";")[0].lower()}
            if suffix in {".js", ".mjs"}:
                allowed_base_types.add("application/javascript")
            if detected_base_type not in allowed_base_types:
                logger.warning(
                    "Corrected frontend MIME suffix=%s detected=%s forced=%s",
                    suffix,
                    detected_media_type,
                    media_type,
                )
        return response


def spa_index_response(index_path: Path) -> FileResponse:
    """构造 SPA 入口 index.html 的响应，并附禁止缓存的头，保证发版后立即生效。"""
    return FileResponse(index_path, headers=SPA_INDEX_HEADERS)


def mount_frontend(app: FastAPI) -> None:
    """挂载前端静态资源与 SPA 回退路由：多前缀 /assets、品牌图标及各页面入口。"""
    app.mount(
        "/assets",
        FrontendStaticFiles(directory=ENTERPRISE_DIST / "assets", check_dir=False),
        name="assets",
    )
    app.mount(
        "/enterprise/assets",
        FrontendStaticFiles(directory=ENTERPRISE_DIST / "assets", check_dir=False),
        name="enterprise-assets",
    )
    app.mount(
        "/chat/assets",
        FrontendStaticFiles(directory=ENTERPRISE_DIST / "assets", check_dir=False),
        name="chat-assets",
    )
    app.mount(
        "/workspace/assets",
        FrontendStaticFiles(directory=ENTERPRISE_DIST / "assets", check_dir=False),
        name="workspace-assets",
    )

    @app.get("/", include_in_schema=False)
    def root_redirect() -> RedirectResponse:
        """把根路径重定向到用户端聊天入口 /chat/。"""
        return RedirectResponse(url="/chat/")

    @app.get("/favicon.ico", include_in_schema=False)
    @app.get("/favicon.png", include_in_schema=False)
    @app.get("/staffdeck-icon.png", include_in_schema=False)
    def brand_icon(request: Request) -> FileResponse:
        """按请求路径返回品牌图标；目标文件不存在时回退到 dist 根目录的 favicon.ico。"""
        # 品牌图标：从前端 dist 根目录 serve（favicon.ico/png、apple-touch-icon）
        name = request.url.path.lstrip("/")
        target = ENTERPRISE_DIST / name
        if not target.exists():
            target = ENTERPRISE_DIST / "favicon.ico"
        return FileResponse(target)

    @app.get("/enterprise", include_in_schema=False)
    @app.get("/enterprise/{path:path}", include_in_schema=False)
    def enterprise_app(path: str = "") -> FileResponse:
        """管理端 SPA 回退：任意 /enterprise 子路径都返回 index.html，交由前端路由处理。"""
        return spa_index_response(ENTERPRISE_DIST / "index.html")

    @app.get("/login", include_in_schema=False)
    @app.get("/chat", include_in_schema=False)
    @app.get("/chat/{path:path}", include_in_schema=False)
    @app.get("/workspace", include_in_schema=False)
    @app.get("/workspace/{path:path}", include_in_schema=False)
    def chat_app(path: str = "") -> FileResponse:
        """用户端 SPA 回退：/login、/chat、/workspace 及其子路径统一返回 index.html。"""
        return spa_index_response(ENTERPRISE_DIST / "index.html")
