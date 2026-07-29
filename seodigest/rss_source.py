"""Fetch recent articles from RSS/Atom feeds. Reliable, no auth needed."""
from __future__ import annotations

from datetime import datetime, timezone
from time import mktime
from typing import List

from .models import Item


def _entry_datetime(entry):
    for key in ("published_parsed", "updated_parsed"):
        val = getattr(entry, key, None) or entry.get(key)
        if val:
            return datetime.fromtimestamp(mktime(val), tz=timezone.utc)
    return None


def _within_window(dt, since):
    if dt is None:
        return True
    return dt >= since


def fetch(cfg: dict, since: datetime) -> List[Item]:
    if not cfg.get("rss", {}).get("enabled"):
        return []
    import feedparser

    items: List[Item] = []
    max_per = cfg["rss"].get("max_items_per_feed", 10)
    for feed in cfg["rss"].get("feeds", []):
        name, url = feed["name"], feed["url"]
        try:
            parsed = feedparser.parse(url)
            for entry in parsed.entries[:max_per]:
                dt = _entry_datetime(entry)
                if not _within_window(dt, since):
                    continue
                summary = getattr(entry, "summary", "") or ""
                # strip crude HTML so the LLM gets clean text
                import re
                summary = re.sub(r"<[^>]+>", " ", summary)
                summary = re.sub(r"\s+", " ", summary).strip()
                title = getattr(entry, "title", "")
                link = getattr(entry, "link", "")
                guid = getattr(entry, "id", None) or link
                items.append(
                    Item(
                        id=str(guid),
                        source="rss",
                        source_name=name,
                        group="rss",
                        author=name,
                        text=f"{title}. {summary[:500]}",
                        url=link,
                        published=dt,
                    )
                )
        except Exception as e:
            print(f"  [rss] feed '{name}' failed: {e}")
    return items
