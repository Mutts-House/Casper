"""Build showcase reports for every pack from the synthetic demo data.

Output: examples/<pack>_report.html, one per pack. All three use the same
config-driven renderer. The retail example runs the retail pack through the
generic engine on a canonical projection of the POS demo export, so no
retail-specific renderer is involved.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import pack_demo, pack_run, pipeline
from .report_pack import render_pack_report
from .workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent
PACKS = ROOT / "packs"
DISCLAIMER = "Synthetic demo data generated for this showcase. Not real customers, patients, participants, or sales."


def _retail_canonical(pos_csv: Path, out_csv: Path) -> Path:
    """Project POS line items onto the generic schema: one row per line, transaction time shared."""
    lines = pipeline.load_lines(pipeline.RunConfig(files=[str(pos_csv)]))
    ident = lines[lines["customer_key"].notna()].copy()
    tx_time = ident.groupby("transaction_id")["timestamp"].transform("min")
    canon = pd.DataFrame({
        "transaction_id": ident["transaction_id"],
        "timestamp": tx_time,
        "entity_id": ident["customer_key"].astype(str),
        "amount": ident["amount"],
        "category": ident["category"],
    })
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    canon.to_csv(out_csv, index=False)
    return out_csv


def build_examples(out_dir: str | Path = "examples", data_dir: str | Path = "sample_data") -> dict[str, Path]:
    data = Path(data_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    work = out / ".work"
    work.mkdir(exist_ok=True)

    jobs = {
        "retail": (
            PACKS / "retail.json",
            _retail_canonical(data / "pos_export_demo.csv", work / "retail_canonical.csv"),
            data / "trends_demo.csv",
        ),
        "healthcare": (PACKS / "healthcare.json", data / "healthcare_visits_demo.csv", data / "health_trends_demo.csv"),
        "community": (PACKS / "community.json", data / "community_attendance_demo.csv", data / "community_trends_demo.csv"),
    }
    written = {}
    for name, (pack_path, events, trends_csv) in jobs.items():
        ws = Workspace.load(pack_path)
        result = pack_run.run_pack(pack_path, events, work / name, trends_path=str(trends_csv))
        target = out / f"{name}_report.html"
        target.write_text(render_pack_report(result, ws, disclaimer=DISCLAIMER), encoding="utf-8")
        written[name] = target
    return written
