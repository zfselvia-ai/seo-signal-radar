"""Fetch recent articles from RSS/Atom feeds. Reliable, no auth needed."""
from __future__ import annotations

from datetime import datetime, timezone
from time import mktime
from typing import List

from .models import Item

# Populated by fetch() so the caller can record feed availability in the
# archive alongside the X numbers. See x_source.LAST_HEALTH.
LAST_HEALTH: dict = {}


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
        LAST_HEALTH.update({"enabled": False, "ok": 0, "failed": 0, "total": 0,
                            "failed_names": []})
        return []
    import feedparser
    from datetime import timedelta

    items: List[Item] = []
    ok_feeds, failed_feeds = [], []
    max_per = cfg["rss"].get("max_items_per_feed", 10)
    weights = (cfg.get("scoring") or {}).get("source_weights", {})

    # Slow-moving, high-trust sources get a longer window. A controlled
    # experiment or an official docs change is just as actionable three days
    # after publication, but a strict 24h window silently drops it — which is
    # how a digest ends up with nothing but same-day chatter. Deduplication
    # downstream stops the extra days from repeating across runs.
    daily = cfg.get("daily", {})
    evergreen_cats = set(daily.get("evergreen_categories") or [])
    ever_hours = daily.get("evergreen_lookback_hours")
    evergreen_since = (since - timedelta(hours=ever_hours - daily.get("lookback_hours", 24))
                       if ever_hours and evergreen_cats else since)

    for feed in cfg["rss"].get("feeds", []):
        name, url = feed["name"], feed["url"]
        category = feed.get("category", "trade_news")
        weight = float(feed.get("weight", weights.get(category, 0.55)))
        cutoff = evergreen_since if category in evergreen_cats else since
        try:
            parsed = feedparser.parse(url)
            # feedparser doesn't raise on HTTP/DNS failure — it returns an
            # empty feed. Without this check a permanently dead feed URL (a
            # renamed blog, a moved path) stays invisible forever.
            if not parsed.entries and not (parsed.feed or {}).get("title"):
                failed_feeds.append(name)
                print(f"  [rss] feed '{name}' returned nothing — check the URL")
                continue
            ok_feeds.append(name)
            for entry in parsed.entries[:max_per]:
                dt = _entry_datetime(entry)
                if not _within_window(dt, cutoff):
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
                        group=category,
                        author=name,
                        text=f"{title}. {summary[:500]}",
                        url=link,
                        published=dt,
                        source_type=category,
                        tags=[category],
                        commercial_interest=bool(feed.get("commercial_interest", False)),
                        weight=weight,
                    )
                )
        except Exception as e:
            failed_feeds.append(name)
            print(f"  [rss] feed '{name}' failed: {e}")

    LAST_HEALTH.update({
        "enabled": True,
        "ok": len(ok_feeds),
        "failed": len(failed_feeds),
        "total": len(ok_feeds) + len(failed_feeds),
        "failed_names": failed_feeds,
    })
    return items
