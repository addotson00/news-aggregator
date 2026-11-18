# Sourced by fetch.sh / publish.sh: one pipeline run at a time.
# LOCK_WAIT (seconds, default 0) = how long to wait for a running job first.
LOCK_DIR="${TMPDIR:-/tmp}/newscanner.lock"
_waited=0
until mkdir "$LOCK_DIR" 2>/dev/null; do
  # Stale lock (older than 30 min, e.g. after a crash)? Take it over.
  if [[ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +30 2>/dev/null)" ]]; then
    rm -rf "$LOCK_DIR"
    continue
  fi
  if (( _waited >= ${LOCK_WAIT:-0} )); then
    echo "Another newscanner run is in progress; skipping."
    exit "${LOCK_SKIP_STATUS:-0}"
  fi
  sleep 5
  _waited=$((_waited + 5))
done
trap 'rm -rf "$LOCK_DIR"' EXIT
