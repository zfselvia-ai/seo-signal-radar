"""Compressed push notification + generic webhook sender.

The web dashboard is the full product and single source of truth. The
notification is only a COVER: the day's Morning Brief status, the top N
signals, whether Google confirmed anything, up to M action items, and a link
back to the dashboard. It never tries to be the whole digest.

One generic webhook, several channel formats (slack | discord | telegram |
feishu | plain). The URL comes from env NOTIFY_WEBHOOK_URL so secrets stay out
of config.yaml. If no URL is set, build the payload and return it (dry) so the
caller can print/preview it without sending.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional


# --------------------------- compression ----------------------------------
def _collect_signals(digest: dict) -> list:
    out = []
    for sec, arr in (digest.get("sections") or {}).items():
        if sec == "Today's Action Items":
            continue
        for s in arr:
            if s.get("what_happened"):
                out.append(s)
    # P0 first, then by score desc.
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "": 4}
    out.sort(key=lambda s: (order.get(s.get("impact", ""), 4), -float(s.get("score", 0) or 0)))
    return out


def _brief(digest: dict, serp: Optional[dict]) -> dict:
    sigs = _collect_signals(digest)
    impacts = [s.get("impact", "") for s in sigs]
    top = next((p for p in ("P0", "P1", "P2", "P3") if p in impacts), "—")
    confirmed = bool((digest.get("sections") or {}).get("Confirmed Search Updates"))
    band = (serp or {}).get("global_band", "Unknown")
    return {"signals": len(sigs), "top_impact": top,
            "confirmed": confirmed, "serp_band": band}


def build_summary(cfg: dict, digest: dict, date_str: str,
                  serp: Optional[dict] = None) -> dict:
    """The channel-agnostic content of the push. Renderers format from this."""
    n = cfg["notify"]["top_signals"]
    m = cfg["notify"]["max_actions"]
    sigs = _collect_signals(digest)[:n]
    actions = [a for a in (digest.get("sections") or {}).get("Today's Action Items", [])][:m]
    return {
        "title": cfg["brand"]["daily_title"],
        "date": date_str,
        "headline": digest.get("headline", ""),
        "brief": _brief(digest, serp),
        "top_signals": [{
            "what_happened": s.get("what_happened", ""),
            "impact": s.get("impact", ""),
            "confidence": s.get("confidence", ""),
            "what_to_do": s.get("what_to_do", ""),
        } for s in sigs],
        "actions": [{"text": a.get("what_to_do", ""), "impact": a.get("impact", "")}
                    for a in actions],
        "dashboard_url": cfg["notify"].get("dashboard_url", ""),
    }


# --------------------------- channel formats -------------------------------
def _lines(sm: dict) -> list:
    b = sm["brief"]
    head = (f"📡 {sm['title']} · {sm['date']}\n"
            f"{b['signals']} signals · top {b['top_impact']} · "
            f"{'✅ confirmed update' if b['confirmed'] else 'no confirmed update'} · "
            f"SERP {b['serp_band']}")
    lines = [head]
    if sm.get("headline"):
        lines.append(f"\n{sm['headline']}")
    if sm["top_signals"]:
        lines.append("")
        for s in sm["top_signals"]:
            tag = f"[{s['impact']}] " if s["impact"] else ""
            conf = f" ({s['confidence']})" if s["confidence"] else ""
            lines.append(f"• {tag}{s['what_happened']}{conf}")
    if sm["actions"]:
        lines.append("\nActions:")
        for a in sm["actions"]:
            tag = f"[{a['impact']}] " if a["impact"] else ""
            lines.append(f"→ {tag}{a['text']}")
    if sm["dashboard_url"]:
        lines.append(f"\nFull digest: {sm['dashboard_url']}")
    return lines


def format_plain(sm: dict) -> str:
    return "\n".join(_lines(sm))


def _markdown(sm: dict) -> str:
    """Markdown-formatted summary for PushPlus / WeChat."""
    b = sm["brief"]
    lines = [
        f"**📡 {sm['title']} · {sm['date']}**",
        "",
        f"> {b['signals']} 条信号 · 最高优先级 **{b['top_impact']}** · "
        f"{'✅ 有确认更新' if b['confirmed'] else '无确认更新'} · SERP {b['serp_band']}",
    ]
    if sm.get("headline"):
        lines += ["", f"**{sm['headline']}**"]
    if sm["top_signals"]:
        lines += ["", "**今日信号**"]
        for s in sm["top_signals"]:
            tag = f"[{s['impact']}] " if s["impact"] else ""
            conf = f" ({s['confidence']})" if s["confidence"] else ""
            lines.append(f"- {tag}{s['what_happened']}{conf}")
    if sm["actions"]:
        lines += ["", "**行动项**"]
        for a in sm["actions"]:
            tag = f"[{a['impact']}] " if a["impact"] else ""
            lines.append(f"- {tag}{a['text']}")
    if sm.get("dashboard_url"):
        lines += ["", f"[查看完整看板]({sm['dashboard_url']})"]
    return "\n".join(lines)


def format_payload(channel: str, sm: dict) -> dict:
    """Return the JSON body appropriate for the channel's incoming webhook."""
    text = format_plain(sm)
    if channel == "slack":
        return {"text": text}
    if channel == "discord":
        return {"content": text[:1900]}
    if channel == "telegram":
        # expects a bot sendMessage webhook proxy; chat_id supplied by the proxy
        return {"text": text, "parse_mode": "None"}
    if channel == "feishu":
        return {"msg_type": "text", "content": {"text": text}}
    if channel == "pushplus":
        return {
            "token": os.getenv("PUSHPLUS_TOKEN", ""),
            "title": f"{sm['title']} · {sm['date']}",
            "content": _markdown(sm),
            "template": "markdown",
        }
    return {"text": text}


