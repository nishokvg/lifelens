"""LifeLens — The World Since You Were Born.

A personalized view of how the world changed during one person's lifetime,
built from the World Bank's World Development Indicators.

Status: the input form and five tabs (My Story, Lifetime in Data, My Two
Worlds, Timeline & Discoveries, Earth & Resources) are live. Quiz & Share is
present in the navigation and arrives in the next phase. See DESIGN.md.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from services.environment import (
    DEFAULT_ENVIRONMENT_CODES,
    ENVIRONMENT_CAVEATS,
    ENVIRONMENT_INDICATORS,
    ENVIRONMENT_SOURCE_ATTRIBUTION,
    fetch_environment_indicators,
    get_environment_indicator,
    is_cumulative_meaningful,
)
from services.world_bank import (
    DEFAULT_INDICATOR_CODES,
    INDICATORS,
    SOURCE_ATTRIBUTION,
    TIDY_COLUMNS,
    WORLD_CODE,
    WorldBankError,
    fetch_countries,
    fetch_indicators,
    get_indicator,
)
from utils.calculations import (
    Change,
    common_year,
    compute_change,
    get_series,
    largest_change,
    largest_gap,
    latest_available,
    life_span,
    milestones_for_year,
    milestones_in_range,
    nearest_available,
    strongest_improvement,
    timeline_observation,
    timeline_years,
)
from utils.environment import (
    build_environment_workbook,
    order_selection,
    share_of_peak,
    summarize_depletion,
    summary_export_frame,
    window_frame,
    year_window,
)
from utils.formatting import (
    UNAVAILABLE,
    flag_emoji,
    format_date,
    format_percent,
    format_points,
    format_value,
    human_number,
    is_improvement,
    safe_name,
)
from utils.narratives import (
    build_story,
    comparison_insight,
    depletion_interpretation,
    describe_change,
    headline_insight,
    interpret,
    timeline_interpretation,
)

APP_ROOT = Path(__file__).parent

# Demonstration defaults.
DEFAULT_NAME = "Nishok"
DEFAULT_BIRTH_DATE = date(1990, 6, 25)
DEFAULT_BIRTH_COUNTRY = "India"
DEFAULT_CURRENT_COUNTRY = "United States"

# World Development Indicators begin in 1960.
MIN_BIRTH_YEAR = 1960
MAX_PLAUSIBLE_AGE = 120
# Longest observed gap between the current year and the newest reported value.
REPORTING_LAG_YEARS = 3

# Validated data-viz palette, stepped for the light surface (#fcfcfb) this app
# commits to. Slots are assigned in fixed order and follow the entity: the birth
# country is always slot 1, wherever it appears. Filtering never repaints them.
COLOR_BIRTH = "#2a78d6"     # slot 1, blue
COLOR_CURRENT = "#eb6834"   # slot 2, orange
COLOR_WORLD = "#1baf7a"     # slot 3, aqua

# Reserved status tokens — never reused as a series colour.
COLOR_GOOD = "#006300"
COLOR_BAD = "#d03b3b"

# Chart chrome.
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"

# Milestone categories. Colour reinforces the emoji and the category name that
# appear on every card, so identity is never carried by hue alone.
CATEGORY_STYLE = {
    "Science": ("🔬", "#2a78d6"),
    "Technology": ("💻", "#eb6834"),
    "World events": ("🌍", "#1baf7a"),
    "Health": ("🩺", "#e87ba4"),
    "Space": ("🚀", "#4a3aa7"),
}
DEFAULT_CATEGORY_STYLE = ("✨", BASELINE)


def category_style(category: str) -> tuple[str, str]:
    return CATEGORY_STYLE.get(category, DEFAULT_CATEGORY_STYLE)


st.set_page_config(page_title="LifeLens", page_icon="🔭", layout="wide")


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def load_css() -> None:
    css_path = APP_ROOT / "assets" / "styles.css"
    if css_path.exists():
        st.markdown(f"<style>{css_path.read_text()}</style>", unsafe_allow_html=True)


def render_hero() -> None:
    """Title plus a chip per indicator — a legend for the whole app."""
    chips = "".join(
        f'<span class="lens-chip">{indicator.emoji} {indicator.short_label}</span>'
        for indicator in INDICATORS.values()
    )
    st.markdown(
        f"""
        <div class="lens-hero">
            <h1>🔭 LifeLens</h1>
            <p>🌍 The World Since You Were Born — a personal view of global development data</p>
            <div class="lens-chips">{chips}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=60 * 60 * 24 * 7, show_spinner=False)
def country_options() -> pd.DataFrame:
    """Selectable countries, aggregates already excluded."""
    return fetch_countries()


def stat_card(
    label: str,
    value: str,
    year_note: str = "",
    delta: str = "",
    delta_state: str | None = None,
    missing: bool = False,
    accent: str = BASELINE,
    icon: str = "",
) -> str:
    """One statistic, always carrying the year it came from.

    ``accent`` is a 3px left rule, not a fill — saturated colour stays a small
    mark. ``delta_state`` adds an arrow glyph alongside the colour so the
    direction of travel never rests on hue alone.
    """
    value_class = "lens-missing" if missing else "lens-value"
    heading = f"{icon} {label}".strip()
    parts = [
        f'<div class="lens-card" style="--accent:{accent}">',
        f'<div class="lens-label">{heading}</div>',
        f'<div class="{value_class}">{value}</div>',
    ]
    if year_note:
        parts.append(f'<div class="lens-year">{year_note}</div>')
    if delta:
        tone = {"up": "lens-up", "down": "lens-down"}.get(delta_state or "", "lens-flat")
        arrow = {"up": "▲", "down": "▼"}.get(delta_state or "", "→")
        parts.append(f'<div class="lens-delta {tone}">{arrow} {delta}</div>')
    parts.append("</div>")
    return "".join(parts)


def year_range_selector(
    column,
    years: list[int],
    key: str,
    birth_year: int | None = None,
) -> tuple[int, int] | None:
    """A year-range slider, or a labelled year when only one was reported.

    Streamlit sliders require ``min_value < max_value``, so an indicator that
    collapses to a single reported year would raise rather than render. Both
    tabs that offer a year range go through here, so neither can regress into
    that crash independently.
    """
    window = year_window(years, birth_year)
    if window is None:
        return None

    if window.is_single_year:
        column.markdown(
            stat_card(
                "Year range",
                str(window.first),
                "the only year reported for this indicator",
                accent=BASELINE,
                icon="📅",
            ),
            unsafe_allow_html=True,
        )
        return (window.first, window.last)

    return column.slider(
        "Year range",
        min_value=window.first,
        max_value=window.last,
        value=(window.default_start, window.last),
        key=key,
    )


def delta_state_for(change: Change | None, indicator) -> str:
    """Colour a delta by whether it is good, not merely by its sign."""
    if change is None:
        return "flat"
    improved = is_improvement(change.direction, indicator.better)
    if improved is None:
        return "flat"
    return "up" if improved else "down"


# ---------------------------------------------------------------------------
# Sidebar form
# ---------------------------------------------------------------------------

