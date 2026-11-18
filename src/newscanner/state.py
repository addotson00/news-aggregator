"""Per-source fetch state kept between runs (state/<topic>.json, committed).

Per source: HTTP cache validators, last successful fetch, recently seen ids
(for gap detection), detected gaps, and error streaks.
"""

from __future__ import annotations

from pathlib import Path

from .store import read_json, write_json


class TopicState:
    def __init__(self, state_dir: Path, slug: str):
        self.path = state_dir / f"{slug}.json"
        self.data = read_json(self.path, {"sources": {}})

    def source(self, source_id: str) -> dict:
        return self.data["sources"].setdefault(source_id, {})

    def save(self) -> None:
        write_json(self.path, self.data, pretty=True)
