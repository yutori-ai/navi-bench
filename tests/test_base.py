"""Tests for ``all_or_nothing_coverage_result``, extracted from the near-identical
``all(...) -> score -> sum(...) -> FinalResult -> log`` tail that ``ResyUrlMatch.compute()``
and ``GoogleFlightsSearchMatch.compute()`` each used to repeat verbatim (differing only in
the class name baked into the log message). These pin the scoring semantics so the shared
helper can be verified as behavior-preserving for both call sites.
"""

import asyncio

import pytest
from datasets import Value
from pydantic import BaseModel

from navi_bench.base import (
    FinalResult,
    all_or_nothing_coverage_result,
    basic_pydantic_to_hf_features,
    unwrap_optional_type,
)
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


class TestUnwrapOptionalType:
    """Characterization tests for the shared ``Optional[T]``/``T | None`` unwrapping logic,
    extracted from the near-identical duplicate in ``basic_pydantic_to_hf_features`` and
    ``evaluation.cli._build_argparse_kwargs``. Both call sites relied on the same "a union
    with exactly one non-None member is a simple optional" semantics, which this helper
    now centralizes.
    """

    def test_pipe_none_union_unwraps(self):
        assert unwrap_optional_type(int | None) == (int, True)

    def test_optional_typing_alias_unwraps(self):
        from typing import Optional

        assert unwrap_optional_type(Optional[str]) == (str, True)

    def test_plain_type_is_not_optional(self):
        assert unwrap_optional_type(int) == (int, False)

    def test_two_member_non_none_union_is_not_optional(self):
        assert unwrap_optional_type(int | str) == (int | str, False)

    def test_three_member_union_with_none_is_not_optional(self):
        annotation = int | str | None
        assert unwrap_optional_type(annotation) == (annotation, False)


class TestBasicPydanticToHfFeatures:
    def test_basic_types(self):
        class Model(BaseModel):
            a: str
            b: int
            c: float
            d: bool

        features = basic_pydantic_to_hf_features(Model)

        assert features["a"] == Value(dtype="string")
        assert features["b"] == Value(dtype="int64")
        assert features["c"] == Value(dtype="float64")
        assert features["d"] == Value(dtype="bool")

    def test_optional_field_unwraps_to_inner_type(self):
        class Model(BaseModel):
            a: str | None = None

        features = basic_pydantic_to_hf_features(Model)

        assert features["a"] == Value(dtype="string")

    def test_nested_pydantic_model(self):
        class Inner(BaseModel):
            x: int

        class Outer(BaseModel):
            inner: Inner

        features = basic_pydantic_to_hf_features(Outer)

        assert features["inner"]["x"] == Value(dtype="int64")

    def test_non_optional_union_raises(self):
        class Model(BaseModel):
            a: int | str

        with pytest.raises(ValueError, match="Unexpected union type"):
            basic_pydantic_to_hf_features(Model)

    def test_unsupported_type_raises(self):
        class Model(BaseModel):
            a: list[str]

        with pytest.raises(ValueError, match="Unexpected field type"):
            basic_pydantic_to_hf_features(Model)
