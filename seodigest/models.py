"""Shared data models.

`Item`   — raw content pulled from any source (X, RSS, Google Status).
`Signal` — a curated, scored digest entry with the 5-field schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, List


@dataclass
class Item:
    """One raw piece of content from a source, pre-curation."""
    id: str
    source: str            # "x" | "rss" | "google_status"
    source_name: str       # handle, list label, feed name, or "google_status"
    group: str             # X list key, feed name, or product family
    author: str
    text: str
    url: str
    published: Optional[datetime] = None
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published"] = self.published.isoformat() if self.published else None
        return d


@dataclass
class Signal:
    """A curated digest entry. Mirrors daily.signal_fields in config."""
    what_happened: str
    why_it_matters: str
    evidence: str
    who_it_affects: str             # vertical label(s): ecommerce/publisher/SaaS/...
    what_to_do: str
    confidence: str                 # Confirmed | Data-backed | Observed | Speculative
    impact: str                     # P0 | P1 | P2 | P3
    section: str                    # one of daily.sections
    score: float = 0.0
    sources: List[dict] = field(default_factory=list)  # [{name,url}]

    def to_dict(self) -> dict:
        return asdict(self)
