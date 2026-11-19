#!/usr/bin/env bash
# Daily job: fetch, then commit and push everything, including source-health
# timestamps that the hourly fetch only pushes alongside new articles.
# Also the hook for Hermes's once-a-day summary.
#
#   scripts/publish.sh            # fetch + commit + push
#   NO_PUSH=1 scripts/publish.sh  # fetch + commit only
#
# Output ends with "Pushed <hash>." / "No data changes." / "✗ git push failed ...".
# Exit status is non-zero if the push failed or a topic had no working sources.
set -euo pipefail
cd "$(dirname "$0")/.."
# Publishing must not be skipped just because an hourly fetch is mid-run:
# wait up to 10 minutes, and fail loudly (exit 75) if the lock never frees.
LOCK_WAIT=600 LOCK_SKIP_STATUS=75
source scripts/_lock.sh
source scripts/_run.sh
source scripts/_git.sh
echo "[$(date -u +%FT%TZ)] publish"

git_sync

status=0
newscanner run || status=$?   # non-zero = a topic had no working sources

commit_and_push "data: daily update $(date -u +%Y-%m-%d)" || status=1
exit "$status"
