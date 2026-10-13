"""Manual captures — things a human read and pasted in.

Why this exists: LinkedIn carries a lot of the SEO conversation now, but it has
no public feed, its API does not expose other people's posts, and scraping it
violates the terms of service (and sits behind a login wall, so it would break
the way X did). The honest way LinkedIn content enters the pool is therefore a
person: you saw the post, you copy the URL and the text you actually read, and
it becomes a candidate like any other — citable, excerpted verbatim, carrying
your stated source_type on the trust ladder.

Storage is one JSON object per line in data/inbox/*.jsonl, appended by
`python main.py capture ...`. Nothing is ever deleted from the inbox; dedup is
by URL via data/seen.json like every other source, so a capture is offered to
the pipeline exactly once.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import List

from .models import Item

INBOX_DIR = os.path.join("data", "inbox")
CAPTURES_FILE = os.path.join(INBOX_DIR, "captures.jsonl")

# What a capture is unless the person says otherwise: a credible first-hand
# report (0.60 on the ladder). It is NOT official and it is NOT data research
# just because it was interesting — the curation step decides that.
DEFAULT_SOURCE_TYPE = "practitioner_observation"

LAST_HEALTH: dict = {}


def _norm_url(u: str) -> str:
    from urllib.parse import urlsplit, urlunsplit
    p = urlsplit((u or "").strip())
    # Strip tracking params; LinkedIn appends several per share.
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", ""))


def capture_id(url: str) -> str:
    return "manual:" + hashlib.sha1(_norm_url(url).encode("utf-8")).hexdigest()[:16]


def append_capture(rec: dict, path: str = CAPTURES_FILE) -> dict:
    """Validate + append one capture. Returns the stored record."""
    url = (rec.get("url") or "").strip()
    text = " ".join((rec.get("text") or "").split())
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    if len(text) < 40:
        raise ValueError("text is too short — paste the passage you actually read (>= 40 chars)")
    stored = {
        "url": url,
        "text": text,
        "author": (rec.get("author") or "").strip(),
        "published": (rec.get("published") or "").strip(),
        "source_type": (rec.get("source_type") or DEFAULT_SOURCE_TYPE).strip(),
        "tags": [t for t in (rec.get("tags") or []) if t],
        "commercial_interest": bool(rec.get("commercial_interest", False)),
        "note": (rec.get("note") or "").strip(),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(stored, ensure_ascii=False) + "\n")
    return stored


def _parse_dt(s: str):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_captures(inbox_dir: str = INBOX_DIR) -> List[dict]:
    out = []
    for path in sorted(glob.glob(os.path.join(inbox_dir, "*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"  [manual] {os.path.basename(path)}:{n} unreadable, skipped ({e})")
    return out


def fetch(cfg: dict, since: datetime) -> List[Item]:
    mcfg = cfg.get("manual") or {}
    if not mcfg.get("enabled", True):
        LAST_HEALTH.update({"enabled": False, "items": 0, "total": 0})
        return []
    inbox_dir = mcfg.get("inbox_dir", INBOX_DIR)
    weights = (cfg.get("scoring") or {}).get("source_weights", {})
    items: List[Item] = []
    caps = load_captures(inbox_dir)
    for c in caps:
        # Window on WHEN IT WAS CAPTURED, not when the post was published: a
        # three-day-old LinkedIn post pasted in today is today's candidate.
        captured = _parse_dt(c.get("captured_at", ""))
        if captured and captured < since:
            continue
        stype = c.get("source_type") or DEFAULT_SOURCE_TYPE
        author = c.get("author") or "manual capture"
        items.append(Item(
            id=capture_id(c["url"]),
            source="manual",
            source_name=author,
            group="manual",
            author=author,
            text=c.get("text", ""),
            url=c["url"],
            published=_parse_dt(c.get("published", "")) or captured,
            source_type=stype,
            tags=["manual_capture"] + list(c.get("tags") or []),
            commercial_interest=bool(c.get("commercial_interest", False)),
            weight=float(weights.get(stype, 0.6)),
        ))
    LAST_HEALTH.update({"enabled": True, "items": len(items), "total": len(caps)})
    return items