def sidebar_form(countries: pd.DataFrame) -> dict | None:
    """Collect and validate inputs. Returns a profile only on a valid submit."""
    names = countries["name"].tolist()

    def index_of(name: str) -> int:
        return names.index(name) if name in names else 0

    with st.sidebar:
        st.markdown("### Your details")

        with st.form("lifelens_inputs"):
            name = st.text_input(
                "Name (optional)",
                value=DEFAULT_NAME,
                max_chars=60,
                help="Leave this blank and the story will simply say “you”.",
            )
            birth_date = st.date_input(
                "Date of birth",
                value=DEFAULT_BIRTH_DATE,
                min_value=date(MIN_BIRTH_YEAR, 1, 1),
                max_value=date.today(),
                format="YYYY-MM-DD",
            )
            birth_country = st.selectbox(
                "Birth country", names, index=index_of(DEFAULT_BIRTH_COUNTRY)
            )
            current_country = st.selectbox(
                "Current country", names, index=index_of(DEFAULT_CURRENT_COUNTRY)
            )
            submitted = st.form_submit_button(
                "🔭 Explore My Lifetime", width="stretch", type="primary"
            )

        st.caption(SOURCE_ATTRIBUTION)

    if not submitted:
        return None

    errors = validate(birth_date)
    if errors:
        for message in errors:
            st.sidebar.error(message, icon="⚠️")
        return None

    birth_row = countries[countries["name"] == birth_country].iloc[0]
    current_row = countries[countries["name"] == current_country].iloc[0]

    if birth_row["code"] == current_row["code"]:
        st.sidebar.info(
            "Birth and current country are the same — “My Two Worlds” will "
            "compare your country with the world average.",
            icon="🌍",
        )

    return {
        "name": safe_name(name),
        "birth_date": birth_date,
        "birth_code": birth_row["code"],
        "birth_name": birth_row["name"],
        "birth_iso2": birth_row["iso2"],
        "current_code": current_row["code"],
        "current_name": current_row["name"],
        "current_iso2": current_row["iso2"],
    }


def validate(birth_date: date) -> list[str]:
    """Friendly, specific validation messages."""
    today = date.today()
    problems: list[str] = []

    if birth_date > today:
        problems.append(
            f"That date is in the future. Please choose a date on or before "
            f"{format_date(today)}."
        )
    if birth_date.year < MIN_BIRTH_YEAR:
        problems.append(
            f"World Bank development data begins in {MIN_BIRTH_YEAR}. Please "
            f"choose a birth year of {MIN_BIRTH_YEAR} or later."
        )
    if today.year - birth_date.year > MAX_PLAUSIBLE_AGE:
        problems.append("That birth date implies an age above 120 years.")

    return problems


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_story_data(profile: dict) -> dict:
    """Fetch every indicator once; each tab reads from this result."""
    codes = list(DEFAULT_INDICATOR_CODES)
    countries = [profile["birth_code"], profile["current_code"], WORLD_CODE]
    end_year = date.today().year
    # World Bank series lag reality by up to ~3 years. Someone born very
    # recently would otherwise request a window containing no reported data at
    # all, so always look back far enough to find the latest reported value.
    start_year = min(profile["birth_date"].year, end_year - REPORTING_LAG_YEARS)

    status = st.status("Retrieving World Bank data…", expanded=False)

    def progress(done: int, total: int, label: str) -> None:
        status.update(label=f"Retrieving {label} ({done} of {total})…")

    try:
        frame, errors = fetch_indicators(
            codes, countries, start_year, end_year, progress=progress
        )
    except WorldBankError as exc:
        status.update(label="Could not reach the World Bank API", state="error")
        raise exc

    # Depletion series are fetched in the same pass. Streamlit renders every
    # tab body on every run, so a "lazy" fetch inside the tab would run anyway;
    # doing it here keeps the fetch-once-render-many contract intact. A failure
    # here degrades one tab and never blocks the story.
    try:
        environment_frame, environment_errors = fetch_environment_indicators(
            countries, start_year, end_year, progress=progress
        )
    except WorldBankError as exc:
        environment_frame = pd.DataFrame(columns=TIDY_COLUMNS)
        environment_errors = {code: str(exc) for code in DEFAULT_ENVIRONMENT_CODES}

    if frame.empty:
        status.update(label="No data returned", state="error")
    else:
        loaded = len(codes) - len(errors)
        status.update(
            label=f"Loaded {loaded} of {len(codes)} indicators", state="complete"
        )

    return {
        "frame": frame,
        "errors": errors,
        "environment_frame": environment_frame,
        "environment_errors": environment_errors,
        "profile": profile,
        "start_year": start_year,
        "end_year": end_year,
    }


def compute_all_changes(frame: pd.DataFrame, country_code: str, birth_year: int) -> dict[str, Change]:
    """Birth-year-to-latest change for every indicator that has one."""
    changes: dict[str, Change] = {}
    for code, indicator in INDICATORS.items():
        change = compute_change(
            frame, code, country_code, birth_year, is_percentage=indicator.is_percentage
        )
        if change is not None:
            changes[code] = change
    return changes


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def series_traces(frame: pd.DataFrame, code: str, entries: list[tuple[str, str, str]]) -> list[go.Scatter]:
    """One line per country. ``entries`` is (country_code, label, colour)."""
    traces = []
    for country_code, label, color in entries:
        series = get_series(frame, code, country_code)
        if series.empty:
            continue
        traces.append(
            go.Scatter(
                x=list(series.index),
                y=list(series.values),
                name=label,
                mode="lines",
                line=dict(color=color, width=2),
                connectgaps=False,  # unreported years stay visibly absent
                hovertemplate=f"<b>{label}</b><br>%{{x}}: %{{y:,.2f}}<extra></extra>",
            )
        )
    return traces


def style_figure(fig: go.Figure, indicator, height: int = 430) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=54, b=10),
        hovermode="x unified",
        # A legend is always present for two or more series, so identity never
        # rests on colour alone.
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif"),
        yaxis_title=indicator.unit,
        xaxis_title="Year",
    )
    fig.update_xaxes(showgrid=False, linecolor=BASELINE, tickfont=dict(color=INK_MUTED))
    fig.update_yaxes(
        gridcolor=GRIDLINE,
        zeroline=False,
        linecolor=BASELINE,
        tickfont=dict(color=INK_MUTED),
    )
    return fig


def add_year_markers(fig: go.Figure, birth_year: int, latest_year: int | None) -> None:
    fig.add_vline(
        x=birth_year,
        line_dash="dash",
        line_color=INK_MUTED,
        annotation_text=f"🎂 Born {birth_year}",
        annotation_position="top left",
    )
    if latest_year and latest_year != birth_year:
        fig.add_vline(
            x=latest_year,
            line_dash="dot",
            line_color=COLOR_GOOD,
            annotation_text=f"📅 Latest data {latest_year}",
            annotation_position="top right",
        )


# ---------------------------------------------------------------------------
# Tab 1 — My Story
# ---------------------------------------------------------------------------

