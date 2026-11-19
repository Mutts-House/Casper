import pandas as pd
import pytest

from personas import b2b
from personas.cli import main


def _df(rows):
    return pd.DataFrame(rows, columns=["company", "invoice_date", "due_date", "paid_date", "amount", "cost", "disputed"])


def test_interp_clamps_and_interpolates():
    assert b2b.interp(-60, b2b.GROWTH_BREAKS) == 0
    assert b2b.interp(-5, b2b.GROWTH_BREAKS) == pytest.approx(43.8)
    assert b2b.interp(999, b2b.GROWTH_BREAKS) == 100
    assert b2b.interp(0, b2b.GROWTH_BREAKS) == 50
    assert b2b.interp(0.30, b2b.CONCENTRATION_BREAKS) == 50  # midpoint between 0.10->100 and 0.50->0


def test_missing_required_column_is_an_error():
    with pytest.raises(ValueError, match="missing required"):
        b2b.load_invoices(pd.DataFrame({"company": ["A"], "amount": [10]}))


def test_bad_rows_dropped_with_warning():
    df, warnings = b2b.load_invoices(_df([
        ["A", "2026-01-05", None, None, 100, None, 0],
        ["", "2026-01-05", None, None, 100, None, 0],
        ["B", "not a date", None, None, 100, None, 0],
        ["C", "2026-01-05", None, None, -4, None, 0],
    ]))
    assert list(df["company"]) == ["A"]
    assert warnings and "Dropped 3" in warnings[0]


def test_reliability_needs_minimum_due_invoices():
    df, _ = b2b.load_invoices(_df([
        ["A", "2026-01-05", "2026-02-04", "2026-02-01", 100, 50, 0],
        ["A", "2026-02-05", "2026-03-06", "2026-03-06", 100, 50, 0],
    ]))
    card = b2b.build_scorecards(df, "2026-06-01")["scorecards"][0]
    assert card["reliability"]["score"] is None
    assert "Not scored" in card["reliability"]["reason"]


def test_reliability_counts_unpaid_past_due_as_late():
    rows = [
        ["A", "2026-01-05", "2026-02-04", "2026-02-01", 100, 50, 0],  # on time
        ["A", "2026-01-10", "2026-02-09", "2026-03-20", 100, 50, 0],  # late
        ["A", "2026-01-15", "2026-02-14", None, 100, 50, 0],          # unpaid, past due
        ["A", "2026-01-20", "2026-02-19", "2026-02-10", 100, 50, 0],  # on time
    ]
    df, _ = b2b.load_invoices(_df(rows))
    rel = b2b.build_scorecards(df, "2026-06-01")["scorecards"][0]["reliability"]
    assert rel["due_invoices"] == 4
    assert rel["on_time_rate"] == 0.5
    assert rel["score"] == 50.0


def test_growth_unscored_without_prior_baseline():
    df, _ = b2b.load_invoices(_df([
        ["New", "2026-05-01", None, None, 500, 300, 0],
        ["New", "2026-06-01", None, None, 500, 300, 0],
        ["New", "2026-07-01", None, None, 500, 300, 0],
    ]))
    card = b2b.build_scorecards(df, "2026-08-01")["scorecards"][0]
    assert card["growth"]["score"] is None
    assert card["growth"]["spend_prior_12m"] == 0.0


def test_missing_cost_uses_default_margin_and_is_tagged_inferred():
    df, _ = b2b.load_invoices(_df([
        ["A", "2026-05-01", None, None, 1000, None, 0],
    ]))
    value = b2b.build_scorecards(df, "2026-06-01")["scorecards"][0]["value"]
    assert value["provenance"] == "inferred"
    assert value["gross_profit_12m"] == pytest.approx(1000 * b2b.DEFAULT_MARGIN)


def test_concentration_lower_share_scores_higher():
    df, _ = b2b.load_invoices(_df([
        ["Big", "2026-05-01", None, None, 9000, 5000, 0],
        ["Small", "2026-05-01", None, None, 1000, 500, 0],
    ]))
    cards = {c["company"]: c for c in b2b.build_scorecards(df, "2026-06-01")["scorecards"]}
    assert cards["Small"]["concentration"]["share_of_spend"] == pytest.approx(0.1)
    assert cards["Small"]["concentration"]["score"] == 100
    assert cards["Big"]["concentration"]["score"] < cards["Small"]["concentration"]["score"]


def test_each_score_carries_reason_and_provenance():
    df, _ = b2b.load_invoices(_df([
        ["A", "2026-05-01", "2026-06-01", "2026-05-25", 800, 400, 1],
        ["A", "2026-05-10", "2026-06-10", None, 800, 400, 0],
        ["A", "2026-05-20", "2026-06-20", "2026-06-18", 800, 400, 0],
    ]))
    card = b2b.build_scorecards(df, "2026-07-01")["scorecards"][0]
    for key in ("value", "reliability", "growth", "concentration"):
        assert card[key]["reason"]
    assert card["concentration"]["disputes_12m"] == 1


def test_cli_b2b_writes_outputs(tmp_path, capsys):
    rc = main(["b2b", "--input", "sample_data/b2b_invoices_demo.csv", "--out", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "b2b_scores.csv").exists()
    assert (tmp_path / "b2b_scorecards.json").exists()
    assert "Scored" in capsys.readouterr().out
