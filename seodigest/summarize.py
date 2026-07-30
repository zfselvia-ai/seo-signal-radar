"""LLM step for the DAILY digest.

Turns raw Items into scored Signals, each with the 5-field schema, sorted into
the 7 fixed sections. The scoring formula and curation rubric come from config
so tuning happens in YAML, not code.

Provider-agnostic (anthropic | openai). Output is strict JSON.
"""
from __future__ import annotations

import json
import os
from typing import List

from .models import Item

LANG_RULES = {
    "bilingual": ("Write 'what_happened' and 'evidence' in English (source language). "
                  "Write 'why_it_matters', 'who_it_affects' and 'what_to_do' in "
                  "Simplified Chinese, keeping English SEO jargon (AEO, GEO, agentic "
                  "traffic, AI Overviews, CTR, E-E-A-T, query fan-out) as-is."),
    "en": "Write all fields in English.",
    "zh": ("Write all fields in Simplified Chinese, keeping English SEO jargon "
           "(AEO, GEO, agentic traffic, AI Overviews) as-is."),
}

SYSTEM = """You are the editor of a daily SEO/GEO SIGNAL RADAR for advanced, \
professional SEOs. This is NOT a news feed. Your job: detect what genuinely \
requires an expert to change behaviour today, and cut everything else.

Mental model: X discovers anomalies -> official data confirms -> cases/data \
decide whether it's worth acting. Reward evidence; punish hype.

This is a UNIVERSAL product — never assume the reader owns a specific site. \
'who_it_affects' must name audience verticals from: {verticals}.

CANDIDATES come from four X lists (official_signals, algo_serp, technical_data, \
geo_ai), keyword searches, RSS blogs, and the Google Search Status API.

STEP 1 - SCORE each candidate 0-100:
{weights}
Then SUBTRACT penalties: marketing/promo -{promo}, duplicate/rehash -{dup}.

SOURCE TRUST LADDER — every candidate carries `source_type` and `trust_weight`. \
Multiply your source-authority judgement by that weight:
{trust_ladder}
Hard rules that follow from it:
- An `official_person` (Google staff explaining mechanics) OUTRANKS any pundit \
commentary about the same topic. They post rarely; when they explain a boundary, \
that IS the signal.
- `commercial_interest: true` means the author sells a tool or consultancy. Their \
OPINION caps at "Observed". Only original data, code, or a paper lifts them to \
"Data-backed". Never let a vendor's framing become the headline.
- A candidate only counts as `reproducible_experiment` if it shows: {experiment_reqs}. \
Missing any one of those -> downgrade to practitioner_observation.
- Multiple independent trusted accounts reporting the SAME phenomenon is itself \
evidence — merge them into one signal and raise confidence.

STEP 2 - KEEP only the best {min_signals}-{max_signals}. If fewer are truly \
valuable, keep fewer. NEVER pad. Curation rubric:
KEEP:
{keep}
DROP:
{drop}

STEP 3 - For each kept signal fill the schema. Assign:
- confidence tier ({tiers}). Anything from Google Search Status API = "Confirmed".
- impact/priority: P0 (check now) | P1 (act this week) | P2 (worth testing) | \
P3 (awareness only). Separate OBSERVATION from ACTION: unconfirmed flux is rarely P0.
Keep every field to <= {max_chars} characters — headline density, not paragraphs.

STEP 4 - Sort each signal into exactly one section:
{sections}
Rules: Google-confirmed updates -> "Confirmed Search Updates". Explanations of \
search mechanics by Google staff (source_type official_person) -> "Official \
Explanations". Information-retrieval / entity / knowledge-graph / patent / paper \
analysis -> "Research & Retrieval". Unconfirmed flux/rumor -> "Unconfirmed \
Watchlist" (advise monitor, not edit). Cross-cutting to-dos summarised in \
"Today's Action Items" tagged P0/P1/P2.
Sections may be EMPTY. "Official Explanations" and "Research & Retrieval" are \
slower-moving than news — leave them out entirely rather than padding them with \
weak items.

OUTPUT LANGUAGE: {lang}

Return ONLY valid JSON (no markdown fences), schema:
{{
  "headline": "one line: the single most important development today (<={max_chars} chars)",
  "signals": [
    {{
      "what_happened": "<={max_chars} chars, headline-style",
      "why_it_matters": "why an expert should care",
      "evidence": "official doc | dataset | case | observation — be specific",
      "who_it_affects": "one or more of: {verticals}",
      "what_to_do": "check X | test Y | monitor Z | no action",
      "confidence": "Confirmed | Data-backed | Observed | Speculative",
      "impact": "P0 | P1 | P2 | P3",
      "section": "<one of the sections>",
      "score": <int 0-100>,
      "sources": [{{"name": "author or feed", "url": "..."}}]
    }}
  ],
  "action_items": [{{"text": "...", "impact": "P0|P1|P2"}}],
  "dropped_count": <int>
}}"""


