"""Source fetchers.

Each fetcher returns raw entry dicts: {url, title, summary, author, categories,
published(datetime|None)}. Add a source type by writing a function and
registering it in FETCHERS.
"""

from __future__ import annotations

import calendar
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import feedparser
import httpx

from . import USER_AGENT
from .config import Source
from .normalize import parse_iso


class FetchError(Exception):
    pass


@dataclass
class FetchResult:
    entries: list[dict] = field(default_factory=list)
    not_modified: bool = False  # server answered 304 for page 1
    pages_read: int = 0
    validators: dict = field(default_factory=dict)  # etag / last_modified for next time


_robots_cache: dict[str, RobotFileParser | None] = {}


def robots_allows(client: httpx.Client, url: str) -> bool:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _robots_cache:
        rp: RobotFileParser | None = None
        try:
            r = client.get(f"{origin}/robots.txt")
            if r.status_code == 200:
                rp = RobotFileParser()
                rp.parse(r.text.splitlines())
        except httpx.HTTPError:
            rp = None  # unreachable robots.txt -> no stated restrictions
        _robots_cache[origin] = rp
    rp = _robots_cache[origin]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


def _get(client: httpx.Client, url: str, validators: dict | None = None) -> httpx.Response:
    if not robots_allows(client, url):
        raise FetchError("disallowed by robots.txt")
    headers = {}
    if validators:
        if validators.get("etag"):
            headers["If-None-Match"] = validators["etag"]
        if validators.get("last_modified"):
            headers["If-Modified-Since"] = validators["last_modified"]
    try:
        r = client.get(url, headers=headers)
    except httpx.HTTPError as e:
        raise FetchError(f"network error: {e.__class__.__name__}: {e}") from e
    if r.status_code not in (200, 304):
        raise FetchError(f"HTTP {r.status_code}")
    return r


def _validators(r: httpx.Response) -> dict:
    v = {}
    if r.headers.get("etag"):
        v["etag"] = r.headers["etag"]
    if r.headers.get("last-modified"):
        v["last_modified"] = r.headers["last-modified"]
    return v


def _page_url(url: str, param: str, n: int) -> str:
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query) if k != param] + [(param, str(n))]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def _struct_to_dt(st) -> datetime | None:
    if not st:
        return None
    return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)


def _parse_feed(content: bytes) -> list[dict]:
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise FetchError(f"unparseable feed: {feed.get('bozo_exception')}")
    return [
        {
            "url": e.get("link", ""),
            "title": e.get("title", ""),
            "summary": e.get("summary") or e.get("description") or "",
            "author": e.get("author", ""),
            "categories": [t.get("term", "") for t in e.get("tags", [])],
            "published": _struct_to_dt(e.get("published_parsed") or e.get("updated_parsed")),
        }
        for e in feed.entries
    ]


def fetch_rss(client: httpx.Client, src: Source, validators: dict) -> FetchResult:
    """RSS or Atom. With `pages > 1`, also reads ?<page_param>=2..N (WordPress style)."""
    first = _get(client, src.url, validators)
    if first.status_code == 304:
        return FetchResult(not_modified=True, validators=validators)
    result = FetchResult(entries=_parse_feed(first.content), pages_read=1, validators=_validators(first))
    seen = {e["url"] for e in result.entries}
    for n in range(2, src.pages + 1):
        try:
            r = _get(client, _page_url(src.url, src.page_param, n))
            page = _parse_feed(r.content)
        except FetchError:
            break  # ran past the last page, or paging unsupported
        fresh = [e for e in page if e["url"] not in seen]
        if not fresh:
            break  # site ignores the page parameter
        seen.update(e["url"] for e in fresh)
        result.entries.extend(fresh)
        result.pages_read = n
    return result


NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}


def fetch_news_sitemap(client: httpx.Client, src: Source, validators: dict) -> FetchResult:
    """Google News sitemaps: title + date per URL (no summary)."""
    r = _get(client, src.url, validators)
    if r.status_code == 304:
        return FetchResult(not_modified=True, validators=validators)
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as e:
        raise FetchError(f"unparseable sitemap: {e}") from e
    out = []
    for node in root.findall("sm:url", NS):
        news = node.find("news:news", NS)
        if news is None:
            continue
        kw = news.findtext("news:keywords", default="", namespaces=NS)
        out.append(
            {
                "url": node.findtext("sm:loc", default="", namespaces=NS),
                "title": news.findtext("news:title", default="", namespaces=NS),
                "summary": "",
                "author": "",
                "categories": [k.strip() for k in kw.split(",") if k.strip()],
                "published": parse_iso(news.findtext("news:publication_date", default="", namespaces=NS)),
            }
        )
    return FetchResult(entries=out, pages_read=1, validators=_validators(r))


def fetch_html(client: httpx.Client, src: Source, validators: dict) -> FetchResult:
    raise FetchError("html scraping not implemented yet")


FETCHERS = {
    "rss": fetch_rss,
    "news_sitemap": fetch_news_sitemap,
    "html": fetch_html,
}


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"},
        timeout=httpx.Timeout(20.0),
        follow_redirects=True,
    )


def fetch_source(client: httpx.Client, src: Source, validators: dict | None = None) -> FetchResult:
    return FETCHERS[src.type](client, src, validators or {})
