#!/usr/bin/env bash
# Daily job: one last fetch, then commit data + fetch state and push.
# GitHub Pages redeploys on push.
#
#   scripts/publish.sh            # fetch + commit + push
#   NO_PUSH=1 scripts/publish.sh  # fetch + commit only
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/_lock.sh
source scripts/_run.sh
echo "[$(date -u +%FT%TZ)] publish"

git pull --rebase --autostash --quiet || echo "warn: git pull failed, continuing with local copy"

status=0
newscanner run || status=$?   # non-zero = a topic had no working sources

if [[ -n "$(git status --porcelain -- docs/data state)" ]]; then
  git add docs/data state
  git commit --quiet -m "data: update $(date -u +%Y-%m-%d)"
  [[ -z "${NO_PUSH:-}" ]] && git push --quiet
  echo "Committed updated data."
else
  echo "No data changes."
fi
exit "$status"
