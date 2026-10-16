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
import re
from datetime import datetime, timedelta, timezone
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


def commit_seen(items: List[Item], mirrored=frozenset()) -> None:
    """Mark items seen. Also fingerprints original posts by `mirrored` people,
    so a cross-post that arrives on the other channel in a later run (Nitter
    was down yesterday, up today) is still recognised as already shown."""
    state = _load_state()
    seen = state.get("seen_ids", [])
    seen.extend(it.id for it in items)
    state["seen_ids"] = seen[-MAX_SEEN:]
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=FINGERPRINT_DAYS)
    recent = []
    for r in state.get("recent_posts", []):
        try:
            if datetime.fromisoformat(r["t"]) >= cutoff:
                recent.append(r)
        except (KeyError, TypeError, ValueError):
            continue
    for it in items:
        if _is_original_post(it, mirrored) and it.published >= cutoff:
            recent.append({"p": it.person, "s": it.source,
                           "t": it.published.isoformat(), "x": _post_text(it.text)})
    state["recent_posts"] = recent
    state["last_run"] = now.isoformat()
    _save_state(state)


# ------------------------- cross-platform duplicates -----------------------
# Some people are followed on X (via Nitter) AND on Bluesky, so their posts
# still arrive when Nitter is down: rss.feeds entries with `same_as: <handle>`.
# When both channels work, one post arrives twice under two different ids,
# which id-based dedup cannot see.
#
# Thresholds calibrated on real posts (2026-10-13, Glenn Gabe / Aleyda Solis /
# Barry Schwartz / Lily Ray): all 17 genuine cross-posts were published within
# 1.3h of each other and shared >= 0.62 of the shorter post's words (URLs
# removed); different posts by the same person within 3h shared <= 0.29.
CROSSPOST_WINDOW_H = 3
CROSSPOST_OVERLAP = 0.5
CROSSPOST_MIN_TOKENS = 3      # "Fantastic article" must not swallow a real post
FINGERPRINT_DAYS = 8          # > the 7-day evergreen window
_URL_RE = re.compile(r"https?://\S+|\b[\w-]+(?:\.[\w-]+)+/\S*")
# Retweets and replies are never matched: a reply's twin on Bluesky is often a
# standalone post, and keeping the reply (which the ranker drops) would lose it.
_NOT_ORIGINAL = ("RT by @", "R to @", "RT @")


def mirrored_people(cfg: dict) -> set:
    """X handles that also have a Bluesky feed (`same_as`) in rss.feeds."""
    return {str(f["same_as"]).strip().lstrip("@").lower()
            for f in (cfg.get("rss") or {}).get("feeds", []) if f.get("same_as")}


def _post_text(text: str) -> str:
    return " ".join(_URL_RE.sub(" ", text or "").split())[:600]


def _post_tokens(text: str) -> set:
    from .summarize import _tokens   # lazy: summarize imports store lazily too
    return _tokens(_post_text(text))


def _is_original_post(it: Item, mirrored) -> bool:
    return (bool(it.person) and it.person in mirrored
            and it.source in ("x", "bluesky") and it.published is not None
            and not (it.text or "").startswith(_NOT_ORIGINAL))


