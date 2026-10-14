#!/usr/bin/env python3
"""Re-curate degraded archive days through the LLM.

When the daily LLM call fails, summarize_daily falls back to raw uncurated
items (why_it_matters = "Auto-extracted from source (LLM summary
unavailable)."). Those days stay junky in the archive forever. This script
rebuilds candidate Items from the archived signals and runs the normal
summarize pipeline over them, then writes the curated digest back.

Usage:  python scripts/recurate.py [YYYY-MM-DD ...]      (default: all degraded)
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml
from dotenv import load_dotenv

from seodigest import summarize
from seodigest.models import Item

load_dotenv(ROOT / ".env")

DEGRADED_MARK = "LLM summary unavailable"


def load_cfg():
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    # Cross-day dedup must be off: these signals were already seen-deduped by
    # the original run, and "recent stories" would now mean the FUTURE days
    # that were curated after them.
    cfg["daily"]["dedupe_lookback_days"] = 0
    return cfg


def rebuild_items(cfg, digest):
    """Turn archived signals back into candidate Items for the LLM."""
    x_accounts = cfg["x"]["accounts"]
    rss_feeds = {f["name"]: f for f in cfg["rss"]["feeds"]}
    weights = (cfg.get("scoring") or {}).get("source_weights", {})

    items = []
    for i, sig in enumerate(digest.get("signals", [])):
        srcs = sig.get("sources") or [{}]
        url = srcs[0].get("url", "")
        name = srcs[0].get("name") or sig.get("evidence") or "unknown"
        handle = name.lstrip("@")
        if "x.com/" in url and handle in x_accounts:
            acct = x_accounts[handle]
            stype = acct.get("source_type", "practitioner_observation")
            group = (acct.get("tags") or ["general"])[0]
            tags = acct.get("tags", [])
            ci = bool(acct.get("commercial_interest", False))
            source, source_name = "x", handle
        elif "x.com/" in url:
            stype, group, tags, ci = "expert_analysis", "general", [], False
            source, source_name = "x", handle
        else:
            feed = rss_feeds.get(name, {})
            stype = feed.get("category", "trade_news")
            group = stype
            tags = [stype]
            ci = bool(feed.get("commercial_interest", False))
            source, source_name = "rss", name
        items.append(Item(
            id=url or f"re-{i}",
            source=source,
            source_name=source_name,
            group=group,
            author=sig.get("evidence") or source_name,
            text=sig.get("what_happened", ""),
            url=url,
            source_type=stype,
            tags=tags,
            commercial_interest=ci,
            weight=float(weights.get(stype, 0.6)),
        ))
    return items


def main():
    days = sys.argv[1:]
    cfg = load_cfg()
    archive_dir = ROOT / cfg["output"]["archive_dir"]

    if not days:
        days = [p.stem for p in sorted(archive_dir.glob("*.json"))]
    for day in days:
        path = archive_dir / f"{day}.json"
        digest = json.load(open(path))
        sigs = digest.get("signals", [])
        if not any(DEGRADED_MARK in (s.get("why_it_matters") or "") for s in sigs):
            print(f"[skip] {day}: not degraded")
            continue

        items = rebuild_items(cfg, digest)
        print(f"[*] {day}: re-curating {len(items)} signals ...")
        new = summarize.summarize_daily(cfg, items)
        if not new.get("signals"):
            print(f"[!] {day}: re-curation produced nothing; keeping original")
            continue

        digest["signals"] = new["signals"]
        digest["sections"] = new["sections"]
        digest["headline"] = new["headline"]
        digest["action_items"] = new.get("action_items", [])
        digest["dropped_count"] = new.get("dropped_count", 0)
        digest["generated_at"] = new.get("generated_at") or digest.get("generated_at")
        rm = digest.setdefault("run_meta", {})
        rm["llm_provider"] = "recurated"
        json.dump(digest, open(path, "w"), ensure_ascii=False, indent=2)
        kept = new["signals"]
        print(f"    -> {len(kept)} curated signals; headline: {new['headline'][:80]}")


if __name__ == "__main__":
    main()
