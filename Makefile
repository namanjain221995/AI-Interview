# =============================================================================
# AI Interview Platform — developer and operator entry points.
# `make` (or `make help`) lists every target. See LOCAL_SETUP.md / DEPLOYMENT.md.
#
# Safety: `make down` never removes volumes (no `-v`): database, Redis, uploaded
# files, certificates and metrics survive. Destructive targets ask first.
# =============================================================================

SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
MAKEFLAGS += --no-print-directory

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
API := $(ROOT)/apps/api
WEB := $(ROOT)/apps/web
E2E := $(ROOT)/tests/e2e
COMPOSE := docker compose --project-directory $(ROOT) -f $(ROOT)/docker-compose.yml
UV := uv run
CADDY_IMAGE := caddy:2.11.7-alpine
PROM_IMAGE := prom/prometheus:v3.2.1
SHELLCHECK_IMAGE := koalaman/shellcheck:v0.10.0

SERVICE ?=
FILE ?=
ARGS ?=

APP_SERVICES := api worker tts sandbox edge
MONITORING := prometheus grafana postgres-exporter redis-exporter
RUNTIMES := runtime-python runtime-node runtime-java runtime-go runtime-cpp

.PHONY: help
help: ## List targets
	@awk 'BEGIN { FS = ":.*## " } /^[a-zA-Z0-9_.-]+:.*## / { printf "  %-18s %s\n", $$1, $$2 } /^##@/ { printf "\n%s\n", substr($$0, 5) }' $(MAKEFILE_LIST)

##@ Setup
.PHONY: setup env deps download-assets optimize-avatar doctor
setup: env download-assets deps optimize-avatar ## First-time setup: .env secrets, model/avatar assets, dependencies
	@echo "Setup complete. Next: make doctor && make sandbox-images && make up && make seed"

env: ## Create .env with fresh secrets (never overwrites an existing one)
	@bash $(ROOT)/scripts/setup.sh

doctor: ## Preflight this node: tooling, .env, the configured AI endpoints, assets, runtime images, deep readiness
	@$(ROOT)/scripts/doctor.sh

deps: ## Install API (uv) and web/e2e (npm) dependencies when missing
	cd $(API) && uv sync --frozen
	@if [ ! -d $(WEB)/node_modules ]; then cd $(WEB) && npm ci; else echo "apps/web/node_modules present (run 'npm ci' there to reinstall)"; fi
	@if [ -f $(E2E)/package.json ] && [ ! -d $(E2E)/node_modules ]; then cd $(E2E) && npm ci; fi

download-assets: ## Download Kokoro, Silero VAD and the avatar source (sha256-verified, idempotent)
	@$(ROOT)/scripts/download_assets.sh

optimize-avatar: ## Build the optimized interviewer avatar from the MPFB source when missing
	@if [ -f $(WEB)/public/avatars/interviewer.glb ]; then echo "apps/web/public/avatars/interviewer.glb present"; \
	else cd $(WEB) && npm run optimize-avatar; fi

##@ Stack (docker compose, project "interview")
.PHONY: up down ps logs build build-edge sandbox-images restart monitoring-up
up: ## Build missing images, start the whole stack and wait until healthy
	$(COMPOSE) up -d
	@$(ROOT)/scripts/wait_for_healthy.sh --timeout 600

down: ## Stop and remove containers (volumes are kept)
	$(COMPOSE) down --remove-orphans

ps: ## Show service status
	$(COMPOSE) ps

logs: ## Follow logs (SERVICE=api for one service)
	$(COMPOSE) logs -f --tail=200 $(SERVICE)

build: ## Build all application images
	$(COMPOSE) build $(APP_SERVICES)

build-edge: ## Rebuild only the edge image (SPA + Caddy)
	$(COMPOSE) build edge

sandbox-images: ## Build the per-language code execution runtime images
	$(COMPOSE) --profile runtimes build $(RUNTIMES)

