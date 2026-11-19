"""Official events feed: Ticketmaster Discovery API (events search).

Turns upcoming and past local events near the store into trend rows, so the
persona engine can match them to segments and measure their sales effect.

Requires a free API key from developer.ticketmaster.com, set as the
TICKETMASTER_API_KEY environment variable. Never hardcode the key.

Terms: Ticketmaster requires attribution and rate limits. Keep the source
name in the output (it is written to each trend row) and do not store
more event data than the analysis needs.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

import pandas as pd

from .trends import TrendSource

BASE_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
TREND_COLUMNS = ["date", "scope", "topic", "tags", "source", "url"]

# Ticketmaster segment -> playbook tags, so events match the right personas.
SEGMENT_TAGS = {
    "Sports": "event;late night;nightlife",
    "Music": "event;nightlife;late night",
    "Arts & Theatre": "event;community",
    "Film": "event;community",
}
DEFAULT_TAGS = "event;community"


def parse_events(payload: dict, source: str = "ticketmaster") -> pd.DataFrame:
    """Convert one Discovery API response into trend rows.

    Expected shape (per the documented response):
      _embedded.events[] -> name, url, dates.start.localDate,
                            classifications[0].segment.name
    Events with no local start date are skipped.
    """
    events = (payload or {}).get("_embedded", {}).get("events", [])
    rows = []
    for e in events:
        start = (e.get("dates") or {}).get("start", {}).get("localDate")
        if not start:
            continue
        classes = e.get("classifications") or [{}]
        segment = (classes[0].get("segment") or {}).get("name", "")
        rows.append({
            "date": start,
            "scope": "local",
            "topic": e.get("name", "").strip(),
            "tags": SEGMENT_TAGS.get(segment, DEFAULT_TAGS),
            "source": source,
            "url": e.get("url", ""),
        })
    return pd.DataFrame(rows, columns=TREND_COLUMNS)


def _default_fetch(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError("Ticketmaster rejected the API key (401). Check TICKETMASTER_API_KEY.") from exc
        if exc.code == 429:
            raise RuntimeError("Ticketmaster rate limit reached (429). Try again later.") from exc
        raise RuntimeError(f"Ticketmaster returned HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Ticketmaster: {exc.reason}") from exc


class TicketmasterEventsSource(TrendSource):
    """Events within `radius` of a postal code, for a date range.

    Fetches one page of up to 200 events, sorted by date. For a longer
    history, run once per quarter and save the output, rather than paging
    every run.
    """

    def __init__(
        self,
        postal_code: str,
        radius: int = 25,
        unit: str = "miles",
        country: str = "US",
        api_key: str | None = None,
        fetch_json: Callable[[str], dict] | None = None,
    ):
        self.api_key = api_key or os.environ.get("TICKETMASTER_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("Set TICKETMASTER_API_KEY to use the events feed.")
        self.postal_code = postal_code
        self.radius = radius
        self.unit = unit
        self.country = country
        self._fetch_json = fetch_json or _default_fetch

    def url_for(self, start: pd.Timestamp, end: pd.Timestamp) -> str:
        params = {
            "postalCode": self.postal_code,
            "radius": str(self.radius),
            "unit": self.unit,
            "countryCode": self.country,
            "startDateTime": start.strftime("%Y-%m-%dT00:00:00Z"),
            "endDateTime": end.strftime("%Y-%m-%dT23:59:59Z"),
            "size": "200",
            "sort": "date,asc",
            "apikey": self.api_key,
        }
        return f"{BASE_URL}?{urllib.parse.urlencode(params)}"

    def fetch(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        payload = self._fetch_json(self.url_for(start, end))
        return parse_events(payload)

    def log_url(self, start, end) -> str:
        """The request URL with the key removed, safe to print or log."""
        return self.url_for(start, end).replace(self.api_key, "REDACTED")
