"""Workspace configuration (a "domain pack") loading and validation.

A workspace config defines the entity types, metrics, segment rules, intake
fields, playbook, triggers, and narrative for one deployment. The engine reads
it; no industry-specific logic lives in code. Validation runs before anything
else, so a bad config fails with a clear message instead of a wrong report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Feature flags are off unless a workspace turns them on. Unknown flags are rejected.
KNOWN_FLAGS = {"enable_geo_fields": False}

# Intake fields that may never drive segment rules or outreach decisions.
SENSITIVE_IDS = {
    "race", "ethnicity", "religion", "disability", "health_condition",
    "sexual_orientation", "gender_identity", "immigration_status",
}

INTAKE_TYPES = {"category", "text", "number", "geo_region"}
ALLOWED_OPS = {">=", ">", "<=", "<", "==", "!="}
TRIGGER_BASELINES = {"same_weekday"}
TRIGGER_MATCHES = {"tag_overlap_with_segment_playbook"}
ALLOWED_AGGS = {
    "count_transactions", "days_since_last", "sum", "sum_margin_weighted",
    "share_of_transactions", "share_of_amount",
}


@dataclass
class Workspace:
    raw: dict

    @classmethod
    def load(cls, path: str | Path) -> "Workspace":
        with open(path, encoding="utf-8") as f:
            ws = cls(json.load(f))
        ws.validate()
        return ws

    # ---- flags -------------------------------------------------------------
    @property
    def flags(self) -> dict:
        merged = dict(KNOWN_FLAGS)
        merged.update(self.raw.get("feature_flags", {}))
        return merged

    def geo_enabled(self) -> bool:
        return bool(self.flags["enable_geo_fields"])

    # ---- lookups -----------------------------------------------------------
    def entity(self, entity_id: str) -> dict:
        for et in self.raw["entity_types"]:
            if et["id"] == entity_id:
                return et
        raise KeyError(f"No entity type '{entity_id}' in workspace")

    def metric_ids(self) -> set[str]:
        return {m["id"] for m in self.raw["metrics"]}

    def rule_intake_ids(self) -> set[str]:
        return {f["id"] for f in self.raw.get("intake_fields", []) if f.get("usable_in_rules")}

    # ---- validation --------------------------------------------------------
    def validate(self) -> None:
        for key in ("pack_id", "pack_version", "workspace", "events", "metrics", "entity_types", "playbook", "triggers"):
            if key not in self.raw:
                raise ValueError(f"Workspace config is missing required section '{key}'")

        self._validate_flags()
        self._validate_metrics()
        self._validate_intake()
        self._validate_triggers()
        for et in self.raw["entity_types"]:
            self._validate_entity(et)

    def _validate_flags(self) -> None:
        for name, value in self.raw.get("feature_flags", {}).items():
            if name not in KNOWN_FLAGS:
                raise ValueError(f"Unknown feature flag '{name}'")
            if not isinstance(value, bool):
                raise ValueError(f"Feature flag '{name}' must be true or false")

    def _validate_metrics(self) -> None:
        seen = set()
        for m in self.raw["metrics"]:
            if m["id"] in seen:
                raise ValueError(f"Duplicate metric id '{m['id']}'")
            seen.add(m["id"])
            if m["agg"] not in ALLOWED_AGGS:
                raise ValueError(f"Metric '{m['id']}' uses unknown aggregation '{m['agg']}'")

    def _validate_intake(self) -> None:
        seen = set()
        for f in self.raw.get("intake_fields", []):
            fid = f["id"]
            if fid in seen:
                raise ValueError(f"Duplicate intake field '{fid}'")
            seen.add(fid)
            ftype = f.get("type", "text")
            if ftype not in INTAKE_TYPES:
                raise ValueError(f"Intake field '{fid}' has unknown type '{ftype}'")
            if ftype == "geo_region" and not self.geo_enabled():
                raise ValueError(
                    f"Intake field '{fid}' is type 'geo_region', which requires "
                    "feature_flags.enable_geo_fields = true"
                )
            sensitive = fid in SENSITIVE_IDS or f.get("sensitive", False)
            if sensitive and f.get("usable_in_rules", False):
                raise ValueError(f"Sensitive intake field '{fid}' cannot be used in segment rules")

    def _validate_triggers(self) -> None:
        t = self.raw["triggers"]
        for key in ("fields", "correlate"):
            if key not in t:
                raise ValueError(f"Triggers section is missing '{key}'")
        c = t["correlate"]
        metrics = {m["id"]: m for m in self.raw["metrics"]}
        if c.get("metric") not in metrics:
            raise ValueError(f"Trigger metric '{c.get('metric')}' is not a declared metric")
        m = metrics[c["metric"]]
        if m["agg"] != "sum" or m.get("of") != "amount":
            raise ValueError("Trigger metric must be a sum of 'amount' (the daily series it is measured on)")
        if c.get("baseline") not in TRIGGER_BASELINES:
            raise ValueError(f"Unknown trigger baseline '{c.get('baseline')}'")
        if c.get("match") not in TRIGGER_MATCHES:
            raise ValueError(f"Unknown trigger match mode '{c.get('match')}'")
        if not isinstance(c.get("min_events"), int) or c["min_events"] < 1:
            raise ValueError("Trigger min_events must be a whole number of at least 1")
        if not isinstance(c.get("z_threshold"), (int, float)) or c["z_threshold"] <= 0:
            raise ValueError("Trigger z_threshold must be a positive number")
        for canon in ("date", "topic"):
            if canon not in t["fields"]:
                raise ValueError(f"Trigger fields must map '{canon}' to a feed column")

    def _validate_entity(self, et: dict) -> None:
        segments = et["segments"]
        ids = [s["id"] for s in segments]
        if len(set(ids)) != len(ids):
            raise ValueError(f"Entity '{et['id']}' has duplicate segment ids")

        defaults = [s for s in segments if s.get("default")]
        if len(defaults) != 1:
            raise ValueError(f"Entity '{et['id']}' must have exactly one default segment")

        priorities = [s["priority"] for s in segments if "priority" in s]
        if len(set(priorities)) != len(priorities):
            raise ValueError(f"Entity '{et['id']}' has duplicate segment priorities")

        metrics = self.metric_ids()
        intake = self.rule_intake_ids()
        for seg in segments:
            if seg.get("default"):
                continue
            if "when" not in seg:
                raise ValueError(f"Segment '{seg['id']}' needs a 'when' rule or 'default': true")
            for cond in _conditions(seg["when"]):
                ref = cond["metric"]
                if ref not in metrics and ref not in intake:
                    raise ValueError(f"Segment '{seg['id']}' references unknown field '{ref}'")
                if cond["op"] not in ALLOWED_OPS:
                    raise ValueError(f"Segment '{seg['id']}' uses unknown operator '{cond['op']}'")
                if not isinstance(cond["value"], (int, float)):
                    raise ValueError(f"Segment '{seg['id']}' condition on '{ref}' needs a numeric value")

        playbook_fields = self.raw["playbook"]["fields"]
        required = [f["id"] for f in playbook_fields if f.get("required")]
        known = {f["id"] for f in playbook_fields}
        for seg in segments:
            entry = self.raw["playbook"]["by_segment"].get(seg["id"])
            if entry is None:
                raise ValueError(f"Segment '{seg['id']}' has no playbook entry")
            for rid in required:
                if rid not in entry:
                    raise ValueError(f"Playbook for '{seg['id']}' is missing required field '{rid}'")
            for key in entry:
                if key not in known:
                    raise ValueError(f"Playbook for '{seg['id']}' uses unknown field '{key}'")


def _conditions(when: dict) -> list[dict]:
    if "all" in when:
        return [c for w in when["all"] for c in _conditions(w)]
    if "any" in when:
        return [c for w in when["any"] for c in _conditions(w)]
    return [when]
