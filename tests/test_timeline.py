"""Tests for the personal timeline and the curated milestone file.

Two things matter here and are covered explicitly:

* the three-year nearest-observation rule, which must never silently reach
  further or interpolate; and
* the integrity of ``data/milestones.csv``, since a malformed row would
  otherwise surface as a broken card in the UI.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.calculations import (  # noqa: E402
    TIMELINE_AGES,
    TIMELINE_MAX_DISTANCE,
    milestones_for_year,
    milestones_in_range,
    timeline_observation,
    timeline_years,
)
from utils.narratives import timeline_interpretation  # noqa: E402
from services.world_bank import INDICATORS  # noqa: E402

MILESTONES_PATH = ROOT / "data" / "milestones.csv"
REQUIRED_COLUMNS = ["year", "category", "title", "description", "source_url"]


def build_frame(records):
    return pd.DataFrame(
        records, columns=["indicator", "country_code", "country_name", "year", "value"]
    )


@pytest.fixture
def frame():
    rows = [
        ("SP.DYN.LE00.IN", "IND", "India", year, value)
        for year, value in [(1990, 58.6), (1995, 60.5), (2000, 62.5), (2024, 72.2)]
    ]
    # A series with a wide hole: nothing between 1996 and 2020.
    rows += [
        ("IT.NET.USER.ZS", "IND", "India", year, value)
        for year, value in [(1996, 0.05), (2020, 43.0)]
    ]
    return build_frame(rows)


@pytest.fixture(scope="module")
def milestones():
    return pd.read_csv(MILESTONES_PATH)


# --- timeline construction --------------------------------------------------

def test_timeline_includes_reached_ages_only():
    points = timeline_years(date(1990, 6, 25), latest_year=2025, as_of=date(2026, 8, 12))
    ages = [point.age for point in points if not point.is_latest]
    assert ages == [0, 5, 10, 18, 21, 30]


def test_timeline_omits_unreached_ages():
    """A twelve-year-old must not see an Age 18 marker."""
    points = timeline_years(date(2014, 1, 1), latest_year=2025, as_of=date(2026, 8, 12))
    ages = [point.age for point in points if not point.is_latest]
    assert ages == [0, 5, 10]
    assert 18 not in ages


def test_timeline_labels_birth_and_ages():
    points = timeline_years(date(1990, 6, 25), latest_year=2025, as_of=date(2026, 8, 12))
    labels = [point.label for point in points]
    assert labels[0] == "Birth"
    assert "Age 5" in labels
    assert any(label.startswith("Latest data") for label in labels)


def test_timeline_appends_latest_year_with_correct_age():
    points = timeline_years(date(1990, 6, 25), latest_year=2025, as_of=date(2026, 8, 12))
    latest = [point for point in points if point.is_latest]
    assert len(latest) == 1
    assert latest[0].year == 2025
    assert latest[0].age == 35


def test_timeline_does_not_duplicate_latest_year():
    """If the latest data year is already a milestone year, do not repeat it."""
    points = timeline_years(date(1990, 1, 1), latest_year=2020, as_of=date(2026, 8, 12))
    years = [point.year for point in points]
    assert years.count(2020) == 1


def test_timeline_is_sorted_by_year():
    points = timeline_years(date(1990, 6, 25), latest_year=2025, as_of=date(2026, 8, 12))
    years = [point.year for point in points]
    assert years == sorted(years)


def test_timeline_without_latest_year():
    points = timeline_years(date(1990, 6, 25), latest_year=None, as_of=date(2026, 8, 12))
    assert all(not point.is_latest for point in points)
    assert len(points) == len(TIMELINE_AGES)


def test_timeline_for_newborn_has_birth_point_only():
    points = timeline_years(date(2026, 8, 1), latest_year=None, as_of=date(2026, 8, 12))
    assert len(points) == 1
    assert points[0].label == "Birth"


# --- the three-year rule ----------------------------------------------------

def test_observation_exact_year_when_available(frame):
    observation = timeline_observation(frame, "SP.DYN.LE00.IN", "IND", 1995)
    assert observation.year == 1995
    assert observation.value == pytest.approx(60.5)


def test_observation_falls_back_to_nearest_within_three_years(frame):
    """1998 has no value; 2000 is two years away and is used, flagged as 2000."""
    observation = timeline_observation(frame, "SP.DYN.LE00.IN", "IND", 1998)
    assert observation.year == 2000
    assert observation.value == pytest.approx(62.5)


def test_observation_returns_none_beyond_three_years(frame):
    """2008 sits in a 24-year hole — no value may be shown at all."""
    assert timeline_observation(frame, "IT.NET.USER.ZS", "IND", 2008) is None


def test_three_year_boundary_is_inclusive(frame):
    """Exactly three years away is allowed; four is not."""
    assert timeline_observation(frame, "IT.NET.USER.ZS", "IND", 1993).year == 1996
    assert timeline_observation(frame, "IT.NET.USER.ZS", "IND", 1992) is None
    assert TIMELINE_MAX_DISTANCE == 3


def test_observation_never_interpolates(frame):
    """Returned values must be real observations, not computed midpoints."""
    real_values = set(frame["value"])
    for year in range(1990, 2025):
        observation = timeline_observation(frame, "SP.DYN.LE00.IN", "IND", year)
        if observation is not None:
            assert observation.value in real_values


def test_observation_missing_country_returns_none(frame):
    assert timeline_observation(frame, "SP.DYN.LE00.IN", "BRA", 1995) is None


# --- interpretation ---------------------------------------------------------

def test_interpretation_reports_unavailable_without_observation():
    text = timeline_interpretation(
        INDICATORS["SP.DYN.LE00.IN"], "India", 2008, 18, None, None
    )
    assert "no" in text.lower()
    assert "within three years" in text


def test_interpretation_flags_a_substituted_year(frame):
    observation = timeline_observation(frame, "SP.DYN.LE00.IN", "IND", 1998)
    text = timeline_interpretation(
        INDICATORS["SP.DYN.LE00.IN"], "India", 1998, 8, observation, None
    )
    assert "2000" in text
    assert "2 years later" in text


def test_interpretation_states_exact_year_when_no_substitution(frame):
    observation = timeline_observation(frame, "SP.DYN.LE00.IN", "IND", 1995)
    text = timeline_interpretation(
        INDICATORS["SP.DYN.LE00.IN"], "India", 1995, 5, observation, None
    )
    assert "for 1995 itself" in text


def test_interpretation_compares_against_birth_year(frame):
    birth = timeline_observation(frame, "SP.DYN.LE00.IN", "IND", 1990)
    observation = timeline_observation(frame, "SP.DYN.LE00.IN", "IND", 2024)
    text = timeline_interpretation(
        INDICATORS["SP.DYN.LE00.IN"], "India", 2024, 34, observation, birth
    )
    assert "higher" in text
    assert "1990" in text


def test_interpretation_uses_birth_wording_at_age_zero(frame):
    observation = timeline_observation(frame, "SP.DYN.LE00.IN", "IND", 1990)
    text = timeline_interpretation(
        INDICATORS["SP.DYN.LE00.IN"], "India", 1990, 0, observation, None
    )
    assert "the year you were born" in text


# --- milestone filtering ----------------------------------------------------

def test_milestones_filtered_to_lifespan(milestones):
    subset = milestones_in_range(milestones, 1990, 2025)
    assert subset["year"].min() >= 1990
    assert subset["year"].max() <= 2025
    assert 1985 not in set(subset["year"])   # before birth, excluded


def test_milestones_carry_age_at_event(milestones):
    subset = milestones_in_range(milestones, 1990, 2025)
    row = subset[subset["year"] == 2007].iloc[0]
    assert row["age"] == 17


def test_milestones_category_filter(milestones):
    subset = milestones_in_range(milestones, 1990, 2025, categories=["Technology"])
    assert set(subset["category"]) == {"Technology"}


def test_milestones_unmatched_category_returns_empty(milestones):
    subset = milestones_in_range(milestones, 1990, 2025, categories=["Nonexistent"])
    assert subset.empty


def test_milestones_outside_lifespan_returns_empty(milestones):
    assert milestones_in_range(milestones, 1900, 1910).empty


def test_milestones_for_year_empty_when_no_event(milestones):
    """A timeline year with no milestone yields nothing to render."""
    subset = milestones_in_range(milestones, 1990, 2025)
    assert milestones_for_year(subset, 2013).empty
    assert not milestones_for_year(subset, 2007).empty


def test_milestones_handles_empty_frame():
    empty = pd.DataFrame(columns=REQUIRED_COLUMNS)
    assert milestones_in_range(empty, 1990, 2025).empty
    assert milestones_for_year(empty, 2000).empty


# --- milestone file integrity ----------------------------------------------

def test_milestones_file_exists_and_has_required_columns(milestones):
    assert list(milestones.columns) == REQUIRED_COLUMNS


def test_milestones_have_no_missing_fields(milestones):
    assert not milestones.isnull().any().any()


def test_milestone_years_are_plausible(milestones):
    assert milestones["year"].between(1900, date.today().year).all()


def test_every_milestone_has_a_working_source_link(milestones):
    assert milestones["source_url"].str.startswith("http").all()


def test_milestone_categories_are_from_a_known_set(milestones):
    allowed = {"Science", "Technology", "World events", "Health", "Space"}
    assert set(milestones["category"]) <= allowed


def test_milestone_file_covers_recent_decades(milestones):
    """A 1990 birth year must produce a populated timeline."""
    assert len(milestones_in_range(milestones, 1990, 2025)) >= 20


def test_no_nobel_prize_placeholder_data(milestones):
    """Nobel Prize integration is deferred; no hard-coded rows may sneak in."""
    text = " ".join(milestones["title"]) + " " + " ".join(milestones["description"])
    assert "nobel" not in text.lower()
