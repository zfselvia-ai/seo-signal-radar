"""Static single-file dashboard — the product's "single source of truth".

Reads the structured daily archive (data/archive/*.json) and emits ONE
self-contained HTML file (no build step, no external calls) with the data
embedded as JSON. Three views:

  Today          — Morning Brief bar, headline, SERP weather, the 7 sections,
                   Watchlist and Action Items.
  Algorithm Map  — the signature element: a three-track timeline
                   (Official confirmations / External SERP flux / Community
                   discussion) across 7D/30D/90D/1Y.
  Source Library — every signal ever archived, searchable + filterable by
                   section, confidence, impact and vertical.

Visual language: Linear light. Inter for UI/body, JetBrains Mono for data and
labels; monochrome ink on white; red/amber/green used functionally only.
Universal product — no own-site data anywhere.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from . import store


# --------------------------- data assembly --------------------------------
def _all_records(cfg: dict) -> list:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=cfg["dashboard"].get("history_days", 365))
    records = store.load_archive_range(cfg, start, end)
    records.sort(key=lambda r: r.get("date", ""))
    return records


def _flatten_signals(records: list) -> list:
    """One flat list of signals across all days, each tagged with its date."""
    out = []
    for rec in records:
        date = rec.get("date", "")
        sections = rec.get("sections", {})
        for section, sigs in sections.items():
            if section == "Today's Action Items":
                continue
            for s in sigs:
                if not s.get("what_happened"):
                    continue
                row = {
                    "date": date,
                    "section": section,
                    "what_happened": s.get("what_happened", ""),
                    "why_it_matters": s.get("why_it_matters", ""),
                    "evidence": s.get("evidence", ""),
                    "who_it_affects": s.get("who_it_affects", ""),
                    "what_to_do": s.get("what_to_do", ""),
                    "confidence": s.get("confidence", ""),
                    "impact": s.get("impact", ""),
                    "sources": s.get("sources", []),
                }
                # Playbook fields: only carried when present, so the archive
                # stays lean but a past tactic remains searchable months later.
                for k in ("how_to_test", "success_metric", "effort"):
                    if s.get(k):
                        row[k] = s[k]
                out.append(row)
    return out


def _map_tracks(records: list) -> list:
    """Three-track timeline events. Each event: {date, track, label, band, detail}.

    Tracks:
      official  — Google Search Status confirmed updates (Confirmed layer)
      external  — SERP volatility (Global/Vertical heat bands per day)
      community — high-confidence community signals in SERP/Algorithm sections
    """
    events = []
    for rec in records:
        date = rec.get("date", "")
        # official track — confirmed updates
        for c in rec.get("confirmed_updates", []):
            events.append({
                "date": date, "track": "official",
                "label": c.get("title") or c.get("kind", "Confirmed update"),
                "band": "Extreme" if c.get("ongoing") else "High",
                "detail": c.get("kind", ""),
            })
        # external track — SERP heat for the day
        serp = rec.get("serp") or {}
        gb = serp.get("global_band")
        if gb and gb not in ("Unknown", None):
            events.append({
                "date": date, "track": "external",
                "label": f"Global {gb} ({serp.get('global_heat','?')})",
                "band": gb,
                "detail": f"Vertical {serp.get('vertical_band','?')} · "
                          f"{serp.get('quadrant','')}",
            })
        # community track — SERP/Algorithm-section signals
        for s in rec.get("sections", {}).get("SERP & Algorithm Signals", []):
            if not s.get("what_happened"):
                continue
            events.append({
                "date": date, "track": "community",
                "label": s.get("what_happened", ""),
                "band": _conf_to_band(s.get("confidence", "")),
                "detail": s.get("confidence", ""),
            })
    return events


def _conf_to_band(conf: str) -> str:
    return {"Confirmed": "High", "Data-backed": "High",
            "Observed": "Elevated", "Speculative": "Normal"}.get(conf, "Normal")


def build_data(cfg: dict) -> dict:
    records = _all_records(cfg)
    latest = records[-1] if records else {}
    # Send all archived records (not just latest) so the frontend date picker
    # can render any past day without a round-trip.
    archive_by_date = {r.get("date", ""): r for r in records}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "thesis": cfg["dashboard"].get("thesis", ""),
        "thesis_zh": cfg["dashboard"].get("thesis_zh", ""),
        "title": cfg["brand"].get("daily_title", "SEO Signal Radar"),
        "map_ranges": cfg["dashboard"].get("map_ranges", ["7D", "30D", "90D", "1Y"]),
        "verticals": cfg["daily"].get("verticals", []),
        "sections": cfg["daily"].get("sections", []),
        "today": latest,
        "archive": archive_by_date,
        "library": _flatten_signals(records),
        "map_events": _map_tracks(records),
        "days_archived": len(records),
    }


# ------------------------------ render -------------------------------------
def render_html(cfg: dict) -> str:
    data = build_data(cfg)
    payload = json.dumps(data, ensure_ascii=False)
    return _TEMPLATE.replace("__DATA__", payload)


def write_dashboard(cfg: dict) -> str:
    out = cfg["dashboard"]["output_file"]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    html = render_html(cfg)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return out


# The template lives in a sibling file to keep this module readable.
def _load_template() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "dashboard_template.html"), encoding="utf-8") as f:
        return f.read()


_TEMPLATE = _load_template()
