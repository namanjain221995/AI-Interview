#!/usr/bin/env bash
# Shared helpers for the operational scripts. Source it; do not execute it.
# shellcheck shell=bash

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ROOT

# Colour codes are used by the scripts that source this file.
# shellcheck disable=SC2034
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_RED=$'\033[31m' C_GREEN=$'\033[32m' C_YELLOW=$'\033[33m' C_DIM=$'\033[2m' C_RESET=$'\033[0m'
else
  C_RED="" C_GREEN="" C_YELLOW="" C_DIM="" C_RESET=""
fi

info() { printf '%s\n' "$*"; }
ok() { printf '%sOK%s    %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '%sWARN%s  %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
fail() { printf '%sFAIL%s  %s\n' "$C_RED" "$C_RESET" "$*" >&2; }
die() {
  fail "$*"
  exit 1
}

# docker compose pinned to this repository, wherever the caller's cwd is.
compose() {
  docker compose --project-directory "$ROOT" -f "$ROOT/docker-compose.yml" "$@"
}

# Value of KEY from .env (never echoed by the scripts). Empty when unset.
env_get() {
  local key="$1" file="$ROOT/.env" line
  [[ -f "$file" ]] || return 0
  line="$(grep -E "^${key}=" "$file" | tail -n 1 || true)"
  line="${line#*=}"
  line="${line%\"}"
  line="${line#\"}"
  printf '%s' "$line"
}

require() {
  local cmd
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null 2>&1 || die "required command not found: $cmd"
  done
}

# sha256 of a file on Linux (sha256sum) or macOS (shasum).
sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

# Container id of a compose service (empty when not created).
service_container() {
  compose ps -a -q "$1" 2>/dev/null | head -n 1
}

service_running() {
  local id
  id="$(service_container "$1")"
  [[ -n "$id" && "$(docker inspect -f '{{.State.Running}}' "$id" 2>/dev/null)" == "true" ]]
}
