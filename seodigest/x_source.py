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
import os
from datetime import datetime, timezone
from typing import List

from .models import Item

COOKIES_PATH = "cookies.json"


async def _get_client():
    from twikit import Client

    client = Client("en-US")
    if os.path.exists(COOKIES_PATH):
        client.load_cookies(COOKIES_PATH)
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
                tweets = await user.get_tweets("Tweets", count=per)
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
            tweets = await client.search_tweet(q, product="Latest", count=per)
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
