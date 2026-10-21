#!/usr/bin/env bash
# Deploys one of the two copies that run on this node from a clean checkout of its branch:
#
#   prod  branch main    project "interview"      http://localhost:3200  https://<lan-ip>:3443  + public tunnel
#   dev   branch hd-dev  project "interview-dev"  http://localhost:3201  https://<lan-ip>:3444  (local only)
#
#   scripts/deploy.sh prod                 # fetch origin/main, back up, build, migrate, restart, smoke
#   scripts/deploy.sh dev                  # fetch origin/hd-dev (first run also creates and seeds the dev copy)
#   scripts/deploy.sh dev --ref <ref>      # deploy any commit/branch instead (e.g. an unpushed local branch)
#   scripts/deploy.sh prod --force         # also when an interview is live (it pauses, the candidate reconnects)
#   scripts/deploy.sh status               # what each copy runs, health, URLs
#   scripts/deploy.sh rollback prod        # previous images + previous checkout (the database is NOT rolled back)
#
# Each copy is a detached git worktree under DEPLOY_ROOT (default ~/deploy/ai-interview/{prod,dev}) with its own
# .env (created on the first deploy: prod copies this repository's .env, dev derives its own from prod's with fresh
# secrets, its own ports, database and Redis). The working repository is never deployed. Images are tagged
# :prod / :dev (the previous ones :prod-previous / :dev-previous). dev shares prod's tts, sandbox, stt and
# monitoring, so it builds and runs only postgres, redis, migrate, api, worker and edge.
# Deploys are serialised (one image build at a time: memory is shared with vLLM). Exit status 75 means "not now"
# (another deploy holds the lock, or prod has a live interview): scripts/autodeploy.sh retries those later.
set -euo pipefail
# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
require docker git python3 flock

usage() {
  sed -n '2,21p' "$0"
  exit "${1:-0}"
}

DEPLOY_ROOT="${DEPLOY_ROOT:-$HOME/deploy/ai-interview}"
# The main working tree (holds .git and the operator's .env), also when this runs from a deploy worktree.
REPO="$(cd "$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)/.." && pwd)"
REMOTE="${DEPLOY_REMOTE:-origin}"

# busy MESSAGE: a refusal worth retrying later (EX_TEMPFAIL).
busy() {
  fail "$*"
  exit 75
}

cmd="${1:-}"
[[ -n "$cmd" ]] || usage 2
shift
case "$cmd" in
  -h | --help) usage 0 ;;
  prod | dev | status) ;;
  rollback)
    cmd="rollback"
    env_name="${1:-}"
    [[ "$env_name" == prod || "$env_name" == dev ]] || die "usage: scripts/deploy.sh rollback prod|dev"
    shift
    ;;
  *) usage 2 ;;
esac
[[ "$cmd" == prod || "$cmd" == dev ]] && env_name="$cmd"

ref=""
force=false
backup=true
smoke=true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref)
      ref="${2:?--ref needs a git ref}"
      shift 2
      ;;
    --force) force=true && shift ;;
    --no-backup) backup=false && shift ;;
    --skip-smoke) smoke=false && shift ;;
    -h | --help) usage 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

# ---------------------------------------------------------------------------------------------- per-copy settings
configure() {
  case "$1" in
    prod)
      BRANCH=main PROJECT=interview TAG=prod HTTP_PORT=3200 HTTPS_PORT=3443
      BUILD=(api edge tts sandbox) SERVICES=() SMOKE_SKIP_LIST=""
      ;;
    dev)
      BRANCH=hd-dev PROJECT=interview-dev TAG=dev HTTP_PORT=3201 HTTPS_PORT=3444
      BUILD=(api edge) SERVICES=(api worker edge) SMOKE_SKIP_LIST="monitoring"
      ;;
  esac
  DIR="$DEPLOY_ROOT/$1"
  STATE="$DEPLOY_ROOT/state/$1.env"
}

dc() { docker compose --project-directory "$DIR" -f "$DIR/docker-compose.yml" "$@"; }

log() {
  mkdir -p "$DEPLOY_ROOT"
  printf '%s %s %s\n' "$(date -u +%FT%TZ)" "$env_name" "$*" >>"$DEPLOY_ROOT/deploy.log"
  info "$*"
}

state_get() {
  [[ -f "$STATE" ]] || return 0
  grep -E "^$1=" "$STATE" | tail -n 1 | cut -d= -f2- || true
}

