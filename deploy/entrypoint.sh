#!/bin/sh
# Container entrypoint: verify (and if needed fetch) the artefacts, then serve.
# fetch_artefacts.py exits non-zero on any checksum mismatch, so the server never starts on
# unverified data. One uvicorn worker: the router, store and quota state are shared in-process.
set -eu

DATA_DIR="${DATA_DIR:-/data}"
RAM_DIR="${RAM_DIR:-/ram}"

served_from_ram=0
if [ -d "$RAM_DIR" ] && python /opt/app/fetch_artefacts.py --data-dir "$DATA_DIR" --marker-only >/dev/null 2>&1; then
  if cp -R "$DATA_DIR"/. "$RAM_DIR"/ 2>/dev/null && python /opt/app/fetch_artefacts.py --data-dir "$RAM_DIR" --verify-only >/dev/null 2>&1; then
    STORE_DIR="$RAM_DIR"
    ROUTER_INDEX="$RAM_DIR/index"
    CONCEPTS_INDEX="$RAM_DIR/concepts"
    served_from_ram=1
  fi
fi

if [ "$served_from_ram" -eq 0 ]; then
  python /opt/app/fetch_artefacts.py --data-dir "$DATA_DIR"
  STORE_DIR="$DATA_DIR"
  ROUTER_INDEX="$DATA_DIR/index"
  CONCEPTS_INDEX="$DATA_DIR/concepts"
  if [ -d "$RAM_DIR" ] && cp -R "$DATA_DIR"/. "$RAM_DIR"/ 2>/dev/null && python /opt/app/fetch_artefacts.py --data-dir "$RAM_DIR" --verify-only >/dev/null 2>&1; then
    STORE_DIR="$RAM_DIR"
    ROUTER_INDEX="$RAM_DIR/index"
    CONCEPTS_INDEX="$RAM_DIR/concepts"
  else
    echo "warning: tmpfs copy or verification failed; falling back to volume paths" >&2
  fi
fi

if [ -f "$STORE_DIR/symspell/dictionary.txt" ]; then
  SYMSPELL_DICT="$STORE_DIR/symspell/dictionary.txt"
  export SYMSPELL_DICT
else
  echo "warning: $STORE_DIR/symspell/dictionary.txt not found; running without prebuilt dictionary" >&2
fi

export STORE_DIR ROUTER_INDEX CONCEPTS_INDEX

exec uvicorn chapterverse.app:app \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT:-8000}" \
  --workers 1 \
  --limit-concurrency "${LIMIT_CONCURRENCY:-64}" \
  --timeout-graceful-shutdown 20 \
  --no-server-header \
  --no-access-log \
  --log-level "${LOG_LEVEL:-info}"
