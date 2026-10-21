#!/usr/bin/env bash
# Deploys a copy when its branch moves on the remote: dev follows origin/hd-dev, prod follows origin/main.
# Run every two minutes by the systemd user timer in infra/systemd (make autodeploy-install); safe to run by hand.
#
#   scripts/autodeploy.sh            # check both copies, deploy the ones whose branch has a new commit
#
# Only copies that were deployed once (scripts/deploy.sh created their worktree) are followed, so the first deploy of
# a copy is always a deliberate `make deploy-<copy>`. A copy is redeployed when origin/<branch> is neither the
# deployed commit nor an ancestor of it (a manual `--ref` deploy of newer local commits is kept). A deploy that
# fails is not retried for the same commit (the next push retries; `make deploy-<copy>` retries now). A deploy
# that is refused "for now" (exit 75: another deploy running, an interview live on prod) is retried next run.
set -euo pipefail
# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
require git flock

DEPLOY_ROOT="${DEPLOY_ROOT:-$HOME/deploy/ai-interview}"
REPO="$(cd "$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)/.." && pwd)"
REMOTE="${DEPLOY_REMOTE:-origin}"
LOG="$DEPLOY_ROOT/autodeploy.log"

mkdir -p "$DEPLOY_ROOT/state"
exec 8>"$DEPLOY_ROOT/.autodeploy.lock"
flock -n 8 || exit 0 # the previous run is still deploying

log() {
  printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >>"$LOG"
  info "$*"
}

# value KEY FILE: KEY's value in a state file (empty when missing).
value() {
  [[ -f "$2" ]] || return 0
  grep -E "^$1=" "$2" | tail -n 1 | cut -d= -f2- || true
}

git -C "$REPO" fetch --quiet --prune "$REMOTE" || {
  warn "fetch from $REMOTE failed; trying again next run"
  exit 0
}

# dev first: it is the one being checked before a merge, and prod's deploy waits for the lock anyway.
for copy in dev prod; do
  case "$copy" in
    dev) branch=hd-dev ;;
    prod) branch=main ;;
  esac
  [[ -e "$DEPLOY_ROOT/$copy/.git" ]] || continue
  newest="$(git -C "$REPO" rev-parse --verify --quiet "$REMOTE/$branch^{commit}" || true)"
  [[ -n "$newest" ]] || continue
  state="$DEPLOY_ROOT/state/$copy.env"
  marker="$DEPLOY_ROOT/state/$copy.autodeploy"
  deployed="$(value DEPLOYED_SHA "$state")"
  [[ "$newest" == "$deployed" ]] && continue
  if [[ -n "$deployed" ]] && git -C "$REPO" merge-base --is-ancestor "$newest" "$deployed" 2>/dev/null; then
    continue
  fi
  [[ "$newest" == "$(value FAILED_SHA "$marker")" ]] && continue

  label="$(git -C "$REPO" log -1 --format='%h %s' "$newest")"
  log "$copy: $REMOTE/$branch moved to $label; deploying"
  status=0
  "$ROOT/scripts/deploy.sh" "$copy" || status=$?
  case "$status" in
    0) log "$copy: deployed $label" && rm -f "$marker" ;;
    75) log "$copy: not now (busy or an interview is live); retrying next run" ;;
    *)
      # deploy.sh records DEPLOYED_SHA before its smoke test, so a smoke failure does not land here.
      echo "FAILED_SHA=$newest" >"$marker"
      log "$copy: deploy of $label FAILED (exit $status); not retried until $branch moves (make deploy-$copy retries now)"
      ;;
  esac
done