restart: ## Restart services (SERVICE=api) or the application tier
	$(COMPOSE) restart $(or $(SERVICE),$(APP_SERVICES))

monitoring-up: ## Start only Prometheus, Grafana and the exporters
	$(COMPOSE) up -d --no-deps $(MONITORING)

##@ Database
.PHONY: migrate migrate-docker seed demo seed-docker demo-docker backup-db restore-db backup-storage restore-storage
migrate: ## Apply Alembic migrations from the host (postgres must be up)
	cd $(API) && $(UV) alembic upgrade head

migrate-docker: ## Apply migrations with the migrate container
	$(COMPOSE) run --rm migrate

seed: ## Seed the organization and staff accounts (prints the logins)
	cd $(API) && $(UV) python -m app.seed --print-credentials

demo: ## Seed demo jobs + demo candidates and print their live interview invite links
	cd $(API) && $(UV) python -m app.seed --demo --print-credentials

seed-docker: ## Seed from inside the running api container (no host uv needed)
	$(COMPOSE) exec -T api python -m app.seed --print-credentials

demo-docker: ## Demo seed from inside the running api container (no host uv needed)
	$(COMPOSE) exec -T api python -m app.seed --demo --print-credentials

backup-db: ## pg_dump to data/backups/db/ (custom format + sha256)
	@$(ROOT)/scripts/backup_db.sh

restore-db: ## Restore a dump: make restore-db FILE=data/backups/db/<file>.dump (asks for confirmation)
	@test -n "$(FILE)" || { echo "usage: make restore-db FILE=data/backups/db/<file>.dump"; exit 2; }
	@$(ROOT)/scripts/restore_db.sh "$(FILE)"

backup-storage: ## Archive the appdata volume (uploaded files) to data/backups/storage/
	@$(ROOT)/scripts/backup_storage.sh

restore-storage: ## Restore uploaded files: make restore-storage FILE=<archive> (asks for confirmation)
	@test -n "$(FILE)" || { echo "usage: make restore-storage FILE=data/backups/storage/<file>.tar.gz"; exit 2; }
	@$(ROOT)/scripts/restore_storage.sh "$(FILE)"

##@ Development servers (host processes; postgres/redis from compose)
.PHONY: api-dev worker-dev web-dev infra-up
infra-up: ## Start postgres and redis only
	$(COMPOSE) up -d postgres redis
	@$(ROOT)/scripts/wait_for_healthy.sh postgres redis

api-dev: ## Run the API with auto-reload on 127.0.0.1:8200
	cd $(API) && $(UV) uvicorn app.main:app --host 127.0.0.1 --port 8200 --reload \
		--proxy-headers --forwarded-allow-ips 127.0.0.1 --ws-ping-interval 20 --ws-ping-timeout 20

worker-dev: ## Run the background worker (metrics on :8201)
	cd $(API) && $(UV) python -m app.worker

web-dev: ## Run the Vite dev server on :5173 (proxies /api and /ws to :8200)
	cd $(WEB) && npm run dev

##@ Quality
.PHONY: test test-api test-web test-integration test-live test-e2e test-infra lint typecheck smoke ci-api ci-web ci-infra ci
test: test-api test-web ## Unit tests (API + web)

ci: ci-api ci-web ci-infra ## Everything the CI workflow runs (.github/workflows/ci.yml), locally

ci-api: ## CI: ruff, mypy, API unit tests, single Alembic head, integration tests
	cd $(API) && $(UV) ruff check . $(ROOT)/infra && $(UV) mypy app
	cd $(API) && $(UV) pytest -m "not live and not integration" -q
	cd $(API) && test "$$($(UV) alembic heads | sed '/^\s*$$/d' | wc -l)" -eq 1 || { echo 'more than one Alembic head'; exit 1; }
	cd $(API) && $(UV) pytest -m integration -q

ci-web: ## CI: typecheck, vitest, production build
	cd $(WEB) && npm run typecheck && npm test && npm run build

