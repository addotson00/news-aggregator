#!/usr/bin/env bash
# Hermes cron wrapper (hourly) for ~/news-aggregator/scripts/fetch.sh
# - Full output is appended to ~/.hermes/logs/newsagg.log
# - stdout (what Hermes delivers) is empty on a healthy run; otherwise it
#   shows failed sources (✗), possible gaps (⚠), or the tail of a failed run.
# - The exit code of fetch.sh is passed through.
set -uo pipefail   # no -e: a failing run must still be logged and reported
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export LC_ALL=C.UTF-8

LOG_FILE="$HOME/.hermes/logs/newsagg.log"
mkdir -p "$(dirname "$LOG_FILE")"
TS="$(date -u +%FT%TZ)"

EXIT_CODE=0
OUTPUT="$("$HOME/news-aggregator/scripts/fetch.sh" "$@" 2>&1)" || EXIT_CODE=$?

{
  echo "=== [$TS] newsagg-fetch started ==="
  echo "$OUTPUT"
  echo "=== [$TS] newsagg-fetch exit code: $EXIT_CODE ==="
  echo
} >> "$LOG_FILE"

grep -E '✗|⚠' <<<"$OUTPUT" || true
if (( EXIT_CODE != 0 )); then
  echo "newsagg-fetch failed (exit $EXIT_CODE). Last lines:"
  tail -n 8 <<<"$OUTPUT"
fi
exit "$EXIT_CODE"
