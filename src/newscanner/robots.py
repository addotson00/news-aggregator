"""robots.txt evaluation per RFC 9309 (with * and $ wildcards).

Python's urllib.robotparser ignores wildcards, which matters once we fetch
article pages (sites often disallow patterns like /*/print/ or /*?amp).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

PRODUCT_TOKEN = "newscanner"


class Robots:
    def __init__(self, text: str, agent: str = PRODUCT_TOKEN):
        groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        in_agent_lines = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (p.strip() for p in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if not in_agent_lines and agents:
                    groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(value.lower())
                in_agent_lines = True
            elif key in ("allow", "disallow"):
                in_agent_lines = False
                if agents:
                    rules.append((key == "allow", value))
            # other keys (sitemap, crawl-delay) neither start nor end a group
        if agents:
            groups.append((agents, rules))

        specific = [r for a, r in groups if agent.lower() in a]
        chosen = specific or [r for a, r in groups if "*" in a]
        self.rules = [(allow, path, _compile(path)) for rs in chosen for allow, path in rs if path]

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: tuple[int, bool] | None = None
        for allow, path, rx in self.rules:
            if rx.match(target):
                # Longest (most specific) rule wins; on a tie, allow wins.
                if best is None or len(path) > best[0] or (len(path) == best[0] and allow):
                    best = (len(path), allow)
        return True if best is None else best[1]


def _compile(path: str) -> re.Pattern:
    anchored = path.endswith("$")
    body = re.escape(path[:-1] if anchored else path).replace(r"\*", ".*")
    return re.compile(body + ("$" if anchored else ""))
