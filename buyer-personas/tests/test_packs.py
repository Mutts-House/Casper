import copy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from personas import engine, pack_demo, pack_run, trends
from personas.workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent
PACKS = {"healthcare": ROOT / "packs" / "healthcare.json", "community": ROOT / "packs" / "community.json"}


def raw_of(name) -> dict:
    return copy.deepcopy(json.loads(PACKS[name].read_text(encoding="utf-8")))


@pytest.mark.parametrize("name", list(PACKS))
def test_pack_validates(name):
    assert Workspace.load(PACKS[name]).raw["pack_id"]


@pytest.mark.parametrize("name", list(PACKS))
def test_pack_has_no_clinical_or_sensitive_rule_fields(name):
    ws = Workspace.load(PACKS[name])
    rule_fields = {m["metric"] for seg in ws.raw["entity_types"][0]["segments"] if "when" in seg
                   for m in seg["when"]["all"]}
    for f in ws.raw["intake_fields"]:
        if f.get("usable_in_rules"):
            pytest.fail(f"{name}: intake field {f['id']} should not be rule-usable here")
    forbidden = {"race", "ethnicity", "religion", "disability", "health_condition",
                 "sexual_orientation", "gender_identity", "immigration_status", "diagnosis", "medication"}
    assert not (rule_fields & forbidden)
    assert not any(k in forbidden for k in ws.raw["events"]["fields"])


def test_healthcare_geo_region_rejected_while_flag_off():
    raw = raw_of("healthcare")
    raw["intake_fields"].append({"id": "area", "label": "Area", "type": "geo_region", "usable_in_rules": False})
    ws = Workspace(raw)
    with pytest.raises(ValueError, match="enable_geo_fields"):
        ws.validate()


def test_community_geo_region_allowed_with_flag_on():
    ws = Workspace.load(PACKS["community"])
    assert ws.geo_enabled()
    assert any(f["type"] == "geo_region" for f in ws.raw["intake_fields"])


def test_demo_data_is_deterministic(tmp_path):
    a = pack_demo.generate_healthcare(str(tmp_path / "a"))
    b = pack_demo.generate_healthcare(str(tmp_path / "b"))
    assert hashlib.sha256(Path(a).read_bytes()).hexdigest() == hashlib.sha256(Path(b).read_bytes()).hexdigest()


@pytest.mark.parametrize("name, gen", [("healthcare", pack_demo.generate_healthcare),
                                       ("community", pack_demo.generate_community)])
def test_pack_runs_end_to_end_on_demo_data(name, gen, tmp_path):
    data = gen(str(tmp_path / "data"))
    result = pack_run.run_pack(PACKS[name], data, tmp_path / "out")
    assert (tmp_path / "out" / "personas.json").exists()
    segments = [p["segment"] for p in result["personas"]]
    expected = {s["label"] for s in Workspace.load(PACKS[name]).raw["entity_types"][0]["segments"]}
    assert set(segments) == expected
    assert sum(p["size"]["customers"] for p in result["personas"]) == result["entities"]
    for p in result["personas"]:
        assert "{" not in p["narrative"] and "}" not in p["narrative"]
        assert p["playbook"]["goal"]


def test_pack_playbook_is_used_not_retail(tmp_path):
    data = pack_demo.generate_healthcare(str(tmp_path / "data"))
    result = pack_run.run_pack(PACKS["healthcare"], data, tmp_path / "out")
    goals = {p["playbook"]["goal"] for p in result["personas"]}
    assert not any("basket" in g.lower() or "fuel" in g.lower() for g in goals)


def test_event_count_lift_flags_a_real_spike(tmp_path):
    """Event-count trigger: a spike in daily visit counts on tagged days is flagged."""
    import numpy as np
    rng = np.random.default_rng(3)
    days = pd.date_range("2026-08-01", periods=90, freq="D")
    spike_days = [pd.Timestamp("2026-09-10"), pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-22")]
    rows, pid = [], 0
    for d in days:
        n = int(rng.integers(3, 7)) + (25 if d in spike_days else 0)
        for k in range(n):
            pid += 1
            rows.append({"visit_id": f"V{pid}", "visit_date": f"{d.date()} 10:00",
                         "patient_id": f"PT{(pid % 60) + 1:03d}", "visit_type": "Routine visit",
                         "charge_amount": "100"})
    events = tmp_path / "events.csv"
    pd.DataFrame(rows).to_csv(events, index=False)
    feed = tmp_path / "feed.csv"
    pd.DataFrame({"date": [d.date().isoformat() for d in spike_days], "scope": "local",
                  "topic": "Flu clinic surge", "tags": "flu season", "source": "test", "url": ""}).to_csv(feed, index=False)

    result = pack_run.run_pack(PACKS["healthcare"], events, tmp_path / "out", trends_path=str(feed))
    lift = {row["topic"]: row for row in result["trend_lift"]}
    assert lift["Flu clinic surge"]["events"] == 3
    assert lift["Flu clinic surge"]["evidence"] == "outside normal range"
    assert lift["Flu clinic surge"]["avg_lift_pct"] > 100


def test_count_trigger_rejects_windowed_metric():
    raw = raw_of("healthcare")
    raw["triggers"]["correlate"]["metric"] = "visits_in_window"
    with pytest.raises(ValueError, match="unwindowed count_transactions"):
        Workspace(raw).validate()
