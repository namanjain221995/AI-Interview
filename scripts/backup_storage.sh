#!/usr/bin/env bash
# Backs up the local file storage (resumes, answer audio, frames, recordings,
# report artifacts). In containers this is the `appdata` volume mounted at
# /srv/interview-data; media rows in the database point into it, so take this
# backup together with scripts/backup_db.sh.
#
#   scripts/backup_storage.sh                     # -> data/backups/storage/appdata-<UTC>.tar.gz
#   scripts/backup_storage.sh --host-path data/storage   # dev mode: API/worker running on the host
#   scripts/backup_storage.sh --out /secure/path
#
# The archive holds candidate personal data: it is written 0600 next to a
# .sha256 file; keep it on encrypted storage only.
set -euo pipefail
# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
require docker tar

out_dir="$ROOT/data/backups/storage"
host_path=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)
      out_dir="${2:?--out needs a directory}"
      shift 2
      ;;
    --host-path)
      host_path="${2:?--host-path needs a directory}"
      shift 2
      ;;
    -h | --help)
      sed -n '2,13p' "$0"
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

umask 077
mkdir -p "$out_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
start=$SECONDS

if [[ -n "$host_path" ]]; then
  [[ -d "$host_path" ]] || die "no such directory: $host_path"
  name="storage-host-${stamp}.tar.gz"
  tar -C "$host_path" -czf "$out_dir/$name" .
else
  volume="$(docker volume ls -q --filter label=com.docker.compose.project=interview --filter label=com.docker.compose.volume=appdata | head -n 1)"
  [[ -n "$volume" ]] || die "the appdata volume does not exist yet (nothing stored in container mode)"
  name="appdata-${stamp}.tar.gz"
  # Files are 0600 and owned by the service user, so archive as root inside a
  # throwaway container and hand the result to the invoking user.
  docker run --rm --network none \
    -v "$volume:/data:ro" -v "$(cd "$out_dir" && pwd):/backup" \
    alpine:3 sh -c "tar -C /data -czf /backup/$name . && chown $(id -u):$(id -g) /backup/$name && chmod 0600 /backup/$name"
fi

(cd "$out_dir" && sha256_of "$name" | awk -v f="$name" '{print $1 "  " f}' >"$name.sha256")
chmod 0600 "$out_dir/$name" "$out_dir/$name.sha256"
files="$(tar -tzf "$out_dir/$name" | grep -vc '/$' || true)"
ok "storage backup written: $out_dir/$name ($(du -h "$out_dir/$name" | cut -f1), $files files, $((SECONDS - start))s)"
