#!/usr/bin/env python3
"""
Higher Ed + AI Daily Briefing Generator

Fetches RSS feeds, scores for AI relevance, classifies by theme,
and generates a static HTML briefing for GitHub Pages.
"""

import os
import sys
import json
import yaml
import feedparser
import hashlib
import re
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse
import html

# Optional: rapidfuzz for better dedupe (install if available)
try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


class BriefingGenerator:
    def __init__(self, feeds_file="feeds.opml", taxonomy_file="taxonomy.yaml",
                 template_file="templates/briefing.j2", output_dir="docs"):
        self.feeds_file = feeds_file
        self.taxonomy_file = taxonomy_file
        self.template_file = template_file
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.taxonomy = self._load_taxonomy()
        self.feeds = self._parse_opml()

    def _load_taxonomy(self):
        with open(self.taxonomy_file) as f:
            return yaml.safe_load(f)

    def _parse_opml(self):
        """Parse OPML file and return list of feed dicts."""
        import xml.etree.ElementTree as ET
        tree = ET.parse(self.feeds_file)
        root = tree.getroot()
        feeds = []
        for outline in root.findall(".//outline[@xmlUrl]"):
            feeds.append({
                "title": outline.get("title", outline.get("text", "Unknown")),
                "url": outline.get("xmlUrl"),
                "html_url": outline.get("htmlUrl", ""),
                "category": outline.get("text", "General")
            })
        return feeds

    def fetch_all_feeds(self, hours_back=24):
        """Fetch all feeds and return normalized items."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        all_items = []

        for feed in self.feeds:
            try:
                parsed = feedparser.parse(feed["url"])
                if parsed.bozo and not parsed.entries:
                    print(f"Warning: Failed to parse {feed['title']}: {parsed.bozo_exception}")
                    continue

                for entry in parsed.entries:
                    # Parse publication date
                    pub_date = self._parse_date(entry)
                    if pub_date and pub_date < datetime.now(timezone.utc) - timedelta(hours=48):
                        continue  # Skip items older than 48 hours

                    item = {
                        "title": html.unescape(entry.get("title", "")).strip(),
                        "link": entry.get("link", ""),
                        "summary": html.unescape(entry.get("summary", entry.get("description", ""))).strip(),
                        "published": pub_date.isoformat() if pub_date else None,
                        "source": feed["title"],
                        "source_url": feed.get("html_url", ""),
                        "feed_category": feed.get("category", "General"),
                    }
                    # Generate content hash for dedupe
                    content = f"{item['title']}{item['link']}"
                    item["hash"] = hashlib.md5(content.encode()).hexdigest()[:12]
                    all_items.append(item)

            except Exception as e:
                print(f"Error fetching {feed['title']}: {e}")

        return all_items

    def _parse_date(self, entry):
        """Parse various date formats from feed entries."""
        for key in ["published_parsed", "updated_parsed", "created_parsed"]:
            if hasattr(entry, key) and getattr(entry, key):
                try:
                    return datetime(*entry[key][:6], tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    continue
        # Try string parsing
        for key in ["published", "updated", "created"]:
            if entry.get(key):
                try:
                    # Try common formats
                    for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z",
                                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
                        try:
                            return datetime.strptime(entry[key], fmt).replace(tzinfo=timezone.utc)
                        except ValueError:
                            continue
                except (ValueError, TypeError):
                    continue
        return None

    def dedupe_items(self, items):
        """Deduplicate items by hash and title similarity."""
        seen_hashes = set()
        unique = []

        for item in items:
            if item["hash"] in seen_hashes:
                continue

            # Check title similarity with already accepted items
            is_dupe = False
            for existing in unique:
                if self._titles_similar(item["title"], existing["title"]):
                    is_dupe = True
                    break

            if not is_dupe:
                seen_hashes.add(item["hash"])
                unique.append(item)

        return unique

    def _titles_similar(self, title1, title2, threshold=0.85):
        """Check if two titles are similar."""
        if HAS_RAPIDFUZZ:
            return fuzz.ratio(title1.lower(), title2.lower()) / 100 >= threshold
        # Simple fallback: check word overlap
        words1 = set(title1.lower().split())
        words2 = set(title2.lower().split())
        if not words1 or not words2:
            return False
        overlap = len(words1 & words2) / max(len(words1), len(words2))
        return overlap >= threshold

    def score_ai_relevance(self, item):
        """Score item for AI relevance."""
        text = f"{item['title']} {item.get('summary', '')}".lower()
        score = 0

        # Title match boost
        title_lower = item["title"].lower()
        for kw in self.taxonomy.get("ai_boost_keywords", []):
            if kw.lower() in title_lower:
                score += self.taxonomy["scoring"].get("title_match_boost", 3)

        # AI keyword weight
        for kw in self.taxonomy.get("ai_boost_keywords", []):
            if kw.lower() in text:
                score += self.taxonomy["scoring"].get("ai_keyword_weight", 2)

        # Theme keyword weight
        for theme, config in self.taxonomy.get("themes", {}).items():
            for kw in config.get("keywords", []):
                if kw.lower() in text:
                    score += self.taxonomy["scoring"].get("theme_keyword_weight", 1)

        return score

    def classify_themes(self, item):
        """Classify item into themes."""
        text = f"{item['title']} {item.get('summary', '')}".lower()
        tags = []

        for theme, config in self.taxonomy.get("themes", {}).items():
            for kw in config.get("keywords", []):
                if kw.lower() in text:
                    tags.append(theme)
                    break

        return tags if tags else ["General Higher Ed"]

    def process_items(self, items):
        """Score, tag, and enrich items."""
        processed = []
        for item in items:
            item["ai_score"] = self.score_ai_relevance(item)
            item["tags"] = self.classify_themes(item)
            item["is_ai_relevant"] = item["ai_score"] >= self.taxonomy["scoring"].get("min_ai_score_for_ai_section", 2)
            processed.append(item)
        return processed

    def group_by_theme(self, items):
        """Group items by theme, sorted by AI score."""
        theme_groups = defaultdict(list)
        for item in items:
            for tag in item["tags"]:
                theme_groups[tag].append(item)

        # Sort each group by AI score desc, then recency
        for theme, group in theme_groups.items():
            theme_groups[theme] = sorted(group,
                                         key=lambda x: (-x["ai_score"], x.get("published", "")),
                                         reverse=False)[:5]  # Top 5 per theme

        # Sort themes by max AI score desc
        sorted_themes = sorted(theme_groups.items(),
                               key=lambda x: -max(item["ai_score"] for item in x[1]))
        return sorted_themes

    def render_briefing(self, theme_groups, all_items, stats):
        """Render the briefing using Jinja2 template."""
        from jinja2 import Environment, FileSystemLoader

        env = Environment(loader=FileSystemLoader("templates"))
        template = env.get_template("briefing.j2")

        today = datetime.now(timezone.utc).strftime("%B %d, %Y")
        utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Get top AI-relevant items for "Top Signals"
        ai_items = [i for i in all_items if i["is_ai_relevant"]]
        top_ai = sorted(ai_items, key=lambda x: -x["ai_score"])[:5]

        html_content = template.render(
            date=today,
            generated_utc=utc_now,
            theme_groups=theme_groups,
            top_ai_signals=top_ai,
            total_items=len(all_items),
            ai_items=len([i for i in all_items if i["is_ai_relevant"]]),
            total_feeds=len(self.feeds),
        )

        output_file = self.output_dir / "index.html"
        with open(output_file, "w") as f:
            f.write(html_content)

        # Also write JSON for archive/API
        json_file = self.output_dir / "briefing.json"
        with open(json_file, "w") as f:
            json.dump({
                "date": datetime.now(timezone.utc).date().isoformat(),
                "generated_utc": datetime.now(timezone.utc).isoformat(),
                "stats": stats,
                "top_ai": [{"title": i["title"], "link": i["link"], "source": i["source"],
                            "ai_score": i["ai_score"], "tags": i["tags"]}
                           for i in ai_items[:10]],
                "by_theme": {theme: [{"title": i["title"], "link": i["link"],
                                      "source": i["source"], "ai_score": i["ai_score"]}
                                     for i in items]
                             for theme, items in theme_groups}
            }, f, indent=2)

        return output_file

    def run(self, hours_back=24):
        """Main pipeline."""
        print(f"Fetching {len(self.feeds)} feeds...")
        raw_items = self.fetch_all_feeds(hours_back)
        print(f"Fetched {len(raw_items)} raw items")

        unique_items = self.dedupe_items(raw_items)
        print(f"After dedupe: {len(unique_items)} items")

        processed = self.process_items(unique_items)
        print(f"Scored and tagged {len(processed)} items")

        theme_groups = self.group_by_theme(processed)
        print(f"Grouped into {len(theme_groups)} themes")

        stats = {
            "total_items": len(processed),
            "ai_relevant": len([i for i in processed if i["is_ai_relevant"]]),
            "themes": len(theme_groups),
            "feeds_fetched": len(self.feeds),
        }

        output_file = self.render_briefing(theme_groups, processed, stats)
        print(f"Briefing written to {output_file}")
        return output_file


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate Higher Ed + AI Daily Briefing")
    parser.add_argument("--feeds", default="feeds.opml", help="OPML feeds file")
    parser.add_argument("--taxonomy", default="taxonomy.yaml", help="Taxonomy YAML file")
    parser.add_argument("--template", default="templates/briefing.j2", help="Jinja2 template file")
    parser.add_argument("--output", default="docs", help="Output directory (for GitHub Pages)")
    parser.add_argument("--hours", type=int, default=24, help="Hours of history to fetch")
    args = parser.parse_args()

    generator = BriefingGenerator(
        feeds_file=args.feeds,
        taxonomy_file=args.taxonomy,
        template_file=args.template,
        output_dir=args.output,
    )
    generator.run(hours_back=args.hours)


if __name__ == "__main__":
    main()