# Sourced by fetch.sh / publish.sh. Runs the CLI with uv when available,
# otherwise with system python3 (deps from apt: python3-feedparser,
# python3-httpx, python3-yaml).
newscanner() {
  if command -v uv >/dev/null 2>&1; then
    uv run newscanner "$@"
  else
    PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m newscanner.cli "$@"
  fi
}
