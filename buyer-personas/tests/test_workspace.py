import copy
import json
from pathlib import Path

import pytest

from personas import config
from personas.workspace import Workspace

PACK = Path(__file__).resolve().parent.parent / "packs" / "retail.json"


def base() -> dict:
    return copy.deepcopy(json.loads(PACK.read_text(encoding="utf-8")))


def load_raw(raw: dict) -> Workspace:
    ws = Workspace(raw)
    ws.validate()
    return ws


def slug(label: str) -> str:
    return label.lower().replace("-", "_").replace(" ", "_")


# ---- the shipped pack ------------------------------------------------------

def test_retail_pack_loads_and_validates():
    ws = Workspace.load(PACK)
    assert ws.raw["pack_id"] == "retail"
    assert ws.flags["enable_geo_fields"] is False


# ---- geo_region gating -----------------------------------------------------

def geo_field(**extra) -> dict:
    f = {"id": "service_area", "label": "Service area", "type": "geo_region", "usable_in_rules": False}
    f.update(extra)
    return f


def test_geo_region_rejected_when_flag_off():
    raw = base()
    raw["intake_fields"].append(geo_field())
    with pytest.raises(ValueError, match="enable_geo_fields"):
        load_raw(raw)


def test_geo_region_accepted_when_flag_on():
    raw = base()
    raw["feature_flags"]["enable_geo_fields"] = True
    raw["intake_fields"].append(geo_field())
    ws = load_raw(raw)
    assert ws.geo_enabled()


def test_unknown_feature_flag_rejected():
    raw = base()
    raw["feature_flags"]["enable_gps"] = True
    with pytest.raises(ValueError, match="Unknown feature flag"):
        load_raw(raw)


def test_non_boolean_flag_rejected():
    raw = base()
    raw["feature_flags"]["enable_geo_fields"] = "yes"
    with pytest.raises(ValueError, match="true or false"):
        load_raw(raw)


# ---- sensitive-field guard -------------------------------------------------

@pytest.mark.parametrize("fid", ["race", "religion", "health_condition", "immigration_status"])
def test_sensitive_identifier_cannot_drive_rules(fid):
    raw = base()
    raw["intake_fields"].append({"id": fid, "label": fid, "type": "category", "usable_in_rules": True})
    with pytest.raises(ValueError, match="cannot be used in segment rules"):
        load_raw(raw)


def test_sensitive_flag_cannot_drive_rules():
    raw = base()
    raw["intake_fields"].append({"id": "pet_names", "label": "x", "type": "text",
                                 "usable_in_rules": True, "sensitive": True})
    with pytest.raises(ValueError, match="cannot be used in segment rules"):
        load_raw(raw)


def test_sensitive_field_allowed_as_non_rule_intake():
    raw = base()
    raw["intake_fields"].append({"id": "religion", "label": "x", "type": "category",
                                 "usable_in_rules": False})
    load_raw(raw)


# ---- reference and structure checks ---------------------------------------

def test_rule_referencing_unknown_metric_rejected():
    raw = base()
    raw["entity_types"][0]["segments"][0]["when"]["all"][0]["metric"] = "not_a_metric"
    with pytest.raises(ValueError, match="unknown field 'not_a_metric'"):
        load_raw(raw)


def test_unknown_aggregation_rejected():
    raw = base()
    raw["metrics"].append({"id": "weird", "agg": "median_of_vibes"})
    with pytest.raises(ValueError, match="unknown aggregation"):
        load_raw(raw)


def test_two_default_segments_rejected():
    raw = base()
    seg = raw["entity_types"][0]["segments"]
    seg[0]["default"] = True
    with pytest.raises(ValueError, match="exactly one default"):
        load_raw(raw)


def test_duplicate_priority_rejected():
    raw = base()
    raw["entity_types"][0]["segments"][1]["priority"] = 1
    with pytest.raises(ValueError, match="duplicate segment priorities"):
        load_raw(raw)


def test_missing_playbook_entry_rejected():
    raw = base()
    del raw["playbook"]["by_segment"]["loyal_core"]
    with pytest.raises(ValueError, match="no playbook entry"):
        load_raw(raw)


def test_playbook_missing_required_field_rejected():
    raw = base()
    del raw["playbook"]["by_segment"]["loyal_core"]["goal"]
    with pytest.raises(ValueError, match="missing required field 'goal'"):
        load_raw(raw)


def test_missing_top_level_section_rejected():
    raw = base()
    del raw["events"]
    with pytest.raises(ValueError, match="missing required section 'events'"):
        load_raw(raw)


# ---- drift: pack must mirror personas/config.py ---------------------------

def test_pack_column_aliases_match_config():
    ws = Workspace.load(PACK)
    assert ws.raw["events"]["fields"] == config.COLUMN_ALIASES


def test_pack_category_rules_match_config():
    ws = Workspace.load(PACK)
    rules = [(r["category"], r["keywords"]) for r in ws.raw["category_normalization"]["rules"]]
    assert rules == [(c, list(k)) for c, k in config.CATEGORY_RULES]
    assert ws.raw["category_normalization"]["default"] == config.DEFAULT_CATEGORY


def test_pack_margins_match_config():
    ws = Workspace.load(PACK)
    norm = ws.raw["category_normalization"]
    assert norm["margin_rates"] == config.MARGIN_RATES
    assert norm["default_margin"] == config.DEFAULT_MARGIN


def test_pack_dayparts_match_config():
    ws = Workspace.load(PACK)
    pack = [(d["label"], d["start"], d["end"]) for d in ws.raw["dayparts"]]
    assert pack == config.DAYPARTS


def test_pack_thresholds_match_config():
    ws = Workspace.load(PACK)
    thr = dict(ws.raw["rules_thresholds"])
    expected = dict(config.RULES)
    expected.pop("min_customers_for_persona")  # engine-level, not a segment threshold
    thr["lapse_regular_window"] = tuple(thr["lapse_regular_window"])
    assert thr == expected


def test_pack_playbook_matches_config():
    ws = Workspace.load(PACK)
    by_seg = ws.raw["playbook"]["by_segment"]
    assert set(by_seg) == {slug(label) for label in config.PLAYBOOK}
    for label, entry in config.PLAYBOOK.items():
        assert by_seg[slug(label)] == entry, label


def test_pack_segment_labels_match_config_playbook():
    ws = Workspace.load(PACK)
    labels = {s["label"] for s in ws.raw["entity_types"][0]["segments"] if not s.get("default")}
    assert labels <= set(config.PLAYBOOK)
