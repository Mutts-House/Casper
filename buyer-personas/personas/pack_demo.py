"""Synthetic demo data for the healthcare and community packs.

Every record is generated, not collected. Patient and participant IDs are
made-up codes. No clinical conditions, diagnoses, or medications are recorded:
the data only has visit or attendance dates, visit type, charge or hours.
"""

from __future__ import annotations

import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

AS_OF = date(2026, 11, 15)


def _dt(d: date, rng: random.Random) -> str:
    return f"{d.isoformat()} {rng.randint(8, 19):02d}:{rng.choice(['00', '15', '30', '45'])}"


def _between(rng: random.Random, lo: int, hi: int) -> date:
    """A date between `hi` and `lo` days before AS_OF (lo is the older bound)."""
    return AS_OF - timedelta(days=rng.randint(hi, lo))


def generate_healthcare(out_dir: str, seed: int = 11) -> str:
    rng = random.Random(seed)
    rows = []
    n = 0

    def add(pid: str, d: date, vtype: str, charge: float):
        nonlocal n
        n += 1
        rows.append({
            "visit_id": f"V{n:06d}", "visit_date": _dt(d, rng), "patient_id": pid,
            "visit_type": vtype, "charge_amount": f"{charge:.2f}",
        })

    plan = [("lapsed", 60), ("preventive", 40), ("follow_up", 60), ("urgent", 40), ("occasional", 100)]
    pid = 0
    for kind, count in plan:
        for _ in range(count):
            pid += 1
            code = f"PT{pid:04d}"
            if kind == "lapsed":
                for _ in range(rng.randint(2, 4)):
                    add(code, _between(rng, 365, 185), rng.choice(["Routine visit", "Follow-up visit"]), rng.uniform(90, 140))
            elif kind == "preventive":
                for _ in range(rng.randint(1, 3)):
                    add(code, _between(rng, 900, 320), "Preventive wellness visit", 150.0)
            elif kind == "follow_up":
                for m in range(rng.randint(4, 12)):
                    add(code, AS_OF - timedelta(days=28 * m + rng.randint(0, 5)), "Follow-up visit", rng.uniform(55, 70))
            elif kind == "urgent":
                for _ in range(rng.randint(2, 4)):
                    add(code, _between(rng, 180, 0), "Urgent walk-in", rng.uniform(180, 260))
            else:
                for _ in range(rng.randint(1, 2)):
                    add(code, _between(rng, 730, 0), "Routine visit", rng.uniform(90, 140))

    path = Path(out_dir) / "healthcare_visits_demo.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return str(path)


def generate_community(out_dir: str, seed: int = 13) -> str:
    rng = random.Random(seed)
    rows = []
    n = 0

    def add(pid: str, d: date, program: str):
        nonlocal n
        n += 1
        rows.append({
            "attendance_id": f"A{n:06d}", "session_date": _dt(d, rng), "participant_id": pid,
            "program_type": program, "hours": f"{rng.uniform(1.0, 2.0):.1f}",
        })

    plan = [("regular", 40), ("lapsing", 50), ("youth", 40), ("food", 50), ("occasional", 70)]
    pid = 0
    for kind, count in plan:
        for _ in range(count):
            pid += 1
            code = f"CP{pid:04d}"
            if kind == "regular":
                for _ in range(rng.randint(12, 30)):
                    add(code, _between(rng, 270, 0), rng.choice(["Adult education class", "General workshop"]))
            elif kind == "lapsing":
                for _ in range(rng.randint(3, 6)):
                    add(code, _between(rng, 360, 95), rng.choice(["Adult education class", "General workshop"]))
            elif kind == "youth":
                for _ in range(rng.randint(3, 8)):
                    add(code, _between(rng, 180, 0), "Youth after-school program")
            elif kind == "food":
                for _ in range(rng.randint(2, 6)):
                    add(code, _between(rng, 180, 0), "Food pantry visit")
            else:
                for _ in range(rng.randint(1, 2)):
                    add(code, _between(rng, 365, 0), rng.choice(["General workshop", "Senior activity"]))

    path = Path(out_dir) / "community_attendance_demo.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return str(path)
