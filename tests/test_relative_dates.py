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

from navi_bench.relative_dates import (
    _expand_md_range,
    days_until_next_weekday,
    parse_relative_date,
    parse_relative_dates,
)


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


class TestWeekdaysRequiredBranchesRaiseOnUnparsedWeekdays:
    """Both the "<weekdays> in <mod> month" and "<weekdays> in <month-ref> through
    <month-ref>" branches of ``parse_relative_dates`` require a non-empty weekday filter on
    their left-hand side and previously repeated the identical
    ``_collect_weekdays_list(...) -> if not wds: raise ValueError(...)`` guard verbatim; now
    both delegate to the shared ``_parse_weekdays_or_raise`` helper. Pins that an
    unparseable left-hand side still raises the same error in both branches, and that the
    sibling "from <date> through <date>" branch (which deliberately treats a missing
    weekday filter as "no filter" rather than raising) is unaffected."""

    def test_in_mod_month_branch_raises_on_unparsed_weekdays(self):
        with pytest.raises(ValueError, match="Could not parse weekdays in the left-hand side"):
            parse_relative_dates("foo in this month", BASE_DATE)

    def test_month_ref_through_month_ref_branch_raises_on_unparsed_weekdays(self):
        with pytest.raises(ValueError, match="Could not parse weekdays in the left-hand side"):
            parse_relative_dates("foo in Jan through Mar", BASE_DATE)

    def test_from_through_branch_treats_unparsed_weekdays_as_no_filter_not_a_raise(self):
        # Sibling branch, deliberately different behavior: falls back to "no weekday
        # filter" (every day in the span) instead of raising.
        assert parse_relative_dates("foo from Nov 10 through Nov 12", BASE_DATE) == [
            "2025-11-10",
            "2025-11-11",
            "2025-11-12",
        ]


class TestExpandMdRangeDirect:
    """Direct coverage for ``_expand_md_range`` (no prior direct test coverage; it was
    previously only exercised indirectly through ``parse_relative_dates``). Pins its
    day-clamping behavior, including the "next"-modifier year-bump branch, which now
    delegates to ``clamp_day`` instead of hand-rolling ``_days_in_month``/``min``/``max``
    twice (matching the "bump year, clamp day" idiom already used elsewhere in this module,
    e.g. ``TestFeb29YearBump`` above)."""

    def test_no_modifier_no_bump(self):
        assert _expand_md_range(2025, 5, 11, 14) == [
            date(2025, 5, 11),
            date(2025, 5, 12),
            date(2025, 5, 13),
            date(2025, 5, 14),
        ]

    def test_reversed_start_end_normalized(self):
        assert _expand_md_range(2025, 5, 14, 11) == [
            date(2025, 5, 11),
            date(2025, 5, 12),
            date(2025, 5, 13),
            date(2025, 5, 14),
        ]

    def test_out_of_range_days_clamp_to_month_length(self):
        # April has 30 days; day 31 should clamp down to 30.
        assert _expand_md_range(2025, 4, 29, 35) == [date(2025, 4, 29), date(2025, 4, 30)]

    def test_next_modifier_bumps_year_when_start_has_passed(self):
        base = date(2025, 6, 1)  # after May 11-14, 2025 has already passed
        assert _expand_md_range(2025, 5, 11, 14, base=base, modifier="next") == [
            date(2026, 5, 11),
            date(2026, 5, 12),
            date(2026, 5, 13),
            date(2026, 5, 14),
        ]

    def test_next_modifier_no_bump_when_start_still_upcoming(self):
        base = date(2025, 4, 1)  # before May 11-14, 2025
        assert _expand_md_range(2025, 5, 11, 14, base=base, modifier="next") == [
            date(2025, 5, 11),
            date(2025, 5, 12),
            date(2025, 5, 13),
            date(2025, 5, 14),
        ]

    def test_next_modifier_feb_29_bumped_into_non_leap_year_clamps_to_28(self):
        # 2028 is a leap year; bumping "next Feb 27-29" past it lands on 2029, a non-leap
        # year, so day 29 must clamp to 28 via clamp_day instead of raising ValueError.
        base = date(2028, 3, 1)  # after Feb 27-29, 2028 has already passed
        assert _expand_md_range(2028, 2, 27, 29, base=base, modifier="next") == [
            date(2029, 2, 27),
            date(2029, 2, 28),
        ]

    def test_coming_modifier_behaves_like_next(self):
        base = date(2025, 6, 1)
        assert _expand_md_range(2025, 5, 11, 14, base=base, modifier="coming") == _expand_md_range(
            2025, 5, 11, 14, base=base, modifier="next"
        )


