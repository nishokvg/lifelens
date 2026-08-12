"""Deterministic narrative generation.

Sentences are built from templates filled with retrieved values. There is no
randomness and no language model — the same inputs always produce the same
words, which is what makes the output checkable against the charts.

Two rules run through this module:

* **The name is optional.** Every template reads correctly with a name and
  without one ("During your lifetime, ..." rather than "Nishok, during ...").
* **Never overstate.** Life expectancy at birth is a period measure for babies
  born in a given year; it is not a prediction about the reader. Phrasing here
  reflects that.
"""

from __future__ import annotations

from typing import Optional

from utils.calculations import Change
from utils.formatting import (
    UNAVAILABLE,
    article_name,
    format_percent,
    format_points,
    format_value,
    human_number,
    pluralize,
    sentence_label,
)


def opener(name: str, clause: str = "during your lifetime") -> str:
    """Open a sentence, with or without a name."""
    if name:
        return f"{name}, {clause}"
    return clause[0].upper() + clause[1:]


def address(name: str) -> str:
    return name if name else "you"


def describe_change(change: Optional[Change], indicator, country_name: str) -> str:
    """One plain-English sentence about a single indicator's movement."""
    if change is None:
        return f"{indicator.short_label} in {article_name(country_name)}: {UNAVAILABLE}."

    verb = {"up": "rose", "down": "fell", "flat": "stayed close to"}[change.direction]
    start = format_value(change.start.value, indicator, compact=False)
    end = format_value(change.end.value, indicator, compact=False)

    sentence = (
        f"In {article_name(country_name)}, {sentence_label(indicator.short_label)} "
        f"{verb} from {start} in {change.start.year} to {end} in {change.end.year}"
    )

    if indicator.is_percentage:
        magnitude = f"a change of {format_points(change.percentage_points)}"
    elif change.percent is not None:
        magnitude = (
            f"a change of {format_value(change.absolute, indicator, compact=False)} "
            f"({format_percent(change.percent)})"
        )
    else:
        magnitude = (
            f"a change of {format_value(change.absolute, indicator, compact=False)} "
            f"(no percentage comparison is possible from a starting value of zero)"
        )

    return f"{sentence} — {magnitude} over {change.years_elapsed} years."


def interpret(change: Optional[Change], indicator, country_name: str) -> str:
    """The interpretation shown under a chart: what the movement means."""
    if change is None:
        return (
            f"The World Bank has not reported enough "
            f"{sentence_label(indicator.short_label)} data for "
            f"{article_name(country_name)} to describe a trend over this period."
        )

    base = describe_change(change, indicator, country_name)

    if indicator.code == "SP.DYN.LE00.IN":
        return (
            f"{base} This measures how long a baby born in {change.end.year} could "
            f"expect to live under that year's mortality rates — it is not a "
            f"forecast for anyone already alive."
        )

    if indicator.better == "up" and change.direction == "up":
        return f"{base} Higher is generally considered better for this measure."
    if indicator.better == "down" and change.direction == "down":
        return f"{base} Lower is generally considered better for this measure."
    if indicator.better == "up" and change.direction == "down":
        return f"{base} This measure moved in the less favourable direction."
    if indicator.better == "down" and change.direction == "up":
        return f"{base} This measure moved in the less favourable direction."

    return f"{base} This measure has no inherently better direction."


def population_sentence(change: Optional[Change], label: str) -> str:
    label = article_name(label)
    if change is None:
        return f"Population data for {label} is unavailable."
    added = change.absolute
    return (
        f"{label[0].upper() + label[1:]} went from "
        f"{human_number(change.start.value, 2)} people in "
        f"{change.start.year} to {human_number(change.end.value, 2)} in "
        f"{change.end.year} — {human_number(abs(added), 2)} "
        f"{'more' if added >= 0 else 'fewer'} people."
    )


def headline_insight(code: str, change: Change, indicator, country_name: str) -> str:
    """The 'what changed the most' line."""
    magnitude = (
        format_percent(change.percent)
        if change.percent is not None
        else format_value(change.absolute, indicator, compact=False)
    )
    return (
        f"**{indicator.label}** changed most in relative terms: {magnitude} "
        f"between {change.start.year} and {change.end.year} in "
        f"{article_name(country_name)}."
    )


