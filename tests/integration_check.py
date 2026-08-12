"""Live World Bank integration check (not part of the offline test suite).

Hits the real API for India, the United States and the World aggregate across
1990 to the present, and reports coverage per indicator and country.

Run:  python tests/integration_check.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.world_bank import WorldBankError, _fetch_indicator  # noqa: E402
from utils.calculations import coverage  # noqa: E402

COUNTRIES = [("IND", "India"), ("USA", "United States"), ("WLD", "World")]
START_YEAR = 1990
END_YEAR = date.today().year

INDICATOR_CODES = [
    "SP.POP.TOTL",
    "SP.DYN.LE00.IN",
    "NY.GDP.PCAP.KD",   # constant 2015 US$ — the MVP's GDP indicator
    "IT.NET.USER.ZS",
    "SP.URB.TOTL.IN.ZS",
    "SH.DYN.MORT",
]


def main() -> int:
    header = f"{'Indicator':<20} {'Country':<15} {'First':>6} {'Last':>6} {'Obs':>5}  Missing-data issues"
    print(f"\nWorld Bank integration check — {START_YEAR} to {END_YEAR}")
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    failures = 0

    for code in INDICATOR_CODES:
        try:
            frame = _fetch_indicator(code, [c for c, _ in COUNTRIES], START_YEAR, END_YEAR)
        except WorldBankError as exc:
            print(f"{code:<20} {'ALL':<15} {'-':>6} {'-':>6} {'-':>5}  REQUEST FAILED: {exc}")
            failures += 1
            continue

        for country_code, country_name in COUNTRIES:
            report = coverage(frame, code, country_code, START_YEAR, END_YEAR)
            first = report.earliest_year or "-"
            last = report.latest_year or "-"
            print(
                f"{code:<20} {country_name:<15} {first:>6} {last:>6} "
                f"{report.observations:>5}  {report.issue}"
            )
            if report.observations == 0:
                failures += 1
        print("-" * len(header))

    print(f"\nIndicators checked: {len(INDICATOR_CODES)} | countries: {len(COUNTRIES)} "
          f"| empty results: {failures}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
