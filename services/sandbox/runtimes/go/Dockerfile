# syntax=docker/dockerfile:1.7
# Sandbox runtime for Go submissions (Go 1.27, CGO disabled).
#
# A cold `go build` compiles the runtime and every imported std package
# (~3.5 s, 12 s CPU). The image ships a GOCACHE pre-warmed with the std
# packages solutions use and with the harness package (sandbox/sbxharness);
# it stays read-only at run time (Go ignores cache write failures), so a
# candidate build only compiles its own package and links: ~0.2 s at 1 CPU.
FROM golang:1.27-trixie AS toolchain

FROM debian:trixie-slim

LABEL org.opencontainers.image.title="interview-sandbox-go" \
      interview.sandbox.runtime="go" \
      interview.sandbox.languages="go" \
      interview.sandbox.protocol="1"

COPY --from=toolchain /usr/local/go /usr/local/go

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/*

# Build configuration shared by the cache warm-up below and every sandboxed
# build (the warmed entries are only reused when these values match).
ENV PATH=/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    HOME=/tmp \
    TMPDIR=/tmp \
    GOTOOLCHAIN=local \
    GOFLAGS="-mod=mod -trimpath -buildvcs=false" \
    GOPROXY=off \
    GOSUMDB=off \
    GOENV=off \
    CGO_ENABLED=0 \
    GOCACHE=/opt/sandbox/go/cache \
    GOPATH=/tmp/go \
    GOMODCACHE=/tmp/go/pkg/mod \
    GOTMPDIR=/tmp \
    XDG_CONFIG_HOME=/opt/sandbox/go/config

COPY --chmod=0444 runner.py /opt/sandbox/runner.py
COPY go.mod /opt/sandbox/go/go.mod
COPY sbxharness/harness.go /opt/sandbox/go/sbxharness/harness.go
COPY warm/ /tmp/warm-src/

# Warm the cache exactly as the sandbox builds: module "sandbox" in /tmp/work,
# the harness package next to package main. A plain program covers run mode.
RUN set -eux; \
    mkdir -p /opt/sandbox/go/config/go/telemetry; \
    echo off > /opt/sandbox/go/config/go/telemetry/mode; \
    mkdir -p /tmp/work/sbxharness; cd /tmp/work; \
    cp /opt/sandbox/go/go.mod .; cp /opt/sandbox/go/sbxharness/harness.go sbxharness/; \
    cp /tmp/warm-src/main.go warm.go; \
    go build -ldflags="-s -w" -o /tmp/warm-prog .; \
    rm warm.go; cp /tmp/warm-src/solution.go /tmp/warm-src/entry.go .; \
    go build -ldflags="-s -w" -o prog .; \
    echo '{"tests":[{"i":0,"args":[[2,7,11,15],9]}]}' | ./prog 3>&1 | grep -q '"actual":\[0,1\]'; \
    cd / && rm -rf /tmp/work /tmp/warm-src /tmp/warm-prog /tmp/go /tmp/go-build* /tmp/.cache; \
    chmod -R a+rX /opt/sandbox

RUN groupadd --gid 10001 runner \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin runner \
 && find / -xdev -perm /6000 -type f -exec chmod a-s {} + \
 && rm -f /bin/sh /bin/bash /bin/dash /bin/rbash /usr/bin/sh /usr/bin/bash /usr/bin/dash /usr/bin/rbash

USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["/usr/bin/python3", "-I", "-B", "/opt/sandbox/runner.py"]
