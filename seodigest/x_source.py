"""Fetch tweets from four curated X Lists + keyword searches via twikit.

twikit logs in with your X account and caches cookies.json so later runs skip
re-auth (lower challenge/ban risk). First run needs X_USERNAME/X_EMAIL/
X_PASSWORD; afterwards only cookies.json is used.

The four lists (official / algo-serp / technical-data / geo-ai) are kept as
separate `group`s so downstream weighting and sectioning can treat an official
Google account differently from a keyword-search hit.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import List

# --- twikit 2.3.3 monkey patch ------------------------------------------------
# twikit 2.3.3 breaks because X changed its frontend bundle format; the
# ON_DEMAND_FILE_REGEX no longer matches, causing "Couldn't get KEY_BYTE
# indices" / "'ClientTransaction' object has no attribute 'key'" on every call.
# Patch from https://github.com/d60/twikit/issues/408 (audioeng89's snippet).
# Remove this block once twikit ships an official fix (> 2.3.3).
try:
    import re as _re
    _tx = __import__("twikit.x_client_transaction.transaction",
                     fromlist=["ClientTransaction"])
    _tx.ON_DEMAND_FILE_REGEX = _re.compile(
        r""",(\d+):["']ondemand\.s["']""",
        flags=(_re.VERBOSE | _re.MULTILINE))
    _tx.ON_DEMAND_HASH_PATTERN = r',{}:"([0-9a-f]+)"'

    async def _patched_get_indices(self, home_page_response, session, headers):
        key_byte_indices = []
        response = self.validate_response(home_page_response) or self.home_page_response
        m = _tx.ON_DEMAND_FILE_REGEX.search(str(response))
        if not m:
            raise Exception("Couldn't get KEY_BYTE indices (ondemand.s not found)")
        on_demand_file_index = m.group(1)
        regex = _re.compile(_tx.ON_DEMAND_HASH_PATTERN.format(on_demand_file_index))
        hm = regex.search(str(response))
        if not hm:
            raise Exception("Couldn't get KEY_BYTE indices (hash not found)")
        filename = hm.group(1)
        on_demand_file_url = (
            f"https://abs.twimg.com/responsive-web/client-web/"
            f"ondemand.s.{filename}a.js")
        on_demand_file_response = await session.request(
            method="GET", url=on_demand_file_url, headers=headers)
        for item in _tx.INDICES_REGEX.finditer(str(on_demand_file_response.text)):
            key_byte_indices.append(item.group(2))
        if not key_byte_indices:
            raise Exception("Couldn't get KEY_BYTE indices")
        key_byte_indices = list(map(int, key_byte_indices))
        return key_byte_indices[0], key_byte_indices[1:]

    _tx.ClientTransaction.get_indices = _patched_get_indices
except Exception as _patch_err:  # pragma: no cover
    # If the patch can't be applied (e.g. twikit fixed it upstream or layout
    # changed again), don't crash the whole module — the X fetch will just fail
    # at runtime with its own error.
    print(f"[x] twikit patch skipped: {_patch_err}")
# --- end monkey patch ---------------------------------------------------------

from .models import Item

COOKIES_PATH = "cookies.json"


def _tweet_list(resp):
    """twikit 1.x returns a list of tweets; 2.x returns a Result object
    with a .results list. Normalize to an iterable of tweets."""
    if resp is None:
        return []
    if isinstance(resp, list):
        return resp
    # twikit 2.x Result-like object
    return getattr(resp, "results", None) or getattr(resp, "tweets", None) or []


def _normalize_cookies(raw):
    """Accept both twikit dict ({name:value}) and browser-export list
    ([{name,value,...}]) formats. twikit's set_cookies only accepts a dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        return {c["name"]: c["value"] for c in raw if c.get("name") and c.get("value")}
    raise ValueError(f"Unrecognized cookies.json format: {type(raw).__name__}")


async def _get_client():
    from twikit import Client

    client = Client("en-US")
    if os.path.exists(COOKIES_PATH):
        with open(COOKIES_PATH, encoding="utf-8") as f:
            cookies = _normalize_cookies(json.load(f))
        client.set_cookies(cookies)
        return client
    username = os.getenv("X_USERNAME")
    email = os.getenv("X_EMAIL")
    password = os.getenv("X_PASSWORD")
    if not (username and password):
        raise RuntimeError("No cookies.json and X_USERNAME/X_PASSWORD not set.")
    await client.login(auth_info_1=username, auth_info_2=email, password=password)
    client.save_cookies(COOKIES_PATH)
    return client


def _within(dt, since):
    if dt is None:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= since


def _to_item(tweet, group, source_name) -> Item:
    published = getattr(tweet, "created_at_datetime", None)
    sn = tweet.user.screen_name
    return Item(
        id=str(tweet.id),
        source="x",
        source_name=source_name,
        group=group,
        author=f"@{sn}",
        text=tweet.text or "",
        url=f"https://x.com/{sn}/status/{tweet.id}",
        published=published,
        metrics={
            "likes": getattr(tweet, "favorite_count", 0) or 0,
            "retweets": getattr(tweet, "retweet_count", 0) or 0,
            "replies": getattr(tweet, "reply_count", 0) or 0,
        },
    )


async def _fetch_lists(client, cfg, since) -> List[Item]:
    items: List[Item] = []
    per = cfg["x"].get("tweets_per_handle", 15)
    for list_key, spec in cfg["x"].get("lists", {}).items():
        for handle in spec.get("handles", []):
            try:
                user = await client.get_user_by_screen_name(handle)
                tweets = _tweet_list(await user.get_tweets("Tweets", count=per))
                for t in tweets:
                    if getattr(t, "text", "").startswith("RT @"):
                        continue
                    if _within(getattr(t, "created_at_datetime", None), since):
                        items.append(_to_item(t, list_key, handle))
                await asyncio.sleep(2)
            except Exception as e:
                print(f"  [x] @{handle} ({list_key}) failed: {e}")
    return items


async def _fetch_keywords(client, cfg, since) -> List[Item]:
    items: List[Item] = []
    ks = cfg["x"].get("keyword_search", {})
    if not ks.get("enabled"):
        return items
    per = ks.get("per_query", 15)
    min_likes = ks.get("min_likes", 20)
    for q in ks.get("queries", []):
        try:
            tweets = _tweet_list(await client.search_tweet(q, product="Latest", count=per))
            for t in tweets:
                if getattr(t, "text", "").startswith("RT @"):
                    continue
                if (getattr(t, "favorite_count", 0) or 0) < min_likes:
                    continue
                if _within(getattr(t, "created_at_datetime", None), since):
                    items.append(_to_item(t, "keyword", f"keyword:{q}"))
            await asyncio.sleep(2)
        except Exception as e:
            print(f"  [x] search '{q}' failed: {e}")
    return items


async def _fetch_all(cfg, since) -> List[Item]:
    client = await _get_client()
    a = await _fetch_lists(client, cfg, since)
    b = await _fetch_keywords(client, cfg, since)
    return a + b


def fetch(cfg: dict, since: datetime) -> List[Item]:
    if not cfg.get("x", {}).get("enabled"):
        return []
    try:
        return asyncio.run(_fetch_all(cfg, since))
    except Exception as e:
        print(f"[x] fetch aborted: {e}")
        return []