class TestParseRelativeDatesUnparsableFallbackChainsCause:
    """``parse_relative_dates``'s final fallback (single-date parser) wraps a ``ValueError``
    raised by ``parse_relative_date`` into its own, more specific ``ValueError``. This pins that
    the wrapping preserves exception chaining (``raise ... from e``), matching the convention
    used everywhere else in the repo an exception is re-raised as a different type (e.g.
    ``base.py``'s ``ImportError from e``, ``google_flights_search_match.py``'s
    ``ValueError from e``) -- this was previously the sole site missing ``from e``, which drops
    the original traceback/cause when the wrapped error is inspected or logged.
    """

    def test_unparsable_query_raises_with_original_error_as_cause(self):
        with pytest.raises(ValueError, match="Could not parse date range/multiple description") as exc_info:
            parse_relative_dates("asdfghjkl gibberish query", BASE_DATE)

        assert isinstance(exc_info.value.__cause__, ValueError)
        assert "Could not parse relative date description" in str(exc_info.value.__cause__)


class TestMonthDayPatternFallThrough:
    """Pins the ordering/fall-through semantics of the ``_MONTH_DAY_PATTERNS`` table that
    ``parse_relative_date`` iterates. Each accepted month+day phrasing used to be its own
    ``re.fullmatch`` + ``if m and m.group(N) in MONTHS`` block; a phrasing whose regex matched
    but whose month group was not a real month name fell through to the *next* phrasing (and
    ultimately to the weekday/holiday/``in N units`` branches below). These tests pin that
    behavior so the table-driven loop's ``continue``-to-next-pattern is verifiable.
    """

    @pytest.mark.parametrize(
        "text,expected",
        [
            # A) month + day
            ("next Dec. 3rd", "2025-12-03"),
            # B1) leading modifier + day + 'of' + month
            ("next 3rd of December", "2025-12-03"),
            # B2) day + month + trailing modifier
            ("the 3rd of December next", "2025-12-03"),
            # B3) day + modifier + month
            ("the 3rd next December", "2025-12-03"),
            # B4) day + month, no modifier
            ("3rd of Dec.", "2025-12-03"),
        ],
    )
    def test_each_month_day_phrasing_resolves_identically(self, text, expected):
        assert parse_relative_date(text, BASE_DATE) == expected

    def test_month_day_shape_with_non_month_word_falls_through_to_later_branches(self):
        # Matches the bare day+word shape, but "month" is not a month name, so the
        # month+day patterns must all decline and let the "<D> of the <mod> month"
        # branch handle it.
        assert parse_relative_date("26th of the next month", BASE_DATE) == "2025-12-26"

    def test_month_day_shape_with_unknown_word_is_unparseable(self):
        # Same shape, but nothing further down the chain claims it either.
        with pytest.raises(ValueError, match="Could not parse relative date description"):
            parse_relative_date("3rd of notamonth", BASE_DATE)

    def test_modifier_plus_weekday_is_not_captured_by_month_day_patterns(self):
        # "next monday" matches the bare ``<mod>? <word>`` weekday branch, not a month+day
        # phrasing; pins that the month+day loop declines it rather than consuming it.
        assert parse_relative_date("next Monday", BASE_DATE) == "2025-11-10"
