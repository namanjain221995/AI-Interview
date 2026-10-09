# syntax=docker/dockerfile:1.7
# Sandbox runtime for Python and SQL (SQLite) submissions.
#
# Contains the interpreter, the generic job runner and the harnesses. Built by
# `docker compose --profile runtimes build`; containers are created only by the
# sandbox service (read-only rootfs, no network, non-root, all caps dropped).
FROM python:3.13-slim-trixie

LABEL org.opencontainers.image.title="interview-sandbox-python" \
      interview.sandbox.runtime="python" \
      interview.sandbox.languages="python,sql" \
      interview.sandbox.protocol="1"

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/tmp \
    TMPDIR=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8

COPY --chmod=0444 runner.py check.py harness.py sql_harness.py /opt/sandbox/

# Dedicated unprivileged user, no setuid/setgid binaries, no shells.
RUN chmod 0755 /opt/sandbox \
 && chmod 0444 /opt/sandbox/* \
 && groupadd --gid 10001 runner \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin runner \
 && python3 -I -c "import ctypes, json, selectors, sqlite3; print('sqlite', sqlite3.sqlite_version)" \
 && find / -xdev -perm /6000 -type f -exec chmod a-s {} + \
 && rm -f /bin/sh /bin/bash /bin/dash /bin/rbash /usr/bin/sh /usr/bin/bash /usr/bin/dash /usr/bin/rbash

USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["/usr/local/bin/python3", "-I", "-B", "/opt/sandbox/runner.py"]
