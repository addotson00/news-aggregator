# Running on Hermes (or rebuilding the machine)

How the aggregators are run in production, and how to rebuild that setup from this repo alone.

**Current host:** an Ubuntu VM (`ubuntu@add`) running the Hermes agent. The working copy is `~/news-aggregator`. Hermes's scheduler runs two script-only jobs (no LLM calls):

| Job | Schedule (America/Anchorage) | Runs | Delivers to Telegram |
|---|---|---|---|
| `newsagg-fetch` | `5 * * * *` (hourly) | `newsagg-fetch.sh` → `scripts/fetch.sh` | Only problems (`✗` failed source, `⚠` possible gap, failed push). Healthy runs are silent. |
| `newsagg-publish` | `30 6 * * *` (daily) | `newsagg-publish.sh` → `scripts/publish.sh` | A one-line summary per aggregator, plus the push result |

`fetch.sh` pushes to GitHub whenever a run finds new articles. `publish.sh` pushes once a day regardless, which refreshes source-health timestamps. Topics with `fetch_every_minutes` (UA: 120) skip hourly runs until they're due.

The wrapper scripts in this folder are copies of what lives in `~/.hermes/scripts/`. Hermes only runs scripts from that directory.

## Rebuild from scratch

```bash
# 1. System packages (no uv needed; the scripts fall back to python3)
sudo apt install -y git gh python3-feedparser python3-httpx python3-yaml

# 2. GitHub access as addotson00, and git identity (GitHub noreply address)
gh auth login                      # choose HTTPS; interactive
gh auth setup-git                  # git push uses the gh token (no password prompts in cron)
git config --global user.name  "addotson00"
git config --global user.email "212404531+addotson00@users.noreply.github.com"

# 3. Working copy (fetch state in state/ comes along, so 304 caching and gap tracking continue)
git clone https://github.com/addotson00/news-aggregator ~/news-aggregator
cd ~/news-aggregator
git config --local --get user.email && echo "remove repo-local identity: git config --local --unset user.email"

# 4. Smoke test (no push)
NO_PUSH=1 scripts/fetch.sh --force

# 5. Hermes wrappers + jobs
mkdir -p ~/.hermes/scripts ~/.hermes/logs
cp deploy/hermes/newsagg-*.sh ~/.hermes/scripts/ && chmod 775 ~/.hermes/scripts/newsagg-*.sh
hermes cron create "5 * * * *"  --name newsagg-fetch   --no-agent --script newsagg-fetch.sh \
  --timezone America/Anchorage --deliver telegram --catch-up off
hermes cron create "30 6 * * *" --name newsagg-publish --no-agent --script newsagg-publish.sh \
  --timezone America/Anchorage --deliver telegram --catch-up on

# 6. Verify
hermes cron doctor
~/.hermes/scripts/newsagg-fetch.sh;   echo "exit=$?"   # expect no output, exit 0
~/.hermes/scripts/newsagg-publish.sh; echo "exit=$?"   # expect summary + "Pushed …" or "No data changes."
```

GitHub Pages must be set to **Deploy from a branch → `main` → `/docs`** (repo Settings → Pages).

## Without Hermes

Any scheduler works. Example crontab:

```
5 * * * *  cd ~/news-aggregator && scripts/fetch.sh   >> ~/newscanner.log 2>&1
30 6 * * * cd ~/news-aggregator && scripts/publish.sh >> ~/newscanner.log 2>&1
```

Both scripts exit non-zero if a push failed or a topic had no working sources, and print lines starting with `✗`/`⚠` for anything worth an alert.

## Operating notes

- **Logs:** `~/.hermes/logs/newsagg.log` has full output of every run. `hermes cron runs <job-id>` shows run history.
- **After editing a wrapper on Hermes,** copy it back into this folder and commit, so this folder stays the source of truth.
- **Concurrency:** `scripts/_lock.sh` allows one pipeline run at a time. Publish waits up to 10 minutes for a running fetch.
- **Rate limits:** TownNews-hosted outlets (News-Miner, Ketchikan, Kodiak, Your Alaska Link) throttle article-page reads. Backlogs clear over a few runs; lower `page_fetch_limit` in `topics/UA.yaml` if they keep failing.
