"""Characterization tests for ``navi_bench.dates.initialize_placeholder_map``.

This function had no prior test coverage even though it is the shared placeholder-date
resolver used by resy, opentable, and google_flights task generation (all via
``initialize_placeholder_map``). Its string-parsed branch used to discard the
already-correct calendar year(s) computed by ``parse_relative_date(s)`` and re-derive a
single shared year for the *entire* resolved list via "normalize every date to
base_date.year, then bump every date to base_date.year + 1 if the earliest one is on or
before base_date". That is the same "hand-rolled year rollover instead of trusting the
already-correct resolution" bug pattern already fixed for December -> January rollovers
elsewhere in this module (#144/#145/#151), but here it corrupted dates outright rather than
crashing:

- A resolved list spanning two different years (e.g. "Dec 27 through Jan 10", which
  legitimately resolves to some dates in year Y and some in Y + 1) got every date's year
  collapsed onto a single re-derived year, producing a self-contradictory span (the
  December dates ended up *after* the January dates).
- Any "this month"/"this <weekday>" style list containing even one date before base_date
  (e.g. "weekends in this month" when today falls after the month's first weekend) got its
  *entire* list -- including the still-upcoming dates -- shoved a full year forward, since
  the bump decision looked only at the earliest date.
- A "last X" resolution that correctly lands in the year before base_date's year got
  silently forced back into base_date's year (undoing the "last" semantics) whenever that
  day-of-year had not yet occurred within base_date's year.

Verified via a parity check against the old reconstruction logic across ~3000 randomized
(description, base_date) pairs: the two implementations agree exactly whenever the resolved
list shares a single year that is either base_date's year or exactly base_date's year + 1
(the common "next codified as a forward bump" case), and diverge -- with the new code
matching the trusted ``parse_relative_date(s)`` output and the old code corrupting it -- on
every year-spanning or backward ("last") case.
"""

from datetime import date, datetime, timezone

import pytest

from navi_bench.base import UserMetadata
from navi_bench.dates import initialize_placeholder_map, render_task_statement, resolve_placeholder_values


def _user_metadata(base_date: date) -> UserMetadata:
    """Build a ``UserMetadata`` whose ``user_metadata_datetime()`` is ``base_date`` at noon UTC."""
    timestamp = int(datetime(base_date.year, base_date.month, base_date.day, 12, tzinfo=timezone.utc).timestamp())
    return UserMetadata(location="San Francisco, CA, United States", timezone="UTC", timestamp=timestamp)


class TestInitializePlaceholderMapYearSpanningRange:
    """A "from <A> through <B>" placeholder resolving across a Dec -> Jan year boundary must
    keep each date's own (already-correct) year rather than collapsing the whole list onto
    a single re-derived year."""

    def test_dates_keep_their_own_years_across_new_years(self):
        placeholder_map, base_date = initialize_placeholder_map(
            _user_metadata(date(2025, 12, 1)),
            {"dateRange": "Sat and Sun from Dec 27 through Jan 10"},
        )
        assert base_date == date(2025, 12, 1)
        _, iso_dates = placeholder_map["dateRange"]
        # December dates stay in 2025; January dates are correctly in 2026. The old code
        # forced every date (including the December ones) onto 2026, making the range run
        # backwards (Dec 2026 after Jan 2026).
        assert iso_dates == ["2025-12-27", "2025-12-28", "2026-01-03", "2026-01-04", "2026-01-10"]
        assert sorted(iso_dates) == iso_dates


class TestInitializePlaceholderMapThisMonthPartiallyPast:
    """A "this month"/"this <weekday>" placeholder must not be pushed a full year forward
    just because some of its dates fall earlier in the month than base_date."""

    def test_weekends_in_this_month_stay_in_current_year(self):
        # base_date (Jun 8, 2026) falls after the month's first weekend (Jun 6-7), so the
        # old "bump if the earliest resolved date is on/before base_date" rule pushed the
        # *entire* list -- including the still-upcoming Jun 13/14/20/21/27/28 weekends -- to
        # June 2027.
        placeholder_map, base_date = initialize_placeholder_map(
            _user_metadata(date(2026, 6, 8)),
            {"weekends": "weekends in this month"},
        )
        assert base_date == date(2026, 6, 8)
        desc, iso_dates = placeholder_map["weekends"]
        assert desc == "weekends in this month"
        assert iso_dates == [
            "2026-06-06",
            "2026-06-07",
            "2026-06-13",
            "2026-06-14",
            "2026-06-20",
            "2026-06-21",
            "2026-06-27",
            "2026-06-28",
        ]


class TestInitializePlaceholderMapLastModifierPriorYear:
    """A "last X" placeholder that correctly resolves into the year before base_date's year
    must not be silently forced back into base_date's year."""

    def test_last_christmas_before_this_years_christmas_stays_in_prior_year(self):
        # base_date (Jul 31, 2025) is before Dec 25, so the correct "last Christmas" is
        # 2024-12-25. The old code re-derived the year as base_date.year (2025) and only
        # bumped forward, never backward, so it produced 2025-12-25 -- a date in the
        # *future* relative to base_date, contradicting "last".
        placeholder_map, base_date = initialize_placeholder_map(
            _user_metadata(date(2025, 7, 31)),
            {"date": "last Christmas"},
        )
        assert base_date == date(2025, 7, 31)
        desc, iso_dates = placeholder_map["date"]
        assert iso_dates == ["2024-12-25"]
        assert desc == "last Christmas, 2024"