def _trust_ladder_block(scoring: dict) -> str:
    sw = scoring.get("source_weights", {})
    return "\n".join(f"- {k} = {v}" for k, v in sw.items())


def _weights_block(scoring: dict) -> str:
    w = scoring["weights"]
    labels = {
        "source_authority": "source authority",
        "data_and_evidence": "data & evidence",
        "novelty": "novelty",
        "potential_impact": "potential impact",
        "actionability": "actionability",
    }
    return "\n".join(f"- {w[k]}% {labels.get(k, k)}" for k in w)


def build_prompt(cfg: dict, items: List[Item]) -> tuple[str, str]:
    daily = cfg["daily"]
    scoring = cfg["scoring"]
    system = SYSTEM.format(
        weights=_weights_block(scoring),
        promo=scoring["penalty"]["marketing_promo"],
        dup=scoring["penalty"]["duplicate"],
        min_signals=daily["min_signals"],
        max_signals=daily["max_signals"],
        keep="\n".join(f"- {x}" for x in cfg["curation"]["keep"]),
        drop="\n".join(f"- {x}" for x in cfg["curation"]["drop"]),
        tiers=" | ".join(scoring["confidence_tiers"]),
        sections="\n".join(f"- {s}" for s in daily["sections"]),
        lang=LANG_RULES.get(cfg["brand"]["language"], LANG_RULES["bilingual"]),
        max_chars=daily["max_field_chars"],
        verticals=" / ".join(daily["verticals"]),
        trust_ladder=_trust_ladder_block(scoring),
        experiment_reqs=", ".join(scoring.get("experiment_requirements", [])),
    )
    payload = [{
        "group": it.group,
        "source": it.source,
        "author": it.author,
        "source_name": it.source_name,
        # Trust metadata — the model must weigh these, not just the text.
        "source_type": it.source_type,
        "trust_weight": it.weight,
        "tags": it.tags,
        "commercial_interest": it.commercial_interest,
        "text": it.text,
        "url": it.url,
        "likes": it.metrics.get("likes", 0),
    } for it in items]
    user = "CANDIDATES:\n" + json.dumps(payload, ensure_ascii=False)
    return system, user


def _strip_fences(text: str) -> str:
    if not text:
        return ""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    return text.strip()


def _extract_json(text: str) -> str:
    """Extract the JSON object from an LLM response.

    Reasoning models (e.g. Kimi K2.6) may emit chain-of-thought before the
    final JSON. This finds the last valid JSON object in the text.
    """
    if not text:
        return ""
    text = text.strip()
    # Fast path: already valid JSON
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    # Try ```json fenced block
    import re
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            json.loads(m.group(1))
            return m.group(1)
        except json.JSONDecodeError:
            pass
    # Find the last JSON object: search for {"headline" and balance braces
    for start in range(len(text) - 1, -1, -1):
        if text[start] == "{" and text[start:start + 12].lstrip().startswith("{"):
            # Try parsing from this position
            depth = 0
            for end in range(start, len(text)):
                if text[end] == "{":
                    depth += 1
                elif text[end] == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:end + 1]
                        try:
                            json.loads(candidate)
                            return candidate
                        except json.JSONDecodeError:
                            break
    # Last resort: return original
    return text


