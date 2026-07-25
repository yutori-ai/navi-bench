"""Characterization tests for CraigslistUrlMatch.

Unlike every sibling domain matcher (apartments, resy, opentable, google_flights),
``craigslist_url_match.py`` had zero test coverage before this file. These tests pin
``_parse_state``'s ignored-parameter filtering and, more importantly, ``compute()``'s
nested "AND of ORs" coverage logic: every group in ``gt_urls`` (outer list) must be
covered, where a group counts as covered if any one of its alternatives (inner list) is
matched by some visited URL.
"""

import asyncio

from navi_bench.craigslist.craigslist_url_match import CraigslistUrlMatch


def _run(coro):
    return asyncio.run(coro)


class TestParseState:
    def test_query_params_parsed_into_dict_of_lists(self):
        state = CraigslistUrlMatch._parse_state("https://sfbay.craigslist.org/search/apa?min_bedrooms=2&pets_cat=1")

        assert state == {"min_bedrooms": ["2"], "pets_cat": ["1"]}

    def test_ignored_param_is_trusted_is_dropped(self):
        state = CraigslistUrlMatch._parse_state("https://sfbay.craigslist.org/search/apa?isTrusted=true&postal=94043")

        assert state == {"postal": ["94043"]}

    def test_no_query_string_yields_empty_dict(self):
        assert CraigslistUrlMatch._parse_state("https://sfbay.craigslist.org/search/apa") == {}

    def test_fragment_is_not_part_of_query(self):
        state = CraigslistUrlMatch._parse_state(
            "https://sfbay.craigslist.org/search/apa?postal=94043#search=2~gallery~0"
        )

        assert state == {"postal": ["94043"]}


class TestUpdate:
    def test_new_url_is_recorded_in_intermediate_state(self):
        metric = CraigslistUrlMatch(gt_urls=[["https://sfbay.craigslist.org/search/apa?postal=94043"]])

        _run(metric.update(url="https://sfbay.craigslist.org/search/apa?postal=94043"))

        assert metric._intermediate_url_to_state == {
            "https://sfbay.craigslist.org/search/apa?postal=94043": {"postal": ["94043"]}
        }

    def test_repeat_url_is_not_reparsed_or_duplicated(self):
        metric = CraigslistUrlMatch(gt_urls=[["https://sfbay.craigslist.org/search/apa?postal=94043"]])
        url = "https://sfbay.craigslist.org/search/apa?postal=94043"

        _run(metric.update(url=url))
        _run(metric.update(url=url))

        assert list(metric._intermediate_url_to_state.keys()) == [url]

    def test_multiple_distinct_urls_are_all_recorded(self):
        metric = CraigslistUrlMatch(gt_urls=[["https://sfbay.craigslist.org/search/apa?postal=94043"]])

        _run(metric.update(url="https://sfbay.craigslist.org/search/apa?postal=94043"))
        _run(metric.update(url="https://sfbay.craigslist.org/search/apa?postal=10001"))

        assert len(metric._intermediate_url_to_state) == 2


class TestCompute:
    def test_no_urls_visited_scores_zero(self):
        metric = CraigslistUrlMatch(gt_urls=[["https://sfbay.craigslist.org/search/apa?postal=94043"]])

        result = _run(metric.compute())

        assert result.score == 0.0
        assert result.reasoning == "Covered 0 out of 1 required URLs"

    def test_single_group_single_alternative_matched_scores_one(self):
        metric = CraigslistUrlMatch(gt_urls=[["https://sfbay.craigslist.org/search/apa?postal=94043"]])

        _run(metric.update(url="https://sfbay.craigslist.org/search/apa?postal=94043&isTrusted=true"))
        result = _run(metric.compute())

        assert result.score == 1.0
        assert result.reasoning == "Covered 1 out of 1 required URLs"

    def test_or_alternative_within_a_group_counts_as_covered(self):
        # A single required group with two acceptable alternative URLs ("OR" semantics):
        # visiting only the second alternative should still fully cover the group.
        metric = CraigslistUrlMatch(
            gt_urls=[
                [
                    "https://sfbay.craigslist.org/search/apa?postal=94043",
                    "https://sfbay.craigslist.org/search/apa?postal=10001",
                ]
            ]
        )

        _run(metric.update(url="https://sfbay.craigslist.org/search/apa?postal=10001"))
        result = _run(metric.compute())

        assert result.score == 1.0

    def test_all_groups_required_and_partial_coverage_is_fractional(self):
        # Two required ("AND") groups; only the first is covered.
        metric = CraigslistUrlMatch(
            gt_urls=[
                ["https://sfbay.craigslist.org/search/apa?postal=94043"],
                ["https://sfbay.craigslist.org/search/apa?postal=10001"],
            ]
        )

        _run(metric.update(url="https://sfbay.craigslist.org/search/apa?postal=94043"))
        result = _run(metric.compute())

        assert result.score == 0.5
        assert result.reasoning == "Covered 1 out of 2 required URLs"

    def test_single_visited_url_can_cover_two_distinct_groups(self):
        # Same gt state appearing in two different AND groups; one visited URL matching
        # that state should satisfy both groups independently (not consumed after first use).
        metric = CraigslistUrlMatch(
            gt_urls=[
                ["https://sfbay.craigslist.org/search/apa?postal=94043"],
                ["https://sfbay.craigslist.org/search/apa?postal=94043"],
            ]
        )

        _run(metric.update(url="https://sfbay.craigslist.org/search/apa?postal=94043"))
        result = _run(metric.compute())

        assert result.score == 1.0
        assert result.reasoning == "Covered 2 out of 2 required URLs"

    def test_visited_url_with_extra_ignored_param_still_matches(self):
        metric = CraigslistUrlMatch(gt_urls=[["https://sfbay.craigslist.org/search/apa?postal=94043"]])

        _run(metric.update(url="https://sfbay.craigslist.org/search/apa?isTrusted=true&postal=94043"))
        result = _run(metric.compute())

        assert result.score == 1.0

    def test_visited_url_with_different_params_does_not_match(self):
        metric = CraigslistUrlMatch(gt_urls=[["https://sfbay.craigslist.org/search/apa?postal=94043"]])

        _run(metric.update(url="https://sfbay.craigslist.org/search/apa?postal=10001"))
        result = _run(metric.compute())

        assert result.score == 0.0


class TestResetAndRepr:
    def test_reset_clears_intermediate_state(self):
        metric = CraigslistUrlMatch(gt_urls=[["https://sfbay.craigslist.org/search/apa?postal=94043"]])
        _run(metric.update(url="https://sfbay.craigslist.org/search/apa?postal=94043"))
        assert metric._intermediate_url_to_state

        _run(metric.reset())

        assert metric._intermediate_url_to_state == {}

    def test_reset_reverts_compute_to_uncovered(self):
        metric = CraigslistUrlMatch(gt_urls=[["https://sfbay.craigslist.org/search/apa?postal=94043"]])
        _run(metric.update(url="https://sfbay.craigslist.org/search/apa?postal=94043"))
        assert _run(metric.compute()).score == 1.0

        _run(metric.reset())

        assert _run(metric.compute()).score == 0.0

    def test_repr_shows_gt_urls(self):
        gt_urls = [["https://sfbay.craigslist.org/search/apa?postal=94043"]]
        metric = CraigslistUrlMatch(gt_urls=gt_urls)

        assert repr(metric) == f"CraigslistUrlMatch(gt_urls={gt_urls})"
