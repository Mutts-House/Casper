#!/usr/bin/env python3
"""Generate a self-contained Buyer Personas report from CSV inputs."""
import argparse
import csv
import html
import random
import statistics
from collections import Counter, defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path

PERSONAS = [
    ("P1", "Fuel-and-Go Commuters"),
    ("P2", "Lunch Grab-and-Go"),
    ("P3", "Loyal Core"),
    ("P4", "Late-Night Regulars"),
    ("P5", "Occasional Visitors"),
    ("P6", "Lapsing Regulars"),
]


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def parse_dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _median_days(last_dates, as_of):
    return statistics.median([(as_of - d.date()).days for d in last_dates])


def segment_customers(transactions, as_of=None):
    """Return customer assignments, overlap count, and per-customer observations.

    Priority is P1, P2, P3, P4, P5, P6. Overlap counts customers satisfying
    two or more rules before priority resolves their assignment.
    """
    grouped = defaultdict(list)
    for row in transactions:
        if row.get("customer_id", "").strip():
            row = dict(row)
            row["_dt"] = parse_dt(row["timestamp"])
            row["_amount"] = float(row["amount"])
            row["_fuel"] = str(row.get("is_fuel", "")).lower() in ("1", "true", "yes")
            grouped[row["customer_id"].strip()].append(row)
    as_of = as_of or max((r["_dt"].date() for rows in grouped.values() for r in rows), default=date.today())
    assignments, observations, overlaps = {}, {}, 0
    for customer, rows in grouped.items():
        visits = {r["_dt"].date() for r in rows}
        fuel = sum(r["_fuel"] for r in rows)
        lunch = sum(r["_dt"].hour in range(11, 15) and r["category"].lower() in ("food service", "food", "prepared food") for r in rows)
        late = sum(r["_dt"].hour >= 21 or r["_dt"].hour < 5 for r in rows)
        days_lapsed = (as_of - max(visits)).days
        rules = [
            fuel >= 2 and fuel / len(rows) >= 0.6,
            lunch >= 2 and lunch / len(rows) >= 0.3,
            len(visits) >= 8,
            late >= 2,
            len(visits) <= 2,
            len(visits) >= 3 and days_lapsed >= 30,
        ]
        if sum(rules) > 1:
            overlaps += 1
        index = next((i for i, matched in enumerate(rules) if matched), 5)
        assignments[customer] = PERSONAS[index][0]
        observations[customer] = {"rows": rows, "visits": visits, "last": max(visits), "days_since": days_lapsed}
    return assignments, overlaps, observations


def weekday_lift(transactions, min_baseline=1):
    """Calculate transaction-count lift against the same-weekday baseline."""
    counts = Counter(parse_dt(r["timestamp"]).date() for r in transactions)
    by_weekday = defaultdict(list)
    for day, count in counts.items():
        by_weekday[day.weekday()].append((day, count))
    result = {}
    for day, count in counts.items():
        prior = [n for d, n in by_weekday[day.weekday()] if d < day]
        baseline = statistics.mean(prior[-8:]) if prior else float(min_baseline)
        result[day] = {"count": count, "baseline": baseline, "lift": (count / baseline - 1) if baseline else 0}
    return result


def _money(n):
    return "${:,.2f}".format(n)


def _playbook(code, median_days):
    if code == "P1":
        return ("Protect routine visits", "Fuel + coffee bundle", "SMS/app", "Before the weekday commute", "Deep discounts on fuel")
    if code == "P2":
        return ("Win the lunch mission", "Meal-and-drink combo", "App/email", "10:30–11:30 on workdays", "Late-night messaging")
    if code == "P3":
        return ("Reward habit", "Tiered loyalty reward", "Loyalty app", "Weekly, near their usual daypart", "One-size-fits-all coupons")
    if code == "P4":
        return ("Own the late shift", "Night snack bundle", "SMS", "20:00–22:00", "Daytime-only offers")
    if code == "P5":
        return ("Create a second visit", "First-to-second visit offer", "Receipt/app", "Within 7 days of a visit", "Day-30 win-back triggers")
    timing = "Within 7 days of a visit" if median_days < 30 else "At the next likely occasion"
    return ("Recover the relationship", "Personalized win-back bundle", "SMS/email", timing, "Assuming the old routine still holds")


