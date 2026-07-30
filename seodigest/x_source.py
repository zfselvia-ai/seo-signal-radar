"""Fetch tweets via Nitter RSS (no twikit, no cookies, no login).

Why Nitter RSS instead of twikit:
  twikit 2.3.3 is broken — X removed the ondemand.s webpack chunk that twikit's
  client-transaction signing depends on, so every request fails with
  "Couldn't get KEY_BYTE indices". This is an ongoing cat-and-mouse game.
  Nitter exposes the same tweets as standard RSS, which is stable and needs no
  auth. We try multiple public Nitter instances with fallback so one going down
  doesn't kill the fetch.

Each X list (official / algo-serp / technical-data / geo-ai) is kept as a
separate `group` so downstream weighting can treat an official Google account
differently from a keyword hit.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from time import mktime
from typing import List

from .models import Item

# Public Nitter instances, tried in order. The first that returns valid RSS
# for a handle wins. Instances come and go — update this list when needed.
NITTER_INSTANCES = [
    "nitter.net",
    "nitter.privacyredirect.com",
    "nitter.poast.org",
    "nitter.1d4.us",
]


def _entry_datetime(entry):
    for key in ("published_parsed", "updated_parsed"):
        val = getattr(entry, key, None) or entry.get(key)
        if val:
            try:
                return datetime.fromtimestamp(mktime(val), tz=timezone.utc)
            except Exception:
                continue
    return None


def _within(dt, since):
    if dt is None:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= since


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_handle_rss(parsed, handle, group) -> List[Item]:
    """Turn a parsed Nitter RSS feed into Items."""
    items: List[Item] = []
    for entry in parsed.entries:
        # Nitter's <link> is https://<host>/<handle>/status/<id>#m — the most
        # reliable place to find the tweet id. <guid>/<id> may be bare digits.
        link = getattr(entry, "link", "") or ""
        m = re.search(r"/status/(\d+)", link)
        if not m:
            continue
        tweet_id = m.group(1)
        title = getattr(entry, "title", "") or ""
        desc = _strip_html(getattr(entry, "summary", "") or "")
        text = title if not desc else f"{title}. {desc[:500]}"
        # Rewrite nitter link back to x.com so the dashboard links to the
        # canonical tweet.
        url = f"https://x.com/{handle}/status/{tweet_id}"
        items.append(Item(
            id=tweet_id,
            source="x",
            source_name=handle,
            group=group,
            author=f"@{handle}",
            text=text,
            url=url,
            published=_entry_datetime(entry),
            metrics={"likes": 0, "retweets": 0, "replies": 0},
        ))
    return items


def _fetch_handle(feedparser, handle, group, per, instances, since) -> List[Item]:
    """Fetch one handle's timeline, trying each Nitter instance in turn."""
    for host in instances:
        url = f"https://{host}/{handle}/rss"
        try:
            parsed = feedparser.parse(url)
            # Nitter returns a valid feed even when the user has 0 tweets;
            # detect real failure by an empty channel or an error status.
            status = getattr(parsed, "status", None) or getattr(parsed.get("feed", {}), "status", None)
            if status and int(status) >= 400:
                continue
            if not parsed.entries and not parsed.feed.get("title"):
                continue
            return _parse_handle_rss(parsed, handle, group)
        except Exception:
            continue
    print(f"  [x] @{handle} ({group}) failed: no Nitter instance returned a feed")
    return []


def fetch(cfg: dict, since: datetime) -> List[Item]:
    if not cfg.get("x", {}).get("enabled"):
        return []
    import feedparser

    per = cfg["x"].get("tweets_per_handle", 15)
    instances = cfg["x"].get("nitter_instances") or NITTER_INSTANCES
    items: List[Item] = []

    for list_key, spec in cfg["x"].get("lists", {}).items():
        for handle in spec.get("handles", []):
            tweets = _fetch_handle(feedparser, handle, list_key, per, instances, since)
            for t in tweets[:per]:
                if t.text.startswith("RT @"):
                    continue
                if _within(t.published, since):
                    items.append(t)

    # Keyword search is not supported via Nitter RSS (no search endpoint that's
    # reliable across instances). Keyword hits are dropped silently — the four
    # curated lists are the primary signal source anyway.
    return items