def render_my_story(story: dict) -> None:
    profile = story["profile"]
    frame = story["frame"]
    name = profile["name"]
    birth_year = profile["birth_date"].year
    span = life_span(profile["birth_date"])

    greeting = f"Hello, {name}." if name else "Hello."
    st.subheader(greeting)
    st.markdown(
        f"You were born on **{format_date(profile['birth_date'])}** in "
        f"**{profile['birth_name']}**."
    )

    # --- A life, in round numbers -----------------------------------------
    st.markdown("#### ⏳ Your lifetime so far")
    cols = st.columns(4)
    cols[0].markdown(
        stat_card("Exact age", f"{span.years} yrs", f"and {span.months} months",
                  accent=COLOR_BIRTH, icon="🎂"),
        unsafe_allow_html=True,
    )
    cols[1].markdown(
        stat_card("Days alive", f"{span.days_total:,}", "approximate",
                  accent=COLOR_CURRENT, icon="📆"),
        unsafe_allow_html=True,
    )
    cols[2].markdown(
        stat_card("Full moons", f"{span.full_moons:,}", "estimated — one per 29.5 days",
                  accent="#eda100", icon="🌕"),
        unsafe_allow_html=True,
    )
    cols[3].markdown(
        stat_card(
            "Heartbeats",
            human_number(span.heartbeats, 1),
            "estimated at ~72 bpm",
            accent="#d03b3b",
            icon="💓",
        ),
        unsafe_allow_html=True,
    )

    st.markdown(
        f'<p class="lens-note">🌬️ Roughly <b>{human_number(span.breaths, 1)} breaths</b> '
        f"at about 16 per minute · ☀️ <b>{span.sunrises:,} sunrises</b>, one for each "
        f"day lived · 🪐 <b>{human_number(span.km_around_sun, 1)} km</b> carried "
        f"through space by Earth's orbit across {span.orbits:.1f} laps of the Sun. "
        f"These are estimates from average rates, not measurements, and they are the "
        f"only figures on this page not sourced from the World Bank.</p>",
        unsafe_allow_html=True,
    )

    st.divider()

    # --- Then versus latest available year --------------------------------
    st.markdown("#### 🔀 Then versus now")
    st.caption(
        "Every figure is labelled with the year the World Bank actually reported it. "
        "The most recent reported year is used throughout — never today's date."
    )

    pairs = [
        ("SP.POP.TOTL", WORLD_CODE, "World population", COLOR_WORLD),
        ("SP.POP.TOTL", profile["birth_code"], f"{profile['birth_name']} population", COLOR_BIRTH),
        ("SP.DYN.LE00.IN", profile["birth_code"],
         f"Life expectancy — {profile['birth_name']}", COLOR_BIRTH),
        ("IT.NET.USER.ZS", profile["birth_code"],
         f"Internet users — {profile['birth_name']}", COLOR_BIRTH),
    ]

    columns = st.columns(len(pairs))
    for column, (code, country_code, label, accent) in zip(columns, pairs):
        indicator = get_indicator(code)
        series = get_series(frame, code, country_code)
        birth_obs = nearest_available(series, birth_year)
        latest_obs = latest_available(series)

        if birth_obs is None or latest_obs is None:
            column.markdown(
                stat_card(label, UNAVAILABLE, "not reported", missing=True,
                          accent=accent, icon=indicator.emoji),
                unsafe_allow_html=True,
            )
            continue

        change = compute_change(
            frame, code, country_code, birth_year, is_percentage=indicator.is_percentage
        )
        if change is None:
            delta_text = ""
            state = "flat"
        elif indicator.is_percentage:
            delta_text = f"{format_points(change.percentage_points)} since {change.start.year}"
            state = delta_state_for(change, indicator)
        elif change.percent is not None:
            delta_text = f"{format_percent(change.percent)} since {change.start.year}"
            state = delta_state_for(change, indicator)
        else:
            delta_text = f"{format_value(change.absolute, indicator)} since {change.start.year}"
            state = delta_state_for(change, indicator)

        column.markdown(
            stat_card(
                label,
                format_value(latest_obs.value, indicator),
                f"in {latest_obs.year} · was "
                f"{format_value(birth_obs.value, indicator)} in {birth_obs.year}",
                delta_text,
                state,
                accent=accent,
                icon=indicator.emoji,
            ),
            unsafe_allow_html=True,
        )

    st.divider()

    # --- What changed the most --------------------------------------------
    changes = compute_all_changes(frame, profile["birth_code"], birth_year)
    st.markdown("#### 🚀 What changed the most")

    if not changes:
        latest_reported = frame["year"].max() if not frame.empty else None
        if latest_reported is not None and birth_year >= int(latest_reported):
            st.info(
                f"Your lifetime is shorter than the World Bank's reporting cycle — "
                f"the most recent reported year is {int(latest_reported)}. The "
                f"figures above show the world as most recently measured, but there "
                f"is not yet a span of years to compare against.",
                icon="ℹ️",
            )
        else:
            st.info("Not enough data was returned to compare changes.", icon="ℹ️")
    else:
        top = largest_change(changes)
        improved = strongest_improvement(
            changes, {code: ind.better for code, ind in INDICATORS.items()}
        )
        left, right = st.columns(2)
        with left:
            if top is None:
                st.markdown("Relative comparison unavailable.")
            else:
                code, change = top
                st.markdown(
                    headline_insight(code, change, get_indicator(code), profile["birth_name"])
                )
        with right:
            if improved is None:
                st.markdown(
                    "No indicator with a clearly better direction improved over this period."
                )
            else:
                code, change = improved
                indicator = get_indicator(code)
                magnitude = (
                    format_percent(change.percent)
                    if change.percent is not None
                    else format_value(change.absolute, indicator, compact=False)
                )
                st.markdown(
                    f"**Strongest improvement:** {indicator.label} moved "
                    f"{magnitude} in the favourable direction between "
                    f"{change.start.year} and {change.end.year}."
                )

    st.divider()

    # --- Narrative ---------------------------------------------------------
    st.markdown("#### ✍️ Your story in words")
    paragraphs = build_story(
        name=name,
        birth_country=profile["birth_name"],
        current_country=profile["current_name"],
        span=span,
        changes=changes,
        indicators=INDICATORS,
    )
    for paragraph in paragraphs:
        st.markdown(paragraph)

    render_errors(story)


# ---------------------------------------------------------------------------
# Tab 2 — Lifetime in Data
# ---------------------------------------------------------------------------

