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
    "bilingual": ("Write 'what_happened' as: English headline | 中文一句话说明. "
                  "Write 'evidence' in English (source language). "
                  "Write 'why_it_matters', 'who_it_affects' and 'what_to_do' in "
                  "Simplified Chinese, keeping English SEO jargon (AEO, GEO, agentic "
                  "traffic, AI Overviews, CTR, E-E-A-T, query fan-out) as-is."),
    "en": "Write all fields in English.",
    "zh": ("Write all fields in Simplified Chinese, keeping English SEO jargon "
           "(AEO, GEO, agentic traffic, AI Overviews) as-is."),
}

SYSTEM = """You are the editor of a daily SEO/GEO SIGNAL RADAR for advanced, \
professional SEOs. This is NOT a news feed. Your job: surface what genuinely \
changes what an expert does — either because something shifted today, or \
because there is something well-evidenced worth TESTING — and cut everything else.

Mental model: X discovers anomalies -> official data confirms -> cases/data \
decide whether it's worth acting. Reward evidence; punish hype.

TWO KINDS OF VALUE, and you must deliver both:
1. NEWS — something changed and the reader may need to react.
2. PLAYBOOK — an evergreen, testable tactic with a real mechanism. It does NOT \
have to be new. Most days Google confirms nothing; a digest that only carries \
breaking news is worthless on those days, while the reader still has an \
afternoon to spend improving something. Never treat "this was published a few \
days ago" as a reason to drop a genuinely useful, well-evidenced tactic.

This is a UNIVERSAL product — never assume the reader owns a specific site. \
'who_it_affects' must name audience verticals from: {verticals}.

CANDIDATES come from curated X accounts, RSS blogs (official docs, controlled \
experiments, data research, trade news), and the Google Search Status API. You \
will be shown MANY more candidates than you may keep. That is deliberate: \
discard aggressively.

STEP 1 - SCORE each candidate 0-100:
{weights}
Then SUBTRACT penalties: marketing/promo -{promo}, duplicate/rehash -{dup}.
Note the weighting: ACTIONABILITY outranks NOVELTY. Being new is not a reason \
to act. An old finding the reader can test beats a fresh rumour they cannot.

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

STEP 2 - KEEP only the best {min_signals}-{max_signals}. You MUST keep at \
least {min_signals} signals unless fewer than {min_signals} candidates exist. \
Do NOT over-filter: if you have {min_signals} or more candidates, keeping only \
2-3 is a failure. When in doubt between dropping and keeping, KEEP — the reader \
is an expert who can judge for themselves. Curation rubric:
KEEP:
{keep}
DROP:
{drop}

STEP 3 - For each kept signal fill the schema. Assign:
- confidence tier ({tiers}). Anything from Google Search Status API = "Confirmed".
- impact/priority: P0 (check now) | P1 (act this week) | P2 (worth testing) | \
P3 (awareness only). Separate OBSERVATION from ACTION: unconfirmed flux is rarely P0. \
P2 is NOT a dumping ground for weak items — it is the "run an experiment" tier and \
carries real work. Anything you cannot justify as testable or actionable is P3.
Keep every field to <= {max_chars} characters — headline density, not paragraphs.
The ONE exception is `evidence_detail`, which is deliberately longer: it is hidden \
behind a click, so it costs the scanner nothing and rewards the reader who wants \
to check your work. Aim for 2-4 sentences there.

STEP 4 - Sort each signal into exactly one section:
{sections}
Rules: Google-confirmed updates -> "Confirmed Search Updates". Explanations of \
search mechanics by Google staff (source_type official_person) -> "Official \
Explanations". Information-retrieval / entity / knowledge-graph / patent / paper \
analysis -> "Research & Retrieval". Unconfirmed flux/rumor -> "Unconfirmed \
Watchlist" (advise monitor, not edit). Cross-cutting to-dos summarised in \
"Today's Action Items" tagged P0/P1/P2.
News sections may be EMPTY. "Official Explanations" and "Research & Retrieval" are \
slower-moving than news — leave them out entirely rather than padding them with \
weak items.

{playbook_block}

{recent_block}

OUTPUT LANGUAGE: {lang}

Return ONLY valid JSON (no markdown fences), schema:
{{
  "headline": "one line: the single most important development today (<={max_chars} chars)",
  "signals": [
    {{
      "what_happened": "<={max_chars} chars, headline-style",
      "why_it_matters": "why an expert should care",
      "evidence": "official doc | dataset | case | observation — be specific",
      "evidence_detail": "2-4 sentences expanding the evidence for a reader who \
clicked to see more: methodology, sample size or scope, the actual numbers, and \
the main caveat or limitation. STRICTLY grounded in the candidate text — if the \
source does not state a sample size or a number, say what it does state and note \
what is missing. NEVER invent figures, p-values or study designs. Omit this field \
entirely when the candidate is a one-line announcement with nothing to expand.",
      "who_it_affects": "one or more of: {verticals}",
      "what_to_do": "check X | test Y | monitor Z | no action",
      "confidence": "Confirmed | Data-backed | Observed | Speculative",
      "impact": "P0 | P1 | P2 | P3",
      "section": "<one of the sections>",
      "score": <int 0-100>,
      "how_to_test": "PLAYBOOK ITEMS ONLY: the concrete experiment or check to run",
      "success_metric": "PLAYBOOK ITEMS ONLY: which number moves if it worked",
      "effort": "PLAYBOOK ITEMS ONLY: rough cost + main risk",
      "sources": [{{"name": "author or feed", "url": "..."}}]
    }}
  ],
  "action_items": [{{"text": "...", "impact": "P0|P1|P2"}}],
  "dropped_count": <int>
}}"""


