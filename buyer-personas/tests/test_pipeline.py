import json
import sqlite3
from datetime import datetime, timedelta

import pandas as pd
import pytest

from personas import ingest, trends
from personas.demo import generate
from personas.ingest import hash_identifier
from personas.pipeline import RunConfig, run
from personas.plugins import AIProvider, TemplateProvider, get_provider
from personas.profile import facts_sheet
from personas.report import render_html
from personas.segment import customer_features, segment_customers


def _write_csv(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


# ---------- ingest ----------

def test_column_aliases_and_money_parsing(tmp_path):
    f = _write_csv(tmp_path / "pos.csv", [
        {"Receipt": "A1", "Date Time": "2026-10-01 08:10", "Loyalty ID": "L1", "Item": "Coffee",
         "Department": "Coffee", "Net Sales": "$1,234.50"},
        {"Receipt": "A2", "Date Time": "2026-10-01 08:20", "Loyalty ID": "L1", "Item": "Void",
         "Department": "Coffee", "Net Sales": "-2.00"},
    ])
    lines = ingest.load_file(f, ingest.load_mapping(None))
    assert len(lines) == 1, "voids (negative amounts) must be dropped"
    assert lines["amount"].iloc[0] == pytest.approx(1234.50)
    assert lines["category"].iloc[0] == "beverage"


def test_missing_required_column_gives_actionable_error(tmp_path):
    f = _write_csv(tmp_path / "bad.csv", [{"Foo": 1, "Bar": 2}])
    with pytest.raises(ValueError, match="Required column"):
        ingest.load_file(f, ingest.load_mapping(None))


def test_custom_mapping_file(tmp_path):
    f = _write_csv(tmp_path / "custom.csv", [
        {"Sale Date": "2026-10-01 09:00", "Net Price": "3.00", "Ticket": "T1", "Desc": "Hot Pizza"}
    ])
    mp = tmp_path / "map.json"
    mp.write_text(json.dumps({"timestamp": ["Sale Date"], "amount": ["Net Price"], "product": ["Desc"]}))
    lines = ingest.load_file(f, ingest.load_mapping(str(mp)))
    assert lines["amount"].iloc[0] == 3.0
    assert lines["category"].iloc[0] == "food_service"


def test_sqlite_source(tmp_path):
    db = tmp_path / "pos.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE sales (receipt TEXT, sale_time TEXT, loyalty_id TEXT, item TEXT, department TEXT, net_sales REAL)")
        conn.execute("INSERT INTO sales VALUES ('S1','2026-10-02 18:00','L9','Chips','Snacks',2.19)")
    lines = ingest.load_sql(str(db), "SELECT * FROM sales", ingest.load_mapping(None))
    assert len(lines) == 1 and lines["category"].iloc[0] == "snack"


def test_raw_identifiers_are_never_stored(tmp_path):
    f = _write_csv(tmp_path / "pii.csv", [
        {"Receipt": "A1", "Date Time": "2026-10-01 08:10", "Email": "jane@example.com",
         "Phone": "206-555-0100", "Item": "Coffee", "Department": "Coffee", "Net Sales": "2.49"}
    ])
    lines = ingest.load_file(f, ingest.load_mapping(None))
    blob = lines.to_csv()
    assert "jane@example.com" not in blob and "555-0100" not in blob
    assert lines["customer_key"].iloc[0] == hash_identifier("jane@example.com")


def test_survey_links_to_same_key_as_transactions(tmp_path):
    f = _write_csv(tmp_path / "s.csv", [
        {"loyalty_id": "", "email": "jane@example.com", "age_band": "25-34"},
        {"loyalty_id": "L7", "email": "", "age_band": "55+"},
    ])
    survey = ingest.load_survey(str(f))
    assert set(survey["customer_key"]) == {hash_identifier("jane@example.com"), hash_identifier("L7")}
    assert "age_band" in survey.columns


# ---------- segmentation ----------

def _shopper(cid, visit_days_ago, hour, dept="Coffee", amount=2.49, as_of=None):
    rows = []
    for i, d in enumerate(visit_days_ago):
        ts = (as_of - timedelta(days=d)).replace(hour=hour, minute=0)
        rows.append({"transaction_id": f"{cid}-{i}", "timestamp": ts, "customer_key": cid,
                     "category": "beverage", "amount": amount, "product": dept, "source": "t"})
    return rows


