"""Tests for metric computation.

Focus is on the edge cases that produce wrong numbers rather than crashes:
year selection when a series starts late, zero baselines, and single-point
series that must not be presented as trends.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.formatting import (  # noqa: E402
    UNAVAILABLE,
    article_name,
    flag_emoji,
    format_percent,
    format_points,
    human_number,
    pluralize,
    safe_name,
    sentence_label,
)
from utils.calculations import (  # noqa: E402
    common_year,
    compute_change,
    coverage,
    earliest_available,
    get_series,
    largest_change,
    largest_gap,
    latest_available,
    life_span,
    nearest_available,
    strongest_improvement,
)


def build_frame(records):
    return pd.DataFrame(
        records, columns=["indicator", "country_code", "country_name", "year", "value"]
    )


@pytest.fixture
def frame():
    rows = []
    for year, value in [(1990, 57.9), (1995, 60.5), (2000, 62.5), (2022, 67.2)]:
        rows.append(("SP.DYN.LE00.IN", "IND", "India", year, value))
    for year, value in [(1990, 75.2), (1995, 75.7), (2000, 76.6), (2022, 77.4)]:
        rows.append(("SP.DYN.LE00.IN", "USA", "United States", year, value))
    # Internet usage: series starts after 1990, and the baseline is zero.
    for year, value in [(1994, 0.0), (2000, 0.5), (2022, 46.3)]:
        rows.append(("IT.NET.USER.ZS", "IND", "India", year, value))
    # Under-5 mortality: lower is better.
    for year, value in [(1990, 126.2), (2022, 30.1)]:
        rows.append(("SH.DYN.MORT", "IND", "India", year, value))
    return build_frame(rows)


# --- series access ----------------------------------------------------------

def test_get_series_returns_year_indexed_values(frame):
    series = get_series(frame, "SP.DYN.LE00.IN", "IND")
    assert list(series.index) == [1990, 1995, 2000, 2022]
    assert series.loc[2022] == pytest.approx(67.2)


def test_get_series_is_case_insensitive_on_country(frame):
    assert not get_series(frame, "SP.DYN.LE00.IN", "ind").empty


def test_get_series_missing_combination_returns_empty(frame):
    assert get_series(frame, "SP.DYN.LE00.IN", "BRA").empty
    assert get_series(frame, "NOPE", "IND").empty


def test_get_series_handles_empty_frame():
    assert get_series(build_frame([]), "SP.POP.TOTL", "IND").empty


# --- year selection ---------------------------------------------------------

def test_latest_available_is_newest_reported_year_not_current_year(frame):
    latest = latest_available(get_series(frame, "SP.DYN.LE00.IN", "IND"))
    assert latest.year == 2022
    assert latest.value == pytest.approx(67.2)


def test_earliest_available(frame):
    assert earliest_available(get_series(frame, "SP.DYN.LE00.IN", "IND")).year == 1990


def test_latest_available_on_empty_series_returns_none():
    assert latest_available(pd.Series(dtype="float64")) is None


def test_nearest_available_exact_match(frame):
    found = nearest_available(get_series(frame, "SP.DYN.LE00.IN", "IND"), 1990)
    assert found.year == 1990


def test_nearest_available_when_series_starts_late(frame):
    """Born 1990, internet data starts 1994 — snap forward, don't extrapolate."""
    found = nearest_available(get_series(frame, "IT.NET.USER.ZS", "IND"), 1990)
    assert found.year == 1994
    assert found.value == 0.0


def test_nearest_available_prefers_later_year_on_tie():
    series = pd.Series({1990: 1.0, 2000: 2.0})
    assert nearest_available(series, 1995).year == 2000


def test_nearest_available_respects_max_distance(frame):
    series = get_series(frame, "IT.NET.USER.ZS", "IND")
    assert nearest_available(series, 1990, max_distance=2) is None
    assert nearest_available(series, 1990, max_distance=5).year == 1994


def test_nearest_available_on_empty_series_returns_none():
    assert nearest_available(pd.Series(dtype="float64"), 1990) is None


# --- change arithmetic ------------------------------------------------------

