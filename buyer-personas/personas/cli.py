"""Command line interface.

Examples:
  python -m personas demo
  python -m personas run --input sales.csv --survey survey.csv --trends trends.csv --store "Main Store #1234"
  python -m personas run --input sales.xlsx --sqlite pos.db --sql "SELECT * FROM sales" --ai ollama:llama3.1
  python -m personas run --input sales.csv --ai file:ai_plugins/my_provider.py
"""

from __future__ import annotations

import argparse
import sys

from .demo import generate
from .pipeline import RunConfig, run


def _run_cmd(args: argparse.Namespace) -> int:
    cfg = RunConfig(
        files=args.input or [],
        pdfs=args.pdf or [],
        sql=[(args.sqlite, args.sql)] if args.sqlite and args.sql else [],
        db_urls=[(args.db_url, args.sql)] if args.db_url and args.sql else [],
        mapping_path=args.mapping,
        survey_path=args.survey,
        trends_path=args.trends,
        ai=args.ai,
        store_name=args.store,
        out_dir=args.out,
        as_of=args.as_of,
        trend_window_days=args.trend_window,
        margins_path=args.margins,
        events_postal_code=args.events_zip,
        events_radius=args.events_radius,
    )
    result = run(cfg)
    q = result["data_quality"]
    print(f"Store:            {result['store']}")
    print(f"Period:           {q['period']}")
    print(f"Transactions:     {q['transactions']}  (line items: {q['line_items']})")
    print(f"Identified:       {q['identified_customers']} customers, {q['identified_revenue_share_pct']}% of revenue")
    print(f"AI provider:      {result['ai_provider']}")
    print()
    for p in result["personas"]:
        print(f"  {p['id']} {p['segment']:<24} {p['size']['customers']:>5} customers  "
              f"{p['size']['share_of_identified_revenue_pct']:>5}% of revenue  "
              f"[narrative: {p['narrative_source']}]")
    for w in result.get("warnings", []):
        print(f"Warning: {w}")
    rejected = result.get("narrative_rejections", [])
    if rejected:
        print(f"\nGrounding: {len(rejected)} AI narrative(s) rejected for invented figures; template text used.")
    print(f"\nReport:  {args.out}/report.html")
    print(f"Data:    {args.out}/personas.json")
    return 0


def _demo_cmd(args: argparse.Namespace) -> int:
    paths = generate(args.data_dir)
    print(f"Synthetic demo data written to {args.data_dir}/ (labeled SYNTHETIC)")
    args_run = argparse.Namespace(
        input=[paths["pos"]], pdf=[], sqlite=None, sql=None, db_url=None, mapping=None,
        survey=paths["survey"], trends=paths["trends"], ai=args.ai,
        store="Demo Store (synthetic)", out=args.out, as_of=None, trend_window=90, margins=None,
        events_zip=None, events_radius=25,
    )
    return _run_cmd(args_run)


def _b2b_cmd(args: argparse.Namespace) -> int:
    from . import b2b
    df, warnings = b2b.load_invoices(args.input)
    for w in warnings:
        print(f"Warning: {w}")
    scorecards = b2b.write_outputs(df, args.out, as_of=args.as_of)
    print(f"Scored {len(scorecards['scorecards'])} companies as of {scorecards['as_of']}.")
    print(f"Wrote {args.out}/b2b_scores.csv and {args.out}/b2b_scorecards.json")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="personas", description="Buyer persona engine")
    sub = parser.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="Build personas from your sales exports")
    r.add_argument("--input", action="append", help="CSV or Excel export (repeatable)")
    r.add_argument("--pdf", action="append", help="PDF with sales tables (repeatable)")
    r.add_argument("--sqlite", help="SQLite database file")
    r.add_argument("--db-url", help="SQLAlchemy URL, e.g. postgresql://user:pass@host/db")
    r.add_argument("--sql", help="Query to run against --sqlite or --db-url")
    r.add_argument("--mapping", help="JSON file mapping your column names to canonical fields")
    r.add_argument("--survey", help="Optional survey CSV for stated attributes")
    r.add_argument("--trends", help="Optional trend feed CSV")
    r.add_argument("--trend-window", type=int, default=90, help="Days of trends to match to personas (default 90)")
    r.add_argument("--events-zip", help="Postal code for the Ticketmaster events feed (needs TICKETMASTER_API_KEY)")
    r.add_argument("--events-radius", type=int, default=25, help="Events search radius in miles")
    r.add_argument("--margins", help="JSON of category margin rates, e.g. {\"fuel\": 0.08}. Defaults are placeholders.")
    r.add_argument("--ai", default="template", help="template | ollama[:model] | openai-compatible[:model] | anthropic[:model] | file:path.py")
    r.add_argument("--store", default="My Store", help="Store name for the report")
    r.add_argument("--out", default="output", help="Output folder")
    r.add_argument("--as-of", help="Analysis date YYYY-MM-DD (default: latest sale)")
    r.set_defaults(func=_run_cmd)

    d = sub.add_parser("demo", help="Generate synthetic data and run the full pipeline")
    d.add_argument("--data-dir", default="sample_data")
    d.add_argument("--out", default="output")
    d.add_argument("--ai", default="template")
    d.set_defaults(func=_demo_cmd)

    b = sub.add_parser("b2b", help="Score B2B companies: value, reliability, growth, concentration")
    b.add_argument("--input", required=True, help="Invoice CSV: company, invoice_date, amount, and optionally due_date, paid_date, cost, disputed")
    b.add_argument("--as-of", help="Analysis date YYYY-MM-DD (default: latest invoice)")
    b.add_argument("--out", default="output_b2b", help="Output folder")
    b.set_defaults(func=_b2b_cmd)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
