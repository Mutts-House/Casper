"""B2B company scorecards: value, reliability, growth, and concentration.

Each score is reported on its own, with the raw inputs it came from, the
thresholds used to turn them into 0-100, a provenance tag, and a one-line
reason. Nothing is blended into a total, so a reviewer can see which input
drove each result and make the call.

Score meaning: higher is always better for the store.
- value:          12-month gross profit from the company
- reliability:    share of due invoices paid on or before the due date
- growth:         12-month spend against the prior 12 months
- concentration:  share of your 12-month spend going to this company
                  (lower share scores higher, because dependence is the risk)

Provenance:
- observed: the figure came straight from your invoice file
- inferred: the engine filled it in (for example, a default margin)
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

# Piecewise-linear breakpoints (input, score). Kept visible so the cutoffs can be audited.
VALUE_GP_BREAKS = [(0, 0), (1_000, 25), (5_000, 50), (20_000, 75), (50_000, 100)]  # annual gross profit, USD
GROWTH_BREAKS = [(-50, 0), (-20, 25), (0, 50), (20, 75), (50, 100)]                 # percent change vs prior 12 months
CONCENTRATION_BREAKS = [(0.10, 100), (0.50, 0)]                                     # share of 12-month spend
MIN_DUE_INVOICES = 3       # below this, reliability is not scored
DEFAULT_MARGIN = 0.30      # used only when cost is missing; always tagged inferred

REQUIRED = ["company", "invoice_date", "amount"]
OPTIONAL = ["due_date", "paid_date", "cost", "disputed"]


def interp(x: float, breaks: list[tuple[float, float]]) -> float:
    """Linear interpolation across breakpoints, clamped at both ends."""
    if x <= breaks[0][0]:
        return float(breaks[0][1])
    if x >= breaks[-1][0]:
        return float(breaks[-1][1])
    for (x0, y0), (x1, y1) in zip(breaks, breaks[1:]):
        if x0 <= x <= x1:
            return round(y0 + (y1 - y0) * (x - x0) / (x1 - x0), 1)
    raise ValueError("breakpoints must be sorted by input")


def load_invoices(source) -> tuple[pd.DataFrame, list[str]]:
    """Load invoices from a CSV path or DataFrame. Returns (clean frame, warnings)."""
    df = pd.read_csv(source) if not isinstance(source, pd.DataFrame) else source.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Invoice file is missing required column(s): {', '.join(missing)}")
    for c in OPTIONAL:
        if c not in df.columns:
            df[c] = pd.NA

    warnings: list[str] = []
    df["invoice_date"] = pd.to_datetime(df["invoice_date"], errors="coerce")
    df["due_date"] = pd.to_datetime(df["due_date"], errors="coerce")
    df["paid_date"] = pd.to_datetime(df["paid_date"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df["cost"] = pd.to_numeric(df["cost"], errors="coerce")
    df["disputed"] = pd.to_numeric(df["disputed"], errors="coerce").fillna(0)
    df["company"] = df["company"].astype("string").str.strip()

    bad = (df["company"].isna() | (df["company"] == "") | df["invoice_date"].isna()
           | df["amount"].isna() | (df["amount"] <= 0))
    if bad.any():
        warnings.append(f"Dropped {int(bad.sum())} row(s) with a missing company, invoice date, or amount, or a zero or negative amount.")
    df = df[~bad].copy()
    if df.empty:
        raise ValueError("No usable invoice rows after cleaning.")
    return df, warnings


def _reliability(g: pd.DataFrame, as_of: pd.Timestamp) -> dict:
    due = g[g["due_date"].notna() & (g["due_date"] <= as_of)].copy()
    out = {"due_invoices": int(len(due)), "on_time_rate": None, "median_days_late": None,
           "score": None, "provenance": "observed",
           "reason": ""}
    if len(due) < MIN_DUE_INVOICES:
        out["reason"] = f"Not scored: only {len(due)} invoice(s) with a due date on or before the analysis date (need {MIN_DUE_INVOICES})."
        return out
    paid = due["paid_date"].notna() & (due["paid_date"] <= as_of)
    end = due["paid_date"].where(paid, as_of)
    days_late = (end - due["due_date"]).dt.days
    on_time = paid & (due["paid_date"] <= due["due_date"])
    rate = float(on_time.mean())
    out["on_time_rate"] = round(rate, 3)
    out["median_days_late"] = float(days_late.median())
    out["score"] = round(rate * 100, 1)
    out["reason"] = (f"{rate:.0%} of {len(due)} due invoices paid on time; "
                     f"median {days_late.median():+.0f} days vs due date (unpaid counted to analysis date).")
    return out


def score_companies(df: pd.DataFrame, as_of=None) -> pd.DataFrame:
    """One row per company with each score, its inputs, and its reason."""
    as_of = pd.Timestamp(as_of) if as_of is not None else df["invoice_date"].max()
    df = df[df["invoice_date"] <= as_of]
    start12 = as_of - pd.DateOffset(months=12)
    start24 = as_of - pd.DateOffset(months=24)
    cur_all = df[df["invoice_date"] > start12]
    prior_all = df[(df["invoice_date"] > start24) & (df["invoice_date"] <= start12)]
    total_cur = float(cur_all["amount"].sum())

    cards = []
    for company, g in df.groupby("company"):
        c = cur_all[cur_all["company"] == company]
        p = prior_all[prior_all["company"] == company]
        card: dict = {"company": company, "invoices_all_time": int(len(g))}

        # Value: 12-month gross profit. Uses cost where present, default margin otherwise.
        known = c["cost"].notna()
        gp = (c["amount"] - c["cost"]).where(known, c["amount"] * DEFAULT_MARGIN)
        gp_total = float(gp.sum())
        if len(c) == 0:
            value = {"score": None, "gross_profit_12m": 0.0,
                     "provenance": "n/a", "reason": "No invoices in the last 12 months."}
        else:
            prov = "observed" if known.all() else ("inferred" if not known.any() else "observed+inferred")
            value = {"score": interp(gp_total, VALUE_GP_BREAKS), "gross_profit_12m": round(gp_total, 2),
                     "provenance": prov,
                     "reason": f"${gp_total:,.0f} gross profit over 12 months from {len(c)} invoice(s)"
                               + ("" if prov == "observed" else f"; default {DEFAULT_MARGIN:.0%} margin used for {int((~known).sum())} invoice(s)") + "."}
        card["value"] = value

        # Reliability: due-date performance over all history up to as_of.
        card["reliability"] = _reliability(g, as_of)

        # Growth: 12-month spend against prior 12 months.
        spend12 = float(c["amount"].sum())
        spend_prior = float(p["amount"].sum())
        if spend_prior == 0:
            growth = {"score": None, "spend_12m": round(spend12, 2), "spend_prior_12m": 0.0,
                      "change_pct": None, "provenance": "observed",
                      "reason": "Not scored: no spend in the prior 12 months, so there is no baseline."}
        else:
            pct = (spend12 - spend_prior) / spend_prior * 100
            growth = {"score": interp(pct, GROWTH_BREAKS), "spend_12m": round(spend12, 2),
                      "spend_prior_12m": round(spend_prior, 2), "change_pct": round(pct, 1),
                      "provenance": "observed",
                      "reason": f"Spend {pct:+.1f}% vs prior 12 months (${spend_prior:,.0f} -> ${spend12:,.0f})."}
        card["growth"] = growth

        # Concentration: share of your 12-month spend that goes to this company.
        share = spend12 / total_cur if total_cur > 0 else 0.0
        disputes = int(c["disputed"].sum())
        card["concentration"] = {
            "score": interp(share, CONCENTRATION_BREAKS) if total_cur > 0 else None,
            "share_of_spend": round(share, 4),
            "disputes_12m": disputes,
            "provenance": "observed",
            "reason": f"{share:.1%} of 12-month spend; {disputes} disputed invoice(s) in 12 months (reported, not scored).",
        }
        cards.append(card)

    return pd.DataFrame(cards)


def build_scorecards(df: pd.DataFrame, as_of=None) -> dict:
    """Full audit output: thresholds, as-of date, and one scorecard per company."""
    as_of_ts = pd.Timestamp(as_of) if as_of is not None else df["invoice_date"].max()
    table = score_companies(df, as_of_ts)
    cards = []
    for _, row in table.iterrows():
        card = row.to_dict()
        cards.append({
            "company": card["company"],
            "value": card["value"],
            "reliability": card["reliability"],
            "growth": card["growth"],
            "concentration": card["concentration"],
            "invoices_all_time": card["invoices_all_time"],
        })
    return {
        "as_of": as_of_ts.date().isoformat(),
        "thresholds": {
            "value_gross_profit_usd": VALUE_GP_BREAKS,
            "growth_change_pct": GROWTH_BREAKS,
            "concentration_share": CONCENTRATION_BREAKS,
            "min_due_invoices_for_reliability": MIN_DUE_INVOICES,
            "default_margin_when_cost_missing": DEFAULT_MARGIN,
        },
        "scorecards": cards,
    }


def flatten(scorecards: dict) -> pd.DataFrame:
    """Flat table for spreadsheets: one row per company, one column per input and score."""
    rows = []
    for c in scorecards["scorecards"]:
        rows.append({
            "company": c["company"],
            "value_score": c["value"]["score"], "value_gross_profit_12m": c["value"]["gross_profit_12m"],
            "value_provenance": c["value"]["provenance"],
            "reliability_score": c["reliability"]["score"], "on_time_rate": c["reliability"]["on_time_rate"],
            "median_days_late": c["reliability"]["median_days_late"], "due_invoices": c["reliability"]["due_invoices"],
            "growth_score": c["growth"]["score"], "spend_12m": c["growth"]["spend_12m"],
            "spend_prior_12m": c["growth"]["spend_prior_12m"], "change_pct": c["growth"]["change_pct"],
            "concentration_score": c["concentration"]["score"], "share_of_spend": c["concentration"]["share_of_spend"],
            "disputes_12m": c["concentration"]["disputes_12m"],
            "value_reason": c["value"]["reason"], "reliability_reason": c["reliability"]["reason"],
            "growth_reason": c["growth"]["reason"], "concentration_reason": c["concentration"]["reason"],
        })
    return pd.DataFrame(rows)


def write_outputs(df: pd.DataFrame, out_dir: str | Path, as_of=None) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    scorecards = build_scorecards(df, as_of)
    (out / "b2b_scorecards.json").write_text(json.dumps(scorecards, indent=2, default=str), encoding="utf-8")
    flatten(scorecards).to_csv(out / "b2b_scores.csv", index=False)
    return scorecards
