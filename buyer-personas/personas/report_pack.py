"""Config-driven HTML report for any domain pack.

Every label on the page comes from the pack: workspace name and description,
entity labels, metric labels, intake fields, playbook field labels, segment rules,
and trigger settings. This module contains no industry-specific wording.
Opens in any browser; no server or network access needed.
"""

from __future__ import annotations

import html

from .report import STYLE
from .workspace import Workspace

OP_TEXT = {">=": "at least", ">": "more than", "<=": "at most", "<": "less than", "==": "exactly", "!=": "not"}
BASELINE_TEXT = {"same_weekday": "same weekday, excluding trend dates"}
MATCH_TEXT = {"tag_overlap_with_segment_playbook": "trend tags overlap the segment's playbook tags"}
EXTRA_STYLE = """
.pill{display:inline-block;font-size:12px;padding:3px 9px;border-radius:99px;background:#eef1f6;margin:2px 4px 2px 0}
.disclaimer{background:#fff3dd;padding:10px 14px;border-radius:8px;font-size:13px;margin-bottom:16px}
.rules{font-size:13px;color:#2c3446;margin:6px 0 0;padding-left:18px}
h2.section{margin-top:32px;font-size:18px}
"""


def _e(v) -> str:
    return html.escape(str(v))


def _pct_metric(metric: dict) -> bool:
    return metric.get("agg", "").startswith("share_")


def _fmt_value(metric: dict | None, value) -> str:
    if isinstance(value, float) and _pct_metric(metric or {}):
        return f"{value * 100:.0f}%"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


class _Labels:
    def __init__(self, ws: Workspace):
        self.ws = ws
        self.metrics = {m["id"]: m for m in ws.raw["metrics"]}

    def metric(self, mid: str) -> str:
        return self.metrics.get(mid, {}).get("label", mid.replace("_", " ").capitalize())

    def segment_id(self, label: str, entity: str) -> str | None:
        for seg in self.ws.entity(entity)["segments"]:
            if seg["label"] == label:
                return seg["id"]
        return None

    def condition(self, c: dict) -> str:
        m = self.metrics.get(c["metric"], {})
        val = _fmt_value(m, c["value"])
        return f"{self.metric(c['metric'])} {OP_TEXT.get(c['op'], c['op'])} {val}"

    def when_text(self, when: dict) -> str:
        if "all" in when:
            return " and ".join(self.when_text(w) if "all" in w or "any" in w else self.condition(w)
                                for w in when["all"])
        if "any" in when:
            return " or ".join(self.when_text(w) if "all" in w or "any" in w else self.condition(w)
                               for w in when["any"])
        return self.condition(when)


def _playbook_sections(ws: Workspace, labels: _Labels, persona: dict, entity: str) -> str:
    fields = [f for f in ws.raw["playbook"]["fields"] if f["id"] != "tags"]
    sid = labels.segment_id(persona["segment"], entity)
    pb = ws.raw["playbook"]["by_segment"].get(sid, {}) if sid else {}
    values = dict(persona.get("playbook", {}))
    rows = ""
    for f in fields:
        val = values.get(f["id"], "")
        if val:
            rows += f"<tr><td>{_e(f['label'])}</td><td>{_e(val)}</td></tr>"
    tags = pb.get("tags", [])
    tags_html = "".join(f"<span class='pill'>{_e(t)}</span>" for t in tags)
    tag_label = next((f["label"] for f in ws.raw["playbook"]["fields"] if f["id"] == "tags"), "Trigger tags")
    return (f"<h3 style='font-size:14px;margin:14px 0 0'>Playbook</h3><table>{rows}</table>"
            f"<div style='margin-top:6px'><span class='label'>{_e(tag_label)}:</span> {tags_html}</div>")


def _persona_card(ws: Workspace, labels: _Labels, entity: str, p: dict) -> str:
    behavior = p.get("behavior", {})
    rows = "".join(
        f"<tr><td>{_e(labels.metric(k))}</td><td>{_e(_fmt_value(labels.metrics.get(k), v))}</td></tr>"
        for k, v in behavior.items()
    )
    hooks = "".join(
        f"<li>{_e(t['topic'])} <span class='label'>({_e(t['date'])}, {_e(t.get('scope', ''))})</span></li>"
        for t in p.get("trend_hooks", [])
    ) or "<li class='label'>No matching trends in the window</li>"
    s = p["size"]
    share_key = next((k for k in s if k.startswith("share_of_")), None)
    share_label = "Share of " + ws.raw["events"].get("amount_label", "amount").lower()
    share_html = f"<tr><td>{_e(share_label)}</td><td><b>{s[share_key]}%</b></td></tr>" if share_key else ""
    return f"""
<div class="card">
  <h2>{_e(p['id'])} · {_e(p['segment'])}</h2>
  <div class="label">{_e(p.get('summary', ''))}</div>
  <table>
    <tr><td>{_e(ws.entity(entity)['label_plural'])}</td><td><b>{s['customers']}</b></td></tr>
    {share_html}
  </table>
  <h3 style='font-size:14px;margin:14px 0 0'>Behavior (observed)</h3>
  <table>{rows}</table>
  {_playbook_sections(ws, labels, p, entity)}
  <h3 style='font-size:14px;margin:14px 0 0'>Trend hooks</h3>
  <ul style='font-size:13px;padding-left:18px'>{hooks}</ul>
  <div class="narr">{_e(p.get('narrative', ''))}</div>
</div>"""


