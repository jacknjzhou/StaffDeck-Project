"""Server-mode entry: API + /api/v1 + admin/user frontend hosting.

No PilotDeck proxy, no site-chat proxy, no LLM relay — those stay in the
desktop-oriented single_port_app.

Run with `server_app:app` while `backend/` is the working directory (or on
PYTHONPATH), because this module sits next to the `app` package rather than
inside it — `backend.server_app:app` does NOT resolve:
    cd backend && python -m uvicorn server_app:app --host 0.0.0.0 --port 5173
The Docker image mirrors this layout: WORKDIR /app/backend.

Process role can be narrowed via the PROCESS_ROLE env var
(all | api | worker | connector, default all).
"""

from app.main import app  # 复用同一 ASGI 应用与启动钩子
from app.static_serving import mount_frontend

mount_frontend(app)
