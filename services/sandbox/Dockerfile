# syntax=docker/dockerfile:1.7
# Sandbox service: authenticates execution requests and runs each one in a
# fresh, locked-down container (images: runtimes/<lang>, built with
# `docker compose --profile runtimes build`).
#
# The container starts as root only so the entrypoint can read the GID of the
# mounted /var/run/docker.sock; it then drops to uid/gid 10002 with that GID
# as its single supplementary group before serving (sandbox_service/__main__.py).
FROM python:3.12-slim-trixie

LABEL org.opencontainers.image.title="interview-sandbox" \
      org.opencontainers.image.description="Secure code execution service for Swayam"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANG=C.UTF-8

RUN groupadd --system --gid 10002 sandbox \
 && useradd --system --uid 10002 --gid 10002 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin sandbox

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt \
 && find / -xdev -perm /6000 -type f -exec chmod a-s {} + 2>/dev/null || true

COPY sandbox_service ./sandbox_service
RUN python -m compileall -q sandbox_service

EXPOSE 8220
STOPSIGNAL SIGTERM
ENTRYPOINT ["python", "-m", "sandbox_service"]
