# syntax=docker/dockerfile:1.7
#
# One image for the API (uvicorn app.main:app), the background worker
# (python -m app.worker) and migrations (alembic upgrade head); docker-compose
# selects the command. Built natively for the host (arm64 on the DGX Spark).
#
# * dependencies come only from uv.lock (`uv sync --frozen --no-dev`);
# * runs as the unprivileged uid/gid 10001; code is root-owned and read-only;
# * /srv/interview-data (the `appdata` volume: local file storage, no S3) is
#   owned by uid 10001 so a fresh named volume inherits that ownership;
# * no secrets are baked in: configuration arrives at runtime via env_file.
#
# The working directory must stay at least two levels deep (/srv/app):
# app.core.config derives REPO_ROOT from its own path.

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.12.5

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# --------------------------------------------------------------------- build
FROM python:${PYTHON_VERSION}-slim AS build

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /srv/app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ------------------------------------------------------------------- runtime
FROM python:${PYTHON_VERSION}-slim AS runtime

# HOME=/tmp: libraries that cache per-user state (onnxruntime telemetry id)
# need a writable home; the code directory stays read-only.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/srv/app \
    PATH=/opt/venv/bin:$PATH \
    HOME=/tmp \
    STORAGE_LOCAL_ROOT=/srv/interview-data/storage

# ffmpeg/ffprobe: the `recording.finalize` job remuxes the browser's
# MediaRecorder segments into one seekable WebM/MP4 (stream copy only, never a
# transcode). Without it the job falls back to plain concatenation.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 interview \
    && useradd --system --uid 10001 --gid 10001 --home-dir /srv/app --no-create-home \
       --shell /usr/sbin/nologin interview \
    && mkdir -p /srv/interview-data/storage \
    && chown -R 10001:10001 /srv/interview-data \
    && chmod 0700 /srv/interview-data /srv/interview-data/storage

COPY --from=build /opt/venv /opt/venv

WORKDIR /srv/app
COPY alembic.ini ./
COPY alembic ./alembic
COPY models ./models
COPY app ./app
RUN python -m compileall -q app alembic \
    && python -c "import app.core.config, app.db.models"

USER 10001:10001
EXPOSE 8200 8201

CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8200"]
