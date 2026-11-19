import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from personas import demo, pack_demo, pack_run
from personas.report_pack import render_pack_report
from personas.workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
RETAIL_BASELINE_SHA256 = "9b0672ec2c2959f8263a56983de99a8b7ec6ba2e0f83aaac4870ea155e3cb39a"
RETAIL_WORDS = ["fuel", "basket", "store", "revenue", "loyalty", "retail", "gross profit", "commute", "circle k"]


def visible_text(html_text: str) -> str:
    body = re.sub(r"<style>.*?</style>", "", html_text, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).lower()


@pytest.mark.parametrize("name", ["retail", "healthcare", "community"])
def test_example_report_exists_and_has_sections(name):
    html_text = (EXAMPLES / f"{name}_report.html").read_text(encoding="utf-8")
    text = visible_text(html_text)
    for section in ["segments", "personas", "segment rules", "intake fields", "trend triggers"]:
        assert section in text, section
    assert "synthetic demo data" in text


def test_healthcare_and_community_reports_have_no_retail_vocabulary():
    for name in ["healthcare", "community"]:
        text = visible_text((EXAMPLES / f"{name}_report.html").read_text(encoding="utf-8"))
        hits = [w for w in RETAIL_WORDS if w in text]
        assert not hits, (name, hits)


def test_renderer_source_has_no_retail_vocabulary():
    src = (ROOT / "personas" / "report_pack.py").read_text(encoding="utf-8").lower()
    for word in RETAIL_WORDS + ["basket", "fuel", "daypart"]:
        assert word not in src, word


def test_labels_come_from_the_pack(tmp_path):
    data = pack_demo.generate_healthcare(str(tmp_path / "d"))
    raw = json.loads((ROOT / "packs" / "healthcare.json").read_text(encoding="utf-8"))
    for m in raw["metrics"]:
        if m["id"] == "charges":
            m["label"] = "Zorp total"
    edited = tmp_path / "pack.json"
    edited.write_text(json.dumps(raw))
    ws = Workspace.load(edited)
    result = pack_run.run_pack(edited, data, tmp_path / "o")
    assert "Zorp total" in render_pack_report(result, ws)


def test_user_text_is_escaped(tmp_path):
    data = pack_demo.generate_community(str(tmp_path / "d"))
    ws = Workspace.load(ROOT / "packs" / "community.json")
    result = pack_run.run_pack(ROOT / "packs" / "community.json", data, tmp_path / "o")
    result["personas"][0]["narrative"] = "<script>alert(1)</script>"
    html_text = render_pack_report(result, ws)
    assert "<script>alert(1)</script>" not in html_text
    assert "&lt;script&gt;" in html_text


def test_retail_pipeline_report_matches_baseline(tmp_path):
    demo.generate(str(tmp_path / "data"))
    from personas.pipeline import RunConfig, run
    paths = {"pos": tmp_path / "data" / "pos_export_demo.csv"}
    args = dict(
        files=[str(paths["pos"])], survey_path=str(tmp_path / "data" / "survey_demo.csv"),
        trends_path=str(tmp_path / "data" / "trends_demo.csv"), store_name="Demo Store (synthetic)",
        out_dir=str(tmp_path / "out"),
    )
    run(RunConfig(**args))
    sha = hashlib.sha256((tmp_path / "out" / "report.html").read_bytes()).hexdigest()
    assert sha == RETAIL_BASELINE_SHA256
