#!/usr/bin/env python3
"""
Higher Ed + AI Daily Briefing Generator

Fetches RSS feeds, scores for AI relevance, classifies by theme,
and generates a multi-page static site for GitHub Pages.
"""

import os
import sys
import json
import yaml
import feedparser
import hashlib
import re
import markdown
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse
import html

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


class BriefingGenerator:
    def __init__(self, feeds_file="feeds.opml", taxonomy_file="taxonomy.yaml",
                 template_dir="templates", output_dir="docs"):
        self.feeds_file = feeds_file
        self.taxonomy_file = taxonomy_file
        self.template_dir = Path(template_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.taxonomy = self._load_taxonomy()
        self.feeds = self._parse_opml()
        self.md = markdown.Markdown(extensions=['fenced_code', 'tables', 'toc'])

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
                    pub_date = self._parse_date(entry)
                    if pub_date and pub_date < datetime.now(timezone.utc) - timedelta(hours=48):
                        continue

                    raw_summary = entry.get("summary", entry.get("description", ""))
                    clean_summary = self._clean_summary(raw_summary)

                    item = {
                        "title": html.unescape(entry.get("title", "")).strip(),
                        "link": entry.get("link", ""),
                        "summary": clean_summary,
                        "published": pub_date.isoformat() if pub_date else None,
                        "published_dt": pub_date,
                        "source": feed["title"],
                        "source_url": feed.get("html_url", ""),
                        "feed_category": feed.get("category", "General"),
                    }
                    content = f"{item['title']}{item['link']}"
                    item["hash"] = hashlib.md5(content.encode()).hexdigest()[:12]
                    all_items.append(item)

            except Exception as e:
                print(f"Error fetching {feed['title']}: {e}")

        return all_items

    def _parse_date(self, entry):
        for key in ["published_parsed", "updated_parsed", "created_parsed"]:
            if hasattr(entry, key) and getattr(entry, key):
                try:
                    return datetime(*entry[key][:6], tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    continue
        for key in ["published", "updated", "created"]:
            if entry.get(key):
                try:
                    for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z",
                                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
                        try:
                            return datetime.strptime(entry[key], fmt).replace(tzinfo=timezone.utc)
                        except ValueError:
                            continue
                except (ValueError, TypeError):
                    continue
        return None

    def _clean_summary(self, raw_summary):
        if not raw_summary:
            return ""
        text = html.unescape(raw_summary)
        text = re.sub(r'<img[^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'<figure[^>]*>.*?</figure>', '', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<div[^>]*class=["\'][^"\']*image[^"\']*["\'][^>]*>.*?</div>', '', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<div[^>]*class=["\'][^"\']*figure[^"\']*["\'][^>]*>.*?</div>', '', text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r'<[^>]+>', '', text)
        text = html.unescape(text)
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        text = re.sub(r'^\s*[\[\(]?\s*(image|photo|picture|video|gallery)\s*[\]\)]?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s{2,}', ' ', text)
        return text.strip()

    def dedupe_items(self, items):
        seen_hashes = set()
        unique = []
        for item in items:
            if item["hash"] in seen_hashes:
                continue
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
        if HAS_RAPIDFUZZ:
            return fuzz.ratio(title1.lower(), title2.lower()) / 100 >= threshold
        words1 = set(title1.lower().split())
        words2 = set(title2.lower().split())
        if not words1 or not words2:
            return False
        overlap = len(words1 & words2) / max(len(words1), len(words2))
        return overlap >= threshold

    def score_ai_relevance(self, item):
        text = f"{item['title']} {item.get('summary', '')}".lower()
        score = 0
        title_lower = item["title"].lower()
        for kw in self.taxonomy.get("ai_boost_keywords", []):
            if kw.lower() in title_lower:
                score += self.taxonomy["scoring"].get("title_match_boost", 3)
        for kw in self.taxonomy.get("ai_boost_keywords", []):
            if kw.lower() in text:
                score += self.taxonomy["scoring"].get("ai_keyword_weight", 2)
        for theme, config in self.taxonomy.get("themes", {}).items():
            for kw in config.get("keywords", []):
                if kw.lower() in text:
                    score += self.taxonomy["scoring"].get("theme_keyword_weight", 1)
        return score

    def classify_themes(self, item):
        text = f"{item['title']} {item.get('summary', '')}".lower()
        tags = []
        for theme, config in self.taxonomy.get("themes", {}).items():
            for kw in config.get("keywords", []):
                if kw.lower() in text:
                    tags.append(theme)
                    break
        return tags if tags else ["General Higher Ed"]

    def process_items(self, items):
        processed = []
        for item in items:
            item["ai_score"] = self.score_ai_relevance(item)
            item["tags"] = self.classify_themes(item)
            item["is_ai_relevant"] = item["ai_score"] >= self.taxonomy["scoring"].get("min_ai_score_for_ai_section", 2)
            processed.append(item)
        return processed

    def group_by_theme(self, items):
        theme_groups = defaultdict(list)
        for item in items:
            for tag in item["tags"]:
                theme_groups[tag].append(item)
        for theme, group in theme_groups.items():
            theme_groups[theme] = sorted(group, key=lambda x: (-x["ai_score"], x.get("published", "")), reverse=False)[:5]
        sorted_themes = sorted(theme_groups.items(), key=lambda x: -max(item["ai_score"] for item in x[1]))
        return sorted_themes

    def _get_all_themes(self):
        return list(self.taxonomy.get("themes", {}).keys()) + ["General Higher Ed"]

    def _render_page(self, template_name, context, output_path):
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader("templates"))
        template = env.get_template(template_name)
        html_content = template.render(**context)
        output_file = self.output_dir / output_path
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w") as f:
            f.write(html_content)
        return output_file

    def _group_feeds_by_category(self):
        grouped = defaultdict(list)
        for feed in self.feeds:
            grouped[feed.get("category", "General")].append(feed)
        return dict(grouped)

    def generate_all_pages(self, items, hours_back=24):
        processed = self.process_items(items)
        theme_groups = self.group_by_theme(processed)
        
        stats = {
            "total_items": len(processed),
            "ai_relevant": len([i for i in processed if i["is_ai_relevant"]]),
            "themes": len(set(tag for i in processed for tag in i["tags"])),
            "feeds_fetched": len(self.feeds),
        }

        all_themes = self._get_all_themes()
        today = datetime.now(timezone.utc).strftime("%B %d, %Y")
        utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Page 1: Main Briefing (AI-focused)
        ai_items = [i for i in processed if i["is_ai_relevant"]]
        top_ai = sorted(ai_items, key=lambda x: -x["ai_score"])[:5]
        
        context_base = {
            "date": datetime.now(timezone.utc).strftime("%B %d, %Y"),
            "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "total_items": len(processed),
            "ai_items": len(ai_items),
            "total_feeds": len(self.feeds),
            "theme_groups": dict(self.group_by_theme(processed)),
            "all_themes": self._get_all_themes(),
            "taxonomy_version": "v1.2",
            "stats": {
                "total_items": len(processed),
                "ai_relevant": len(ai_items),
                "themes": len(set(tag for i in processed for tag in i["tags"])),
                "feeds_fetched": len(self.feeds),
            },
        }

        # Page 1: Main Briefing (AI-focused)
        top_ai = sorted([i for i in processed if i["is_ai_relevant"]], key=lambda x: -x["ai_score"])[:5]
        
        theme_groups_dict = dict(self.group_by_theme(processed))
        
        self._render_page("briefing.j2", {
            **context_base,
            "page": "briefing",
            "top_ai_signals": top_ai,
            "theme_groups": theme_groups_dict,
        }, "index.html")

        # Page 2: All Articles with Filters
        self._render_page("all_articles.j2", {
            **context_base,
            "page": "all",
            "items": sorted(processed, key=lambda x: x.get("published_dt") or datetime.min, reverse=True),
            "all_sources": sorted(set(i["source"] for i in processed)),
        }, "all.html")

        # Page 3: Feeds & Configuration
        self._render_page("config.j2", {
            **context_base,
            "page": "config",
            "feeds": self.feeds,
            "taxonomy": self.taxonomy,
            "feeds_by_category": self._group_feeds_by_category(),
        }, "config.html")

        # Page 4: Theme Explorer
                # Page 4: Theme Explorer
        theme_groups_dict = dict(self.group_by_theme(processed))
        self._render_page("theme.j2", {
            **context_base,
            "page": "theme",
            "theme_groups": theme_groups_dict,
            "all_themes": all_themes,
            "taxonomy": self.taxonomy,
        }, "theme.html")

        # JSON Archive
        json_file = self.output_dir / "briefing.json"
        with open(json_file, "w") as f:
            json.dump({
                "date": datetime.now(timezone.utc).date().isoformat(),
                "generated_utc": datetime.now(timezone.utc).isoformat(),
                "stats": {
                    "total_items": len(processed),
                    "ai_relevant": len([i for i in processed if i["is_ai_relevant"]]),
                    "themes": len(set(tag for i in processed for tag in i["tags"])),
                    "feeds_fetched": len(self.feeds),
                },
                "items": [
                    {
                        "title": i["title"],
                        "link": i["link"],
                        "source": i["source"],
                        "source_url": i.get("source_url", ""),
                        "published": i.get("published"),
                        "summary": i.get("summary", "")[:500],
                        "ai_score": i["ai_score"],
                        "tags": i["tags"],
                        "is_ai_relevant": i["is_ai_relevant"],
                    }
                    for i in processed
                ],
            }, f, indent=2)

        # Copy feeds.opml and taxonomy.yaml to docs for reference
        import shutil
        shutil.copy2(self.feeds_file, self.output_dir / "feeds.opml")
        shutil.copy2(self.taxonomy_file, self.output_dir / "taxonomy.yaml")

        print(f"Generated all pages in {self.output_dir}")

    def _group_feeds_by_category(self):
        grouped = defaultdict(list)
        for feed in self.feeds:
            grouped[feed.get("category", "General")].append(feed)
        return dict(grouped)

    def run(self, hours_back=24):
        print(f"Fetching {len(self.feeds)} feeds...")
        raw_items = self.fetch_all_feeds(hours_back)
        print(f"Fetched {len(raw_items)} raw items")

        unique_items = self.dedupe_items(raw_items)
        print(f"After dedupe: {len(unique_items)} items")

        self.generate_all_pages(unique_items, hours_back)
        print("All pages generated successfully")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate Higher Ed + AI Daily Briefing Site")
    parser.add_argument("--feeds", default="feeds.opml", help="OPML feeds file")
    parser.add_argument("--taxonomy", default="taxonomy.yaml", help="Taxonomy YAML file")
    parser.add_argument("--template-dir", default="templates", help="Template directory")
    parser.add_argument("--output", default="docs", help="Output directory (for GitHub Pages)")
    parser.add_argument("--hours", type=int, default=24, help="Hours of history to fetch")
    args = parser.parse_args()

    generator = BriefingGenerator(
        feeds_file=args.feeds,
        taxonomy_file=args.taxonomy,
        template_dir=args.template_dir,
        output_dir=args.output,
    )
    generator.run(hours_back=args.hours)


if __name__ == "__main__":
    main()