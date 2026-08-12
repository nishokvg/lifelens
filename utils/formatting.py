"""Display formatting: numbers, values, years, names, flags.

The single place where a float becomes a string. Keeping it here means every
tab reports the same number the same way, and the "Data unavailable" wording is
consistent everywhere.
"""

from __future__ import annotations

import html
from datetime import date
from typing import Optional

UNAVAILABLE = "Data unavailable"

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def human_number(value: Optional[float], decimals: int = 1) -> str:
    """Compact magnitude form: 1.42B, 336M, 8.1K, 62.4."""
    if value is None:
        return UNAVAILABLE

    magnitude = abs(value)
    sign = "-" if value < 0 else ""

    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= threshold:
            return f"{sign}{magnitude / threshold:.{decimals}f}{suffix}"

    if magnitude >= 100:
        return f"{sign}{magnitude:,.0f}"
    return f"{sign}{magnitude:,.{decimals}f}"


def full_number(value: Optional[float], decimals: int = 0) -> str:
    """Fully written out with thousands separators: 1,417,492,000."""
    if value is None:
        return UNAVAILABLE
    return f"{value:,.{decimals}f}"


def format_value(value: Optional[float], indicator, compact: bool = True) -> str:
    """Render a value according to its indicator's unit and precision."""
    if value is None:
        return UNAVAILABLE

    if getattr(indicator, "is_percentage", False):
        return f"{value:.{indicator.decimals}f}%"

    unit = getattr(indicator, "unit", "")

    if "US$" in unit:
        return f"${human_number(value, 2)}" if compact else f"${full_number(value, 0)}"

    if unit == "people":
        return human_number(value, 2) if compact else full_number(value, 0)

    if unit == "years":
        return f"{value:.{indicator.decimals}f} years"

    if unit.startswith("per 1,000"):
        return f"{value:.{indicator.decimals}f}"

    return f"{value:,.{indicator.decimals}f}"


def format_change(value: Optional[float], indicator, always_sign: bool = True) -> str:
    """A change in an indicator's own units, sign-prefixed."""
    if value is None:
        return UNAVAILABLE
    sign = "+" if value > 0 and always_sign else ""
    return f"{sign}{format_value(value, indicator)}"


def format_percent(value: Optional[float], decimals: int = 1, always_sign: bool = True) -> str:
    """A percent *change*. Distinct from a percentage-point change."""
    if value is None:
        return UNAVAILABLE
    sign = "+" if value > 0 and always_sign else ""
    return f"{sign}{value:,.{decimals}f}%"


def format_points(value: Optional[float], decimals: int = 1) -> str:
    """A percentage-point change, for indicators already measured in percent."""
    if value is None:
        return UNAVAILABLE
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.{decimals}f} pp"


def format_date(value: date) -> str:
    return f"{MONTHS[value.month - 1]} {value.day}, {value.year}"


def year_label(year: Optional[int]) -> str:
    """Year suffix appended to every statistic, so no figure is undated."""
    return f"({year} data)" if year else "(year unknown)"


def pluralize(count: int, word: str, plural: Optional[str] = None) -> str:
    """"1 month", "36 years"."""
    if count == 1:
        return f"{count} {word}"
    return f"{count} {plural or word + 's'}"


# Country names that read as "the X" in running prose. Matched by suffix so the
# list stays short and covers names the World Bank spells its own way
# (for example "Russian Federation", "Lao PDR").
_ARTICLE_SUFFIXES = (
    "Islands", "Island", "Republic", "Emirates", "States", "Kingdom",
    "Federation", "Netherlands", "Philippines", "Maldives", "Seychelles",
    "Comoros", "Gambia", "Bahamas", "PDR", "Union", "Territories",
)


def article_name(name: str) -> str:
    """Prefix "the" where English requires it: "the United States".

    Names the World Bank already writes with an article ("Bahamas, The") are
    left alone.
    """
    if not name or ", The" in name:
        return name
    if name == "World":
        return "the world"  # the World Bank aggregate, read as prose
    if name.endswith(_ARTICLE_SUFFIXES):
        return f"the {name}"
    return name


def sentence_label(label: str) -> str:
    """Lower-case a label for mid-sentence use, preserving acronyms.

    "Life expectancy" -> "life expectancy", but "GDP per capita" is unchanged.
    """
    if not label:
        return label
    first = label.split()[0]
    if first.isupper() and len(first) > 1:
        return label
    return label[0].lower() + label[1:]


def ordinal(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value:,}{suffix}"


def safe_name(name: Optional[str]) -> str:
    """Escape user input before it reaches a markdown/HTML render path."""
    if not name:
        return ""
    return html.escape(name.strip())[:60]


def possessive(name: str) -> str:
    if not name:
        return "Your"
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def flag_emoji(iso2: Optional[str]) -> str:
    """Regional-indicator flag from an ISO-3166 alpha-2 code.

    Aggregates such as the World ("1W") are not real countries and fall back to
    a globe.
    """
    if not iso2 or len(iso2) != 2 or not iso2.isalpha():
        return "🌍"
    return "".join(chr(0x1F1E6 + ord(char.upper()) - ord("A")) for char in iso2)


def direction_word(direction: str, better: str) -> str:
    """Plain-English verb for a movement."""
    if direction == "flat":
        return "stayed about the same"
    return "rose" if direction == "up" else "fell"


def is_improvement(direction: str, better: str) -> Optional[bool]:
    """Whether a movement counts as good. ``None`` for neutral indicators."""
    if better not in ("up", "down") or direction == "flat":
        return None
    return direction == better
