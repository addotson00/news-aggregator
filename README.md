# News Aggregators

A hub of config-driven news aggregators, built with the NewScanner engine in `src/newscanner/`. Each aggregator pulls RSS/Atom feeds (and Google News sitemaps), tags articles by keyword group, and writes static JSON. GitHub Pages serves a searchable, filterable page for each one.

**Live:** https://addotson00.github.io/news-aggregator/

| Aggregator | Config | URL |
|---|---|---|
| AI in Higher Education | `topics/AI-HE.yaml` | [`/news-aggregator/AI-HE/`](https://addotson00.github.io/news-aggregator/AI-HE/) |

```
topics/<slug>.yaml ──► newscanner run ──► docs/data/<slug>/*.json  +  docs/<slug>/index.html
                                          docs/index.html (redirects to the default aggregator)
```

## Quick start

```bash
uv run newscanner run          # fetch every topic in topics/
uv run newscanner serve        # preview at http://127.0.0.1:8000
```

**Without uv** (for example, on a dedicated server), install the three dependencies from the OS and use `python3` directly:

```bash
sudo apt install python3-feedparser python3-httpx python3-yaml
PYTHONPATH=src python3 -m newscanner.cli run
```

`scripts/fetch.sh` and `scripts/publish.sh` detect this automatically: they use uv when it's installed and fall back to `python3` when it isn't.

Other commands:

```bash
uv run newscanner run --topic AI-HE                  # one aggregator only
uv run newscanner run --force                        # ignore 304 caching, refetch everything
uv run newscanner discover https://example.com/      # find a site's RSS/Atom/news-sitemap
```

## Adding an aggregator

1. Copy `topics/AI-HE.yaml` to `topics/<Slug>.yaml`. Set a new `slug`, `title`, and `description`, then edit the sources and keywords. The slug becomes the URL path, so `slug: Grants` is served at `/news-aggregator/Grants/`. Use letters, digits, `-`, and `_`, and avoid `assets` and `data`.
2. Run `newscanner run`. It fetches the new aggregator, generates `docs/<Slug>/index.html`, and adds it to the switcher on every aggregator page.
3. The next `publish.sh` pushes it live. The existing Hermes schedule already covers it, because `run` processes every YAML in `topics/`.

The site root `/news-aggregator/` redirects to the aggregator with `default: true` in its YAML, or to the first one if none is marked.

To remove or rename an aggregator, delete or rename its YAML. The next full run drops it from the switcher. Its data folder `docs/data/<old-slug>/` and page `docs/<old-slug>/` are left in place, so delete them by hand if you don't want them published anymore.

The per-aggregator page is generated from `src/newscanner/site/topic.html`. Edit the template, not the generated copies.

Key settings in a topic file:

| Setting | Meaning |
|---|---|
| `mode: tag` | Keep every article and tag matches. The site toggles between *Keyword matches* and *All articles*. |
| `mode: filter` | Store only articles that match. |
| `keywords` | Named groups of terms. Each group becomes a filter tag on the site. |
| `match_groups` | Which groups decide whether an article is a "match". Other groups only add facet tags. |
| `exclude` | Terms that veto a match. |
| `retention_days` | Default 30. Older articles move to `docs/data/<slug>/archive/YYYY-MM.json`. The site's **All time** option loads the archive on demand. |

Keyword syntax: plain text is case-insensitive and whole-word. A trailing `*` is a prefix wildcard. `/regex/` is a raw regex and is case-sensitive, which is how `/\bA\.?I\.?\b/` matches "AI" and "A.I." but not "ai".

Tags are recomputed for every stored article on each run, so keyword edits apply to past articles too.

## Source types

| type | Use for |
|---|---|
| `rss` | RSS 2.0 or Atom feeds. Add `pages: N` to also read `?paged=2..N` (WordPress feeds). |
| `news_sitemap` | Google News sitemaps (title and date, no summary) |
| `html` | Placeholder for future scrapers. Not implemented yet. |

The fetcher identifies itself honestly (`NewScanner/x.y`), checks robots.txt, and sends conditional requests (ETag/Last-Modified), so unchanged feeds cost a `304` with no body. Only headlines, short excerpts, and links are stored.

## Completeness: feeds are rolling windows

A feed only shows the latest N items. If an outlet publishes more than N between fetches, the extras slide off unseen. Three measures handle this:

1. **Fetch often, publish daily.** `scripts/fetch.sh` runs hourly and `scripts/publish.sh` runs once a day.
2. **Feed paging** (`pages:`) reads deeper into feeds that support it.
3. **Gap detection.** Each run records the article ids it saw per source, in `state/<topic>.json`. If a later fetch shares *no* ids with the previous one, items may have been missed in between. The run logs a warning, and the site footer shows "possible gap" for 7 days. If a source keeps gapping, fetch more often or raise its `pages`.

## Duplicates and stale articles

- **Same article, different URL.** URLs are canonicalized (tracking parameters, trailing slashes, and fragments are removed) before hashing into the article id.
- **Same story at two outlets (syndication).** An identical headline within 4 days becomes one card with "Also at …" links. Titles under 25 characters ("Quick Takes") are never merged.
- **Old items still sitting in deep feeds** (EdSurge's feed goes back months) go straight into the archive and never reappear as "new".
- **Expiry.** Each run moves articles past `retention_days` into monthly archive files. Unchanged archive files aren't rewritten, so git only sees real changes.

**Expected size** for ~55 articles/day: the current window is about 1,650 articles (~1.2 MB JSON, ~300 KB gzipped over Pages), and the archive grows by ~1.2 MB a month (~15 MB/year). That's comfortable for GitHub Pages (1 GB limit) and for in-browser search. A multi-year archive might eventually want yearly roll-ups or Pagefind.

## Scheduling

`scripts/fetch.sh` (hourly) and `scripts/publish.sh` (daily) share a lock, so they never overlap. Example crontab entries:

```
5 * * * *  cd /path/to/news-aggregator && scripts/fetch.sh   >> ~/newscanner.log 2>&1
30 6 * * * cd /path/to/news-aggregator && scripts/publish.sh >> ~/newscanner.log 2>&1
```

For a Hermes agent, call the same two scripts. `publish.sh` exits non-zero if a topic had no working sources. Per-source health (errors, last success, gaps) is in `docs/data/manifest.json`.

**GitHub Actions (optional):** `.github/workflows/aggregate.yml` runs the pipeline on GitHub's servers. Runners have no disk between runs, so each run commits. Its schedule (every 2 hours) is commented out so it won't race with a local job. Enable one scheduler, not both.

## Publishing on GitHub Pages

This repo deploys from a branch: **Settings → Pages → Deploy from a branch → `main` → `/docs`**. Each push redeploys within a minute or two. Each aggregator is at `/news-aggregator/<slug>/`, and the root redirects to the default one.

On free accounts, every *public* repo can have its own Pages site at `<user>.github.io/<repo>/`. The one-site limit applies only to the user site, `<user>.github.io`. Hosting all aggregators in this one repo is a choice for shared code and a single schedule, not a requirement.

## Status of current sources (AI-HE)

| Outlet | Method |
|---|---|
| Inside Higher Ed, Higher Ed Dive, EdSurge, NYT Education | RSS |
| Hechinger Report (2 pages), PIE News (3 pages) | RSS with paging |
| Chronicle of Higher Education | Atom (`/index.atom`) |
| Times Higher Education | Google News sitemap |
| Diverse, eCampus News, Campus Technology, NSC Research Center, CCRC | RSS (verified replacements for the earlier feeds.opml list) |
| The Conversation (Education) | Atom |
| University World News | No feed found. Disabled until an HTML scraper exists. |

## Layout

```
topics/                  one YAML per aggregator (slug = URL path)
src/newscanner/          engine: config, fetchers, normalize, tagging, store, state, cli
src/newscanner/site/     template for per-aggregator pages
docs/                    GitHub Pages root
  index.html             generated redirect to the default aggregator
  <slug>/index.html      generated aggregator page
  assets/                shared JS/CSS
  data/                  manifest.json + data/<slug>/articles.json, archive/
state/                   per-source fetch state (validators, seen ids, gaps); committed
scripts/fetch.sh         hourly: fetch only
scripts/publish.sh       daily: fetch + commit + push
.github/workflows/       optional scheduled run on GitHub
```