def render_lifetime_in_data(story: dict) -> None:
    profile = story["profile"]
    frame = story["frame"]
    birth_year = profile["birth_date"].year

    available = [code for code in DEFAULT_INDICATOR_CODES if code not in story["errors"]]
    if not available:
        st.error("No indicators loaded. Try again from the sidebar.", icon="🚫")
        return

    entries = country_entries(profile)
    labels = {
        code: f"{INDICATORS[code].emoji} {INDICATORS[code].label}" for code in available
    }

    controls = st.columns([2, 2, 3])
    code = controls[0].selectbox(
        "Indicator", available, format_func=lambda c: labels[c], key="lid_indicator"
    )
    chosen = controls[1].multiselect(
        "Countries",
        [entry[0] for entry in entries],
        default=[entry[0] for entry in entries],
        format_func=lambda c: {e[0]: e[1] for e in entries}[c],
        key="lid_countries",
    )

    indicator = get_indicator(code)
    all_years = sorted({int(y) for y in frame[frame["indicator"] == code]["year"]})
    if not all_years:
        st.warning(f"No data was returned for {indicator.label}.", icon="⚠️")
        return

    # Opens on the full range, as before; a single reported year renders a
    # label instead of a slider rather than raising.
    year_range = year_range_selector(controls[2], all_years, key="lid_years")

    selected = [entry for entry in entries if entry[0] in chosen]
    if not selected:
        st.info("Select at least one country to draw the chart.", icon="ℹ️")
        return

    windowed = frame[
        (frame["indicator"] == code)
        & (frame["year"] >= year_range[0])
        & (frame["year"] <= year_range[1])
    ]

    fig = go.Figure(series_traces(windowed, code, selected))
    if not fig.data:
        st.warning("No observations fall inside the selected year range.", icon="⚠️")
        return

    primary = get_series(windowed, code, profile["birth_code"])
    latest_obs = latest_available(primary) or latest_available(
        get_series(windowed, code, selected[0][0])
    )
    fig.update_layout(title=indicator.label)
    if year_range[0] <= birth_year <= year_range[1]:
        add_year_markers(fig, birth_year, latest_obs.year if latest_obs else None)
    style_figure(fig, indicator)
    st.plotly_chart(fig, width="stretch")

    # --- Numbers for the primary country ----------------------------------
    focus_code, focus_label, _ = selected[0]
    change = compute_change(
        frame, code, focus_code, birth_year, is_percentage=indicator.is_percentage
    )

    st.markdown(
        f"##### {indicator.emoji} {focus_label} — birth year to latest available year"
    )
    metric_count = 5 if indicator.is_percentage else 4
    metrics = st.columns(metric_count)

    if change is None:
        for column, label in zip(metrics, ["Birth value", "Latest value", "Change", "% change", "pp change"]):
            column.markdown(
                stat_card(label, UNAVAILABLE, "not reported", missing=True),
                unsafe_allow_html=True,
            )
    else:
        metrics[0].markdown(
            stat_card(
                "Birth value",
                format_value(change.start.value, indicator),
                f"{change.start.year} data",
                accent=COLOR_BIRTH,
                icon="🎂",
            ),
            unsafe_allow_html=True,
        )
        metrics[1].markdown(
            stat_card(
                "Latest value",
                format_value(change.end.value, indicator),
                f"{change.end.year} data",
                accent=COLOR_GOOD,
                icon="📅",
            ),
            unsafe_allow_html=True,
        )
        metrics[2].markdown(
            stat_card(
                "Absolute change",
                format_value(change.absolute, indicator),
                f"over {change.years_elapsed} years",
                delta_state=delta_state_for(change, indicator),
                accent=COLOR_CURRENT,
                icon="📊",
            ),
            unsafe_allow_html=True,
        )
        metrics[3].markdown(
            stat_card(
                "Percentage change",
                format_percent(change.percent) if change.percent is not None else "n/a",
                "baseline is zero" if change.percent is None else "relative to birth year",
                missing=change.percent is None,
            ),
            unsafe_allow_html=True,
        )
        if indicator.is_percentage:
            metrics[4].markdown(
                stat_card(
                    "Percentage-point change",
                    format_points(change.percentage_points),
                    "this indicator is measured in %",
                ),
                unsafe_allow_html=True,
            )

    st.markdown(f"**What this means:** {interpret(change, indicator, focus_label)}")

    if indicator.note:
        st.caption(f"Note: {indicator.note}")

    # --- Raw data ----------------------------------------------------------
    table = windowed.copy()
    table = table.rename(
        columns={
            "indicator": "Indicator",
            "country_code": "Country code",
            "country_name": "Country",
            "year": "Year",
            "value": "Value",
        }
    )
    table = table[table["Country code"].isin(chosen)].sort_values(["Country", "Year"])

    with st.expander(f"View raw data ({len(table)} observations)"):
        st.dataframe(table, width="stretch", hide_index=True)

    st.download_button(
        "⬇️ Download this data as CSV",
        data=table.to_csv(index=False).encode("utf-8"),
        file_name=f"lifelens_{code}_{year_range[0]}_{year_range[1]}.csv",
        mime="text/csv",
    )

    st.caption(f"{SOURCE_ATTRIBUTION} · Indicator code: {code}")


# ---------------------------------------------------------------------------
# Tab 3 — My Two Worlds
# ---------------------------------------------------------------------------

def country_entries(profile: dict) -> list[tuple[str, str, str]]:
    """(code, label, colour) for the birth country, current country and World.

    Deduplicated, so a user who never moved does not get the same country twice.
    """
    entries = [(profile["birth_code"], profile["birth_name"], COLOR_BIRTH)]
    if profile["current_code"] != profile["birth_code"]:
        entries.append((profile["current_code"], profile["current_name"], COLOR_CURRENT))
    entries.append((WORLD_CODE, "World", COLOR_WORLD))
    return entries


