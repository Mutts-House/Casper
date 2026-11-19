from pathlib import Path

import pandas as pd
import pytest

from personas import engine, generic
from personas.pipeline import RunConfig, load_lines
from personas.segment import customer_features, segment_customers
from personas.workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent
POS = ROOT / "sample_data" / "pos_export_demo.csv"


def retail_as_generic_rows(lines: pd.DataFrame) -> tuple[pd.DataFrame, pd.Timestamp]:
    """Project retail line items onto the generic schema (one timestamp per transaction)."""
    as_of = lines["timestamp"].max()
    ident = lines[lines["customer_key"].notna()].copy()
    tx_time = ident.groupby("transaction_id")["timestamp"].transform("min")
    rows = pd.DataFrame({
        "entity_id": ident["customer_key"].astype(str),
        "transaction_id": ident["transaction_id"],
        "timestamp": tx_time,
        "amount": ident["amount"],
        "category": ident["category"],
    })
    return rows, as_of


@pytest.fixture(scope="module")
def retail():
    lines = load_lines(RunConfig(files=[str(POS)]))
    feats = segment_customers(customer_features(lines, lines["timestamp"].max()))
    rows, as_of = retail_as_generic_rows(lines)
    return feats, rows, as_of


def test_generic_segments_match_retail_pipeline(retail):
    feats, rows, as_of = retail
    ws = Workspace.load(ROOT / "packs" / "retail.json")
    metrics = generic.compute_metrics(rows, ws, as_of)
    generic_seg = generic.assign_segments(metrics, ws, "customer")
    expected = dict(zip(feats["customer_key"].astype(str), feats["segment"]))
    got = generic_seg.to_dict()
    assert set(got) == set(expected)
    mismatches = {k: (expected[k], got[k]) for k in expected if expected[k] != got[k]}
    assert not mismatches, list(mismatches.items())[:5]


def test_generic_metrics_match_retail_features(retail):
    feats, rows, as_of = retail
    ws = Workspace.load(ROOT / "packs" / "retail.json")
    metrics = generic.compute_metrics(rows, ws, as_of)
    f = feats.set_index(feats["customer_key"].astype(str))
    for col_generic, col_legacy in [
        ("visits", "visits"), ("visits_regular_period", "visits_regular_period"),
        ("recency_days", "recency_days"), ("spend", "spend"),
        ("share_late_night", "share_late_night"), ("share_midday", "share_midday"),
    ]:
        left = metrics[col_generic].reindex(f.index)
        right = f[col_legacy].astype(float)
        assert (left.astype(float) - right).abs().max() < 1e-6, col_generic


def test_map_columns_reports_missing_required_field():
    ws = Workspace.load(ROOT / "packs" / "retail.json")
    df = pd.DataFrame({"when": ["2026-01-01"], "who": ["x"]})
    with pytest.raises(ValueError, match="entity_id|transaction_id"):
        generic.map_columns(df, ws)
