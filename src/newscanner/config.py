"""Load topic YAML files into typed objects."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SOURCE_TYPES = {"rss", "news_sitemap", "html"}


@dataclass
class Source:
    id: str
    name: str
    url: str
    type: str = "rss"
    homepage: str = ""
    enabled: bool = True
    note: str = ""
    pages: int = 1  # >1 reads extra feed pages via ?<page_param>=N (WordPress feeds)
    page_param: str = "paged"
    text_check: bool = True  # may fetch article pages when the topic checks article text


@dataclass
class Term:
    text: str
    scope: str = "all"  # "all" = headline + article text; "headline" = title/summary/categories only


@dataclass
class KeywordGroup:
    terms: list[Term]
    scope: str = "all"  # default scope for the group's terms


@dataclass
class Topic:
    slug: str
    title: str
    description: str = ""
    mode: str = "tag"  # "tag" keeps everything; "filter" keeps matches only
    retention_days: int = 30
    sources: list[Source] = field(default_factory=list)
    keywords: dict[str, KeywordGroup] = field(default_factory=dict)
    match_groups: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    default: bool = False  # the site root redirects to this aggregator
    fetch_every_minutes: int = 0  # 0 = every run; otherwise skip runs until due
    text_check: bool = False  # also search article text (feed full text, else fetch the page)
    text_check_days: int = 14  # only fetch pages for articles published this recently
    page_fetch_limit: int = 25  # max article pages per source per run
    hidden_tags: list[str] = field(default_factory=list)  # tags hidden on the site by default
    path: Path | None = None


def load_topic(path: Path) -> Topic:
    raw = yaml.safe_load(path.read_text()) or {}
    sources = []
    for s in raw.get("sources", []):
        src = Source(**s)
        if src.type not in SOURCE_TYPES:
            raise ValueError(f"{path.name}: source {src.id!r} has unknown type {src.type!r}")
        sources.append(src)

    ids = [s.id for s in sources]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path.name}: duplicate source ids")

    keywords: dict[str, KeywordGroup] = {}
    for name, spec in (raw.get("keywords") or {}).items():
        group_scope = spec.get("scope", "all") if isinstance(spec, dict) else "all"
        raw_terms = (spec.get("terms") if isinstance(spec, dict) else spec) or []
        terms = []
        for t in raw_terms:
            # a term is a string, or {term: ..., scope: headline|all} to override the group scope
            term = Term(str(t["term"]), t.get("scope", group_scope)) if isinstance(t, dict) else Term(str(t), group_scope)
            if term.scope not in ("all", "headline"):
                raise ValueError(f"{path.name}: keyword group {name!r}: scope must be 'all' or 'headline'")
            terms.append(term)
        keywords[str(name)] = KeywordGroup(terms=terms, scope=group_scope)
    match_groups = raw.get("match_groups") or list(keywords)
    unknown = set(match_groups) - set(keywords)
    if unknown:
        raise ValueError(f"{path.name}: match_groups not defined in keywords: {sorted(unknown)}")

    unknown_hidden = set(raw.get("hidden_tags") or []) - set(keywords)
    if unknown_hidden:
        raise ValueError(f"{path.name}: hidden_tags not defined in keywords: {sorted(unknown_hidden)}")

    mode = raw.get("mode", "tag")
    if mode not in ("tag", "filter"):
        raise ValueError(f"{path.name}: mode must be 'tag' or 'filter'")

    slug = str(raw["slug"])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", slug) or slug in ("assets", "data"):
        raise ValueError(f"{path.name}: slug {slug!r} must be letters/digits/-/_ and not 'assets' or 'data'")

    return Topic(
        slug=slug,
        title=raw.get("title", raw["slug"]),
        description=raw.get("description", ""),
        mode=mode,
        retention_days=int(raw.get("retention_days", 30)),
        sources=sources,
        keywords=keywords,
        match_groups=list(match_groups),
        exclude=[str(t) for t in (raw.get("exclude") or [])],
        default=bool(raw.get("default", False)),
        fetch_every_minutes=int(raw.get("fetch_every_minutes", 0)),
        text_check=bool(raw.get("text_check", False)),
        text_check_days=int(raw.get("text_check_days", 14)),
        page_fetch_limit=int(raw.get("page_fetch_limit", 25)),
        hidden_tags=[str(t) for t in (raw.get("hidden_tags") or [])],
        path=path,
    )


def load_topics(topics_dir: Path, only: list[str] | None = None) -> list[Topic]:
    topics = [load_topic(p) for p in sorted(topics_dir.glob("*.y*ml"))]
    if only:
        missing = set(only) - {t.slug for t in topics}
        if missing:
            raise SystemExit(f"Unknown topic(s): {', '.join(sorted(missing))}")
        topics = [t for t in topics if t.slug in only]
    return topics
