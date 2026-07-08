"""Characterization tests for OpenTableInfoGathering's query-matching logic.

These tests pin the CURRENT behavior of ``_check_multi_candidate_query`` and
``_check_single_candidate_query`` (and, transitively, ``_is_exhausted``) before a
structural refactor extracts their shared four-way branch (``"no online availability"``
/ time-range ``"unavailable"`` / plain ``"unavailable"``-or-``"unfortunately"`` /
available). They are not meant to exhaustively spec the domain; they exist so a
refactor of the shared branch logic can be verified as behavior-preserving without
hand-tracing every case from scratch.
"""

import pytest

from navi_bench.opentable.opentable_info_gathering import (
    MEAL_TIMES,
    InfoDict,
    MultiCandidateQuery,
    OpenTableInfoGathering,
    SingleCandidateQuery,
)


def _info(**kwargs) -> InfoDict:
    base: InfoDict = {
        "url": "https://www.opentable.com/r/example",
        "restaurantName": "Chez TJ",
        "partySize": 2,
        "info": "",
        "date": "2025-07-10",
        "time": "19:00:00",
    }
    base.update(kwargs)
    return base


class TestCheckMultiCandidateQuery:
    """Pin `_check_multi_candidate_query`'s behavior across its four branches."""

    def test_available_branch_returns_true_and_adds_no_evidence(self):
        query: MultiCandidateQuery = {"dates": ["2025-07-10"], "times": ["19:00:00"]}
        info = _info(info="Table for 2 is available.")
        evidences: list[InfoDict] = []

        result = OpenTableInfoGathering._check_multi_candidate_query(query, info, evidences)

        assert result is True
        assert evidences == []

    def test_available_branch_date_mismatch_returns_false_no_evidence(self):
        query: MultiCandidateQuery = {"dates": ["2025-07-11"], "times": ["19:00:00"]}
        info = _info(info="Table for 2 is available.")
        evidences: list[InfoDict] = []

        result = OpenTableInfoGathering._check_multi_candidate_query(query, info, evidences)

        assert result is False
        assert evidences == []

    def test_no_online_availability_window_match_adds_evidence(self):
        # info's requested slot (2025-07-10 19:00) +/- 2 hours covers the query's slot.
        query: MultiCandidateQuery = {"dates": ["2025-07-10"], "times": ["20:00:00"]}
        info = _info(info="Sorry, there is no online availability within 2 hours of your requested time.")
        evidences: list[InfoDict] = []

        result = OpenTableInfoGathering._check_multi_candidate_query(query, info, evidences)

        assert result is False
        assert evidences == [info]

    def test_no_online_availability_window_miss_adds_no_evidence(self):
        # Query's slot (23:00) falls outside the +/- 2 hour window around 19:00.
        query: MultiCandidateQuery = {"dates": ["2025-07-10"], "times": ["23:00:00"]}
        info = _info(info="Sorry, there is no online availability within 2 hours of your requested time.")
        evidences: list[InfoDict] = []

        result = OpenTableInfoGathering._check_multi_candidate_query(query, info, evidences)

        assert result is False
        assert evidences == []

    def test_range_unavailable_match_adds_evidence(self):
        query: MultiCandidateQuery = {"dates": ["2025-07-10"], "times": ["19:00:00"]}
        info = _info(
            info="This restaurant is unavailable for online booking during this period.",
            startDate="2025-07-10",
            startTime="18:00:00",
            endDate="2025-07-10",
            endTime="21:00:00",
        )
        evidences: list[InfoDict] = []

        result = OpenTableInfoGathering._check_multi_candidate_query(query, info, evidences)

        assert result is False
        assert evidences == [info]

    def test_range_unavailable_miss_adds_no_evidence(self):
        query: MultiCandidateQuery = {"dates": ["2025-07-10"], "times": ["23:00:00"]}
        info = _info(
            info="This restaurant is unavailable for online booking during this period.",
            startDate="2025-07-10",
            startTime="18:00:00",
            endDate="2025-07-10",
            endTime="21:00:00",
        )
        evidences: list[InfoDict] = []

        result = OpenTableInfoGathering._check_multi_candidate_query(query, info, evidences)

        assert result is False
        assert evidences == []

    def test_plain_unavailable_match_adds_evidence(self):
        query: MultiCandidateQuery = {"dates": ["2025-07-10"], "times": ["19:00:00"]}
        info = _info(info="Sorry, this time is unavailable.")
        evidences: list[InfoDict] = []

        result = OpenTableInfoGathering._check_multi_candidate_query(query, info, evidences)

        assert result is False
        assert evidences == [info]

    def test_plain_unfortunately_date_mismatch_adds_no_evidence(self):
        query: MultiCandidateQuery = {"dates": ["2025-07-11"], "times": ["19:00:00"]}
        info = _info(info="Unfortunately, there are no tables at this time.")
        evidences: list[InfoDict] = []

        result = OpenTableInfoGathering._check_multi_candidate_query(query, info, evidences)

        assert result is False
        assert evidences == []