def render_two_worlds(story: dict) -> None:
    profile = story["profile"]
    frame = story["frame"]
    birth_year = profile["birth_date"].year
    same_country = profile["birth_code"] == profile["current_code"]

    if same_country:
        st.info(
            f"You were born in {profile['birth_name']} and still live there, so this "
            f"tab compares {profile['birth_name']} with the world average.",
            icon="🌍",
        )

    entries = country_entries(profile)

    # --- Headline flags ----------------------------------------------------
    header = st.columns(len(entries))
    for column, (code, label, color) in zip(header, entries):
        iso2 = {
            profile["birth_code"]: profile["birth_iso2"],
            profile["current_code"]: profile["current_iso2"],
        }.get(code, "")
        role = (
            "Birth country" if code == profile["birth_code"]
            else "Current country" if code == profile["current_code"]
            else "World aggregate"
        )
        column.markdown(
            f'<div class="lens-card" style="--accent:{color}">'
            f'<div class="lens-flag">{flag_emoji(iso2)}</div>'
            f'<div class="lens-value" style="font-size:1.25rem">{label}</div>'
            f'<div class="lens-year">{role}</div></div>',
            unsafe_allow_html=True,
        )

    st.divider()

    available = [code for code in DEFAULT_INDICATOR_CODES if code not in story["errors"]]
    if not available:
        st.error("No indicators loaded. Try again from the sidebar.", icon="🚫")
        return

    code = st.selectbox(
        "Indicator",
        available,
        format_func=lambda c: f"{INDICATORS[c].emoji} {INDICATORS[c].label}",
        key="tw_indicator",
    )
    indicator = get_indicator(code)

    # --- Side-by-side latest values ---------------------------------------
    st.markdown(f"##### {indicator.emoji} Latest available values")
    cards = st.columns(len(entries))
    for column, (country_code, label, color) in zip(cards, entries):
        series = get_series(frame, code, country_code)
        latest_obs = latest_available(series)
        change = compute_change(
            frame, code, country_code, birth_year, is_percentage=indicator.is_percentage
        )

        if latest_obs is None:
            column.markdown(
                stat_card(label, UNAVAILABLE, "not reported", missing=True, accent=color),
                unsafe_allow_html=True,
            )
            continue

        if change is None:
            delta_text = ""
        elif indicator.is_percentage:
            delta_text = f"{format_points(change.percentage_points)} since {change.start.year}"
        elif change.percent is not None:
            delta_text = f"{format_percent(change.percent)} since {change.start.year}"
        else:
            delta_text = f"{format_value(change.absolute, indicator)} since {change.start.year}"

        column.markdown(
            stat_card(
                label,
                format_value(latest_obs.value, indicator),
                f"{latest_obs.year} data",
                delta_text,
                delta_state_for(change, indicator),
                accent=color,
            ),
            unsafe_allow_html=True,
        )

    if indicator.code == "SP.DYN.LE00.IN":
        st.caption(
            "Life expectancy at birth describes babies born in the stated year under "
            "that year's mortality rates. It is a country-level statistic and says "
            "nothing about how long any particular person will live."
        )

    # --- Grouped bar: birth year versus latest shared year ------------------
    st.markdown("##### ⚖️ Then and now, side by side")
    codes_present = [entry[0] for entry in entries]
    shared_year = common_year(frame, code, codes_present)

    if shared_year is None:
        st.warning(
            "These countries have no year in common for this indicator, so a direct "
            "comparison would be misleading.",
            icon="⚠️",
        )
    else:
        bar = go.Figure()
        for country_code, label, color in entries:
            series = get_series(frame, code, country_code)
            birth_obs = nearest_available(series, birth_year)
            latest_obs = latest_available(series)
            if birth_obs is None or latest_obs is None:
                continue
            bar.add_trace(
                go.Bar(
                    name=label,
                    x=[f"Birth year ({birth_obs.year})", f"Latest ({latest_obs.year})"],
                    y=[birth_obs.value, latest_obs.value],
                    marker_color=color,
                    hovertemplate=f"<b>{label}</b><br>%{{x}}: %{{y:,.2f}}<extra></extra>",
                )
            )
        bar.update_layout(barmode="group", title=indicator.label)
        style_figure(bar, indicator, height=380)
        st.plotly_chart(bar, width="stretch")
        st.caption(
            "Bars are labelled with each country's own nearest reported year, which "
            "may differ slightly between countries."
        )

    # --- Historical comparison --------------------------------------------
    st.markdown("##### 📉 The whole picture, year by year")
    line = go.Figure(series_traces(frame[frame["indicator"] == code], code, entries))
    if line.data:
        line.update_layout(title=f"{indicator.label} since {birth_year}")
        add_year_markers(line, birth_year, shared_year)
        style_figure(line, indicator)
        st.plotly_chart(line, width="stretch")
    else:
        st.warning("No series available to chart for this indicator.", icon="⚠️")

    st.divider()

    # --- Insights ----------------------------------------------------------
    st.markdown("##### 💡 What the comparison shows")
    comparison_code = profile["current_code"] if not same_country else WORLD_CODE
    comparison_label = profile["current_name"] if not same_country else "the world average"

    left, right = st.columns(2)

    with left:
        st.markdown("**📏 Largest difference**")
        gap = largest_gap(frame, available, profile["birth_code"], comparison_code)
        if gap is None:
            st.markdown("No indicator has a year reported by both, so no gap can be calculated.")
        else:
            gap_code, _, gap_year = gap
            gap_indicator = get_indicator(gap_code)
            series_a = get_series(frame, gap_code, profile["birth_code"])
            series_b = get_series(frame, gap_code, comparison_code)
            st.markdown(
                comparison_insight(
                    gap_indicator,
                    gap_year,
                    profile["birth_name"],
                    float(series_a.loc[gap_year]),
                    comparison_label,
                    float(series_b.loc[gap_year]),
                )
            )

    with right:
        st.markdown("**🌟 Strongest improvement**")
        changes = compute_all_changes(frame, profile["birth_code"], birth_year)
        improved = strongest_improvement(
            changes, {c: ind.better for c, ind in INDICATORS.items()}
        )
        if improved is None:
            st.markdown(
                f"No indicator with a clearly better direction improved in "
                f"{profile['birth_name']} over this period."
            )
        else:
            improved_code, change = improved
            st.markdown(
                describe_change(change, get_indicator(improved_code), profile["birth_name"])
            )

    st.caption(SOURCE_ATTRIBUTION)


# ---------------------------------------------------------------------------
# Tab 4 — Timeline & Discoveries
# ---------------------------------------------------------------------------

MILESTONE_COLUMNS = ["year", "category", "title", "description", "source_url"]

@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def load_milestones() -> pd.DataFrame:
    """Read the curated milestone file.

    Returns an empty frame if the file is missing or malformed, so a data
    problem degrades this one tab rather than the application. Nothing here
    invents an event — every row comes from the CSV.
    """
    path = APP_ROOT / "data" / "milestones.csv"
    if not path.exists():
        return pd.DataFrame(columns=MILESTONE_COLUMNS)

    try:
        frame = pd.read_csv(path)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError):
        return pd.DataFrame(columns=MILESTONE_COLUMNS)

    missing = [column for column in MILESTONE_COLUMNS if column not in frame.columns]
    if missing:
        return pd.DataFrame(columns=MILESTONE_COLUMNS)

    frame = frame.dropna(subset=["year", "title"])
    frame["year"] = pd.to_numeric(frame["year"], errors="coerce")
    frame = frame.dropna(subset=["year"])
    frame["year"] = frame["year"].astype(int)
    frame["category"] = frame["category"].fillna("Other").astype(str).str.strip()
    frame["description"] = frame["description"].fillna("").astype(str)
    frame["source_url"] = frame["source_url"].fillna("").astype(str)

    return frame.sort_values(["year", "title"], ignore_index=True)


def timeline_strip(points: list, selected_year: int) -> go.Figure:
    """A static horizontal timeline. No animation — it is a reference strip."""
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[point.year for point in points],
            y=[0] * len(points),
            mode="lines",
            line=dict(color=BASELINE, width=3),
            hoverinfo="skip",
            showlegend=False,
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[point.year for point in points],
            y=[0] * len(points),
            mode="markers+text",
            marker=dict(
                size=[20 if point.year == selected_year else 13 for point in points],
                color=[
                    COLOR_BIRTH if point.year == selected_year
                    else COLOR_WORLD if point.is_latest
                    else INK_MUTED
                    for point in points
                ],
                line=dict(color="#ffffff", width=2),
            ),
            text=[point.label for point in points],
            textposition="top center",
            textfont=dict(size=11),
            customdata=[point.age for point in points],
            hovertemplate="<b>%{text}</b><br>Year %{x}<br>Age %{customdata}<extra></extra>",
            showlegend=False,
        )
    )
    fig.update_layout(
        height=190,
        margin=dict(l=10, r=10, t=40, b=10),
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=False, tickformat="d", title=""),
        yaxis=dict(visible=False, range=[-0.6, 0.9]),
    )
    return fig


