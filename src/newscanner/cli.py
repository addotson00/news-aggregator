"""Command line entry point: `uv run newscanner <command>`."""

from __future__ import annotations

import argparse
import functools
import hashlib
import html
import json
import http.server
import re
import sys
import time
from pathlib import Path

from collections import Counter
from datetime import datetime, timedelta, timezone

from .config import Source, Topic, load_topics
from .fetchers import (FetchError, TransientFetchError, fetch_page_text, fetch_source, host_refusal,
                       make_client)
from .normalize import build_article, now_iso, parse_iso
from .state import TopicState
from .store import TopicStore, prune_manifest, update_manifest
from .tagging import Tagger

SEEN_IDS_KEEP = 400  # per source; must exceed the most entries one fetch can return
TEXT_CHECKED_KEEP = 3000  # per source; ids whose article page was already read


def fetch_one(src: Source, topic: Topic, client, store: TopicStore, tagger: Tagger, st: dict, force: bool = False) -> dict:
    """Fetch one source, ingest its entries, update its state. Returns a status dict for the manifest."""
    now = now_iso()
    t0 = time.monotonic()
    try:
        res = fetch_source(client, src, None if force else st.get("validators"))
    except FetchError as e:
        st["consecutive_errors"] = st.get("consecutive_errors", 0) + 1
        st["last_error"] = {"at": now, "error": str(e)}
        print(f"  ✗ {src.name:<36} {e}")
        return {"state": "error", "error": str(e), "checked": now}

    st["validators"] = res.validators
    st["consecutive_errors"] = 0
    prev_ok = st.get("last_ok")
    st["last_ok"] = now

    if res.not_modified:
        print(f"  = {src.name:<36} not modified since last fetch")
        return {"state": "ok", "not_modified": True, "checked": now, "fetched": 0, "new": 0}

    counts: Counter[str] = Counter()
    ids, oldest = [], None
    checked = st.get("text_checked", [])
    checked_set = set(checked)
    newly_checked: list[str] = []
    budget = topic.page_fetch_limit
    recent = datetime.now(timezone.utc) - timedelta(days=topic.text_check_days)
    page_errors: Counter[str] = Counter()
    for r in res.entries:
        art = build_article(r, src.id, src.name)
        if not art:
            continue
        feed_text = art.pop("_fulltext", "")
        ids.append(art["id"])
        if art["published"] and (oldest is None or art["published"] < oldest):
            oldest = art["published"]

        text = None
        if topic.text_check:
            stored = store.articles.get(art["id"])
            if stored and stored.get("mentions"):
                art["mentions"] = stored["mentions"]  # keep evidence from an earlier page read
            if feed_text:
                text = feed_text
                counts["text_feed"] += 1
            elif src.text_check and art["id"] not in checked_set:
                published = parse_iso(art["published"])
                refused = host_refusal(art["url"])
                if published and published < recent:
                    counts["text_too_old"] += 1
                elif refused:  # host already said no this run: no request, no budget used
                    page_errors[f"host refused pages this run ({refused[0]})"] += 1
                    if refused[1]:
                        counts["text_pending"] += 1
                    else:
                        newly_checked.append(art["id"])
                elif budget <= 0:
                    counts["text_pending"] += 1
                else:
                    budget -= 1
                    try:
                        text = fetch_page_text(client, art["url"])
                        counts["text_page"] += 1
                        newly_checked.append(art["id"])
                    except TransientFetchError as e:
                        page_errors[str(e)] += 1
                        counts["text_pending"] += 1  # retry next run
                    except FetchError as e:
                        page_errors[str(e)] += 1
                        newly_checked.append(art["id"])  # permanent (404, robots): don't retry

        tagger.apply(art, text)
        if text is not None and art["found_in"] == "text":
            counts["text_match"] += 1
        if topic.mode == "filter" and not art["match"]:
            counts["skipped"] += 1
            continue
        counts[store.ingest(art)] += 1

    # Gap check: if nothing in this fetch was seen before, items may have slid
    # off the feed between runs. Overlap with last run's ids proves continuity.
    seen = st.get("seen_ids", [])
    gap = None
    if prev_ok and seen and ids and not set(ids) & set(seen):
        gap = {"detected": now, "previous_fetch": prev_ok, "oldest_item": oldest}
        st.setdefault("gaps", []).append(gap)
        st["gaps"] = st["gaps"][-20:]
    st["seen_ids"] = (ids + [i for i in seen if i not in set(ids)])[:SEEN_IDS_KEEP]
    if topic.text_check:
        st["text_checked"] = (newly_checked + checked)[:TEXT_CHECKED_KEEP]
        if counts["text_pending"]:
            st["validators"] = {}  # unread pages remain: refetch the feed next run instead of a 304

    new = counts["new"] + counts["archived_new"]
    extras = []
    if counts["duplicate"]:
        extras.append(f"{counts['duplicate']} dup")
    if counts["archived_new"]:
        extras.append(f"{counts['archived_new']} straight to archive")
    if topic.text_check:
        if counts["text_page"]:
            extras.append(f"{counts['text_page']} pages read")
        if counts["text_match"]:
            extras.append(f"{counts['text_match']} matched in article text")
        if counts["text_pending"]:
            extras.append(f"{counts['text_pending']} pages pending")
    pages = f" over {res.pages_read} pages" if res.pages_read > 1 else ""
    print(
        f"  ✓ {src.name:<36} {len(ids):>3} entries{pages}, {counts['new']:>3} new"
        + (f" ({', '.join(extras)})" if extras else "")
        + f"  [{time.monotonic() - t0:.1f}s]"
    )
    for err, n in page_errors.most_common(3):
        print(f"    · article pages: {n}× {err}")
    if gap:
        print(f"    ⚠ possible gap: no overlap with previous fetch ({prev_ok}); oldest item now {oldest}."
              " Fetch more often or raise `pages`.")
    return {
        "state": "ok",
        "checked": now,
        "fetched": len(ids),
        "pages": res.pages_read,
        "new": new,
        "duplicates": counts["duplicate"],
        "text": ("feed" if counts["text_feed"] else "pages" if src.text_check else "headline")
        if topic.text_check else None,
        "pages_read": counts["text_page"],
        "pages_pending": counts["text_pending"],
        "gap": gap,
    }