# ------------------------------ send ---------------------------------------
def send(cfg: dict, digest: dict, date_str: str,
         serp: Optional[dict] = None) -> dict:
    """Build and (if a webhook is configured) POST the compressed notification.

    Returns {sent: bool, channel, preview, status}. Never raises on network
    failure — a broken webhook must not fail the daily job.
    """
    if not cfg.get("notify", {}).get("enabled"):
        return {"sent": False, "reason": "notify disabled"}
    sm = build_summary(cfg, digest, date_str, serp)
    channel = cfg["notify"].get("channel", "plain")
    preview = format_plain(sm)

    # PushPlus uses its own API endpoint and token (not a generic webhook URL).
    if channel == "pushplus":
        token = os.getenv("PUSHPLUS_TOKEN", "").strip()
        if not token:
            return {"sent": False, "reason": "PUSHPLUS_TOKEN not set",
                    "channel": channel, "preview": preview}
        payload = format_payload(channel, sm)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            "http://www.pushplus.plus/send",
            data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
                # PushPlus returns JSON like {"code":200,"msg":"...","data":"..."}
                # code 200 means accepted; anything else is a rejection.
                try:
                    api_resp = json.loads(raw)
                except Exception:
                    api_resp = {"raw": raw}
                code = api_resp.get("code", resp.status)
                msg = api_resp.get("msg", "")
                if code == 200:
                    return {"sent": True, "channel": channel, "status": resp.status,
                            "preview": preview}
                return {"sent": False, "channel": channel, "status": resp.status,
                        "pushplus_code": code, "pushplus_msg": msg,
                        "preview": preview}
        except Exception as e:
            return {"sent": False, "channel": channel, "error": str(e),
                    "preview": preview}

    # Generic webhook (slack, discord, telegram, feishu, etc.)
    url = os.getenv("NOTIFY_WEBHOOK_URL")
    if not url:
        return {"sent": False, "reason": "NOTIFY_WEBHOOK_URL not set",
                "channel": channel, "preview": preview}
    body = json.dumps(format_payload(channel, sm)).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return {"sent": True, "channel": channel, "status": resp.status,
                    "preview": preview}
    except Exception as e:
        return {"sent": False, "channel": channel, "error": str(e),
                "preview": preview}
