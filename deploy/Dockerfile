# syntax=docker/dockerfile:1.7
#
# Chapter & Verse engine image. One Dockerfile for both hosts:
#   linux/arm64  Oracle A1 (compose, port 8000)
#   linux/amd64  Hugging Face Space standby (PORT=7860)
#
# Build context is the repository root:
#   docker buildx build -f deploy/Dockerfile --platform linux/arm64,linux/amd64 -t IMAGE:TAG .
# The ignore file for this context is deploy/Dockerfile.dockerignore (BuildKit reads
# <Dockerfile>.dockerignore next to the Dockerfile in preference to a root .dockerignore).
#
# No artefacts are baked in. At start, deploy/entrypoint.sh runs fetch_artefacts.py, which
# downloads and verifies the text store and router indexes into /data, then starts uvicorn.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.6

FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------- build
FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src

# Dependencies first, so source edits do not invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# The project itself, installed non-editable so the runtime stage needs no source tree.
COPY README.md LICENSE NOTICE ./
COPY engine ./engine
# uv keys a local package's cached build on pyproject.toml only, so without this flag the cache mount reuses a stale wheel (found 20 Nov 2026: the image ran code from an older commit).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --reinstall-package chapterverse

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.title="chapter-and-verse-engine" \
      org.opencontainers.image.description="Chapter & Verse: UK legislation answers with verified citations" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

# uid 1000 is also the uid the Hugging Face Space runtime uses.
RUN groupadd --gid 1000 app \
 && useradd --uid 1000 --gid 1000 --home-dir /home/app --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data \
 && chown 1000:1000 /data

COPY --from=build /opt/venv /opt/venv
COPY --chmod=0755 deploy/entrypoint.sh /opt/app/entrypoint.sh
COPY --chmod=0644 deploy/fetch_artefacts.py /opt/app/fetch_artefacts.py

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    DATA_DIR=/data \
    LEGAL_DATA_ROOT=/data \
    STORE_DIR=/data \
    MAX_QUERY_BYTES=4096

USER 1000:1000
WORKDIR /home/app
EXPOSE 8000 7860

# The first start downloads about 4 GB before uvicorn listens, hence the long start period.
HEALTHCHECK --interval=30s --timeout=5s --start-period=900s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/api/health', timeout=4)"]

ENTRYPOINT ["/opt/app/entrypoint.sh"]