PLAYBOOK_BLOCK = """STEP 5 - THE PLAYBOOK SECTION ("{section}") — treat this as a \
standing obligation, not an optional extra. Fill it with {min_items}-{max_items} \
items the reader can go TEST, drawn from anywhere in the candidate pool \
(experiments, data research, official docs, deep technical analysis). Unlike the \
news sections, this one should almost never be empty: if nothing from today \
qualifies, use the strongest evergreen candidate available.
Every playbook item MUST supply: {requirements}. If a candidate cannot support all \
of those, it is not a playbook item — put it in a news section or drop it.
Playbook items are normally P2 ("worth testing"); use P1 only when there is a real \
deadline or an active risk. Do NOT invent mechanisms, numbers or test procedures \
that the source does not support — an honest "test this on 20 URLs and compare \
impressions" beats a fabricated case study."""


RECENT_BLOCK = """ALREADY PUBLISHED — do NOT report these again. The reader saw \
them in the last {days} days:
{stories}
Rules: if a candidate is the same STORY as one of the above, skip it and spend \
the slot on something the reader has not seen. The ONE exception is an \
ESCALATION — if a story we ran as a rumour is now officially confirmed, or new \
data settles it, that IS news: report it and say explicitly what changed \
("previously observed, now confirmed by X"). Do not repeat a story merely \
because more people are discussing it."""


def _recent_block(cfg: dict) -> str:
    days = cfg.get("daily", {}).get("dedupe_lookback_days", 0)
    if not days:
        return ""
    try:
        prior = recent_stories(cfg, days)
    except Exception:
        return ""
    if not prior:
        return ""
    lines = "\n".join(
        f"- [{p['date']} · {p['confidence'] or 'n/a'}] {p['what_happened']}"
        for p in prior[-40:]
    )
    return RECENT_BLOCK.format(days=days, stories=lines)