# set_env FILE KEY=VALUE...: replaces or appends keys without echoing values.
set_env() {
  local file="$1"
  shift
  python3 -I - "$file" "$@" <<'PY'
import re, sys
path, pairs = sys.argv[1], sys.argv[2:]
lines = open(path, encoding="utf-8").read().splitlines()
for pair in pairs:
    key, value = pair.split("=", 1)
    pattern = re.compile(rf"^{re.escape(key)}=")
    hits = [i for i, line in enumerate(lines) if pattern.match(line)]
    if hits:
        for i in hits:
            lines[i] = f"{key}={value}"
    else:
        lines.append(f"{key}={value}")
open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
PY
}

# url_with FILE KEY PASSWORD PORT: KEY's URL with another password and port (same scheme, user, path).
url_with() {
  python3 -I - "$1" "$2" "$3" "$4" <<'PY'
import re, sys
from urllib.parse import urlsplit, urlunsplit
path, key, password, port = sys.argv[1:]
value = next(m.group(1) for line in open(path, encoding="utf-8") if (m := re.match(rf"^{key}=(.*)$", line)))
parts = urlsplit(value.strip().strip('"'))
netloc = f"{parts.username or ''}:{password}@{parts.hostname}:{port}"
print(urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment)))
PY
}

secret() { python3 -I -c 'import secrets; print(secrets.token_hex(20))'; }

# ------------------------------------------------------------------------------------------------- .env creation
create_env() {
  local file="$DIR/.env" source_env
  [[ -f "$file" ]] && return 0
  umask 077
  if [[ "$env_name" == prod ]]; then
    [[ -f "$REPO/.env" ]] || die "$REPO/.env missing: production's settings are copied from it on the first deploy"
    if grep -qE '^COMPOSE_PROJECT_NAME=' "$REPO/.env" && ! grep -qE '^COMPOSE_PROJECT_NAME=interview$' "$REPO/.env"; then
      die "$REPO/.env is no longer production's (COMPOSE_PROJECT_NAME); restore $file from a backup instead"
    fi
    cp "$REPO/.env" "$file"
    set_env "$file" COMPOSE_PROJECT_NAME=interview IMAGE_TAG=prod
    log "created $file from $REPO/.env"
    return 0
  fi
  source_env="$DEPLOY_ROOT/prod/.env"
  [[ -f "$source_env" ]] || source_env="$REPO/.env"
  [[ -f "$source_env" ]] || die "no production .env to derive the dev settings from (deploy prod first)"
  cp "$source_env" "$file"
  local pg_pw redis_pw
  pg_pw="$(secret)"
  redis_pw="$(secret)"
  # Same AI endpoints, tts, sandbox (shared secret) and seed logins as prod; everything stateful is its own.
  set_env "$file" \
    COMPOSE_PROJECT_NAME=interview-dev IMAGE_TAG=dev BACKEND_NETWORK=interview-dev-backend COMPOSE_PROFILES= \
    API_PORT=8300 WORKER_METRICS_PORT=8301 EDGE_HTTP_PORT=3201 EDGE_HTTPS_PORT=3444 EDGE_TUNNEL_PORT=3281 \
    POSTGRES_PORT=5443 REDIS_PORT=6391 PUBLIC_BASE_URL=http://localhost:3201 COOKIE_NAME_PREFIX=dev_ \
    CLOUDFLARE_TUNNEL_TOKEN= JWT_PREVIOUS_SECRET= \
    "JWT_SECRET=$(secret)" "MEDIA_URL_SECRET=$(secret)" "TEST_HOOKS_TOKEN=$(secret)" \
    "POSTGRES_PASSWORD=$pg_pw" "REDIS_PASSWORD=$redis_pw"
  set_env "$file" "DATABASE_URL=$(url_with "$file" DATABASE_URL "$pg_pw" 5443)" \
    "REDIS_URL=$(url_with "$file" REDIS_URL "$redis_pw" 6391)"
  log "created $file (derived from $source_env: own ports, database, Redis, secrets; cookie prefix dev_)"
}

