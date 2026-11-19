"""Source fetchers.

Each fetcher returns raw entry dicts: {url, title, summary, author, categories,
published(datetime|None)}. Add a source type by writing a function and
registering it in FETCHERS.
"""

from __future__ import annotations

import calendar
import json
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx

from . import USER_AGENT
from .config import Source
from .normalize import clean_text, parse_iso
from .robots import Robots


class FetchError(Exception):
    pass


class TransientFetchError(FetchError):
    """Worth retrying on a later run (rate limits, network trouble)."""


@dataclass
class FetchResult:
    entries: list[dict] = field(default_factory=list)
    not_modified: bool = False  # server answered 304 for page 1
    pages_read: int = 0
    validators: dict = field(default_factory=dict)  # etag / last_modified for next time


_robots_cache: dict[str, Robots | None] = {}


def robots_allows(client: httpx.Client, url: str) -> bool:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _robots_cache:
        rules: Robots | None = None
        try:
            r = client.get(f"{origin}/robots.txt")
            if r.status_code == 200:
                rules = Robots(r.text)
        except httpx.HTTPError:
            rules = None  # unreachable robots.txt -> no stated restrictions
        _robots_cache[origin] = rules
    rules = _robots_cache[origin]
    return True if rules is None else rules.allowed(url)


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
    if r.status_code in (429, 503):
        raise TransientFetchError(f"HTTP {r.status_code}")
    if r.status_code not in (200, 304):
        raise FetchError(f"HTTP {r.status_code}")
    return r


def is_rate_limit(error: str) -> bool:
    """True for errors meaning 'slow down' (HTTP 429/503), as opposed to broken."""
    return "HTTP 429" in error or "HTTP 503" in error


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
            "content": (e.get("content") or [{}])[0].get("value", ""),  # full text, when the feed has it
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


# ---------------------------------------------------------------------------
# Article pages (used only to check an article's text for keywords; the text
# itself is never stored, only short snippets around matches).

PAGE_MAX_BYTES = 3_000_000
PAGE_HOST_DELAY = 3.0  # seconds between page requests to the same host
_last_page_hit: dict[str, float] = {}
_blocked_hosts: dict[str, tuple[str, bool]] = {}  # host -> (reason, transient), for the rest of this run


class _ParagraphText(HTMLParser):
    """Collects <p> text, noting which paragraphs sit inside <article>."""

    SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "figure"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.article = 0
        self.in_p = 0
        self.buf: list[str] = []
        self.paras: list[tuple[bool, str]] = []
        self.ld_json: list[str] = []
        self._ld = False

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self._ld = True
            return
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "article":
            self.article += 1
        elif tag == "p" and not self.skip:
            self.in_p += 1
            self.buf = []

    def handle_endtag(self, tag):
        if tag == "script" and self._ld:
            self._ld = False
            return
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "article" and self.article:
            self.article -= 1
        elif tag == "p" and self.in_p:
            self.in_p -= 1
            text = " ".join("".join(self.buf).split())
            if text:
                self.paras.append((self.article > 0, text))

    def handle_data(self, data):
        if self._ld:
            self.ld_json.append(data)
        elif self.in_p and not self.skip:
            self.buf.append(data)


def _ld_article_body(chunks: list[str]) -> str:
    def walk(node):
        if isinstance(node, dict):
            body = node.get("articleBody")
            if isinstance(body, str) and len(body) > 200:
                return body
            for v in node.values():
                if (found := walk(v)):
                    return found
        elif isinstance(node, list):
            for v in node:
                if (found := walk(v)):
                    return found
        return ""

    for chunk in chunks:
        try:
            if (body := walk(json.loads(chunk))):
                return body
        except ValueError:
            continue
    return ""


def extract_article_text(html_text: str) -> str:
    """Best-effort article body: JSON-LD articleBody, else <p> text inside <article>, else all <p>."""
    parser = _ParagraphText()
    try:
        parser.feed(html_text)
    except Exception:  # noqa: BLE001 - malformed HTML; use whatever was collected
        pass
    if (body := _ld_article_body(parser.ld_json)):
        return clean_text(body)
    in_article = [t for inside, t in parser.paras if inside]
    paras = in_article if sum(map(len, in_article)) > 400 else [t for _, t in parser.paras]
    return clean_text("\n".join(paras))


def host_refusal(url: str) -> tuple[str, bool] | None:
    """(reason, transient) if this run already backed off from the URL's host."""
    return _blocked_hosts.get(urlsplit(url).netloc.lower())


def fetch_page_text(client: httpx.Client, url: str, delay: float = PAGE_HOST_DELAY) -> str:
    """Fetch one article page politely (at least `delay` s after the previous page
    request to the same host) and return its text. Raises FetchError."""
    host = urlsplit(url).netloc.lower()
    if host in _blocked_hosts:
        reason, transient = _blocked_hosts[host]
        raise (TransientFetchError if transient else FetchError)(f"host refused pages this run ({reason})")
    if not robots_allows(client, url):
        raise FetchError("article page disallowed by robots.txt")
    wait = delay - (time.monotonic() - _last_page_hit.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_page_hit[host] = time.monotonic()
    try:
        r = client.get(url, headers={"Accept": "text/html,application/xhtml+xml"})
    except httpx.HTTPError as e:
        raise TransientFetchError(f"network error: {e.__class__.__name__}") from e
    if r.status_code in (401, 403, 429, 503):
        # Rate limits and bot walls are often temporary: back off from this host for
        # the rest of the run and retry later. Retries stop once an article is older
        # than text_check_days, so a host that always refuses can't pile up work.
        _blocked_hosts[host] = (f"HTTP {r.status_code}", True)
        raise TransientFetchError(f"HTTP {r.status_code}")
    if r.status_code != 200:
        raise FetchError(f"HTTP {r.status_code}")
    if "html" not in r.headers.get("content-type", "html"):
        raise FetchError("not an HTML page")
    return extract_article_text(r.content[:PAGE_MAX_BYTES].decode(r.encoding or "utf-8", errors="replace"))


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
