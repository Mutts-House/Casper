"""Pack-driven metrics, segmentation and summaries for any domain.

Input is a transaction-level table, one row per line of an event (a visit, an
attendance, a charge). Required canonical columns after mapping:
    entity_id, transaction_id, timestamp, amount, category

Every metric and segment rule is read from the workspace pack, so this module
has no industry-specific values. It is what runs a new domain pack
(healthcare, community services) end to end. The retail pack keeps its
dedicated pipeline; test_generic.py checks that this module reproduces the
retail segments on the same data.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from . import engine
from .workspace import Workspace

CANONICAL = ["entity_id", "transaction_id", "timestamp", "amount", "category"]


def _norm(name: str) -> str:
    return re.sub(r"[\s_]+", "", str(name).strip().lower())


def map_columns(df: pd.DataFrame, ws: Workspace) -> pd.DataFrame:
    """Rename source columns to canonical names using the pack's events.fields aliases."""
    fields = ws.raw["events"]["fields"]
    lookup = {_norm(c): c for c in df.columns}
    rename = {}
    missing = []
    for canon in CANONICAL:
        if canon == "amount" and "amount" not in fields:
            continue
        if canon == "category" and "category" not in fields:
            continue
        found = next((lookup[_norm(a)] for a in fields.get(canon, []) if _norm(a) in lookup), None)
        if found is None:
            if canon in ("amount", "category"):
                continue
            missing.append(canon)
        else:
            rename[found] = canon
    if missing:
        raise ValueError(f"Input is missing required column(s) for: {', '.join(missing)}")
    return df.rename(columns=rename)


def normalize_categories(values: pd.Series, ws: Workspace) -> pd.Series:
    rules = [(r["category"], [k.lower() for k in r["keywords"]])
             for r in ws.raw["category_normalization"]["rules"]]
    default = ws.raw["category_normalization"]["default"]

    def one(text) -> str:
        t = "" if pd.isna(text) else str(text).lower()
        for label, keywords in rules:
            if any(k in t for k in keywords):
                return label
        return default

    return values.map(one)


def _daypart_labels(ws: Workspace) -> dict[int, str]:
    out: dict[int, str] = {}
    for label, start, end in engine.dayparts(ws):
        for h in range(start, end):
            out[h] = label
    return out


