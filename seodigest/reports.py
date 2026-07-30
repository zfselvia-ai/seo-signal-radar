"""Weekly, Monthly, and Special Brief generators.

Each reads the structured daily archive (data/archive/*.json) for its period and
asks the LLM to synthesise — NOT concatenate. Per spec:
  weekly  -> reduce information (judgments + experiments)
  monthly -> reduce misjudgment (structural trends + strategy)
  special -> event-driven (monitoring alert, or post-update analysis)

Reports never copy each other: a signal is escalated only if it keeps producing
evidence.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import store
from .summarize import call_llm, _extract_json, LANG_RULES


def _parse_report(raw: str, meta: dict, label: str) -> dict:
    """Parse an LLM report response without letting a bad day crash the run.

    Uses _extract_json (not _strip_fences) because reasoning models emit
    chain-of-thought before the JSON — the weekly and monthly generators used
    the weaker helper and would raise on any such response. And since these run
    unattended on a cron, an exception means no report at all and a red CI run;
    an "empty" record at least renders and says why.
    """
    data = _extract_json(raw or "")
    try:
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise ValueError("expected a JSON object")
        return {**meta, **parsed}
    except Exception as e:
        print(f"[!] {label} LLM output was not usable JSON: {e}")
        return {**meta, "empty": True,
                "error": f"LLM output could not be parsed as JSON ({e})"}


# ------------------------------ periods -----------------------------------
def _prev_mon_sun(now: datetime) -> tuple[datetime, datetime]:
    """Previous Monday..Sunday relative to `now` (which is typically Tuesday)."""
    weekday = now.weekday()  # Mon=0
    this_monday = (now - timedelta(days=weekday)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    start = this_monday - timedelta(days=7)
    end = this_monday - timedelta(days=1)
    return start, end


def _prev_calendar_month(now: datetime) -> tuple[datetime, datetime]:
    first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = first_this - timedelta(days=1)
    start = end.replace(day=1)
    return start, end


def _digest_archive(records: list) -> str:
    """Compact the archive into a token-lean JSON string for the LLM."""
    compact = []
    for r in records:
        compact.append({
            "date": r.get("date"),
            "headline": r.get("headline"),
            "confirmed_updates": [
                {"kind": c.get("kind"), "title": c.get("title"),
                 "ongoing": c.get("ongoing")}
                for c in r.get("confirmed_updates", [])
            ],
            "serp": {
                "global_heat": r.get("serp", {}).get("global_heat"),
                "vertical_heat": r.get("serp", {}).get("vertical_heat"),
                "quadrant": r.get("serp", {}).get("quadrant"),
                "anomalous_verticals": r.get("serp", {}).get("anomalous_verticals"),
            },
            "signals": [
                {k: s.get(k) for k in
                 ("what_happened", "why_it_matters", "evidence", "who_it_affects",
                  "what_to_do", "confidence", "impact", "section")}
                for s in r.get("signals", [])
            ],
        })
    return json.dumps(compact, ensure_ascii=False)


# ------------------------------ weekly ------------------------------------
WEEKLY_SYSTEM = """You are the editor of "Search Intelligence Weekly" for \
advanced SEOs. The weekly job is to REDUCE NOISE and form JUDGMENTS — not to \
rank news. Answer: what few things this week actually warrant changing behaviour?

Proportion: 20% what happened, 50% what it means, 30% what to check/test next week.
Keep <=10 signals aggregated into <=3 themes. Lead with CONCLUSIONS, not a news list.

You receive a JSON array of this week's daily archive records. Synthesise across \
days: escalate signals that kept producing evidence; drop one-offs that died.

OUTPUT LANGUAGE: {lang}

