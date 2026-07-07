"""Characterization tests for ResyUrlMatch's placeholder-rendering helpers.

These tests pin the CURRENT behavior of ``_render_placeholders_in_queries_any`` and
``_render_placeholders_in_queries_all`` before a structural refactor extracts their shared
per-placeholder validation preamble (build the ``{key}`` template string, reject empty
resolved dates via ``ensure_resolved_dates``, reject dates beyond the booking window via
``_ensure_within_booking_window``). ``_any`` additionally requires exactly one resolved date,
checked *between* the other two validations in the original code — a couple of these tests
exist specifically to pin that ordering (which error wins when a placeholder is both
multi-valued and out of the booking window) so the refactor can be verified as
behavior-preserving without hand-tracing every case from scratch.
"""

from datetime import date

import pytest

from navi_bench.resy.resy_url_match import (
    _render_placeholders_in_queries_all,
    _render_placeholders_in_queries_any,
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
