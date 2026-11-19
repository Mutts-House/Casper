"""Customer features and rule-based segmentation.

Rules are used instead of k-means on purpose: a small-store manager can read
exactly why a customer landed in a segment, and the rules can be tuned in
config.RULES without retraining anything. k-means can be added later for
larger datasets as an alternative segmenter.
"""

from __future__ import annotations

import pandas as pd

from .config import DAYPARTS, DEFAULT_MARGIN, MARGIN_RATES, RULES

DAYPART_LABEL = {}
for label, start, end in DAYPARTS:
    for h in range(start, end):
        DAYPART_LABEL[h] = label


def to_transactions(lines: pd.DataFrame) -> pd.DataFrame:
    """Collapse line items into one row per transaction."""
    tx = (
        lines.groupby(["transaction_id", "customer_key"], dropna=False)
        .agg(timestamp=("timestamp", "min"), amount=("amount", "sum"))
        .reset_index()
    )
    tx["hour"] = tx["timestamp"].dt.hour
    tx["daypart"] = tx["hour"].map(DAYPART_LABEL).fillna("other")
    tx["is_weekend"] = tx["timestamp"].dt.dayofweek >= 5
    return tx


def customer_features(
    lines: pd.DataFrame,
    as_of: pd.Timestamp | None = None,
    margins: dict[str, float] | None = None,
) -> pd.DataFrame:
    """One row per identified customer, with behavior metrics."""
    identified = lines[lines["customer_key"].notna()]
    if identified.empty:
        return pd.DataFrame()

    as_of = as_of or lines["timestamp"].max()
    tx = to_transactions(identified)
    span_days = max((as_of - lines["timestamp"].min()).days, 1)
    months = max(span_days / 30.0, 1 / 30.0)

    g = tx.groupby("customer_key")
    feats = pd.DataFrame({
        "visits": g.size(),
        "spend": g["amount"].sum(),
        "first_seen": g["timestamp"].min(),
        "last_seen": g["timestamp"].max(),
    })
    feats["avg_basket"] = feats["spend"] / feats["visits"]
    feats["visits_per_month"] = feats["visits"] / months
    feats["recency_days"] = (as_of - feats["last_seen"]).dt.days

    # Share of transactions by daypart and weekend.
    dp = pd.crosstab(tx["customer_key"], tx["daypart"], normalize="index")
    for part in ["commute_am", "commute_pm", "late_night", "midday", "afternoon"]:
        feats[f"share_{part}"] = dp[part] if part in dp else 0.0
    feats["share_weekend"] = g["is_weekend"].mean()

    # Visits during the "regular period" (before the quiet period). Used to tell
    # a customer who used to come often from one who was never a regular.
    start_days, end_days = RULES["lapse_regular_window"]
    in_window = tx["timestamp"].between(as_of - pd.Timedelta(days=start_days), as_of - pd.Timedelta(days=end_days))
    feats["visits_regular_period"] = tx[in_window].groupby("customer_key").size()
    feats["visits_regular_period"] = feats["visits_regular_period"].reindex(feats.index).fillna(0).astype(int)

    # Share of spend by category.
    cat = identified.pivot_table(
        index="customer_key", columns="category", values="amount", aggfunc="sum", fill_value=0.0
    )
    totals = cat.sum(axis=1).replace(0, 1)
    for label in ["fuel", "food_service", "beverage", "snack", "restricted", "other"]:
        feats[f"share_{label}"] = (cat[label] / totals) if label in cat else 0.0

    # Restricted (age-gated) items never define a persona's "top category":
    # they would otherwise win on count and misdescribe the customer.
    non_restricted = cat.drop(columns=["restricted"], errors="ignore")
    has_sales = non_restricted.sum(axis=1) > 0
    feats["top_category"] = non_restricted.idxmax(axis=1).where(has_sales, "other")

    # Gross profit: the value measure. Revenue alone overweights low-margin fuel.
    rates = {**MARGIN_RATES, **(margins or {})}
    gp_lines = identified.assign(
        gross=identified["amount"] * identified["category"].map(lambda c: rates.get(c, DEFAULT_MARGIN))
    )
    feats["gross_profit"] = gp_lines.groupby("customer_key")["gross"].sum().reindex(feats.index).fillna(0.0)
    feats["peak_daypart"] = dp.idxmax(axis=1) if not dp.empty else "other"
    feats["share_commute"] = feats["share_commute_am"] + feats["share_commute_pm"]
    return feats.reset_index()


def assign_segment(row: pd.Series) -> str:
    """Priority-ordered rules. The first match wins."""
    r = RULES
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


def segment_customers(feats: pd.DataFrame) -> pd.DataFrame:
    if feats.empty:
        return feats
    feats = feats.copy()
    feats["segment"] = feats.apply(assign_segment, axis=1)
    return feats
