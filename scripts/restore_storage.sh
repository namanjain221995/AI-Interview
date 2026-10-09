#!/usr/bin/env bash
# Restores a scripts/backup_storage.sh archive into the `appdata` volume.
#
#   scripts/restore_storage.sh data/backups/storage/appdata-20261007T101500Z.tar.gz [--yes]
#
# DESTRUCTIVE: the volume is emptied first, so files stored after the backup
# are lost. Restore the matching database dump as well (scripts/restore_db.sh).
# api/worker are stopped during the restore and started again afterwards.
set -euo pipefail
# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
require docker

file=""
assume_yes=false
for arg in "$@"; do
  case "$arg" in
    --yes) assume_yes=true ;;
    -h | --help)
      sed -n '2,8p' "$0"
      exit 0
      ;;
    *)
      [[ -z "$file" ]] || die "unexpected argument: $arg"
      file="$arg"
      ;;
  esac
done
[[ -n "$file" && -f "$file" ]] || die "usage: scripts/restore_storage.sh <archive.tar.gz> [--yes]"

if [[ -f "$file.sha256" ]]; then
  [[ "$(sha256_of "$file")" == "$(awk '{print $1}' "$file.sha256")" ]] || die "checksum mismatch for $file"
  ok "checksum verified"
else
  warn "no $file.sha256 found; integrity not verified"
fi
tar -tzf "$file" >/dev/null || die "$file is not a readable tar.gz archive"
if tar -tzf "$file" | grep -qE '(^/|(^|/)\.\.(/|$))'; then
  die "$file contains absolute or parent-directory paths; refusing to extract"
fi

volume="$(docker volume ls -q --filter label=com.docker.compose.project=interview --filter label=com.docker.compose.volume=appdata | head -n 1)"
if [[ -z "$volume" ]]; then
  compose create api worker >/dev/null 2>&1 || true
  volume="$(docker volume ls -q --filter label=com.docker.compose.project=interview --filter label=com.docker.compose.volume=appdata | head -n 1)"
fi
[[ -n "$volume" ]] || die "could not find or create the appdata volume"

printf '\n%sThis empties volume %s and restores %s into it.%s\n\n' "$C_RED" "$volume" "$(basename "$file")" "$C_RESET"
if ! $assume_yes; then
  [[ -t 0 ]] || die "refusing to restore without a terminal; pass --yes to confirm non-interactively"
  read -r -p "Type 'restore' to continue: " answer
  [[ "$answer" == "restore" ]] || die "aborted"
fi

stopped=()
for svc in api worker; do
  if service_running "$svc"; then
    compose stop "$svc" >/dev/null
    stopped+=("$svc")
  fi
done
restart_stopped() {
  if [[ ${#stopped[@]} -gt 0 ]]; then
    compose start "${stopped[@]}" >/dev/null && info "started ${stopped[*]} again"
  fi
}
trap restart_stopped EXIT

archive_dir="$(cd "$(dirname "$file")" && pwd)"
archive_name="$(basename "$file")"
docker run --rm --network none -v "$volume:/data" -v "$archive_dir:/backup:ro" alpine:3 \
  sh -c "find /data -mindepth 1 -delete && tar -C /data -xzf '/backup/$archive_name'"
ok "storage restored into $volume"