ci-infra: test-infra ## CI: infra checks + edge behaviour tests

test-api: ## API unit tests (live-model tests excluded)
	cd $(API) && $(UV) pytest -m "not live and not integration" $(ARGS)

test-web: ## Web unit tests (vitest)
	cd $(WEB) && npm test

test-integration: ## API integration tests against the running postgres/redis
	cd $(API) && $(UV) pytest -m integration $(ARGS)

test-live: ## Tests that call the real local AI endpoints (keep them small: shared GPUs)
	cd $(API) && $(UV) pytest -m live $(ARGS)

test-e2e: ## Playwright end-to-end tests against the edge (E2E_BASE_URL, default http://localhost:3200)
	@test -f $(E2E)/package.json || { echo "tests/e2e is not set up"; exit 2; }
	cd $(E2E) && npm test -- $(ARGS)

test-infra: edge-validate prom-check compose-check dashboards-check shellcheck ## Infra checks + edge proxy behaviour tests
	cd $(API) && $(UV) pytest $(ROOT)/infra/tests -q $(ARGS)

lint: shellcheck ## Ruff (API + infra Python), shellcheck (scripts), Caddyfile format
	cd $(API) && $(UV) ruff check . $(ROOT)/infra
	docker run --rm -v $(ROOT)/infra/caddy/Caddyfile:/etc/caddy/Caddyfile:ro $(CADDY_IMAGE) caddy fmt --diff /etc/caddy/Caddyfile >/dev/null

typecheck: ## mypy (API) and tsc (web)
	cd $(API) && $(UV) mypy app
	cd $(WEB) && npm run typecheck

smoke: ## Smoke-test a running stack (health, edge headers, targets)
	@$(ROOT)/scripts/smoke_test.sh

##@ Edge, monitoring, infra
.PHONY: edge-validate edge-reload edge-ca prom-check prom-reload dashboards dashboards-check compose-check shellcheck
edge-validate: ## caddy validate + fmt check of infra/caddy/Caddyfile
	docker run --rm -v $(ROOT)/infra/caddy/Caddyfile:/etc/caddy/Caddyfile:ro $(CADDY_IMAGE) \
		sh -c 'caddy fmt --diff /etc/caddy/Caddyfile >/dev/null && caddy validate --config /etc/caddy/Caddyfile 2>&1 | tail -n 1'

edge-reload: edge-validate ## Reload the running edge's Caddyfile without dropping WebSockets
	$(COMPOSE) exec edge caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile

edge-ca: ## Export the edge's internal root CA (to trust https://<lan-ip>:3443 on test devices)
	@$(ROOT)/scripts/export_edge_ca.sh

prom-check: ## promtool: config, rules and alert unit tests
	docker run --rm --entrypoint promtool -v $(ROOT)/infra/prometheus:/etc/prometheus:ro $(PROM_IMAGE) check config /etc/prometheus/prometheus.yml
	docker run --rm --entrypoint promtool -v $(ROOT)/infra/prometheus:/etc/prometheus:ro $(PROM_IMAGE) test rules /etc/prometheus/tests/alerts_test.yml

prom-reload: prom-check ## Reload Prometheus configuration and rules
	curl -fsS -X POST http://127.0.0.1:9191/-/reload && echo "prometheus reloaded"

dashboards: ## Regenerate Grafana dashboards from infra/grafana/generate_dashboards.py
	python3 $(ROOT)/infra/grafana/generate_dashboards.py

dashboards-check: ## Fail if the dashboard JSON is out of date
	python3 $(ROOT)/infra/grafana/generate_dashboards.py --check

compose-check: ## Validate docker-compose.yml
	$(COMPOSE) config -q && echo "docker-compose.yml is valid"

shellcheck: ## Lint scripts/*.sh with shellcheck
	docker run --rm -v $(ROOT):/mnt:ro -w /mnt $(SHELLCHECK_IMAGE) -x scripts/*.sh
