"""Run any domain pack end to end on a transaction-level CSV.

Used for packs other than retail (healthcare, community programs). Retail keeps
its dedicated pipeline. Output is personas.json with segments, playbook text,
template narratives, and optional trend lift measured on the pack's trigger metric.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import engine, generic, trends
from .workspace import Workspace


def run_pack(
    pack_path: str | Path,
    input_path: str | Path,
    out_dir: str | Path,
    as_of: str | None = None,
    trends_path: str | None = None,
    trend_window_days: int = 90,
) -> dict:
    ws = Workspace.load(pack_path)
    entity = ws.raw["entity_types"][0]["id"]
    rows = generic.load_events(input_path, ws)
    if rows.empty:
        raise ValueError("No usable rows after loading. Check the input columns and dates.")

    as_of_ts = pd.Timestamp(as_of) if as_of else rows["timestamp"].max()
    rows = rows[rows["timestamp"] <= as_of_ts]

    summary = generic.summarize(rows, ws, entity, as_of_ts)
    metrics = generic.compute_metrics(rows, ws, as_of_ts)
    seg_map = generic.assign_segments(metrics, ws, entity).to_dict()
    lines = rows.assign(segment=rows["entity_id"].map(seg_map).fillna("Unidentified"))

    lift = pd.DataFrame()
    used = pd.DataFrame()
    hooks: dict[str, list[dict]] = {}
    if trends_path:
        cfg = engine.trigger_settings(ws)
        feed = trends.load_feed(trends_path, ws.raw["triggers"]["fields"])
        feed["date"] = pd.to_datetime(feed["date"], errors="coerce")
        used = feed.dropna(subset=["date"])
        if not used.empty:
            daily, daily_seg = trends.daily_series(lines, cfg["metric"], ws)
            lift = trends.measure_trend_lift(
                daily, used, daily_seg, min_events=cfg["min_events"], z_threshold=cfg["z_threshold"]
            )
            window_start = as_of_ts - pd.Timedelta(days=trend_window_days)
            recent = used[(used["date"] >= window_start) & (used["date"] <= as_of_ts)]
            hooks = trends.match_trends_to_segments(
                recent, sorted(set(seg_map.values())), ws=ws, entity_id=entity
            )

    personas = summary["personas"]
    for p in personas:
        p["trend_hooks"] = hooks.get(p["segment"], [])[:5]

    result = {
        "pack_id": ws.raw["pack_id"],
        "pack_version": ws.raw["pack_version"],
        "entity": entity,
        "as_of": as_of_ts.date().isoformat(),
        "period": f"{rows['timestamp'].min().date()} to {as_of_ts.date()}",
        "entities": summary["entities"],
        "geo_fields_enabled": ws.geo_enabled(),
        "personas": personas,
        "trend_lift": lift.to_dict(orient="records") if not lift.empty else [],
        "trend_feed_rows_used": int(len(used)),
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "personas.json").write_text(json.dumps(result, indent=2, default=str))
    return result
