#!/usr/bin/env python3
"""One-off: mark pre-existing fallback days as degraded.

Why this exists as a script and not as logic in the reader: the `degraded` flag
was added after these records were written, so days where the LLM call failed
sit in the archive wearing `confidence: Observed` and `impact: P2` — i.e. they
tell the reader "somebody assessed this, it's worth an afternoon of testing"
about raw, uncurated source text. One such card reads "The Black Mirror music
video will be on @BET this weekend."

The honest fix is to correct the stored records once, rather than teach every
reader to sniff for the old headline forever. Detection is deliberately narrow:
the fallback path wrote a fixed sentinel into every signal it produced, so we
match on that rather than guessing from tone or field emptiness.

Idempotent. Run from the repo root:  python3 scripts/backfill_degraded.py
Add --dry-run to see what would change without writing.
"""
from __future__ import annotations

import glob
import json
import os
import sys

# The exact string the old _fallback_digest stamped into every signal it made.
SENTINEL = "LLM summary unavailable"


def is_fallback(rec: dict) -> bool:
    if rec.get("degraded"):
        return False  # already marked; nothing to do
    if SENTINEL in (rec.get("headline") or ""):
        return True
    return any(SENTINEL in (s.get("why_it_matters") or "")
               for s in rec.get("signals", []))


def fix(rec: dict) -> dict:
    rec["degraded"] = True
    rec["degraded_reason"] = rec.get("degraded_reason") or (
        "LLM step failed (backfilled — original reason not recorded)")
    rec.setdefault("quiet_day", False)

    def demote(s: dict) -> dict:
        s["degraded"] = True
        # Same capping the live fallback path now applies: nobody observed
        # these, and nobody judged them worth testing.
        s["confidence"] = "Speculative"
        s["impact"] = "P3"
        return s

    rec["signals"] = [demote(s) for s in rec.get("signals", [])]
    for sec, arr in (rec.get("sections") or {}).items():
        if sec == "Today's Action Items":
            continue
        for s in arr:
            demote(s)
    return rec


def main() -> int:
    dry = "--dry-run" in sys.argv
    paths = sorted(glob.glob(os.path.join("data", "archive", "*.json")))
    if not paths:
        print("No archive records found — run this from the repo root.")
        return 1
    changed = 0
    fixed_dates = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                rec = json.load(f)
        except Exception as e:
            print(f"  skip {os.path.basename(p)}: {e}")
            continue
        if not is_fallback(rec):
            continue
        n = len(rec.get("signals", []))
        print(f"  {'would mark' if dry else 'marking'} "
              f"{os.path.basename(p)} degraded ({n} signals -> Speculative/P3)")
        if not dry:
            rec = fix(rec)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False, indent=2)
            fixed_dates.append(rec.get("date") or
                               os.path.splitext(os.path.basename(p))[0])
        changed += 1

    # The .md/.html reports are the archival copy someone may read with no
    # dashboard in front of them. Leaving them saying "P2 / Observed" while the
    # dashboard says "uncurated" would just move the lie somewhere quieter.
    if fixed_dates:
        _rerender(fixed_dates)

    print(f"\n{changed} record(s) {'would be' if dry else ''} updated "
          f"out of {len(paths)}.")
    if changed and not dry:
        print("Rebuild the dashboard to see it:  python3 main.py dashboard")
    return 0


def _rerender(dates: list) -> None:
    """Re-emit the daily report files for the records we just corrected."""
    try:
        sys.path.insert(0, os.getcwd())
        import yaml
        from seodigest import render
        with open("config.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except Exception as e:
        print(f"  ! could not re-render reports ({e}); "
              f"the archive is fixed but reports/daily/*.md still show old labels")
        return
    for d in dates:
        try:
            with open(os.path.join("data", "archive", f"{d}.json"),
                      encoding="utf-8") as f:
                rec = json.load(f)
            md, html = render.render_daily(rec, cfg, d, rec.get("serp") or None)
            render.write_report(cfg, "daily", d, md, html)
            print(f"  re-rendered reports/daily/{d}.*")
        except Exception as e:
            print(f"  ! re-render failed for {d}: {e}")


if __name__ == "__main__":
    sys.exit(main())
