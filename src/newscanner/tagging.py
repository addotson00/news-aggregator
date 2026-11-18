"""Keyword matching -> tags."""

from __future__ import annotations

import re

from .config import Topic


def compile_term(term: str) -> re.Pattern:
    term = term.strip()
    if len(term) > 2 and term.startswith("/") and term.endswith("/"):
        return re.compile(term[1:-1])
    if term.endswith("*"):
        body = re.escape(term[:-1]) + r"[\w-]*"
    else:
        body = re.escape(term)
    return re.compile(rf"(?<![\w-]){body}(?![\w-])", re.IGNORECASE)


class Tagger:
    def __init__(self, topic: Topic):
        self.groups = {g: [(t, compile_term(t)) for t in terms] for g, terms in topic.keywords.items()}
        self.match_groups = set(topic.match_groups)
        self.exclude = [compile_term(t) for t in topic.exclude]

    def apply(self, article: dict) -> dict:
        text = " \n ".join(
            [article.get("title", ""), article.get("summary", ""), " ; ".join(article.get("categories", []))]
        )
        tags, terms = [], []
        for group, patterns in self.groups.items():
            hits = [m.group(0) for _, p in patterns if (m := p.search(text))]
            if hits:
                tags.append(group)
                terms.extend(hits)
        excluded = any(p.search(text) for p in self.exclude)
        article["tags"] = tags
        article["matched_terms"] = sorted(set(terms), key=str.lower)
        article["match"] = (not excluded) and bool(self.match_groups.intersection(tags))
        return article
