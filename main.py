"""SEO Signal Radar — orchestrator.

Subcommands:
  python main.py daily            # fetch, curate, render daily + archive
  python main.py daily --dry-run  # fetch + dedup only, no LLM
  python main.py weekly           # Search Intelligence Weekly (Tue)
  python main.py monthly          # State of Search Monthly (4th working day)
  python main.py brief            # generate any pending Special Briefs
  python main.py dashboard        # rebuild the static dashboard from archive
  python main.py notify           # send compressed push for the latest day
  python main.py serp-add ...     # record a manual SERP volatility reading
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

import yaml
from dotenv import load_dotenv

from seodigest import (x_source, rss_source, google_status, store,
                       summarize, reports, render, serp_layer, dashboard, notify)


def load_config(path="config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _tz(cfg):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(cfg["brand"].get("timezone", "UTC"))
    except Exception:
        return timezone.utc


def _now_local(cfg):
    return datetime.now(_tz(cfg))


def _date_str(cfg):
    return _now_local(cfg).strftime("%Y-%m-%d")


def compute_since(cfg):
    lookback = datetime.now(timezone.utc) - timedelta(hours=cfg["daily"]["lookback_hours"])
    last = store.last_run()
    if last and last < lookback:
        return last
    return lookback


def _run_meta(cfg, x_items, rss_items, gs_items, fresh, provider_hint=None):
    """Per-run health record, archived alongside the digest.

    Why bother: every failure mode in this pipeline is SILENT. If Nitter dies
    entirely, or a feed URL quietly 404s after a blog migration, the run still
    succeeds and produces a short digest — which is indistinguishable from a
    genuinely quiet news day. You'd trust a thin digest for weeks. Recording
    reachability per run makes the difference visible on the dashboard.
    """
    xh = dict(x_source.LAST_HEALTH or {})
    rh = dict(rss_source.LAST_HEALTH or {})
    return {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "x": {"ok": xh.get("ok", 0), "total": xh.get("total", 0),
                  "failed_names": xh.get("failed_names", [])[:10],
                  "items": len(x_items)},
            "rss": {"ok": rh.get("ok", 0), "total": rh.get("total", 0),
                    "failed_names": rh.get("failed_names", [])[:10],
                    "items": len(rss_items)},
            "google_status": {"items": len(gs_items)},
        },
        "candidates_fetched": len(x_items) + len(rss_items) + len(gs_items),
        "candidates_fresh": len(fresh),
        "llm_provider": provider_hint or "",
    }


# ------------------------------ daily -------------------------------------
def run_daily(cfg, dry_run=False):
    since = compute_since(cfg)
    print(f"[*] window since {since.isoformat()}")

    x_items = x_source.fetch(cfg, since)
    xh = x_source.LAST_HEALTH or {}
    print(f"    X: {len(x_items)} items from {xh.get('ok', 0)}/{xh.get('total', 0)} accounts")
    rss_items = rss_source.fetch(cfg, since)
    rh = rss_source.LAST_HEALTH or {}
    print(f"    RSS: {len(rss_items)} items from {rh.get('ok', 0)}/{rh.get('total', 0)} feeds")
    gs_items, confirmed = google_status.fetch(cfg, since)
    print(f"    Google Status: {len(gs_items)} items, {len(confirmed)} confirmed updates")

    # Loud warning when a whole source layer is down — a thin digest caused by
    # a dead Nitter must never be mistaken for a quiet day.
    for label, h in (("X/Nitter", xh), ("RSS", rh)):
        if h.get("enabled") and h.get("total") and h.get("ok", 0) == 0:
            print(f"[!!] {label} is COMPLETELY unavailable — today's digest is "
                  f"not representative. Check the source config.")

    all_items = gs_items + x_items + rss_items
    fresh = store.filter_unseen(all_items)
    print(f"[*] {len(fresh)} fresh after dedup")

    # SERP snapshot (manual readings if present; framework no-ops cleanly if empty)
    serp_snapshot = None
    if cfg.get("serp_layer", {}).get("enabled"):
        readings = serp_layer.collect_manual_readings(cfg)
        if readings:
            serp_snapshot = serp_layer.compute_heat(cfg, readings)
            print(f"[*] SERP heat: global={serp_snapshot.get('global_heat')} "
                  f"vertical={serp_snapshot.get('vertical_heat')}")

    if dry_run:
        for it in fresh[:50]:
            print(f"    - [{it.group}/{it.source_name}] {it.author}: {it.text[:80]}")
        print("[dry-run] stop before LLM.")
        return

    date_str = _date_str(cfg)
    run_meta = _run_meta(cfg, x_items, rss_items, gs_items, fresh)

    if not fresh and not confirmed:
        print("[*] nothing new.")
        store.archive_run_meta(cfg, date_str, run_meta)
        if cfg.get("dashboard", {}).get("enabled"):
            dpath = dashboard.write_dashboard(cfg)
            print(f"[*] rebuilt dashboard: {dpath}")
        return

    if not fresh:
        print("[*] no fresh items to summarize; rebuilding dashboard only.")
        store.archive_run_meta(cfg, date_str, run_meta)
        if cfg.get("dashboard", {}).get("enabled"):
            dpath = dashboard.write_dashboard(cfg)
            print(f"[*] rebuilt dashboard: {dpath}")
        # Still send notification from the latest archived digest so the
        # daily push is reliable even on quiet days.
        if cfg.get("notify", {}).get("enabled"):
            _send_notify_from_latest_archive(cfg)
        return

    digest = summarize.summarize_daily(cfg, fresh)
    digest["confirmed_updates"] = confirmed
    kept = sum(len(v) for v in digest.get("sections", {}).values())
    print(f"[*] kept {kept} signals")

    md, html = render.render_daily(digest, cfg, date_str, serp_snapshot)
    paths = render.write_report(cfg, "daily", date_str, md, html)
    store.archive_daily(cfg, date_str, digest, serp_snapshot)
    store.archive_run_meta(cfg, date_str, run_meta)
    store.commit_seen(fresh)
    for fmt, p in paths.items():
        print(f"[*] wrote daily {fmt}: {p}")

    # Rebuild the dashboard from the fresh archive (single source of truth).
    if cfg.get("dashboard", {}).get("enabled"):
        dpath = dashboard.write_dashboard(cfg)
        print(f"[*] rebuilt dashboard: {dpath}")

    # Compressed push notification (only sends if NOTIFY_WEBHOOK_URL is set).
    if cfg.get("notify", {}).get("enabled"):
        res = notify.send(cfg, digest, date_str, serp_snapshot)
        if res.get("sent"):
            print(f"[*] notification sent via {res.get('channel')}")
        else:
            print(f"[*] notification not sent ({res.get('reason') or res.get('error')})")

    # Special briefs triggered by confirmed ranking updates.
    pending = google_status.pending_special_briefs(
        confirmed, cfg["reports"]["special_brief"]["post_update_delay_days"])
    if pending:
        print(f"[*] {len(pending)} special brief(s) pending — run `python main.py brief`")


# ------------------------------ weekly ------------------------------------
def run_weekly(cfg):
    rep = reports.generate_weekly(cfg, _now_local(cfg))
    md, html = render.render_weekly(rep, cfg)
    paths = render.write_report(cfg, "weekly", _date_str(cfg), md, html)
    for fmt, p in paths.items():
        print(f"[*] wrote weekly {fmt}: {p}")


# ------------------------------ monthly -----------------------------------
def _is_nth_working_day(dt, n):
    """True if `dt` is the nth working day (Mon-Fri) of its month."""
    count = 0
    d = dt.replace(day=1)
    while d.month == dt.month:
        if d.weekday() < 5:
            count += 1
            if count == n and d.day == dt.day:
                return True
            if count == n:
                return False
        d += timedelta(days=1)
    return False


def run_monthly(cfg, force=False):
    now = _now_local(cfg)
    if not force and not _is_nth_working_day(now, 4):
        print("[*] not the 4th working day; skip (use --force to override).")
        return
    rep = reports.generate_monthly(cfg, now)
    md, html = render.render_monthly(rep, cfg)
    paths = render.write_report(cfg, "monthly", _date_str(cfg), md, html)
    for fmt, p in paths.items():
        print(f"[*] wrote monthly {fmt}: {p}")


# ------------------------------ brief -------------------------------------
def run_brief(cfg):
    since = datetime.now(timezone.utc) - timedelta(days=30)
    _, confirmed = google_status.fetch(cfg, since)
    pending = google_status.pending_special_briefs(
        confirmed, cfg["reports"]["special_brief"]["post_update_delay_days"])
    if not pending:
        print("[*] no special briefs pending.")
        return
    for ev in pending:
        rep = reports.generate_special_brief(cfg, ev)
        md, html = render.render_special(rep, cfg)
        stamp = f"{_date_str(cfg)}_{ev.get('id','event')}_{ev.get('brief_node','')}"
        paths = render.write_report(cfg, "special", stamp, md, html)
        for fmt, p in paths.items():
            print(f"[*] wrote special {fmt}: {p}")


# ------------------------------ dashboard / notify ------------------------
def run_dashboard(cfg):
    if not cfg.get("dashboard", {}).get("enabled"):
        print("[*] dashboard disabled in config.")
        return
    path = dashboard.write_dashboard(cfg)
    print(f"[*] wrote dashboard: {path}")


def _send_notify_from_latest_archive(cfg):
    """Send notification using the most recent archived digest."""
    end = datetime.now(timezone.utc)
    records = store.load_archive_range(cfg, end - timedelta(days=7), end)
    if not records:
        print("[*] no recent archive to notify from.")
        return
    latest = sorted(records, key=lambda r: r.get("date", ""))[-1]
    digest = {"headline": latest.get("headline", ""),
              "sections": latest.get("sections", {})}
    date_str = latest.get("date", _date_str(cfg))
    res = notify.send(cfg, digest, date_str, latest.get("serp"))
    if res.get("sent"):
        print(f"[*] notification sent via {res.get('channel')}")
    else:
        print(f"[*] notification not sent ({res.get('reason') or res.get('error')}). Preview:\n")
        print(res.get("preview", ""))


def run_notify(cfg):
    """Rebuild the compressed push from the most recent archived day and send."""
    _send_notify_from_latest_archive(cfg)


# ------------------------------ serp-add ----------------------------------
def run_serp_add(cfg, args):
    store.append_serp_reading({
        "date": args.date or _date_str(cfg),
        "source": args.source, "country": args.country, "device": args.device,
        "vertical": args.vertical, "keyword_group": args.keyword_group,
        "raw_value": args.value, "sample_size": args.sample_size,
        "source_updated_at": datetime.now(timezone.utc).isoformat(),
        "data_quality": args.quality,
    })
    print(f"[*] recorded {args.source} reading {args.value}")


def main():
    load_dotenv()
    p = argparse.ArgumentParser(description="SEO Signal Radar")
    p.add_argument("--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("daily"); d.add_argument("--dry-run", action="store_true")
    sub.add_parser("weekly")
    m = sub.add_parser("monthly"); m.add_argument("--force", action="store_true")
    sub.add_parser("brief")
    sub.add_parser("dashboard", help="rebuild the static dashboard from the archive")
    sub.add_parser("notify", help="send the compressed push for the latest day")

    sa = sub.add_parser("serp-add", help="record a manual SERP volatility reading")
    sa.add_argument("--source", required=True, choices=["sistrix", "algoroo", "awr"])
    sa.add_argument("--value", required=True, type=float)
    sa.add_argument("--country", default=""); sa.add_argument("--device", default="mobile")
    sa.add_argument("--vertical", default=""); sa.add_argument("--keyword-group", dest="keyword_group", default="")
    sa.add_argument("--sample-size", dest="sample_size", default="")
    sa.add_argument("--quality", default="ok"); sa.add_argument("--date", default="")

    args = p.parse_args()
    cfg = load_config(args.config)

    if args.cmd == "daily":
        run_daily(cfg, dry_run=args.dry_run)
    elif args.cmd == "weekly":
        run_weekly(cfg)
    elif args.cmd == "monthly":
        run_monthly(cfg, force=args.force)
    elif args.cmd == "brief":
        run_brief(cfg)
    elif args.cmd == "dashboard":
        run_dashboard(cfg)
    elif args.cmd == "notify":
        run_notify(cfg)
    elif args.cmd == "serp-add":
        run_serp_add(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
