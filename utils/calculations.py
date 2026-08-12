"""Metric computation over the tidy indicator frame.

Every function here is pure: DataFrames and numbers in, dataclasses and numbers
out. No I/O, no Streamlit, no formatting. That keeps the interesting logic —
year selection and change arithmetic — testable in isolation.

The governing rule: **never invent a value.** When data is absent these
functions return ``None`` and let the caller render "Data unavailable".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd

# Approximate physiological rates, used only for clearly-labelled estimates.
HEARTBEATS_PER_MINUTE = 72
BREATHS_PER_MINUTE = 16
DAYS_PER_YEAR = 365.2425

# Astronomical constants behind the "since you were born" estimates.
LUNAR_CYCLE_DAYS = 29.530588      # one synodic month
ORBITAL_SPEED_KM_S = 29.78        # Earth's mean speed around the Sun


@dataclass(frozen=True)
class Observation:
    """A single value together with the year it was actually reported."""

    year: int
    value: float

    @property
    def is_estimate_of(self) -> int:
        return self.year


@dataclass(frozen=True)
class Change:
    """The change in one indicator between two real observations."""

    indicator: str
    country_code: str
    start: Observation
    end: Observation
    absolute: float
    percent: Optional[float]         # None when the baseline is zero
    percentage_points: Optional[float]  # only for percentage indicators

    @property
    def years_elapsed(self) -> int:
        return self.end.year - self.start.year

    @property
    def direction(self) -> str:
        if self.absolute > 0:
            return "up"
        if self.absolute < 0:
            return "down"
        return "flat"


# ---------------------------------------------------------------------------
# Series access
# ---------------------------------------------------------------------------

def get_series(frame: pd.DataFrame, indicator: str, country_code: str) -> pd.Series:
    """Return one country's series for one indicator as ``year -> value``.

    Always sorted by year, always free of nulls (the parser drops them), and
    empty rather than raising when the combination is absent.
    """
    if frame is None or frame.empty:
        return pd.Series(dtype="float64", name=indicator)

    subset = frame[
        (frame["indicator"] == indicator) & (frame["country_code"] == country_code.upper())
    ]
    if subset.empty:
        return pd.Series(dtype="float64", name=indicator)

    series = subset.set_index("year")["value"].dropna().sort_index()
    series.name = indicator
    return series[~series.index.duplicated(keep="last")]


def available_years(series: pd.Series) -> list[int]:
    return [int(year) for year in series.index]


def latest_available(series: pd.Series) -> Optional[Observation]:
    """The most recent year with a real reported value.

    This is the app's replacement for "today". World Bank series lag reality by
    one to three years, so the newest observation is rarely the current year.
    """
    if series.empty:
        return None
    year = int(series.index.max())
    return Observation(year=year, value=float(series.loc[year]))


def earliest_available(series: pd.Series) -> Optional[Observation]:
    if series.empty:
        return None
    year = int(series.index.min())
    return Observation(year=year, value=float(series.loc[year]))


def nearest_available(
    series: pd.Series,
    target_year: int,
    max_distance: Optional[int] = None,
) -> Optional[Observation]:
    """The observation closest to ``target_year``.

    Used for the birth-year baseline, because plenty of series do not start
    until after a given birth year (internet usage is the usual culprit).
    Ties prefer the *later* year, so a baseline sits inside the lifetime rather
    than before it. Returns ``None`` when nothing is within ``max_distance``.
    """
    if series.empty:
        return None

    years = series.index.to_numpy()
    distances = abs(years - target_year)
    best = int(distances.min())

    if max_distance is not None and best > max_distance:
        return None

    candidates = [int(year) for year, dist in zip(years, distances) if dist == best]
    chosen = max(candidates)  # tie -> later year
    return Observation(year=chosen, value=float(series.loc[chosen]))


def has_data(series: pd.Series) -> bool:
    return not series.empty


# ---------------------------------------------------------------------------
# Change arithmetic
# ---------------------------------------------------------------------------

def compute_change(
    frame: pd.DataFrame,
    indicator: str,
    country_code: str,
    birth_year: int,
    is_percentage: bool = False,
    max_distance: Optional[int] = None,
) -> Optional[Change]:
    """Change from the birth-year baseline to the latest reported value.

    Returns ``None`` when the series is missing, or when only one observation
    exists — a single point is not a trend and must not be presented as one.
    """
    series = get_series(frame, indicator, country_code)
    start = nearest_available(series, birth_year, max_distance=max_distance)
    end = latest_available(series)

    if start is None or end is None or start.year == end.year:
        return None

    absolute = end.value - start.value
    percent = (absolute / start.value * 100.0) if start.value else None

    return Change(
        indicator=indicator,
        country_code=country_code.upper(),
        start=start,
        end=end,
        absolute=absolute,
        percent=percent,
        percentage_points=absolute if is_percentage else None,
    )


def relative_magnitude(change: Change) -> float:
    """A scale-free size for ranking changes across unlike indicators.

    Percent change where the baseline allows it; otherwise zero, so an
    indicator with a zero baseline never wins "changed the most" on a
    technicality.
    """
    return abs(change.percent) if change.percent is not None else 0.0


def largest_change(changes: dict[str, Change]) -> Optional[tuple[str, Change]]:
    """The indicator that moved most in relative terms."""
    ranked = [(code, ch) for code, ch in changes.items() if ch.percent is not None]
    if not ranked:
        return None
    return max(ranked, key=lambda item: relative_magnitude(item[1]))


def strongest_improvement(
    changes: dict[str, Change],
    directions: dict[str, str],
) -> Optional[tuple[str, Change]]:
    """The largest move in the direction that counts as better.

    ``directions`` maps indicator code to "up", "down" or "neutral"; neutral
    indicators are excluded because "improvement" is undefined for them.
    """
    candidates: list[tuple[str, Change]] = []
    for code, change in changes.items():
        better = directions.get(code, "neutral")
        if better == "up" and change.absolute > 0:
            candidates.append((code, change))
        elif better == "down" and change.absolute < 0:
            candidates.append((code, change))

    if not candidates:
        return None
    return max(candidates, key=lambda item: relative_magnitude(item[1]))


def largest_gap(
    frame: pd.DataFrame,
    indicator_codes: list[str],
    country_a: str,
    country_b: str,
) -> Optional[tuple[str, float, int]]:
    """Indicator with the biggest relative gap between two countries.

    Returns ``(indicator_code, percent_difference, comparison_year)`` where the
    comparison year is the latest year *both* countries reported — comparing
    across different years would be misleading.
    """
    best: Optional[tuple[str, float, int]] = None

    for code in indicator_codes:
        series_a = get_series(frame, code, country_a)
        series_b = get_series(frame, code, country_b)
        shared = sorted(set(series_a.index) & set(series_b.index))
        if not shared:
            continue

        year = int(shared[-1])
        value_a = float(series_a.loc[year])
        value_b = float(series_b.loc[year])
        if not value_a and not value_b:
            continue

        denominator = min(abs(value_a), abs(value_b)) or max(abs(value_a), abs(value_b))
        gap = abs(value_a - value_b) / denominator * 100.0

        if best is None or gap > best[1]:
            best = (code, gap, year)

    return best


def common_year(frame: pd.DataFrame, indicator: str, country_codes: list[str]) -> Optional[int]:
    """Latest year for which every listed country reported this indicator."""
    year_sets = []
    for code in country_codes:
        series = get_series(frame, indicator, code)
        if series.empty:
            return None
        year_sets.append(set(int(y) for y in series.index))

    shared = set.intersection(*year_sets) if year_sets else set()
    return max(shared) if shared else None


# ---------------------------------------------------------------------------
# Personal arithmetic
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LifeSpan:
    """Everything derivable from a date of birth alone."""

    birth_date: date
    as_of: date
    years: int
    months: int
    days_total: int

    @property
    def sunrises(self) -> int:
        """One sunrise per day lived — so numerically identical to days_total.

        Kept because it is a nicer way to say the same thing in prose, but it
        must never occupy its own stat card beside the day count: two cards
        showing an identical number look like a bug.
        """
        return self.days_total

    @property
    def full_moons(self) -> int:
        """Full moons since birth — one per synodic month."""
        return int(self.days_total / LUNAR_CYCLE_DAYS)

    @property
    def orbits(self) -> float:
        """Laps of the Sun completed, including the partial one in progress."""
        return self.days_total / DAYS_PER_YEAR

    @property
    def km_around_sun(self) -> int:
        """Distance carried through space by Earth's orbit, in kilometres."""
        return int(self.days_total * 24 * 60 * 60 * ORBITAL_SPEED_KM_S)

    @property
    def heartbeats(self) -> int:
        return int(self.days_total * 24 * 60 * HEARTBEATS_PER_MINUTE)

    @property
    def breaths(self) -> int:
        return int(self.days_total * 24 * 60 * BREATHS_PER_MINUTE)


