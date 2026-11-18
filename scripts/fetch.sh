#!/usr/bin/env bash
# Frequent job (hourly): fetch every topic and update local JSON. No git.
# Frequent fetching is what stops articles sliding off short feeds unseen;
# conditional requests (ETag/Last-Modified) keep it cheap for publishers.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/_lock.sh
source scripts/_run.sh
echo "[$(date -u +%FT%TZ)] fetch"
newscanner run "$@"
