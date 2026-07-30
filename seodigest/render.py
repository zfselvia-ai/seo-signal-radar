"""Render structured reports to Markdown + styled HTML.

Handles four report kinds: daily, weekly, monthly, special. Each renderer
returns a (markdown, html) tuple; write_report saves configured formats.
"""
from __future__ import annotations

import os
import html as _html


# ------------------------------ helpers -----------------------------------
CONF_BADGE = {
    "Confirmed": ("#166534", "#dcfce7"),
    "Data-backed": ("#1e40af", "#dbeafe"),
    "Observed": ("#92400e", "#fef3c7"),
    "Speculative": ("#6b7280", "#f3f4f6"),
}
IMPACT_BADGE = {
    "P0": ("#991b1b", "#fee2e2"),
    "P1": ("#9a3412", "#ffedd5"),
    "P2": ("#854d0e", "#fef9c3"),
    "P3": ("#374151", "#f3f4f6"),
}
HEAT_COLOR = {"Extreme": "🔴", "High": "🟠", "Elevated": "🟡",
              "Normal": "🟢", "Unknown": "⚪"}


def _esc(s):
    return _html.escape(str(s or ""))


def _morning_brief(digest: dict, serp: dict | None) -> dict:
    """Compress the day into a one-line status bar: signal count, top impact,
    confirmed-update flag, SERP band. Feeds both the MD/HTML header and notify."""
    sigs = [s for lst in digest.get("sections", {}).values() for s in lst
            if s.get("what_happened")]
    impacts = [s.get("impact", "") for s in sigs]
    top = next((p for p in ("P0", "P1", "P2", "P3") if p in impacts), "—")
    confirmed = bool(digest.get("sections", {}).get("Confirmed Search Updates"))
    band = (serp or {}).get("global_band", "Unknown")
    return {"signals": len(sigs), "top_impact": top,
            "confirmed": confirmed, "serp_band": band}


# ------------------------------ daily -------------------------------------
def _serp_card_md(serp: dict) -> str:
    if not serp:
        return ""
    gb = serp.get("global_band", "Unknown")
    rb = serp.get("vertical_band", serp.get("relevant_band", "Unknown"))
    vheat = serp.get("vertical_heat", serp.get("relevant_heat", "n/a"))
    lines = ["### SERP Weather", "",
             f"- **Global Heat:** {HEAT_COLOR.get(gb,'')} {gb} "
             f"({serp.get('global_heat','n/a')}/100)",
             f"- **Vertical Heat:** {HEAT_COLOR.get(rb,'')} {rb} "
             f"({vheat}/100)",
             f"- **Source agreement:** {serp.get('source_agreement','n/a')}",
             f"- **Quadrant:** {serp.get('quadrant','n/a')}"]
    anomalous = serp.get("anomalous_verticals", serp.get("anomalous_cohorts"))
    if anomalous:
        lines.append(f"- **Affected verticals:** {', '.join(anomalous)}")
    if serp.get("interpretation"):
        lines += ["", f"> {serp['interpretation']}"]
    return "\n".join(lines) + "\n"


def render_daily(digest: dict, cfg: dict, date_str: str, serp: dict | None = None):
    title = cfg["brand"]["daily_title"]
    brief = _morning_brief(digest, serp)
    md = [f"# {title}", "", f"**{date_str}**", ""]
    md += [f"> **Morning Brief** · {brief['signals']} signals · "
           f"top {brief['top_impact']} · "
           f"{'✅ confirmed update' if brief['confirmed'] else 'no confirmed update'} · "
           f"SERP {brief['serp_band']}", ""]
    if digest.get("headline"):
        md += [f"> {digest['headline']}", ""]
    if serp:
        md += [_serp_card_md(serp)]

    for section in cfg["daily"]["sections"]:
        sigs = digest.get("sections", {}).get(section)
        if not sigs:
            continue
        md += [f"## {section}", ""]
        if section == "Today's Action Items":
            for s in sigs:
                tag = s.get("impact", "")
                prefix = f"`{tag}` " if tag else ""
                md.append(f"- {prefix}{s.get('what_to_do','')}")
            md.append("")
            continue
        for s in sigs:
            conf = s.get("confidence", "")
            imp = s.get("impact", "")
            tags = " ".join(t for t in (f"`{imp}`" if imp else "",
                                        f"`{conf}`" if conf else "") if t)
            md.append(f"- **{s.get('what_happened','')}**  {tags}")
            if s.get("why_it_matters"):
                md.append(f"  - 为何重要 / Why: {s['why_it_matters']}")
            if s.get("evidence"):
                md.append(f"  - 证据 / Evidence: {s['evidence']}")
            if s.get("who_it_affects"):
                md.append(f"  - 影响 / Affects: {s['who_it_affects']}")
            if s.get("what_to_do"):
                md.append(f"  - 行动 / Action: {s['what_to_do']}")
            # Playbook items carry a test recipe; only present when relevant.
            if s.get("how_to_test"):
                md.append(f"  - 如何测试 / Test: {s['how_to_test']}")
            if s.get("success_metric"):
                md.append(f"  - 衡量 / Measure: {s['success_metric']}")
            if s.get("effort"):
                md.append(f"  - 成本与风险 / Effort: {s['effort']}")
            for src in s.get("sources", []):
                md.append(f"  - [{src.get('name','source')}]({src.get('url','')})")
        md.append("")
    if digest.get("dropped_count"):
        md += [f"*Filtered out {digest['dropped_count']} lower-value candidates.*", ""]
    md_text = "\n".join(md)
    return md_text, _daily_html(digest, cfg, date_str, serp, title, brief)


