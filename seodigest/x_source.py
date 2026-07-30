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
# Keep this short: each dead instance adds up to 8s per handle of stall time.
NITTER_INSTANCES = [
    "nitter.net",
    "nitter.privacyredirect.com",
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


def _parse_handle_rss(parsed, handle, group, meta=None) -> List[Item]:
    """Turn a parsed Nitter RSS feed into Items."""
    meta = meta or {}
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
            source_type=meta.get("source_type", "practitioner_observation"),
            tags=list(meta.get("tags", [])),
            commercial_interest=bool(meta.get("commercial_interest", False)),
            weight=float(meta.get("weight", 0.0)),
        ))
    return items


def _fetch_handle(feedparser, handle, group, per, instances, since, meta=None) -> List[Item]:
    """Fetch one handle's timeline, trying each Nitter instance in turn."""
    import httpx
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SEO-Signal-Radar/1.0)"}
    for host in instances:
        url = f"https://{host}/{handle}/rss"
        try:
            # Hard 8s timeout per instance so one slow/dead instance can't
            # stall the whole fetch for minutes.
            r = httpx.get(url, headers=headers, timeout=8, follow_redirects=True)
            if r.status_code >= 400:
                continue
            parsed = feedparser.parse(r.text)
            # Detect real failure: empty feed with no channel title.
            if not parsed.entries and not parsed.feed.get("title"):
                continue
            return _parse_handle_rss(parsed, handle, group, meta)
        except Exception:
            continue
    print(f"  [x] @{handle} ({group}) failed: no Nitter instance returned a feed")
    return []


def _resolve_accounts(xcfg: dict) -> dict:
    """Return {handle: meta}. Each handle appears EXACTLY ONCE.

    Supports the current `accounts:` map (one account, many tags) and falls
    back to the legacy `lists:` structure, where a handle could appear in
    several groups and therefore got fetched and stored more than once. When
    falling back, duplicates are merged: tags union, highest trust wins.
    """
    accounts = xcfg.get("accounts")
    if accounts:
        return {h: dict(meta or {}) for h, meta in accounts.items()}

    merged: dict = {}
    for list_key, spec in (xcfg.get("lists") or {}).items():
        for handle in spec.get("handles", []):
            entry = merged.setdefault(handle, {"tags": [], "source_type": "expert_analysis"})
            if list_key not in entry["tags"]:
                entry["tags"].append(list_key)
    return merged


def fetch(cfg: dict, since: datetime) -> List[Item]:
    if not cfg.get("x", {}).get("enabled"):
        return []
    import feedparser

    xcfg = cfg["x"]
    per = xcfg.get("tweets_per_handle", 15)
    instances = xcfg.get("nitter_instances") or NITTER_INSTANCES
    weights = (cfg.get("scoring") or {}).get("source_weights", {})
    accounts = _resolve_accounts(xcfg)
    items: List[Item] = []

    for handle, meta in accounts.items():
        meta = dict(meta)
        meta.setdefault("weight", weights.get(meta.get("source_type"), 0.6))
        # `group` keeps the primary tag so downstream grouping still works.
        group = (meta.get("tags") or ["general"])[0]
        tweets = _fetch_handle(feedparser, handle, group, per, instances, since, meta)
        for t in tweets[:per]:
            # Plain retweets carry no added analysis — config x.filters.drop.
            if t.text.startswith("RT @"):
                continue
            if _within(t.published, since):
                items.append(t)

    # Keyword search is not supported via Nitter RSS (no search endpoint that's
    # reliable across instances). Keyword hits are dropped silently — the
    # curated accounts are the primary signal source anyway.
    return items
