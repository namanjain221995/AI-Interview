#!/usr/bin/env bash
# Restores a scripts/backup_db.sh dump.
#
#   scripts/restore_db.sh data/backups/db/interview-20261007T101500Z.dump
#   scripts/restore_db.sh <file> --yes                    # non-interactive (automation)
#   scripts/restore_db.sh <file> --database restore_drill --create --yes
#                                                         # restore drill into a scratch database
#
# DESTRUCTIVE for the target database: every object in the dump is dropped and
# recreated (pg_restore --clean --if-exists) inside one transaction, so the
# restore is all-or-nothing. Rows created after the backup are lost. The script
# verifies the .sha256 file when present and asks you to type the database
# name. When the target is the platform database (POSTGRES_DB) it stops
# api/worker for the duration (open connections would block the drops) and
# starts them again afterwards.
set -euo pipefail
# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
require docker

file=""
assume_yes=false
create=false
platform_db="$(env_get POSTGRES_DB)"
platform_db="${platform_db:-interview}"
db="$platform_db"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) assume_yes=true ;;
    --create) create=true ;;
    --database)
      db="${2:?--database needs a name}"
      shift
      ;;
    -h | --help)
      sed -n '2,15p' "$0"
      exit 0
      ;;
    *)
      [[ -z "$file" ]] || die "unexpected argument: $1"
      file="$1"
      ;;
  esac
  shift
done
[[ -n "$file" ]] || die "usage: scripts/restore_db.sh <dump-file> [--database NAME [--create]] [--yes]"
[[ -f "$file" ]] || die "no such file: $file"
[[ "$db" =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]] || die "invalid database name: $db"
service_running postgres || die "postgres is not running (docker compose up -d postgres)"

if [[ -f "$file.sha256" ]]; then
  expected="$(awk '{print $1}' "$file.sha256")"
  [[ "$(sha256_of "$file")" == "$expected" ]] || die "checksum mismatch for $file (expected $expected)"
  ok "checksum verified"
else
  warn "no $file.sha256 found; integrity not verified"
fi

compose exec -T postgres pg_restore --list <"$file" >/dev/null || die "$file is not a pg_dump custom-format archive"

printf '\n%sThis replaces the contents of database "%s" with %s.%s\n' "$C_RED" "$db" "$(basename "$file")" "$C_RESET"
printf 'Data written after that backup will be lost. Take a fresh backup first (make backup-db).\n\n'
if ! $assume_yes; then
  [[ -t 0 ]] || die "refusing to restore without a terminal; pass --yes to confirm non-interactively"
  read -r -p "Type the database name ($db) to continue: " answer
  [[ "$answer" == "$db" ]] || die "aborted"
fi

# psql reads the query from stdin so :'target' is bound as a quoted literal.
# shellcheck disable=SC2016  # $POSTGRES_USER/$TARGET_DB are expanded by the container's shell
exists="$(printf "SELECT 1 FROM pg_database WHERE datname = :'target';\n" |
  compose exec -T -e TARGET_DB="$db" postgres sh -c \
    'psql --username="$POSTGRES_USER" --dbname=postgres --no-password --quiet -tA -v target="$TARGET_DB"')"
if [[ "$exists" != "1" ]]; then
  $create || die "database '$db' does not exist (pass --create to create it)"
  # shellcheck disable=SC2016
  compose exec -T -e TARGET_DB="$db" postgres sh -c 'createdb --username="$POSTGRES_USER" --no-password "$TARGET_DB"'
  info "created database '$db'"
fi

stopped=()
if [[ "$db" == "$platform_db" ]]; then
  for svc in api worker; do
    if service_running "$svc"; then
      compose stop "$svc" >/dev/null
      stopped+=("$svc")
      info "stopped $svc"
    fi
  done
fi
restart_stopped() {
  if [[ ${#stopped[@]} -gt 0 ]]; then
    compose start "${stopped[@]}" >/dev/null && info "started ${stopped[*]} again"
  fi
}
trap restart_stopped EXIT

info "restoring into '$db' ..."
start=$SECONDS
# shellcheck disable=SC2016  # expanded by the container's shell, not this one
compose exec -T -e TARGET_DB="$db" postgres sh -c \
  'exec pg_restore --username="$POSTGRES_USER" --dbname="$TARGET_DB" --clean --if-exists --no-owner --single-transaction --exit-on-error --no-password' \
  <"$file"
ok "restore into '$db' completed in $((SECONDS - start))s"
if [[ "$db" == "$platform_db" ]]; then
  info "next: verify with 'make smoke'; run 'make migrate' if the backup predates the current schema"
fi
