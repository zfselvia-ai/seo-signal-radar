# SEO Signal Radar

Not a news aggregator — a **signal radar** for advanced SEOs:

> **X discovers anomalies → official data confirms → cases & data decide whether it's worth acting.**

It pulls from 21 curated X accounts, 9 Bluesky accounts (John Mueller, Gary Illyes and
Danny Sullivan, who left X; two practitioners; and Bluesky mirrors of four X accounts so
they still arrive when Nitter is down),
22 SEO/official feeds (RSS — including the newsletters and blogs of people who mostly
post on LinkedIn), the **official Google Search Status API**, and a **manual inbox** for
things you read somewhere without a feed. Then a curation step — an LLM when you have a
key, a rule-based ranker plus hand curation when you don't (see *Running without an
LLM*) — reads up to **70 candidates** and keeps only the 5–10 that matter, filing them
into fixed sections with a **priority (P0–P3)**, a **confidence tier**, and a concrete
"what to do."

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
21 X accounts ─────┤
9 Bluesky accounts ┼─► fetch ─► dedup ─► score+curate (LLM | rules + hand) ─► 10 sections ─► daily MD/HTML
22 RSS feeds ──────┤                                    │
manual inbox ──────┘                                    │
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

**Every card opens.** One line is enough to triage but not to act on, so each
signal carries an expandable panel with two halves. First a **verbatim excerpt**
(700 chars) copied straight from the fetched page or tweet, with its author, date
and trust tier — this is the only thing on the card that *cannot* have been
fabricated, so you check our summary rather than trusting it. Second an
LLM-written **detail** (2–4 sentences: methodology, sample size, actual numbers,
main caveat), the one field exempt from the 120-char cap because it sits behind a
click and costs a scanner nothing. It's a native `<details>` element, closed by
default, keyboard-accessible, and it renders nothing at all when a signal has
neither — an unverified claim has no quotable source and must not open onto an
empty box.

Scoring (tunable in `config.yaml`): 25% source authority + 25% data & evidence +
20% actionability + 20% potential impact + 10% novelty, minus penalties for promo
and duplication. Novelty is deliberately the smallest weight — it is a tiebreaker,
not a reason to act.

Above the score sits a **trust ladder** (`scoring.source_weights`): official API
and documentation 1.00, a Google engineer explaining mechanics 0.90, a
reproducible experiment 0.90, data research 0.85, independent analysis 0.70, an
expert who also sells a tool 0.65, trade news 0.55, an unverified screenshot 0.20.
Accounts flagged `commercial_interest` have their *opinions* capped at "Observed"
— only data, code or a paper lifts them higher. An "experiment" only earns 0.90
if it shows methodology, sample/scope and a measured outcome.

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

Runs entirely on your own machine. There is no CI, no hosting and no deploy step.

```bash
pip install -r requirements.txt
cp .env.example .env      # paste ONE LLM API key
```

Pick the LLM provider in `config.yaml` (`llm.provider: auto` detects whichever
key you set). `scripts/setup_local.sh` does all of the above and runs one real
digest so you can eyeball the output.

**X needs no credentials at all.** `x_source.py` reads public Nitter RSS, so
there is no account to log in, no cookie to refresh, and no password anywhere.
The only secret the pipeline requires is one LLM API key.

## Run

```bash
python main.py daily            # today's radar (+ archive, dashboard rebuild, push)
python main.py daily --dry-run  # fetch + dedup only, no LLM cost
python main.py doctor           # probe every feed + Nitter instance; no LLM key needed
python main.py capture --url … --text "…" --author "Name (LinkedIn)"   # paste something you read into the pool
python main.py weekly           # Search Intelligence Weekly
python main.py monthly          # State of Search Monthly (only runs on 4th working day; --force to override)
python main.py brief            # generate any pending Special Briefs
python main.py dashboard        # rebuild dashboard/index.html from the archive
python main.py notify           # (re)send the compressed push for the latest day
```