# -------------------------------------------------------------------------------------------------------- assets
# Git-ignored model files the images COPY (scripts/download_assets.sh): copied from the working repository when
# present there, downloaded (sha256-verified) otherwise.
sync_assets() {
  local groups=(vad face) files=(apps/api/models/silero_vad.onnx apps/api/models/face_detection_yunet_2023mar.onnx) rel
  if [[ "$env_name" == prod ]]; then # dev uses prod's tts
    groups+=(tts)
    files+=(services/tts/models/kokoro-v1.0.onnx services/tts/models/voices-v1.0.bin)
  fi
  for rel in "${files[@]}"; do
    if [[ ! -f "$DIR/$rel" && -f "$REPO/$rel" ]]; then
      cp "$REPO/$rel" "$DIR/$rel"
    fi
  done
  if ! "$DIR/scripts/download_assets.sh" --check "${groups[@]}" >/dev/null 2>&1; then
    "$DIR/scripts/download_assets.sh" "${groups[@]}"
  fi
}

# ---------------------------------------------------------------------------------------------------- guards
container_of() { docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter "label=com.docker.compose.service=$1" | head -n 1; }

# sql QUERY: one value from this copy's database (empty when postgres is not running).
sql() {
  local id
  id="$(container_of postgres)"
  [[ -n "$id" ]] || return 0
  # shellcheck disable=SC2016  # expanded by the container's shell; the query is passed as $0
  docker exec "$id" sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$0"' "$1" 2>/dev/null | tr -d '[:space:]'
}

live_sessions() { sql "select count(*) from interview_sessions where status in ('live', 'paused')"; }

seed_if_empty() {
  if [[ "$(sql "select count(*) from users")" == 0 ]]; then
    log "empty database: seeding the organization, staff logins (SEED_PASSWORD in $DIR/.env) and demo jobs"
    dc exec -T api python -m app.seed --demo >/dev/null
  fi
}

retag() { # from-tag to-tag (for every app image that exists with from-tag)
  local image
  for image in interview-api interview-edge interview-tts interview-sandbox interview-stt; do
    if docker image inspect "$image:$1" >/dev/null 2>&1; then docker tag "$image:$1" "$image:$2"; fi
  done
}

