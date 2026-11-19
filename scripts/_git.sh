# Sourced by fetch.sh / publish.sh: shared git steps.

git_sync() {
  git pull --rebase --autostash --quiet || echo "⚠ git pull failed, continuing with local copy"
}

# True when article data changed (new/updated articles or archive), as opposed
# to only timestamps in manifest.json / state.
articles_changed() {
  [[ -n "$(git status --porcelain -- 'docs/data/*/articles.json' 'docs/data/*/archive')" ]]
}

has_unpushed() {
  [[ -n "$(git log '@{u}..HEAD' --oneline 2>/dev/null)" ]]
}

# commit_and_push MESSAGE: commit docs/data + state if changed, then push
# anything not yet on the remote (including commits a failed push left behind).
# Prints one of "Pushed <hash>." / "No data changes." / "NO_PUSH set; not pushing."
# / "✗ git push failed ..." and returns 1 only when the push failed.
commit_and_push() {
  if [[ -n "$(git status --porcelain -- docs/data state)" ]]; then
    git add docs/data state
    git commit --quiet -m "$1"
    echo "Committed $(git rev-parse --short HEAD)."
  fi
  if [[ -n "${NO_PUSH:-}" ]]; then
    echo "NO_PUSH set; not pushing."
  elif ! has_unpushed; then
    echo "No data changes."
  elif git push --quiet 2>&1; then
    echo "Pushed $(git rev-parse --short HEAD)."
  else
    echo "✗ git push failed; $(git rev-list --count '@{u}..HEAD') commit(s) waiting to be pushed."
    return 1
  fi
}
