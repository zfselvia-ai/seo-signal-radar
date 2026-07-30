# SEO Signal Radar

Not a news aggregator — a **signal radar** for advanced SEOs:

> **X discovers anomalies → official data confirms → cases & data decide whether it's worth acting.**

It pulls from 29 curated X accounts, 11 SEO/official feeds (RSS), and the **official Google
Search Status API**, then an LLM reads up to **70 candidates** and keeps only the 5–8 that
matter, filing them into fixed sections with a **priority (P0–P3)**, a
**confidence tier**, and a concrete "what to do."

It delivers **two kinds of value**, because most days Google confirms nothing and a
pure news feed is empty on those days:

1. **News** — something changed and you may need to react.
2. **Playbook: Worth Testing** — evergreen, testable tactics with a stated mechanism,
   a test procedure, a success metric and an effort/risk note. These do *not* have to
   be new, in the spirit of a good Ahrefs or SearchPilot post. Scoring reflects this:
   **actionability (20) outranks novelty (10)** — being new is not a reason to act.
   Slow-moving, high-trust sources (controlled experiments, official docs, Google
   staff) get a 7-day window instead of 24h, so a quiet news day still yields
   something to go try.

Daily signals are archived as
structured JSON that feeds three things: a **Weekly** and **Monthly** rollup,
event-driven **Special Briefs**, and a self-contained **web dashboard** (the
single source of truth). A compressed **push notification** links back to it.

This is a **universal** product: signals are tagged by audience vertical
(ecommerce / publisher / SaaS / UGC / local / international / all), never tied to
any one site's own data.

```
Google Status API ─┐
29 X accounts ─────┼─► fetch ─► dedup ─► score+curate (LLM) ─► 10 sections ─► daily MD/HTML
RSS feeds ─────────┘                                    │
SERP volatility ───► percentile + 4-quadrant ───────────┘
                                                         ▼
                       data/archive/*.json ─► Weekly (Tue) ─► Monthly (4th working day)
                                           ├─► Special Brief (event-driven)
                                           ├─► dashboard/index.html (Today / Algorithm Map / Library)
                                           └─► compressed push (webhook)
```

## Why each report exists (they never copy each other)

| Report | Question | Job |
|---|---|---|
| **Daily** | What happened today? | Signals & alerts. Do I touch the site or not? |
| **Weekly** (`Search Intelligence Weekly`, Tue) | What truly mattered this week? | **Reduce noise** → 3 core judgments + experiments + next-week actions |
| **Monthly** (`State of Search Monthly`, 4th working day) | What structural trends emerged? | **Reduce misjudgment** → 3–5 narratives + strategy & allocation |
| **Special Brief** (event-driven) | What does this major event mean? | Monitoring alert now; full analysis only after update ends + 7 days |

A signal escalates from daily → weekly → monthly **only if it keeps producing
evidence.** One-offs are allowed to die.

## The three-track Algorithm Map

The dashboard's signature view is a timeline with three tracks (7D / 30D / 90D / 1Y):

1. **Official** — Google Search Status API (`incidents.json`, filtered by `affected_products[].id`; `service_key` is deprecated). Drives "Confirmed Search Updates" and Special-Brief triggers.
2. **External flux** — SERP volatility: Global Heat + Vertical Heat per day (see below).
3. **Community** — high-confidence expert signals from the SERP/Algorithm section.

(There is deliberately **no own-site track** — this is a universal product.
Business events like migrations or launches belong in your own change log.)

## The daily signal schema

Each signal answers: **what happened · why it matters · evidence (official doc / dataset / case / observation) · who it affects (ecommerce/publisher/SaaS/UGC/local/international/all) · what to do (check/test/monitor/none) · confidence (Confirmed / Data-backed / Observed / Speculative) · priority (P0 check now / P1 act this week / P2 worth testing / P3 awareness only)**. Fields are capped at 120 chars — headline density, not paragraphs.

Scoring (tunable in `config.yaml`): 30% source authority + 25% data & evidence + 20% novelty + 15% potential impact + 10% actionability, minus penalties for promo and duplication.

## SERP Volatility & Consensus Layer

Never averages raw values (apples + thermometers + kangaroos). It stores raw
readings for provenance and decides on the **180-day percentile** of each source
independently, then reads two axes as a four-quadrant diagnosis:

| Global Heat | Vertical Heat | Reading |
|---|---|---|
| High | High | Broad update likely, **specific verticals hit hardest** |
| High | Low | Market-wide flux, no single vertical standing out |
| Low | High | Vertical/regional event, not a broad update |
| Low | Low | Normal noise, no action |

**Vertical Heat** tracks generic industry verticals (ecommerce / publisher / SaaS /
local / international), not any one site's keyword universe. Data sources are
pluggable; default is **manual** — record readings and the percentile math
activates once you have ~10+ days of history:

```bash
python main.py serp-add --source sistrix --value 6.2 --country US --device mobile
python main.py serp-add --source awr --value 72 --vertical ecommerce
```