def build_story(
    name: str,
    birth_country: str,
    current_country: str,
    span,
    changes: dict[str, Change],
    indicators: dict,
) -> list[str]:
    """The multi-paragraph personal narrative on the My Story tab.

    Deterministic: every number comes from ``changes``, and any indicator
    missing from it is simply left out rather than guessed at.
    """
    paragraphs: list[str] = []

    moved = birth_country != current_country
    where = (
        f"You were born in {article_name(birth_country)} and now live in "
        f"{article_name(current_country)}."
        if moved
        else f"You were born in {article_name(birth_country)} and still call it home."
    )
    paragraphs.append(
        f"{opener(name, 'you have been alive for')} "
        f"{pluralize(span.years, 'year')} and {pluralize(span.months, 'month')} — about "
        f"{span.days_total:,} days. {where}"
    )

    life = changes.get("SP.DYN.LE00.IN")
    if life is not None:
        paragraphs.append(
            f"When {address(name)} arrived in {life.start.year}, a baby born in "
            f"{article_name(birth_country)} could expect to live "
            f"{format_value(life.start.value, indicators['SP.DYN.LE00.IN'], compact=False)}. "
            f"By {life.end.year} that figure had reached "
            f"{format_value(life.end.value, indicators['SP.DYN.LE00.IN'], compact=False)} — "
            f"{format_value(abs(life.absolute), indicators['SP.DYN.LE00.IN'], compact=False)} "
            f"{'more' if life.absolute >= 0 else 'less'} than at the start of your life."
        )

    net = changes.get("IT.NET.USER.ZS")
    if net is not None:
        if net.start.value < 1:
            paragraphs.append(
                f"The internet barely existed where you were born: in "
                f"{net.start.year}, {net.start.value:.1f}% of people in "
                f"{article_name(birth_country)} used it. By {net.end.year} that figure was "
                f"{net.end.value:.1f}% — a shift of "
                f"{format_points(net.percentage_points)} inside one lifetime."
            )
        else:
            paragraphs.append(
                f"Internet use in {article_name(birth_country)} went from {net.start.value:.1f}% "
                f"in {net.start.year} to {net.end.value:.1f}% in {net.end.year}, "
                f"a change of {format_points(net.percentage_points)}."
            )

    mortality = changes.get("SH.DYN.MORT")
    if mortality is not None and mortality.direction == "down":
        paragraphs.append(
            f"Child survival improved sharply. In {mortality.start.year}, "
            f"{mortality.start.value:.0f} of every 1,000 children born in "
            f"{article_name(birth_country)} died before their fifth birthday. By "
            f"{mortality.end.year} that had fallen to "
            f"{mortality.end.value:.0f} per 1,000."
        )

    pop = changes.get("SP.POP.TOTL")
    if pop is not None:
        paragraphs.append(
            f"And there are far more of us. {population_sentence(pop, birth_country)}"
        )

    return paragraphs


def timeline_interpretation(
    indicator,
    country_name: str,
    timeline_year: int,
    age: int,
    observation,
    birth_observation=None,
) -> str:
    """The short, deterministic line on the selected-year statistic card.

    States the value, the year it was actually observed, and — when a birth-year
    value exists — how far it had moved by then. Nothing is inferred beyond the
    two retrieved observations.
    """
    if observation is None:
        return (
            f"The World Bank has no {sentence_label(indicator.short_label)} "
            f"observation for {article_name(country_name)} within three years of "
            f"{timeline_year}."
        )

    value = format_value(observation.value, indicator, compact=False)
    stage = "the year you were born" if age == 0 else f"the year you turned {age}"

    if observation.year != timeline_year:
        provenance = (
            f"The nearest reported observation is from {observation.year}, "
            f"{abs(observation.year - timeline_year)} year"
            f"{'s' if abs(observation.year - timeline_year) != 1 else ''} "
            f"{'later' if observation.year > timeline_year else 'earlier'}."
        )
    else:
        provenance = f"This is the reported value for {timeline_year} itself."

    sentence = (
        f"In {timeline_year} — {stage} — {sentence_label(indicator.short_label)} "
        f"in {article_name(country_name)} stood at {value}. {provenance}"
    )

    if birth_observation is None or observation.year == birth_observation.year:
        return sentence

    difference = observation.value - birth_observation.value
    if difference == 0:
        return f"{sentence} That is unchanged from your birth year."

    movement = "higher" if difference > 0 else "lower"
    gap = (
        format_points(abs(difference))
        if indicator.is_percentage
        else format_value(abs(difference), indicator, compact=False)
    )
    return (
        f"{sentence} That is {gap.lstrip('+')} {movement} than in "
        f"{birth_observation.year}."
    )


def comparison_insight(
    indicator,
    year: int,
    name_a: str,
    value_a: float,
    name_b: str,
    value_b: float,
) -> str:
    """A neutral statement of the difference between two countries."""
    higher, lower = (name_a, name_b) if value_a >= value_b else (name_b, name_a)
    high_value, low_value = (
        (value_a, value_b) if value_a >= value_b else (value_b, value_a)
    )
    difference = abs(value_a - value_b)

    detail = (
        f"{format_value(high_value, indicator, compact=False)} versus "
        f"{format_value(low_value, indicator, compact=False)}"
    )
    gap = (
        format_points(difference)
        if indicator.is_percentage
        else format_value(difference, indicator, compact=False)
    )

    return (
        f"In {year}, {article_name(higher)} recorded a higher "
        f"{sentence_label(indicator.short_label)} than {article_name(lower)} — "
        f"{detail}, a difference of {gap}."
    )
