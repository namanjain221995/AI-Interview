#!/usr/bin/env bash
# Waits until compose services are ready:
#   * services with a healthcheck report "healthy",
#   * services without one are "running",
#   * one-shot services (migrate) have exited with code 0.
#
#   scripts/wait_for_healthy.sh                       # every created service of the project
#   scripts/wait_for_healthy.sh --timeout 600 api worker edge
#
# On timeout or failure it prints the state of every service and the last log
# lines of the ones that are not ready, then exits 1.
set -euo pipefail
# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
require docker

timeout=300
services=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --timeout)
      timeout="${2:?--timeout needs seconds}"
      shift 2
      ;;
    -h | --help)
      sed -n '2,11p' "$0"
      exit 0
      ;;
    *)
      services+=("$1")
      shift
      ;;
  esac
done

if [[ ${#services[@]} -eq 0 ]]; then
  mapfile -t services < <(compose ps -a --format '{{.Service}}' | sort -u)
  [[ ${#services[@]} -gt 0 ]] || die "no containers exist for this project (run: make up)"
fi

# Prints one of: ready, waiting, failed, missing
state_of() {
  local id status health exit_code
  id="$(service_container "$1")"
  [[ -n "$id" ]] || {
    echo missing
    return
  }
  read -r status health exit_code < <(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.State.ExitCode}}' "$id")
  case "$status/$health" in
    running/healthy | running/none) echo ready ;;
    running/starting) echo waiting ;;
    running/unhealthy) echo failed ;;
    exited/*) if [[ "$exit_code" == "0" && "$1" == "migrate" ]]; then echo ready; else echo failed; fi ;;
    created/* | restarting/*) echo waiting ;;
    *) echo failed ;;
  esac
}

report() {
  local svc
  for svc in "${services[@]}"; do
    printf '  %-20s %s\n' "$svc" "$(state_of "$svc")"
  done
}

start=$SECONDS
info "waiting up to ${timeout}s for: ${services[*]}"
while true; do
  pending=()
  failed=()
  for svc in "${services[@]}"; do
    case "$(state_of "$svc")" in
      ready) ;;
      failed) failed+=("$svc") ;;
      *) pending+=("$svc") ;;
    esac
  done
  if [[ ${#failed[@]} -eq 0 && ${#pending[@]} -eq 0 ]]; then
    ok "all services ready after $((SECONDS - start))s"
    exit 0
  fi
  # Unhealthy containers may still recover (restart policy); give up on them
  # only at the deadline.
  if ((SECONDS - start >= timeout)); then
    fail "timed out after ${timeout}s"
    report
    for svc in "${failed[@]}" "${pending[@]}"; do
      printf '\n%s--- last log lines: %s ---%s\n' "$C_DIM" "$svc" "$C_RESET"
      compose logs --no-color --tail 40 "$svc" 2>&1 || true
    done
    exit 1
  fi
  sleep 3
done
