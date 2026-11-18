"""Keyword matching -> tags, match flag, and mention snippets.

Two texts are searched:
  headline  title + summary + the outlet's category labels (always available)
  body      article text: the feed's full text or a fetched page when given,
            otherwise the stored `mentions` snippets from an earlier check

Groups with scope "headline" (e.g. Sports) only look at the headline, so a
passing word deep in an article doesn't tag it.
"""

from __future__ import annotations

import re

from .config import Topic

SNIPPET_RADIUS = 110
MAX_SNIPPETS = 3


def compile_term(term: str) -> re.Pattern:
    term = term.strip()
    if len(term) > 2 and term.startswith("/") and term.endswith("/"):
        return re.compile(term[1:-1])
    if term.endswith("*"):
        body = re.escape(term[:-1]) + r"[\w-]*"
    else:
        body = re.escape(term)
    return re.compile(rf"(?<![\w-]){body}(?![\w-])", re.IGNORECASE)


def snippets(text: str, spans: list[tuple[int, int]]) -> list[str]:
    """Short context windows around matches, merged when they overlap."""
    windows: list[list[int]] = []
    for s, e in sorted(spans):
        lo, hi = max(0, s - SNIPPET_RADIUS), min(len(text), e + SNIPPET_RADIUS)
        if windows and lo <= windows[-1][1]:
            windows[-1][1] = max(windows[-1][1], hi)
        else:
            windows.append([lo, hi])
    out = []
    for lo, hi in windows[:MAX_SNIPPETS]:
        # widen to word boundaries so snippets don't start mid-word
        while lo > 0 and not text[lo - 1].isspace():
            lo -= 1
        while hi < len(text) and not text[hi].isspace():
            hi += 1
        out.append(("…" if lo > 0 else "") + text[lo:hi].strip() + ("…" if hi < len(text) else ""))
    return out


class Tagger:
    def __init__(self, topic: Topic):
        self.groups = {
            name: (g.scope, [compile_term(t) for t in g.terms]) for name, g in topic.keywords.items()
        }
        self.match_groups = set(topic.match_groups)
        self.exclude = [compile_term(t) for t in topic.exclude]

    def apply(self, article: dict, fulltext: str | None = None) -> dict:
        """Tag an article. Pass `fulltext` when the article text was just read;
        otherwise previously stored mention snippets stand in for it."""
        head = " \n ".join(
            [article.get("title", ""), article.get("summary", ""), " ; ".join(article.get("categories", []))]
        )
        body = fulltext if fulltext is not None else " \n ".join(article.get("mentions", []))

        tags, terms, spans = [], [], []
        head_hit = body_hit = False
        for group, (scope, patterns) in self.groups.items():
            hit = False
            for p in patterns:
                if m := p.search(head):
                    hit = True
                    terms.append(m.group(0))
                    head_hit |= group in self.match_groups
                if scope == "all" and body:
                    found = list(p.finditer(body))
                    if found:
                        hit = True
                        terms.append(found[0].group(0))
                        if group in self.match_groups:
                            body_hit = True
                            spans.extend(m.span() for m in found)
            if hit:
                tags.append(group)

        excluded = any(p.search(head) or (body and p.search(body)) for p in self.exclude)
        article["tags"] = tags
        article["matched_terms"] = sorted(set(terms), key=str.lower)
        article["match"] = not excluded and (head_hit or body_hit)
        article["found_in"] = "headline" if head_hit else ("text" if body_hit else None)
        if fulltext is not None:
            article["mentions"] = snippets(fulltext, spans) if spans else []
        return article
