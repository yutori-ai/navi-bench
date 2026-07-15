"""Characterization tests for ResyUrlMatch's placeholder-rendering helpers.

These tests pin the behavior of ``_render_placeholders_in_queries_any`` and
``_render_placeholders_in_queries_all``, including their shared per-placeholder validation
preamble (build the ``{key}`` template string, reject empty resolved dates via
``ensure_resolved_dates``, reject dates beyond the booking window via
``_ensure_within_booking_window``) extracted into ``_validate_placeholder_dates_and_get_template_string``.
``_any`` additionally requires exactly one resolved date, checked *between* the other two
validations — a couple of these tests exist specifically to pin that ordering (which error
wins when a placeholder is both multi-valued and out of the booking window).
"""

from datetime import date

import pytest

from navi_bench.resy.resy_url_match import (
    RESTAURANT_METADATA,
    ResyQueryState,
    ResyUrlMatch,
    _parse_optional_int,
    _render_placeholders_in_queries_all,
    _render_placeholders_in_queries_any,
    parse_time_to_hour,
)


BASE_DATE = date(2025, 7, 10)


class TestRenderPlaceholdersInQueriesAny:
    def test_single_date_substitution(self):
        queries = [["https://resy.com/cities/sf/venues/foo?date={date}&seats=2"]]
        resolved = {"date": ("July 15, 2025", ["2025-07-15"])}

        result = _render_placeholders_in_queries_any(queries, resolved, BASE_DATE, None)

        assert result == [["https://resy.com/cities/sf/venues/foo?date=2025-07-15&seats=2"]]

    def test_empty_dates_raises_no_future_dates_error(self):
        queries = [["https://resy.com/cities/sf/venues/foo?date={date}&seats=2"]]
        resolved = {"date": ("some description", [])}

        with pytest.raises(ValueError, match="No future dates resolved for placeholder 'date'"):
            _render_placeholders_in_queries_any(queries, resolved, BASE_DATE, None)

    def test_multiple_dates_raises_single_date_expected_error(self):
        queries = [["https://resy.com/cities/sf/venues/foo?date={date}&seats=2"]]
        resolved = {"date": ("a range", ["2025-07-15", "2025-07-16"])}

        with pytest.raises(ValueError, match="expects descriptions resolving to a single date"):
            _render_placeholders_in_queries_any(queries, resolved, BASE_DATE, None)

    def test_date_beyond_booking_window_raises(self):
        queries = [["https://resy.com/cities/sf/venues/foo?date={date}&seats=2"]]
        resolved = {"date": ("August 15, 2025", ["2025-08-15"])}

        with pytest.raises(ValueError, match="beyond the booking window"):
            _render_placeholders_in_queries_any(queries, resolved, BASE_DATE, 10)

    def test_date_within_booking_window_passes(self):
        queries = [["https://resy.com/cities/sf/venues/foo?date={date}&seats=2"]]
        resolved = {"date": ("July 15, 2025", ["2025-07-15"])}

        result = _render_placeholders_in_queries_any(queries, resolved, BASE_DATE, 10)

        assert result == [["https://resy.com/cities/sf/venues/foo?date=2025-07-15&seats=2"]]

    def test_multi_date_and_out_of_window_raises_single_date_error_not_window_error(self):
        """Pins ordering: the single-date check fires before the booking-window check."""
        queries = [["https://resy.com/cities/sf/venues/foo?date={date}&seats=2"]]
        resolved = {"date": ("a range far out", ["2025-08-20", "2025-08-21"])}

        with pytest.raises(ValueError, match="expects descriptions resolving to a single date"):
            _render_placeholders_in_queries_any(queries, resolved, BASE_DATE, 5)


