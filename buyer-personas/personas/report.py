"""Self-contained HTML report: opens in any browser, no server required."""

from __future__ import annotations

import html

STYLE = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f6f7f9;color:#1d2330;margin:0;padding:32px}
h1{margin:0 0 4px;font-size:26px} .sub{color:#5b6475;margin-bottom:24px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:18px}
.card{background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.card h2{margin:0 0 4px;font-size:18px} .tag{display:inline-block;font-size:11px;padding:2px 7px;border-radius:99px;margin-left:6px;vertical-align:middle}
.observed{background:#e3f1ff;color:#0a5aa6}.stated{background:#e6f7ea;color:#1e7a3a}.inferred{background:#fff3dd;color:#8a5a00}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px} td,th{text-align:left;padding:6px 4px;border-bottom:1px solid #eceef2}
.kpi{font-size:22px;font-weight:600} .label{font-size:12px;color:#5b6475}
.narr{font-size:14px;line-height:1.5;background:#f6f7f9;padding:12px;border-radius:8px;margin-top:10px}
.warn{background:#fff3dd;padding:10px 14px;border-radius:8px;font-size:13px;margin-bottom:16px}
"""


def _e(v) -> str:
    return html.escape(str(v))


def _persona_card(p: dict) -> str:
    b = p["behavior"]
    stated_rows = ""
    for field_name, info in p["stated"].items():
        if field_name == "_note":
            stated_rows += f"<tr><td colspan=2>{_e(info)}</td></tr>"
        else:
            stated_rows += (
                f"<tr><td>{_e(field_name)}</td><td>{_e(info['top'])} "
                f"({round(info['share'] * 100)}% of {info['n']})</td></tr>"
            )
    stated_html = (
        f"<h3 style='font-size:14px;margin:14px 0 0'>Stated <span class='tag stated'>survey</span></h3>"
        f"<table>{stated_rows}</table>" if stated_rows else ""
    )
    trends_html = "".join(
        f"<li>{_e(t['topic'])} <span class='label'>({_e(t['date'])}, {_e(t['scope'])})</span></li>"
        for t in p["trend_hooks"]
    ) or "<li class='label'>No matching trends in window</li>"
    return f"""
<div class="card">
  <h2>{_e(p['id'])} · {_e(p['segment'])}</h2>
  <div class="label">{_e(p['summary'])}</div>
  <table>
    <tr><td>Customers</td><td><b>{p['size']['customers']}</b> <span class='tag observed'>observed</span></td></tr>
    <tr><td>Share of identified revenue</td><td><b>{p['size']['share_of_identified_revenue_pct']}%</b></td></tr>
    <tr><td>Share of gross profit <span class='label'>(placeholder margins)</span></td><td><b>{p['size']['share_of_gross_profit_pct']}%</b></td></tr>
    <tr><td>Visits in window (avg)</td><td>{b['avg_visits_in_window']}</td></tr>
    <tr><td>Average basket</td><td>${b['avg_basket_usd']}</td></tr>
    <tr><td>Days since last visit (median)</td><td>{b['median_days_since_last_visit']}</td></tr>
    <tr><td>Peak daypart</td><td>{_e(b['peak_daypart'])}</td></tr>
    <tr><td>Top category</td><td>{_e(b['top_category'])}</td></tr>
    <tr><td>Weekend share</td><td>{b['weekend_share_pct']}%</td></tr>
  </table>
  {stated_html}
  <h3 style='font-size:14px;margin:14px 0 0'>Playbook <span class='tag inferred'>inferred</span></h3>
  <table>
    <tr><td>Goal</td><td>{_e(p['playbook']['goal'])}</td></tr>
    <tr><td>Offer</td><td>{_e(p['playbook']['offer_angle'])}</td></tr>
    <tr><td>Channel</td><td>{_e(p['playbook']['channel'])}</td></tr>
    <tr><td>Timing</td><td>{_e(p['playbook']['timing'])}</td></tr>
    <tr><td>Avoid</td><td>{_e(p['playbook']['avoid'])}</td></tr>
  </table>
  <h3 style='font-size:14px;margin:14px 0 0'>Trend hooks</h3>
  <ul style='font-size:13px;padding-left:18px'>{trends_html}</ul>
  <div class="narr">{_e(p['narrative'])}<div class="label" style="margin-top:6px">Narrative source: {_e(p['narrative_source'])}</div></div>
</div>"""


def render_html(result: dict) -> str:
    q = result["data_quality"]
    warn = ""
    if q["identified_revenue_share_pct"] < 50:
        warn = (
            f"<div class='warn'>Only {q['identified_revenue_share_pct']}% of revenue is tied to a "
            "loyalty, email, or phone identifier. Persona results describe identified shoppers only.</div>"
        )
    lift_rows = "".join(
        f"<tr><td>{_e(r['topic'])}</td><td>{r['events']}</td><td>{r['avg_lift_pct']}%</td>"
        f"<td>{_e(r['top_segment'])}</td><td>{_e(r['evidence'])}</td></tr>"
        for r in result["trend_lift"]
    ) or "<tr><td colspan=5>No trend feed provided or no matching dates.</td></tr>"
    cards = "".join(_persona_card(p) for p in result["personas"])
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Buyer Personas · {_e(result['store'])}</title><style>{STYLE}</style></head><body>
<h1>Buyer Personas · {_e(result['store'])}</h1>
<div class="sub">Period {_e(q['period'])} · {q['transactions']} transactions · {q['identified_customers']} identified customers · AI: {_e(result['ai_provider'])}</div>
{warn}
<div class="grid">{cards}</div>
<h2 style="margin-top:32px;font-size:18px">Trend lift (observed sales on trend dates vs. normal for that weekday)</h2>
<div class="card"><table><tr><th>Trend</th><th>Events</th><th>Avg lift</th><th>Most responsive segment</th><th>Evidence</th></tr>{lift_rows}</table>
<div class="label" style="margin-top:10px">Lift below 3 events is anecdote, not evidence. Correlation on a few days does not prove the trend caused the sales change.</div></div>
</body></html>"""
