"""Characterization tests for OpenTableInfoGathering's query-matching logic.

These tests pin the CURRENT behavior of ``_check_multi_candidate_query`` and
``_check_single_candidate_query`` (and, transitively, ``_is_exhausted``) before a
structural refactor extracts their shared four-way branch (``"no online availability"``
/ time-range ``"unavailable"`` / plain ``"unavailable"``-or-``"unfortunately"`` /
available). They are not meant to exhaustively spec the domain; they exist so a
refactor of the shared branch logic can be verified as behavior-preserving without
hand-tracing every case from scratch.
"""

from datetime import datetime, timezone

import pytest

from navi_bench.opentable.opentable_info_gathering import (
    DATE_OPTIONS,
    MEAL_TIMES,
    InfoDict,
    MultiCandidateQuery,
    OpenTableInfoGathering,
    SingleCandidateQuery,
    get_days_until_date,
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


# Canonical info-message flavors and the date-range window reused verbatim across the
# multi-candidate/single-candidate/exhaustion tests below, mirroring the four branches
# `_check_multi_candidate_query`/`_check_single_candidate_query` distinguish.
_NO_ONLINE_AVAILABILITY_INFO = "Sorry, there is no online availability within 2 hours of your requested time."
_RANGE_UNAVAILABLE_INFO = "This restaurant is unavailable for online booking during this period."
_RANGE_DATE_WINDOW = {
    "startDate": "2025-07-10",
    "startTime": "18:00:00",
    "endDate": "2025-07-10",
    "endTime": "21:00:00",
}
_PLAIN_UNAVAILABLE_INFO = "Sorry, this time is unavailable."
_UNFORTUNATELY_INFO = "Unfortunately, there are no tables at this time."


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
        info = _info(info=_NO_ONLINE_AVAILABILITY_INFO)
        evidences: list[InfoDict] = []

        result = OpenTableInfoGathering._check_multi_candidate_query(query, info, evidences)

        assert result is False
        assert evidences == [info]

    def test_no_online_availability_window_miss_adds_no_evidence(self):
        # Query's slot (23:00) falls outside the +/- 2 hour window around 19:00.
        query: MultiCandidateQuery = {"dates": ["2025-07-10"], "times": ["23:00:00"]}
        info = _info(info=_NO_ONLINE_AVAILABILITY_INFO)
        evidences: list[InfoDict] = []

        result = OpenTableInfoGathering._check_multi_candidate_query(query, info, evidences)

        assert result is False
        assert evidences == []

    def test_range_unavailable_match_adds_evidence(self):
        query: MultiCandidateQuery = {"dates": ["2025-07-10"], "times": ["19:00:00"]}
        info = _info(info=_RANGE_UNAVAILABLE_INFO, **_RANGE_DATE_WINDOW)
        evidences: list[InfoDict] = []

        result = OpenTableInfoGathering._check_multi_candidate_query(query, info, evidences)

        assert result is False
        assert evidences == [info]

    def test_range_unavailable_miss_adds_no_evidence(self):
        query: MultiCandidateQuery = {"dates": ["2025-07-10"], "times": ["23:00:00"]}
        info = _info(info=_RANGE_UNAVAILABLE_INFO, **_RANGE_DATE_WINDOW)
        evidences: list[InfoDict] = []

        result = OpenTableInfoGathering._check_multi_candidate_query(query, info, evidences)

        assert result is False
        assert evidences == []

    def test_plain_unavailable_match_adds_evidence(self):
        query: MultiCandidateQuery = {"dates": ["2025-07-10"], "times": ["19:00:00"]}
        info = _info(info=_PLAIN_UNAVAILABLE_INFO)
        evidences: list[InfoDict] = []

        result = OpenTableInfoGathering._check_multi_candidate_query(query, info, evidences)

        assert result is False
        assert evidences == [info]

    def test_plain_unfortunately_date_mismatch_adds_no_evidence(self):
        query: MultiCandidateQuery = {"dates": ["2025-07-11"], "times": ["19:00:00"]}
        info = _info(info=_UNFORTUNATELY_INFO)
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
        info = _info(info=_NO_ONLINE_AVAILABILITY_INFO)

        assert OpenTableInfoGathering._check_single_candidate_query(query, info) is True

    def test_no_online_availability_window_miss_is_false(self):
        query: SingleCandidateQuery = {"date": "2025-07-10", "time": "23:00:00"}
        info = _info(info=_NO_ONLINE_AVAILABILITY_INFO)

        assert OpenTableInfoGathering._check_single_candidate_query(query, info) is False

    def test_range_unavailable_match_is_true(self):
        query: SingleCandidateQuery = {"date": "2025-07-10", "time": "19:00:00"}
        info = _info(info=_RANGE_UNAVAILABLE_INFO, **_RANGE_DATE_WINDOW)

        assert OpenTableInfoGathering._check_single_candidate_query(query, info) is True

    def test_range_unavailable_miss_is_false(self):
        query: SingleCandidateQuery = {"date": "2025-07-10", "time": "23:00:00"}
        info = _info(info=_RANGE_UNAVAILABLE_INFO, **_RANGE_DATE_WINDOW)

        assert OpenTableInfoGathering._check_single_candidate_query(query, info) is False

    def test_plain_unavailable_match_is_true(self):
        query: SingleCandidateQuery = {"date": "2025-07-10", "time": "19:00:00"}
        info = _info(info=_PLAIN_UNAVAILABLE_INFO)

        assert OpenTableInfoGathering._check_single_candidate_query(query, info) is True

    def test_plain_unfortunately_mismatch_is_false(self):
        query: SingleCandidateQuery = {"date": "2025-07-11", "time": "19:00:00"}
        info = _info(info=_UNFORTUNATELY_INFO)

        assert OpenTableInfoGathering._check_single_candidate_query(query, info) is False


class TestIsExhausted:
    """Pin `_is_exhausted`'s multi-candidate exhaustion behavior, including the
    evidence-accumulation side effect that only `_check_multi_candidate_query` performs.
    """

    def test_multi_candidate_not_exhausted_when_one_date_uncovered(self):
        query: MultiCandidateQuery = {"dates": ["2025-07-10", "2025-07-11"], "times": ["19:00:00"]}
        # Only the 07-10 slot has "unavailable" evidence; 07-11 was never probed.
        evidence_info = _info(date="2025-07-10", time="19:00:00", info=_PLAIN_UNAVAILABLE_INFO)
        evidences: list[InfoDict] = []
        matched = OpenTableInfoGathering._check_multi_candidate_query(query, evidence_info, evidences)
        assert matched is False
        assert evidences == [evidence_info]

        assert OpenTableInfoGathering._is_exhausted(query, evidences) is False

    def test_multi_candidate_exhausted_when_all_dates_covered(self):
        query: MultiCandidateQuery = {"dates": ["2025-07-10", "2025-07-11"], "times": ["19:00:00"]}
        evidences: list[InfoDict] = []
        for date in ["2025-07-10", "2025-07-11"]:
            info = _info(date=date, time="19:00:00", info=_PLAIN_UNAVAILABLE_INFO)
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
            {"info": _NO_ONLINE_AVAILABILITY_INFO},
            ["2025-07-10"],
            ["20:00:00"],
            True,
        ),
        (
            {"info": _NO_ONLINE_AVAILABILITY_INFO},
            ["2025-07-10"],
            ["23:00:00"],
            False,
        ),
        (
            {"info": "Unavailable during this period.", **_RANGE_DATE_WINDOW},
            ["2025-07-10"],
            ["19:00:00"],
            True,
        ),
        (
            {"info": _PLAIN_UNAVAILABLE_INFO},
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


class TestDateOptions:
    """Pin the exact ``DATE_OPTIONS`` entries, extracted verbatim from the hand-typed literal
    list this held before the per-weekday entries were replaced by a generator over
    ``calendar.day_name``. ``generate_task_config_random`` samples from this list, so a change
    to its entries or ordering would silently change which date phrasings can be sampled.
    """

    def test_date_options_are_unchanged(self):
        assert DATE_OPTIONS == [
            "tomorrow",
            "day after tomorrow",
            "for the upcoming Monday",
            "for the upcoming Tuesday",
            "for the upcoming Wednesday",
            "for the upcoming Thursday",
            "for the upcoming Friday",
            "for the upcoming Saturday",
            "for the upcoming Sunday",
            "upcoming weekend",
            "the following weekend",
            "the next two weekends",
            "the first weekend of the next calendar month",
        ]


class TestGetDaysUntilDateUpcomingWeekday:
    """Pin the "for the upcoming <weekday>" branch of ``get_days_until_date`` ahead of a
    refactor that delegates its next-occurrence math to the shared
    ``relative_dates.days_until_next_weekday`` helper (used identically by
    ``relative_dates.parse_relative_date``'s weekday branch). ``today`` is 2025-11-06, a
    Thursday, chosen so the Thursday case exercises the "target is today" edge (rolls to 7).
    """

    _TODAY = datetime(2025, 11, 6, tzinfo=timezone.utc)  # Thursday

    @pytest.mark.parametrize(
        "weekday_name,expected_days",
        [
            ("Monday", 4),
            ("Tuesday", 5),
            ("Wednesday", 6),
            ("Thursday", 7),  # today's own weekday rolls to next week
            ("Friday", 1),
            ("Saturday", 2),
            ("Sunday", 3),
        ],
    )
    def test_upcoming_weekday_offset(self, weekday_name, expected_days):
        assert get_days_until_date(f"for the upcoming {weekday_name}", self._TODAY) == [expected_days]