def run_topic(topic: Topic, out_dir: Path, state_dir: Path, client, force: bool = False,
              ignore_schedule: bool = False) -> bool:
    state = TopicState(state_dir, topic.slug)
    last_run = parse_iso(state.data.get("last_run"))
    if topic.fetch_every_minutes and last_run and not (force or ignore_schedule):
        # 10 min slack so an hourly job reliably hits a 120-min interval every 2nd run
        due = last_run + timedelta(minutes=max(topic.fetch_every_minutes - 10, 0))
        if datetime.now(timezone.utc) < due:
            print(f"\n== {topic.title} ({topic.slug}): not due (every {topic.fetch_every_minutes} min,"
                  f" last run {state.data['last_run']})")
            return True
    print(f"\n== {topic.title} ({topic.slug})")
    state.data["last_run"] = now_iso()
    store = TopicStore(out_dir, topic.slug, topic.retention_days)
    tagger = Tagger(topic)
    source_status = []
    any_ok = False

    for src in topic.sources:
        base = {"id": src.id, "name": src.name, "homepage": src.homepage or src.url, "type": src.type}
        if not src.enabled:
            print(f"  - {src.name:<36} disabled ({src.note})")
            source_status.append({**base, "state": "disabled", "note": src.note})
            continue
        st = state.source(src.id)
        status = fetch_one(src, topic, client, store, tagger, st, force)
        any_ok = any_ok or status["state"] == "ok"
        status["last_ok"] = st.get("last_ok")
        status["gaps_total"] = len(st.get("gaps", []))
        status["last_gap"] = (st.get("gaps") or [None])[-1]
        status.pop("gap", None)
        source_status.append({**base, **status})

    # Re-tag everything so keyword edits apply retroactively.
    for art in store.all():
        tagger.apply(art)
    if topic.mode == "filter":
        store.drop({a["id"] for a in store.all() if not a["match"]})

    expired = store.expire()
    store.retag_archive(tagger.apply)
    archive = store.save()
    state.save()

    arts = store.all()
    matches = sum(a["match"] for a in arts)
    update_manifest(
        out_dir,
        {
            "slug": topic.slug,
            "title": topic.title,
            "description": topic.description,
            "mode": topic.mode,
            "retention_days": topic.retention_days,
            "tags": list(topic.keywords),
            "match_groups": topic.match_groups,
            "hidden_tags": topic.hidden_tags,
            "text_check": topic.text_check,
            "fetch_every_minutes": topic.fetch_every_minutes,
            "article_count": len(arts),
            "match_count": matches,
            "archive": archive,
            "updated_at": now_iso(),
            "sources": source_status,
        },
    )
    archived_total = sum(m["count"] for m in archive)
    print(f"  → {len(arts)} current articles ({matches} matches), {expired} expired to archive this run,"
          f" {archived_total} in archive")
    return any_ok


SITE_TEMPLATE = Path(__file__).parent / "site" / "topic.html"


def asset_version(out_dir: Path) -> str:
    """Short content hash of the shared JS/CSS, appended as ?v= so browsers and
    the Pages CDN (max-age=600) pick up changes immediately."""
    h = hashlib.sha1()
    for name in ("app.js", "style.css"):
        p = out_dir / "assets" / name
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