def render_timeline(story: dict) -> None:
    profile = story["profile"]
    frame = story["frame"]
    birth_year = profile["birth_date"].year

    available = [code for code in DEFAULT_INDICATOR_CODES if code not in story["errors"]]
    latest_year = int(frame["year"].max()) if not frame.empty else None

    points = timeline_years(profile["birth_date"], latest_year)
    if not points:
        st.info("No timeline points could be built from this birth date.", icon="ℹ️")
        return

    st.markdown("#### 🗓️ Your personal timeline")
    st.caption(
        "Milestone ages you have reached, ending on the most recent year the "
        "World Bank has reported."
    )

    labels = {f"{point.label} · {point.year}": point for point in points}
    chosen_label = st.radio(
        "Select a point in your life",
        list(labels),
        horizontal=True,
        key="tl_point",
    )
    selected = labels[chosen_label]

    st.plotly_chart(timeline_strip(points, selected.year), width="stretch")

    st.divider()

    # --- Selected-year statistic card -------------------------------------
    st.markdown(f"#### {selected.year} — the year you turned {selected.age}"
                if selected.age > 0 else f"#### {selected.year} — the year you were born")

    if not available:
        st.warning("No indicators loaded, so no statistic can be shown.", icon="⚠️")
    else:
        stat_columns = st.columns([2, 3])

        with stat_columns[0]:
            code = st.selectbox(
                "Statistic",
                available,
                format_func=lambda c: f"{INDICATORS[c].emoji} {INDICATORS[c].label}",
                key="tl_indicator",
            )
            country_code = st.radio(
                "Country",
                [entry[0] for entry in country_entries(profile)],
                format_func=lambda c: {e[0]: e[1] for e in country_entries(profile)}[c],
                key="tl_country",
            )

        indicator = get_indicator(code)
        observation = timeline_observation(frame, code, country_code, selected.year)
        birth_observation = timeline_observation(frame, code, country_code, birth_year)
        country_label = {e[0]: e[1] for e in country_entries(profile)}[country_code]

        with stat_columns[1]:
            cards = st.columns(3)
            cards[0].markdown(
                stat_card("Your age", f"{selected.age}", f"in {selected.year}",
                          accent=COLOR_BIRTH, icon="🎂"),
                unsafe_allow_html=True,
            )
            cards[1].markdown(
                stat_card("Calendar year", f"{selected.year}", selected.label,
                          accent=COLOR_CURRENT, icon="📆"),
                unsafe_allow_html=True,
            )
            if observation is None:
                cards[2].markdown(
                    stat_card(
                        indicator.short_label,
                        UNAVAILABLE,
                        "no observation within 3 years",
                        missing=True,
                        icon=indicator.emoji,
                    ),
                    unsafe_allow_html=True,
                )
            else:
                exact = observation.year == selected.year
                cards[2].markdown(
                    stat_card(
                        indicator.short_label,
                        format_value(observation.value, indicator),
                        f"{observation.year} data"
                        + ("" if exact else f" · nearest to {selected.year}"),
                        accent=COLOR_WORLD,
                        icon=indicator.emoji,
                    ),
                    unsafe_allow_html=True,
                )

        st.markdown(
            timeline_interpretation(
                indicator,
                country_label,
                selected.year,
                selected.age,
                observation,
                birth_observation,
            )
        )

        if observation is not None and observation.year != selected.year:
            st.caption(
                f"No {selected.year} observation exists for this indicator, so the "
                f"nearest reported year within three years is shown. Values are "
                f"never interpolated."
            )

    st.divider()

    # --- Discoveries -------------------------------------------------------
    st.markdown("#### 🔭 Discoveries during your lifetime")

    milestones = load_milestones()
    if milestones.empty:
        st.warning(
            "The milestone file could not be read, so no discovery cards are "
            "available. Everything else on this tab is unaffected.",
            icon="⚠️",
        )
        return

    end_year = latest_year or date.today().year
    in_range = milestones_in_range(milestones, birth_year, end_year)

    if in_range.empty:
        st.info(
            f"The curated milestone list covers "
            f"{int(milestones['year'].min())}–{int(milestones['year'].max())}, "
            f"which does not overlap your lifetime.",
            icon="ℹ️",
        )
        return

    categories = sorted(in_range["category"].unique())
    chosen_categories = st.multiselect(
        "Categories",
        categories,
        default=categories,
        format_func=lambda c: f"{category_style(c)[0]} {c}",
        key="tl_categories",
    )

    filtered = milestones_in_range(
        milestones, birth_year, end_year, categories=chosen_categories
    )

    if filtered.empty:
        st.info("No milestones match the selected categories.", icon="ℹ️")
        return

    # Events in the selected timeline year, when there are any. A year with no
    # milestone simply renders nothing here.
    selected_year_events = milestones_for_year(filtered, selected.year)
    if not selected_year_events.empty:
        stage = (
            "the year you were born"
            if selected.age == 0
            else f"the year you turned {selected.age}"
        )
        st.markdown(f"**In {selected.year}, {stage}:**")
        for _, row in selected_year_events.iterrows():
            st.markdown(
                f"{category_style(row['category'])[0]} **{row['title']}** — "
                f"{row['description']}"
            )
        st.divider()

    st.caption(
        f"{len(filtered)} milestones between {birth_year} and {end_year}, "
        f"with your age at the time."
    )

    for _, row in filtered.iterrows():
        icon, color = category_style(row["category"])
        source = (
            f'<a href="{row["source_url"]}" target="_blank">Source ↗</a>'
            if row["source_url"]
            else "<span class='lens-note'>No source link</span>"
        )
        st.markdown(
            f'<div class="lens-milestone" style="--accent:{color}">'
            f'<div class="lens-label">{icon} {row["category"]} · {row["year"]} · '
            f'you were {row["age"]}</div>'
            f'<div class="lens-title">{row["title"]}</div>'
            f'<div class="lens-note" style="margin-top:0.3rem">{row["description"]} '
            f"{source}</div></div>",
            unsafe_allow_html=True,
        )

    st.caption(
        "Milestones are hand-curated in `data/milestones.csv`, each with its own "
        "source link. They are not retrieved from an API."
    )


# ---------------------------------------------------------------------------
# Tab 5 — Earth & Resources
# ---------------------------------------------------------------------------

def environment_entries(profile: dict) -> list[tuple[str, str, str]]:
    """(code, label, colour) with the World first.

    Same colours as ``country_entries`` — an entity keeps its hue everywhere in
    the app — but the world aggregate leads, because resource depletion is a
    planetary story before it is a national one.
    """
    entries = [(WORLD_CODE, "World", COLOR_WORLD)]
    entries.append((profile["birth_code"], profile["birth_name"], COLOR_BIRTH))
    if profile["current_code"] != profile["birth_code"]:
        entries.append((profile["current_code"], profile["current_name"], COLOR_CURRENT))
    return entries


def render_environment_errors(errors: dict[str, str]) -> None:
    if not errors:
        return
    with st.expander(f"⚠️ {len(errors)} depletion series could not be loaded"):
        for code, reason in errors.items():
            st.markdown(f"- **{get_environment_indicator(code).label}** — {reason}")


def render_environment_methodology(code: str) -> None:
    """The caveat block. Identical wording to the exported methodology sheet."""
    indicator = get_environment_indicator(code)
    with st.expander("📋 Methodology and caveats"):
        st.markdown(f"**{indicator.label}** (`{indicator.code}`) — {indicator.note}")
        for caveat in ENVIRONMENT_CAVEATS:
            st.markdown(f"- {caveat}")
        st.caption(ENVIRONMENT_SOURCE_ATTRIBUTION)


