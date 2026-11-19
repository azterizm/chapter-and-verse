#!/bin/sh
# Container entrypoint: verify (and if needed fetch) the artefacts, then serve.
# fetch_artefacts.py exits non-zero on any checksum mismatch, so the server never starts on
# unverified data. One uvicorn worker: the router, store and quota state are shared in-process.
set -eu

python /opt/app/fetch_artefacts.py

exec uvicorn chapterverse.app:app \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT:-8000}" \
  --workers 1 \
  --limit-concurrency "${LIMIT_CONCURRENCY:-64}" \
  --timeout-graceful-shutdown 20 \
  --no-server-header \
  --no-access-log \
  --log-level "${LOG_LEVEL:-info}"
