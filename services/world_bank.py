"""World Bank Indicators API client.

A small, dependency-light client for https://api.worldbank.org/v2.

Design notes
------------
* The API is public and keyless, so there is no secret handling anywhere.
* Countries are batched into a single request per indicator: fetching
  ``IND;USA;WLD`` for one indicator costs one HTTP call, not three.
* The API returns **HTTP 200 with an error body** for bad parameters, so the
  parser branches on payload *structure*, never on status code alone.
* Public ``fetch_*`` functions are Streamlit-cached. The ``_fetch_*`` variants
  underneath are plain functions so the test suite can exercise them without a
  Streamlit runtime.
* Null observations are dropped, never imputed. A year that the World Bank did
  not report simply does not appear in the returned frame.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Sequence

import pandas as pd
import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.worldbank.org/v2"

# (connect timeout, read timeout) in seconds.
TIMEOUT: tuple[float, float] = (5.0, 10.0)

MAX_ATTEMPTS = 3
BACKOFF_BASE = 0.5  # sleeps of 0.5s, 1.0s between attempts
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

WORLD_CODE = "WLD"
SOURCE_ATTRIBUTION = "Source: World Bank Open Data — World Development Indicators"

# Columns of the tidy frame every fetch resolves to.
TIDY_COLUMNS = ["indicator", "country_code", "country_name", "year", "value"]


class WorldBankError(RuntimeError):
    """The request failed, or the API replied with an error payload.

    Distinct from "the data does not exist", which is represented by an empty
    DataFrame rather than an exception.
    """


@dataclass(frozen=True)
class Indicator:
    """One World Bank indicator and everything the UI needs to present it."""

    code: str
    label: str            # official World Bank name — provenance, always kept
    short_label: str      # for tight spaces (cards, chart legends)
    beat: str             # the story question this indicator answers
    unit: str
    decimals: int
    better: str           # "up", "down" or "neutral"
    is_percentage: bool   # drives percentage-point vs percent-change wording
    emoji: str = "📊"     # identity glyph — pairs with colour so neither is alone
    note: str = ""        # caveat surfaced in footnotes
    # Plain-English name for a general audience. Never replaces ``label`` — it
    # leads, and the official name follows in muted text, so a reader can still
    # trace any figure back to the published series.
    plain_label: str = ""

    @property
    def direction_is_meaningful(self) -> bool:
        return self.better in ("up", "down")

    @property
    def display_label(self) -> str:
        """What the reader sees first: plain English where we have it."""
        return self.plain_label or self.label


# The six MVP indicators. Adding a seventh is one entry here and nothing else.
INDICATORS: dict[str, Indicator] = {
    "SP.POP.TOTL": Indicator(
        code="SP.POP.TOTL",
        label="Population, total",
        short_label="Population",
        beat="How many of us there are",
        unit="people",
        decimals=0,
        better="neutral",
        is_percentage=False,
        emoji="👥",
    ),
    "SP.DYN.LE00.IN": Indicator(
        code="SP.DYN.LE00.IN",
        label="Life expectancy at birth, total",
        short_label="Life expectancy",
        beat="How long a newborn is expected to live",
        unit="years",
        decimals=1,
        better="up",
        is_percentage=False,
        emoji="❤️",
        note=(
            "Life expectancy at birth is a period measure for babies born in "
            "that year under that year's mortality rates. It is not a "
            "prediction for anyone alive today."
        ),
    ),
    "NY.GDP.PCAP.KD": Indicator(
        code="NY.GDP.PCAP.KD",
        label="GDP per capita (constant 2015 US$)",
        short_label="GDP per capita",
        beat="How much economic output there is per person",
        unit="constant 2015 US$",
        decimals=0,
        better="up",
        is_percentage=False,
        emoji="💰",
        note="Constant 2015 US$, so growth is not inflated by price changes.",
    ),
    "IT.NET.USER.ZS": Indicator(
        code="IT.NET.USER.ZS",
        label="Individuals using the Internet (% of population)",
        short_label="Internet users",
        beat="How connected we are",
        unit="% of population",
        decimals=1,
        better="up",
        is_percentage=True,
        emoji="🌐",
        note="Reporting begins around 1990; earlier years are largely unreported.",
    ),
    "SP.URB.TOTL.IN.ZS": Indicator(
        code="SP.URB.TOTL.IN.ZS",
        label="Urban population (% of total population)",
        short_label="Urban population",
        beat="Where we live",
        unit="% of population",
        decimals=1,
        better="neutral",
        is_percentage=True,
        emoji="🏙️",
    ),
    "SH.DYN.MORT": Indicator(
        code="SH.DYN.MORT",
        label="Mortality rate, under-5 (per 1,000 live births)",
        short_label="Under-5 mortality",
        beat="How many children survive",
        unit="per 1,000 live births",
        decimals=1,
        better="down",
        is_percentage=False,
        emoji="👶",
    ),
}

DEFAULT_INDICATOR_CODES: tuple[str, ...] = tuple(INDICATORS)


def get_indicator(code: str) -> Indicator:
    """Look up an indicator, falling back to a generic descriptor.

    Keeps the UI from crashing if a caller passes a code outside the registry.
    """
    if code in INDICATORS:
        return INDICATORS[code]
    return Indicator(
        code=code,
        label=code,
        short_label=code,
        beat="",
        unit="",
        decimals=2,
        better="neutral",
        is_percentage=False,
    )


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

_SESSION: requests.Session | None = None


def _session() -> requests.Session:
    """One module-level session, so connections are pooled across calls."""
    global _SESSION
    if _SESSION is None:
        session = requests.Session()
        session.headers.update({"User-Agent": "LifeLens/0.1 (educational project)"})
        _SESSION = session
    return _SESSION


def _request(path: str, params: dict[str, Any]) -> Any:
    """GET a World Bank endpoint and return the decoded JSON payload.

    Retries transient failures with exponential backoff. Raises
    :class:`WorldBankError` once retries are exhausted.
    """
    url = f"{API_BASE}/{path.lstrip('/')}"
    query = {"format": "json", **params}
    last_error: str = "unknown error"

    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            time.sleep(BACKOFF_BASE * (2 ** (attempt - 1)))
        try:
            response = _session().get(url, params=query, timeout=TIMEOUT)
        except requests.Timeout:
            last_error = f"timed out after {TIMEOUT[1]}s"
            logger.warning("World Bank request timed out (attempt %s): %s", attempt + 1, url)
            continue
        except requests.RequestException as exc:
            last_error = f"connection error ({exc.__class__.__name__})"
            logger.warning("World Bank request failed (attempt %s): %s", attempt + 1, exc)
            continue

        if response.status_code in RETRY_STATUS:
            last_error = f"HTTP {response.status_code}"
            logger.warning("World Bank returned %s (attempt %s)", response.status_code, attempt + 1)
            continue

        if not response.ok:
            raise WorldBankError(f"World Bank API returned HTTP {response.status_code} for {url}")

        try:
            return response.json()
        except ValueError as exc:
            raise WorldBankError(f"World Bank API returned a non-JSON response for {url}") from exc

    attempts = f"{MAX_ATTEMPTS} attempt{'s' if MAX_ATTEMPTS != 1 else ''}"
    raise WorldBankError(f"World Bank API unreachable after {attempts} ({last_error}).")


def extract_rows(payload: Any) -> list[dict[str, Any]]:
    """Pull the data rows out of a World Bank payload.

    The API wraps results as ``[metadata, rows]``. Failures arrive as HTTP 200
    with ``[{"message": [...]}]``, so this branches on structure.

    Returns an empty list when the query was valid but matched no observations.
    Raises :class:`WorldBankError` for API-level errors and unusable shapes.
    """
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        message = payload[0].get("message")
        if message:
            detail = "; ".join(
                f"{item.get('key', '')}: {item.get('value', '')}".strip(": ")
                for item in message
                if isinstance(item, dict)
            )
            raise WorldBankError(f"World Bank API error — {detail or 'unspecified'}")

    if not isinstance(payload, list) or len(payload) < 2:
        raise WorldBankError("Unexpected response shape from the World Bank API.")

    rows = payload[1]
    if rows is None:
        return []  # valid query, zero observations
    if not isinstance(rows, list):
        raise WorldBankError("Unexpected data section in the World Bank response.")
    return [row for row in rows if isinstance(row, dict)]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _empty_tidy_frame() -> pd.DataFrame:
    frame = pd.DataFrame(columns=TIDY_COLUMNS)
    return frame.astype({"year": "int64", "value": "float64"})


def parse_indicator_rows(rows: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """Normalize raw indicator rows into the tidy frame the whole app consumes.

    Columns: ``indicator, country_code, country_name, year, value``.

    Rows whose value is null are dropped rather than imputed — a year the World
    Bank did not report must not become a number the user reads as fact.
    """
    records: list[dict[str, Any]] = []

    for row in rows:
        value = row.get("value")
        if value is None:
            continue  # unreported; never fabricate

        year_raw = row.get("date")
        try:
            year = int(str(year_raw))
        except (TypeError, ValueError):
            logger.debug("Skipping row with unparseable date: %r", year_raw)
            continue

        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            logger.debug("Skipping row with non-numeric value: %r", value)
            continue

        indicator = row.get("indicator") or {}
        country = row.get("country") or {}
        code = row.get("countryiso3code") or country.get("id") or ""

        records.append(
            {
                "indicator": indicator.get("id", "") if isinstance(indicator, dict) else "",
                "country_code": str(code).upper(),
                "country_name": country.get("value", "") if isinstance(country, dict) else "",
                "year": year,
                "value": numeric_value,
            }
        )

    if not records:
        return _empty_tidy_frame()

    frame = pd.DataFrame.from_records(records, columns=TIDY_COLUMNS)
    frame = frame.astype({"year": "int64", "value": "float64"})
    return frame.sort_values(["indicator", "country_code", "year"], ignore_index=True)


def parse_country_rows(rows: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """Normalize the country endpoint into a selectable country table.

    Aggregates (regions, income groups, "World") carry ``region.id == "NA"`` and
    are excluded, so the user cannot pick "Euro area" as a birth country.
    """
    records: list[dict[str, Any]] = []

    for row in rows:
        region = row.get("region") or {}
        region_id = region.get("id", "") if isinstance(region, dict) else ""
        if str(region_id).strip().upper() == "NA":
            continue  # aggregate, not a country

        code = str(row.get("id", "")).upper()
        name = row.get("name", "")
        if not code or not name:
            continue

        income = row.get("incomeLevel") or {}
        records.append(
            {
                "code": code,
                "iso2": str(row.get("iso2Code", "")).upper(),
                "name": str(name).strip(),
                "region": region.get("value", "") if isinstance(region, dict) else "",
                "income_level": income.get("value", "") if isinstance(income, dict) else "",
            }
        )

    if not records:
        return pd.DataFrame(columns=["code", "iso2", "name", "region", "income_level"])

    frame = pd.DataFrame.from_records(records)
    return frame.sort_values("name", ignore_index=True)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _fetch_countries() -> pd.DataFrame:
    payload = _request("country", {"per_page": 400})
    return parse_country_rows(extract_rows(payload))


def _fetch_indicator(
    code: str,
    country_codes: Sequence[str],
    start_year: int,
    end_year: int,
) -> pd.DataFrame:
    if not country_codes:
        return _empty_tidy_frame()

    countries = ";".join(dict.fromkeys(c.upper() for c in country_codes))
    payload = _request(
        f"country/{countries}/indicator/{code}",
        {"date": f"{start_year}:{end_year}", "per_page": 5000},
    )
    return parse_indicator_rows(extract_rows(payload))


def fetch_indicators(
    codes: Sequence[str],
    country_codes: Sequence[str],
    start_year: int,
    end_year: int,
    progress=None,
    label_for: Optional[Callable[[str], str]] = None,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Fetch several indicators, tolerating individual failures.

    Returns ``(tidy_frame, errors)`` where ``errors`` maps an indicator code to
    a human-readable reason. A failure in one indicator never prevents the rest
    from being returned — this is what makes partial rendering possible.

    ``progress`` is an optional ``callable(done, total, label)`` for UI status.
    ``label_for`` names a code for that progress line; registries outside this
    module (see ``services/environment.py``) pass their own lookup so the status
    text reads as a name rather than a code.
    """
    frames: list[pd.DataFrame] = []
    errors: dict[str, str] = {}
    total = len(codes)
    name_of = label_for or (lambda code: get_indicator(code).short_label)

    for index, code in enumerate(codes, start=1):
        if progress is not None:
            progress(index, total, name_of(code))
        try:
            frame = fetch_indicator(code, country_codes, start_year, end_year)
        except WorldBankError as exc:
            logger.warning("Indicator %s failed: %s", code, exc)
            errors[code] = str(exc)
            continue
        if frame.empty:
            errors[code] = "The World Bank returned no observations for this indicator."
            continue
        frames.append(frame)

    if not frames:
        return _empty_tidy_frame(), errors

    combined = pd.concat(frames, ignore_index=True)
    return combined.sort_values(["indicator", "country_code", "year"], ignore_index=True), errors


# ---------------------------------------------------------------------------
# Cached public entry points
# ---------------------------------------------------------------------------
# Wrapped only when Streamlit is importable, so the modules above stay testable
# without a Streamlit runtime.

try:  # pragma: no cover - exercised implicitly by the running app
    import streamlit as st

    fetch_countries = st.cache_data(ttl=60 * 60 * 24 * 7, show_spinner=False)(_fetch_countries)
    fetch_indicator = st.cache_data(ttl=60 * 60 * 24, show_spinner=False)(_fetch_indicator)
except ImportError:  # pragma: no cover
    fetch_countries = _fetch_countries
    fetch_indicator = _fetch_indicator