def _daily_html(digest, cfg, date_str, serp, title, brief=None):
    parts = [_HTML_HEAD.format(title=_esc(title), date=_esc(date_str)),
             f"<h1>{_esc(title)}</h1><div class='date'>{_esc(date_str)}</div>"]
    if brief:
        chips = (f"<span class='chip'>{brief['signals']} signals</span>"
                 f"<span class='chip'>top {_esc(brief['top_impact'])}</span>"
                 f"<span class='chip'>{'✅ confirmed update' if brief['confirmed'] else 'no confirmed update'}</span>"
                 f"<span class='chip'>SERP {_esc(brief['serp_band'])}</span>")
        parts.append(f"<div class='brief'>{chips}</div>")
    if digest.get("headline"):
        parts.append(f"<div class='headline'>{_esc(digest['headline'])}</div>")
    if serp:
        parts.append(_serp_card_html(serp))
    for section in cfg["daily"]["sections"]:
        sigs = digest.get("sections", {}).get(section)
        if not sigs:
            continue
        parts.append(f"<h2>{_esc(section)}</h2>")
        if section == "Today's Action Items":
            lis = []
            for s in sigs:
                imp = s.get("impact", "")
                b = ""
                if imp:
                    fg, bg = IMPACT_BADGE.get(imp, ("#333", "#eee"))
                    b = f"<span class='badge' style='color:{fg};background:{bg}'>{_esc(imp)}</span> "
                lis.append(f"<li>{b}{_esc(s.get('what_to_do',''))}</li>")
            parts.append("<ul>" + "".join(lis) + "</ul>")
            continue
        for s in sigs:
            fg, bg = CONF_BADGE.get(s.get("confidence", ""), ("#333", "#eee"))
            badge = (f"<span class='badge' style='color:{fg};background:{bg}'>"
                     f"{_esc(s.get('confidence',''))}</span>")
            imp = s.get("impact", "")
            ibadge = ""
            if imp:
                ifg, ibg = IMPACT_BADGE.get(imp, ("#333", "#eee"))
                ibadge = (f"<span class='badge' style='color:{ifg};background:{ibg}'>"
                          f"{_esc(imp)}</span>")
            parts.append("<div class='item'>")
            parts.append(f"<div class='what'>{_esc(s.get('what_happened',''))}{ibadge}{badge}</div>")
            for lab, key in (("为何重要 Why", "why_it_matters"),
                             ("证据 Evidence", "evidence"),
                             ("影响 Affects", "who_it_affects"),
                             ("行动 Action", "what_to_do"),
                             # Playbook-only fields, skipped when absent.
                             ("如何测试 Test", "how_to_test"),
                             ("衡量 Measure", "success_metric"),
                             ("成本与风险 Effort", "effort")):
                if s.get(key):
                    parts.append(f"<div class='meta'><b>{lab}:</b> {_esc(s[key])}</div>")
            for src in s.get("sources", []):
                parts.append(f"<div class='src'><a href='{_esc(src.get('url',''))}'>"
                             f"{_esc(src.get('name','source'))} ↗</a></div>")
            parts.append("</div>")
    parts.append(_HTML_FOOT)
    return "\n".join(parts)