def collapse_crossposts(items: List[Item], mirrored, use_history: bool = True):
    """Drop the second copy of a post made on both X and Bluesky.

    Returns (kept, dropped), both in input order. When both copies are new the
    X one is kept (it also carries quoted-post and link-card text); a copy
    matching a post committed in an earlier run is dropped. Only pairs across
    the two platforms, by the same mirrored person, are ever collapsed.
    """
    if not mirrored:
        return list(items), []
    prior: dict = {}   # person -> [(source, published, tokens)]
    if use_history:
        for r in _load_state().get("recent_posts", []):
            try:
                prior.setdefault(r["p"], []).append(
                    (r["s"], datetime.fromisoformat(r["t"]), _post_tokens(r["x"])))
            except (KeyError, TypeError, ValueError):
                continue
    drop = set()
    for i in sorted(range(len(items)), key=lambda i: items[i].source != "x"):
        it = items[i]
        if not _is_original_post(it, mirrored):
            continue
        toks = _post_tokens(it.text)
        for src, ts, other in prior.get(it.person, []):
            m = min(len(toks), len(other))
            if (src != it.source and m >= CROSSPOST_MIN_TOKENS
                    and abs((it.published - ts).total_seconds()) <= CROSSPOST_WINDOW_H * 3600
                    and len(toks & other) / m >= CROSSPOST_OVERLAP):
                drop.add(i)
                break
        else:
            prior.setdefault(it.person, []).append((it.source, it.published, toks))
    return ([it for i, it in enumerate(items) if i not in drop],
            [it for i, it in enumerate(items) if i in drop])


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
        # Structured flags, not prose. A day where the LLM call failed still
        # produces cards — uncurated raw text wearing the same confidence and
        # priority labels as a real signal — and the ONLY thing distinguishing
        # it was English in the headline. Nothing downstream could filter it,
        # and a reader skimming the dashboard could not tell. Now it is a field.
        "degraded": bool(digest.get("degraded")),
        "degraded_reason": digest.get("degraded_reason", ""),
        "quiet_day": bool(digest.get("quiet_day")),
    }
    # Provenance for hand-curated / back-dated records (scripts/ingest_curated.py).
    # `generated_at` already betrays a backfill; this makes it a field.
    for k in ("curated_by", "backfilled", "llm_error"):
        if digest.get(k):
            record[k] = digest[k]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return path


def _record_rank(r: dict) -> int:
    """Curated (by hand or by a model) outranks a raw/uncurated fallback day."""
    return 0 if r.get("degraded") else 1


def daily_record_guard(cfg: dict, date_str: str, digest: dict):
    """Return why today's archived record must be kept instead, or None.

    A second run on the same day sees only what the first run had not already
    marked seen, so its record is thinner, and a raw run can replace a day that
    was curated by hand or by a model. Today's existing record is therefore
    replaced only by one at least as good: curated over raw; raw over raw only
    with at least as many signals. Hand edits are never overwritten by a run.
    """
    path = os.path.join(cfg["output"]["archive_dir"], f"{date_str}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        return None
    if old.get("curated_by"):
        return f"{date_str} was curated by hand ({old['curated_by']})"
    new_rank, old_rank = _record_rank(digest), _record_rank(old)
    if new_rank < old_rank:
        return f"{date_str} already has a curated record, and this run has only the raw feed"
    if new_rank == old_rank and len(digest.get("signals") or []) < len(old.get("signals") or []):
        return (f"{date_str} already has {len(old['signals'])} signals of the same kind, "
                f"this run only {len(digest.get('signals') or [])}")
    return None


def archive_run_meta(cfg: dict, date_str: str, run_meta: dict) -> str:
    """Attach/refresh run health on today's record, creating a stub if needed.

    Deliberately separate from archive_daily because the runs that most need a
    health record are the ones that produce NO digest: if every source is
    unreachable, run_daily returns early and nothing would ever be written —
    the one day you'd most want to know something was wrong.
    """
    adir = cfg["output"]["archive_dir"]
    os.makedirs(adir, exist_ok=True)
    path = os.path.join(adir, f"{date_str}.json")
    record = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                record = json.load(f)
        except Exception:
            record = {}
    record.setdefault("date", date_str)
    record.setdefault("headline", "")
    record.setdefault("sections", {})
    record.setdefault("signals", [])
    record.setdefault("serp", {})
    record.setdefault("confirmed_updates", [])
    # A run that found nothing new must not overwrite the health record of the
    # run that produced today's content: the strip on the page describes that
    # run (sources reached, model grounding). Keep the latest attempt beside it.
    if record.get("signals") and record.get("run_meta"):
        record["run_meta"]["last_empty_run_at"] = run_meta.get("ran_at")
    else:
        record["run_meta"] = run_meta
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