class TestRenderPlaceholdersInQueriesAll:
    def test_multi_date_expansion(self):
        template_query = [["https://resy.com/cities/ny/venues/bar?date={dateRange}&seats=13"]]
        resolved = {"dateRange": ("Dec 5-6", ["2025-07-15", "2025-07-16"])}

        result = _render_placeholders_in_queries_all(template_query, resolved, BASE_DATE, None)

        assert result == [
            ["https://resy.com/cities/ny/venues/bar?date=2025-07-15&seats=13"],
            ["https://resy.com/cities/ny/venues/bar?date=2025-07-16&seats=13"],
        ]

    def test_empty_dates_raises_no_future_dates_error(self):
        template_query = [["https://resy.com/cities/ny/venues/bar?date={dateRange}&seats=13"]]
        resolved = {"dateRange": ("some description", [])}

        with pytest.raises(ValueError, match="No future dates resolved for placeholder 'dateRange'"):
            _render_placeholders_in_queries_all(template_query, resolved, BASE_DATE, None)

    def test_date_beyond_booking_window_raises(self):
        template_query = [["https://resy.com/cities/ny/venues/bar?date={dateRange}&seats=13"]]
        resolved = {"dateRange": ("far out", ["2025-07-15", "2025-08-20"])}

        with pytest.raises(ValueError, match="beyond the booking window"):
            _render_placeholders_in_queries_all(template_query, resolved, BASE_DATE, 10)

    def test_dates_within_booking_window_pass(self):
        template_query = [["https://resy.com/cities/ny/venues/bar?date={dateRange}&seats=13"]]
        resolved = {"dateRange": ("close in", ["2025-07-15", "2025-07-16"])}

        result = _render_placeholders_in_queries_all(template_query, resolved, BASE_DATE, 10)

        assert result == [
            ["https://resy.com/cities/ny/venues/bar?date=2025-07-15&seats=13"],
            ["https://resy.com/cities/ny/venues/bar?date=2025-07-16&seats=13"],
        ]


class TestDescribeConditionalReason:
    """Characterization tests for ``ResyUrlMatch._describe_conditional_reason``.

    Pins current behavior, including the reason->description lookup extracted to the
    module-level ``_CONDITIONAL_REASON_DESCRIPTIONS`` constant alongside this file's other
    module-level dicts (``CITY_METADATA``, ``VENUE_SLUG_MAPPING``).
    """

    def _matcher(self) -> ResyUrlMatch:
        return ResyUrlMatch(queries=[["https://resy.com/cities/ny/venues/bar"]])

    def _state(self, gt_time: str | None = "1900") -> ResyQueryState:
        return ResyQueryState(
            group_index=0,
            alt_index=0,
            gt_url="https://resy.com/cities/ny/venues/bar",
            base_without_time="resy.com/cities/ny/venues/bar",
            gt_time=gt_time,
        )

    def test_mapped_reasons(self):
        matcher = self._matcher()
        state = self._state()
        expected = {
            "gt_time_in_url": "available slot matched by URL parameter",
            "gt_time_visible": "available slot visible on page",
            "neighbor_times_seen": "unavailable slot inferred from visible neighboring times",
            "boundary_previous_seen_via_next": "unavailable slot inferred before earliest visible availability",
            "boundary_next_seen_via_prev": "unavailable slot inferred after latest visible availability",
            "gt_time_outside_available_range": "unavailable slot outside listed availability range",
            "no_available_slots": "unavailable slot inferred because page lists no availability",
        }
        for reason, description in expected.items():
            result = matcher._describe_conditional_reason(
                reason=reason, state=state, url_time="1930", has_availabilities=True
            )
            assert result == description

    def test_gt_time_missing(self):
        matcher = self._matcher()
        result = matcher._describe_conditional_reason(
            reason="gt_time_missing", state=self._state(), url_time="1930", has_availabilities=True
        )
        assert result == "ground-truth time missing from configuration"

    def test_gt_time_available_not_seen(self):
        matcher = self._matcher()
        result = matcher._describe_conditional_reason(
            reason="gt_time_available_not_seen", state=self._state(), url_time="1930", has_availabilities=True
        )
        assert result == "available slot exists but was not observed"

    def test_no_slots_but_wrong_time(self):
        matcher = self._matcher()
        result = matcher._describe_conditional_reason(
            reason="no_slots_but_wrong_time", state=self._state(), url_time="1930", has_availabilities=False
        )
        assert result == "URL time does not match ground truth and no availability data to verify"

    def test_neighbors_not_seen_includes_missing_suffix(self):
        matcher = self._matcher()
        result = matcher._describe_conditional_reason(
            reason="neighbors_not_seen:1830,1930", state=self._state(), url_time="1930", has_availabilities=True
        )
        assert result == "unavailable slot needs adjacent times to be visible (missing neighbors: 1830,1930)"

    def test_boundary_previous_not_seen_includes_missing_suffix(self):
        matcher = self._matcher()
        result = matcher._describe_conditional_reason(
            reason="boundary_previous_not_seen:1830", state=self._state(), url_time="1930", has_availabilities=True
        )
        assert result == (
            "unavailable slot earlier than visible range requires earliest time to be visible (missing: 1830)"
        )

    def test_boundary_next_not_seen_includes_missing_suffix(self):
        matcher = self._matcher()
        result = matcher._describe_conditional_reason(
            reason="boundary_next_not_seen:1930", state=self._state(), url_time="1930", has_availabilities=True
        )
        assert result == (
            "unavailable slot later than visible range requires latest time to be visible (missing: 1930)"
        )

    def test_unknown_reason_falls_back_to_generic_description_with_availabilities(self):
        matcher = self._matcher()
        result = matcher._describe_conditional_reason(
            reason="some_unknown_reason", state=self._state("1900"), url_time="1930", has_availabilities=True
        )
        assert result == (
            "conditional coverage reason=some_unknown_reason (with availabilities; gt_time=1900; url_time=1930)"
        )

    def test_unknown_reason_falls_back_to_generic_description_without_availabilities(self):
        matcher = self._matcher()
        result = matcher._describe_conditional_reason(
            reason="some_unknown_reason", state=self._state("1900"), url_time=None, has_availabilities=False
        )
        assert result == (
            "conditional coverage reason=some_unknown_reason "
            "(with no availabilities listed; gt_time=1900; url_time=None)"
        )