def compute_metrics(rows: pd.DataFrame, ws: Workspace, as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    """One row per entity, one column per metric in the pack."""
    rows = rows[rows["entity_id"].notna()].copy()
    as_of = as_of or rows["timestamp"].max()
    rows = rows[rows["timestamp"] <= as_of]
    rows["daypart"] = rows["timestamp"].dt.hour.map(_daypart_labels(ws)).fillna("other")
    if "category" not in rows:
        rows["category"] = ws.raw["category_normalization"]["default"]
    margins = engine.margin_rates(ws)
    default_margin = engine.default_margin(ws)

    keys = sorted(rows["entity_id"].unique())
    out = pd.DataFrame(index=pd.Index(keys, name="entity_id"))
    for m in ws.raw["metrics"]:
        out[m["id"]] = _one_metric(rows, m, as_of, margins, default_margin).reindex(keys).fillna(0)
    return out


def _filter(rows: pd.DataFrame, m: dict, as_of: pd.Timestamp) -> pd.DataFrame:
    base = rows
    if "window_days" in m:
        lo = as_of - pd.Timedelta(days=m["window_days"]["from"])
        hi = as_of - pd.Timedelta(days=m["window_days"]["to"])
        base = base[base["timestamp"].between(lo, hi)]
    where = m.get("where", {})
    if "daypart" in where:
        base = base[base["daypart"] == where["daypart"]]
    if "daypart_in" in where:
        base = base[base["daypart"].isin(where["daypart_in"])]
    if "category" in where:
        base = base[base["category"] == where["category"]]
    return base


def _one_metric(rows, m, as_of, margins, default_margin) -> pd.Series:
    agg = m["agg"]
    key = "entity_id"
    if agg == "count_transactions":
        return _filter(rows, m, as_of).groupby(key)["transaction_id"].nunique()
    if agg == "days_since_last":
        return (as_of - rows.groupby(key)["timestamp"].max()).dt.days.astype(int)
    if agg == "sum":
        return _filter(rows, m, as_of).groupby(key)[m.get("of", "amount")].sum()
    if agg == "sum_margin_weighted":
        f = _filter(rows, m, as_of)
        gross = f["amount"] * f["category"].map(lambda c: margins.get(c, default_margin))
        return gross.groupby(f[key]).sum()
    if agg == "share_of_transactions":
        num = _filter(rows, m, as_of).groupby(key)["transaction_id"].nunique()
        den = rows.groupby(key)["transaction_id"].nunique().replace(0, 1)
        return (num / den)
    if agg == "share_of_amount":
        num = _filter(rows, m, as_of).groupby(key)["amount"].sum()
        den = rows.groupby(key)["amount"].sum().replace(0, 1)
        return (num / den)
    raise ValueError(f"Unsupported aggregation '{agg}'")


def assign_segments(metrics: pd.DataFrame, ws: Workspace, entity_id: str) -> pd.Series:
    return metrics.apply(lambda r: engine.assign_segment(r, entity_id, ws), axis=1).rename("segment")


def summarize(rows: pd.DataFrame, ws: Workspace, entity_id: str = "customer", as_of=None) -> dict:
    """Segment-level summary with playbook text and a template narrative."""
    rows = rows.copy()
    if "category" not in rows:
        rows["category"] = ws.raw["category_normalization"]["default"]
    metrics = compute_metrics(rows, ws, as_of)
    seg = assign_segments(metrics, ws, entity_id)
    metrics = metrics.join(seg)
    total_amount = float(rows["amount"].sum()) or 1.0
    entity_amount = rows.groupby("entity_id")["amount"].sum()
    metrics["amount"] = entity_amount.reindex(metrics.index).fillna(0)

    plural = engine.entity_plural(ws, entity_id)
    template = engine.narrative_template(ws)
    personas = []
    ordered = metrics.groupby("segment")["amount"].sum().sort_values(ascending=False).index
    for i, label in enumerate(ordered, start=1):
        g = metrics[metrics["segment"] == label]
        pb = engine.playbook_for(label, ws, entity_id)
        share = round(float(g["amount"].sum()) / total_amount * 100, 1)
        facts = {
            "segment": label,
            "share_pct": share,
            "customers": int(len(g)),
            "entity_plural": plural,
            "visits": round(float(g["visits"].mean()), 1) if "visits" in g else 0,
            "avg_amount": f"{float(g['amount'].mean()):.2f}",
            "goal": pb.get("goal", ""),
            "offer_angle": pb.get("offer_angle", ""),
            "channel": pb.get("channel", ""),
            "timing": pb.get("timing", ""),
        }
        facts["avg_basket"] = facts["avg_amount"]
        facts["peak_daypart"] = ""
        personas.append({
            "id": f"P{i}",
            "segment": label,
            "summary": pb.get("summary", ""),
            "size": {"customers": facts["customers"], "share_of_amount_pct": share},
            "behavior": {k: round(float(g[k].mean()), 2) for k in g.columns
                         if k in {m["id"] for m in ws.raw["metrics"]} and k != "segment"},
            "playbook": {k: pb.get(k, "") for k in ("goal", "offer_angle", "channel", "timing", "avoid")},
            "narrative": template.format(**facts),
        })
    return {"entity": entity_id, "pack_id": ws.raw["pack_id"], "as_of": str(as_of or rows["timestamp"].max()),
            "personas": personas, "entities": int(len(metrics))}


def load_events(path: str | Path, ws: Workspace) -> pd.DataFrame:
    df = map_columns(pd.read_csv(path), ws)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    if "amount" in df:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    else:
        df["amount"] = 0.0
    if "category" in df:
        df["category"] = normalize_categories(df["category"], ws)
    else:
        df["category"] = ws.raw["category_normalization"]["default"]
    df["entity_id"] = df["entity_id"].astype(str).str.strip().replace({"": None, "nan": None})
    return df
