"""Tests for the Earth & Resources data layer.

The failure modes that matter here are quiet ones: a cumulative total that
silently sums a ratio, a peak year that drifts on a tie, a lifetime total that
includes years before the user was born, and an export that loses a sheet when
the selection is empty. Each has an explicit test.

All offline — the registry and the pure helpers only, no live requests.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.environment import (  # noqa: E402
    DEFAULT_ENVIRONMENT_CODES,
    ENVIRONMENT_CAVEATS,
    ENVIRONMENT_INDICATORS,
    ENVIRONMENT_SOURCE_ATTRIBUTION,
    NO_WORLD_AGGREGATE_CODES,
    get_environment_indicator,
    has_world_aggregate,
    is_cumulative_meaningful,
)
from utils.environment import (  # noqa: E402
    ANNUAL_COLUMNS,
    EXPORT_SHEETS,
    SUMMARY_COLUMNS,
    annual_export_frame,
    build_environment_workbook,
    build_workbook,
    countries_only,
    cumulative_total,
    methodology_frame,
    order_selection,
    peak_observation,
    rank_within,
    share_of_peak,
    summarize_depletion,
    summary_export_frame,
    top_reporters,
    window_frame,
    year_window,
)
from utils.calculations import get_series  # noqa: E402
from utils.narratives import depletion_interpretation  # noqa: E402

ENERGY = "NY.ADJ.DNGY.CD"
MINERAL = "NY.ADJ.DMIN.CD"
FOREST = "NY.ADJ.DFOR.CD"
RENTS = "NY.GDP.TOTL.RT.ZS"


def build_frame(records):
    return pd.DataFrame(
        records, columns=["indicator", "country_code", "country_name", "year", "value"]
    )


def empty_frame():
    return build_frame([])


@pytest.fixture
def frame():
    """A small but awkward dataset: a gap, a tie, a zero, and a sparse country."""
    rows = []
    # Energy depletion, World: 1988 predates the 1990 birth year; 1995 missing.
    for year, value in [
        (1988, 300.0),
        (1990, 400.0),
        (1992, 900.0),
        (1994, 700.0),
        (1996, 500.0),
    ]:
        rows.append((ENERGY, "WLD", "World", year, value))
    # Energy depletion, India: a tie on the maximum across 1991 and 1993.
    for year, value in [(1990, 50.0), (1991, 120.0), (1993, 120.0), (1996, 80.0)]:
        rows.append((ENERGY, "IND", "India", year, value))
    # Net forest depletion, World: a reported zero is a real value.
    for year, value in [(1990, 0.0), (1991, 0.0), (1992, 10.0)]:
        rows.append((FOREST, "WLD", "World", year, value))
    # Resource rents, World: a ratio series.
    for year, value in [(1990, 4.5), (1992, 6.25), (1996, 3.0)]:
        rows.append((RENTS, "WLD", "World", year, value))
    # A single-observation country — sparse, but must not crash anything.
    rows.append((ENERGY, "USA", "United States", 1994, 42.0))
    return build_frame(rows)


# --- registry ---------------------------------------------------------------

def test_registry_holds_the_four_depletion_indicators():
    assert set(ENVIRONMENT_INDICATORS) == {ENERGY, MINERAL, FOREST, RENTS}
    assert set(DEFAULT_ENVIRONMENT_CODES) == set(ENVIRONMENT_INDICATORS)


def test_currency_indicators_carry_their_unit():
    for code in (ENERGY, MINERAL, FOREST):
        assert ENVIRONMENT_INDICATORS[code].unit == "current US$"
        assert not ENVIRONMENT_INDICATORS[code].is_percentage


def test_rents_is_flagged_as_a_percentage_series():
    assert ENVIRONMENT_INDICATORS[RENTS].is_percentage
    assert ENVIRONMENT_INDICATORS[RENTS].unit == "% of GDP"


def test_no_indicator_claims_a_better_direction():
    """Depletion rising or falling is not framed as good or bad by this app."""
    assert all(ind.better == "neutral" for ind in ENVIRONMENT_INDICATORS.values())


def test_every_indicator_has_a_definition_note():
    assert all(ind.note.strip() for ind in ENVIRONMENT_INDICATORS.values())


def test_get_environment_indicator_falls_back_for_unknown_code():
    indicator = get_environment_indicator("XX.NOT.REAL")
    assert indicator.code == "XX.NOT.REAL"
    assert indicator.better == "neutral"


def test_cumulative_is_refused_for_ratio_series():
    assert is_cumulative_meaningful(ENERGY)
    assert not is_cumulative_meaningful(RENTS)


def test_caveats_never_claim_to_measure_what_remains():
    """Copy guard: the honesty rules of this tab live in one place."""
    text = " ".join(ENVIRONMENT_CAVEATS).lower()
    assert "not a measure of reserves remaining" in text
    assert "latest year reported" in text
    for banned in ("how much is left", "remaining gold", "remaining oil"):
        assert banned not in text


def test_attribution_does_not_call_every_series_an_adjusted_savings_line():
    """Total natural resources rents is not part of the adjusted savings account."""
    attribution = ENVIRONMENT_SOURCE_ATTRIBUTION.lower()
    assert "world development indicators" in attribution
    assert "natural resources rents" in attribution


def test_depletion_series_are_attributed_to_the_adjusted_savings_account():
    for code in (ENERGY, MINERAL, FOREST):
        assert "adjusted net (genuine) savings account" in ENVIRONMENT_INDICATORS[code].note


def test_rents_is_not_attributed_to_the_adjusted_savings_account():
    note = ENVIRONMENT_INDICATORS[RENTS].note
    assert "standalone World Development Indicators series" in note
    assert "not a line of the adjusted savings account" in note


# --- plain-English labels ---------------------------------------------------

def test_every_indicator_has_a_plain_english_label():
    """A general reader should not have to parse "Adjusted savings:"."""
    for indicator in ENVIRONMENT_INDICATORS.values():
        assert indicator.plain_label
        assert "adjusted savings" not in indicator.plain_label.lower()
        assert indicator.display_label == indicator.plain_label


def test_plain_labels_are_the_agreed_wording():
    assert ENVIRONMENT_INDICATORS[ENERGY].plain_label == "Fossil fuels used up"
    assert ENVIRONMENT_INDICATORS[MINERAL].plain_label == "Minerals & metals used up"
    assert (
        ENVIRONMENT_INDICATORS[FOREST].plain_label
        == "Forest resources used faster than they regrew"
    )
    assert ENVIRONMENT_INDICATORS[RENTS].plain_label == "Resource dependence"


def test_official_label_and_code_are_never_lost():
    """Provenance survives the rename — both still available to the UI."""
    for code, indicator in ENVIRONMENT_INDICATORS.items():
        assert indicator.code == code
        assert indicator.label
        assert indicator.label != indicator.plain_label


def test_development_indicators_fall_back_to_their_official_label():
    from services.world_bank import INDICATORS

    population = INDICATORS["SP.POP.TOTL"]
    assert population.plain_label == ""
    assert population.display_label == population.label


# --- world-aggregate availability -------------------------------------------

def test_depletion_flows_have_no_world_aggregate():
    """Verified against the API: no World, region or income group reports these."""
    assert NO_WORLD_AGGREGATE_CODES == {ENERGY, MINERAL, FOREST}
    for code in (ENERGY, MINERAL, FOREST):
        assert not has_world_aggregate(code)


def test_resource_rents_does_have_a_world_aggregate():
    assert has_world_aggregate(RENTS)


# --- rank and peers ---------------------------------------------------------

def snapshot_frame(records):
    """A one-year, many-country snapshot as returned by country/all."""
    return pd.DataFrame(
        [(MINERAL, code, name, 2021, value) for code, name, value in records],
        columns=["indicator", "country_code", "country_name", "year", "value"],
    )


@pytest.fixture
def snapshot():
    return snapshot_frame([
        ("AUS", "Australia", 68.6),
        ("CHN", "China", 56.6),
        ("IND", "India", 29.6),
        ("CHL", "Chile", 28.1),
        ("BRA", "Brazil", 23.2),
        ("USA", "United States", 14.3),
        ("KEN", "Kenya", 0.0),
        ("FJI", "Fiji", 0.0),
        ("WLD", "World", 999.9),      # an aggregate that must be excluded
        ("SAS", "South Asia", 500.0),  # ditto
    ])


REAL_CODES = ["AUS", "CHN", "IND", "CHL", "BRA", "USA", "KEN", "FJI"]


def test_countries_only_drops_aggregates(snapshot):
    """Ranking India against "South Asia" would be meaningless."""
    ranked = countries_only(snapshot, REAL_CODES)
    assert set(ranked["country_code"]) == set(REAL_CODES)
    assert "WLD" not in set(ranked["country_code"])


def test_countries_only_on_an_empty_snapshot():
    assert countries_only(empty_frame(), REAL_CODES).empty


def test_rank_within_places_a_country_among_reporters(snapshot):
    rank = rank_within(countries_only(snapshot, REAL_CODES), "IND")
    assert rank.rank == 3
    assert rank.reporting == 8
    assert rank.ordinal == "3rd"
    assert rank.country_name == "India"
    assert rank.value == pytest.approx(29.6)
    assert rank.is_top_ten


def test_rank_within_counts_only_real_countries(snapshot):
    """With aggregates left in, India would rank 5th instead of 3rd."""
    unfiltered = rank_within(snapshot, "IND")
    filtered = rank_within(countries_only(snapshot, REAL_CODES), "IND")
    assert unfiltered.rank == 5
    assert filtered.rank == 3


def test_rank_within_is_case_insensitive(snapshot):
    assert rank_within(countries_only(snapshot, REAL_CODES), "ind").rank == 3


def test_rank_within_returns_none_for_a_country_that_did_not_report(snapshot):
    assert rank_within(countries_only(snapshot, REAL_CODES), "NPL") is None


def test_rank_within_on_an_empty_snapshot_is_none():
    assert rank_within(empty_frame(), "IND") is None


def test_rank_within_shares_a_rank_on_ties(snapshot):
    """Two countries reporting zero are both in the same position."""
    ranked = countries_only(snapshot, REAL_CODES)
    kenya = rank_within(ranked, "KEN")
    fiji = rank_within(ranked, "FJI")
    assert kenya.rank == fiji.rank == 7
    assert kenya.value == 0.0


def test_ordinal_wording():
    base = rank_within(snapshot_frame([("IND", "India", 1.0)]), "IND")
    expected = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 11: "11th", 12: "12th",
                13: "13th", 21: "21st", 22: "22nd", 101: "101st", 111: "111th"}
    for number, wording in expected.items():
        assert replace(base, rank=number).ordinal == wording


def test_top_reporters_returns_the_highest_values_in_order(snapshot):
    table = top_reporters(countries_only(snapshot, REAL_CODES), limit=3)
    assert list(table["country_code"]) == ["AUS", "CHN", "IND"]
    assert list(table["rank"]) == [1, 2, 3]


def test_top_reporters_keeps_the_users_country_outside_the_top(snapshot):
    """The reader came for their own country; it must never be dropped."""
    table = top_reporters(countries_only(snapshot, REAL_CODES), limit=2, always_include=["USA"])
    assert list(table["country_code"]) == ["AUS", "CHN", "USA"]
    assert int(table[table["country_code"] == "USA"]["rank"].iloc[0]) == 6


def test_top_reporters_does_not_duplicate_a_country_already_in_the_top(snapshot):
    table = top_reporters(countries_only(snapshot, REAL_CODES), limit=5, always_include=["IND"])
    assert list(table["country_code"]).count("IND") == 1


def test_top_reporters_on_an_empty_snapshot_is_empty():
    assert top_reporters(empty_frame(), limit=10).empty


def test_nothing_in_the_ranking_path_sums_country_values(snapshot):
    """The guard against inventing a world total: no output equals the sum."""
    ranked = countries_only(snapshot, REAL_CODES)
    total = float(ranked["value"].sum())
    table = top_reporters(ranked, limit=10)
    assert total not in set(table["value"])
    assert rank_within(ranked, "AUS").value != total


# --- windowing --------------------------------------------------------------

def test_window_frame_filters_indicator_years_and_countries(frame):
    windowed = window_frame(frame, ENERGY, 1990, 1994, ["WLD", "IND"])
    assert set(windowed["indicator"]) == {ENERGY}
    assert set(windowed["country_code"]) == {"WLD", "IND"}
    assert windowed["year"].min() == 1990
    assert windowed["year"].max() == 1994


def test_window_frame_on_empty_frame_returns_empty():
    assert window_frame(empty_frame(), ENERGY, 1990, 2020).empty


def test_window_frame_with_no_matching_years_returns_empty(frame):
    assert window_frame(frame, ENERGY, 2010, 2020, ["WLD"]).empty


# --- peak detection ---------------------------------------------------------

def test_peak_observation_finds_the_highest_year(frame):
    peak = peak_observation(get_series(frame, ENERGY, "WLD"))
    assert peak.year == 1992
    assert peak.value == pytest.approx(900.0)


def test_peak_observation_breaks_ties_on_the_earliest_year(frame):
    """1991 and 1993 both hit 120 — the first time it was reached wins."""
    peak = peak_observation(get_series(frame, ENERGY, "IND"))
    assert peak.year == 1991


def test_peak_observation_on_empty_series_returns_none():
    assert peak_observation(pd.Series(dtype="float64")) is None


def test_peak_of_an_all_zero_series_is_zero_not_none(frame):
    series = get_series(frame, FOREST, "WLD")[[1990, 1991]]
    peak = peak_observation(series)
    assert peak.value == 0.0
    assert peak.year == 1990


# --- cumulative totals ------------------------------------------------------

def test_cumulative_total_sums_reported_values(frame):
    series = get_series(frame, ENERGY, "WLD")
    assert cumulative_total(series) == pytest.approx(2800.0)


def test_cumulative_total_excludes_years_before_birth(frame):
    """The 1988 observation must not enter a lifetime total for a 1990 birth."""
    series = get_series(frame, ENERGY, "WLD")
    assert cumulative_total(series, since_year=1990) == pytest.approx(2500.0)


def test_cumulative_total_on_empty_series_returns_none():
    assert cumulative_total(pd.Series(dtype="float64")) is None


def test_cumulative_total_returns_none_when_nothing_follows_the_birth_year(frame):
    series = get_series(frame, ENERGY, "WLD")
    assert cumulative_total(series, since_year=2050) is None


# --- summaries --------------------------------------------------------------

def test_summary_reports_birth_latest_peak_and_cumulative(frame):
    windowed = window_frame(frame, ENERGY, 1990, 1996, ["WLD"])
    summary = summarize_depletion(windowed, ENERGY, "WLD", 1990, country_name="World")

    assert summary.has_data
    assert summary.birth.year == 1990 and summary.birth.value == pytest.approx(400.0)
    assert summary.latest.year == 1996 and summary.latest.value == pytest.approx(500.0)
    assert summary.peak.year == 1992
    assert summary.cumulative == pytest.approx(2500.0)
    assert summary.reported_years == 4
    assert summary.first_year == 1990 and summary.last_year == 1996
    assert summary.coverage_label == "1990–1996"


def test_summary_of_an_empty_dataset_is_an_empty_state_not_a_crash():
    summary = summarize_depletion(empty_frame(), ENERGY, "WLD", 1990)
    assert not summary.has_data
    assert summary.birth is None
    assert summary.latest is None
    assert summary.peak is None
    assert summary.cumulative is None
    assert summary.reported_years == 0
    assert summary.coverage_label == "no reported years"


def test_summary_for_a_missing_country_is_empty_not_an_error(frame):
    summary = summarize_depletion(frame, ENERGY, "BRA", 1990)
    assert not summary.has_data


def test_summary_of_a_single_observation_series_is_coherent(frame):
    """One year is not a trend, but it must still summarize without crashing."""
    summary = summarize_depletion(frame, ENERGY, "USA", 1990, country_name="United States")
    assert summary.reported_years == 1
    assert summary.birth.year == summary.latest.year == 1994
    assert summary.peak.year == 1994
    assert summary.cumulative == pytest.approx(42.0)
    assert summary.coverage_label == "1994 only"


def test_summary_baseline_snaps_forward_when_the_series_starts_late(frame):
    """Born 1990, US series starts 1994 — the baseline is labelled 1994."""
    summary = summarize_depletion(frame, ENERGY, "USA", 1990)
    assert summary.birth.year == 1994
    assert summary.has_lifetime_baseline is True
    assert summary.ends_before_birth_year is False


def test_summary_never_takes_a_baseline_from_before_the_birth_year(frame):
    """A 2024 birth over a series ending 2021 gets no baseline at all.

    Reaching backwards would label a value recorded before the user was alive
    as their "birth-year value" — the one thing this card must never do.
    """
    summary = summarize_depletion(frame, ENERGY, "WLD", 2024, country_name="World")
    assert summary.birth is None
    assert summary.has_lifetime_baseline is False
    assert summary.ends_before_birth_year is True
    assert summary.cumulative is None
    assert summary.latest.year == 1996  # the series itself is still described


def test_summary_baseline_is_the_first_lifetime_year_not_the_nearest(frame):
    """Born 1993: 1992 is nearer than 1994, but 1992 predates the birth."""
    summary = summarize_depletion(frame, ENERGY, "WLD", 1993)
    assert summary.birth.year == 1994
    assert summary.cumulative == pytest.approx(1200.0)  # 1994 + 1996 only


def test_summary_refuses_a_cumulative_total_for_a_ratio_series(frame):
    summary = summarize_depletion(
        frame, RENTS, "WLD", 1990, country_name="World", cumulative=False
    )
    assert summary.has_data
    assert summary.cumulative is None
    assert summary.latest.value == pytest.approx(3.0)


def test_summary_respects_the_selected_year_window(frame):
    """Cards must describe the window on screen, not the whole download."""
    windowed = window_frame(frame, ENERGY, 1990, 1992, ["WLD"])
    summary = summarize_depletion(windowed, ENERGY, "WLD", 1990)
    assert summary.latest.year == 1992
    assert summary.cumulative == pytest.approx(1300.0)


def test_share_of_peak_handles_a_zero_peak(frame):
    zero_only = build_frame([(FOREST, "WLD", "World", 1990, 0.0)])
    assert share_of_peak(summarize_depletion(zero_only, FOREST, "WLD", 1990)) is None

    summary = summarize_depletion(frame, ENERGY, "WLD", 1990)
    assert share_of_peak(summary) == pytest.approx(500 / 900 * 100)


# --- year window and selection order ----------------------------------------

def test_year_window_opens_on_the_birth_year_when_covered(frame):
    window = year_window([1990, 1992, 1994, 1996], 1992)
    assert (window.first, window.last, window.default_start) == (1990, 1996, 1992)
    assert not window.is_single_year


def test_year_window_opens_on_the_first_year_for_an_earlier_birth():
    window = year_window([1990, 1995, 2000], 1975)
    assert window.default_start == 1990


def test_year_window_clamps_a_birth_after_the_data_to_the_last_year():
    window = year_window([1990, 1995, 2000], 2024)
    assert window.default_start == 2000


def test_year_window_flags_single_year_coverage():
    """A range slider needs min < max; this is the case that would crash it."""
    window = year_window([2021], 2021)
    assert window.is_single_year
    assert window.first == window.last == 2021
    assert window.default_start == 2021


def test_year_window_without_a_birth_year_opens_on_the_full_range():
    """The Lifetime in Data tab has no birth-year anchor and keeps its default."""
    window = year_window([1990, 1995, 2000])
    assert (window.first, window.default_start, window.last) == (1990, 1990, 2000)


def test_year_window_flags_single_year_coverage_without_a_birth_year():
    window = year_window([2024])
    assert window.is_single_year
    assert window.default_start == 2024


def test_year_window_of_no_years_is_none():
    assert year_window([], 1990) is None
    assert year_window([]) is None


def test_order_selection_follows_the_user_not_the_registry():
    entries = [("WLD", "World", "g"), ("IND", "India", "b"), ("USA", "United States", "o")]
    ordered = order_selection(entries, ["IND", "WLD"])
    assert [entry[0] for entry in ordered] == ["IND", "WLD"]


def test_order_selection_drops_unselected_and_unknown_codes():
    entries = [("WLD", "World", "g"), ("IND", "India", "b")]
    assert order_selection(entries, ["BRA"]) == []
    assert [e[0] for e in order_selection(entries, ["WLD"])] == ["WLD"]


def test_order_selection_of_nothing_is_empty():
    assert order_selection([("WLD", "World", "g")], []) == []


# --- narrative --------------------------------------------------------------

def test_interpretation_states_the_latest_reported_year(frame):
    summary = summarize_depletion(frame, ENERGY, "WLD", 1990, country_name="World")
    sentence = depletion_interpretation(summary, ENVIRONMENT_INDICATORS[ENERGY], "World")
    assert "1996" in sentence
    assert "1992" in sentence  # the peak year
    assert "remains in the ground" in sentence  # the standing caveat


def test_interpretation_of_an_empty_summary_says_so(frame):
    summary = summarize_depletion(empty_frame(), ENERGY, "WLD", 1990)
    sentence = depletion_interpretation(summary, ENVIRONMENT_INDICATORS[ENERGY], "World")
    assert "not reported" in sentence
    assert "$" not in sentence  # no fabricated figure


def test_interpretation_says_when_the_series_ends_before_the_birth_year(frame):
    summary = summarize_depletion(frame, ENERGY, "WLD", 2024, country_name="World")
    sentence = depletion_interpretation(summary, ENVIRONMENT_INDICATORS[ENERGY], "World")
    assert "ends before 2024" in sentence
    assert "none of it falls inside your lifetime" in sentence


def test_interpretation_handles_a_missing_summary():
    sentence = depletion_interpretation(None, ENVIRONMENT_INDICATORS[ENERGY], "World")
    assert "not reported" in sentence


# --- export frames ----------------------------------------------------------

def test_annual_export_frame_has_the_agreed_columns(frame):
    windowed = window_frame(frame, ENERGY, 1990, 1996, ["WLD"])
    table = annual_export_frame(windowed, ENVIRONMENT_INDICATORS)

    assert list(table.columns) == ANNUAL_COLUMNS
    assert len(table) == 4
    assert set(table["indicator_code"]) == {ENERGY}
    assert set(table["unit"]) == {"current US$"}
    assert table["indicator_label"].iloc[0] == ENVIRONMENT_INDICATORS[ENERGY].label


def test_annual_export_frame_of_an_empty_selection_keeps_its_headers():
    table = annual_export_frame(empty_frame(), ENVIRONMENT_INDICATORS)
    assert table.empty
    assert list(table.columns) == ANNUAL_COLUMNS


def test_summary_export_frame_carries_every_required_field(frame):
    windowed = window_frame(frame, ENERGY, 1990, 1996, ["WLD", "IND"])
    summaries = [
        summarize_depletion(windowed, ENERGY, "WLD", 1990, country_name="World"),
        summarize_depletion(windowed, ENERGY, "IND", 1990, country_name="India"),
    ]
    table = summary_export_frame(summaries, ENVIRONMENT_INDICATORS)

    assert list(table.columns) == SUMMARY_COLUMNS
    assert len(table) == 2
    world = table[table["country_code"] == "WLD"].iloc[0]
    assert world["birth_year"] == 1990
    assert world["birth_year_value"] == pytest.approx(400.0)
    assert world["latest_value"] == pytest.approx(500.0)
    assert world["latest_reported_year"] == 1996
    assert world["cumulative_depletion"] == pytest.approx(2500.0)
    assert world["peak_year"] == 1992
    assert world["peak_value"] == pytest.approx(900.0)


def test_summary_export_frame_leaves_missing_values_blank():
    summaries = [summarize_depletion(empty_frame(), ENERGY, "WLD", 1990, country_name="World")]
    table = summary_export_frame(summaries, ENVIRONMENT_INDICATORS)
    row = table.iloc[0]
    assert pd.isna(row["latest_value"])
    assert pd.isna(row["peak_year"])
    assert row["reported_years"] == 0


def test_methodology_frame_carries_source_definitions_caveats_and_timestamp():
    stamp = datetime(2026, 5, 1, 12, 30, tzinfo=timezone.utc)
    table = methodology_frame(
        ENVIRONMENT_INDICATORS,
        ENVIRONMENT_SOURCE_ATTRIBUTION,
        ENVIRONMENT_CAVEATS,
        generated_at=stamp,
        context={"Birth year": "1990"},
    )
    sections = set(table["section"])
    assert {"Source", "Indicator definitions", "Caveats", "Selection", "Export"} <= sections

    definitions = table[table["section"] == "Indicator definitions"]
    assert len(definitions) == len(ENVIRONMENT_INDICATORS)
    assert len(table[table["section"] == "Caveats"]) == len(ENVIRONMENT_CAVEATS)
    assert "2026-05-01 12:30:00 UTC" in table[table["section"] == "Export"]["detail"].iloc[0]


# --- workbook ---------------------------------------------------------------

def read_sheets(payload: bytes) -> dict[str, pd.DataFrame]:
    return pd.read_excel(BytesIO(payload), sheet_name=None, engine="openpyxl")


def test_workbook_has_the_three_named_sheets(frame):
    windowed = window_frame(frame, ENERGY, 1990, 1996, ["WLD"])
    summaries = [summarize_depletion(windowed, ENERGY, "WLD", 1990, country_name="World")]
    payload = build_environment_workbook(
        windowed,
        summaries,
        ENVIRONMENT_INDICATORS,
        ENVIRONMENT_SOURCE_ATTRIBUTION,
        ENVIRONMENT_CAVEATS,
        context={"Birth year": "1990"},
    )

    sheets = read_sheets(payload)
    assert list(sheets) == list(EXPORT_SHEETS)
    assert list(sheets["annual_data"].columns) == ANNUAL_COLUMNS
    assert len(sheets["annual_data"]) == 4
    assert sheets["summary"].iloc[0]["peak_year"] == 1992
    assert "Caveats" in set(sheets["methodology"]["section"])


def test_workbook_of_an_empty_selection_still_opens_with_all_sheets():
    payload = build_environment_workbook(
        empty_frame(),
        [],
        ENVIRONMENT_INDICATORS,
        ENVIRONMENT_SOURCE_ATTRIBUTION,
        ENVIRONMENT_CAVEATS,
    )
    sheets = read_sheets(payload)
    assert list(sheets) == list(EXPORT_SHEETS)
    assert sheets["annual_data"].empty
    assert sheets["summary"].empty
    assert not sheets["methodology"].empty  # caveats travel even with no data


def test_build_workbook_tolerates_none_frames():
    sheets = read_sheets(build_workbook(None, None, None))
    assert list(sheets) == list(EXPORT_SHEETS)