def render_earth_resources(story: dict) -> None:
    profile = story["profile"]
    frame = story.get("environment_frame")
    errors = story.get("environment_errors", {})
    birth_year = profile["birth_date"].year

    st.markdown("#### 🌱 Resource depletion during your lifetime")
    st.markdown(
        "Three of these indicators are the World Bank's **adjusted savings "
        "depletion** series — the value of energy, mineral and forest "
        "resources recorded as drawn down in each reported year — and the "
        "fourth is **total natural resources rents**, extraction's share of "
        "the economy. Together they describe what was *depleted*, year by "
        "year, while you have been alive. They are **not** a measure of what "
        "remains underground, and nothing on this tab estimates remaining "
        "reserves."
    )

    available = [code for code in DEFAULT_ENVIRONMENT_CODES if code not in errors]
    if frame is None or frame.empty or not available:
        st.info(
            "The World Bank returned no depletion observations for these "
            "countries and years. Nothing is estimated in their place — this "
            "tab simply has nothing to show.",
            icon="🌍",
        )
        render_environment_errors(errors)
        return

    entries = environment_entries(profile)
    entry_labels = {code: label for code, label, _ in entries}

    # --- Controls ----------------------------------------------------------
    controls = st.columns([2, 2, 3])
    code = controls[0].selectbox(
        "Indicator",
        available,
        format_func=lambda c: (
            f"{ENVIRONMENT_INDICATORS[c].emoji} {ENVIRONMENT_INDICATORS[c].label}"
        ),
        key="env_indicator",
    )
    indicator = get_environment_indicator(code)

    for_indicator = frame[frame["indicator"] == code]
    reporting = {str(country) for country in for_indicator["country_code"]}
    # World first when the World Bank publishes a world aggregate for this
    # series — it does for resource rents, but not for the depletion flows,
    # where the first reporting country leads instead of an empty chart.
    ranked = [entry[0] for entry in entries if entry[0] in reporting]
    default_geographies = ranked[:1] or [entries[0][0]]

    chosen = controls[1].multiselect(
        "Geography",
        [entry[0] for entry in entries],
        default=default_geographies,
        format_func=lambda c: entry_labels.get(c, c),
        # Availability differs per series, so each indicator keeps its own
        # selection rather than inheriting one that reports nothing.
        key=f"env_geographies_{code}",
        help="Add your birth or current country to compare against the default.",
    )

    indicator_years = sorted({int(y) for y in for_indicator["year"]})
    if not indicator_years:
        st.warning(
            f"The World Bank reported no {indicator.short_label.lower()} "
            f"observations for these countries.",
            icon="⚠️",
        )
        render_environment_errors(errors)
        return

    # Opens on the birth year where the series covers it. Someone born in the
    # last reported year gets a single-year label rather than a crash.
    year_range = year_range_selector(
        controls[2], indicator_years, key=f"env_years_{code}", birth_year=birth_year
    )

    if WORLD_CODE not in reporting:
        st.caption(
            "The World Bank does not publish a world aggregate for this series, "
            "so only country-level figures are available here."
        )

    if not chosen:
        st.info("Select at least one geography to draw the chart.", icon="ℹ️")
        render_environment_methodology(code)
        return

    # Selection order, not registry order: the cards describe whichever
    # geography the user picked first, and the caption below says so.
    selected = order_selection(entries, chosen)
    windowed = window_frame(frame, code, year_range[0], year_range[1], chosen)

    if windowed.empty:
        st.info(
            f"No {indicator.short_label.lower()} observations fall inside "
            f"{year_range[0]}–{year_range[1]} for this selection.",
            icon="🌍",
        )
        render_environment_methodology(code)
        return

    cumulative_ok = is_cumulative_meaningful(code)
    summaries = [
        summarize_depletion(
            windowed,
            code,
            country_code,
            birth_year,
            country_name=label,
            cumulative=cumulative_ok,
        )
        for country_code, label, _ in selected
    ]

    # --- Chart -------------------------------------------------------------
    fig = go.Figure(series_traces(windowed, code, selected))
    if not fig.data:
        st.info("No series could be drawn for this selection.", icon="🌍")
        render_environment_methodology(code)
        return

    focus = summaries[0]
    fig.update_layout(title=f"{indicator.label} — {focus.country_name}"
                      if len(summaries) == 1 else indicator.label)
    if year_range[0] <= birth_year <= year_range[1]:
        add_year_markers(fig, birth_year, focus.latest.year if focus.latest else None)
    elif focus.latest is not None:
        fig.add_vline(
            x=focus.latest.year,
            line_dash="dot",
            line_color=COLOR_GOOD,
            annotation_text=f"📅 Latest reported {focus.latest.year}",
            annotation_position="top right",
        )
    style_figure(fig, indicator)
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "Unreported years are left as gaps rather than joined across. The "
        "right-hand marker is the latest year the World Bank reported — not "
        "the current calendar year."
    )

    # --- Summary cards -----------------------------------------------------
    st.markdown(f"##### {indicator.emoji} {focus.country_name} — recorded depletion")

    if not focus.has_data:
        st.info(
            f"The World Bank reported no {indicator.short_label.lower()} for "
            f"{focus.country_name} in this range.",
            icon="🌍",
        )
    else:
        if focus.ends_before_birth_year:
            st.info(
                f"This series ends in {focus.latest.year}, before you were born "
                f"in {birth_year}. The chart above is real reported data, but "
                f"none of it falls inside your lifetime, so no birth-year "
                f"baseline or lifetime total is shown.",
                icon="🌍",
            )

        top = st.columns(3)
        if focus.birth is None:
            top[0].markdown(
                stat_card(
                    "Birth-year value",
                    UNAVAILABLE,
                    "nothing reported from your birth year onward",
                    missing=True,
                    accent=COLOR_BIRTH,
                    icon="🎂",
                ),
                unsafe_allow_html=True,
            )
        else:
            exact = focus.birth.year == birth_year
            top[0].markdown(
                stat_card(
                    "Birth-year value",
                    format_value(focus.birth.value, indicator),
                    f"{focus.birth.year} data"
                    + ("" if exact else " · first reported year of your lifetime"),
                    accent=COLOR_BIRTH,
                    icon="🎂",
                ),
                unsafe_allow_html=True,
            )
        top[1].markdown(
            stat_card(
                "Latest reported value",
                format_value(focus.latest.value, indicator),
                f"{focus.latest.year} — latest reported year",
                accent=COLOR_GOOD,
                icon="📅",
            ),
            unsafe_allow_html=True,
        )
        if focus.cumulative is None:
            # Two different reasons, and they must not be confused: a ratio
            # cannot be summed at all, whereas a currency series simply has
            # nothing reported inside this lifetime.
            top[2].markdown(
                stat_card(
                    "Cumulative depletion",
                    "Not applicable" if not cumulative_ok else UNAVAILABLE,
                    "a share of GDP cannot be summed across years"
                    if not cumulative_ok
                    else "nothing reported from your birth year onward",
                    missing=True,
                    accent=COLOR_CURRENT,
                    icon="🧮",
                ),
                unsafe_allow_html=True,
            )
        else:
            top[2].markdown(
                stat_card(
                    "Cumulative depletion",
                    format_value(focus.cumulative, indicator),
                    f"reported years from {max(birth_year, focus.first_year or birth_year)} "
                    f"onward · nominal, not inflation-adjusted",
                    accent=COLOR_CURRENT,
                    icon="🧮",
                ),
                unsafe_allow_html=True,
            )

        bottom = st.columns(3)
        bottom[0].markdown(
            stat_card(
                "Peak year",
                str(focus.peak.year) if focus.peak else UNAVAILABLE,
                "highest recorded year in range",
                missing=focus.peak is None,
                accent="#eda100",
                icon="⛰️",
            ),
            unsafe_allow_html=True,
        )
        against_peak = share_of_peak(focus)
        peak_note = (
            f"recorded in {focus.peak.year}" if focus.peak else "not reported"
        )
        if against_peak is not None:
            peak_note += f" · latest is {against_peak:,.0f}% of it"
        bottom[1].markdown(
            stat_card(
                "Peak value",
                format_value(focus.peak.value, indicator) if focus.peak else UNAVAILABLE,
                peak_note,
                missing=focus.peak is None,
                accent="#eda100",
                icon="📈",
            ),
            unsafe_allow_html=True,
        )
        bottom[2].markdown(
            stat_card(
                "Coverage years",
                f"{focus.reported_years}",
                f"{focus.coverage_label} · reported, not interpolated",
                accent="#4a3aa7",
                icon="🗂️",
            ),
            unsafe_allow_html=True,
        )

    st.markdown(
        f"**What this shows:** "
        f"{depletion_interpretation(focus, indicator, focus.country_name)}"
    )

    if len(summaries) > 1:
        st.caption(
            "Cards describe "
            f"{focus.country_name}, the first selected geography. The chart and "
            "the export cover every geography selected."
        )

    # --- Raw data and export ----------------------------------------------
    table = windowed.rename(
        columns={
            "indicator": "Indicator",
            "country_code": "Country code",
            "country_name": "Country",
            "year": "Year",
            "value": "Value",
        }
    ).sort_values(["Country", "Year"])

    with st.expander(f"View raw data ({len(table)} observations)"):
        st.dataframe(table, width="stretch", hide_index=True)
        if len(summaries) > 1:
            st.markdown("**Summary, as exported**")
            st.dataframe(
                summary_export_frame(summaries, ENVIRONMENT_INDICATORS),
                width="stretch",
                hide_index=True,
            )

    context = {
        "Birth year": str(birth_year),
        "Selected indicator": f"{indicator.code} — {indicator.label}",
        "Selected geography": ", ".join(summary.country_name for summary in summaries),
        "Year range": f"{year_range[0]}–{year_range[1]}",
    }

    try:
        workbook = build_environment_workbook(
            windowed,
            summaries,
            ENVIRONMENT_INDICATORS,
            ENVIRONMENT_SOURCE_ATTRIBUTION,
            ENVIRONMENT_CAVEATS,
            context=context,
        )
    except (ImportError, ValueError) as exc:
        st.warning(
            f"The Excel workbook could not be assembled ({exc}). Install "
            "`openpyxl` to enable the download.",
            icon="⚠️",
        )
    else:
        st.download_button(
            "⬇️ Download Excel workbook",
            data=workbook,
            file_name=(
                f"lifelens_earth_{code}_{year_range[0]}_{year_range[1]}.xlsx"
            ),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            help="Three sheets: annual_data, summary and methodology.",
        )

    render_environment_methodology(code)
    render_environment_errors(errors)
    st.caption(f"{ENVIRONMENT_SOURCE_ATTRIBUTION} · Indicator code: {code}")


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------