def test_compute_change_basic(frame):
    change = compute_change(frame, "SP.DYN.LE00.IN", "IND", 1990)
    assert change.start.year == 1990
    assert change.end.year == 2022
    assert change.absolute == pytest.approx(9.3)
    assert change.percent == pytest.approx(16.06, abs=0.01)
    assert change.years_elapsed == 32
    assert change.direction == "up"


def test_compute_change_percentage_indicator_reports_points(frame):
    change = compute_change(frame, "IT.NET.USER.ZS", "IND", 1990, is_percentage=True)
    assert change.percentage_points == pytest.approx(46.3)


def test_compute_change_zero_baseline_yields_none_percent(frame):
    """0 -> 46.3 has no meaningful percent change; it must not be infinity."""
    change = compute_change(frame, "IT.NET.USER.ZS", "IND", 1990)
    assert change.start.value == 0.0
    assert change.percent is None
    assert change.absolute == pytest.approx(46.3)


def test_compute_change_declining_indicator(frame):
    change = compute_change(frame, "SH.DYN.MORT", "IND", 1990)
    assert change.direction == "down"
    assert change.absolute == pytest.approx(-96.1)


def test_compute_change_single_observation_is_not_a_trend():
    single = build_frame([("SP.POP.TOTL", "IND", "India", 2024, 1_450_000_000)])
    assert compute_change(single, "SP.POP.TOTL", "IND", 2024) is None


def test_compute_change_missing_series_returns_none(frame):
    assert compute_change(frame, "SP.POP.TOTL", "BRA", 1990) is None


# --- ranking ----------------------------------------------------------------

def test_largest_change_ignores_none_percent(frame):
    changes = {
        code: compute_change(frame, code, "IND", 1990)
        for code in ("SP.DYN.LE00.IN", "IT.NET.USER.ZS", "SH.DYN.MORT")
    }
    code, change = largest_change(changes)
    assert code == "SH.DYN.MORT"  # -76%, vs +16% for life expectancy


def test_largest_change_empty_returns_none():
    assert largest_change({}) is None


def test_strongest_improvement_respects_direction(frame):
    changes = {
        code: compute_change(frame, code, "IND", 1990)
        for code in ("SP.DYN.LE00.IN", "SH.DYN.MORT")
    }
    directions = {"SP.DYN.LE00.IN": "up", "SH.DYN.MORT": "down"}
    code, _ = strongest_improvement(changes, directions)
    assert code == "SH.DYN.MORT"  # a large fall counts as improvement


def test_strongest_improvement_excludes_neutral_indicators(frame):
    changes = {"SP.DYN.LE00.IN": compute_change(frame, "SP.DYN.LE00.IN", "IND", 1990)}
    assert strongest_improvement(changes, {"SP.DYN.LE00.IN": "neutral"}) is None


def test_strongest_improvement_when_nothing_improved():
    rows = [("SP.DYN.LE00.IN", "IND", "India", 1990, 70.0),
            ("SP.DYN.LE00.IN", "IND", "India", 2020, 65.0)]
    frame = build_frame(rows)
    changes = {"SP.DYN.LE00.IN": compute_change(frame, "SP.DYN.LE00.IN", "IND", 1990)}
    assert strongest_improvement(changes, {"SP.DYN.LE00.IN": "up"}) is None


# --- cross-country comparison ----------------------------------------------

def test_common_year_uses_latest_year_both_countries_reported(frame):
    assert common_year(frame, "SP.DYN.LE00.IN", ["IND", "USA"]) == 2022


def test_common_year_returns_none_when_a_country_has_no_data(frame):
    assert common_year(frame, "IT.NET.USER.ZS", ["IND", "USA"]) is None


def test_largest_gap_reports_indicator_and_shared_year(frame):
    result = largest_gap(frame, ["SP.DYN.LE00.IN"], "IND", "USA")
    code, gap, year = result
    assert code == "SP.DYN.LE00.IN"
    assert year == 2022
    assert gap == pytest.approx(15.18, abs=0.01)


def test_largest_gap_returns_none_without_shared_years(frame):
    assert largest_gap(frame, ["IT.NET.USER.ZS"], "IND", "USA") is None


# --- personal arithmetic ----------------------------------------------------

def test_life_span_exact_age():
    span = life_span(date(1990, 6, 25), as_of=date(2026, 8, 12))
    assert span.years == 36
    assert span.months == 1
    assert span.days_total == 13197