def write_topic_page(out_dir: Path, topic: Topic) -> None:
    """docs/<slug>/index.html: the per-aggregator page, served at <site>/<slug>/."""
    page = (
        SITE_TEMPLATE.read_text()
        .replace("{{ASSET_VERSION}}", asset_version(out_dir))
        .replace("{{SLUG}}", html.escape(topic.slug))
        .replace("{{TITLE}}", html.escape(topic.title))
        .replace("{{DESCRIPTION}}", html.escape(topic.description))
    )
    dest = out_dir / topic.slug / "index.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or dest.read_text() != page:
        dest.write_text(page)


def write_root_redirect(out_dir: Path, topics: list[Topic]) -> None:
    """docs/index.html: redirect the site root to the default aggregator."""
    if not topics:
        return
    target = next((t for t in topics if t.default), topics[0])
    href = html.escape(f"{target.slug}/")
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <!-- Generated by `newscanner run`: redirects to the default aggregator (set `default: true` in its YAML). -->
  <title>{html.escape(target.title)}</title>
  <meta http-equiv="refresh" content="0; url={href}">
  <link rel="canonical" href="{href}">
  <script>location.replace({json.dumps(target.slug + "/")} + location.search + location.hash);</script>
</head>
<body>
  <p>Redirecting to <a href="{href}">{html.escape(target.title)}</a>…</p>
</body>
</html>
"""
    dest = out_dir / "index.html"
    if not dest.exists() or dest.read_text() != page:
        dest.write_text(page)


def cmd_run(args) -> int:
    topics = load_topics(Path(args.topics), args.topic)
    if not topics:
        print("No topics found.", file=sys.stderr)
        return 1
    out = Path(args.out)
    with make_client() as client:
        results = [run_topic(t, out, Path(args.state), client, args.force, ignore_schedule=bool(args.topic))
                   for t in topics]
    for t in topics:
        write_topic_page(out, t)
    write_root_redirect(out, load_topics(Path(args.topics)))  # always considers every topic
    if not args.topic:  # full run: forget topics whose YAML was removed or renamed
        for slug in prune_manifest(out, {t.slug for t in topics}):
            print(f"Removed stale topic {slug!r} from manifest (its data folder is left in place).")
    if not all(results):
        print("\nAt least one topic had no successful sources.", file=sys.stderr)
        return 1
    return 0


def cmd_serve(args) -> int:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=args.out)
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as httpd:
        print(f"Serving {args.out}/ at http://127.0.0.1:{args.port}/  (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


FEED_LINK_RE = re.compile(r"<link[^>]+type=[\"']application/(?:rss|atom)\+xml[\"'][^>]*>", re.I)
HREF_RE = re.compile(r"href=[\"']([^\"']+)[\"']", re.I)


def cmd_discover(args) -> int:
    """Look for RSS/Atom feeds advertised on a page, plus common feed paths."""
    from urllib.parse import urljoin

    from .fetchers import robots_allows

    with make_client() as client:
        found = []
        try:
            r = client.get(args.url)
            print(f"{args.url} -> HTTP {r.status_code}")
            for tag in FEED_LINK_RE.findall(r.text):
                if m := HREF_RE.search(tag):
                    found.append(urljoin(args.url, m.group(1)))
        except Exception as e:  # noqa: BLE001
            print(f"page fetch failed: {e}")
        base = args.url.split("/", 3)
        origin = "/".join(base[:3])
        for path in ("/feed/", "/rss.xml", "/index.atom", "/feed.xml", "/atom.xml", "/rss", "/googlenews.xml", "/news-sitemap.xml"):
            found.append(origin + path)
        seen = set()
        for u in found:
            if u in seen:
                continue
            seen.add(u)
            if not robots_allows(client, u):
                print(f"  robots.txt disallows {u}")
                continue
            try:
                r = client.get(u)
            except Exception:  # noqa: BLE001
                continue
            ctype = r.headers.get("content-type", "")
            n = len(re.findall(r"<(?:item|entry|url)[\s>]", r.text))
            if r.status_code == 200 and n:
                print(f"  ✓ {u}  [{ctype.split(';')[0]}] {n} entries")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="newscanner")
    p.add_argument("--topics", default="topics", help="directory of topic YAML files")
    p.add_argument("--out", default="docs", help="static site directory (GitHub Pages root)")
    p.add_argument("--state", default="state", help="directory for per-source fetch state")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="fetch sources, tag, and write JSON")
    r.add_argument("--topic", action="append", help="only run this topic slug (repeatable)")
    r.add_argument("--force", action="store_true", help="ignore ETag/Last-Modified and refetch everything")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("serve", help="preview the static site locally")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    d = sub.add_parser("discover", help="find RSS/Atom/news-sitemap feeds for a site")
    d.add_argument("url")
    d.set_defaults(func=cmd_discover)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
