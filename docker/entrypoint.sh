#!/bin/sh
# Container entrypoint:
#   1. Run schema migration + admin bootstrap (idempotent — no-op on subsequent starts).
#   2. exec the CMD (gunicorn) so signals reach it cleanly.
set -e

if [ -z "${TASKBOARD_SECRET_KEY:-}" ]; then
  echo "[entrypoint] WARNING: TASKBOARD_SECRET_KEY not set; sessions will be invalidated on every restart" >&2
fi

echo "[entrypoint] running bootstrap (idempotent)"
python /app/backend/cli.py bootstrap || true

echo "[entrypoint] starting: $*"
exec "$@"
