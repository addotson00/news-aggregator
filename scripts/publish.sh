#!/usr/bin/env bash
# Daily job: one last fetch, then commit data + fetch state and push.
# GitHub Pages redeploys on push.
#
#   scripts/publish.sh            # fetch + commit + push
#   NO_PUSH=1 scripts/publish.sh  # fetch + commit only
#
# Output ends with exactly one of:
#   "Pushed <hash>."  /  "No data changes."  /  "✗ git push failed ..."
# Exit status is non-zero if the push failed or a topic had no working sources.
set -euo pipefail
cd "$(dirname "$0")/.."
# Publishing must not be skipped just because an hourly fetch is mid-run:
# wait up to 10 minutes, and fail loudly (exit 75) if the lock never frees.
LOCK_WAIT=600 LOCK_SKIP_STATUS=75
source scripts/_lock.sh
source scripts/_run.sh
echo "[$(date -u +%FT%TZ)] publish"

git pull --rebase --autostash --quiet || echo "⚠ git pull failed, continuing with local copy"

status=0
newscanner run || status=$?   # non-zero = a topic had no working sources

if [[ -n "$(git status --porcelain -- docs/data state)" ]]; then
  git add docs/data state
  git commit --quiet -m "data: update $(date -u +%Y-%m-%d)"
  echo "Committed $(git rev-parse --short HEAD)."
fi

# Push anything not yet on the remote, including commits left by an earlier failed push.
if [[ -n "${NO_PUSH:-}" ]]; then
  echo "NO_PUSH set; not pushing."
elif [[ -z "$(git log '@{u}..HEAD' --oneline 2>/dev/null)" ]]; then
  echo "No data changes."
elif git push --quiet 2>&1; then
  echo "Pushed $(git rev-parse --short HEAD)."
else
  echo "✗ git push failed; $(git rev-list --count '@{u}..HEAD') commit(s) waiting to be pushed."
  status=1
fi
exit "$status"