def _call_anthropic(cfg, system, user) -> str:
    from anthropic import Anthropic
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    resp = client.messages.create(
        model=cfg["llm"]["anthropic_model"],
        max_tokens=cfg["llm"]["max_output_tokens"],
        temperature=cfg["llm"]["temperature"],
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


def _call_openai(cfg, system, user) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model=cfg["llm"]["openai_model"],
        max_tokens=cfg["llm"]["max_output_tokens"],
        temperature=cfg["llm"]["temperature"],
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content


def _call_moonshot(cfg, system, user) -> str:
    """Moonshot / Kimi — OpenAI-compatible API, different base_url + key."""
    from openai import OpenAI
    client = OpenAI(
        api_key=os.getenv("MOONSHOT_API_KEY"),
        base_url=cfg["llm"].get("moonshot_base_url", "https://api.moonshot.ai/v1"),
    )
    resp = client.chat.completions.create(
        model=cfg["llm"]["moonshot_model"],
        max_tokens=cfg["llm"]["max_output_tokens"],
        temperature=cfg["llm"]["temperature"],
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content


def _call_scnet(cfg, system, user) -> str:
    """SCNet (超算互联网) — Kimi K2.6 via OpenAI-compatible API."""
    from openai import OpenAI
    client = OpenAI(
        api_key=os.getenv("SCNET_API_KEY"),
        base_url=cfg["llm"].get("scnet_base_url", "https://api.scnet.cn/api/llm/v1"),
        timeout=180,
        max_retries=2,
    )
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=cfg["llm"]["scnet_model"],
                max_tokens=cfg["llm"]["max_output_tokens"],
                temperature=cfg["llm"]["temperature"],
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
            )
            content = resp.choices[0].message.content
            if content:
                return content
            print(f"[scnet] attempt {attempt+1}: empty content, finish={resp.choices[0].finish_reason}")
        except Exception as e:
            print(f"[scnet] attempt {attempt+1} error: {e}")
            if attempt == 2:
                raise
    return ""


def _detect_provider() -> str:
    """Pick the provider from whichever API key is actually set."""
    for env_key, provider in (("SCNET_API_KEY", "scnet"),
                              ("MOONSHOT_API_KEY", "moonshot"),
                              ("ANTHROPIC_API_KEY", "anthropic"),
                              ("OPENAI_API_KEY", "openai")):
        if os.getenv(env_key, "").strip():
            return provider
    raise RuntimeError(
        "No LLM API key found. Set one of SCNET_API_KEY, MOONSHOT_API_KEY, "
        "ANTHROPIC_API_KEY or OPENAI_API_KEY in your .env (or as a GitHub secret)."
    )


def call_llm(cfg: dict, system: str, user: str) -> str:
    provider = cfg["llm"].get("provider", "auto")
    if provider == "auto":
        provider = _detect_provider()
    if provider in ("scnet", "glm"):
        return _call_scnet(cfg, system, user)
    if provider in ("moonshot", "kimi"):
        return _call_moonshot(cfg, system, user)
    if provider == "openai":
        return _call_openai(cfg, system, user)
    return _call_anthropic(cfg, system, user)


def _select_candidates(cfg: dict, items: List[Item]) -> List[Item]:
    """Pick a diverse, high-value subset of candidates for the LLM.

    Naively taking items[:N] biases toward whichever source ran first
    (often one noisy handle flooding the window). Instead we round-robin
    across groups so every list is represented, then top up from the rest.
    """
    max_candidates = cfg["daily"].get("max_signals", 8)
    # Bucket by group, preserving config group order.
    buckets: dict[str, List[Item]] = {}
    for it in items:
        buckets.setdefault(it.group, []).append(it)
    # Round-robin: take one from each non-empty bucket in turn.
    selected: List[Item] = []
    idx = {g: 0 for g in buckets}
    while len(selected) < max_candidates:
        progressed = False
        for g in buckets:
            if idx[g] < len(buckets[g]):
                selected.append(buckets[g][idx[g]])
                idx[g] += 1
                progressed = True
                if len(selected) >= max_candidates:
                    break
        if not progressed:
            break
    return selected


def summarize_daily(cfg: dict, items: List[Item]) -> dict:
    """Return a structured daily digest dict (grouped into sections)."""
    if not items:
        return {"headline": "No new SEO signals in this window.",
                "signals": [], "sections": {}, "action_items": [],
                "dropped_count": 0}
    # Pick a diverse subset so one noisy handle can't crowd out every other
    # source — the LLM curation rubric needs cross-source signal to work.
    candidates = _select_candidates(cfg, items)
    system, user = build_prompt(cfg, candidates)
    raw = _extract_json(call_llm(cfg, system, user))
    if not raw:
        print("[!] LLM returned empty response; falling back to raw items.")
        return _fallback_digest(items)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[!] LLM JSON parse failed: {e}; falling back to raw items.")
        return _fallback_digest(items)

    # Group signals into sections, preserving config order.
    grouped = {s: [] for s in cfg["daily"]["sections"]}
    for sig in data.get("signals", []):
        sec = sig.get("section")
        grouped.setdefault(sec, []).append(sig)
    if data.get("action_items"):
        grouped["Today's Action Items"] = [
            {"what_to_do": a.get("text", ""), "impact": a.get("impact", "P2")}
            if isinstance(a, dict) else {"what_to_do": a, "impact": "P2"}
            for a in data["action_items"]
        ]
    data["sections"] = {k: v for k, v in grouped.items() if v}

    # If the LLM dropped everything (e.g. all candidates were promo/noise),
    # fall back to raw items so the day isn't blank. The curation rubric is
    # advisory, not a hard gate — an expert still wants to see the raw feed.
    if not data.get("signals"):
        print(f"[!] LLM kept 0 signals (dropped {data.get('dropped_count', '?')}); "
              f"falling back to raw items.")
        return _fallback_digest(items)
    return data


_GROUP_TO_SECTION = {
    "official_signals": "Confirmed Search Updates",
    "algo_serp": "SERP & Algorithm Signals",
    "technical_data": "Technical SEO Experiments",
    "geo_ai": "AI Search / GEO",
    "rss": "Tools, Papers & Open Source",
    "keyword": "Unconfirmed Watchlist",
    "google_status": "Confirmed Search Updates",
}


def _fallback_digest(items: List[Item]) -> dict:
    """When the LLM fails, produce a basic digest from raw item text."""
    # Use the same diverse selection so a single noisy source can't dominate.
    pick = _select_candidates({"daily": {"max_signals": 8}}, items)
    signals = []
    for it in pick:
        section = _GROUP_TO_SECTION.get(it.group, "SERP & Algorithm Signals")
        signals.append({
            "what_happened": it.text[:120],
            "why_it_matters": "Auto-extracted from source (LLM summary unavailable).",
            "evidence": it.source_name,
            "who_it_affects": "all",
            "what_to_do": "monitor",
            "confidence": "Observed",
            "impact": "P2",
            "section": section,
            "sources": [{"name": it.source_name, "url": it.url}],
        })
    # Group by the section we just assigned.
    grouped: dict[str, list] = {}
    for s in signals:
        grouped.setdefault(s["section"], []).append(s)
    return {
        "headline": f"{len(signals)} signals from sources (LLM summary unavailable).",
        "signals": signals,
        "sections": grouped,
        "action_items": [],
        "dropped_count": max(0, len(items) - len(signals)),
    }