def life_span(birth_date: date, as_of: Optional[date] = None) -> LifeSpan:
    """Exact age plus the day count behind the estimate statistics."""
    as_of = as_of or date.today()

    years = as_of.year - birth_date.year
    months = as_of.month - birth_date.month
    if as_of.day < birth_date.day:
        months -= 1
    if months < 0:
        years -= 1
        months += 12

    return LifeSpan(
        birth_date=birth_date,
        as_of=as_of,
        years=max(years, 0),
        months=max(months, 0),
        days_total=max((as_of - birth_date).days, 0),
    )


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

# Life stages shown on the personal timeline. Ages beyond the user's current
# age are dropped, so a 12-year-old does not see an "Age 30" marker.
TIMELINE_AGES: tuple[int, ...] = (0, 5, 10, 18, 21, 30)

# How far the timeline may look for a statistic before giving up. Three years
# covers the World Bank's reporting lag without silently reaching so far that
# the number stops describing the year in question.
TIMELINE_MAX_DISTANCE = 3


@dataclass(frozen=True)
class TimelinePoint:
    """One marker on the personal timeline."""

    label: str
    year: int
    age: int
    is_latest: bool = False


def timeline_years(
    birth_date: date,
    latest_year: Optional[int] = None,
    as_of: Optional[date] = None,
) -> list[TimelinePoint]:
    """Build the personal timeline: birth, milestone ages, latest data year.

    Milestone ages the user has not reached are omitted. The latest available
    data year is appended when it is not already a milestone year, so the
    timeline always ends on real data.
    """
    as_of = as_of or date.today()
    current_age = life_span(birth_date, as_of).years
    birth_year = birth_date.year

    points: list[TimelinePoint] = []
    for age in TIMELINE_AGES:
        if age > current_age:
            continue
        label = "Birth" if age == 0 else f"Age {age}"
        points.append(TimelinePoint(label=label, year=birth_year + age, age=age))

    if latest_year is not None:
        existing = {point.year for point in points}
        if latest_year not in existing and latest_year >= birth_year:
            points.append(
                TimelinePoint(
                    label=f"Latest data ({latest_year})",
                    year=latest_year,
                    age=latest_year - birth_year,
                    is_latest=True,
                )
            )

    return sorted(points, key=lambda point: point.year)


