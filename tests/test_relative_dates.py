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

from navi_bench.relative_dates import days_until_next_weekday, parse_relative_date, parse_relative_dates


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


class TestOfTheModMonthBranch:
    """Characterization tests for the "<D> of the <mod> month" branch of
    ``parse_relative_date`` (e.g. "26th of the next month"). The loose pattern here
    (optional leading "on"/"the", optional "of"/"the" before the modifier) is a strict
    superset of the "<D> of the <mod> month" literal phrasing, so it always matches first
    and the phrasing pins down that the loose pattern alone is sufficient -- no separate
    "strict" fallback pattern is reachable or needed."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("26th of the next month", "2025-12-26"),
            ("3rd of the last month", "2025-10-03"),
            ("1st of the this month", "2025-11-01"),
        ],
    )
    def test_of_the_mod_month_phrasing(self, text, expected):
        assert parse_relative_date(text, BASE_DATE) == expected


class TestWeekdaysInMonthRangeBranch:
    """Characterization tests for the ``parse_relative_dates`` "<weekdays> in <month-ref>
    through <month-ref>" branch (e.g. "Mondays and Fridays in next Jan through Mar"), which
    walks from the start month to the end month inclusive using the shared ``add_months``
    helper for its month-rollover step, the same helper already used by
    ``opentable_info_gathering.get_first_weekend_of_next_month_offsets`` (#144); this branch's
    loop crosses a December -> January boundary whenever the requested range spans the turn
    of the year, exercising that rollover.
    """

    def test_single_month_range(self):
        assert parse_relative_dates("Mondays and Fridays in next Jan through Mar", BASE_DATE) == [
            "2026-01-02",
            "2026-01-05",
            "2026-01-09",
            "2026-01-12",
            "2026-01-16",
            "2026-01-19",
            "2026-01-23",
            "2026-01-26",
            "2026-01-30",
            "2026-02-02",
            "2026-02-06",
            "2026-02-09",
            "2026-02-13",
            "2026-02-16",
            "2026-02-20",
            "2026-02-23",
            "2026-02-27",
            "2026-03-02",
            "2026-03-06",
            "2026-03-09",
            "2026-03-13",
            "2026-03-16",
            "2026-03-20",
            "2026-03-23",
            "2026-03-27",
            "2026-03-30",
        ]

    def test_range_crossing_december_into_january(self):
        # "this month" (Nov) through "next Jan" walks Nov -> Dec -> Jan, exercising the
        # mo == 12 rollover branch mid-loop.
        assert parse_relative_dates("Mondays in this month through next Jan", BASE_DATE) == [
            "2025-11-03",
            "2025-11-10",
            "2025-11-17",
            "2025-11-24",
            "2025-12-01",
            "2025-12-08",
            "2025-12-15",
            "2025-12-22",
            "2025-12-29",
            "2026-01-05",
            "2026-01-12",
            "2026-01-19",
            "2026-01-26",
        ]


class TestLastWeekdayBranch:
    """Characterization tests for the "last/previous <weekday>" branch of
    ``parse_relative_date``. This branch delegates to the shared
    ``days_until_next_weekday`` helper (the same ``(target - current) % 7``-with-
    zero-bumped-to-7 math used elsewhere in this module), just with the two weekday
    arguments swapped since it's counting backwards. BASE_DATE (2025-11-06) is a
    Thursday.
    """

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("last Monday", "2025-11-03"),
            ("last Thursday", "2025-10-30"),  # same weekday as base rolls back a full week
            ("last Sunday", "2025-11-02"),
            ("last Friday", "2025-10-31"),
            ("previous Monday", "2025-11-03"),  # "previous" is a synonym for "last"
        ],
    )
    def test_last_weekday(self, text, expected):
        assert parse_relative_date(text, BASE_DATE) == expected


class TestDaysUntilNextWeekday:
    """Direct coverage for ``days_until_next_weekday``, the shared "next strictly-future
    weekday" helper extracted from the duplicated (target - current) % 7, bump-0-to-7 math
    in this module's weekday branch and in
    ``opentable_info_gathering.get_days_until_date``'s "for the upcoming <weekday>" branch."""

    def test_same_weekday_rolls_to_next_week(self):
        assert days_until_next_weekday(3, 3) == 7

    @pytest.mark.parametrize(
        "current_weekday,target_weekday,expected",
        [
            (3, 4, 1),  # Thu -> Fri
            (3, 0, 4),  # Thu -> Mon (wraps past week boundary)
            (0, 6, 6),  # Mon -> Sun
            (6, 0, 1),  # Sun -> Mon (wraps forward)
        ],
    )
    def test_future_weekday_offset(self, current_weekday, target_weekday, expected):
        assert days_until_next_weekday(current_weekday, target_weekday) == expected


class TestFeb29YearBump:
    """``_choose_occurrence`` (used by every modifier'd month+day/holiday branch of
    ``parse_relative_date``) and the "from <A> through <B>" branch of
    ``parse_relative_dates`` both bump a resolved date into a neighboring year. Both used
    to do so with raw ``date.replace(year=...)``/``date(y + 1, m, d)`` construction, which
    raises ``ValueError`` whenever a Feb 29 occurrence lands on a non-leap year -- the same
    "hand-rolled year/month rollover instead of delegating to the module's own
    clamp_day/add_months helpers" bug pattern already fixed for December -> January
    rollover in #144/#145. Both now go through ``clamp_day`` (already used for this exact
    "bump year, clamp day" idiom at this module's "in N years" branch), matching every
    other date shifted into a shorter month.
    """

    def test_next_feb_29_shifted_into_non_leap_year_clamps_to_feb_28(self):
        # target_this_year (Feb 29, 2028, a leap year) is already in the past relative to
        # base, so the "next" modifier must shift it forward into 2029, a non-leap year.
        assert parse_relative_date("next Feb 29", date(2028, 3, 1)) == "2029-02-28"

    def test_last_feb_29_shifted_into_non_leap_year_clamps_to_feb_28(self):
        # Mirror of the "next" case above: target_this_year (Feb 29, 2028) hasn't happened
        # yet relative to base, so "last" must shift back into 2027, a non-leap year.
        assert parse_relative_date("last Feb 29", date(2028, 1, 15)) == "2027-02-28"

    def test_from_through_span_with_feb_29_end_bumped_past_non_leap_year(self):
        # Resolving "Feb 29" relative to `start` (2028-03-15, after Feb 29 that year) forces
        # a "next occurrence" shift into 2029 (non-leap), which used to raise ValueError
        # inside the branch's inner try; the outer except then fell back to resolving "Feb
        # 29" relative to `base` (2028-01-15, before Feb 29 that year, so no shift/crash),
        # got 2028-02-29, saw it was < start, and bumped the year again -- which used to
        # raise the same ValueError a second time, escaping to the wrong outer except
        # branch entirely and silently misparsing the whole expression as a literal
        # start/end pair (producing a nonsensical span that runs *backwards* from
        # 2028-01-22 to 2028-03-15). The fix makes both bumps clamp Feb 29 -> Feb 28
        # instead of raising, so the span correctly runs forward from the resolved start
        # (Saturdays only) through the clamped end.
        assert parse_relative_dates("Sat from March 15 through Feb 29", date(2028, 1, 15)) == [
            "2028-03-18",
            "2028-03-25",
            "2028-04-01",
            "2028-04-08",
            "2028-04-15",
            "2028-04-22",
            "2028-04-29",
            "2028-05-06",
            "2028-05-13",
            "2028-05-20",
            "2028-05-27",
            "2028-06-03",
            "2028-06-10",
            "2028-06-17",
            "2028-06-24",
            "2028-07-01",
            "2028-07-08",
            "2028-07-15",
            "2028-07-22",
            "2028-07-29",
            "2028-08-05",
            "2028-08-12",
            "2028-08-19",
            "2028-08-26",
            "2028-09-02",
            "2028-09-09",
            "2028-09-16",
            "2028-09-23",
            "2028-09-30",
            "2028-10-07",
            "2028-10-14",
            "2028-10-21",
            "2028-10-28",
            "2028-11-04",
            "2028-11-11",
            "2028-11-18",
            "2028-11-25",
            "2028-12-02",
            "2028-12-09",
            "2028-12-16",
            "2028-12-23",
            "2028-12-30",
            "2029-01-06",
            "2029-01-13",
            "2029-01-20",
            "2029-01-27",
            "2029-02-03",
            "2029-02-10",
            "2029-02-17",
            "2029-02-24",
        ]