AWR has an official API/MCP and is the natural first real integration; SISTRIX
Radar and Algoroo lack confirmed public endpoints, so they start as manual /
watchlist inputs (verify licensing before automating).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env      # add LLM key; add X creds for first login
```

Pick the LLM provider in `config.yaml` (`llm.provider`) and set the matching key.
Default is **Kimi K2.5** (`moonshot`) — set `MOONSHOT_API_KEY`; `anthropic` and
`openai` are drop-in alternatives.
First X run uses `X_USERNAME/X_EMAIL/X_PASSWORD` then caches `cookies.json`.

> ⚠️ **twikit drives a real X account** via private endpoints. Use a secondary
> account, keep `tweets_per_handle` modest, and run a few times a day at most.
> To eliminate the risk entirely, swap `x_source.py` for a paid API — the `Item`
> interface stays the same.

## Run

```bash
python main.py daily            # today's radar (+ archive, dashboard rebuild, push)
python main.py daily --dry-run  # fetch + dedup only, no LLM cost
python main.py weekly           # Search Intelligence Weekly
python main.py monthly          # State of Search Monthly (only runs on 4th working day; --force to override)
python main.py brief            # generate any pending Special Briefs
python main.py dashboard        # rebuild dashboard/index.html from the archive
python main.py notify           # (re)send the compressed push for the latest day
```

## Dashboard & notification (two ends, one library)

The **dashboard** (`dashboard/index.html`) is the full product and single source
of truth: one self-contained HTML file, no build step, no external calls — the
archive is embedded as JSON. Three views:

- **Today** — Morning Brief status bar, headline, SERP weather, the 10 sections, action items.
- **Algorithm Map** — the three-track timeline (Official / External flux / Community) across 7D/30D/90D/1Y.
- **Source Library** — every archived signal, searchable and filterable by section, confidence, priority, and vertical.

`dashboard/index.html` is a **build artifact and is not committed** (it's gitignored).
It embeds the whole archive inline, so it's rewritten in full on every run —
committing it stored a fresh multi-MB blob daily and made `.git` grow *quadratically*
(~136 MB after a year, ~1.2 GB after three). CI publishes it straight to the
`gh-pages` branch with `force_orphan` instead. Regenerate it any time with
`python main.py dashboard`.

**Storage, measured:** the archive is ~12 KB/day — about **4.5 MB/year**, 22 MB after
five years. That is comfortably inside GitHub's limits, so no external database or
cloud hosting is needed; GitHub Actions plus Pages is sufficient indefinitely. The
only real ceiling is page weight: past roughly 1,000 days of embedded history the
single-file dashboard gets heavy on mobile, at which point split the Library into a
separate lazy-loaded JSON rather than raising `dashboard.history_days`.

The **notification** is only a cover: Morning Brief + top 3 signals + confirmed-update
flag + up to 2 actions + a link back. Set the webhook URL in the environment
(kept out of `config.yaml`) and pick a channel format in `notify.channel`:

```bash
export NOTIFY_WEBHOOK_URL="https://hooks.slack.com/…"   # slack|discord|telegram|feishu|plain
```

If no URL is set, `python main.py notify` just prints the preview.

## Automation

- `.github/workflows/digest.yml` — daily at 07:00 Asia/Shanghai; runs the digest, rebuilds the dashboard, sends the push, commits, and publishes `dashboard/` to GitHub Pages.
- `.github/workflows/reports.yml` — weekly (Tue) + monthly (code verifies the 4th working day).

Repo secrets: ONE LLM key (`MOONSHOT_API_KEY`, `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`
— the provider is auto-detected) and optional `NOTIFY_WEBHOOK_URL` for the push.
X needs **no credentials at all**: it reads public Nitter RSS. State (`data/`) is cached between runs
so items never repeat and the archive survives for weekly/monthly rollups.

## Layout

```
config.yaml              # everything: lists, scoring, sections, SERP rules, cadence, rubric, dashboard, notify
main.py                  # orchestrator (daily/weekly/monthly/brief/dashboard/notify/serp-add)
seodigest/
  models.py              # Item + Signal
  x_source.py            # Nitter RSS — 29 tagged accounts, each fetched once
  rss_source.py          # feedparser
  google_status.py       # official confirmation layer + brief triggers
  serp_layer.py          # percentile math, four-quadrant, event detection (Global + Vertical Heat)
  summarize.py           # daily scoring + curation + 7-field/7-section LLM step
  reports.py             # weekly / monthly / special-brief synthesis
  render.py              # MD + HTML for all report kinds
  store.py               # dedup, structured archive, SERP history DB
  dashboard.py           # builds the single-file static dashboard from the archive
  dashboard_template.html# the dashboard shell (data injected at build time)
  notify.py              # compressed push + per-channel webhook formats
reports/{daily,weekly,monthly,special}/
data/archive/            # structured daily JSON (weekly/monthly/dashboard read from here)
dashboard/index.html     # the deployed product
.github/workflows/
```

## Notes & limits

- **X is the fragile part** (twikit → private endpoints). If challenged, delete `cookies.json` and re-login.
- **Percentiles need history.** Until ~10+ days of SERP readings accrue, the layer records data and reports "insufficient history" rather than guessing.
- **GEO open-source tools** (e.g. `geo-aeo-tracker`, `awesome-generative-engine-optimization`) are treated as *candidate sources* for the "Tools, Papers & Open Source" section, not integrated runtimes — self-hosting them still needs paid API keys (Bright Data / OpenRouter / Gemini).
- LLM cost is roughly one call per daily run plus one per weekly/monthly — cents.