(`python` here means the interpreter that has the dependencies — if you used
`python3 -m venv .venv`, that is `.venv/bin/python`. `doctor` tells you when
you've picked the wrong one.)

### LinkedIn, and other places without a feed

LinkedIn has no public feed, its API does not expose other people's posts, and
scraping it breaks the terms of service and the login wall — the same trap X
turned into. So LinkedIn is covered two honest ways:

1. **Follow the long-form outlet instead.** Nearly everyone worth reading there
   publishes the full analysis in a newsletter or blog that *does* have a feed
   (Growth Memo, Lily Ray's Substack, Zyppy Signal, GSQi, DEJAN, Jono Alderson,
   Marie Haynes, Suganthan, Detailed, Moz, SearchPilot). These are under
   `rss.feeds`, each probed before it was added.
2. **Capture it yourself.** When you read a post worth keeping, run
   `python main.py capture --url <post url> --text "<the passage you read>"`
   with `--author` and optionally `--type data_research` (any rung of
   `scoring.source_weights`). It lands in `data/inbox/captures.jsonl` and is
   offered to the next `daily` run as a normal candidate — your pasted text
   becomes the verbatim excerpt on the card, and it is deduped by URL after
   that. Default trust is `practitioner_observation` (0.60); say otherwise if
   it's data or an official statement.

Bluesky, by contrast, publishes a public RSS feed per account
(`bsky.app/profile/<handle>/rss`), which is how John Mueller, Gary Illyes and
Danny Sullivan are followed now that their X accounts are dormant.

## Running without an LLM

Set `llm.provider: "none"` in `config.yaml` (it is, as of 2026-10) and `daily`
still fetches, dedups and publishes — but what it publishes is the **top 10 raw
items by a rule-based ranker** (trust weight + SEO keyword hits, minus promo;
plain retweets, reply chatter and off-topic posts dropped; two per source;
near-duplicates collapsed). Those cards are honest about what they are: marked
`uncurated`, `P3` and `Speculative` as placeholders, with the original text
expandable underneath. Nobody has judged them.

Real curation then happens by hand, through two scripts that keep the same
guarantees the LLM path has:

```bash
python scripts/dump_candidates.py --since 2026-10-09 --out /tmp/pool   # pool.json + pool.md, no state touched
# …read pool.md, write curated.json in the schema summarize.SYSTEM asks the model for…
python scripts/ingest_curated.py --curated curated.json --pool /tmp/pool.json --date 2026-10-09
```

`ingest_curated.py` runs the hand-written digest through exactly the post-LLM
pipeline: **every cited URL must exist in the pool** (anything else is stripped
and the signal marked `unverified`, same as a fabricated model citation),
cross-day story dedup, sectioning, archive, MD/HTML, dashboard rebuild. Records
written this way carry `curated_by` and, if back-dated, `backfilled: true`, so a
hand-filled gap is distinguishable from a day the pipeline actually ran. When a
valid key is back, set `provider` to `auto` and the LLM path resumes unchanged.

Nothing is scheduled for you. Run `daily` when you want it, or wire it to `cron`
/ `launchd` yourself:

```cron
0 9 * * *  cd /path/to/SEO看板 && /usr/bin/python3 main.py daily >> radar.log 2>&1
```

### When the digest looks thin: `python main.py doctor`

Every failure in this pipeline is **silent**. `feedparser` does not raise on a
403, and a rate-limited Nitter instance returns the same nothing as an account
that simply didn't post — so a broken source and a quiet news day produce an
identical short digest. That is worth knowing about, because you would otherwise
trust a half-dead digest for weeks.

`doctor` probes every RSS feed and every Nitter instance with the *same headers
the real fetch uses*, and prints the actual status code, entry count and timing:

```
=== RSS · 11 feeds ===
  ok    Google Search Central Blog       20 entries      412ms · newest 2026-08-04
  FAIL  Search Engine Roundtable         HTTP 403        280ms
        https://www.seroundtable.com/index.rss

=== Probing alternates for 1 broken feed(s) ===
  Search Engine Roundtable
    WORKS  https://www.seroundtable.com/feed  (30 entries)
```

It writes nothing and needs no API key, so it is safe to re-run while fixing
URLs. Any `WORKS` line is a drop-in replacement for `config.yaml`. If every
Nitter instance is down, it says so and points at
[status.d420.de](https://status.d420.de/) for a current instance list — put
working hosts under `x.nitter_instances`, which are tried *before* the built-in
defaults rather than replacing them.

## Dashboard & notification (two ends, one library)

The **dashboard** (`dashboard/index.html`) is the full product and single source
of truth: one self-contained HTML file, no build step, no external calls — the
archive is embedded as JSON. Open it directly in a browser. Three views:

- **Today** — Morning Brief status bar, headline, SERP weather, the 10 sections, action items.
- **Algorithm Map** — the three-track timeline (Official / External flux / Community) across 7D/30D/90D/1Y.
- **Source Library** — every archived signal, searchable and filterable by section, confidence, priority, and vertical.

`dashboard/index.html` is a **build artifact and is not committed** (it's gitignored).
It embeds the whole archive inline, so it's rewritten in full on every run —
committing it stored a fresh multi-MB blob daily and made `.git` grow *quadratically*
(~136 MB after a year, ~1.2 GB after three). Regenerate it any time with
`python main.py dashboard`.

Because it's a local file there is no login to worry about, and no public URL to
protect. (An earlier version published to GitHub Pages behind an in-page password
prompt. That was security theatre: a static site has no server to check a
password, so the check ran in JavaScript and the page shipped its own hash for
anyone to read with View Source. Keeping the file local removes the problem
rather than papering over it.)

**Storage, measured:** the archive is ~12 KB/day — about **4.5 MB/year**, 22 MB after
five years. No database or cloud hosting is needed. The only real ceiling is page
weight: past roughly 1,000 days of embedded history the single-file dashboard gets
heavy on mobile, at which point split the Library into a separate lazy-loaded JSON
rather than raising `dashboard.history_days`.

The **notification** is only a cover: Morning Brief + top 3 signals + confirmed-update
flag + up to 2 actions. Set the webhook URL in the environment (kept out of
`config.yaml`) and pick a channel format in `notify.channel`:

```bash
export NOTIFY_WEBHOOK_URL="https://hooks.slack.com/…"   # slack|discord|telegram|feishu|plain
export PUSHPLUS_TOKEN="…"                               # if notify.channel is pushplus
```

If no URL is set, `python main.py notify` just prints the preview. `notify.dashboard_url`
is blank by default — a `file://` path isn't clickable from a phone, so the push
carries the signals themselves rather than a dead link.

## Layout

```
config.yaml              # everything: accounts, scoring, sections, SERP rules, cadence, rubric, dashboard, notify
main.py                  # orchestrator (daily/weekly/monthly/brief/dashboard/notify/serp-add/doctor)
seodigest/
  models.py              # Item + Signal
  x_source.py            # Nitter RSS — 21 tagged accounts, each fetched once
  rss_source.py          # RSS/Atom + Bluesky feeds, fetched with a browser UA + hard timeout
  manual_source.py       # data/inbox captures (LinkedIn etc.) → candidates
  google_status.py       # official confirmation layer + brief triggers
  serp_layer.py          # percentile math, four-quadrant, event detection (Global + Vertical Heat)
  summarize.py           # daily scoring + curation + LLM step + source-URL verification
  reports.py             # weekly / monthly / special-brief synthesis
  render.py              # MD + HTML for all report kinds
  store.py               # dedup (incl. X/Bluesky cross-posts), structured archive, run-health records, SERP history
  dashboard.py           # builds the single-file static dashboard from the archive
  dashboard_template.html# the dashboard shell (data injected at build time)
  notify.py              # compressed push + per-channel webhook formats
scripts/setup_local.sh   # one-time install + key check + first real run
scripts/dump_candidates.py   # export the candidate pool for hand curation (no state touched)
scripts/ingest_curated.py    # publish a hand-curated JSON through the normal pipeline
reports/{daily,weekly,monthly,special}/
data/archive/            # structured daily JSON (weekly/monthly/dashboard read from here)
data/inbox/              # captures.jsonl — things you pasted in with `capture`
dashboard/index.html     # the product (gitignored build artifact — open it locally)
```

## Notes & limits

- **X is the fragile part** — Nitter instances go down and rate-limit constantly. `x_source.py` tries several in order and distinguishes "no tweets" from "every instance refused", recording *which* instance refused and how (a run where every handle reports `:429` is a throttling problem, not 29 dead accounts). The health strip on the dashboard turns amber below `dashboard.source_warn_ratio` and red at zero coverage. Run `doctor` when it does.
- **Four X accounts have a Bluesky mirror, and one post is shown once.** Glenn Gabe, Aleyda Solis, Lily Ray and Barry Schwartz are also followed on Bluesky (`same_as` in `rss.feeds`), so they keep arriving when every Nitter instance is down. Most of their posts go to both platforms, under different ids, so `store.collapse_crossposts` keeps one copy (the X one when both are new). That holds across runs too, via short-lived fingerprints in `seen.json`. The thresholds were calibrated on real pairs: genuine cross-posts land within 1.3h of each other and share at least half the words of the shorter post, while different posts made within 3h shared at most 0.29. The raw ranker's two-per-source cap counts one person across both channels. The official X API (pay per use, $0.005 per post read) was evaluated in Oct 2026 and not adopted.
- **Feed timestamps are UTC: parse them with `calendar.timegm`, never `time.mktime`.** Until 2026-10-13 both fetchers used `mktime`, which reads feedparser's UTC tuples as *local* time. On this UTC+8 machine every post was dated 8h early, so cards showed the wrong day, and the first 8h of each run's window silently fell outside it — a third of a 24h window. Records made by the old CI runner (UTC) were unaffected, and the hand-curated records were corrected and re-checked against tweet-ID timestamps.
- **Fetching is done with a browser User-Agent, deliberately.** Cloudflare-fronted publishers 403 a library-shaped UA, and `feedparser` returns an empty feed instead of raising — which is why four feeds failed silently for nine days while looking exactly like "no new articles". A 200 that parses to nothing is also treated as a failure, since a renamed blog usually serves an HTML error page with a 200.
- **Sources are verified, not trusted.** Every URL the LLM cites is checked against the candidate pool it was actually shown. Anything invented is stripped, the signal is marked `unverified`, and its confidence is capped at Speculative.
- **Repeats are filtered across days, escalations are not.** Item-level dedup keys on ID, so one story discussed by five accounts over three days would otherwise occupy a slot all week. Story-level dedup (Jaccard overlap, CJK compared per character) runs in two layers — the prompt is told what already ran, *and* the output is post-filtered, because the model sometimes ignores the instruction. A rumour that becomes officially confirmed always survives: that escalation is the single most valuable thing this product delivers.
- **A quiet day is allowed to look quiet.** If nothing survives, the digest says so rather than backfilling from raw items — which would resurrect exactly what dedup just dropped.
- **A reachable account is not a live one.** In Oct 2026, 8 of 29 X handles returned a valid feed on every run and contributed nothing for two months — they had stopped posting a year or more earlier (Google staff moved to Bluesky). "29/29 ok" was true and useless. Dormant handles now sit in `x.excluded_or_manual_review` with their last-post date; when a source goes quiet for months, probe it rather than trusting the green light.
- **No fabricated data, ever.** Early demo records (Jul 27–28) carried an invented "August core update" and made-up SERP heat; they were deleted on 2026-10-12 and every cited URL in the archive was re-fetched (27/27 live). Anything that enters the archive must trace to a fetched item or a pasted capture.
- **Percentiles need history.** Until ~10+ days of SERP readings accrue, the layer records data and reports "insufficient history" rather than guessing. All SERP adapters are `manual`, so the External-flux lane shows "not connected" instead of asserting calm without data.
- **GEO open-source tools** (e.g. `geo-aeo-tracker`, `awesome-generative-engine-optimization`) are treated as *candidate sources* for the "Tools, Papers & Open Source" section, not integrated runtimes — self-hosting them still needs paid API keys (Bright Data / OpenRouter / Gemini).
- LLM cost is roughly one call per daily run plus one per weekly/monthly — cents.
