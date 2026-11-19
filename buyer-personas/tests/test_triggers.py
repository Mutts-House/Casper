import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from personas import engine, trends
from personas.workspace import Workspace

PACK = Path(__file__).resolve().parent.parent / "packs" / "retail.json"


def base() -> dict:
    return copy.deepcopy(json.loads(PACK.read_text(encoding="utf-8")))


def load_raw(raw):
    ws = Workspace(raw)
    ws.validate()
    return ws


def test_trigger_settings_match_legacy_constants():
    s = engine.trigger_settings()
    assert s["min_events"] == 3  # matches the original MIN_EVENTS_FOR_EVIDENCE
    assert s["z_threshold"] == 2.0
    assert s["metric"] == "spend"
    assert s["baseline"] == "same_weekday"
    assert s["match"] == "tag_overlap_with_segment_playbook"


def test_load_feed_maps_renamed_columns(tmp_path):
    f = tmp_path / "feed.csv"
    f.write_text("event_date,headline,tags\n2026-10-01,Local game,sports\n", encoding="utf-8")
    df = trends.load_feed(str(f), {"date": "event_date", "topic": "headline", "tags": "tags"})
    assert {"date", "topic", "tags"} <= set(df.columns)
    assert df.loc[0, "topic"] == "Local game"


def test_load_feed_identity_when_names_match(tmp_path):
    f = tmp_path / "feed.csv"
    f.write_text("date,topic,scope,tags\n2026-10-01,Storm,local,weather\n", encoding="utf-8")
    df = trends.load_feed(str(f))
    assert list(df.columns) == ["date", "topic", "scope", "tags"]


def test_min_events_gate_flags_too_few():
    days = pd.date_range("2026-09-01", periods=60, freq="D")
    rev = pd.DataFrame({"date": days, "revenue": [100.0 + (i % 7) for i in range(60)]})
    tr = pd.DataFrame({"date": ["2026-09-20", "2026-09-27"], "topic": ["Parade", "Parade"]})
    out = trends.measure_trend_lift(rev, tr, min_events=5, z_threshold=2.0)
    assert out.loc[0, "evidence"] == "too few events"


def test_z_threshold_is_respected():
    import numpy as np
    days = pd.date_range("2026-09-01", periods=60, freq="D")
    noise = np.random.default_rng(7).normal(0, 5, 60)
    rev = pd.DataFrame({"date": days, "revenue": 100.0 + noise})
    tr = pd.DataFrame({"date": ["2026-09-08", "2026-09-15", "2026-09-22"], "topic": ["X"] * 3})
    rev.loc[rev["date"].isin(pd.to_datetime(tr["date"])), "revenue"] += 500
    loose = trends.measure_trend_lift(rev, tr, min_events=3, z_threshold=1.0)
    strict = trends.measure_trend_lift(rev, tr, min_events=3, z_threshold=1e9)
    assert loose.loc[0, "evidence"] == "outside normal range"
    assert strict.loc[0, "evidence"] == "within normal variation"


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r["triggers"]["correlate"].update(metric="gross_profit"), "sum of 'amount'"),
    (lambda r: r["triggers"]["correlate"].update(metric="not_real"), "not a declared metric"),
    (lambda r: r["triggers"]["correlate"].update(baseline="rolling_mean"), "Unknown trigger baseline"),
    (lambda r: r["triggers"]["correlate"].update(match="fuzzy"), "Unknown trigger match mode"),
    (lambda r: r["triggers"]["correlate"].update(min_events=0), "min_events"),
    (lambda r: r["triggers"]["correlate"].update(z_threshold=-1), "z_threshold"),
    (lambda r: r["triggers"]["fields"].pop("topic"), "must map 'topic'"),
    (lambda r: r.pop("triggers"), "missing required section 'triggers'"),
])
def test_invalid_trigger_config_rejected(mutate, message):
    raw = base()
    mutate(raw)
    with pytest.raises(ValueError, match=message):
        load_raw(raw)
