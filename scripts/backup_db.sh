#!/usr/bin/env bash
# Online logical backup of the platform database with pg_dump, run inside the
# postgres container (no client tools needed on the host).
#
#   scripts/backup_db.sh                 # -> data/backups/db/interview-<UTC timestamp>.dump
#   scripts/backup_db.sh /secure/path    # custom output directory
#   BACKUP_KEEP=14 scripts/backup_db.sh  # also prune all but the newest 14 dumps
#
# Output is pg_dump's custom format (compressed, restorable selectively with
# pg_restore) plus a .sha256 file. Dumps contain candidate personal data:
# they are written 0600 and data/ is git-ignored; copy them only to encrypted
# storage. Restore with scripts/restore_db.sh (make restore-db FILE=...).
set -euo pipefail
# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
require docker

out_dir="${1:-$ROOT/data/backups/db}"
keep="${BACKUP_KEEP:-0}"
service_running postgres || die "postgres is not running (make up, or: docker compose up -d postgres)"

umask 077
mkdir -p "$out_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
db="$(env_get POSTGRES_DB)"
db="${db:-interview}"
target="$out_dir/${db}-${stamp}.dump"
partial="$target.partial"
trap 'rm -f "$partial"' EXIT

info "dumping database '$db' ..."
start=$SECONDS
# Credentials come from the container's own environment; nothing is echoed.
# shellcheck disable=SC2016  # expanded by the container's shell, not this one
compose exec -T postgres sh -c \
  'exec pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom --compress=6 --no-password' \
  >"$partial"
[[ -s "$partial" ]] || die "pg_dump produced an empty file"
mv "$partial" "$target"
trap - EXIT

(cd "$out_dir" && sha256_of "$(basename "$target")" | awk -v f="$(basename "$target")" '{print $1 "  " f}' >"$(basename "$target").sha256")
chmod 0600 "$target" "$target.sha256"

# Sanity check: the archive's table of contents must be readable.
entries="$(compose exec -T postgres pg_restore --list <"$target" | grep -c ' TABLE DATA ' || true)"
size="$(du -h "$target" | cut -f1)"
ok "backup written: $target ($size, $entries tables with data, $((SECONDS - start))s)"

if [[ "$keep" =~ ^[0-9]+$ && "$keep" -gt 0 ]]; then
  # Names are generated timestamps (no spaces), so ls ordering is safe here.
  # shellcheck disable=SC2012
  mapfile -t old < <(ls -1t "$out_dir"/*.dump 2>/dev/null | tail -n +"$((keep + 1))")
  for f in "${old[@]}"; do
    rm -f "$f" "$f.sha256"
    info "pruned $(basename "$f")"
  done
fi
