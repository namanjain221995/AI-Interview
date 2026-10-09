# syntax=docker/dockerfile:1
#
# Kokoro-82M TTS service (CPU, onnxruntime). Multi-arch: built and verified on
# linux/arm64 (DGX Spark); every dependency also ships linux/amd64 wheels.
#
#   docker compose up -d --build tts
#
# Model files are copied from ./models when present, otherwise downloaded from
# the kokoro-onnx release; either way their SHA-256 must match the pinned values
# (scripts/fetch_models.py). The image then carries a variant of the model that
# also outputs per-phoneme durations (scripts/add_duration_output.py) so the
# viseme timeline uses the model's own phoneme timings.

ARG PYTHON_IMAGE=python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f

FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# ---------------------------------------------------------------- dependencies
# Hash-pinned (exported from uv.lock); espeak-ng comes as a shared library plus
# its data inside the espeakng-loader wheel, no system packages needed.
FROM base AS deps
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-deps --require-hashes -r /tmp/requirements.txt \
 && /opt/venv/bin/python -c "import ctypes, espeakng_loader, onnxruntime, kokoro_onnx; ctypes.CDLL(espeakng_loader.get_library_path()); print('espeak-ng', espeakng_loader.get_library_path(), 'onnxruntime', onnxruntime.__version__)"

# ---------------------------------------------------------------- model files
FROM base AS models
COPY scripts/fetch_models.py /build/fetch_models.py
# models/ always exists (models/README.md is tracked); the large files are optional.
COPY models/ /build/models/
RUN python /build/fetch_models.py /build/models

# Add the "duration" output (audio is unchanged). onnx is only needed here.
FROM base AS durations
COPY requirements-build.txt /tmp/requirements-build.txt
RUN pip install --no-deps --require-hashes -r /tmp/requirements-build.txt
COPY scripts/add_duration_output.py /build/add_duration_output.py
COPY --from=models /build/models/kokoro-v1.0.onnx /build/kokoro-v1.0.onnx
RUN python /build/add_duration_output.py /build/kokoro-v1.0.onnx /build/kokoro-v1.0-durations.onnx

# ---------------------------------------------------------------- runtime
FROM base AS runtime
LABEL org.opencontainers.image.title="interview-tts" \
      org.opencontainers.image.description="Kokoro-82M text-to-speech with Oculus viseme timelines (CPU)"

ARG APP_UID=10001
RUN groupadd --system --gid "${APP_UID}" tts \
 && useradd --system --uid "${APP_UID}" --gid tts --create-home --home-dir /home/tts --shell /usr/sbin/nologin tts

COPY --from=deps /opt/venv /opt/venv
COPY --from=durations /build/kokoro-v1.0-durations.onnx /app/models/kokoro-v1.0-durations.onnx
COPY --from=models /build/models/voices-v1.0.bin /app/models/voices-v1.0.bin
COPY tts_service /app/tts_service
RUN /opt/venv/bin/python -m compileall -q /app/tts_service \
 && chmod -R a+rX,go-w /app

WORKDIR /app
ENV PATH=/opt/venv/bin:${PATH} \
    TTS_HOST=0.0.0.0 \
    TTS_PORT=8210 \
    TTS_MODEL_PATH=/app/models/kokoro-v1.0-durations.onnx \
    TTS_VOICES_PATH=/app/models/voices-v1.0.bin

USER tts
EXPOSE 8210
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=10 \
  CMD ["python", "-c", "import os, sys, urllib.request; port = os.environ.get('TTS_PORT', '8210'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3).status == 200 else 1)"]
CMD ["python", "-m", "tts_service"]
