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
# for a handle wins. Instances come and go — update this list when needed, and
# run `python main.py doctor` to see which are alive right now.
#
# Keep it short (each dead instance costs up to `timeout` seconds per handle)
# but never down to one: a single instance means the fallback cannot fire, and
# public Nitter instances rate-limit aggressively. That is what made @JohnMu
# look permanently dead and other handles fail at random — nitter.net throttling
# partway through a 29-handle run, with nowhere to fall back to.
NITTER_INSTANCES = [
    "nitter.net",
    "nitter.privacyredirect.com",
    "nitter.poast.org",
    "lightbrd.com",
]

# A browser UA. Nitter instances sit behind the same anti-bot layers as
# everything else and a library-shaped UA gets challenged. Shared with the
# doctor command so both probe identically — a health check that used different
# headers from the real fetch would be measuring the wrong thing.
FETCH_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36")

# Populated by fetch() so the caller can record source availability in the
# archive. A module-level dict rather than a return value because fetch()'s
# signature is shared with the other sources and callers only want Items.
LAST_HEALTH: dict = {}

# Per-handle failure detail: {handle: "nitter.net:429, nitter.poast.org:empty"}.
# Which instance refused and how is the difference between "this account is
# gone" and "we are being rate-limited".
LAST_ERRORS: dict = {}


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


def _resolve_instances(xcfg: dict) -> list:
    """Config instances FIRST, then the built-in defaults as fallback.

    Deliberately additive rather than a replacement. Listing one preferred
    instance in config.yaml is a reasonable thing to do, but when it silently
    replaced the default list it also disabled the fallback — and a lone
    rate-limited instance is indistinguishable from every account having gone
    quiet. Config now expresses preference, not exclusivity.
    """
    seen, out = set(), []
    for host in list(xcfg.get("nitter_instances") or []) + NITTER_INSTANCES:
        host = (host or "").strip().rstrip("/")
        if host and host not in seen:
            seen.add(host)
            out.append(host)
    return out


def _fetch_handle(feedparser, handle, group, per, instances, since, meta=None):
    """Fetch one handle's timeline, trying each Nitter instance in turn.

    Returns a list of Items on success (possibly EMPTY — the account may simply
    not have posted), or None when every instance refused. That distinction is
    the whole point: "no tweets" and "cannot reach X at all" produce identical
    digests otherwise, and a totally dead Nitter would look like a quiet day.
    """
    try:
        import httpx
    except ImportError:
        # See rss_source.fetch_feed_text: a missing dep must not masquerade as
        # 29 separate unreachable accounts.
        msg = ("MISSING DEPENDENCY: httpx is not installed — "
               "run: pip install -r requirements.txt")
        LAST_ERRORS[handle] = msg
        print(f"  [x] {msg}")
        return None
    headers = {"User-Agent": FETCH_UA}
    errors = []
    for host in instances:
        url = f"https://{host}/{handle}/rss"
        try:
            # Hard timeout per instance so one slow/dead instance can't stall
            # the whole fetch for minutes.
            r = httpx.get(url, headers=headers, timeout=8, follow_redirects=True)
            if r.status_code >= 400:
                # 429 is worth distinguishing when reading the log: it means the
                # instance is alive and the handle probably fine, we're just
                # being throttled — a reason to add instances, not drop accounts.
                errors.append(f"{host}:{r.status_code}")
                continue
            parsed = feedparser.parse(r.text)
            # Detect real failure: empty feed with no channel title.
            if not parsed.entries and not parsed.feed.get("title"):
                errors.append(f"{host}:empty")
                continue
            return _parse_handle_rss(parsed, handle, group, meta)
        except Exception as e:
            errors.append(f"{host}:{type(e).__name__}")
            continue
    LAST_ERRORS[handle] = ", ".join(errors)
    print(f"  [x] @{handle} ({group}) failed: {', '.join(errors)}")
    return None


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
        LAST_HEALTH.update({"enabled": False, "ok": 0, "failed": 0, "total": 0,
                            "failed_names": []})
        return []
    import feedparser
    from datetime import timedelta

    xcfg = cfg["x"]
    per = xcfg.get("tweets_per_handle", 15)
    instances = _resolve_instances(xcfg)
    weights = (cfg.get("scoring") or {}).get("source_weights", {})
    accounts = _resolve_accounts(xcfg)
    items: List[Item] = []
    ok_handles, failed_handles = [], []

    # Longer window for rarely-posting, high-trust accounts (Google staff,
    # controlled-experiment people). @JohnMu explaining an indexing boundary is
    # still the signal of the week three days later; a 24h cutoff throws it away.
    daily = cfg.get("daily", {})
    evergreen_types = set(daily.get("evergreen_categories") or [])
    ever_hours = daily.get("evergreen_lookback_hours")
    evergreen_since = (since - timedelta(hours=ever_hours - daily.get("lookback_hours", 24))
                       if ever_hours and evergreen_types else since)

    for handle, meta in accounts.items():
        meta = dict(meta)
        meta.setdefault("weight", weights.get(meta.get("source_type"), 0.6))
        # `group` keeps the primary tag so downstream grouping still works.
        group = (meta.get("tags") or ["general"])[0]
        cutoff = evergreen_since if meta.get("source_type") in evergreen_types else since
        tweets = _fetch_handle(feedparser, handle, group, per, instances, since, meta)
        # Reachability, not productivity: an account that simply didn't post
        # today returned a valid feed. `_fetch_handle` returns None only when
        # every Nitter instance refused.
        (failed_handles if tweets is None else ok_handles).append(handle)
        for t in (tweets or [])[:per]:
            # Plain retweets carry no added analysis — config x.filters.drop.
            if t.text.startswith("RT @"):
                continue
            if _within(t.published, cutoff):
                items.append(t)

    LAST_HEALTH.update({
        "enabled": True,
        "ok": len(ok_handles),
        "failed": len(failed_handles),
        "total": len(ok_handles) + len(failed_handles),
        "failed_names": failed_handles,
        # Which instance refused, and how. A run where every handle reports
        # ":429" is a rate-limit problem, not 29 dead accounts.
        "errors": {k: v for k, v in LAST_ERRORS.items() if k in failed_handles},
    })

    # Keyword search is not supported via Nitter RSS (no search endpoint that's
    # reliable across instances). Keyword hits are dropped silently — the
    # curated accounts are the primary signal source anyway.
    return items
