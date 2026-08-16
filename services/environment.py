"""Environmental resource-depletion indicators.

A thin registry-plus-fetch layer over :mod:`services.world_bank`. It adds no
new transport: the HTTP client, retry policy, caching and tidy-frame contract
are all reused, so these series arrive in exactly the same shape as the
development indicators — ``indicator, country_code, country_name, year, value``.

Why a separate module rather than four more entries in ``world_bank.INDICATORS``
-------------------------------------------------------------------------------
The development registry drives the hero chips, the "what changed the most"
ranking and the narrative in *My Story*. Depletion series answer a different
question and must not be ranked against life expectancy, so they live in their
own registry and are fetched on their own.

What these series are, and are not
----------------------------------
Three of the four are adjusted-savings depletion series: the **value of
resources drawn down in a given year**, in current US dollars, and a recorded
annual flow. The fourth, total natural resources rents, is a standalone WDI
ratio and is *not* part of that account. None of them is, or may be presented
as, a stock of what remains underground.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd

from services.world_bank import Indicator, fetch_indicators

__all__ = [
    "ENVIRONMENT_INDICATORS",
    "DEFAULT_ENVIRONMENT_CODES",
    "ENVIRONMENT_SOURCE_ATTRIBUTION",
    "ENVIRONMENT_CAVEATS",
    "fetch_environment_indicators",
    "get_environment_indicator",
    "is_cumulative_meaningful",
]

# Deliberately names both families. Three of these four series are lines in the
# adjusted net (genuine) savings account; total natural resources rents is not,
# and attributing it to that account would be wrong.
ENVIRONMENT_SOURCE_ATTRIBUTION = (
    "Source: World Bank Open Data — World Development Indicators "
    "(adjusted savings depletion series and natural resources rents)"
)

# The four depletion indicators. Same dataclass as the development registry, so
# formatting, cards and charts need no special cases.
ENVIRONMENT_INDICATORS: dict[str, Indicator] = {
    "NY.ADJ.DNGY.CD": Indicator(
        code="NY.ADJ.DNGY.CD",
        label="Adjusted savings: energy depletion (current US$)",
        short_label="Energy depletion",
        beat="How much energy stock was drawn down",
        unit="current US$",
        decimals=0,
        # Depletion is neither good nor bad on its own — it falls in recessions
        # as readily as it falls through efficiency — so no direction is claimed.
        better="neutral",
        is_percentage=False,
        emoji="🛢️",
        note=(
            "Energy depletion is the value of the stock of energy resources "
            "(coal, crude oil, natural gas) drawn down in that year, valued at "
            "resource rents. It is a recorded annual flow, not a measure of "
            "reserves remaining. Reported as a line of the World Bank's "
            "adjusted net (genuine) savings account."
        ),
    ),
    "NY.ADJ.DMIN.CD": Indicator(
        code="NY.ADJ.DMIN.CD",
        label="Adjusted savings: mineral depletion (current US$)",
        short_label="Mineral depletion",
        beat="How much mineral stock was drawn down",
        unit="current US$",
        decimals=0,
        better="neutral",
        is_percentage=False,
        emoji="⛏️",
        note=(
            "Mineral depletion covers bauxite, copper, gold, iron ore, lead, "
            "nickel, phosphate, silver, tin and zinc, valued at resource rents "
            "for the year reported. It is a recorded annual flow, not a stock "
            "of remaining ore. Reported as a line of the World Bank's adjusted "
            "net (genuine) savings account."
        ),
    ),
    "NY.ADJ.DFOR.CD": Indicator(
        code="NY.ADJ.DFOR.CD",
        label="Adjusted savings: net forest depletion (current US$)",
        short_label="Net forest depletion",
        beat="How much forest was harvested beyond regrowth",
        unit="current US$",
        decimals=0,
        better="neutral",
        is_percentage=False,
        emoji="🌳",
        note=(
            "Net forest depletion values roundwood harvested in excess of "
            "natural growth. Where harvest does not exceed growth the World "
            "Bank reports zero, so long runs of zeroes are real reported "
            "values rather than missing data. Reported as a line of the World "
            "Bank's adjusted net (genuine) savings account."
        ),
    ),
    "NY.GDP.TOTL.RT.ZS": Indicator(
        code="NY.GDP.TOTL.RT.ZS",
        label="Total natural resources rents (% of GDP)",
        short_label="Resource rents",
        beat="How much of the economy comes from resource extraction",
        unit="% of GDP",
        decimals=2,
        better="neutral",
        is_percentage=True,
        emoji="💵",
        note=(
            "Total natural resources rents are the sum of oil, natural gas, "
            "coal, mineral and forest rents as a share of GDP. Being a share "
            "of GDP, annual values describe intensity and cannot be added "
            "across years. This is a standalone World Development Indicators "
            "series — not a line of the adjusted savings account — though it "
            "draws on the same resource-rent estimates."
        ),
    ),
}

DEFAULT_ENVIRONMENT_CODES: tuple[str, ...] = tuple(ENVIRONMENT_INDICATORS)

# Caveats carried into the UI footnote and the exported methodology sheet. One
# list, so the workbook and the screen can never drift apart.
ENVIRONMENT_CAVEATS: tuple[str, ...] = (
    "These are World Bank resource depletion indicators: the value of "
    "resources recorded as drawn down in each reported year.",
    "They are not a measure of reserves remaining underground. Nothing here "
    "estimates how much of any resource is left.",
    "All values are the World Bank's reported series. Years the World Bank did "
    "not report are absent, never interpolated or filled.",
    "Currency series are in current US$ — the prices of each year they "
    "describe. Cumulative totals are nominal sums and are not adjusted for "
    "inflation.",
    "The most recent year shown is the latest year reported by the World Bank, "
    "not the current calendar year.",
    "A reported zero means the World Bank recorded no depletion of that kind "
    "in that year; it does not mean the data is missing.",
)


def get_environment_indicator(code: str) -> Indicator:
    """Look up a depletion indicator, falling back to a generic descriptor.

    Mirrors ``world_bank.get_indicator`` so an unknown code degrades into a
    plain label rather than a KeyError in the middle of a render.
    """
    if code in ENVIRONMENT_INDICATORS:
        return ENVIRONMENT_INDICATORS[code]
    return Indicator(
        code=code,
        label=code,
        short_label=code,
        beat="",
        unit="",
        decimals=2,
        better="neutral",
        is_percentage=False,
        emoji="🌍",
    )


def is_cumulative_meaningful(code: str) -> bool:
    """Whether summing this indicator across years produces a real quantity.

    True for the current-US$ depletion flows, false for the ratio series:
    adding "% of GDP" across years yields a number with no meaning, and this
    app does not print numbers with no meaning.
    """
    return not get_environment_indicator(code).is_percentage


def fetch_environment_indicators(
    country_codes: Sequence[str],
    start_year: int,
    end_year: int,
    codes: Sequence[str] | None = None,
    progress=None,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Fetch the depletion series, tolerating individual indicator failures.

    Returns the same ``(tidy_frame, errors)`` pair as
    ``world_bank.fetch_indicators``; one failed series never blocks the rest.
    """
    return fetch_indicators(
        list(codes or DEFAULT_ENVIRONMENT_CODES),
        country_codes,
        start_year,
        end_year,
        progress=progress,
        label_for=lambda code: get_environment_indicator(code).short_label,
    )