Return ONLY valid JSON (no fences):
{{
  "core_judgments": [
    {{"judgment": "...", "supporting_evidence": "...", "counter_evidence": "...",
      "applies_to": "which verticals", "confidence": "Confirmed|Data-backed|Observed|Speculative",
      "verify_next_week": "the metric/check that would confirm or kill this"}}
  ],
  "algo_serp_map": [
    {{"item": "...", "relation": "Confirmed|Correlated|Possibly Related|No Evidence"}}
  ],
  "google_platforms": ["..."],
  "ai_search_geo": ["new signals only, no over-trending"],
  "tech_seo_lab": [
    {{"hypothesis": "...", "test_on": "...", "cost": "...", "metric": "...",
      "observation_window": "...", "evidence_maturity": "..."}}
  ],
  "noise_cancelled": ["this week's non-actionable hype, stated plainly"],
  "next_week_actions": ["P0: check ...", "P1: test ...", "P2: watch ..."]
}}
core_judgments MUST have exactly 3 entries. next_week_actions <=3."""


def generate_weekly(cfg: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    start, end = _prev_mon_sun(now)
    records = store.load_archive_range(cfg, start, end)
    meta = {"period_start": start.strftime("%Y-%m-%d"),
            "period_end": end.strftime("%Y-%m-%d"),
            "days_covered": len(records)}
    if not records:
        return {**meta, "empty": True}
    system = WEEKLY_SYSTEM.format(
        lang=LANG_RULES.get(cfg["brand"]["language"], LANG_RULES["bilingual"]))
    user = "THIS WEEK'S DAILY ARCHIVE:\n" + _digest_archive(records)
    return _parse_report(call_llm(cfg, system, user), meta, "weekly")


# ------------------------------ monthly -----------------------------------
MONTHLY_SYSTEM = """You are the editor of "State of Search Monthly" for SEO \
leadership. The monthly job is to REDUCE MISJUDGMENT — find STRUCTURAL change \
and set STRATEGY. Don't report daily news; identify 3-5 narratives that define \
the month. Dare to draw conclusions.

Proportion: 15% what happened, 45% data & trends, 25% impact & opportunity, \
15% next-month allocation. Use a 90-day lens for algorithm impact, not just the \
month. For AI Search/GEO report stable metrics and 3-month direction, never \
single-prompt anecdotes.

You receive a JSON array of the month's daily archive records.

OUTPUT LANGUAGE: {lang}

Return ONLY valid JSON (no fences):
{{
  "executive_summary": {{"top_changes": ["..","..",".."], "risks": ["..",".."],
                          "opportunities": ["..",".."], "stop_doing": "one thing"}},
  "scoreboard": {{"confirmed_updates": 0, "incidents": 0, "serp_flux_days": 0,
                  "high_conf_signals": 0, "ai_search_updates": 0,
                  "notable_specs": 0, "new_open_source": 0, "myths_busted": 0}},
  "structural_trends": [
    {{"narrative": "...", "evidence": "...", "implication": "...",
      "direction_3mo": "..."}}
  ],
  "algorithm_impact_review": "90-day view: before/during/after, by page type & query intent",
  "ai_search_geo_index": ["stable metrics + 3-month direction"],
  "winners_losers": {{"winner_traits": ["..."], "loser_traits": ["..."]}},
  "experiment_results": [
    {{"experiment": "...", "hypothesis": "...", "result": "...",
      "confidence": "...", "status": "Scale|Continue|Modify|Stop|Inconclusive"}}
  ],
  "resource_allocation": {{"double_down": ["..."], "test": ["..."], "stop": ["..."]}}
}}
structural_trends MUST have 3-5 entries."""


def generate_monthly(cfg: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    start, end = _prev_calendar_month(now)
    records = store.load_archive_range(cfg, start, end)
    meta = {"period_start": start.strftime("%Y-%m-%d"),
            "period_end": end.strftime("%Y-%m-%d"),
            "days_covered": len(records)}
    if not records:
        return {**meta, "empty": True}
    system = MONTHLY_SYSTEM.format(
        lang=LANG_RULES.get(cfg["brand"]["language"], LANG_RULES["bilingual"]))
    user = "THIS MONTH'S DAILY ARCHIVE:\n" + _digest_archive(records)
    return _parse_report(call_llm(cfg, system, user), meta, "monthly")


# --------------------------- special brief --------------------------------
SPECIAL_SYSTEM = """You are writing a "Search Intelligence Special Brief" — an \
event-driven report triggered by a confirmed major event.

CRITICAL RULE: if the node is "update_alert" (an update just STARTED), write a \
MONITORING brief only — do NOT diagnose causes or prescribe fixes. Advise \
waiting until the update finishes + at least 7 days before comparing GSC data.
If the node is "post_update_analysis" (update ended >=7 days ago), THEN provide \
before/during/after analysis and recommendations.

OUTPUT LANGUAGE: {lang}

Return ONLY valid JSON (no fences):
{{
  "event_title": "...",
  "node": "update_alert | post_update_analysis",
  "what_we_know": ["..."],
  "what_not_to_do_yet": ["..."],     // for update_alert
  "monitoring_checklist": ["..."],
  "analysis": "..."                    // only meaningful for post_update_analysis
}}"""


def generate_special_brief(cfg: dict, event: dict) -> dict:
    system = SPECIAL_SYSTEM.format(
        lang=LANG_RULES.get(cfg["brand"]["language"], LANG_RULES["bilingual"]))
    user = "TRIGGERING EVENT:\n" + json.dumps(event, ensure_ascii=False)
    data = _parse_report(call_llm(cfg, system, user), {}, "special brief")
    return {**data, "_event": event}
