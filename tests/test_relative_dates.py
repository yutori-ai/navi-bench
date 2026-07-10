"""Characterization tests for ``navi_bench.relative_dates``.

This module has no prior test coverage even though it feeds ground-truth date generation
for resy, opentable, and google_flights via ``dates.py``'s ``resolve_placeholder_values``.
These tests pin the current, verified-correct output of ``parse_relative_date`` and
``parse_relative_dates`` against a fixed base date, using the worked examples already
documented in the module's own ``if __name__ == "__main__":`` block. They exist primarily
to give this module a safety net (matching this repo's convention of adding
characterization tests before/alongside structural refactors of untested code) and to pin
behavior across the ``_MONTH_DAY_RANGE_PATTERN`` regex-dedup refactor in this file.
"""

from datetime import date

import pytest

from navi_bench.relative_dates import parse_relative_date, parse_relative_dates


BASE_DATE = date(2025, 11, 6)  # a Thursday


@pytest.mark.parametrize(
    "text,expected",
    [
        ("upcoming Friday", "2025-11-07"),
        ("on the 26th next month", "2025-12-26"),
        ("26th next month", "2025-12-26"),
        ("26th of the next month", "2025-12-26"),
        ("15th in 3 months", "2026-02-15"),
        ("the 3rd next December", "2025-12-03"),
        ("3rd next December", "2025-12-03"),
        ("the 3rd of December next", "2025-12-03"),
        ("the 3rd of December", "2025-12-03"),
        ("next Dec. 3rd", "2025-12-03"),
        ("July 4th", "2026-07-04"),
        ("next Valentine's Day", "2026-02-14"),
        ("the next Valentine's Day", "2026-02-14"),
        ("the next Monday", "2025-11-10"),
        ("next MLK Day", "2026-01-19"),
        ("this Thanksgiving", "2025-11-27"),
        ("last Christmas", "2024-12-25"),
        ("in 2 weeks", "2025-11-20"),
    ],
)
def test_parse_relative_date(text, expected):
    assert parse_relative_date(text, BASE_DATE) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("upcoming Friday", ["2025-11-07"]),
        ("upcoming Thanksgiving", ["2025-11-27"]),
        (
            "Saturdays and Sundays in next month",
            [
                "2025-12-06",
                "2025-12-07",
                "2025-12-13",
                "2025-12-14",
                "2025-12-20",
                "2025-12-21",
                "2025-12-27",
                "2025-12-28",
            ],
        ),  # fmt: skip
        (
            "weekends in the next month",
            [
                "2025-12-06",
                "2025-12-07",
                "2025-12-13",
                "2025-12-14",
                "2025-12-20",
                "2025-12-21",
                "2025-12-27",
                "2025-12-28",
            ],
        ),  # fmt: skip
        (
            "next May 11-14 and May 18-21",
            [
                "2026-05-11",
                "2026-05-12",
                "2026-05-13",
                "2026-05-14",
                "2026-05-18",
                "2026-05-19",
                "2026-05-20",
                "2026-05-21",
            ],
        ),  # fmt: skip
        (
            "Sat and Sun from next Oct 12 through Nov 25",
            [
                "2026-10-17",
                "2026-10-18",
                "2026-10-24",
                "2026-10-25",
                "2026-10-31",
                "2026-11-01",
                "2026-11-07",
                "2026-11-08",
                "2026-11-14",
                "2026-11-15",
                "2026-11-21",
                "2026-11-22",
            ],
        ),  # fmt: skip
        (
            "next Nov 9th, 16th, 23th, 30th, and Dec 7th",
            [
                "2025-11-09",
                "2025-11-16",
                "2025-11-23",
                "2025-11-30",
                "2025-12-07",
            ],
        ),  # fmt: skip
    ],
)
def test_parse_relative_dates(text, expected):
    assert parse_relative_dates(text, BASE_DATE) == expected


class TestMonthDayRangePattern:
    """Targeted coverage for the two ``parse_relative_dates`` branches that share the
    extracted ``_MONTH_DAY_RANGE_PATTERN`` regex: the per-chunk "and"-joined multi-range
    case, and the single-range fallback case."""

    def test_multi_chunk_month_day_range_with_modifier_and_carried_month(self):
        # "May 18-21" (second chunk) has no month keyword of its own for the day range but
        # carries the year/modifier context established by "next May 11-14".
        assert parse_relative_dates("next May 11-14 and May 18-21", BASE_DATE) == [
            "2026-05-11",
            "2026-05-12",
            "2026-05-13",
            "2026-05-14",
            "2026-05-18",
            "2026-05-19",
            "2026-05-20",
            "2026-05-21",
        ]

    def test_single_range_fallback_with_month_and_modifier(self):
        assert parse_relative_dates("next Dec 3-5", BASE_DATE) == ["2025-12-03", "2025-12-04", "2025-12-05"]

    def test_single_range_fallback_bare_days_uses_current_month(self):
        assert parse_relative_dates("10-12", BASE_DATE) == ["2025-11-10", "2025-11-11", "2025-11-12"]

    def test_single_range_fallback_reversed_range_is_normalized(self):
        assert parse_relative_dates("Dec 5-3", BASE_DATE) == ["2025-12-03", "2025-12-04", "2025-12-05"]
