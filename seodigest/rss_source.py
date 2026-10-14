"""Fetch recent articles from RSS/Atom feeds. Reliable, no auth needed."""
from __future__ import annotations

from calendar import timegm
from datetime import datetime, timezone
from typing import List

from .models import Item

# Populated by fetch() so the caller can record feed availability in the
# archive alongside the X numbers. See x_source.LAST_HEALTH.
LAST_HEALTH: dict = {}

# Per-feed diagnostic detail: {name: "403 Forbidden" | "0 entries" | "timeout"}.
# LAST_HEALTH only counts successes and failures, which is enough for the
# dashboard strip but useless when you need to know WHY a feed is dark.
LAST_ERRORS: dict = {}

# A browser User-Agent, deliberately.
#
# feedparser fetches URLs itself when handed a string, and identifies itself as
# "feedparser/x.y". Cloudflare-fronted publishers (seroundtable,
# searchengineland) routinely 403 that, and feedparser does not raise on a 403
# — it returns an empty feed. So four feeds failed every single run for nine
# days while looking, from the inside, exactly like "no new articles".
#
# We therefore fetch the bytes ourselves and hand feedparser a string. That
# also lets us set a hard timeout (feedparser's default is the socket default,
# i.e. potentially forever) and record the real status code.
FETCH_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
FETCH_TIMEOUT = 15


def fetch_feed_text(url: str, timeout: int = FETCH_TIMEOUT):
    """GET a feed URL as text. Returns (text, status, error).

    Exactly one of text/error is meaningful. Kept separate from fetch() so the
    `doctor` command can probe a feed without running the whole pipeline.
    """
    try:
        import httpx
    except ImportError:
        # Distinguished from a network error on purpose: without this, a missing
        # dependency is reported once per feed as an ordinary fetch failure, and
        # 11 identical "failed" lines look like the network is down rather than
        # like one pip install away. Same wording in x_source.
        return None, None, ("MISSING DEPENDENCY: httpx is not installed — "
                            "run: pip install -r requirements.txt")
    try:
        r = httpx.get(url, headers=FETCH_HEADERS, timeout=timeout,
                      follow_redirects=True)
    except Exception as e:  # DNS, TLS, timeout, connection reset
        return None, None, f"{type(e).__name__}: {e}"
    if r.status_code >= 400:
        return None, r.status_code, f"HTTP {r.status_code}"
    return r.text, r.status_code, None


def _entry_datetime(entry):
    # feedparser's *_parsed tuples are UTC. timegm, NOT time.mktime: mktime
    # reads the tuple as LOCAL time, so on a UTC+8 machine every timestamp came
    # out 8h early — wrong dates on cards, and the first 8h of each run's window
    # silently fell outside it. It only looked right on CI because CI is UTC.
    for key in ("published_parsed", "updated_parsed"):
        val = getattr(entry, key, None) or entry.get(key)
        if val:
            return datetime.fromtimestamp(timegm(val), tz=timezone.utc)
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
        # Bluesky exposes a plain RSS feed per account (bsky.app/profile/<h>/rss),
        # which is how the Google staff who left X are followed now. They ride
        # the RSS fetcher but are POSTS, not articles: the raw ranker must not
        # give them the article bonus, and the entries have no <title>.
        is_bluesky = "bsky.app/" in url
        source_kind = "bluesky" if is_bluesky else "rss"
        author = feed.get("author") or name
        tags = list(feed.get("tags") or [category])
        # A Bluesky mirror of an X account names that handle, so the same
        # person is one person to dedup and the per-person cap.
        person = str(feed.get("same_as") or "").strip().lstrip("@").lower()
        try:
            # Fetch ourselves rather than letting feedparser do it — see
            # FETCH_HEADERS for why the User-Agent is load-bearing.
            # `fallback_url` (e.g. a Google News mirror) is tried only when the
            # primary fails: CI runs from datacenter IPs that some publishers
            # block, but the primary carries the real summary and article URL.
            parsed, err = None, None
            for attempt in [url] + ([feed["fallback_url"]] if feed.get("fallback_url") else []):
                text, status, err = fetch_feed_text(attempt)
                if err:
                    continue
                parsed = feedparser.parse(text)
                # A 200 that parses to nothing is still a failure: a renamed
                # blog often serves an HTML error page with a 200. Without this
                # check a permanently dead feed URL stays invisible forever.
                if parsed.entries or (parsed.feed or {}).get("title"):
                    if attempt != url:
                        print(f"  [rss] feed '{name}': primary failed, using fallback_url")
                    break
                parsed, err = None, f"HTTP {status} but no parseable entries"
            if parsed is None:
                failed_feeds.append(name)
                LAST_ERRORS[name] = err
                print(f"  [rss] feed '{name}' failed: {err}")
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
                title = (getattr(entry, "title", "") or "").strip()
                link = getattr(entry, "link", "")
                guid = getattr(entry, "id", None) or link
                # Bluesky items have no title, only the post body; a leading
                # ". " would otherwise open every excerpt. Bluesky also appends
                # a boilerplate marker for quote posts that is noise in a card.
                if is_bluesky:
                    summary = summary.replace(
                        "[contains quote post or other embedded content]", "").strip()
                text = f"{title}. {summary[:500]}" if title else summary[:600]
                items.append(
                    Item(
                        id=str(guid),
                        source=source_kind,
                        source_name=name,
                        group=category,
                        author=author,
                        text=text,
                        url=link,
                        published=dt,
                        source_type=category,
                        tags=tags,
                        commercial_interest=bool(feed.get("commercial_interest", False)),
                        weight=weight,
                        person=person,
                    )
                )
        except Exception as e:
            failed_feeds.append(name)
            LAST_ERRORS[name] = f"{type(e).__name__}: {e}"
            print(f"  [rss] feed '{name}' failed: {e}")

    LAST_HEALTH.update({
        "enabled": True,
        "ok": len(ok_feeds),
        "failed": len(failed_feeds),
        "total": len(ok_feeds) + len(failed_feeds),
        "failed_names": failed_feeds,
        # Why each one failed, so a repeat offender is diagnosable from the
        # archive months later instead of just being a name in a list.
        "errors": {k: v for k, v in LAST_ERRORS.items() if k in failed_feeds},
    })
    return items
