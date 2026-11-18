"""Turn raw feed entries into a single article schema."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = re.compile(r"^(utm_|mc_|fbclid|gclid|ref$|cmpid|smid|partner)", re.I)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
SUMMARY_MAX = 500


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", parts.netloc.lower(), path, urlencode(query), ""))


def article_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()[:16]


WP_FOOTER_RE = re.compile(r"\s*(The post .{0,300}? (?:appeared first|first appeared) on .{0,100}?\.?|\[(…|&#8230;|\.\.\.)\])\s*$")


def clean_text(text: str | None, limit: int | None = None) -> str:
    if not text:
        return ""
    text = html.unescape(TAG_RE.sub(" ", text))
    text = WS_RE.sub(" ", text).strip()
    text = WP_FOOTER_RE.sub("", text)  # WordPress "The post X appeared first on Y." boilerplate
    if limit and len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def now_iso() -> str:
    return iso(datetime.now(timezone.utc))  # type: ignore[return-value]


def build_article(raw: dict, source_id: str, source_name: str) -> dict | None:
    url = (raw.get("url") or "").strip()
    title = clean_text(raw.get("title"))
    if not url or not title:
        return None
    return {
        "id": article_id(url),
        "url": url,
        "title": title,
        "summary": clean_text(raw.get("summary"), SUMMARY_MAX),
        "author": clean_text(raw.get("author")),
        "categories": sorted({clean_text(c) for c in raw.get("categories", []) if c and clean_text(c)}),
        "published": iso(raw.get("published")),
        "source_id": source_id,
        "source_name": source_name,
        "_fulltext": clean_text(raw.get("content")),  # transient; never stored
    }
