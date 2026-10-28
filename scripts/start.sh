#!/bin/sh
# Container entrypoint: apply migrations, then start the web server.
set -e
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips="*" --no-server-header
