# syntax=docker/dockerfile:1
# =============================================================================
# Edge image (interview-edge:local): builds the SPA and serves it with Caddy,
# which also reverse-proxies /api/* and /ws/* to the API (infra/caddy/Caddyfile).
#
#   docker compose build edge          # preferred (passes the edgeconf context)
#   docker build --build-context edgeconf=infra/caddy -t interview-edge:local apps/web
#
# Build args:
#   SOURCEMAPS=strip|keep   ship Vite source maps publicly (default: strip)
# =============================================================================

ARG NODE_IMAGE=node:22-bookworm-slim
ARG CADDY_IMAGE=caddy:2.11.7-alpine

# ---------------------------------------------------------------------------
# 1. Build the SPA
# ---------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS build
WORKDIR /app
ENV CI=true \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_UPDATE_NOTIFIER=false

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm,sharing=locked npm ci

COPY . .
ARG SOURCEMAPS=strip
RUN npm run build \
    && if [ "$SOURCEMAPS" = "strip" ]; then find dist -type f -name '*.map' -delete; fi \
    && node docker/precompress.mjs dist

# ---------------------------------------------------------------------------
# 2. Runtime: Caddy as an unprivileged user
# ---------------------------------------------------------------------------
FROM ${CADDY_IMAGE} AS edge

# Ports 3200/3443 need no privileges, so the binary's cap_net_bind_service file
# capability is dropped too (it would also block exec under `cap_drop: ALL`).
RUN addgroup -S -g 10001 caddy \
    && adduser -S -D -H -u 10001 -G caddy -h /data -s /sbin/nologin caddy \
    && setcap -r /usr/bin/caddy \
    && mkdir -p /run/caddy /data/caddy /config/caddy /srv/www \
    && chown -R caddy:caddy /run/caddy /data /config \
    && chmod 0700 /run/caddy

# Baked-in copy for non-compose deployments; compose bind-mounts the live file
# over it so `make edge-reload` picks up edits without a rebuild.
COPY --from=edgeconf --chown=root:root --chmod=0644 Caddyfile /etc/caddy/Caddyfile
COPY --from=build --chown=root:root /app/dist /srv/www

ENV EDGE_HTTP_PORT=3200 \
    EDGE_HTTPS_PORT=3443 \
    API_UPSTREAM=127.0.0.1:8200 \
    EDGE_SITE_ROOT=/srv/www

USER caddy
EXPOSE 3200 3443

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null "http://127.0.0.1:${EDGE_HTTP_PORT}/__edge/health" || exit 1

CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]

LABEL org.opencontainers.image.title="interview-edge" \
      org.opencontainers.image.description="AI Interview Platform edge: SPA + reverse proxy (Caddy)"
