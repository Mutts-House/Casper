"""End-to-end run: load -> segment -> trends -> personas -> narrative -> outputs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import events, ingest, profile, trends
from .plugins import get_provider
from .report import render_html
from .segment import customer_features, segment_customers


@dataclass
class RunConfig:
    files: list[str] = field(default_factory=list)
    pdfs: list[str] = field(default_factory=list)
    sql: list[tuple[str, str]] = field(default_factory=list)  # (sqlite_path, query)
    db_urls: list[tuple[str, str]] = field(default_factory=list)  # (sqlalchemy_url, query)
    mapping_path: str | None = None
    survey_path: str | None = None
    trends_path: str | None = None
    ai: str = "template"
    store_name: str = "My Store"
    out_dir: str = "output"
    as_of: str | None = None
    trend_window_days: int = 90
    margins_path: str | None = None
    events_postal_code: str | None = None   # enables the Ticketmaster events feed
    events_radius: int = 25


def load_lines(cfg: RunConfig) -> pd.DataFrame:
    mapping = ingest.load_mapping(cfg.mapping_path)
    frames = [ingest.load_file(f, mapping) for f in cfg.files]
    frames += [ingest.load_pdf(f, mapping) for f in cfg.pdfs]
    frames += [ingest.load_sql(db, q, mapping) for db, q in cfg.sql]
    frames += [ingest.load_sql_engine(url, q, mapping) for url, q in cfg.db_urls]
    if not frames:
        raise ValueError("No input data. Pass --input, --pdf, --sqlite, or --db-url.")
    return pd.concat(frames, ignore_index=True)


def run(cfg: RunConfig) -> dict:
    lines = load_lines(cfg)
    as_of = pd.Timestamp(cfg.as_of) if cfg.as_of else lines["timestamp"].max()
    lines = lines[lines["timestamp"] <= as_of]

    margins = json.loads(Path(cfg.margins_path).read_text()) if cfg.margins_path else None
    feats = segment_customers(customer_features(lines, as_of, margins))
    identified_mask = lines["customer_key"].notna()
    identified_revenue = float(lines.loc[identified_mask, "amount"].sum())
    total_revenue = float(lines["amount"].sum())

    # Line-level segment label; anonymous sales are kept as "Unidentified" for trend math.
    seg_map = dict(zip(feats["customer_key"], feats["segment"])) if not feats.empty else {}
    lines = lines.assign(segment=lines["customer_key"].map(seg_map).fillna("Unidentified"))
    lines["day"] = lines["timestamp"].dt.normalize()
    daily = lines.groupby("day", as_index=False)["amount"].sum().rename(columns={"day": "date", "amount": "revenue"})
    daily_seg = lines.groupby(["day", "segment"], as_index=False)["amount"].sum().rename(
        columns={"day": "date", "amount": "revenue"}
    )

    # Trends: match to segments and measure lift on dates they happened.
    trend_hooks: dict[str, list[dict]] = {}
    lift_table = pd.DataFrame()
    used_trends = pd.DataFrame()
    warnings: list[str] = []
    frames = []
    if cfg.trends_path:
        frames.append(trends.load_feed(cfg.trends_path))
    if cfg.events_postal_code:
        # Fetch the whole data period so lift has history; hooks use the window below.
        try:
            events_src = events.TicketmasterEventsSource(cfg.events_postal_code, radius=cfg.events_radius)
            frames.append(events_src.fetch(lines["timestamp"].min().normalize(), as_of))
        except RuntimeError as exc:
            warnings.append(f"Events feed skipped: {exc}")
    if frames:
        used_trends = pd.concat(frames, ignore_index=True)
        used_trends["date"] = pd.to_datetime(used_trends["date"], errors="coerce")
        used_trends = used_trends.dropna(subset=["date"])
        if not used_trends.empty:
            lift_table = trends.measure_trend_lift(daily, used_trends, daily_seg)
            window_start = as_of - pd.Timedelta(days=cfg.trend_window_days)
            recent = used_trends[(used_trends["date"] >= window_start) & (used_trends["date"] <= as_of)]
            trend_hooks = trends.match_trends_to_segments(
                recent, list(feats["segment"].unique()) if not feats.empty else []
            )

    survey = ingest.load_survey(cfg.survey_path) if cfg.survey_path else None
    personas = profile.build_personas(feats, survey, trend_hooks, identified_revenue)

    period = f"{lines['timestamp'].min().date()} to {as_of.date()}"
    facts = profile.facts_sheet(personas, cfg.store_name, period)
    provider = get_provider(cfg.ai)
    rejections = profile.add_narratives(personas, facts, provider)

    quality = {
        "line_items": int(len(lines)),
        "transactions": int(lines["transaction_id"].nunique()),
        "identified_customers": int(feats["customer_key"].nunique()) if not feats.empty else 0,
        "identified_revenue_share_pct": round(identified_revenue / total_revenue * 100, 1) if total_revenue else 0,
        "period": period,
        "sources": sorted(lines["source"].unique().tolist()),
    }
    result = {
        "store": cfg.store_name,
        "as_of": as_of.date().isoformat(),
        "ai_provider": provider.name,
        "data_quality": quality,
        "personas": personas,
        "narrative_rejections": rejections,
        "warnings": warnings,
        "trend_lift": lift_table.to_dict(orient="records") if not lift_table.empty else [],
        "playbook_version": "1.0",
    }

    out = Path(cfg.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "personas.json").write_text(json.dumps(result, indent=2, default=str))
    if not used_trends.empty:
        used_trends.to_csv(out / "trends_used.csv", index=False)
    (out / "report.html").write_text(render_html(result))
    return result
