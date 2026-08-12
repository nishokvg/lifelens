"""Tests for the World Bank client's parsing and error handling.

All offline — these use recorded payload shapes rather than live requests, so
the suite is fast and deterministic. The live integration check lives in
``tests/integration_check.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.world_bank import (  # noqa: E402
    INDICATORS,
    WorldBankError,
    extract_rows,
    get_indicator,
    parse_country_rows,
    parse_indicator_rows,
)

# --- Recorded payload shapes ------------------------------------------------

SUCCESS_PAYLOAD = [
    {"page": 1, "pages": 1, "per_page": 5000, "total": 3},
    [
        {
            "indicator": {"id": "SP.DYN.LE00.IN", "value": "Life expectancy at birth"},
            "country": {"id": "IN", "value": "India"},
            "countryiso3code": "IND",
            "date": "1992",
            "value": 58.9,
        },
        {
            "indicator": {"id": "SP.DYN.LE00.IN", "value": "Life expectancy at birth"},
            "country": {"id": "IN", "value": "India"},
            "countryiso3code": "IND",
            "date": "1991",
            "value": None,  # unreported year
        },
        {
            "indicator": {"id": "SP.DYN.LE00.IN", "value": "Life expectancy at birth"},
            "country": {"id": "IN", "value": "India"},
            "countryiso3code": "IND",
            "date": "1990",
            "value": 57.9,
        },
    ],
]

# The API answers bad parameters with HTTP 200 and this body.
ERROR_PAYLOAD = [
    {
        "message": [
            {"id": "120", "key": "Invalid value", "value": "The provided parameter value is not valid"}
        ]
    }
]

EMPTY_PAYLOAD = [{"page": 0, "pages": 0, "per_page": 5000, "total": 0}, None]

COUNTRY_PAYLOAD = [
    {"page": 1, "pages": 1, "per_page": 400, "total": 3},
    [
        {
            "id": "IND",
            "iso2Code": "IN",
            "name": "India",
            "region": {"id": "SAS", "value": "South Asia"},
            "incomeLevel": {"id": "LMC", "value": "Lower middle income"},
        },
        {
            "id": "USA",
            "iso2Code": "US",
            "name": "United States",
            "region": {"id": "NAC", "value": "North America"},
            "incomeLevel": {"id": "HIC", "value": "High income"},
        },
        {
            # Aggregate: region.id == "NA". Must not be selectable.
            "id": "WLD",
            "iso2Code": "1W",
            "name": "World",
            "region": {"id": "NA", "value": "Aggregates"},
            "incomeLevel": {"id": "NA", "value": "Aggregates"},
        },
    ],
]


# --- extract_rows -----------------------------------------------------------

def test_extract_rows_returns_data_section():
    rows = extract_rows(SUCCESS_PAYLOAD)
    assert len(rows) == 3


def test_extract_rows_raises_on_error_payload_despite_http_200():
    """The critical case: a 200 response carrying an error body."""
    with pytest.raises(WorldBankError, match="Invalid value"):
        extract_rows(ERROR_PAYLOAD)


def test_extract_rows_returns_empty_for_null_data_section():
    """A valid query with zero observations is not an error."""
    assert extract_rows(EMPTY_PAYLOAD) == []


@pytest.mark.parametrize("payload", [None, {}, [], ["only-one-element"], 42])
def test_extract_rows_raises_on_unusable_shapes(payload):
    with pytest.raises(WorldBankError):
        extract_rows(payload)


# --- parse_indicator_rows ---------------------------------------------------

def test_parse_drops_null_values_without_imputing():
    frame = parse_indicator_rows(extract_rows(SUCCESS_PAYLOAD))
    assert len(frame) == 2                      # the null year is gone
    assert 1991 not in set(frame["year"])       # and was not filled in
    assert frame["value"].notna().all()


def test_parse_returns_tidy_columns_sorted_by_year():
    frame = parse_indicator_rows(extract_rows(SUCCESS_PAYLOAD))
    assert list(frame.columns) == [
        "indicator", "country_code", "country_name", "year", "value",
    ]
    assert list(frame["year"]) == [1990, 1992]
    assert frame["year"].dtype == "int64"
    assert frame["value"].dtype == "float64"


def test_parse_empty_input_returns_typed_empty_frame():
    frame = parse_indicator_rows([])
    assert frame.empty
    assert list(frame.columns) == [
        "indicator", "country_code", "country_name", "year", "value",
    ]


def test_parse_skips_malformed_rows_rather_than_raising():
    rows = [
        {"indicator": {"id": "X"}, "country": {"id": "IN", "value": "India"},
         "countryiso3code": "IND", "date": "not-a-year", "value": 5},
        {"indicator": {"id": "X"}, "country": {"id": "IN", "value": "India"},
         "countryiso3code": "IND", "date": "2000", "value": "not-a-number"},
        {"indicator": {"id": "X"}, "country": {"id": "IN", "value": "India"},
         "countryiso3code": "IND", "date": "2001", "value": 7.5},
    ]
    frame = parse_indicator_rows(rows)
    assert len(frame) == 1
    assert frame.iloc[0]["year"] == 2001


def test_parse_falls_back_to_country_id_when_iso3_missing():
    rows = [
        {"indicator": {"id": "X"}, "country": {"id": "IND", "value": "India"},
         "countryiso3code": "", "date": "2000", "value": 1.0},
    ]
    frame = parse_indicator_rows(rows)
    assert frame.iloc[0]["country_code"] == "IND"


# --- parse_country_rows -----------------------------------------------------

def test_country_parsing_excludes_aggregates():
    frame = parse_country_rows(extract_rows(COUNTRY_PAYLOAD))
    codes = set(frame["code"])
    assert codes == {"IND", "USA"}
    assert "WLD" not in codes


def test_country_parsing_sorts_by_name_and_keeps_iso2():
    frame = parse_country_rows(extract_rows(COUNTRY_PAYLOAD))
    assert list(frame["name"]) == ["India", "United States"]
    assert frame.loc[frame["code"] == "IND", "iso2"].item() == "IN"


# --- registry ---------------------------------------------------------------

def test_registry_holds_the_six_mvp_indicators():
    assert set(INDICATORS) == {
        "SP.POP.TOTL",
        "SP.DYN.LE00.IN",
        "NY.GDP.PCAP.KD",
        "IT.NET.USER.ZS",
        "SP.URB.TOTL.IN.ZS",
        "SH.DYN.MORT",
    }


def test_registry_directions_are_valid():
    assert all(ind.better in ("up", "down", "neutral") for ind in INDICATORS.values())


def test_percentage_indicators_are_flagged():
    """Drives percentage-point vs percent-change wording downstream."""
    assert INDICATORS["IT.NET.USER.ZS"].is_percentage
    assert INDICATORS["SP.URB.TOTL.IN.ZS"].is_percentage
    assert not INDICATORS["SP.POP.TOTL"].is_percentage


def test_get_indicator_falls_back_for_unknown_code():
    indicator = get_indicator("XX.MADE.UP")
    assert indicator.code == "XX.MADE.UP"
    assert indicator.better == "neutral"