def render_errors(story: dict) -> None:
    """Surface partial failures without hiding the data that did load."""
    errors = story["errors"]
    if not errors:
        return
    with st.expander(f"⚠️ {len(errors)} indicator(s) could not be loaded"):
        for code, reason in errors.items():
            st.markdown(f"- **{get_indicator(code).label}** — {reason}")
        st.caption("Everything else on this page loaded normally.")


def render_placeholder(title: str, description: str, items: list[str]) -> None:
    st.subheader(title)
    st.info(description, icon="🚧")
    for item in items:
        st.markdown(f"- {item}")


def render_prompt() -> None:
    st.markdown(
        "### Start here\n"
        "Fill in your details in the sidebar and press **Explore My Lifetime**."
    )
    st.markdown(
        "LifeLens compares the world of your birth year with the most recent year "
        "the World Bank has reported, across six development indicators — and, on "
        "**🌱 Earth & Resources**, the energy, mineral and forest depletion recorded "
        "during your lifetime."
    )
    columns = st.columns(3)
    accents = [COLOR_BIRTH, COLOR_CURRENT, COLOR_WORLD, "#eda100", "#e87ba4", "#4a3aa7"]
    for column, indicator, accent in zip(columns * 2, INDICATORS.values(), accents):
        column.markdown(
            f'<div class="lens-card" style="--accent:{accent}">'
            f'<div class="lens-label">{indicator.emoji} {indicator.beat}</div>'
            f'<div class="lens-value" style="font-size:1.05rem">{indicator.short_label}</div>'
            f'<div class="lens-year">{indicator.code}</div></div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    load_css()
    render_hero()

    try:
        countries = country_options()
    except WorldBankError as exc:
        st.error(
            f"Could not load the country list from the World Bank API. {exc}",
            icon="🚫",
        )
        if st.button("Try again"):
            country_options.clear()
            st.rerun()
        return

    if countries.empty:
        st.error("The World Bank returned an empty country list.", icon="🚫")
        return

    profile = sidebar_form(countries)

    if profile is not None:
        try:
            st.session_state["story"] = load_story_data(profile)
        except WorldBankError as exc:
            st.error(
                f"Could not retrieve indicator data. {exc}  \n"
                "This is usually temporary — please try again.",
                icon="🚫",
            )
            st.session_state.pop("story", None)

    tabs = st.tabs(
        [
            "📖 My Story",
            "📈 Lifetime in Data",
            "🌍 My Two Worlds",
            "🗓️ Timeline & Discoveries",
            "🌱 Earth & Resources",
            "🎯 Quiz & Share",
        ]
    )

    story = st.session_state.get("story")

    with tabs[0]:
        if story is None:
            render_prompt()
        elif story["frame"].empty:
            st.error("No indicator data was returned for these inputs.", icon="🚫")
            render_errors(story)
        else:
            render_my_story(story)

    with tabs[1]:
        if story is None:
            st.info("Press **Explore My Lifetime** in the sidebar to load your data.", icon="👈")
        elif story["frame"].empty:
            st.error("No indicator data available to chart.", icon="🚫")
        else:
            render_lifetime_in_data(story)

    with tabs[2]:
        if story is None:
            st.info("Press **Explore My Lifetime** in the sidebar to load your data.", icon="👈")
        elif story["frame"].empty:
            st.error("No indicator data available to compare.", icon="🚫")
        else:
            render_two_worlds(story)

    with tabs[3]:
        if story is None:
            st.info("Press **Explore My Lifetime** in the sidebar to load your data.", icon="👈")
        elif story["frame"].empty:
            st.error("No indicator data available to build a timeline.", icon="🚫")
        else:
            render_timeline(story)

    with tabs[4]:
        if story is None:
            st.info("Press **Explore My Lifetime** in the sidebar to load your data.", icon="👈")
        else:
            # Depletion data is fetched separately, so this tab renders even when
            # the development indicators came back empty.
            render_earth_resources(story)

    with tabs[5]:
        render_placeholder(
            "Quiz & Share",
            "Arriving in the final phase.",
            [
                "Three guess-before-reveal questions drawn from your own data",
                "Your guess compared against the real figure, with a score out of three",
                "A downloadable plain-text LifeLens story",
            ],
        )


if __name__ == "__main__":
    main()
