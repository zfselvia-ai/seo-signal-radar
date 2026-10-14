#!/usr/bin/env python3
"""Publish a hand-curated digest through the normal pipeline.

Output half of the no-LLM workflow (llm.provider: none). Input is a JSON file in
the SAME schema the LLM is asked for (see summarize.SYSTEM): headline, signals[],
action_items[], dropped_count. It then does exactly what summarize_daily does
after the model answers — verify every cited URL against the candidate pool,
cross-day dedup, group into sections — and archives, renders and rebuilds the
dashboard like run_daily.

The pool file comes from dump_candidates.py and is the ONLY admissible source of
citations: a URL that is not in the pool is stripped and the signal is marked
`unverified`, same as a fabricated LLM citation. Hand-written is not a licence to
cite things nobody fetched.

  python scripts/ingest_curated.py --curated /tmp/curated.json --pool /tmp/pool.json
  python scripts/ingest_curated.py ... --date 2026-10-09      # default: today (brand tz)
  python scripts/ingest_curated.py ... --dry-run              # print, write nothing
  python scripts/ingest_curated.py ... --no-dedupe            # skip cross-day dedup

Several --pool files may be given; they are unioned.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.getcwd())


def _load_pool(paths) -> tuple[list, list]:
    from seodigest.models import Item
    items, confirmed, seen = [], [], set()
    for p in paths:
        with open(p, encoding="utf-8") as f:
            pool = json.load(f)
        for d in pool.get("items", []):
            # Dedupe on URL, not id: a retweet carries the ORIGINAL tweet's id but
            # its own x.com/<retweeter>/ URL, and both must stay citable.
            if d["url"] in seen:
                continue
            seen.add(d["url"])
            pub = d.get("published")
            d["published"] = datetime.fromisoformat(pub) if pub else None
            items.append(Item(**d))
        if not confirmed:
            confirmed = pool.get("confirmed_updates", [])
    return items, confirmed


def _as_of(update: dict, date_str: str):
    """The update as the Status API described it on `date_str`.

    The pool is fetched after the fact, so it holds each update's FINAL state.
    For a back-dated record before the update ended, that state is the future:
    it was still rolling out and had no completion note. Returns
    (update, reconstructed?).
    """
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
        end = datetime.fromisoformat(update["end"]).date() if update.get("end") else None
    except (ValueError, TypeError):
        return update, False
    if end and end > day:
        return {**update, "end": None, "ongoing": True, "latest_update": ""}, True
    return update, False


def _active_on(update: dict, date_str: str, grace_days: int = 1) -> bool:
    """Was this confirmed update running on `date_str` (± a day for completion news)?"""
    from datetime import timedelta
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
        start = datetime.fromisoformat(update["start"]).date() if update.get("start") else None
        end = datetime.fromisoformat(update["end"]).date() if update.get("end") else None
    except (ValueError, TypeError):
        return False
    if start and start > day + timedelta(days=grace_days):
        return False
    if end and end < day - timedelta(days=grace_days):
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--curated", required=True)
    ap.add_argument("--backfilled", action="store_true",
                    help="mark the record as back-dated hand curation")
    ap.add_argument("--pool", required=True, action="append")
    ap.add_argument("--date", help="record date YYYY-MM-DD (default: today in brand tz)")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-dedupe", action="store_true",
                    help="skip cross-day dedup (use when re-publishing the same day)")
    ap.add_argument("--no-notify", action="store_true")
    args = ap.parse_args()

    import yaml
    from dotenv import load_dotenv
    from seodigest import summarize, store, render, dashboard, notify
    load_dotenv()
    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if args.date:
        date_str = args.date
    else:
        from main import _date_str
        date_str = _date_str(cfg)

    with open(args.curated, encoding="utf-8") as f:
        data = json.load(f)
    pool, confirmed = _load_pool(args.pool)
    print(f"[*] pool: {len(pool)} items, {len(confirmed)} confirmed updates")
    print(f"[*] curated: {len(data.get('signals', []))} signals, "
          f"{len(data.get('action_items', []))} action items -> {date_str}")

    if args.no_dedupe:
        cfg = dict(cfg)
        cfg["daily"] = {**cfg["daily"], "dedupe_lookback_days": 0}
    digest = summarize.finalize_digest(cfg, data, pool, pool)
    # Only the updates that were live on this record's date, as they were then.
    # A back-dated record must not claim it saw an update that started weeks
    # later, nor that one still rolling out had already finished.
    as_of = [_as_of(c, date_str) for c in confirmed if _active_on(c, date_str)]
    digest["confirmed_updates"] = [u for u, _ in as_of]
    rewound = {u["url"]: u for u, r in as_of if r and u.get("url")}
    # A Status excerpt reads "[kind] title. <latest status line>"; the status
    # line is the part that changes over time. Keep the verbatim head only.
    for s in digest.get("signals", []):
        for src in s.get("sources", []):
            u = rewound.get(src.get("url"))
            head = u and f"[{u.get('kind')}] {u.get('title')}."
            if head and (src.get("excerpt") or "").startswith(head):
                src["excerpt"] = head
    digest["curated_by"] = data.get("curated_by", "human")
    if args.backfilled or data.get("backfilled"):
        digest["backfilled"] = True

    unverified = [s["what_happened"][:60] for s in digest.get("signals", []) if s.get("unverified")]
    if unverified:
        print(f"[!] {len(unverified)} signal(s) cite URLs not in the pool -> marked unverified:")
        for u in unverified:
            print(f"      - {u}")
    kept = sum(len(v) for v in digest.get("sections", {}).values())
    print(f"[*] {kept} entries across {len(digest.get('sections', {}))} sections")

    if args.dry_run:
        print(json.dumps({k: v for k, v in digest.items() if k != "confirmed_updates"},
                         ensure_ascii=False, indent=2)[:6000])
        print("[dry-run] nothing written.")
        return 0

    # Preserve the run health record the fetch wrote earlier today, if any.
    apath = os.path.join(cfg["output"]["archive_dir"], f"{date_str}.json")
    run_meta = None
    if os.path.exists(apath):
        try:
            with open(apath, encoding="utf-8") as f:
                run_meta = json.load(f).get("run_meta")
        except Exception:
            run_meta = None

    md, html = render.render_daily(digest, cfg, date_str, None)
    paths = render.write_report(cfg, "daily", date_str, md, html)
    store.archive_daily(cfg, date_str, digest, None)
    if run_meta:
        store.archive_run_meta(cfg, date_str, run_meta)
    for fmt, p in paths.items():
        print(f"[*] wrote daily {fmt}: {p}")
    if cfg.get("dashboard", {}).get("enabled"):
        print(f"[*] rebuilt dashboard: {dashboard.write_dashboard(cfg)}")
    if cfg.get("notify", {}).get("enabled") and not args.no_notify:
        res = notify.send(cfg, digest, date_str, None)
        print(f"[*] notification {'sent via ' + str(res.get('channel')) if res.get('sent') else 'not sent (' + str(res.get('reason') or res.get('error') or 'unknown') + ')'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
