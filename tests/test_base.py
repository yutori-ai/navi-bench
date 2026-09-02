"""Tests for ``all_or_nothing_coverage_result``, extracted from the near-identical
``all(...) -> score -> sum(...) -> FinalResult -> log`` tail that ``ResyUrlMatch.compute()``
and ``GoogleFlightsSearchMatch.compute()`` each used to repeat verbatim (differing only in
the class name baked into the log message). These pin the scoring semantics so the shared
helper can be verified as behavior-preserving for both call sites.
"""

import pytest
from conftest import run_async as _run
from datasets import Value
from pydantic import BaseModel, ValidationError

from navi_bench.base import (
    DatasetItem,
    FinalResult,
    _HF_FEATURE_DTYPES,
    all_or_nothing_coverage_result,
    basic_pydantic_to_hf_features,
    find_equal_value_entry,
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

        result = _run(metric.compute())

        assert result.score == 1.0

    def test_uncovered_query_scores_zero(self):
        metric = ResyUrlMatch(queries=[["https://resy.com/cities/sf/venues/foo?date=2025-07-15&seats=2"]])

        result = _run(metric.compute())

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

        result = _run(metric.compute())

        assert result.score == 0.0

    def test_matching_flight_info_scores_one(self):
        metric = GoogleFlightsSearchMatch(gt_info=self._GT_INFO)
        # Directly populate the covered-URL map with the exact base Info the ground truth
        # resolves to, mirroring what `update()` would store after decoding a matching URL.
        metric._url_to_flight_info["https://www.google.com/travel/flights?tfs=fake"] = metric._gt_base_info[0]

        result = _run(metric.compute())

        assert result.score == 1.0


class TestFindEqualValueEntry:
    """Characterization tests for the shared "scan a dict for the first value equal to a
    target, returning its (key, value) pair" lookup, extracted from the near-identical loop
    ``CraigslistUrlMatch.compute()`` and ``GoogleFlightsSearchMatch.compute()`` each hand-rolled
    (they also need the matching key for their log messages, unlike a plain
    ``target in observed.values()`` membership check).
    """

    def test_returns_first_matching_key_value_pair(self):
        observed = {"a": 1, "b": 2, "c": 2}

        assert find_equal_value_entry(observed, 2) == ("b", 2)

    def test_returns_none_when_no_value_matches(self):
        observed = {"a": 1, "b": 2}

        assert find_equal_value_entry(observed, 3) is None

    def test_empty_dict_returns_none(self):
        assert find_equal_value_entry({}, "anything") is None


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


class TestHfFeatureDtypesTable:
    """Pins the ``_HF_FEATURE_DTYPES`` dispatch table ``basic_pydantic_to_hf_features`` looks up,
    which replaced a ``field_type is <type>`` elif chain. ``bool`` is a subclass of ``int``, but
    the table is keyed and looked up by exact type identity, not ``isinstance``, so this doesn't
    change which dtype a ``bool``-typed field resolves to.
    """

    def test_covers_exactly_the_four_basic_types(self):
        assert _HF_FEATURE_DTYPES == {str: "string", int: "int64", float: "float64", bool: "bool"}

    def test_bool_key_resolves_to_bool_not_int(self):
        assert _HF_FEATURE_DTYPES[bool] == "bool"


def _valid_dataset_item_kwargs(**overrides) -> dict:
    kwargs = dict(
        task_id="some_dataset/some_domain/1",
        task_generation_config_json="{}",
        env="real",
        domain="expedia",
        l1_category="food",
    )
    kwargs.update(overrides)
    return kwargs


class TestDatasetItemEnumPatterns:
    """DatasetItem's enum-like fields (``env``, ``l1_category``, ``suggested_difficulty``,
    ``suggested_split``) use a regex ``pattern=`` to restrict values to a fixed set of
    options. Each pattern used an unanchored alternation, e.g. ``r"^real|sim$"``, which
    Python's ``re`` parses as ``(^real)|(sim$)`` rather than ``^(?:real|sim)$`` -- so any
    string merely *starting with* "real" or *ending with* "sim" (not just the exact
    strings) passed validation. These tests pin that invalid values are now rejected while
    every legitimate option still validates.
    """

    def test_env_rejects_value_containing_but_not_equal_to_option(self):
        with pytest.raises(ValidationError):
            DatasetItem(**_valid_dataset_item_kwargs(env="realestate"))

    def test_env_accepts_legitimate_values(self):
        for value in ("real", "sim"):
            DatasetItem(**_valid_dataset_item_kwargs(env=value))

    def test_l1_category_rejects_value_containing_but_not_equal_to_option(self):
        with pytest.raises(ValidationError):
            DatasetItem(**_valid_dataset_item_kwargs(l1_category="xxxfoodxxx"))

    def test_l1_category_accepts_legitimate_values(self):
        for value in ("realestate", "food", "e_commerce", "social", "travel"):
            DatasetItem(**_valid_dataset_item_kwargs(l1_category=value))

    def test_suggested_difficulty_rejects_value_containing_but_not_equal_to_option(self):
        with pytest.raises(ValidationError):
            DatasetItem(**_valid_dataset_item_kwargs(suggested_difficulty="mediumish"))

    def test_suggested_difficulty_accepts_legitimate_values(self):
        for value in ("easy", "medium", "hard"):
            DatasetItem(**_valid_dataset_item_kwargs(suggested_difficulty=value))

    def test_suggested_split_rejects_value_containing_but_not_equal_to_option(self):
        with pytest.raises(ValidationError):
            DatasetItem(**_valid_dataset_item_kwargs(suggested_split="train2"))

    def test_suggested_split_accepts_legitimate_values(self):
        for value in ("train", "validation", "test"):
            DatasetItem(**_valid_dataset_item_kwargs(suggested_split=value))
