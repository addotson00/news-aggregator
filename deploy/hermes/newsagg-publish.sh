#!/usr/bin/env bash
# Hermes cron wrapper (daily) for ~/news-aggregator/scripts/publish.sh
# - Full output is appended to ~/.hermes/logs/newsagg.log
# - stdout (delivered by Hermes): problems (✗/⚠), a per-aggregator summary,
#   and the push result line from publish.sh.
# - The exit code of publish.sh is passed through.
set -uo pipefail   # no -e: a failing run must still be logged and reported
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export LC_ALL=C.UTF-8

REPO="$HOME/news-aggregator"
LOG_FILE="$HOME/.hermes/logs/newsagg.log"
mkdir -p "$(dirname "$LOG_FILE")"
TS="$(date -u +%FT%TZ)"

EXIT_CODE=0
OUTPUT="$("$REPO/scripts/publish.sh" "$@" 2>&1)" || EXIT_CODE=$?

{
  echo "=== [$TS] newsagg-publish started ==="
  echo "$OUTPUT"
  echo "=== [$TS] newsagg-publish exit code: $EXIT_CODE ==="
  echo
} >> "$LOG_FILE"

grep -E '✗|⚠' <<<"$OUTPUT" || true

# Per-aggregator summary: current window, matches, and articles first seen in
# the last 24h (the hourly fetches find most of them, not this run alone).
python3 - "$REPO/docs/data" <<'PY' || echo "(could not read manifest)"
import json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

data = Path(sys.argv[1])
manifest = json.loads((data / "manifest.json").read_text())
since = datetime.now(timezone.utc) - timedelta(hours=24)

def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None

for t in manifest["topics"]:
    arts = json.loads((data / t["slug"] / "articles.json").read_text())["articles"]
    fresh = [a for a in arts if (ts(a.get("first_seen")) or since) > since]
    fresh_matches = sum(a.get("match", False) for a in fresh)
    active = [s for s in t["sources"] if s.get("state") != "disabled"]
    ok = sum(s.get("state") == "ok" for s in active)
    gaps = [s["name"] for s in active
            if s.get("last_gap") and ts(s["last_gap"]["detected"]) > since]
    print(f"{t['title']}: {len(fresh)} new in 24h ({fresh_matches} keyword matches) · "
          f"{t['article_count']} articles / {t['match_count']} matches in window · "
          f"{ok}/{len(active)} sources OK")
    if gaps:
        print(f"  ⚠ possible gaps in 24h: {', '.join(gaps)}")
PY

# Push result, exactly as publish.sh reported it.
grep -E '^(Pushed |No data changes\.|NO_PUSH set|Another newscanner run)' <<<"$OUTPUT" || true
if (( EXIT_CODE != 0 )); then
  echo "newsagg-publish failed (exit $EXIT_CODE). Last lines:"
  tail -n 8 <<<"$OUTPUT"
fi
exit "$EXIT_CODE"
