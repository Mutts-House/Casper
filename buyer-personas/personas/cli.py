"""Command line interface.

Examples:
  python -m personas demo
  python -m personas run --input sales.csv --survey survey.csv --trends trends.csv --store "Main Store #1234"
  python -m personas run --input sales.xlsx --sqlite pos.db --sql "SELECT * FROM sales" --ai ollama:llama3.1
  python -m personas run --input sales.csv --ai file:ai_plugins/my_provider.py
  python -m personas pack-run --pack packs/healthcare.json --input visits.csv --trends feed.csv
  python -m personas pack-demo --pack community
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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


PACKS = {"healthcare": "packs/healthcare.json", "community": "packs/community.json"}


def _pack_root(path: str) -> str:
    p = Path(path)
    return str(p if p.is_absolute() or p.exists() else Path(__file__).resolve().parent.parent / path)


def _pack_run_cmd(args: argparse.Namespace) -> int:
    from .pack_run import run_pack
    result = run_pack(_pack_root(args.pack), args.input, args.out, as_of=args.as_of, trends_path=args.trends)
    print(f"Pack:             {result['pack_id']} v{result['pack_version']}")
    print(f"Period:           {result['period']}")
    print(f"{result['entity'].title()}s:         {result['entities']}")
    for p in result["personas"]:
        print(f"  {p['id']} {p['segment']:<30} {p['size']['customers']:>5}  {p['size']['share_of_amount_pct']:>5}% of total")
    for row in result["trend_lift"]:
        print(f"  trend {row['topic']!r}: {row['evidence']} (events={row['events']}, z={row['avg_z_score']})")
    print(f"Data:    {args.out}/personas.json")
    return 0


def _pack_demo_cmd(args: argparse.Namespace) -> int:
    from . import pack_demo
    from .pack_run import run_pack
    if args.pack == "healthcare":
        visits = pack_demo.generate_healthcare(args.data_dir)
        trends_csv, pack, out = f"{args.data_dir}/health_trends_demo.csv", PACKS["healthcare"], args.out or "output_healthcare"
        data = visits
    else:
        data = pack_demo.generate_community(args.data_dir)
        trends_csv, pack, out = f"{args.data_dir}/community_trends_demo.csv", PACKS["community"], args.out or "output_community"
    print(f"Synthetic demo data written to {args.data_dir}/ (labeled SYNTHETIC)")
    result = run_pack(_pack_root(pack), data, out, trends_path=trends_csv)
    print(f"Ran pack {result['pack_id']}: {len(result['personas'])} segments over {result['entities']} {result['entity']}s.")
    print(f"Data:    {out}/personas.json")
    return 0


def _examples_cmd(args: argparse.Namespace) -> int:
    from .examples import build_examples
    written = build_examples(args.out, args.data_dir)
    for name, path in written.items():
        print(f"{name:<11} {path}")
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

    pr = sub.add_parser("pack-run", help="Run a domain pack (healthcare, community, ...) on a transaction CSV")
    pr.add_argument("--pack", required=True, help="Path to a pack JSON, e.g. packs/healthcare.json")
    pr.add_argument("--input", required=True, help="Transaction CSV using the pack's field names")
    pr.add_argument("--trends", help="Optional trend feed CSV (mapped by the pack's triggers.fields)")
    pr.add_argument("--as-of", help="Analysis date YYYY-MM-DD (default: latest row)")
    pr.add_argument("--out", default="output_pack", help="Output folder")
    pr.set_defaults(func=_pack_run_cmd)

    pd_ = sub.add_parser("pack-demo", help="Generate synthetic data for a demo pack and run it")
    pd_.add_argument("--pack", choices=sorted(PACKS), required=True)
    pd_.add_argument("--data-dir", default="sample_data")
    pd_.add_argument("--out", help="Output folder (default: output_<pack>)")
    pd_.set_defaults(func=_pack_demo_cmd)

    ex = sub.add_parser("examples", help="Build showcase HTML reports for all packs from synthetic data")
    ex.add_argument("--out", default="examples", help="Output folder")
    ex.add_argument("--data-dir", default="sample_data")
    ex.set_defaults(func=_examples_cmd)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