class TestInitializePlaceholderMapUnaffectedForwardBumpStillWorks:
    """The common single-date "forward bump into next year" case (the one scenario the old
    code handled correctly) must keep working identically, including the disambiguating
    ", {year}" suffix on the rendered description."""

    def test_single_date_already_passed_this_year_bumps_forward_with_suffix(self):
        placeholder_map, base_date = initialize_placeholder_map(
            _user_metadata(date(2025, 12, 10)),
            {"date": "the 3rd of December"},
        )
        assert base_date == date(2025, 12, 10)
        desc, iso_dates = placeholder_map["date"]
        assert iso_dates == ["2026-12-03"]
        assert desc == "the 3rd of December, 2026"

    def test_single_date_not_yet_passed_this_year_has_no_suffix(self):
        placeholder_map, base_date = initialize_placeholder_map(
            _user_metadata(date(2025, 12, 1)),
            {"date": "the 3rd of December"},
        )
        assert base_date == date(2025, 12, 1)
        desc, iso_dates = placeholder_map["date"]
        assert iso_dates == ["2025-12-03"]
        assert desc == "the 3rd of December"


class TestResolvePlaceholderValuesDynamicOffset:
    """``resolve_placeholder_values`` had zero direct test coverage: ``initialize_placeholder_map``
    only exercises its string-parsed fallback branch (via ``parse_relative_dates``), never the
    ``{now() + timedelta(start, end)}`` dynamic-offset branch that resy/opentable/google_flights
    placeholder configs can also use. Per ``_DYNAMIC_OFFSET_PATTERN``, the ``|option=value`` options
    string comes *after* the closing ``}``, not inside it -- easy to get wrong, which is exactly why
    this branch needs its own direct tests rather than relying on indirect exercise."""

    BASE = date(2026, 3, 10)

    def test_single_day_offset_has_no_prefix(self):
        assert resolve_placeholder_values("{now() + timedelta(0)}", self.BASE) == (
            "Mar 10th",
            ["2026-03-10"],
            True,
        )

    def test_future_single_day_offset_gets_next_prefix(self):
        assert resolve_placeholder_values("{now() + timedelta(1)}", self.BASE) == (
            "next Mar 11th",
            ["2026-03-11"],
            True,
        )

    def test_range_offset_defaults_to_all_dates(self):
        assert resolve_placeholder_values("{now() + timedelta(1, 3)}", self.BASE) == (
            "next Mar 11th-13th",
            ["2026-03-11", "2026-03-12", "2026-03-13"],
            True,
        )

    def test_range_mode_endpoints_only_returns_start_and_end_dates(self):
        assert resolve_placeholder_values("{now() + timedelta(1, 3)}|range=endpoints", self.BASE) == (
            "next Mar 11th-13th",
            ["2026-03-11", "2026-03-13"],
            True,
        )

    def test_month_style_long(self):
        assert resolve_placeholder_values("{now() + timedelta(1, 3)}|month=long", self.BASE) == (
            "next March 11th-13th",
            ["2026-03-11", "2026-03-12", "2026-03-13"],
            True,
        )

    def test_year_style_set_appends_year_after_range(self):
        assert resolve_placeholder_values("{now() + timedelta(1, 3)}|year=set", self.BASE) == (
            "next Mar 11th-13th, 2026",
            ["2026-03-11", "2026-03-12", "2026-03-13"],
            True,
        )

    def test_prefix_none_suppresses_next_prefix(self):
        assert resolve_placeholder_values("{now() + timedelta(1, 3)}|prefix=none", self.BASE) == (
            "Mar 11th-13th",
            ["2026-03-11", "2026-03-12", "2026-03-13"],
            True,
        )

    def test_prefix_auto_adds_next_when_start_offset_is_at_least_one(self):
        assert resolve_placeholder_values("{now() + timedelta(30, 32)}|prefix=auto", self.BASE) == (
            "next Apr 9th-11th",
            ["2026-04-09", "2026-04-10", "2026-04-11"],
            True,
        )

    def test_prefix_auto_omits_next_when_start_offset_is_negative(self):
        assert resolve_placeholder_values("{now() + timedelta(-2, 0)}|prefix=auto", self.BASE) == (
            "Mar 8th-10th",
            ["2026-03-08", "2026-03-09", "2026-03-10"],
            True,
        )

    def test_end_offset_smaller_than_start_offset_raises(self):
        with pytest.raises(ValueError, match="timedelta end offset cannot be smaller than the start offset"):
            resolve_placeholder_values("{now() + timedelta(3, 1)}", self.BASE)

    def test_invalid_option_value_raises(self):
        with pytest.raises(ValueError, match="month style must be one of"):
            resolve_placeholder_values("{now() + timedelta(1)}|month=bogus", self.BASE)

    def test_non_dynamic_text_falls_back_to_string_parsing(self):
        description, iso_dates, is_dynamic = resolve_placeholder_values("the 3rd of December", self.BASE)
        assert description == "the 3rd of December"
        assert iso_dates == ["2026-12-03"]
        assert is_dynamic is False


class TestRenderTaskStatement:
    """``render_task_statement`` had zero test coverage despite being the shared placeholder
    substitution step used by resy, opentable, and google_flights task generation."""

    def test_substitutes_multiple_placeholders(self):
        rendered = render_task_statement(
            "Book a table for {partySize} on {date}",
            {"partySize": ("4", []), "date": ("March 10th", [])},
        )
        assert rendered == "Book a table for 4 on March 10th"

    def test_missing_placeholder_raises(self):
        with pytest.raises(ValueError, match="Placeholder 'missing' not found in resolved_placeholders"):
            render_task_statement("Book for {missing}", {})

    def test_repeated_placeholder_is_substituted_at_every_occurrence(self):
        rendered = render_task_statement("{x} and {x} again", {"x": ("foo", [])})
        assert rendered == "foo and foo again"

    def test_statement_without_placeholders_is_returned_unchanged(self):
        assert render_task_statement("no placeholders here", {}) == "no placeholders here"
