"""Tests for ``all_or_nothing_coverage_result``, extracted from the near-identical
``all(...) -> score -> sum(...) -> FinalResult -> log`` tail that ``ResyUrlMatch.compute()``
and ``GoogleFlightsSearchMatch.compute()`` each used to repeat verbatim (differing only in
the class name baked into the log message). These pin the scoring semantics so the shared
helper can be verified as behavior-preserving for both call sites.
"""

import asyncio

from navi_bench.base import FinalResult, all_or_nothing_coverage_result
from navi_bench.google_flights.google_flights_search_match import GoogleFlightsSearchMatch
from navi_bench.resy.resy_url_match import ResyUrlMatch


class TestAllOrNothingCoverageResult:
    def test_all_covered_scores_one(self):
        result = all_or_nothing_coverage_result("SomeMatcher", [True, True, True])

        assert result == FinalResult(score=1.0)

    def test_any_uncovered_scores_zero(self):
        result = all_or_nothing_coverage_result("SomeMatcher", [True, False, True])

        assert result == FinalResult(score=0.0)

    def test_empty_list_scores_one(self):
        # vacuously true, mirroring Python's builtin all([]) == True
        result = all_or_nothing_coverage_result("SomeMatcher", [])

        assert result == FinalResult(score=1.0)

    def test_all_uncovered_scores_zero(self):
        result = all_or_nothing_coverage_result("SomeMatcher", [False, False])

        assert result == FinalResult(score=0.0)


class TestResyUrlMatchComputeUsesSharedHelper:
    def test_all_queries_covered_scores_one(self):
        metric = ResyUrlMatch(queries=[["https://resy.com/cities/sf/venues/foo?date=2025-07-15&seats=2"]])
        metric._is_query_covered = [True]

        result = asyncio.run(metric.compute())

        assert result.score == 1.0

    def test_uncovered_query_scores_zero(self):
        metric = ResyUrlMatch(queries=[["https://resy.com/cities/sf/venues/foo?date=2025-07-15&seats=2"]])

        result = asyncio.run(metric.compute())

        assert result.score == 0.0


class TestGoogleFlightsSearchMatchComputeUsesSharedHelper:
    _GT_INFO = [
        {
            "segments": [{"from": "SFO", "to": "MSP", "date": "2025-12-27", "max_stops": 0}],
            "passengers": ["ADULT"],
            "seat": "ECONOMY",
            "trip": "ONE_WAY",
        }
    ]

    def test_no_matching_url_scores_zero(self):
        metric = GoogleFlightsSearchMatch(gt_info=self._GT_INFO)

        result = asyncio.run(metric.compute())

        assert result.score == 0.0

    def test_matching_flight_info_scores_one(self):
        metric = GoogleFlightsSearchMatch(gt_info=self._GT_INFO)
        # Directly populate the covered-URL map with the exact base Info the ground truth
        # resolves to, mirroring what `update()` would store after decoding a matching URL.
        metric._url_to_flight_info["https://www.google.com/travel/flights?tfs=fake"] = metric._gt_base_info[0]

        result = asyncio.run(metric.compute())

        assert result.score == 1.0