def _serp_card_html(serp):
    gb = serp.get("global_band", "Unknown")
    rb = serp.get("vertical_band", serp.get("relevant_band", "Unknown"))
    vheat = serp.get("vertical_heat", serp.get("relevant_heat", "n/a"))
    rows = [
        ("Global Heat", f"{HEAT_COLOR.get(gb,'')} {gb} ({serp.get('global_heat','n/a')}/100)"),
        ("Vertical Heat", f"{HEAT_COLOR.get(rb,'')} {rb} ({vheat}/100)"),
        ("Source agreement", serp.get("source_agreement", "n/a")),
        ("Quadrant", serp.get("quadrant", "n/a")),
    ]
    anomalous = serp.get("anomalous_verticals", serp.get("anomalous_cohorts"))
    if anomalous:
        rows.append(("Affected verticals", ", ".join(anomalous)))
    body = "".join(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>" for k, v in rows)
    interp = (f"<div class='interp'>{_esc(serp['interpretation'])}</div>"
              if serp.get("interpretation") else "")
    return (f"<div class='serp'><h2>SERP Weather</h2>"
            f"<table class='serp-tbl'>{body}</table>{interp}</div>")


# --------------------------- weekly / monthly ------------------------------
def _kv_list(items):
    return "\n".join(f"- {i}" for i in items) if items else "- (none)"


def render_weekly(rep: dict, cfg: dict):
    title = cfg["brand"]["weekly_title"]
    hdr = f"{rep.get('period_start','')} → {rep.get('period_end','')}"
    md = [f"# {title}", "", f"**{hdr}**  ·  {rep.get('days_covered',0)} daily reports aggregated", ""]
    if rep.get("empty"):
        md += ["_No daily archive found for this week. Run the daily job first._"]
        return "\n".join(md), _generic_html(title, hdr, "\n".join(md))

    md += ["## This Week's 3 Core Judgments", ""]
    for i, j in enumerate(rep.get("core_judgments", []), 1):
        md += [f"**{i}. {j.get('judgment','')}**  `{j.get('confidence','')}`",
               f"- ✅ Support: {j.get('supporting_evidence','')}",
               f"- ⚠️ Counter: {j.get('counter_evidence','')}",
               f"- Applies to: {j.get('applies_to','')}",
               f"- Verify next week: {j.get('verify_next_week','')}", ""]

    md += ["## Algorithm & SERP Map (14–30d)", ""]
    for m in rep.get("algo_serp_map", []):
        md.append(f"- `{m.get('relation','')}` — {m.get('item','')}")
    md += ["", "## Google & Search Platforms", "", _kv_list(rep.get("google_platforms", [])),
           "", "## AI Search / GEO", "", _kv_list(rep.get("ai_search_geo", [])), ""]

    md += ["## Technical SEO Lab", ""]
    for e in rep.get("tech_seo_lab", []):
        md += [f"- **Hypothesis:** {e.get('hypothesis','')}",
               f"  - Test on: {e.get('test_on','')} · Cost: {e.get('cost','')} · "
               f"Metric: {e.get('metric','')} · Window: {e.get('observation_window','')} · "
               f"Maturity: {e.get('evidence_maturity','')}"]
    md += ["", "## Noise Cancelled", "", _kv_list(rep.get("noise_cancelled", [])),
           "", "## Next Week's Action Items", "", _kv_list(rep.get("next_week_actions", [])), ""]
    md_text = "\n".join(md)
    return md_text, _generic_html(title, hdr, md_text, as_markdown=True)


def render_monthly(rep: dict, cfg: dict):
    title = cfg["brand"]["monthly_title"]
    hdr = f"{rep.get('period_start','')} → {rep.get('period_end','')}"
    md = [f"# {title}", "", f"**{hdr}**  ·  {rep.get('days_covered',0)} daily reports aggregated", ""]
    if rep.get("empty"):
        md += ["_No daily archive found for this month. Run the daily job first._"]
        return "\n".join(md), _generic_html(title, hdr, "\n".join(md))

    es = rep.get("executive_summary", {})
    md += ["## Executive Summary", "",
           "**Top changes:**", _kv_list(es.get("top_changes", [])), "",
           "**Risks:**", _kv_list(es.get("risks", [])), "",
           "**Opportunities:**", _kv_list(es.get("opportunities", [])), "",
           f"**Stop doing:** {es.get('stop_doing','')}", ""]

    sb = rep.get("scoreboard", {})
    md += ["## Monthly Search Scoreboard", ""]
    for k, v in sb.items():
        md.append(f"- {k.replace('_',' ').title()}: **{v}**")
    md += ["", "## Structural Trends", ""]
    for i, t in enumerate(rep.get("structural_trends", []), 1):
        md += [f"**{i}. {t.get('narrative','')}**",
               f"- Evidence: {t.get('evidence','')}",
               f"- Implication: {t.get('implication','')}",
               f"- 3-month direction: {t.get('direction_3mo','')}", ""]

    md += ["## Algorithm Impact Review (90-day view)", "",
           rep.get("algorithm_impact_review", ""), "",
           "## AI Search / GEO Index", "", _kv_list(rep.get("ai_search_geo_index", [])), ""]

    wl = rep.get("winners_losers", {})
    md += ["## Winners & Losers", "",
           "**Winner traits:**", _kv_list(wl.get("winner_traits", [])), "",
           "**Loser traits:**", _kv_list(wl.get("loser_traits", [])), "",
           "## Experiment Results", ""]
    for e in rep.get("experiment_results", []):
        md.append(f"- `{e.get('status','')}` **{e.get('experiment','')}** — "
                  f"{e.get('result','')} ({e.get('confidence','')})")
    ra = rep.get("resource_allocation", {})
    md += ["", "## Next Month's Resource Allocation", "",
           "**Double down:**", _kv_list(ra.get("double_down", [])), "",
           "**Test:**", _kv_list(ra.get("test", [])), "",
           "**Stop:**", _kv_list(ra.get("stop", [])), ""]
    md_text = "\n".join(md)
    return md_text, _generic_html(title, hdr, md_text, as_markdown=True)


def render_special(rep: dict, cfg: dict):
    title = cfg["brand"]["special_title"]
    node = rep.get("node", "")
    hdr = f"{rep.get('event_title','')} · {node}"
    md = [f"# {title}", "", f"**{rep.get('event_title','')}**",
          f"_Node: {node}_", ""]
    md += ["## What we know", "", _kv_list(rep.get("what_we_know", [])), ""]
    if node == "update_alert":
        md += ["## ⛔ What NOT to do yet", "", _kv_list(rep.get("what_not_to_do_yet", [])), ""]
    md += ["## Monitoring checklist", "", _kv_list(rep.get("monitoring_checklist", [])), ""]
    if node == "post_update_analysis" and rep.get("analysis"):
        md += ["## Analysis", "", rep["analysis"], ""]
    md_text = "\n".join(md)
    return md_text, _generic_html(title, hdr, md_text, as_markdown=True)


# ------------------------------ html shell ---------------------------------
_HTML_HEAD = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — {date}</title><style>
:root {{ color-scheme: light; }}
body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC",sans-serif;
 max-width:760px;margin:0 auto;padding:40px 24px;color:#1a1a1a;background:#fff;line-height:1.65;}}
h1{{font-size:28px;margin-bottom:2px;}} .date{{color:#666;font-size:14px;margin-bottom:22px;}}
.headline{{background:#f4f6f8;border-left:4px solid #111;padding:14px 18px;border-radius:6px;
 font-size:16px;margin-bottom:24px;}}
.brief{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:18px;}}
.chip{{background:#f1f3f5;border:1px solid #e5e7eb;border-radius:20px;padding:3px 12px;
 font-size:12px;color:#374151;}}
h2{{font-size:19px;margin-top:30px;padding-bottom:6px;border-bottom:1px solid #eee;}}
.item{{margin:14px 0;}} .what{{font-weight:600;}}
.badge{{font-size:11px;padding:1px 8px;border-radius:10px;margin-left:8px;vertical-align:middle;}}
.meta{{color:#444;font-size:14px;margin:2px 0;}} .src a{{color:#2563eb;text-decoration:none;font-size:13px;}}
.serp{{background:#fafafa;border:1px solid #eee;border-radius:8px;padding:8px 18px 16px;margin-bottom:24px;}}
.serp-tbl{{width:100%;border-collapse:collapse;font-size:14px;}}
.serp-tbl td{{padding:4px 6px;border-bottom:1px solid #f0f0f0;}}
.serp-tbl td:first-child{{color:#666;width:180px;}}
.interp{{margin-top:10px;font-size:14px;color:#333;font-style:italic;}}
.footer{{color:#999;font-size:12px;margin-top:32px;}}
</style></head><body>"""
_HTML_FOOT = "</body></html>"


def _generic_html(title, hdr, md_text, as_markdown=False):
    """Lightweight HTML: render markdown-ish text in a <pre> for report kinds
    where a faithful structured HTML isn't worth the complexity."""
    body = f"<pre style='white-space:pre-wrap;font-family:inherit'>{_esc(md_text)}</pre>" \
        if as_markdown else f"<div>{_esc(md_text)}</div>"
    return (_HTML_HEAD.format(title=_esc(title), date=_esc(hdr)) +
            f"<h1>{_esc(title)}</h1><div class='date'>{_esc(hdr)}</div>" +
            body + _HTML_FOOT)


# ------------------------------ writer ------------------------------------
def write_report(cfg: dict, kind: str, date_str: str, md_text: str, html_text: str) -> dict:
    out_dir = cfg["output"]["dir"]
    sub = os.path.join(out_dir, kind)
    os.makedirs(sub, exist_ok=True)
    fname = date_str.replace(" ", "_").replace(":", "-")
    paths = {}
    if "md" in cfg["output"]["formats"]:
        p = os.path.join(sub, f"{fname}.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(md_text)
        paths["md"] = p
    if "html" in cfg["output"]["formats"]:
        p = os.path.join(sub, f"{fname}.html")
        with open(p, "w", encoding="utf-8") as f:
            f.write(html_text)
        paths["html"] = p
    return paths