def _rules_section(ws: Workspace, labels: _Labels, entity: str) -> str:
    items = ""
    for seg in sorted(ws.entity(entity)["segments"], key=lambda s: s.get("priority", 10**6)):
        if seg.get("default"):
            body = "Everyone not matched above"
        else:
            body = labels.when_text(seg["when"])
        items += f"<li><b>{_e(seg['label'])}</b>: {_e(body)}</li>"
    return (f"<h2 class='section'>Segment rules</h2><div class='card'>"
            f"<div class='label'>Checked in priority order; the first match wins.</div>"
            f"<ul class='rules'>{items}</ul></div>")


def _intake_section(ws: Workspace) -> str:
    fields = ws.raw.get("intake_fields", [])
    if not fields:
        return ""
    rows = ""
    for f in fields:
        used = "Yes" if f.get("usable_in_rules") else "No, collected for planning only"
        rows += (f"<tr><td>{_e(f['label'])}</td><td>{_e(f['type'])}</td>"
                 f"<td>{_e(used)}</td></tr>")
    return (f"<h2 class='section'>Intake fields</h2><div class='card'>"
            f"<table><tr><th>Field</th><th>Type</th><th>Used in segment rules</th></tr>{rows}</table>"
            f"</div>")


def _triggers_section(ws: Workspace, labels: _Labels, result: dict) -> str:
    t = ws.raw["triggers"]["correlate"]
    lift_rows = "".join(
        f"<tr><td>{_e(r['topic'])}</td><td>{r['events']}</td><td>{r['avg_lift_pct']}%</td>"
        f"<td>{_e(r['top_segment'])}</td><td>{_e(r['evidence'])}</td></tr>"
        for r in result.get("trend_lift", [])
    ) or f"<tr><td colspan=5>No trend feed provided or no matching dates.</td></tr>"
    settings = (
        f"<div class='label'>Measured on: <b>{_e(labels.metric(t['metric']))}</b> · "
        f"baseline: {_e(BASELINE_TEXT.get(t['baseline'], t['baseline']))} · "
        f"match: {_e(MATCH_TEXT.get(t['match'], t['match']))} · "
        f"minimum events: {t['min_events']} · z threshold: {t['z_threshold']}</div>"
    )
    return (f"<h2 class='section'>Trend triggers</h2><div class='card'>{settings}"
            f"<table><tr><th>Trend</th><th>Events</th><th>Avg lift</th><th>Most responsive segment</th>"
            f"<th>Evidence</th></tr>{lift_rows}</table>"
            f"<div class='label' style='margin-top:10px'>Fewer events than the minimum is anecdote, not evidence. "
            f"A correlation on a few days does not show the trend caused the change.</div></div>")


def render_pack_report(result: dict, ws: Workspace, disclaimer: str = "") -> str:
    labels = _Labels(ws)
    entity = result["entity"]
    ws_meta = ws.raw["workspace"]
    cards = "".join(_persona_card(ws, labels, entity, p) for p in result["personas"])
    segment_rows = "".join(
        f"<tr><td>{_e(p['id'])}</td><td>{_e(p['segment'])}</td><td>{p['size']['customers']}</td></tr>"
        for p in result["personas"]
    )
    geo = ("Coarse geographic intake enabled (planning only; no individual locations)"
           if ws.geo_enabled() else "No geographic intake")
    banner = f"<div class='disclaimer'>{_e(disclaimer)}</div>" if disclaimer else ""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>{_e(ws_meta['name'])} · {_e(ws.raw['pack_id'])}</title><style>{STYLE}{EXTRA_STYLE}</style></head><body>
<h1>{_e(ws_meta['name'])}</h1>
<div class="sub">{_e(ws.raw.get('description', ''))}</div>
<div class="sub">Period {_e(result['period'])} · {result['entities']} {_e(ws.entity(entity)['label_plural'].lower())} · as of {_e(result['as_of'])} · pack {_e(ws.raw['pack_id'])} v{ws.raw['pack_version']} · {_e(geo)}</div>
{banner}
<h2 class="section">Segments</h2>
<div class="card"><table><tr><th>ID</th><th>Segment</th><th>{_e(ws.entity(entity)['label_plural'])}</th></tr>{segment_rows}</table></div>
<h2 class="section">Personas</h2>
<div class="grid">{cards}</div>
{_rules_section(ws, labels, entity)}
{_intake_section(ws)}
{_triggers_section(ws, labels, result)}
</body></html>"""