def build_report(transactions, surveys, trends, margins, closed_dates=None):
    identified = [r for r in transactions if r.get("customer_id", "").strip()]
    assignments, overlaps, obs = segment_customers(transactions)
    lifts = weekday_lift(transactions)
    margin_map = {r["category"]: float(r["margin"]) for r in margins}
    placeholder_margins = not margins or all(abs(v - 0.30) < 1e-9 for v in margin_map.values())
    total_identified_revenue = sum(float(r["amount"]) for r in identified)
    total_revenue = sum(float(r["amount"]) for r in transactions)
    metrics = {}
    for code, _ in PERSONAS:
        customers = [c for c, p in assignments.items() if p == code]
        rows = [r for r in identified if assignments[r["customer_id"].strip()] == code]
        dates = [parse_dt(r["timestamp"]).date() for r in rows]
        median = statistics.median([(max(dates) - d).days for d in []]) if False else statistics.median(
            [obs[c]["days_since"] for c in customers]
        ) if customers else 0
        profit = sum(float(r["amount"]) * margin_map.get(r["category"], 0.30) for r in rows)
        in_store = [r for r in rows if not str(r.get("is_fuel", "")).lower() in ("1", "true", "yes")]
        dayparts = Counter(("Morning" if parse_dt(r["timestamp"]).hour < 11 else "Lunch" if parse_dt(r["timestamp"]).hour < 15 else "Afternoon" if parse_dt(r["timestamp"]).hour < 18 else "Evening" if parse_dt(r["timestamp"]).hour < 21 else "Late night") for r in rows)
        median_days = int(median) if customers else 0
        metrics[code] = {
            "customers": len(customers), "revenue": sum(float(r["amount"]) for r in rows),
            "profit": profit, "visits": len({(r["customer_id"], parse_dt(r["timestamp"]).date()) for r in rows}),
            "basket": sum(float(r["amount"]) for r in rows) / len(rows) if rows else 0,
            "in_store_basket": sum(float(r["amount"]) for r in in_store) / len(in_store) if in_store else 0,
            "median_days": median_days, "peak": dayparts.most_common(1)[0][0] if dayparts else "—",
            "top_category": Counter(r["category"] for r in rows).most_common(1)[0][0] if rows else "—",
            "weekend": sum(parse_dt(r["timestamp"]).weekday() >= 5 for r in rows) / len(rows) if rows else 0,
            "playbook": _playbook(code, median_days), "rows": rows,
        }
    trend_rows = []
    for trend in trends:
        try:
            day = date.fromisoformat(trend["date"])
        except (KeyError, ValueError):
            continue
        lift = lifts.get(day, {"lift": 0, "count": 0, "baseline": 0})
        active = {assignments[r["customer_id"].strip()] for r in identified if parse_dt(r["timestamp"]).date() == day and r["customer_id"].strip() in assignments}
        hooks = [code for code in active if metrics[code]["rows"] and lift["lift"] > 0]
        trend_rows.append({**trend, "lift": lift["lift"], "count": lift["count"], "baseline": lift["baseline"],
                           "responsive": max(hooks, key=lambda x: sum(1 for r in metrics[x]["rows"] if parse_dt(r["timestamp"]).date().weekday() == day.weekday()), default=""),
                           "hooks": hooks if len([t for t in trends if t["name"] == trend["name"]]) >= 3 else []})
    survey_rows = []
    by_customer = {r["customer_id"]: r for r in surveys if r.get("customer_id")}
    for field in ("age_band", "household", "weekly_routine"):
        counts = Counter(r.get(field, "").strip() for r in surveys if r.get(field, "").strip())
        total = sum(counts.values())
        if counts:
            label, n = counts.most_common(1)[0]
            contradictions = 0
            if field == "weekly_routine":
                for survey in surveys:
                    customer_rows = obs.get(survey.get("customer_id", ""), {}).get("rows", [])
                    if not customer_rows or not survey.get(field):
                        continue
                    hours = [parse_dt(r["timestamp"]).hour for r in customer_rows]
                    routine = survey[field].lower()
                    observed = ("lunch" if sum(11 <= h < 15 for h in hours) > len(hours) / 2 else
                                "evening" if sum(17 <= h < 21 for h in hours) > len(hours) / 2 else
                                "commute" if sum(6 <= h < 10 for h in hours) > len(hours) / 2 else "")
                    if observed and observed not in routine.lower():
                        contradictions += 1
            note = "weak signal" if n / total < .5 else ""
            if contradictions:
                note = (note + "; " if note else "") + f"{contradictions} contradict observed behavior"
            survey_rows.append((field.replace("_", " ").title(), label, n, total, n / total, note))
    unidentified_share = 1 - len(identified) / len(transactions) if transactions else 0
    return locals()


