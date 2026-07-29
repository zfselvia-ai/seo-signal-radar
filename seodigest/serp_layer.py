"""SERP Volatility & Consensus Layer.

Core principle from spec: never average raw values from SISTRIX / Algoroo / AWR
(apples, thermometers, kangaroos). Store raw for provenance; make product
decisions on the 180-day PERCENTILE of each source independently. Then combine
into two axes:

  Global Heat   — is the whole market shaking?  (SISTRIX macro, Algoroo confirm)
  Vertical Heat — which INDUSTRIES are shaking? (AWR per-vertical)

This is a UNIVERSAL product: Vertical Heat tracks generic industry verticals
(ecommerce / publisher / SaaS / local / international), NOT any one site's own
keyword universe. Read the two axes as a four-quadrant diagnosis.

Data sources are pluggable adapters. Default is "manual" (you record readings
via store.append_serp_reading); AWR has an official API/MCP and is the natural
first real integration. Until history accrues, percentiles are best-effort.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from . import store


# ------------------------------ percentile --------------------------------
def percentile_of(value: float, history: List[float]) -> Optional[float]:
    """Percentile rank (0-100) of `value` within `history`. None if too little data."""
    vals = [v for v in history if v is not None]
    if len(vals) < 10:            # not enough history to be meaningful yet
        return None
    below = sum(1 for v in vals if v < value)
    equal = sum(1 for v in vals if v == value)
    return round((below + 0.5 * equal) / len(vals) * 100, 1)


def band_for(percentile: Optional[float], bands: List[dict]) -> str:
    if percentile is None:
        return "Unknown"
    for b in bands:
        if percentile <= b["max"]:
            return b["label"]
    return bands[-1]["label"]


def _history_values(rows, source, window_days, key_filter=None):
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    out = []
    for r in rows:
        if r.get("source") != source:
            continue
        if key_filter and not key_filter(r):
            continue
        d = r.get("date")
        try:
            dd = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if dd < cutoff:
            continue
        try:
            out.append(float(r.get("raw_value")))
        except (TypeError, ValueError):
            continue
    return out


# --------------------------- heat computation ------------------------------
def compute_heat(cfg: dict, today_readings: List[dict]) -> dict:
    """Compute Global and Vertical heat from today's raw readings + history.

    today_readings: list of dicts with at least
        {source, raw_value, country, device, vertical, keyword_group}
    Returns a snapshot dict ready for rendering / archiving / event detection.
    """
    sl = cfg["serp_layer"]
    bands = sl["bands"]
    window = sl["percentile_window_days"]
    src_roles = {name: v["role"] for name, v in sl["sources"].items()}
    history = store.load_serp_history()

    per_source = {}
    for r in today_readings:
        src = r.get("source")
        try:
            raw = float(r.get("raw_value"))
        except (TypeError, ValueError):
            continue
        hist = _history_values(history, src, window)
        pct = percentile_of(raw, hist)
        per_source[src] = {
            "raw": raw,
            "percentile": pct,
            "band": band_for(pct, bands),
            "role": src_roles.get(src, "unknown"),
            "country": r.get("country", ""),
            "device": r.get("device", ""),
        }

    # Global Heat = macro source (sistrix) percentile; Algoroo = confirmation.
    global_src = next((s for s, v in per_source.items() if v["role"] == "global"), None)
    confirm_src = next((s for s, v in per_source.items() if v["role"] == "confirmation"), None)
    global_pct = per_source[global_src]["percentile"] if global_src else None
    global_band = per_source[global_src]["band"] if global_src else "Unknown"

    # Source agreement: how many independent sources are High/Extreme.
    hot_bands = {"High", "Extreme"}
    agree_hot = sum(1 for v in per_source.values()
                    if v["role"] in ("global", "confirmation") and v["band"] in hot_bands)
    agree_total = sum(1 for v in per_source.values()
                      if v["role"] in ("global", "confirmation"))

    # Vertical Heat = per-industry (AWR). Percentile per vertical, then take the
    # max + fraction of tracked verticals that are anomalous (High/Extreme).
    # Universal: keyed on the generic `vertical` label, not any own-site cohort.
    vertical_rows = [r for r in today_readings if src_roles.get(r.get("source")) == "vertical"]
    vertical_results = []
    for r in vertical_rows:
        try:
            raw = float(r.get("raw_value"))
        except (TypeError, ValueError):
            continue
        vlabel = r.get("vertical") or r.get("keyword_group", "")
        hist = _history_values(
            history, r.get("source"), window,
            key_filter=lambda h, v=vlabel: (h.get("vertical") or h.get("keyword_group")) == v,
        )
        pct = percentile_of(raw, hist)
        vertical_results.append({
            "vertical": vlabel,
            "percentile": pct,
            "band": band_for(pct, bands),
        })
    configured_verticals = sl.get("verticals", [])
    vertical_pct = max((c["percentile"] for c in vertical_results
                        if c["percentile"] is not None), default=None)
    anomalous = [c for c in vertical_results if c["band"] in hot_bands]
    vertical_fraction = (len(anomalous) / len(configured_verticals)) if configured_verticals else 0.0

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "global_heat": global_pct,
        "global_band": global_band,
        "vertical_heat": vertical_pct,
        "vertical_band": band_for(vertical_pct, bands),
        "source_agreement": f"{agree_hot}/{agree_total}" if agree_total else "0/0",
        "per_source": per_source,
        "verticals": vertical_results,
        "anomalous_verticals": [c["vertical"] for c in anomalous],
        "vertical_fraction": round(vertical_fraction, 2),
        "quadrant": _quadrant(global_pct, vertical_pct),
        "interpretation": _interpret(global_band, band_for(vertical_pct, bands),
                                     [c["vertical"] for c in anomalous]),
    }


def _quadrant(global_pct, vertical_pct) -> str:
    """Four-quadrant label. Threshold 80 = High+."""
    if global_pct is None or vertical_pct is None:
        return "Insufficient data"
    g_hi, r_hi = global_pct >= 80, vertical_pct >= 80
    if g_hi and r_hi:
        return "Broad update likely, specific verticals hit hardest"
    if g_hi and not r_hi:
        return "Market-wide flux, no single vertical standing out"
    if not g_hi and r_hi:
        return "Vertical/regional event, not a broad update"
    return "Normal noise, no action"


def _interpret(global_band, vertical_band, anomalous_verticals) -> str:
    if global_band == "Unknown":
        return ("Insufficient SERP history to judge. Recording readings; "
                "percentiles activate after ~10+ days of data.")
    q = _quadrant_from_bands(global_band, vertical_band)
    verticals = ", ".join(anomalous_verticals) if anomalous_verticals else "none"
    return f"{q} Affected verticals: {verticals}. Monitor; confirm via Google Status + GSC before acting."


def _quadrant_from_bands(global_band, vertical_band) -> str:
    hot = {"High", "Extreme"}
    g, r = global_band in hot, vertical_band in hot
    if g and r:
        return "Broad volatility concentrated in specific verticals."
    if g and not r:
        return "Broad market volatility, no single vertical standing out."
    if not g and r:
        return "Localized to specific verticals/regions, not market-wide."
    return "Within normal range."


# --------------------------- event detection ------------------------------
def detect_event(cfg: dict, today_snapshot: dict,
                 recent_snapshots: List[dict]) -> Optional[dict]:
    """Decide whether a Suspected Volatility Event should be (re)generated.

    recent_snapshots: previous days' snapshots (newest last), for the
    'sustained N days' rules.
    """
    rules = cfg["serp_layer"]["event_rules"]
    g = today_snapshot.get("global_heat")
    r = today_snapshot.get("vertical_heat")
    frac = today_snapshot.get("vertical_fraction", 0.0)

    reasons = []
    # Condition B: single-day extreme global spike.
    if g is not None and g >= rules["global_spike_min"]:
        reasons.append(f"Global Heat {g} >= {rules['global_spike_min']} (spike)")

    # Condition A: sustained elevated global heat.
    need = rules["global_sustained_days"]
    series = [s.get("global_heat") for s in recent_snapshots[-(need - 1):]] + [g]
    if all(x is not None and x >= rules["global_sustained_min"] for x in series) \
            and len(series) >= need:
        reasons.append(f"Global Heat >= {rules['global_sustained_min']} for {need} days")

    # Condition C: vertical heat + vertical breadth.
    if r is not None and r >= rules["vertical_min"] and frac >= rules["vertical_fraction"]:
        reasons.append(f"Vertical Heat {r} with {int(frac*100)}% verticals anomalous")

    if not reasons:
        return None
    return {
        "status": "Detected",
        "detected_at": today_snapshot.get("as_of"),
        "reasons": reasons,
        "global_heat": g,
        "vertical_heat": r,
        "affected_verticals": today_snapshot.get("anomalous_verticals", []),
        "google_confirmed": False,   # set true once Google Status corroborates
    }


def collect_manual_readings(cfg: dict) -> List[dict]:
    """Read today's readings from serp_history.csv for today's date.

    In manual mode you (or a wired adapter) append rows via
    store.append_serp_reading; this pulls today's back for heat computation.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return [r for r in store.load_serp_history() if r.get("date") == today]