class TestCheckSingleCandidateQuery:
    """Pin `_check_single_candidate_query`'s behavior across the same four info flavors."""

    def test_available_branch_match_is_true(self):
        query: SingleCandidateQuery = {"date": "2025-07-10", "time": "19:00:00"}
        info = _info(info="Table for 2 is available.")

        assert OpenTableInfoGathering._check_single_candidate_query(query, info) is True

    def test_available_branch_mismatch_is_false(self):
        query: SingleCandidateQuery = {"date": "2025-07-11", "time": "19:00:00"}
        info = _info(info="Table for 2 is available.")

        assert OpenTableInfoGathering._check_single_candidate_query(query, info) is False

    def test_no_online_availability_window_match_is_true(self):
        query: SingleCandidateQuery = {"date": "2025-07-10", "time": "20:00:00"}
        info = _info(info="Sorry, there is no online availability within 2 hours of your requested time.")

        assert OpenTableInfoGathering._check_single_candidate_query(query, info) is True

    def test_no_online_availability_window_miss_is_false(self):
        query: SingleCandidateQuery = {"date": "2025-07-10", "time": "23:00:00"}
        info = _info(info="Sorry, there is no online availability within 2 hours of your requested time.")

        assert OpenTableInfoGathering._check_single_candidate_query(query, info) is False

    def test_range_unavailable_match_is_true(self):
        query: SingleCandidateQuery = {"date": "2025-07-10", "time": "19:00:00"}
        info = _info(
            info="This restaurant is unavailable for online booking during this period.",
            startDate="2025-07-10",
            startTime="18:00:00",
            endDate="2025-07-10",
            endTime="21:00:00",
        )

        assert OpenTableInfoGathering._check_single_candidate_query(query, info) is True

    def test_range_unavailable_miss_is_false(self):
        query: SingleCandidateQuery = {"date": "2025-07-10", "time": "23:00:00"}
        info = _info(
            info="This restaurant is unavailable for online booking during this period.",
            startDate="2025-07-10",
            startTime="18:00:00",
            endDate="2025-07-10",
            endTime="21:00:00",
        )

        assert OpenTableInfoGathering._check_single_candidate_query(query, info) is False

    def test_plain_unavailable_match_is_true(self):
        query: SingleCandidateQuery = {"date": "2025-07-10", "time": "19:00:00"}
        info = _info(info="Sorry, this time is unavailable.")

        assert OpenTableInfoGathering._check_single_candidate_query(query, info) is True

    def test_plain_unfortunately_mismatch_is_false(self):
        query: SingleCandidateQuery = {"date": "2025-07-11", "time": "19:00:00"}
        info = _info(info="Unfortunately, there are no tables at this time.")

        assert OpenTableInfoGathering._check_single_candidate_query(query, info) is False


class TestIsExhausted:
    """Pin `_is_exhausted`'s multi-candidate exhaustion behavior, including the
    evidence-accumulation side effect that only `_check_multi_candidate_query` performs.
    """

    def test_multi_candidate_not_exhausted_when_one_date_uncovered(self):
        query: MultiCandidateQuery = {"dates": ["2025-07-10", "2025-07-11"], "times": ["19:00:00"]}
        # Only the 07-10 slot has "unavailable" evidence; 07-11 was never probed.
        evidence_info = _info(date="2025-07-10", time="19:00:00", info="Sorry, this time is unavailable.")
        evidences: list[InfoDict] = []
        matched = OpenTableInfoGathering._check_multi_candidate_query(query, evidence_info, evidences)
        assert matched is False
        assert evidences == [evidence_info]

        assert OpenTableInfoGathering._is_exhausted(query, evidences) is False

    def test_multi_candidate_exhausted_when_all_dates_covered(self):
        query: MultiCandidateQuery = {"dates": ["2025-07-10", "2025-07-11"], "times": ["19:00:00"]}
        evidences: list[InfoDict] = []
        for date in ["2025-07-10", "2025-07-11"]:
            info = _info(date=date, time="19:00:00", info="Sorry, this time is unavailable.")
            matched = OpenTableInfoGathering._check_multi_candidate_query(query, info, evidences)
            assert matched is False

        assert len(evidences) == 2
        assert OpenTableInfoGathering._is_exhausted(query, evidences) is True

    def test_multi_candidate_not_exhausted_with_no_evidence(self):
        query: MultiCandidateQuery = {"dates": ["2025-07-10"], "times": ["19:00:00"]}
        assert OpenTableInfoGathering._is_exhausted(query, []) is False


