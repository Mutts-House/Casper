import copy
import itertools
import json
from pathlib import Path

import pandas as pd
import pytest

from personas import config, engine, profile
from personas.workspace import Workspace

PACK = Path(__file__).resolve().parent.parent / "packs" / "retail.json"


def legacy_assign(row) -> str:
    """Frozen copy of the original hard-coded rules, used as the oracle."""
    r = config.RULES
    if row["visits_regular_period"] >= r["lapse_min_visits"] and row["recency_days"] >= r["lapse_recency_days"]:
        return "Lapsing Regulars"
    if row["visits"] >= r["loyal_min_visits"] and row["recency_days"] < r["loyal_max_recency_days"]:
        return "Loyal Core"
    enough = row["visits"] >= r["pattern_min_visits"]
    if enough and row["share_late_night"] >= r["late_night_share"]:
        return "Late-Night Regulars"
    if enough and row["share_fuel"] >= r["fuel_share"] and row["share_commute"] >= r["commute_share"]:
        return "Fuel-and-Go Commuters"
    if enough and row["share_food_service"] >= r["food_share"] and row["share_midday"] >= r["lunch_share"]:
        return "Lunch Grab-and-Go"
    return "Occasional Visitors"


def grid_rows():
    """Boundary-heavy grid covering every rule's threshold on both sides."""
    visits = [0, 2, 3, 11, 12, 13]
    regular = [0, 2, 3, 4]
    recency = [0, 29, 30, 31, 120]
    late = [0.0, 0.34, 0.35, 0.9]
    fuel = [0.39, 0.40, 0.8]
    commute = [0.49, 0.50]
    food = [0.39, 0.40]
    midday = [0.39, 0.40]
    for combo in itertools.product(visits, regular, recency, late, fuel, commute, food, midday):
        v, reg, rec, ln, fu, co, fo, md = combo
        yield pd.Series({
            "visits": v, "visits_regular_period": reg, "recency_days": rec,
            "share_late_night": ln, "share_fuel": fu, "share_commute": co,
            "share_food_service": fo, "share_midday": md,
        })


def test_pack_rules_match_legacy_rules_on_grid():
    checked = 0
    for row in grid_rows():
        assert engine.assign_segment(row) == legacy_assign(row), dict(row)
        checked += 1
    assert checked > 10_000


def test_engine_reads_thresholds_from_pack_not_config(tmp_path):
    raw = json.loads(PACK.read_text(encoding="utf-8"))
    loyal = next(s for s in raw["entity_types"][0]["segments"] if s["id"] == "loyal_core")
    loyal["when"]["all"][0]["value"] = 50  # raise the visit bar for Loyal Core
    edited = tmp_path / "pack.json"
    edited.write_text(json.dumps(raw))
    ws = Workspace.load(edited)
    row = pd.Series({"visits": 20, "visits_regular_period": 0, "recency_days": 1,
                     "share_late_night": 0, "share_fuel": 0, "share_commute": 0,
                     "share_food_service": 0, "share_midday": 0})
    assert engine.assign_segment(row) == "Loyal Core"            # default pack
    assert engine.assign_segment(row, ws=ws) == "Occasional Visitors"  # edited pack


def test_default_segment_used_when_nothing_matches():
    row = pd.Series({"visits": 0, "visits_regular_period": 0, "recency_days": 0,
                     "share_late_night": 0, "share_fuel": 0, "share_commute": 0,
                     "share_food_service": 0, "share_midday": 0})
    assert engine.assign_segment(row) == "Occasional Visitors"


def test_playbook_lookup_matches_config_by_label():
    for label, entry in config.PLAYBOOK.items():
        assert engine.playbook_for(label) == entry, label
    assert engine.playbook_for("Not A Segment") == {}


def test_accessors_match_config():
    assert engine.column_aliases() == config.COLUMN_ALIASES
    assert engine.category_rules() == [(c, list(k)) for c, k in config.CATEGORY_RULES]
    assert engine.dayparts() == config.DAYPARTS
    assert engine.margin_rates() == config.MARGIN_RATES
    assert engine.default_margin() == config.DEFAULT_MARGIN
    assert engine.default_category() == config.DEFAULT_CATEGORY
    assert engine.metric_window("visits_regular_period") == config.RULES["lapse_regular_window"]


def test_template_narrative_matches_legacy_wording():
    p = {
        "segment": "Loyal Core",
        "size": {"share_of_identified_revenue_pct": 41.7, "customers": 12},
        "behavior": {"avg_visits_in_window": 17.9, "avg_basket_usd": 9.35,
                     "peak_daypart": "evening commute"},
        "playbook": {"goal": "G.", "offer_angle": "O.", "channel": "C", "timing": "T"},
    }
    legacy = (
        "Loyal Core make up 41.7% of identified revenue "
        "(12 customers). They average 17.9 visits in the window, "
        "with a $9.35 average basket, and most often shop during evening commute. "
        "Goal: G. Suggested offer: O. "
        "Channel: C, timing: T."
    )
    assert profile.template_narrative(p) == legacy


def test_narrative_template_is_sourced_from_pack(tmp_path):
    raw = json.loads(PACK.read_text(encoding="utf-8"))
    raw["narrative"]["template"] = "{segment} edited: {customers}."
    edited = tmp_path / "pack.json"
    edited.write_text(json.dumps(raw))
    assert engine.narrative_template(Workspace.load(edited)).startswith("{segment} edited")