def _playbook_block(daily: dict) -> str:
    pb = daily.get("playbook")
    if not pb:
        return ""
    return PLAYBOOK_BLOCK.format(
        section=pb.get("section", "Playbook: Worth Testing"),
        min_items=pb.get("min_items", 1),
        max_items=pb.get("max_items", 3),
        requirements=", ".join(pb.get("requirements", [])),
    )


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
        playbook_block=_playbook_block(daily),
        recent_block=_recent_block(cfg),
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
    final JSON. This finds the largest valid JSON object in the text.
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
    # Balance braces from each "{" and keep the LARGEST object that parses.
    #
    # Scanning backwards and returning the first hit looks cheaper but is wrong:
    # the last balanced object in a chain-of-thought response is usually a small
    # nested fragment (the final `{"name":..., "url":...}` of the real payload,
    # or an example brace the model wrote while thinking). That parses fine, so
    # the function returned a *valid but tiny* object and the caller silently
    # got a digest with no signals. Length is the right tie-breaker — the real
    # payload is always the outermost, hence longest, valid object.
    best = ""
    for start, ch in enumerate(text):
        if ch != "{":
            continue
        depth = 0
        for end in range(start, len(text)):
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:end + 1]
                    if len(candidate) > len(best):
                        try:
                            json.loads(candidate)
                            best = candidate
                        except json.JSONDecodeError:
                            pass
                    break
    if best:
        return best
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

    Two things this must get right:

    1. The cap is `max_candidates`, NOT `max_signals`. Those are different
       numbers doing different jobs: max_candidates is how much the model
       READS, max_signals is how much it KEEPS. Using max_signals for both
       (the original bug) meant the model saw 8 items and was asked to keep
       5-8 of them — so it kept nearly everything, the trust ladder never
       discriminated, and most configured sources never reached the prompt
       at all.

    2. Naively taking items[:N] biases toward whichever source ran first
       (often one noisy handle flooding the window). So we round-robin
       across groups, and within each group we take the highest-trust items
       first, so a wide pool doesn't just mean a noisier one.
    """
    daily = cfg.get("daily", {})
    max_candidates = daily.get("max_candidates") or (daily.get("max_signals", 8) * 8)
    # Bucket by group, preserving first-seen group order.
    buckets: dict[str, List[Item]] = {}
    for it in items:
        buckets.setdefault(it.group, []).append(it)
    # Within a group, best-evidence first: trust weight, then recency.
    for g in buckets:
        buckets[g].sort(
            key=lambda it: (it.weight, it.published.timestamp() if it.published else 0),
            reverse=True,
        )
    # Round-robin: one from each non-empty bucket in turn, so every source
    # type is represented before any source gets a second slot.
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


def _norm_url(u: str) -> str:
    """Normalise a URL for comparison.

    Models often echo a URL back with a trailing slash, an added utm_*, or a
    different scheme. Those are the same source, so we compare on
    host+path only, lowercased.
    """
    if not u:
        return ""
    from urllib.parse import urlsplit
    try:
        p = urlsplit(u.strip())
    except Exception:
        return u.strip().lower()
    host = (p.netloc or "").lower().removeprefix("www.")
    path = (p.path or "").rstrip("/").lower()
    return f"{host}{path}"


EXCERPT_CHARS = 700


def _source_detail(it: Item) -> dict:
    """The verbatim, non-fabricable half of the evidence expansion.

    Everything here is copied from the fetched item, never generated: the
    author's own words, who wrote them, when, and which rung of the trust
    ladder they sit on. The reader can therefore judge the claim rather than
    take the model's word for it.

    Capped at EXCERPT_CHARS because a long RSS body would otherwise dominate
    the archive (and the embedded dashboard payload) for little gain — the
    "view original" link is right there for the full text. Cut on a word
    boundary so the quote doesn't end mid-token.
    """
    text = " ".join((it.text or "").split())
    if len(text) > EXCERPT_CHARS:
        cut = text[:EXCERPT_CHARS]
        sp = cut.rfind(" ")
        text = (cut[:sp] if sp > EXCERPT_CHARS * 0.6 else cut).rstrip() + "…"
    d = {"excerpt": text}
    if it.author:
        d["author"] = it.author
    if it.published:
        d["published"] = it.published.isoformat()
    if it.source_type:
        d["source_type"] = it.source_type
    # Surfacing this matters: "sells a tool in this space" is context the
    # reader needs when weighing a vendor's own benchmark.
    if it.commercial_interest:
        d["commercial_interest"] = True
    return d


def verify_sources(data: dict, candidates: List[Item]) -> dict:
    """Drop or flag any source URL the model did not actually receive.

    THIS IS A CORRECTNESS GUARD, NOT A STYLE CHECK. The product's entire value
    proposition is "we show you the evidence". An LLM asked for a JSON field
    called "url" will happily produce a plausible-looking one — a fabricated
    SearchPilot link is indistinguishable from a real one at a glance, and the
    reader would make decisions on it. So: the candidate pool is the ONLY
    source of truth for URLs. Anything else is removed.

    A signal that loses every source is kept but marked `unverified: true` and
    capped at "Speculative" confidence — better a visible caveat than a silent
    deletion, since the underlying observation may still be real.

    Verified sources also gain an `excerpt` (plus author/published/source_type)
    taken from the matched candidate. This is the ONLY quotable detail in the
    system that cannot be fabricated: it is the original author's words, copied
    from the item we fetched. It costs no extra tokens and it is what the
    "expand for detail" panel shows first.
    """
    allowed = {}
    for it in candidates:
        key = _norm_url(it.url)
        if key:
            allowed[key] = it
    fabricated, stripped = [], 0
    for sig in data.get("signals", []):
        kept = []
        for src in sig.get("sources", []) or []:
            key = _norm_url(src.get("url", ""))
            if key and key in allowed:
                it = allowed[key]
                # Trust the candidate's own name over the model's paraphrase.
                src["name"] = src.get("name") or it.source_name
                src.update(_source_detail(it))
                kept.append(src)
            else:
                fabricated.append(src.get("url", "") or "(empty)")
                stripped += 1
        sig["sources"] = kept
        if not kept:
            sig["unverified"] = True
            # Never let an unsourced claim keep a high-trust label.
            if sig.get("confidence") in ("Confirmed", "Data-backed"):
                sig["confidence"] = "Speculative"
    if fabricated:
        print(f"[!] source check: removed {stripped} URL(s) not present in the "
              f"candidate pool (possible fabrication): {fabricated[:5]}")
    return data


_CONF_RANK = {"Speculative": 0, "Observed": 1, "Data-backed": 2, "Confirmed": 3}


def _stem(w: str) -> str:
    """Crude suffix stripping so "confirms" and "confirmed" collapse together.

    Not linguistics — just enough that a reworded headline about the same story
    still overlaps. Without it, "Google confirms August core update" vs "August
    core update confirmed by Google" scored 0.57 and slipped past dedup, which
    is exactly the repeat a reader would complain about.
    """
    if len(w) < 4:
        return w
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            break
    return w[:-1] if w.endswith("e") and len(w) > 3 else w


def _tokens(text: str) -> set:
    """Bag of comparison tokens that works for mixed EN/ZH headlines.

    Latin words are stemmed and tokenised on word boundaries; CJK has no spaces,
    so each Han character becomes its own token. Crude, but it makes "Google 确认
    核心更新" and "核心更新已确认 by Google" overlap heavily, which is the point.
    """
    import re
    low = text.lower()
    words = {_stem(w) for w in re.findall(r"[a-z0-9]{3,}", low)}
    han = {c for c in low if "一" <= c <= "鿿"}
    return words | han


def _similar(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def recent_stories(cfg: dict, days: int) -> List[dict]:
    """what_happened + confidence for every signal in the last N archived days."""
    from datetime import datetime, timedelta, timezone
    from . import store
    end = datetime.now(timezone.utc)
    out = []
    for rec in store.load_archive_range(cfg, end - timedelta(days=days), end):
        for sig in rec.get("signals", []):
            wh = sig.get("what_happened")
            if wh:
                out.append({"date": rec.get("date", ""), "what_happened": wh,
                            "confidence": sig.get("confidence", "")})
    return out


def dedupe_against_recent(cfg: dict, data: dict) -> dict:
    """Drop signals that already ran in the last few days.

    Why this is needed: dedup elsewhere is per-ITEM (`store.filter_unseen` keys
    on item id), so a story that five accounts discuss over three days produces
    brand-new items every day and quietly occupies a signal slot all week. The
    reader experiences that as "this digest keeps telling me the same thing".

    The exception that matters: an ESCALATION is news. If we ran a rumour as
    "Observed" on Monday and Google confirms it on Wednesday, the Wednesday
    signal must survive — that confirmation is the single most valuable thing
    the product can deliver. So a repeat is only dropped when its confidence
    is no higher than the version we already published.
    """
    daily = cfg.get("daily", {})
    days = daily.get("dedupe_lookback_days", 0)
    if not days:
        return data
    threshold = daily.get("dedupe_similarity", 0.65)
    prior = recent_stories(cfg, days)
    if not prior:
        return data
    kept, dropped = [], []
    for sig in data.get("signals", []):
        wh = sig.get("what_happened", "")
        now_rank = _CONF_RANK.get(sig.get("confidence", ""), 0)
        repeat = None
        for p in prior:
            if _similar(wh, p["what_happened"]) >= threshold:
                if now_rank > _CONF_RANK.get(p["confidence"], 0):
                    continue  # escalation — keep it, and say so
                repeat = p
                break
        if repeat:
            dropped.append((wh, repeat["date"]))
        else:
            kept.append(sig)
    if dropped:
        data["signals"] = kept
        data["dropped_count"] = data.get("dropped_count", 0) + len(dropped)
        for wh, d in dropped:
            print(f"[*] cross-day dedup: dropped '{wh[:60]}' (ran {d})")
    return data


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

    # Verify every citation against what the model was actually shown, BEFORE
    # the signals are grouped, archived and rendered.
    data = verify_sources(data, candidates)
    llm_kept = len(data.get("signals") or [])
    data = dedupe_against_recent(cfg, data)
    # Did cross-day dedup, rather than the LLM, empty the digest? The two cases
    # need opposite handling and conflating them broke dedup entirely (see the
    # fallback branch below).
    emptied_by_dedup = llm_kept > 0 and not data.get("signals")

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
    #
    # But NOT when cross-day dedup is what emptied it. The fallback rebuilds
    # signals straight from the raw items, which resurrects the very stories
    # dedup just removed — and strips them of confidence and impact on the way
    # through. A genuinely quiet day (everything already reported) must be
    # allowed to render as quiet; that is the honest output and it's also the
    # signal the reader needs: nothing new happened.
    if not data.get("signals") and not emptied_by_dedup:
        print(f"[!] LLM kept 0 signals (dropped {data.get('dropped_count', '?')}); "
              f"falling back to raw items.")
        return _fallback_digest(items)
    if emptied_by_dedup:
        print("[*] every signal today was already reported in the last "
              "few days — publishing a quiet day rather than repeating them.")
        data["headline"] = data.get("headline") or (
            "No new developments — everything on the radar today was already "
            "covered this week.")
        data["quiet_day"] = True
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
    # Same diverse selection, but capped at a readable handful — this is a
    # degraded mode, not the wide curation pool.
    pick = _select_candidates({"daily": {"max_candidates": 8}}, items)
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