def test_life_span_before_birthday_in_current_year():
    span = life_span(date(1990, 6, 25), as_of=date(2026, 6, 24))
    assert span.years == 35
    assert span.months == 11


def test_life_span_on_birthday():
    span = life_span(date(1990, 6, 25), as_of=date(2026, 6, 25))
    assert span.years == 36
    assert span.months == 0


def test_life_span_estimates_scale_with_days():
    span = life_span(date(1990, 6, 25), as_of=date(2026, 8, 12))
    assert span.sunrises == span.days_total
    assert span.heartbeats == span.days_total * 24 * 60 * 72
    assert span.breaths == span.days_total * 24 * 60 * 16


def test_full_moons_differ_from_days_lived():
    """Two stat cards must never show the same number — that reads as a bug."""
    span = life_span(date(1990, 6, 25), as_of=date(2026, 8, 12))
    assert span.full_moons == 446
    assert span.full_moons != span.days_total


def test_orbits_match_age_in_years():
    span = life_span(date(1990, 6, 25), as_of=date(2026, 8, 12))
    assert span.orbits == pytest.approx(36.1, abs=0.1)


def test_km_around_sun_is_positive_and_scales():
    span = life_span(date(1990, 6, 25), as_of=date(2026, 8, 12))
    assert span.km_around_sun == pytest.approx(3.396e10, rel=0.01)


def test_life_span_newborn_has_no_negative_values():
    span = life_span(date(2026, 8, 12), as_of=date(2026, 8, 12))
    assert span.years == 0
    assert span.days_total == 0
    assert span.heartbeats == 0


# --- coverage ---------------------------------------------------------------

def test_coverage_reports_missing_years(frame):
    report = coverage(frame, "SP.DYN.LE00.IN", "IND", 1990, 1995)
    assert report.earliest_year == 1990
    assert report.latest_year == 2022
    assert report.observations == 4
    assert 1991 in report.missing_years
    assert not report.is_complete


def test_coverage_of_absent_series_reports_no_data(frame):
    report = coverage(frame, "SP.POP.TOTL", "BRA", 1990, 2000)
    assert report.observations == 0
    assert report.earliest_year is None
    assert report.issue == "No data returned"


# --- formatting -------------------------------------------------------------

def test_human_number_scales():
    assert human_number(1_463_865_525, 2) == "1.46B"
    assert human_number(864_972_221, 2) == "864.97M"
    assert human_number(-5000, 1) == "-5.0K"
    assert human_number(62.4, 1) == "62.4"


def test_human_number_of_none_is_unavailable():
    assert human_number(None) == UNAVAILABLE


def test_percent_and_points_are_distinct_wordings():
    assert format_percent(369.2) == "+369.2%"
    assert format_points(70.0) == "+70.0 pp"
    assert format_percent(None) == UNAVAILABLE


def test_pluralize_singular_and_plural():
    assert pluralize(1, "month") == "1 month"
    assert pluralize(0, "month") == "0 months"
    assert pluralize(36, "year") == "36 years"


def test_article_name_adds_the_where_english_needs_it():
    assert article_name("United States") == "the United States"
    assert article_name("Netherlands") == "the Netherlands"
    assert article_name("Marshall Islands") == "the Marshall Islands"
    assert article_name("India") == "India"
    assert article_name("Bahamas, The") == "Bahamas, The"


def test_sentence_label_preserves_acronyms():
    assert sentence_label("GDP per capita") == "GDP per capita"
    assert sentence_label("Life expectancy") == "life expectancy"
    assert sentence_label("Under-5 mortality") == "under-5 mortality"


def test_flag_emoji_and_aggregate_fallback():
    assert flag_emoji("IN") == "🇮🇳"
    assert flag_emoji("US") == "🇺🇸"
    assert flag_emoji("1W") == "🌍"   # World aggregate is not a country
    assert flag_emoji(None) == "🌍"


def test_safe_name_escapes_markup():
    assert "<script>" not in safe_name("<script>alert(1)</script>")
    assert safe_name("  Nishok  ") == "Nishok"
    assert safe_name("") == ""
    assert safe_name(None) == ""
