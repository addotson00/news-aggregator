#!/usr/bin/env bash
# Frequent job (hourly): fetch every topic that is due, then push to GitHub
# if any articles were added or changed. Quiet hours make no commit.
# Conditional requests (ETag/Last-Modified) keep the fetching cheap.
#
#   scripts/fetch.sh               # fetch, push if new articles
#   NO_PUSH=1 scripts/fetch.sh     # fetch (and commit) without pushing
#   scripts/fetch.sh --topic UA    # extra args go to `newscanner run`
#
# Exit status is non-zero if a push failed or a topic had no working sources.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/_lock.sh
source scripts/_run.sh
source scripts/_git.sh
echo "[$(date -u +%FT%TZ)] fetch"

git_sync

status=0
newscanner run "$@" || status=$?

if articles_changed || has_unpushed; then
  commit_and_push "data: new articles $(date -u +%Y-%m-%dT%H:%MZ)" || status=1
else
  echo "No new articles; not pushing."
fi
exit "$status"
