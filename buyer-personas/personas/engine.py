"""Pack-driven rule engine.

Every domain decision the engine makes (column aliases, category keywords,
dayparts, margins, segment rules, playbook text, narrative wording) is read
from a workspace pack. The default pack is packs/retail.json. Code in this
module contains no industry-specific values.
"""

from __future__ import annotations

import operator
from functools import lru_cache
from pathlib import Path

from .workspace import Workspace

DEFAULT_PACK = Path(__file__).resolve().parent.parent / "packs" / "retail.json"

_OPS = {
    ">=": operator.ge, ">": operator.gt,
    "<=": operator.le, "<": operator.lt,
    "==": operator.eq, "!=": operator.ne,
}


@lru_cache(maxsize=8)
def _load(path: str) -> Workspace:
    return Workspace.load(path)


def pack(path: str | Path | None = None) -> Workspace:
    """Load and validate a pack. Cached per path; the default is the retail pack."""
    return _load(str(Path(path) if path else DEFAULT_PACK))


# ---- accessors used by ingest, segment, profile and trends ----------------

def column_aliases(ws: Workspace | None = None) -> dict[str, list[str]]:
    ws = ws or pack()
    return {k: list(v) for k, v in ws.raw["events"]["fields"].items()}


def category_rules(ws: Workspace | None = None) -> list[tuple[str, list[str]]]:
    ws = ws or pack()
    return [(r["category"], list(r["keywords"])) for r in ws.raw["category_normalization"]["rules"]]


def default_category(ws: Workspace | None = None) -> str:
    return (ws or pack()).raw["category_normalization"]["default"]


def margin_rates(ws: Workspace | None = None) -> dict[str, float]:
    return dict((ws or pack()).raw["category_normalization"]["margin_rates"])


def default_margin(ws: Workspace | None = None) -> float:
    return (ws or pack()).raw["category_normalization"]["default_margin"]


def dayparts(ws: Workspace | None = None) -> list[tuple[str, int, int]]:
    return [(d["label"], d["start"], d["end"]) for d in (ws or pack()).raw["dayparts"]]


def metric(metric_id: str, ws: Workspace | None = None) -> dict:
    ws = ws or pack()
    for m in ws.raw["metrics"]:
        if m["id"] == metric_id:
            return dict(m)
    raise KeyError(f"No metric '{metric_id}' in this pack")


def metric_window(metric_id: str, ws: Workspace | None = None) -> tuple[int, int]:
    """Day offsets (from, to) of a windowed count metric, e.g. (120, 30)."""
    ws = ws or pack()
    for m in ws.raw["metrics"]:
        if m["id"] == metric_id and "window_days" in m:
            w = m["window_days"]
            return int(w["from"]), int(w["to"])
    raise KeyError(f"Metric '{metric_id}' has no window_days in this pack")


def entity_plural(ws: Workspace | None = None, entity_id: str = "customer") -> str:
    return (ws or pack()).entity(entity_id)["label_plural"].lower()


def narrative_template(ws: Workspace | None = None) -> str:
    return (ws or pack()).raw["narrative"]["template"]


def playbook_for(segment_label: str, ws: Workspace | None = None, entity_id: str = "customer") -> dict:
    """Playbook entry for a segment, looked up by its display label. {} if unknown."""
    ws = ws or pack()
    for seg in ws.entity(entity_id)["segments"]:
        if seg["label"] == segment_label:
            return dict(ws.raw["playbook"]["by_segment"].get(seg["id"], {}))
    return {}


# ---- rule evaluation -------------------------------------------------------

def _holds(when: dict, row) -> bool:
    if "all" in when:
        return all(_holds(w, row) for w in when["all"])
    if "any" in when:
        return any(_holds(w, row) for w in when["any"])
    return bool(_OPS[when["op"]](row[when["metric"]], when["value"]))


def assign_segment(row, entity_id: str = "customer", ws: Workspace | None = None) -> str:
    """Return the display label of the first matching segment (priority order).

    Falls through to the entity's default segment when no rule matches.
    """
    ws = ws or pack()
    segments = ws.entity(entity_id)["segments"]
    ordered = sorted(
        (s for s in segments if not s.get("default")),
        key=lambda s: s["priority"],
    )
    for seg in ordered:
        if _holds(seg["when"], row):
            return seg["label"]
    default = next(s for s in segments if s.get("default"))
    return default["label"]


def segment_labels(entity_id: str = "customer", ws: Workspace | None = None) -> list[str]:
    return [s["label"] for s in (ws or pack()).entity(entity_id)["segments"]]


# ---- trigger (trend-correlation) settings ---------------------------------

def trigger_settings(ws: Workspace | None = None) -> dict:
    """Trend-feed column mapping and lift-evidence settings from the pack."""
    t = (ws or pack()).raw["triggers"]
    c = t["correlate"]
    return {
        "fields": dict(t["fields"]),
        "metric": c["metric"],
        "baseline": c["baseline"],
        "min_events": int(c["min_events"]),
        "z_threshold": float(c["z_threshold"]),
        "match": c["match"],
    }