def test_rules_lapsing_loyal_and_late_night():
    as_of = pd.Timestamp("2026-11-14 12:00")
    rows = []
    rows += _shopper("lapser", [40, 50, 60, 70], hour=9, as_of=as_of)          # regular, then gone
    rows += _shopper("loyal", list(range(1, 20)), hour=8, as_of=as_of)         # 19 recent visits
    rows += _shopper("nightowl", [2, 5, 9], hour=23, as_of=as_of)               # late-night pattern
    rows += _shopper("oneoff", [3], hour=9, as_of=as_of)                        # one visit only
    lines = pd.DataFrame(rows)
    lines["timestamp"] = pd.to_datetime(lines["timestamp"])
    feats = segment_customers(customer_features(lines, as_of))
    seg = dict(zip(feats["customer_key"], feats["segment"]))
    assert seg["lapser"] == "Lapsing Regulars"
    assert seg["loyal"] == "Loyal Core"
    assert seg["nightowl"] == "Late-Night Regulars"
    assert seg["oneoff"] == "Occasional Visitors"


# ---------- end-to-end + accuracy regression ----------

@pytest.fixture(scope="module")
def demo_run(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo")
    paths = generate(str(d / "data"))
    cfg = RunConfig(
        files=[paths["pos"]], survey_path=paths["survey"], trends_path=paths["trends"],
        store_name="Test Store", out_dir=str(d / "out"),
    )
    result = run(cfg)
    return result, paths, d


def test_pipeline_writes_outputs(demo_run):
    result, _, d = demo_run
    assert (d / "out" / "personas.json").exists()
    assert (d / "out" / "report.html").exists()
    assert result["data_quality"]["identified_revenue_share_pct"] > 50


def test_segmentation_accuracy_on_synthetic_truth(demo_run):
    """Regression guard, NOT validation: rules and generator were designed together."""
    _, paths, _ = demo_run
    feats = segment_customers(customer_features(
        ingest.load_file(paths["pos"], ingest.load_mapping(None)),
        None,
    ))
    truth = pd.read_csv(paths["truth"], dtype=str).fillna("")
    truth["customer_key"] = [hash_identifier(r.loyalty_id or r.email or r.phone) for r in truth.itertuples()]
    name = {"loyal": "Loyal Core", "lapsing": "Lapsing Regulars", "late_night": "Late-Night Regulars",
            "fuel_commuter": "Fuel-and-Go Commuters", "lunch": "Lunch Grab-and-Go", "occasional": "Occasional Visitors"}
    truth["expected"] = truth["archetype"].map(name)
    m = truth.merge(feats, on="customer_key")
    assert len(m) > 450
    assert (m["expected"] == m["segment"]).mean() >= 0.90


def test_trend_detects_injected_sales_spike(demo_run):
    result, _, _ = demo_run
    lift = {r["topic"]: r for r in result["trend_lift"]}
    assert lift["Gas price spike"]["avg_z_score"] >= 2
    assert lift["Fourth of July"]["evidence"] != "outside normal range"


def test_no_identifiers_in_outputs(demo_run):
    _, paths, d = demo_run
    text = (d / "out" / "personas.json").read_text() + (d / "out" / "report.html").read_text()
    assert "@example.com" not in text
    assert "555-0" not in text


# ---------- trends ----------

def test_lift_flags_normal_variation_as_normal():
    days = pd.date_range("2026-01-01", periods=60)
    rev = pd.DataFrame({"date": days, "revenue": [1000 + (i % 7) * 10 for i in range(60)]})
    tr = pd.DataFrame({"date": [pd.Timestamp("2026-02-01")] * 3, "topic": ["Ordinary"] * 3})
    out = trends.measure_trend_lift(rev, tr.drop_duplicates(subset=["date"]).assign(topic="Ordinary").pipe(
        lambda df: pd.concat([df, df.assign(date=df["date"] + pd.Timedelta(days=7)),
                              df.assign(date=df["date"] + pd.Timedelta(days=14))])))
    row = out[out["topic"] == "Ordinary"].iloc[0]
    assert row["evidence"] == "within normal variation"


def test_trend_matching_uses_playbook_tags(tmp_path):
    f = tmp_path / "t.csv"
    f.write_text("date,scope,topic,tags,source,url\n2026-10-01,mainstream,Gas spike,gas prices;commute,x,\n")
    df = trends.CsvTrendSource(str(f)).fetch(pd.Timestamp("2026-09-01"), pd.Timestamp("2026-11-01"))
    hits = trends.match_trends_to_segments(df, ["Fuel-and-Go Commuters", "Loyal Core"])
    assert hits["Fuel-and-Go Commuters"] and not hits["Loyal Core"]


# ---------- AI plugins ----------

class RecordingProvider(AIProvider):
    name = "recording"

    def __init__(self):
        self.prompts = []

    def generate(self, system, prompt):
        self.prompts.append(prompt)
        return "Plugin narrative."


def test_ai_provider_receives_only_aggregates(demo_run, monkeypatch):
    result, _, _ = demo_run
    provider = RecordingProvider()
    monkeypatch.setattr("personas.pipeline.get_provider", lambda spec: provider)
    cfg = RunConfig(files=[demo_run[1]["pos"]], store_name="Test", out_dir=str(demo_run[2] / "out2"), ai="recording")
    run(cfg)
    assert provider.prompts, "provider was never called"
    joined = "\n".join(provider.prompts)
    assert "@example.com" not in joined
    assert "customer_key" not in joined
    import re
    assert not re.search(r"\b[0-9a-f]{16}\b", joined), "a hashed identifier leaked into the prompt"


def test_template_provider_is_default():
    assert isinstance(get_provider("template"), TemplateProvider)
    assert get_provider("template").generate("s", "p") is None


def test_file_plugin_loads(tmp_path):
    plugin = tmp_path / "my_plugin.py"
    plugin.write_text(
        "from personas.plugins import AIProvider\n"
        "class P(AIProvider):\n"
        "    name = 'mine'\n"
        "    def generate(self, system, prompt):\n"
        "        return 'hello'\n"
        "PROVIDER = P()\n"
    )
    assert get_provider(f"file:{plugin}").generate("s", "p") == "hello"


def test_failed_ai_falls_back_to_template(demo_run):
    class Broken(AIProvider):
        name = "broken"

        def generate(self, system, prompt):
            raise RuntimeError("server down")

    from personas.profile import add_narratives
    personas = [{"segment": "Loyal Core", "size": {"share_of_identified_revenue_pct": 10, "customers": 3, "share_of_gross_profit_pct": 10},
                 "behavior": {"avg_visits_in_window": 5, "avg_basket_usd": 4.0, "peak_daypart": "midday",
                              "visits_per_month": 2, "median_days_since_last_visit": 3, "top_category": "beverages",
                              "weekend_share_pct": 10},
                 "playbook": {"goal": "g", "offer_angle": "o", "channel": "c", "timing": "t", "avoid": "a"},
                 "trend_hooks": [], "narrative": "", "narrative_source": ""}]
    facts = facts_sheet(personas, "S", "P")
    add_narratives(personas, facts, Broken())
    assert personas[0]["narrative"]
    assert "AI failed" in personas[0]["narrative_source"]


# ---------- report ----------

def test_report_escapes_html(demo_run):
    result, _, _ = demo_run
    evil = dict(result, store="<script>alert(1)</script>")
    html_out = render_html(evil)
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out


# ---------- narrative grounding ----------

from personas.profile import add_narratives, ungrounded_figures  # noqa: E402

GROUND_FACTS = {
    "name": "Loyal Core",
    "share_of_revenue_pct": 7.4,
    "customers": 42,
    "behavior": {"avg_visits_in_window": 17.9, "avg_basket_usd": 5.25, "peak_daypart": "midday"},
    "playbook": {"timing": "Weekly, 9 pm to close", "goal": "Protect frequency."},
    "current_trend_topics": [],
}


def test_grounded_figures_pass():
    text = "Loyal Core is 7.4% of revenue across 42 customers, averaging 17.9 visits and a $5.25 basket."
    assert ungrounded_figures(text, GROUND_FACTS) == []


def test_rounded_figures_are_grounded():
    # 17.9 is grounded by a fact written at 17.9; "18" rounds the same fact to whole number.
    assert ungrounded_figures("About 18 visits.", {"x": 17.93}) == []


def test_invented_figure_is_flagged():
    bad = ungrounded_figures("Loyal Core spends 42.5% more per visit.", GROUND_FACTS)
    assert bad == ["42.5"]


def test_numbers_in_playbook_text_count_as_grounded():
    assert ungrounded_figures("Send offers weekly around 9 pm.", GROUND_FACTS) == []


class FixedProvider(AIProvider):
    name = "fixed"

    def __init__(self, text):
        self.text = text

    def generate(self, system, prompt):
        return self.text


def _one_persona():
    return [{"segment": "Loyal Core", "size": {"share_of_identified_revenue_pct": 7.4, "customers": 42, "share_of_gross_profit_pct": 7.4},
             "behavior": {"avg_visits_in_window": 17.9, "avg_basket_usd": 5.25, "peak_daypart": "midday",
                          "visits_per_month": 2, "median_days_since_last_visit": 3, "top_category": "beverages",
                          "weekend_share_pct": 10},
             "playbook": {"goal": "Protect frequency.", "offer_angle": "o", "channel": "c",
                          "timing": "Weekly, 9 pm to close", "avoid": "a"},
             "trend_hooks": [], "narrative": "", "narrative_source": ""}]


def test_ai_text_with_invented_number_is_rejected_and_template_kept():
    personas = _one_persona()
    facts = facts_sheet(personas, "S", "P")
    rejected = add_narratives(personas, facts, FixedProvider("Loyal Core shoppers grew 63% this month."))
    assert personas[0]["narrative_source"].startswith("template (AI rejected")
    assert "63" in personas[0]["narrative_source"]
    assert personas[0]["narrative"].startswith("Loyal Core make up")
    assert rejected and rejected[0]["figures"] == ["63"]


def test_grounded_ai_text_is_used():
    personas = _one_persona()
    facts = facts_sheet(personas, "S", "P")
    rejected = add_narratives(personas, facts, FixedProvider("Loyal Core is 7.4% of revenue, averaging 17.9 visits."))
    assert personas[0]["narrative_source"] == "fixed"
    assert rejected == []


def test_pipeline_records_rejections(tmp_path, monkeypatch):
    class Liar(FixedProvider):
        name = "liar"

    monkeypatch.setattr("personas.pipeline.get_provider", lambda spec: Liar("Revenue rose 999%."))
    paths = generate(str(tmp_path / "d"))
    result = run(RunConfig(files=[paths["pos"]], store_name="T", out_dir=str(tmp_path / "o"), ai="liar"))
    assert result["narrative_rejections"], "rejections should be recorded in personas.json"
    assert all(p["narrative_source"].startswith("template") for p in result["personas"])


# ---------- regressions for the report fixes ----------

def test_restricted_items_never_define_top_category():
    as_of = pd.Timestamp("2026-11-14 12:00")
    rows = []
    for i in range(6):  # six cigarette packs vs one coffee: restricted must not win
        rows.append({"transaction_id": f"c-{i}", "timestamp": as_of - timedelta(days=i + 1),
                     "customer_key": "k1", "category": "restricted", "amount": 9.0, "product": "Cig", "source": "t"})
    rows.append({"transaction_id": "c-x", "timestamp": as_of - timedelta(days=2),
                 "customer_key": "k1", "category": "beverage", "amount": 2.0, "product": "Coffee", "source": "t"})
    lines = pd.DataFrame(rows)
    lines["timestamp"] = pd.to_datetime(lines["timestamp"])
    feats = customer_features(lines, as_of)
    assert feats.loc[0, "top_category"] == "beverage"


def test_gross_profit_share_uses_margins_not_revenue():
    as_of = pd.Timestamp("2026-11-14 12:00")
    rows = [
        {"transaction_id": "f1", "timestamp": as_of, "customer_key": "fuel", "category": "fuel", "amount": 100.0, "product": "g", "source": "t"},
        {"transaction_id": "b1", "timestamp": as_of, "customer_key": "bev", "category": "beverage", "amount": 40.0, "product": "c", "source": "t"},
    ]
    lines = pd.DataFrame(rows)
    feats = customer_features(lines, as_of).set_index("customer_key")
    # fuel: 100 * 0.09 = 9 gross; beverage: 40 * 0.40 = 16 gross. Beverage buyer leads on profit despite less revenue.
    assert feats.loc["bev", "gross_profit"] > feats.loc["fuel", "gross_profit"]


def test_default_trend_window_is_90_days():
    from personas.pipeline import RunConfig
    assert RunConfig().trend_window_days == 90