def timeline_observation(
    frame: pd.DataFrame,
    indicator: str,
    country_code: str,
    year: int,
) -> Optional[Observation]:
    """The statistic for a timeline year, or the nearest within three years.

    Returns ``None`` rather than reaching further, so the caller shows
    "Data unavailable" instead of a value that does not describe the year.
    """
    series = get_series(frame, indicator, country_code)
    return nearest_available(series, year, max_distance=TIMELINE_MAX_DISTANCE)


def milestones_in_range(
    milestones: pd.DataFrame,
    start_year: int,
    end_year: int,
    categories: Optional[list[str]] = None,
) -> pd.DataFrame:
    """Curated events inside a lifespan, optionally filtered by category.

    Adds an ``age`` column: how old the person was when each event happened.
    """
    if milestones is None or milestones.empty:
        return milestones if milestones is not None else pd.DataFrame()

    subset = milestones[
        (milestones["year"] >= start_year) & (milestones["year"] <= end_year)
    ].copy()

    if categories:
        subset = subset[subset["category"].isin(categories)]

    if subset.empty:
        return subset

    subset["age"] = subset["year"] - start_year
    return subset.sort_values(["year", "category", "title"], ignore_index=True)


def milestones_for_year(milestones: pd.DataFrame, year: int) -> pd.DataFrame:
    """Events in one specific year. Empty when that year has none."""
    if milestones is None or milestones.empty:
        return milestones if milestones is not None else pd.DataFrame()
    return milestones[milestones["year"] == year]


# ---------------------------------------------------------------------------
# Coverage reporting (used by the integration check and the footnotes)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Coverage:
    indicator: str
    country_code: str
    earliest_year: Optional[int]
    latest_year: Optional[int]
    observations: int
    expected_years: int
    missing_years: list[int]

    @property
    def is_complete(self) -> bool:
        return not self.missing_years and self.observations > 0

    @property
    def issue(self) -> str:
        if self.observations == 0:
            return "No data returned"
        if not self.missing_years:
            return "None"
        head = ", ".join(str(y) for y in self.missing_years[:4])
        suffix = f" (+{len(self.missing_years) - 4} more)" if len(self.missing_years) > 4 else ""
        return f"{len(self.missing_years)} missing: {head}{suffix}"


def coverage(
    frame: pd.DataFrame,
    indicator: str,
    country_code: str,
    start_year: int,
    end_year: int,
) -> Coverage:
    """Describe which years in a requested range actually came back."""
    series = get_series(frame, indicator, country_code)
    expected = list(range(start_year, end_year + 1))
    present = set(int(year) for year in series.index)
    missing = [year for year in expected if year not in present]

    return Coverage(
        indicator=indicator,
        country_code=country_code.upper(),
        earliest_year=int(series.index.min()) if not series.empty else None,
        latest_year=int(series.index.max()) if not series.empty else None,
        observations=int(series.size),
        expected_years=len(expected),
        missing_years=missing,
    )
