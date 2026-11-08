#!/bin/sh
# Docker entrypoint for StaffDeck server containers.
# Runs Alembic migrations when STAFFDECK_RUN_MIGRATIONS=1 (api role only),
# then starts the uvicorn server.
set -e

if [ "${STAFFDECK_RUN_MIGRATIONS}" = "1" ]; then
  echo "Running database migrations..."
  python -m app.db.migrations_cli upgrade
fi

# uvicorn only trusts X-Forwarded-* from 127.0.0.1 by default. nginx runs in
# a separate container, so compose sets FORWARDED_ALLOW_IPS to the Docker
# subnet; without it every login would appear to come from nginx's address.
exec python -m uvicorn server_app:app --host 0.0.0.0 --port 5173