def render_html(data):
    esc = lambda x: html.escape(str(x))
    d = data
    warning = '<div class="warning"><strong>Warning:</strong> margins are placeholders; gross profit is directional.</div>' if d["placeholder_margins"] else ""
    rows = []
    for code, name in PERSONAS:
        m = d["metrics"][code]
        play = "<br>".join(f"<b>{k}:</b> {esc(v)}" for k, v in zip(("Goal", "Offer", "Channel", "Timing", "Avoid"), m["playbook"]))
        rows.append(f"<tr><td>{code} {esc(name)}</td><td>{m['customers']}</td><td>{m['revenue']/d['total_identified_revenue']:.1%}</td><td>{m['profit']/sum(x['profit'] for x in d['metrics'].values()):.1%}</td><td>{m['visits']}</td><td>{_money(m['basket'])}<br><small>in-store {_money(m['in_store_basket'])}</small></td><td>{m['median_days']}</td><td>{esc(m['peak'])}</td><td>{esc(m['top_category'])}</td><td>{m['weekend']:.1%}</td><td>{play}</td></tr>")
    trend_html = "".join(f"<tr><td>{esc(t.get('date',''))}</td><td>{esc(t.get('name',''))}</td><td>{t['count']}</td><td>{t['baseline']:.1f}</td><td>{t['lift']:.1%}</td><td>{esc(t['responsive'] or '—') if len([x for x in d['trends'] if x.get('name') == t.get('name')]) >= 3 else '—'}</td><td>{', '.join(t['hooks']) or '—'}</td></tr>" for t in d["trend_rows"])
    survey_html = "".join(f"<li>{esc(a)}: <b>{esc(b)}</b> ({n}/{total}, {share:.1%}) {esc(flag)}</li>" for a,b,n,total,share,flag in d["survey_rows"]) or "<li>No survey responses.</li>"
    return f"""<!doctype html><meta charset="utf-8"><title>Buyer Personas</title>
<style>body{{font:14px system-ui;margin:2rem;color:#17202a}}table{{border-collapse:collapse;width:100%;margin:1rem 0}}th,td{{border:1px solid #ccd;padding:.45rem;text-align:left;vertical-align:top}}th{{background:#eef3f7}}.warning{{padding:1rem;background:#fff0c2;border:2px solid #d99b00}}small{{color:#586}} </style>
<h1>Buyer Personas</h1><p>Identified revenue: {_money(d['total_identified_revenue'])} · Unidentified transactions: {d['unidentified_share']:.1%} · Customers matching multiple rules: {d['overlaps']}</p>{warning}
<p><b>Rule priority:</b> P1 Fuel-and-Go → P2 Lunch → P3 Loyal Core → P4 Late-Night → P5 Occasional → P6 Lapsing. A customer is assigned only to the first matching rule.</p>
<table><tr><th>Persona</th><th>Customers</th><th>Identified revenue</th><th>Gross profit</th><th>Visits</th><th>Avg basket</th><th>Median days since last</th><th>Peak daypart</th><th>Top category</th><th>Weekend</th><th>Playbook</th></tr>{''.join(rows)}</table>
<h2>Trend lift</h2><p>Lift is transaction count versus the average of up to eight earlier dates with the same weekday; it is not revenue. Dates with no transactions are treated as closed. Short-hours/closed dates supplied by the input are flagged by the operator.</p>
<table><tr><th>Date</th><th>Trend</th><th>Transactions</th><th>Same-weekday baseline</th><th>Lift</th><th>Most responsive (3+ events)</th><th>Persona hooks</th></tr>{trend_html}</table>
<h2>Survey signals</h2><p>Sample size is shown; signals below 50% are marked weak. Compare survey claims with observed peak dayparts before activation.</p><ul>{survey_html}</ul>
"""


def generate_report(input_dir, output):
    p = Path(input_dir)
    data = build_report(read_csv(p/"transactions.csv"), read_csv(p/"survey.csv") if (p/"survey.csv").exists() else [], read_csv(p/"trends.csv"), read_csv(p/"margins.csv"))
    Path(output).write_text(render_html(data), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", default=".")
    ap.add_argument("--output", default="report.html")
    args = ap.parse_args()
    generate_report(args.input_dir, args.output)
