import pandas as pd
import pytest

from personas import events
from personas.demo import generate
from personas.pipeline import RunConfig, run

FIXTURE = {
    "_embedded": {"events": [
        {"name": " Home Game Night ", "url": "https://example.test/e1",
         "dates": {"start": {"localDate": "2026-08-16"}},
         "classifications": [{"segment": {"name": "Sports"}}]},
        {"name": "Jazz Night", "url": "https://example.test/e2",
         "dates": {"start": {"localDate": "2026-10-02"}},
         "classifications": [{"segment": {"name": "Music"}}]},
        {"name": "No Date Event", "url": "", "dates": {"start": {}}, "classifications": []},
        {"name": "Unknown Segment", "url": "", "dates": {"start": {"localDate": "2026-10-03"}},
         "classifications": [{"segment": {"name": "Misc"}}]},
    ]}
}


def test_parse_maps_segments_to_playbook_tags_and_skips_undated():
    df = events.parse_events(FIXTURE)
    assert len(df) == 3, "event without a local date must be skipped"
    assert df.iloc[0]["topic"] == "Home Game Night"
    assert "late night" in df.iloc[0]["tags"]
    assert df.iloc[1]["tags"] == "event;nightlife;late night"
    assert df.iloc[2]["tags"] == events.DEFAULT_TAGS
    assert set(df["scope"]) == {"local"} and set(df["source"]) == {"ticketmaster"}


def test_parse_empty_response_gives_empty_frame():
    df = events.parse_events({})
    assert df.empty and list(df.columns) == events.TREND_COLUMNS


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("TICKETMASTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TICKETMASTER_API_KEY"):
        events.TicketmasterEventsSource("98033")


def test_request_url_has_expected_params_and_log_redacts_key():
    src = events.TicketmasterEventsSource("98033", radius=10, api_key="SECRETKEY123")
    url = src.url_for(pd.Timestamp("2026-06-01"), pd.Timestamp("2026-11-14"))
    assert "postalCode=98033" in url and "radius=10" in url and "countryCode=US" in url
    assert "startDateTime=2026-06-01T00%3A00%3A00Z" in url or "startDateTime=2026-06-01T00:00:00Z" in url
    assert "SECRETKEY123" not in src.log_url(pd.Timestamp("2026-06-01"), pd.Timestamp("2026-11-14"))


def test_fetch_uses_injected_transport_and_parses():
    seen = {}

    def fake(url):
        seen["url"] = url
        return FIXTURE

    src = events.TicketmasterEventsSource("98033", api_key="k", fetch_json=fake)
    df = src.fetch(pd.Timestamp("2026-06-01"), pd.Timestamp("2026-11-14"))
    assert len(df) == 3 and "apikey=k" in seen["url"]


def test_events_failure_is_a_warning_not_a_crash(tmp_path, monkeypatch):
    paths = generate(str(tmp_path / "d"))
    monkeypatch.setenv("TICKETMASTER_API_KEY", "k")

    def boom(self, start, end):
        raise RuntimeError("Ticketmaster rate limit reached (429).")

    monkeypatch.setattr(events.TicketmasterEventsSource, "fetch", boom)
    result = run(RunConfig(files=[paths["pos"]], trends_path=paths["trends"], events_postal_code="98033",
                           store_name="T", out_dir=str(tmp_path / "o")))
    assert any("rate limit" in w for w in result["warnings"])
    assert result["personas"], "the run must still produce personas"


def test_events_join_the_lift_analysis(tmp_path, monkeypatch):
    paths = generate(str(tmp_path / "d"))
    monkeypatch.setenv("TICKETMASTER_API_KEY", "k")
    # One real-looking event on the injected snack/beverage spike date (Aug 16 2026).
    fake_rows = pd.DataFrame([{"date": "2026-08-16", "scope": "local", "topic": "Local game",
                               "tags": "event;late night", "source": "ticketmaster", "url": ""}],
                             columns=events.TREND_COLUMNS)
    monkeypatch.setattr(events.TicketmasterEventsSource, "fetch", lambda self, s, e: fake_rows)
    result = run(RunConfig(files=[paths["pos"]], events_postal_code="98033", store_name="T",
                           out_dir=str(tmp_path / "o")))
    topics = [r["topic"] for r in result["trend_lift"]]
    assert "Local game" in topics
    assert (tmp_path / "o" / "trends_used.csv").exists()
