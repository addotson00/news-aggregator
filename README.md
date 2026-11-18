# Higher Ed + AI Daily Briefing

Automated daily briefing that aggregates higher education news from 30+ RSS feeds, scores articles for AI relevance, classifies them by theme, and publishes a static briefing to GitHub Pages.

**Live Briefing:** https://addotson00.github.io/news-aggregator/

---

## Features

- **30+ Curated Feeds** — Core higher ed publications, policy orgs, AI/edtech sources, funding agencies
- **AI Relevance Scoring** — Automatically identifies AI-related content using keyword matching
- **Theme Classification** — 12-theme taxonomy (AI in Teaching, Policy, Research, Operations, Workforce, etc.)
- **GitHub Pages Output** — Static HTML briefing deployed automatically via GitHub Actions
- **JSON Archive** — Machine-readable daily briefing data for further analysis
- **Configurable** — Edit `taxonomy.yaml` to adjust themes, keywords, and scoring weights

---

## Quick Start

### 1. Enable GitHub Pages
1. Go to **Settings → Pages** in your repo
2. Under **Build and deployment**, select **GitHub Actions** as the source
3. The workflow will automatically deploy to `https://<your-username>.github.io/news-aggregator/`

### 2. Trigger First Run
- Go to **Actions → Daily Higher Ed + AI Briefing → Run workflow**
- Or wait for the scheduled run (10:00 AM UTC daily)

### 3. View Briefing
After the workflow completes, visit: `https://addotson00.github.io/news-aggregator/`

---

## Configuration

### `feeds.opml` — RSS Feed Sources
Edit to add/remove sources. Structure:
```xml
<outline type="rss" text="Source Name" title="Source Name"
         xmlUrl="https://example.com/rss.xml" htmlUrl="https://example.com"/>
```

### `taxonomy.yaml` — Themes & Scoring
Customize themes, keywords, and scoring weights:

```yaml
themes:
  "Your Custom Theme":
    keywords: ["keyword1", "keyword2"]

ai_boost_keywords:
  - "your AI term"

scoring:
  title_match_boost: 3
  ai_keyword_weight: 2
  theme_keyword_weight: 1
  min_ai_score_for_ai_section: 2
```

### Schedule (`.github/workflows/daily-briefing.yml`)
Default: 10:00 AM UTC daily. Change the cron expression:
```yaml
on:
  schedule:
    - cron: '0 10 * * *'  # 10:00 AM UTC
```

---

## Output Files

| File | Description |
|------|-------------|
| `docs/index.html` | Main briefing (GitHub Pages entry point) |
| `docs/briefing.json` | Machine-readable daily data |
| `docs/briefing-YYYY-MM-DD.html` | Archived daily briefings (optional) |
| `feeds.opml` | Feed list (import into any RSS reader) |
| `taxonomy.yaml` | Theme/keyword configuration |

---

## Local Development

```bash
# Clone and setup
git clone https://github.com/addotson00/news-aggregator.git
cd news-aggregator
pip install -r requirements.txt

# Generate briefing locally
python briefing.py --hours 24

# Output appears in ./docs/index.html
# Open in browser to preview
```

### Command Options
```bash
python briefing.py --hours 24          # Last 24 hours (default)
python briefing.py --hours 48          # Last 48 hours
python briefing.py --feeds feeds.opml  # Custom feeds file
python briefing.py --taxonomy taxonomy.yaml  # Custom taxonomy
```

---

## Architecture

```
briefing.py          # Main pipeline: fetch → score → tag → render
feeds.opml           # RSS feed sources
taxonomy.yaml        # Themes, keywords, scoring weights
templates/briefing.j2  # Jinja2 HTML template
.github/workflows/   # GitHub Actions for automation
docs/                # GitHub Pages output (index.html, briefing.json)
```

---

## Customization Ideas

- **Add Slack/Discord webhook** in workflow for notifications
- **Add arXiv RSS** for latest AI research papers
- **Integrate Google Scholar alerts** via RSS
- **Add LLM summarization** (requires API key in repo secrets)
- **Create weekly/monthly rollups** from daily JSON archives
- **Add search/filter UI** in template for browsing archives

---

## License

MIT — Feel free to fork and customize for your institution or organization.