@pytest.mark.parametrize(
    ("info_kwargs", "query_dates", "query_times", "expect_matched"),
    [
        (
            {"info": "Sorry, there is no online availability within 2 hours of your requested time."},
            ["2025-07-10"],
            ["20:00:00"],
            True,
        ),
        (
            {"info": "Sorry, there is no online availability within 2 hours of your requested time."},
            ["2025-07-10"],
            ["23:00:00"],
            False,
        ),
        (
            {
                "info": "Unavailable during this period.",
                "startDate": "2025-07-10",
                "startTime": "18:00:00",
                "endDate": "2025-07-10",
                "endTime": "21:00:00",
            },
            ["2025-07-10"],
            ["19:00:00"],
            True,
        ),
        (
            {"info": "Sorry, this time is unavailable."},
            ["2025-07-10"],
            ["19:00:00"],
            True,
        ),
        (
            {"info": "Table for 2 is available."},
            ["2025-07-10"],
            ["19:00:00"],
            True,
        ),
    ],
)
def test_multi_and_single_candidate_agree_on_match_outcome(info_kwargs, query_dates, query_times, expect_matched):
    """The list-valued and scalar-valued checks should agree on whether a singleton
    query matches, even though only the multi-candidate path records evidence and
    always returns False for the three unavailable-flavored branches.
    """
    info = _info(date="2025-07-10", time="19:00:00", **info_kwargs)
    multi_query: MultiCandidateQuery = {"dates": query_dates, "times": query_times}
    single_query: SingleCandidateQuery = {"date": query_dates[0], "time": query_times[0]}

    evidences: list[InfoDict] = []
    multi_result = OpenTableInfoGathering._check_multi_candidate_query(multi_query, info, evidences)
    single_result = OpenTableInfoGathering._check_single_candidate_query(single_query, info)

    assert single_result is expect_matched
    # Whether evidence was recorded (i.e. the branch is unavailable-flavored and matched)
    # should exactly track `expect_matched` for the unavailable branches, and multi should
    # return True only for the "available" branch.
    is_available_branch = "available" == info["info"].lower() or (
        "unavailable" not in info["info"].lower()
        and "unfortunately" not in info["info"].lower()
        and "no online availability" not in info["info"].lower()
    )
    if is_available_branch:
        assert multi_result is expect_matched
        assert evidences == []
    else:
        assert multi_result is False
        assert evidences == ([info] if expect_matched else [])


class TestMealTimes:
    """Pin the exact quarter-hour time slots for each meal, extracted verbatim from the
    hand-written literal lists ``MEAL_TIMES`` used to hold before they were replaced by a
    generated ``_quarter_hour_times`` helper. ``generate_task_config_random`` selects one of
    these lists at random, so a change to the boundaries or step size would silently change
    which times can be sampled.
    """

    def test_breakfast_is_6am_to_10am_inclusive(self):
        assert MEAL_TIMES["breakfast"]["times"] == [
            "06:00:00", "06:15:00", "06:30:00", "06:45:00", "07:00:00", "07:15:00", "07:30:00", "07:45:00",
            "08:00:00", "08:15:00", "08:30:00", "08:45:00", "09:00:00", "09:15:00", "09:30:00", "09:45:00",
            "10:00:00",
        ]  # fmt: skip

    def test_brunch_is_10am_to_2pm_inclusive(self):
        assert MEAL_TIMES["brunch"]["times"] == [
            "10:00:00", "10:15:00", "10:30:00", "10:45:00", "11:00:00", "11:15:00", "11:30:00", "11:45:00",
            "12:00:00", "12:15:00", "12:30:00", "12:45:00", "13:00:00", "13:15:00", "13:30:00", "13:45:00",
            "14:00:00",
        ]  # fmt: skip

    def test_lunch_is_noon_to_2pm_inclusive(self):
        assert MEAL_TIMES["lunch"]["times"] == [
            "12:00:00", "12:15:00", "12:30:00", "12:45:00",
            "13:00:00", "13:15:00", "13:30:00", "13:45:00", "14:00:00",
        ]  # fmt: skip

    def test_dinner_is_5pm_to_8pm_inclusive(self):
        assert MEAL_TIMES["dinner"]["times"] == [
            "17:00:00", "17:15:00", "17:30:00", "17:45:00", "18:00:00", "18:15:00", "18:30:00", "18:45:00",
            "19:00:00", "19:15:00", "19:30:00", "19:45:00", "20:00:00",
        ]  # fmt: skip
