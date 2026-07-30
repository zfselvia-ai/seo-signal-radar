"""Persistence: dedup state, structured daily archive, and SERP percentile DB.

- seen.json          : ids already shown (dedup across runs) + last_run
- data/archive/*.json: one structured record per daily run; weekly/monthly
                       aggregate from these instead of re-scraping.
- data/serp_history.csv: raw SERP volatility readings for percentile math.
"""
from __future__ import annotations

import csv
import glob
import json
import os
from datetime import datetime, timezone
from typing import List

from .models import Item, Signal

DATA_DIR = "data"
STATE_PATH = os.path.join(DATA_DIR, "seen.json")
SERP_HISTORY = os.path.join(DATA_DIR, "serp_history.csv")
MAX_SEEN = 8000


# ----------------------------- dedup state --------------------------------
def _load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"seen_ids": [], "last_run": None}


def _save_state(state: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def last_run() -> datetime | None:
    ts = _load_state().get("last_run")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def filter_unseen(items: List[Item]) -> List[Item]:
    seen = set(_load_state().get("seen_ids", []))
    return [it for it in items if it.id not in seen]


def commit_seen(items: List[Item]) -> None:
    state = _load_state()
    seen = state.get("seen_ids", [])
    seen.extend(it.id for it in items)
    state["seen_ids"] = seen[-MAX_SEEN:]
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)


# --------------------------- structured archive ---------------------------
def archive_daily(cfg: dict, date_str: str, digest: dict,
                  serp_snapshot: dict | None = None) -> str:
    """Save one structured daily record. Returns its path.

    Never overwrites an existing record with empty data — protects against
    same-day re-runs where dedup has consumed all fresh items.
    """
    adir = cfg["output"]["archive_dir"]
    os.makedirs(adir, exist_ok=True)
    sections = digest.get("sections", {})
    signals = digest.get("signals", [])
    has_content = bool(signals) or any(
        isinstance(v, list) and v for v in sections.values())
    path = os.path.join(adir, f"{date_str}.json")
    if os.path.exists(path) and not has_content:
        print(f"[*] skip overwrite {date_str}: new digest empty, keep existing")
        return path
    record = {
        "date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "headline": digest.get("headline", ""),
        "sections": sections,
        "signals": signals,
        "serp": serp_snapshot or {},
        "confirmed_updates": digest.get("confirmed_updates", []),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return path


def load_archive_range(cfg: dict, start: datetime, end: datetime) -> List[dict]:
    """Load daily archive records whose date falls in [start, end] (inclusive)."""
    adir = cfg["output"]["archive_dir"]
    out = []
    for path in sorted(glob.glob(os.path.join(adir, "*.json"))):
        base = os.path.splitext(os.path.basename(path))[0]
        try:
            d = datetime.strptime(base, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if start.date() <= d.date() <= end.date():
            try:
                with open(path, encoding="utf-8") as f:
                    out.append(json.load(f))
            except Exception:
                pass
    return out


# --------------------------- SERP history DB -------------------------------
SERP_COLS = ["date", "source", "country", "device", "vertical", "keyword_group",
             "raw_value", "percentile_180d", "zscore_90d", "sample_size",
             "source_updated_at", "data_quality"]


def append_serp_reading(row: dict) -> None:
    """Append one raw SERP volatility reading. Percentile computed later."""
    os.makedirs(DATA_DIR, exist_ok=True)
    exists = os.path.exists(SERP_HISTORY)
    with open(SERP_HISTORY, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SERP_COLS)
        if not exists:
            w.writeheader()
        w.writerow({c: row.get(c, "") for c in SERP_COLS})


def load_serp_history() -> List[dict]:
    if not os.path.exists(SERP_HISTORY):
        return []
    with open(SERP_HISTORY, encoding="utf-8") as f:
        return list(csv.DictReader(f))
