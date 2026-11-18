# Sourced by fetch.sh / publish.sh: one pipeline run at a time.
LOCK_DIR="${TMPDIR:-/tmp}/newscanner.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  # Stale lock (older than 30 min)? Take it over; otherwise bail out quietly.
  if [[ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +30 2>/dev/null)" ]]; then
    rm -rf "$LOCK_DIR" && mkdir "$LOCK_DIR"
  else
    echo "Another newscanner run is in progress; skipping."
    exit 0
  fi
fi
trap 'rm -rf "$LOCK_DIR"' EXIT
