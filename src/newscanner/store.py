"""Persist articles as static JSON under <out>/data/<topic>/.

Layout:
  articles.json           current window (last `retention_days` days)
  archive/YYYY-MM.json    older articles, bucketed by publish month
  archive/index.json      [{month, count}] so the site knows what exists
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .normalize import now_iso, parse_iso

DUP_TITLE_MIN = 25  # shorter titles ("Quick Takes") are too generic to dedupe on
DUP_WINDOW = timedelta(days=4)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def read_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def write_json(path: Path, data, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    if pretty:
        text = json.dumps(data, ensure_ascii=False, indent=1)
    else:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    tmp.write_text(text + "\n")
    tmp.replace(path)


def title_key(title: str) -> str:
    return _NON_ALNUM.sub(" ", title.lower()).strip()


def _when(a: dict) -> datetime | None:
    return parse_iso(a.get("published")) or parse_iso(a.get("first_seen"))


def _sort_key(a: dict) -> str:
    return a.get("published") or a.get("first_seen") or ""


class TopicStore:
    def __init__(self, out_dir: Path, slug: str, retention_days: int):
        self.slug = slug
        self.dir = out_dir / "data" / slug
        self.path = self.dir / "articles.json"
        self.cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        doc = read_json(self.path, {"articles": []})
        self.articles: dict[str, dict] = {a["id"]: a for a in doc["articles"]}
        self._titles: dict[str, str] = {}
        for a in self.articles.values():
            self._index_title(a)
        self._archive: dict[str, dict[str, dict]] = {}  # month -> id -> article (lazy)
        self._dirty_months: set[str] = set()

    # ---- duplicate detection -------------------------------------------
    def _index_title(self, a: dict) -> None:
        key = title_key(a["title"])
        if len(key) >= DUP_TITLE_MIN:
            self._titles.setdefault(key, a["id"])

    def _find_duplicate(self, a: dict) -> dict | None:
        """Same headline already stored under a different URL (syndication, URL change)."""
        key = title_key(a["title"])
        if len(key) < DUP_TITLE_MIN:
            return None
        other = self.articles.get(self._titles.get(key, ""))
        if not other or other["id"] == a["id"]:
            return None
        t1, t2 = _when(a), _when(other)
        if t1 and t2 and abs(t1 - t2) > DUP_WINDOW:
            return None
        return other

    # ---- archive ---------------------------------------------------------
    def _month(self, month: str) -> dict[str, dict]:
        if month not in self._archive:
            doc = read_json(self.dir / "archive" / f"{month}.json", {"articles": []})
            self._archive[month] = {a["id"]: a for a in doc["articles"]}
        return self._archive[month]

    def _to_archive(self, a: dict) -> bool:
        """Put an article in its month file. Returns True if it wasn't there yet."""
        month = (_when(a) or datetime.now(timezone.utc)).strftime("%Y-%m")
        bucket = self._month(month)
        old = bucket.get(a["id"])
        merged = {**old, **a, "first_seen": old["first_seen"]} if old else a
        if merged != old:  # only rewrite files whose content actually changed
            bucket[a["id"]] = merged
            self._dirty_months.add(month)
        return old is None

    # ---- ingest ----------------------------------------------------------
    def ingest(self, a: dict) -> str:
        """Store one incoming article. Returns one of:
        new, updated, duplicate, archived_new, archived_known.
        """
        existing = self.articles.get(a["id"])
        if existing:
            a["first_seen"] = existing["first_seen"]
            a["published"] = a["published"] or existing.get("published")
            if existing.get("also_at"):
                a["also_at"] = existing["also_at"]
            existing.update(a)
            return "updated"

        dup = self._find_duplicate(a)
        if dup:
            also = dup.setdefault("also_at", [])
            if dup["url"] != a["url"] and all(x["url"] != a["url"] for x in also):
                also.append({"source_id": a["source_id"], "source_name": a["source_name"], "url": a["url"]})
            return "duplicate"

        a["first_seen"] = now_iso()
        when = parse_iso(a.get("published"))
        if when and when < self.cutoff:
            # Old item still sitting in a deep feed: file it straight into the archive.
            return "archived_new" if self._to_archive(a) else "archived_known"

        self.articles[a["id"]] = a
        self._index_title(a)
        return "new"

    def all(self) -> list[dict]:
        return list(self.articles.values())

    def drop(self, ids: set[str]) -> None:
        for i in ids:
            self.articles.pop(i, None)

    def expire(self) -> int:
        """Move articles older than the retention window into the archive."""
        moved = 0
        for a in list(self.articles.values()):
            when = _when(a)
            if when and when < self.cutoff:
                self._to_archive(a)
                del self.articles[a["id"]]
                moved += 1
        return moved

    def retag_archive(self, apply) -> None:
        """Re-apply tagging to dirty archive months (keeps tags consistent with keywords)."""
        for month in self._dirty_months:
            for a in self._archive[month].values():
                apply(a)

    # ---- output ----------------------------------------------------------
    def save(self) -> list[dict]:
        arts = sorted(self.articles.values(), key=_sort_key, reverse=True)
        write_json(self.path, {"topic": self.slug, "generated_at": now_iso(), "articles": arts})
        for month in sorted(self._dirty_months):
            items = sorted(self._archive[month].values(), key=_sort_key, reverse=True)
            write_json(self.dir / "archive" / f"{month}.json", {"month": month, "articles": items})
        return self.archive_index()

    def archive_index(self) -> list[dict]:
        index = []
        for p in sorted((self.dir / "archive").glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].json"), reverse=True):
            month = p.stem
            if month in self._archive:
                count = len(self._archive[month])
            else:
                count = len(read_json(p, {"articles": []})["articles"])
            index.append({"month": month, "count": count})
        write_json(self.dir / "archive" / "index.json", {"months": index}, pretty=True)
        return index


def update_manifest(out_dir: Path, entry: dict) -> None:
    path = out_dir / "data" / "manifest.json"
    doc = read_json(path, {"topics": []})
    topics = [t for t in doc["topics"] if t["slug"] != entry["slug"]] + [entry]
    topics.sort(key=lambda t: t["title"].lower())
    write_json(path, {"generated_at": now_iso(), "topics": topics}, pretty=True)


def prune_manifest(out_dir: Path, keep: set[str]) -> list[str]:
    """Drop manifest entries for topics that no longer have a config file."""
    path = out_dir / "data" / "manifest.json"
    doc = read_json(path, {"topics": []})
    removed = [t["slug"] for t in doc["topics"] if t["slug"] not in keep]
    if removed:
        doc["topics"] = [t for t in doc["topics"] if t["slug"] in keep]
        write_json(path, doc, pretty=True)
    return removed
