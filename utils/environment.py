"""Resource-depletion computation and workbook assembly.

Pure functions over the tidy indicator frame, in the style of
:mod:`utils.calculations`: DataFrames and numbers in, dataclasses, DataFrames
and bytes out. No I/O beyond an in-memory buffer, no Streamlit, no formatting.

Two rules govern this module:

* **Never invent a value.** An absent year stays absent; every summary field is
  ``None`` when the series cannot support it.
* **Never sum something that cannot be summed.** Depletion in current US$ is an
  annual flow and adds up to a nominal lifetime total. A "% of GDP" series is a
  ratio, and its cumulative total is refused rather than printed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Iterable, Optional, Sequence

import pandas as pd

from utils.calculations import Observation, get_series, latest_available, nearest_available

# Sheets of the exported workbook, in order.
EXPORT_SHEETS: tuple[str, ...] = ("annual_data", "summary", "methodology")

ANNUAL_COLUMNS = [
    "year",
    "country_code",
    "country_name",
    "indicator_code",
    "indicator_label",
    "value",
    "unit",
]

SUMMARY_COLUMNS = [
    "birth_year",
    "indicator_code",
    "indicator_label",
    "geography",
    "country_code",
    "birth_year_value",
    "birth_value_year",
    "latest_value",
    "latest_reported_year",
    "cumulative_depletion",
    "peak_year",
    "peak_value",
    "reported_years",
    "unit",
]

METHODOLOGY_COLUMNS = ["section", "item", "detail"]


# ---------------------------------------------------------------------------
# Series helpers
# ---------------------------------------------------------------------------

def window_frame(
    frame: pd.DataFrame,
    indicator: str,
    start_year: int,
    end_year: int,
    country_codes: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """Restrict the tidy frame to one indicator, a year range and some countries.

    Returns an empty frame with the same columns when nothing matches, so
    callers can keep treating the result as a frame.
    """
    if frame is None or frame.empty:
        return frame if frame is not None else pd.DataFrame()

    subset = frame[
        (frame["indicator"] == indicator)
        & (frame["year"] >= start_year)
        & (frame["year"] <= end_year)
    ]
    if country_codes is not None:
        wanted = {str(code).upper() for code in country_codes}
        subset = subset[subset["country_code"].isin(wanted)]
    return subset.copy()


@dataclass(frozen=True)
class YearWindow:
    """The year bounds a selector should offer for one indicator.

    Exists so the degenerate case has a name: an indicator with exactly one
    reported year cannot be given a range slider, because a slider needs
    ``min < max``. The caller checks ``is_single_year`` instead of discovering
    that at render time.
    """

    first: int
    last: int
    default_start: int

    @property
    def is_single_year(self) -> bool:
        return self.first == self.last


def year_window(
    years: Iterable[int],
    birth_year: Optional[int] = None,
) -> Optional[YearWindow]:
    """Bounds and opening position for a year-range selector.

    With a ``birth_year``, the window opens on it where the series covers it,
    on the first reported year for a birth that predates the data, and on the
    last reported year for a birth that postdates it. Without one it opens on
    the full range. ``None`` when no year was reported.
    """
    values = sorted({int(year) for year in years})
    if not values:
        return None
    first, last = values[0], values[-1]
    default_start = first if birth_year is None else min(max(first, birth_year), last)
    return YearWindow(first=first, last=last, default_start=default_start)


def order_selection(
    entries: Sequence[tuple],
    chosen: Sequence[str],
) -> list[tuple]:
    """Entries in the order the user picked them, not registry order.

    The first entry becomes the geography the summary cards describe, so this
    has to follow the selection: telling a reader the cards show "the first
    selected geography" and then showing a different one is a lie in the UI.
    """
    position = {str(code): index for index, code in enumerate(chosen)}
    picked = [entry for entry in entries if entry[0] in position]
    return sorted(picked, key=lambda entry: position[entry[0]])


# ---------------------------------------------------------------------------
# Rank and peers among reporting countries
# ---------------------------------------------------------------------------
# The honest answer to "how does my country compare with the world" for a
# series the World Bank publishes for countries only. Ranking published values
# is a comparison; adding them into a world total would be an invented figure,
# so nothing here sums anything.

@dataclass(frozen=True)
class CountryRank:
    """Where one country sits among the countries that reported that year."""

    country_code: str
    country_name: str
    year: int
    value: float
    rank: int          # 1 = highest reported value
    reporting: int     # how many countries reported that year

    @property
    def is_top_ten(self) -> bool:
        return self.rank <= 10

    @property
    def ordinal(self) -> str:
        """"3rd", "21st" — the rank as it reads in a sentence."""
        if 10 <= self.rank % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(self.rank % 10, "th")
        return f"{self.rank}{suffix}"


def countries_only(snapshot: pd.DataFrame, country_codes: Iterable[str]) -> pd.DataFrame:
    """Drop aggregates from a snapshot, keeping real countries.

    ``country/all`` returns regions and income groups alongside countries, and
    ranking a country against "South Asia" would be meaningless.
    """
    if snapshot is None or snapshot.empty:
        return snapshot if snapshot is not None else pd.DataFrame()
    wanted = {str(code).upper() for code in country_codes}
    return snapshot[snapshot["country_code"].isin(wanted)].copy()


def rank_within(snapshot: pd.DataFrame, country_code: str) -> Optional[CountryRank]:
    """One country's rank among everyone who reported, highest value first.

    Ties share a rank (two countries at the top are both 1st). Returns ``None``
    when that country did not report, rather than inventing a position for it.
    """
    if snapshot is None or snapshot.empty:
        return None

    wanted = str(country_code).upper()
    row = snapshot[snapshot["country_code"] == wanted]
    if row.empty:
        return None

    record = row.iloc[0]
    value = float(record["value"])
    higher = int((snapshot["value"] > value).sum())

    return CountryRank(
        country_code=wanted,
        country_name=str(record.get("country_name", "") or wanted),
        year=int(record["year"]),
        value=value,
        rank=higher + 1,
        reporting=int(len(snapshot)),
    )


def top_reporters(
    snapshot: pd.DataFrame,
    limit: int = 10,
    always_include: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """The highest reported values, plus any countries that must be shown.

    ``always_include`` keeps the user's own countries on the chart even when
    they rank well outside the top, so the comparison never silently omits the
    country the reader came for. Adds a ``rank`` column.
    """
    empty = pd.DataFrame(columns=["country_code", "country_name", "year", "value", "rank"])
    if snapshot is None or snapshot.empty:
        return empty

    ordered = snapshot.sort_values("value", ascending=False).reset_index(drop=True)
    ordered["rank"] = [
        int((ordered["value"] > value).sum()) + 1 for value in ordered["value"]
    ]

    keep = ordered.head(max(limit, 0))
    for code in always_include or ():
        wanted = str(code).upper()
        if wanted not in set(keep["country_code"]):
            extra = ordered[ordered["country_code"] == wanted]
            keep = pd.concat([keep, extra], ignore_index=True)

    columns = [c for c in ["country_code", "country_name", "year", "value", "rank"] if c in keep]
    return keep[columns].reset_index(drop=True)


def peak_observation(series: pd.Series) -> Optional[Observation]:
    """The largest reported value in a series, with the year it occurred.

    Ties resolve to the **earliest** year: the first time the peak was reached
    is the year that describes it. Returns ``None`` for an empty series.
    """
    if series is None or series.empty:
        return None
    peak_value = float(series.max())
    years = [int(year) for year, value in series.items() if float(value) == peak_value]
    return Observation(year=min(years), value=peak_value)


def cumulative_total(series: pd.Series, since_year: Optional[int] = None) -> Optional[float]:
    """Sum of the reported values, optionally from ``since_year`` onward.

    This is a sum of what was *reported*. Years the World Bank did not report
    contribute nothing rather than an estimate, which makes the total a floor,
    not a complete accounting. Returns ``None`` when nothing is in range.
    """
    if series is None or series.empty:
        return None
    subset = series if since_year is None else series[series.index >= since_year]
    if subset.empty:
        return None
    return float(subset.sum())


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DepletionSummary:
    """One geography's depletion story for one indicator.

    Every numeric field is optional. A summary over a series with no
    observations is a perfectly valid object with ``None`` everywhere and
    ``has_data`` false — the empty state is data, not an error.
    """

    indicator: str
    country_code: str
    country_name: str
    birth_year: int
    birth: Optional[Observation]
    latest: Optional[Observation]
    peak: Optional[Observation]
    cumulative: Optional[float]
    reported_years: int
    first_year: Optional[int]
    last_year: Optional[int]

    @property
    def has_data(self) -> bool:
        return self.reported_years > 0

    @property
    def coverage_label(self) -> str:
        """"1990–2022" for a real span, "1990 only" for a single year."""
        if self.first_year is None or self.last_year is None:
            return "no reported years"
        if self.first_year == self.last_year:
            return f"{self.first_year} only"
        return f"{self.first_year}–{self.last_year}"

    @property
    def has_lifetime_baseline(self) -> bool:
        """Whether a baseline exists at or after the birth year.

        False when the series stops before the user was born. The baseline is
        never taken from a year before the birth year, so this is exactly
        "``birth`` was found", stated in the terms the UI cares about.
        """
        return self.birth is not None

    @property
    def ends_before_birth_year(self) -> bool:
        """Whether the whole series predates the birth year.

        The case where a lifetime framing does not apply at all: there is real
        data on screen, but none of it falls inside the user's life.
        """
        return self.latest is not None and self.latest.year < self.birth_year


def summarize_depletion(
    frame: pd.DataFrame,
    indicator: str,
    country_code: str,
    birth_year: int,
    country_name: str = "",
    cumulative: bool = True,
    max_baseline_distance: Optional[int] = None,
) -> DepletionSummary:
    """Describe one country's series: birth year, latest, peak, cumulative.

    ``frame`` is expected to be already windowed to the year range on screen,
    so every figure describes exactly what the chart shows.

    ``cumulative=False`` refuses the lifetime total — pass it for ratio
    indicators, where a sum across years has no meaning.

    The birth-year baseline is only ever taken from a year **at or after** the
    birth year. A value recorded before the user was born is not a "birth-year
    value" however close it falls, so a series that ends before their birth
    yields no baseline and no lifetime total rather than a misleading one.
    """
    series = get_series(frame, indicator, country_code)

    if series.empty:
        return DepletionSummary(
            indicator=indicator,
            country_code=country_code.upper(),
            country_name=country_name,
            birth_year=birth_year,
            birth=None,
            latest=None,
            peak=None,
            cumulative=None,
            reported_years=0,
            first_year=None,
            last_year=None,
        )

    # Forward-only baseline search: snapping to the first reported year of the
    # lifetime is honest, reaching back before the birth year is not.
    lifetime = series[series.index >= birth_year]

    return DepletionSummary(
        indicator=indicator,
        country_code=country_code.upper(),
        country_name=country_name,
        birth_year=birth_year,
        birth=nearest_available(lifetime, birth_year, max_distance=max_baseline_distance),
        latest=latest_available(series),
        peak=peak_observation(series),
        cumulative=cumulative_total(series, since_year=birth_year) if cumulative else None,
        reported_years=int(series.size),
        first_year=int(series.index.min()),
        last_year=int(series.index.max()),
    )


def share_of_peak(summary: DepletionSummary) -> Optional[float]:
    """Latest value as a percentage of the peak. ``None`` when the peak is zero."""
    if summary.latest is None or summary.peak is None or not summary.peak.value:
        return None
    return summary.latest.value / summary.peak.value * 100.0


# ---------------------------------------------------------------------------
# Export frames
# ---------------------------------------------------------------------------

def annual_export_frame(frame: pd.DataFrame, indicators: dict) -> pd.DataFrame:
    """The ``annual_data`` sheet: one row per reported observation.

    ``indicators`` maps an indicator code to an object carrying ``label`` and
    ``unit`` (the registry in ``services/environment.py``). Codes outside the
    registry keep their code as the label rather than being dropped.
    """
    empty = pd.DataFrame(columns=ANNUAL_COLUMNS)
    if frame is None or frame.empty:
        return empty

    table = frame.copy()
    labels = {code: getattr(ind, "label", code) for code, ind in indicators.items()}
    units = {code: getattr(ind, "unit", "") for code, ind in indicators.items()}

    table["indicator_code"] = table["indicator"]
    table["indicator_label"] = table["indicator"].map(labels).fillna(table["indicator"])
    table["unit"] = table["indicator"].map(units).fillna("")

    table = table[ANNUAL_COLUMNS].sort_values(
        ["indicator_code", "country_code", "year"], ignore_index=True
    )
    return table


def summary_export_frame(
    summaries: Iterable[DepletionSummary],
    indicators: dict,
) -> pd.DataFrame:
    """The ``summary`` sheet: one row per geography in the current selection."""
    records = []
    for summary in summaries:
        indicator = indicators.get(summary.indicator)
        records.append(
            {
                "birth_year": summary.birth_year,
                "indicator_code": summary.indicator,
                "indicator_label": getattr(indicator, "label", summary.indicator),
                "geography": summary.country_name or summary.country_code,
                "country_code": summary.country_code,
                "birth_year_value": summary.birth.value if summary.birth else None,
                "birth_value_year": summary.birth.year if summary.birth else None,
                "latest_value": summary.latest.value if summary.latest else None,
                "latest_reported_year": summary.latest.year if summary.latest else None,
                "cumulative_depletion": summary.cumulative,
                "peak_year": summary.peak.year if summary.peak else None,
                "peak_value": summary.peak.value if summary.peak else None,
                "reported_years": summary.reported_years,
                "unit": getattr(indicator, "unit", ""),
            }
        )

    if not records:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    return pd.DataFrame.from_records(records, columns=SUMMARY_COLUMNS)


def methodology_frame(
    indicators: dict,
    source_attribution: str,
    caveats: Sequence[str],
    generated_at: Optional[datetime] = None,
    context: Optional[dict[str, str]] = None,
) -> pd.DataFrame:
    """The ``methodology`` sheet: source, definitions, caveats, timestamp.

    Everything a reader needs to judge the numbers travels inside the workbook,
    so a downloaded file is never separated from its caveats.
    """
    stamp = generated_at or datetime.now(timezone.utc)
    records: list[dict[str, str]] = [
        {"section": "Source", "item": "Attribution", "detail": source_attribution},
        {
            "section": "Source",
            "item": "API",
            "detail": "World Bank Indicators API v2 — https://api.worldbank.org/v2",
        },
    ]

    for code, indicator in indicators.items():
        detail = getattr(indicator, "note", "") or getattr(indicator, "label", code)
        unit = getattr(indicator, "unit", "")
        records.append(
            {
                "section": "Indicator definitions",
                "item": f"{code} — {getattr(indicator, 'label', code)}",
                "detail": f"{detail} Unit: {unit}." if unit else detail,
            }
        )

    for index, caveat in enumerate(caveats, start=1):
        records.append(
            {"section": "Caveats", "item": f"Caveat {index}", "detail": caveat}
        )

    for key, value in (context or {}).items():
        records.append({"section": "Selection", "item": key, "detail": str(value)})

    records.append(
        {
            "section": "Export",
            "item": "Generated at (UTC)",
            "detail": stamp.strftime("%Y-%m-%d %H:%M:%S UTC"),
        }
    )

    return pd.DataFrame.from_records(records, columns=METHODOLOGY_COLUMNS)


# ---------------------------------------------------------------------------
# Workbook
# ---------------------------------------------------------------------------

def _autosize(worksheet, frame: pd.DataFrame) -> None:
    """Widen columns to their content so the file opens readable."""
    try:
        from openpyxl.utils import get_column_letter
    except ImportError:  # pragma: no cover - openpyxl is a hard dependency here
        return

    for index, column in enumerate(frame.columns, start=1):
        sample = [str(value) for value in frame[column].head(200).tolist()]
        widest = max([len(str(column))] + [len(value) for value in sample])
        worksheet.column_dimensions[get_column_letter(index)].width = min(
            max(widest + 2, 12), 60
        )


def build_workbook(
    annual: pd.DataFrame,
    summary: pd.DataFrame,
    methodology: pd.DataFrame,
) -> bytes:
    """Assemble the three-sheet workbook and return it as bytes.

    An empty selection still produces all three sheets with their headers — a
    workbook that opens and explains itself beats a download that fails.
    """
    sheets = {
        "annual_data": annual if annual is not None else pd.DataFrame(columns=ANNUAL_COLUMNS),
        "summary": summary if summary is not None else pd.DataFrame(columns=SUMMARY_COLUMNS),
        "methodology": (
            methodology if methodology is not None else pd.DataFrame(columns=METHODOLOGY_COLUMNS)
        ),
    }

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name in EXPORT_SHEETS:
            frame = sheets[name]
            frame.to_excel(writer, sheet_name=name, index=False)
            _autosize(writer.sheets[name], frame)

    return buffer.getvalue()


def build_environment_workbook(
    frame: pd.DataFrame,
    summaries: Iterable[DepletionSummary],
    indicators: dict,
    source_attribution: str,
    caveats: Sequence[str],
    context: Optional[dict[str, str]] = None,
    generated_at: Optional[datetime] = None,
) -> bytes:
    """One call from the UI: windowed frame and summaries in, workbook out."""
    return build_workbook(
        annual_export_frame(frame, indicators),
        summary_export_frame(summaries, indicators),
        methodology_frame(
            indicators,
            source_attribution,
            caveats,
            generated_at=generated_at,
            context=context,
        ),
    )