class TestParseTimeToHour:
    """Characterization tests for ``parse_time_to_hour``, which was refactored from a
    hand-rolled AM/PM split to ``datetime.strptime(..., "%I:%M %p")``.
    """

    @pytest.mark.parametrize(
        ("time_str", "expected"),
        [
            ("6:00 AM", 6.0),
            ("11:30 PM", 23.5),
            ("12:00 PM", 12.0),
            ("12:00 AM", 0.0),
            ("2:00 AM", 2.0),
            ("9:30 PM", 21.5),
            ("  6:00 am  ", 6.0),
            (None, None),
            ("", None),
            ("   ", None),
            ("not a time", None),
        ],
    )
    def test_parses_expected_values(self, time_str, expected):
        assert parse_time_to_hour(time_str) == expected


class TestParseOptionalInt:
    """Characterization tests for ``_parse_optional_int``, extracted from the ``Guests
    Min``/``Guests Max``/``Days Ahead`` columns in ``load_restaurant_metadata``, which each
    previously repeated ``int(value) if value else None`` inline.
    """

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("1", 1),
            ("28", 28),
            ("0", 0),
            ("", None),
        ],
    )
    def test_parses_expected_values(self, value, expected):
        assert _parse_optional_int(value) == expected


class TestLoadRestaurantMetadata:
    """Sanity check that the bundled ``resy_restaurant.csv`` is still parsed with the
    expected int types after routing through ``_parse_optional_int``."""

    def test_known_row_has_correct_types(self):
        entry = RESTAURANT_METADATA[("new york", "carbone")]
        assert entry["guests_min"] == 1
        assert entry["guests_max"] == 15
        assert entry["days_ahead"] == 28
        assert isinstance(entry["guests_min"], int)
