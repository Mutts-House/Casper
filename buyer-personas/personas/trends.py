"""Trend feeds: match trends to personas, and measure whether they moved sales.

A trend feed is a CSV with one row per event or topic:
    date, scope, topic, tags, source, url
  - date:   YYYY-MM-DD the trend peaked or the event happened
  - scope:  "local" (city, school calendar, weather, events) or "mainstream"
  - tags:   semicolon-separated, matched against playbook tags, e.g. "commute;gas prices"

Any other source (Google Trends, an events API, a news RSS) can be added by
subclassing TrendSource and returning the same columns.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from . import engine

MIN_EVENTS_FOR_EVIDENCE = 3  # below this, a lift number is anecdote, not evidence


class TrendSource(ABC):
    """Plug-in point for live trend connectors."""

    @abstractmethod
    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        """Return rows with columns: date, scope, topic, tags, source, url."""


class CsvTrendSource(TrendSource):
    def __init__(self, path: str):
        self.path = path

    def fetch(self, start, end) -> pd.DataFrame:
        df = pd.read_csv(self.path)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        return df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)


def _tags(raw: object) -> set[str]:
    if not isinstance(raw, str):
        return set()
    return {t.strip().lower() for t in raw.split(";") if t.strip()}


def match_trends_to_segments(trends: pd.DataFrame, segments: list[str]) -> dict[str, list[dict]]:
    """For each segment, list trends whose tags overlap the segment's playbook tags."""
    out: dict[str, list[dict]] = {}
    for seg in segments:
        seg_tags = {t.lower() for t in engine.playbook_for(seg).get("tags", [])}
        hits = []
        for _, row in trends.iterrows():
            overlap = seg_tags & _tags(row.get("tags"))
            if overlap:
                hits.append({
                    "topic": row["topic"],
                    "scope": row.get("scope", ""),
                    "date": row["date"].date().isoformat(),
                    "matched_tags": sorted(overlap),
                    "source": row.get("source", ""),
                })
        out[seg] = hits
    return out


def measure_trend_lift(
    daily_revenue: pd.DataFrame,
    trends: pd.DataFrame,
    segment_daily: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Compare revenue on each trend date to the usual revenue for that weekday.

    Baseline = median revenue on the same weekday, excluding trend dates.
    daily_revenue: columns date, revenue.
    segment_daily (optional): columns date, segment, revenue.
    Returns one row per trend topic with lift %, a z-score against normal day-to-day
    variation for that weekday, and an evidence flag. A lift only counts as a signal
    when the average z-score is at least 2 AND there are at least 3 events.
    """
    if daily_revenue.empty or trends.empty:
        return pd.DataFrame(columns=["topic", "events", "avg_lift_pct", "avg_z_score", "evidence", "top_segment"])

    rev = daily_revenue.copy()
    rev["date"] = pd.to_datetime(rev["date"]).dt.normalize()
    rev = rev.groupby("date", as_index=False)["revenue"].sum()
    trend_dates = set(pd.to_datetime(trends["date"]).dt.normalize())

    # Wide table: one row per day, one column per segment (plus total).
    wide = rev.set_index("date")[["revenue"]].rename(columns={"revenue": "_total"})
    if segment_daily is not None and not segment_daily.empty:
        sd = segment_daily.copy()
        sd["date"] = pd.to_datetime(sd["date"]).dt.normalize()
        seg_wide = sd.pivot_table(index="date", columns="segment", values="revenue", aggfunc="sum", fill_value=0)
        wide = wide.join(seg_wide, how="left").fillna(0)

    # Baseline per column and weekday, using non-trend days only.
    non_trend = wide[~wide.index.isin(trend_dates)]
    base = non_trend.groupby(non_trend.index.dayofweek).median()
    spread = non_trend.groupby(non_trend.index.dayofweek).std().fillna(0)  # normal day-to-day variation

    rows = []
    trends_dated = trends.assign(date=pd.to_datetime(trends["date"]).dt.normalize())
    for topic, grp in trends_dated.groupby("topic"):
        lifts, zs = [], []
        seg_lifts: dict[str, list[float]] = {}
        for d in pd.to_datetime(grp["date"].unique()):
            if d not in wide.index:
                continue
            row = wide.loc[d]
            weekday_base = base.loc[d.dayofweek] if d.dayofweek in base.index else None
            if weekday_base is None or weekday_base["_total"] <= 0:
                continue
            lifts.append((row["_total"] / weekday_base["_total"] - 1) * 100)
            sd = spread.loc[d.dayofweek, "_total"]
            if sd > 0:
                zs.append((row["_total"] - weekday_base["_total"]) / sd)
            for col in wide.columns:
                if col == "_total" or weekday_base[col] <= 0:
                    continue
                seg_lifts.setdefault(col, []).append((row[col] / weekday_base[col] - 1) * 100)

        if not lifts:
            continue
        top_seg = ""
        if seg_lifts:
            top_seg = max(seg_lifts, key=lambda s: sum(seg_lifts[s]) / len(seg_lifts[s]))
        avg_z = sum(zs) / len(zs) if zs else 0.0
        if len(lifts) < MIN_EVENTS_FOR_EVIDENCE:
            evidence = "too few events"
        elif abs(avg_z) >= 2:
            evidence = "outside normal range"
        else:
            evidence = "within normal variation"
        rows.append({
            "topic": topic,
            "events": len(lifts),
            "avg_lift_pct": round(sum(lifts) / len(lifts), 1),
            "avg_z_score": round(avg_z, 2),
            "evidence": evidence,
            "top_segment": top_seg,
        })
    cols = ["topic", "events", "avg_lift_pct", "avg_z_score", "evidence", "top_segment"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols).sort_values("avg_lift_pct", ascending=False)
