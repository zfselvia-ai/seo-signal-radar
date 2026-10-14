#!/usr/bin/env python3
"""Dump the full candidate pool for hand curation, WITHOUT touching dedup state.

This is the input half of the no-LLM workflow (llm.provider: none). The daily
run can only publish raw items; a human (or Kiro) still has to read the pool and
decide what matters. This writes that pool out in two forms:

  <out>.json  — every Item as a dict (the ONLY thing ingest_curated.py accepts as
                a citable source, mirroring verify_sources in the LLM path)
  <out>.md    — the same items, compact and grouped by source, for reading

Nothing is written to data/seen.json, so re-running is free.

  python scripts/dump_candidates.py --since 2026-08-05 --out /tmp/pool
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.getcwd())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="ISO date/datetime (UTC). Default: now - lookback_hours")
    ap.add_argument("--out", default="/tmp/pool", help="output path without extension")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    import yaml
    from seodigest import x_source, rss_source, google_status, manual_source, store
    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if args.since:
        since = datetime.fromisoformat(args.since)
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
    else:
        since = datetime.now(timezone.utc) - timedelta(hours=cfg["daily"]["lookback_hours"])
    print(f"[*] window since {since.isoformat()}")

    x_items = x_source.fetch(cfg, since)
    xh = x_source.LAST_HEALTH or {}
    print(f"    X: {len(x_items)} items from {xh.get('ok', 0)}/{xh.get('total', 0)} accounts")
    rss_items = rss_source.fetch(cfg, since)
    rh = rss_source.LAST_HEALTH or {}
    print(f"    RSS: {len(rss_items)} items from {rh.get('ok', 0)}/{rh.get('total', 0)} feeds")
    gs_items, confirmed = google_status.fetch(cfg, since)
    print(f"    Google Status: {len(gs_items)} items, {len(confirmed)} confirmed updates")
    manual_items = manual_source.fetch(cfg, since)
    print(f"    Manual inbox: {len(manual_items)} capture(s)")

    items = gs_items + manual_items + x_items + rss_items
    # Same post on X and Bluesky -> list it once. No history: a dump ignores
    # seen state by design (it is used for back-dated curation).
    items, xdup = store.collapse_crossposts(items, store.mirrored_people(cfg),
                                            use_history=False)
    if xdup:
        print(f"    collapsed {len(xdup)} X/Bluesky cross-post(s)")
    # Newest first within each source, so the reader sees recent posts before
    # the evergreen tail.
    items.sort(key=lambda it: (it.source_name, -(it.published.timestamp() if it.published else 0)))

    with open(args.out + ".json", "w", encoding="utf-8") as f:
        json.dump({"since": since.isoformat(),
                   "fetched_at": datetime.now(timezone.utc).isoformat(),
                   "confirmed_updates": confirmed,
                   "items": [it.to_dict() for it in items]},
                  f, ensure_ascii=False, indent=1)

    lines = [f"# Candidate pool · since {since.date()} · {len(items)} items", ""]
    cur = None
    for i, it in enumerate(items):
        if it.source_name != cur:
            cur = it.source_name
            flag = " · commercial" if it.commercial_interest else ""
            lines += ["", f"## {it.source_name}  ({it.source_type} w={it.weight:.2f}{flag})"]
        d = it.published.strftime("%m-%d") if it.published else "??-??"
        text = " ".join((it.text or "").split())
        lines.append(f"- [{i}] {d} {text[:300]}")
        lines.append(f"      {it.url}")
    with open(args.out + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[*] wrote {args.out}.json and {args.out}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
