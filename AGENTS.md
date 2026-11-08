# Repository Guidelines

## Project Structure & Module Organization

StaffDeck is a server-first platform: a Python 3.11+ FastAPI service with a React/TypeScript console, deployed as containers behind nginx and reached through a browser. Backend application code lives in `backend/app/`; the production entry point is `backend/server_app.py`. `backend/single_port_app.py` and `desktop_launcher.py` exist only for local development, and packaged desktop installers are legacy. Backend tests are in `backend/tests/`, and the supported conversation runtime is Harness v2. Frontend code is in `frontend-enterprise/src/`, with static assets in `frontend-enterprise/public/` and colocated `*.test.ts` or `*.test.tsx` files. Use `scripts/` for development lifecycle tooling and `packaging/docker/` for the supported deployment assets.

## Build, Test, and Development Commands

- `python3 -m venv backend/.venv && backend/.venv/bin/python -m pip install -e "backend[dev]"` installs backend and test dependencies.
- `npm --prefix frontend-enterprise ci` installs the locked frontend dependencies.
- `scripts/dev_up.sh --detach` builds the frontend and starts the single-port app for local development; use `scripts/dev_status.sh` and `scripts/dev_down.sh` to inspect or stop it.
- `cd packaging/docker && docker compose up -d --build` runs the server deployment (PostgreSQL, Redis, api, worker, connector, nginx); `bash scripts/compose_smoke.sh` verifies it end to end. See `docs/server-deployment.md`.
- `backend/.venv/bin/python -m pytest backend/tests` runs the backend suite.
- `backend/.venv/bin/ruff check backend` checks Python style.
- `npm --prefix frontend-enterprise test` runs Vitest; `npm --prefix frontend-enterprise run build` performs TypeScript checking and the production Vite build.
- Run `i18n:check` and `config:check` from `frontend-enterprise` when changing UI text or Vite environment usage.

## Coding Style & Naming Conventions

Python uses four-space indentation, type hints, `snake_case` functions/modules, and `PascalCase` classes; Ruff targets Python 3.11 with a 100-character line limit. TypeScript is strict and follows the existing two-space, single-quote, semicolon style. Name React components in `PascalCase`, hooks with `use...`, and tests after the unit under test. Prefer the `@/` alias for frontend imports.

## Testing Guidelines

Name Python tests `test_*.py` and frontend tests `*.test.ts(x)`. Add focused regression tests for behavior changes, especially permissions, persistence, streaming, and channel routing. No numeric coverage threshold is configured; changed paths should still be exercised. For UI changes, also verify the affected route and user role in a browser.

## Commit & Pull Request Guidelines

Follow the history’s concise Conventional Commit pattern, such as `feat(channels): add binding status` or `fix: reject unsafe avatar URLs`. Keep commits focused. Pull requests should explain intent and risk, link relevant issues, list tests run, and identify routes and roles used for UI validation. Include screenshots for visible changes and preserve unrelated worktree changes.

## Security & Configuration

Copy `backend/.env.example` to `backend/.env` for local development; never commit secrets or channel credentials. Use strong `APP_SECRET` values and least-privilege external credentials. Production runs on PostgreSQL with Alembic migrations; SQLite is for development and tests only, and every model change needs a migration revision.

## Agent skills

### Issue tracker

Do not create or manage issues for this repository. See `docs/agents/issue-tracker.md`.

### Triage labels

Issue triage labels are not used. See `docs/agents/triage-labels.md`.

### Domain docs

Use the single-context documentation layout. See `docs/agents/domain.md`.
