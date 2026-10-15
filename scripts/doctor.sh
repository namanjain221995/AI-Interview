#!/usr/bin/env bash
# Preflight for a node that runs the AI Interview stack (plan package S16): tooling, .env,
# the AI endpoints the .env points at, model assets, sandbox runtime images and, when the
# stack is up, the API's deep readiness. Required checks exit 1; optional ones only WARN.
#   scripts/doctor.sh            # everything
#   scripts/doctor.sh --no-stack # skip the running-stack checks (before `make up`)
set -euo pipefail
# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

CHECK_STACK=1
[[ "${1:-}" == "--no-stack" ]] && CHECK_STACK=0
failures=0
req_fail() { fail "$*"; failures=$((failures + 1)); }

version_ge() { # version_ge have need  -> true when have >= need (dotted numbers)
  [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" == "$2" ]]
}

info "== Tooling"
for tool in docker curl python3 make; do
  if command -v "$tool" >/dev/null 2>&1; then ok "$tool $(command -v "$tool")"; else req_fail "$tool is not installed"; fi
done
if docker compose version >/dev/null 2>&1; then
  cv="$(docker compose version --short 2>/dev/null || true)"
  if version_ge "${cv#v}" "2.17.0"; then ok "docker compose $cv"; else req_fail "docker compose $cv is older than 2.17 (additional_contexts)"; fi
else
  req_fail "docker compose v2 is not available"
fi
if docker info >/dev/null 2>&1; then ok "docker daemon reachable"; else req_fail "docker daemon not reachable (is the user in the docker group?)"; fi
if command -v uv >/dev/null 2>&1; then ok "uv $(uv --version | awk '{print $2}')"; else warn "uv not installed (needed for host-side make seed/tests; curl -LsSf https://astral.sh/uv/install.sh | sh)"; fi
if command -v node >/dev/null 2>&1; then
  nv="$(node --version)"; nv="${nv#v}"
  if version_ge "$nv" "22.12.0" || { version_ge "$nv" "20.19.0" && ! version_ge "$nv" "21.0.0"; }; then ok "node $nv"; else warn "node $nv (Vite 7 needs 20.19+ or 22.12+)"; fi
else
  warn "node not installed (needed for the web app build and e2e)"
fi

info "== .env"
ENV_FILE="$ROOT/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  req_fail ".env missing (run: make env)"
else
  mode="$(stat -c %a "$ENV_FILE" 2>/dev/null || stat -f %Lp "$ENV_FILE")"
  if [[ "$mode" == "600" ]]; then ok ".env mode 600"; else warn ".env mode $mode (expected 600: chmod 600 .env)"; fi
  for key in JWT_SECRET POSTGRES_PASSWORD REDIS_PASSWORD MEDIA_URL_SECRET SANDBOX_SHARED_SECRET SEED_PASSWORD; do
    val="$(env_get "$key")"
    if [[ -z "$val" || "$val" == "__GENERATE__" ]]; then req_fail "$key is unset or still __GENERATE__"; fi
  done
  ok "secrets present"
  public="$(env_get PUBLIC_BASE_URL)"
  case "$public" in
    https://*|http://localhost*|http://127.0.0.1*) ok "PUBLIC_BASE_URL=$public" ;;
    http://*) warn "PUBLIC_BASE_URL=$public is plain HTTP on a non-loopback name: browsers block camera/microphone and drop Secure cookies there" ;;
    *) req_fail "PUBLIC_BASE_URL is not set" ;;
  esac
  app_env="$(env_get APP_ENV)"
  [[ "$app_env" == "production" || "$app_env" == "staging" ]] && [[ "$(env_get ENABLE_API_DOCS)" == "true" ]] && warn "ENABLE_API_DOCS=true in $app_env"
  [[ "$(env_get ENABLE_TEST_HOOKS)" == "true" ]] && warn "ENABLE_TEST_HOOKS=true (test-only endpoints enabled)"
  if python3 -I "$ROOT/scripts/prom_targets.py" --check 2>/dev/null; then
    ok "prometheus AI scrape targets match .env"
  else
    warn "prometheus AI scrape targets missing or stale (make prom-targets)"
  fi
