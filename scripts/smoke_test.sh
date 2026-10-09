#!/usr/bin/env bash
# Post-deploy smoke test of a running stack (make smoke). Read-only: it never
# creates data. Prints PASS/FAIL per check with latency; exits 1 on any FAIL.
#
# Checks: API liveness/readiness, worker health, edge (SPA, security headers,
# API proxying, internal endpoints blocked for LAN clients, HTTPS listener),
# tts and sandbox health, Prometheus readiness and scrape targets, Grafana.
#
#   scripts/smoke_test.sh
#   SMOKE_SKIP="tts sandbox" scripts/smoke_test.sh     # skip optional components
#   EDGE_URL=http://127.0.0.1:3200 API_URL=http://127.0.0.1:8200 scripts/smoke_test.sh
set -euo pipefail
# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
require curl python3

api_port="$(env_get API_PORT)"
worker_port="$(env_get WORKER_METRICS_PORT)"
API_URL="${API_URL:-http://127.0.0.1:${api_port:-8200}}"
WORKER_URL="${WORKER_URL:-http://127.0.0.1:${worker_port:-8201}}"
EDGE_URL="${EDGE_URL:-http://127.0.0.1:3200}"
EDGE_TLS_URL="${EDGE_TLS_URL:-https://127.0.0.1:3443}"
TTS_URL="${TTS_URL:-http://127.0.0.1:8210}"
SANDBOX_URL="${SANDBOX_URL:-http://127.0.0.1:8220}"
PROM_URL="${PROM_URL:-http://127.0.0.1:9191}"
GRAFANA_URL="${GRAFANA_URL:-http://127.0.0.1:3301}"
SMOKE_SKIP="${SMOKE_SKIP:-}"

passed=0
failed=0
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

skipped() { [[ " $SMOKE_SKIP " == *" $1 "* ]]; }

record() { # status name detail
  if [[ "$1" == PASS ]]; then
    passed=$((passed + 1))
    printf '%sPASS%s  %-46s %s\n' "$C_GREEN" "$C_RESET" "$2" "$3"
  else
    failed=$((failed + 1))
    printf '%sFAIL%s  %-46s %s\n' "$C_RED" "$C_RESET" "$2" "$3"
  fi
}

# http NAME URL EXPECTED_STATUS [curl args...] -> body in $tmp/body, headers in $tmp/headers
http() {
  local name="$1" url="$2" expected="$3"
  shift 3
  local out code seconds
  if ! out="$(curl -sS -o "$tmp/body" -D "$tmp/headers" -w '%{http_code} %{time_total}' --max-time 15 "$@" "$url" 2>"$tmp/err")"; then
    record FAIL "$name" "$(tr -d '\n' <"$tmp/err" | cut -c1-90)"
    return 1
  fi
  read -r code seconds <<<"$out"
  local ms
  ms="$(awk -v s="$seconds" 'BEGIN { printf "%.0f ms", s * 1000 }')"
  if [[ " $expected " == *" $code "* ]]; then
    record PASS "$name" "$code in $ms"
    return 0
  fi
  record FAIL "$name" "got $code (expected $expected) in $ms"
  return 1
}

header_is() { # name header expected
  local value
  value="$(grep -i "^$2:" "$tmp/headers" | head -n 1 | cut -d: -f2- | sed 's/^ *//; s/\r$//')"
  if [[ "$value" == "$3" ]]; then record PASS "$1" "$2: ${value:0:40}"; else record FAIL "$1" "$2 = '${value:0:60}'"; fi
}

lan_ip() {
  python3 - <<'PY' 2>/dev/null || true
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(("192.0.2.1", 9))
    ip = s.getsockname()[0]
    print("" if ip.startswith("127.") else ip)
finally:
    s.close()
PY
}

info "== API"
http "api /healthz" "$API_URL/healthz" 200 || true
http "api /readyz (db, redis, storage)" "$API_URL/readyz" 200 || true
http "worker /healthz" "$WORKER_URL/healthz" 200 || true

info "== Edge"
if http "edge SPA index" "$EDGE_URL/" 200; then
  if grep -q '<div id="root">' "$tmp/body"; then record PASS "edge serves the built SPA" "index.html"; else record FAIL "edge serves the built SPA" "no #root element"; fi
  header_is "edge header X-Frame-Options" "X-Frame-Options" "DENY"
  header_is "edge header X-Content-Type-Options" "X-Content-Type-Options" "nosniff"
  header_is "edge header Referrer-Policy" "Referrer-Policy" "strict-origin-when-cross-origin"
  header_is "edge header Permissions-Policy" "Permissions-Policy" "camera=(self), microphone=(self), display-capture=(self), fullscreen=(self), geolocation=()"
  if grep -qi "^content-security-policy: default-src 'self'" "$tmp/headers"; then record PASS "edge header Content-Security-Policy" "default-src 'self' ..."; else record FAIL "edge header Content-Security-Policy" "missing"; fi
  header_is "edge index Cache-Control" "Cache-Control" "no-cache"
fi
http "edge SPA fallback (/candidate)" "$EDGE_URL/candidate" 200 || true
http "edge -> API proxy (/api/v1/auth/me)" "$EDGE_URL/api/v1/auth/me" "401 403" || true
http "edge /metrics from loopback" "$EDGE_URL/metrics" 200 || true
ip="$(lan_ip)"
if [[ -n "$ip" ]]; then
  http "edge blocks /metrics for LAN ($ip)" "http://$ip:3200/metrics" 403 || true
  http "edge blocks /api/v1/test/* for LAN" "http://$ip:3200/api/v1/test/ping" 403 || true
else
  warn "no LAN address found; skipped the non-loopback block checks"
fi
http "edge HTTPS listener (tls internal)" "$EDGE_TLS_URL/__edge/health" 200 --insecure || true

info "== Speech and coding services"
if skipped tts; then warn "tts skipped (SMOKE_SKIP)"; else http "tts /health" "$TTS_URL/health" 200 || true; fi
if skipped sandbox; then warn "sandbox skipped (SMOKE_SKIP)"; else http "sandbox /health" "$SANDBOX_URL/health" 200 || true; fi

info "== Monitoring"
http "prometheus /-/ready" "$PROM_URL/-/ready" 200 || true
if curl -sS --max-time 10 "$PROM_URL/api/v1/targets?state=active" -o "$tmp/targets" 2>/dev/null; then
  while IFS=$'\t' read -r status job instance health err; do
    if [[ "$status" == required ]]; then
      if [[ "$health" == up ]]; then record PASS "target $job" "$instance"; else record FAIL "target $job" "$instance $health ${err:0:60}"; fi
    elif [[ "$health" != up ]]; then
      warn "shared target $job $instance is $health (not owned by this platform)"
    fi
  done < <(SMOKE_SKIP="$SMOKE_SKIP" python3 -c '
import json, os, sys
skip = os.environ.get("SMOKE_SKIP", "").split()
data = json.load(open(sys.argv[1]))["data"]["activeTargets"]
for t in sorted(data, key=lambda t: t["labels"]["job"]):
    job = t["labels"]["job"]
    if any(job == f"interview-{s}" for s in skip):
        continue
    required = job.startswith("interview-") or job in ("postgres", "redis", "prometheus")
    print("required" if required else "shared", job, t["labels"]["instance"], t["health"], t.get("lastError", "").replace("\t", " "), sep="\t")
' "$tmp/targets")
else
  record FAIL "prometheus targets API" "unreachable"
fi
http "grafana /api/health" "$GRAFANA_URL/api/health" 200 || true

printf '\n%d passed, %d failed\n' "$passed" "$failed"
[[ $failed -eq 0 ]]
