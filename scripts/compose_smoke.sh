#!/usr/bin/env bash
# End-to-end smoke test for the StaffDeck server deployment (Docker Compose).
#
# This is the M1 acceptance gate: it builds and starts the full stack, waits for
# readiness, and asserts the three contract endpoints plus role-lease
# exclusivity. Exit code 0 means M1 is delivered.
#
# Usage:
#   bash scripts/compose_smoke.sh          # build if needed, then verify
#   bash scripts/compose_smoke.sh --rebuild
#   PORT=8080 bash scripts/compose_smoke.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_DIR="${REPO_ROOT}/packaging/docker"
PORT="${PORT:-5173}"
BASE="http://localhost:${PORT}"
REBUILD=""
WAIT_SECONDS="${WAIT_SECONDS:-240}"

if [[ "${1:-}" == "--rebuild" ]]; then
  REBUILD="--build"
fi

log() { printf '[smoke] %s\n' "$*"; }
fail() {
  printf '[smoke] FAIL: %s\n' "$*" >&2
  printf '\n--- compose ps ---\n' >&2
  (cd "$COMPOSE_DIR" && docker compose ps) >&2 || true
  printf '\n--- recent logs ---\n' >&2
  (cd "$COMPOSE_DIR" && docker compose logs --tail 60) >&2 || true
  exit 1
}

cd "$COMPOSE_DIR"

if [[ ! -f .env ]]; then
  log "creating .env from .env.example (edit it for real deployments)"
  cp .env.example .env
  # Inject fixed throwaway values so the stack can boot unattended.
  # These MUST be stable across runs: PostgreSQL only applies POSTGRES_PASSWORD
  # when it initialises the data directory, so a fresh random password would
  # fail authentication against an existing pgdata volume.
  sed -i.bak "s|^STAFFDECK_PG_PASSWORD=.*|STAFFDECK_PG_PASSWORD=staffdeck-smoke|" .env
  sed -i.bak "s|^STAFFDECK_APP_SECRET=.*|STAFFDECK_APP_SECRET=staffdeck-smoke-secret-change-me|" .env
  rm -f .env.bak
fi

# PostgreSQL only applies POSTGRES_PASSWORD when it initialises the data
# directory. Reusing a volume from an earlier run with a different password
# fails auth in a way that looks like a hang, so mention the reset up front.
if docker volume inspect docker_pgdata >/dev/null 2>&1; then
  log "note: an existing pgdata volume was found; if startup stalls on auth,"
  log "      reset it with: (cd packaging/docker && docker compose down -v)"
fi

log "starting stack (port ${PORT})"
docker compose up -d ${REBUILD}

log "waiting for /api/health (timeout ${WAIT_SECONDS}s)"
deadline=$((SECONDS + WAIT_SECONDS))
health=""
while (( SECONDS < deadline )); do
  if health=$(curl -fsS --max-time 3 "${BASE}/api/health" 2>/dev/null); then
    break
  fi
  sleep 3
done
[[ -n "$health" ]] || fail "health endpoint never became ready at ${BASE}/api/health"

log "health: ${health}"
case "$health" in
  *'"status":"ok"'*) ;;
  *) fail "unexpected health payload: ${health}" ;;
esac

log "checking OpenAPI document"
openapi_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${BASE}/api/v1/openapi.json")
[[ "$openapi_code" == "200" ]] || fail "/api/v1/openapi.json returned HTTP ${openapi_code}"
path_count=$(curl -s --max-time 10 "${BASE}/api/v1/openapi.json" \
  | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("paths", {})))' 2>/dev/null || echo 0)
[[ "$path_count" -gt 0 ]] || fail "OpenAPI document exposes no paths"
log "openapi paths: ${path_count}"

log "checking admin + user frontend shells"
for path in /enterprise /workspace; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${BASE}${path}")
  [[ "$code" == "200" ]] || fail "${path} returned HTTP ${code}"
  log "  ${path} -> ${code}"
done

log "checking role leases are held and renewed"
for role in api worker connector; do
  ttl=$(docker compose exec -T redis redis-cli PTTL "staffdeck:lease:${role}" | tr -d '\r')
  [[ "$ttl" =~ ^[0-9]+$ ]] || fail "lease for ${role} is missing (PTTL=${ttl})"
  log "  lease:${role} pttl=${ttl}ms"
done

log "checking a second api instance is rejected by the lease"
second_out=$(docker compose run --rm --no-deps -T \
  -e PROCESS_ROLE=api -e STAFFDECK_RUN_MIGRATIONS=0 api 2>&1 || true)
if grep -q "holds the lease" <<<"$second_out"; then
  log "  second api instance correctly rejected"
else
  fail "a second api instance was NOT rejected by the role lease"
fi

log "all checks passed"
printf '\nStaffDeck server stack is up at %s\n' "$BASE"
printf '  admin console: %s/enterprise\n' "$BASE"
printf '  user workspace: %s/workspace\n' "$BASE"
printf '  openapi:        %s/api/v1/openapi.json\n' "$BASE"
printf 'Stop it with: (cd packaging/docker && docker compose down)\n'