fi

probe() { # probe name url required(1/0)
  local name="$1" url="$2" required="$3" code
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 6 "$url" 2>/dev/null || true)"
  if [[ "$code" == "200" ]]; then ok "$name reachable ($url)"; return 0; fi
  if [[ "$required" == "1" ]]; then req_fail "$name NOT reachable: $url (HTTP ${code:-000}). Interviews cannot run without it."; else warn "$name not reachable: $url (HTTP ${code:-000}); its features degrade"; fi
  return 1
}

if [[ -f "$ENV_FILE" ]]; then
  info "== AI endpoints configured in .env (required: main, whisper, tts; optional: vision, embedding, reranker, ocr)"
  probe "main LLM" "$(env_get MAIN_LLM_BASE_URL)/models" 1 || true
  probe "whisper" "$(env_get WHISPER_BASE_URL)/models" 1 || true
  disabled=",$(env_get AI_DISABLED_ENDPOINTS | tr -d ' '),"
  optional_probe() { # name key url
    if [[ "$disabled" == *",$2,"* ]]; then ok "$1 disabled by AI_DISABLED_ENDPOINTS (its features are off)"; else probe "$1" "$3" 0 || true; fi
  }
  optional_probe "vision LLM" vision "$(env_get VISION_LLM_BASE_URL)/models"
  optional_probe "embeddings" embedding "$(env_get EMBEDDING_BASE_URL)/models"
  optional_probe "reranker" reranker "$(env_get RERANKER_BASE_URL)/health"
  optional_probe "ocr" ocr "$(env_get OCR_BASE_URL)/models"
fi

info "== Model assets (sha256-pinned; scripts/download_assets.sh tts vad face)"
if "$ROOT/scripts/download_assets.sh" --check tts vad face >/dev/null 2>&1; then ok "tts, vad and face assets present and verified"; else warn "assets missing or unverified: run scripts/download_assets.sh tts vad face"; fi

info "== Sandbox runtime images (make sandbox-images)"
missing=()
for lang in python node java go cpp; do
  docker image inspect "interview-sandbox-$lang:local" >/dev/null 2>&1 || missing+=("$lang")
done
if [[ ${#missing[@]} -eq 0 ]]; then ok "all five runtime images present"; else warn "runtime images missing: ${missing[*]} (coding rounds will fail with 'code runner is busy')"; fi

if [[ "$CHECK_STACK" == "1" ]]; then
  info "== Running stack"
  api_port="$(env_get API_PORT)"; api_port="${api_port:-8200}"
  if curl -fsS --max-time 5 "http://127.0.0.1:${api_port}/healthz" >/dev/null 2>&1; then
    ok "api /healthz"
    body="$(curl -sS --max-time 20 "http://127.0.0.1:${api_port}/readyz?deep=1" 2>/dev/null || true)"
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "http://127.0.0.1:${api_port}/readyz?deep=1" 2>/dev/null || true)"
    if [[ "$code" == "200" ]]; then
      degraded="$(printf '%s' "$body" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(",".join(d.get("degraded") or []))' 2>/dev/null || true)"
      if [[ -n "$degraded" ]]; then warn "api deep readiness: ready, degraded optional endpoints: $degraded"; else ok "api deep readiness: every AI endpoint ok"; fi
    else
      req_fail "api /readyz?deep=1 returned HTTP ${code:-000}: $(printf '%s' "$body" | cut -c1-200)"
    fi
  else
    warn "api not running on 127.0.0.1:${api_port} (make up), stack checks skipped"
  fi
fi

echo
if [[ "$failures" -gt 0 ]]; then
  fail "$failures required check(s) failed"
  exit 1
fi
ok "doctor: all required checks passed"
