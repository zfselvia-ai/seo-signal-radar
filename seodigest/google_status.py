"""Google Search Status API — the official confirmation layer.

Pulls incidents.json and filters to the products we care about using
`affected_products[].id` (the official schema marks `service_key` deprecated).
Classifies each incident (Core / Spam / Reviews / Discover / Ranking issue) and
flags whether it should trigger a Special Brief.

Docs: https://developers.google.com/search/blog + Search Status Dashboard.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from typing import List

from dateutil import parser as dtparser

from .models import Item


def _classify(title: str) -> str:
    t = (title or "").lower()
    if "spam" in t:
        return "Spam Update"
    if "review" in t:
        return "Reviews Update"
    if "discover" in t:
        return "Discover Update"
    if "core" in t:
        return "Core Update"
    if "ranking" in t:
        return "Ranking Issue"
    return "Search Update"


def _fetch_json(url: str) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": "seo-signal-radar/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Google status API failed: {resp.status}")
        return json.loads(resp.read().decode("utf-8"))


def fetch(cfg: dict, since: datetime) -> tuple[List[Item], List[dict]]:
    """Return (items, confirmed_updates).

    `items` feed the normal curation pipeline; `confirmed_updates` is the
    structured list used by the Confirmed layer and Special Brief triggers.
    """
    gs = cfg.get("google_status", {})
    if not gs.get("enabled"):
        return [], []

    wanted = gs.get("products", {})  # {family_name: product_id}
    id_to_family = {pid: fam for fam, pid in wanted.items()}
    trigger_families = set(gs.get("special_brief_on", []))

    try:
        incidents = _fetch_json(gs["incidents_url"])
    except Exception as e:
        print(f"[google_status] fetch failed: {e}")
        return [], []

    items: List[Item] = []
    confirmed: List[dict] = []

    for inc in incidents:
        products = inc.get("affected_products") or []
        matched = [id_to_family[p.get("id")] for p in products
                   if p.get("id") in id_to_family]
        if not matched:
            continue

        begin_raw = inc.get("begin")
        begin_dt = None
        if begin_raw:
            try:
                begin_dt = dtparser.parse(begin_raw)
            except Exception:
                begin_dt = None

        title = inc.get("external_desc", "") or ""
        kind = _classify(title)
        uri = inc.get("uri", "")
        detail_url = f"https://status.search.google.com/{uri}" if uri else \
            "https://status.search.google.com/"
        latest = (inc.get("most_recent_update") or {}).get("text", "")

        rec = {
            "id": inc.get("id"),
            "title": title,
            "kind": kind,
            "families": matched,
            "start": begin_raw,
            "end": inc.get("end"),
            "impact": inc.get("status_impact"),
            "ongoing": inc.get("end") is None,
            "latest_update": latest,
            "url": detail_url,
            "triggers_brief": bool(set(matched) & trigger_families),
        }
        confirmed.append(rec)

        # Recent incidents also flow into the daily digest as Item candidates.
        in_window = (begin_dt is None) or \
            (begin_dt.astimezone(timezone.utc) >= since.astimezone(timezone.utc))
        if in_window or rec["ongoing"]:
            items.append(Item(
                id=f"gstatus:{rec['id']}",
                source="google_status",
                source_name="Google Search Status",
                group="official_signals",
                author="Google Search Status Dashboard",
                text=f"[{kind}] {title}. {latest}".strip(),
                url=detail_url,
                published=begin_dt,
                metrics={"impact": rec["impact"] or ""},
            ))

    confirmed.sort(key=lambda r: r.get("start") or "", reverse=True)
    return items, confirmed


def pending_special_briefs(confirmed: List[dict], delay_days: int) -> List[dict]:
    """Given confirmed updates, decide which Special Brief node applies.

    - ongoing/just-started ranking update  -> 'update_alert' (monitoring only)
    - ended >= delay_days ago               -> 'post_update_analysis'
    """
    out = []
    now = datetime.now(timezone.utc)
    for rec in confirmed:
        if not rec.get("triggers_brief"):
            continue
        if rec.get("ongoing"):
            out.append({**rec, "brief_node": "update_alert"})
            continue
        end = rec.get("end")
        if not end:
            continue
        try:
            end_dt = dtparser.parse(end).astimezone(timezone.utc)
        except Exception:
            continue
        age_days = (now - end_dt).days
        if age_days >= delay_days:
            out.append({**rec, "brief_node": "post_update_analysis",
                        "days_since_end": age_days})
    return out