wait_healthy() {
  if [[ ${#SERVICES[@]} -gt 0 ]]; then
    "$DIR/scripts/wait_for_healthy.sh" --timeout 600 "${SERVICES[@]}"
  else
    "$DIR/scripts/wait_for_healthy.sh" --timeout 600
  fi
}

# ------------------------------------------------------------------------------------------------------ deploy
deploy() {
  configure "$env_name"
  mkdir -p "$DEPLOY_ROOT/state" "$DEPLOY_ROOT/backups/$env_name"
  exec 9>"$DEPLOY_ROOT/.deploy.lock"
  flock -n 9 || busy "another deploy is running (lock $DEPLOY_ROOT/.deploy.lock)"

  info "== $env_name: resolving the target commit"
  git -C "$REPO" fetch --quiet --prune "$REMOTE"
  local target sha label
  target="${ref:-$REMOTE/$BRANCH}"
  sha="$(git -C "$REPO" rev-parse --verify --quiet "$target^{commit}")" || die "unknown ref: $target"
  label="$(git -C "$REPO" log -1 --format='%h %s' "$sha")"
  if [[ -z "$ref" ]] && git -C "$REPO" rev-parse --verify --quiet "refs/heads/$BRANCH" >/dev/null; then
    local ahead
    ahead="$(git -C "$REPO" rev-list --count "$sha..refs/heads/$BRANCH")"
    [[ "$ahead" == 0 ]] || warn "local $BRANCH has $ahead commit(s) not on $REMOTE/$BRANCH: they are NOT deployed (push first, or --ref $BRANCH)"
  fi
  info "target: $target = $label"

  if [[ ! -e "$DIR/.git" ]]; then
    git -C "$REPO" worktree add --detach --quiet "$DIR" "$sha"
    log "created worktree $DIR"
  fi
  if [[ -n "$(git -C "$DIR" status --porcelain --untracked-files=no)" ]]; then
    $force || die "$DIR has local edits to tracked files (deploy dirs are build-only); --force discards them"
  fi
  create_env

  if [[ "$env_name" == prod ]]; then
    local live
    live="$(live_sessions)"
    if [[ "${live:-0}" != 0 ]]; then
      $force || busy "$live interview(s) live or paused right now; deploy later or pass --force (they pause and resume)"
      warn "$live interview(s) live: deploying anyway (--force)"
    fi
  fi

  local previous
  previous="$(git -C "$DIR" rev-parse HEAD)"
  git -C "$DIR" checkout --quiet --force --detach "$sha"
  sync_assets

  if $backup && [[ -n "$(container_of postgres)" ]]; then
    info "== $env_name: database backup"
    COMPOSE_PROJECT_NAME="$PROJECT" BACKUP_KEEP=20 "$DIR/scripts/backup_db.sh" "$DEPLOY_ROOT/backups/$env_name"
  fi

  info "== $env_name: building ${BUILD[*]} (one at a time)"
  retag "$TAG" "$TAG-previous"
  local service
  for service in "${BUILD[@]}"; do
    dc build "$service"
  done
  # The 20 GB speech-to-text image: rebuilt only when services/stt changed (or it does not exist yet).
  if [[ "$env_name" == prod ]] && grep -qE '^COMPOSE_PROFILES=.*\bstt\b' "$DIR/.env"; then
    if ! docker image inspect "interview-stt:$TAG" >/dev/null 2>&1 \
      || [[ -n "$(git -C "$DIR" diff --name-only "$previous" "$sha" -- services/stt)" ]]; then
      dc build stt
    fi
  fi

  info "== $env_name: migrate + restart"
  [[ "$env_name" == prod ]] && python3 -I "$DIR/scripts/prom_targets.py"
  dc up -d "${SERVICES[@]}"
  wait_healthy
  [[ "$env_name" == dev ]] && seed_if_empty

  {
    echo "DEPLOYED_SHA=$sha"
    echo "DEPLOYED_REF=$target"
    echo "DEPLOYED_AT=$(date -u +%FT%TZ)"
    echo "PREVIOUS_SHA=$previous"
  } >"$STATE"
  log "deployed $label ($target)"

  if $smoke; then
    info "== $env_name: smoke test"
    SMOKE_SKIP="$SMOKE_SKIP_LIST" "$DIR/scripts/smoke_test.sh" || die "smoke test failed (deployed anyway; roll back with: make deploy-rollback ENV=$env_name)"
  fi
  ok "$env_name is running $label on http://localhost:$HTTP_PORT and https://<lan-ip>:$HTTPS_PORT"
}

rollback() {
  configure "$env_name"
  exec 9>"$DEPLOY_ROOT/.deploy.lock"
  flock -n 9 || die "another deploy is running"
  local previous
  previous="$(state_get PREVIOUS_SHA)"
  [[ -n "$previous" ]] || die "no previous deploy recorded for $env_name"
  docker image inspect "interview-api:$TAG-previous" >/dev/null 2>&1 || die "no :$TAG-previous images to roll back to"
  warn "rolling $env_name back to $(git -C "$REPO" log -1 --format='%h %s' "$previous"); the database keeps its newer migrations"
  git -C "$DIR" checkout --quiet --force --detach "$previous"
  retag "$TAG-previous" "$TAG"
  dc up -d "${SERVICES[@]}"
  wait_healthy
  {
    echo "DEPLOYED_SHA=$previous"
    echo "DEPLOYED_REF=rollback"
    echo "DEPLOYED_AT=$(date -u +%FT%TZ)"
    echo "PREVIOUS_SHA="
  } >"$STATE"
  log "rolled back to $previous"
}

status() {
  git -C "$REPO" fetch --quiet --prune "$REMOTE" || warn "fetch failed; comparing with the last fetched refs"
  local e sha newest healthy total
  for e in prod dev; do
    env_name="$e"
    configure "$e"
    printf '\n%s%s%s  (%s, project %s)\n' "$C_GREEN" "$e" "$C_RESET" "$BRANCH" "$PROJECT"
    if [[ ! -e "$DIR/.git" ]]; then
      echo "  not deployed yet (make deploy-$e)"
      continue
    fi
    sha="$(git -C "$DIR" rev-parse HEAD)"
    newest="$(git -C "$REPO" rev-parse --verify --quiet "$REMOTE/$BRANCH" || true)"
    echo "  running   $(git -C "$DIR" log -1 --format='%h %s (%cr)' "$sha")"
    echo "  deployed  $(state_get DEPLOYED_AT) from $(state_get DEPLOYED_REF)"
    if [[ -n "$newest" && "$newest" != "$sha" ]]; then
      echo "  $REMOTE/$BRANCH is $(git -C "$REPO" rev-list --count "$sha..$newest") commit(s) ahead: make deploy-$e"
    else
      echo "  up to date with $REMOTE/$BRANCH"
    fi
    total="$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" | wc -l)"
    healthy="$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter health=healthy | wc -l)"
    echo "  containers $healthy/$total healthy"
    echo "  open      http://localhost:$HTTP_PORT   https://<lan-ip>:$HTTPS_PORT"
  done
  echo
}

case "$cmd" in
  prod | dev) deploy ;;
  rollback) rollback ;;
  status) status ;;
esac
