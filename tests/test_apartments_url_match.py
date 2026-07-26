"""Characterization tests for ApartmentsUrlMatch.

The ``_normalize_url`` tests pin the behavior of URL normalization (location
extraction/merging, apartment-feature reordering, and ignored-parameter stripping),
including the ``_STATE_ABBREVIATIONS``/``_APARTMENT_FEATURES`` module-level constants
that ``_is_location_part``/``_normalize_apartment_features`` read from.

The ``update``/``compute`` tests below pin the matcher's core lifecycle -- unlike its
sibling domain matchers (craigslist, google_flights, opentable), this file previously had
no direct test coverage of ``update``/``compute`` at all, only of the ``_normalize_url``
helper they call internally.
"""

import asyncio

from navi_bench.apartments.apartments_url_match import ApartmentsUrlMatch


def _run(coro):
    return asyncio.run(coro)


def _normalize(url: str) -> str:
    return ApartmentsUrlMatch(gt_url="https://www.apartments.com/placeholder")._normalize_url(url)


class TestNormalizeUrl:
    def test_single_path_location_with_query_locations_merged_and_sorted(self):
        url = (
            "https://www.apartments.com/hudson-yards-new-york-ny/2-to-3-bedrooms-2-bathrooms-under-7300/"
            "?n=midtown-west_new-york_ny+hell%27s-kitchen_new-york_ny"
        )
        result = _normalize(url)
        assert result == (
            "apartments.com/hell's-kitchen-new-york-ny/2-to-3-bedrooms-2-bathrooms-under-7300"
            "?n=hudson-yards-new-york-ny%2Bmidtown-west-new-york-ny"
        )

    def test_non_location_path_segment_preserved(self):
        url = (
            "https://www.apartments.com/apartments/hudson-yards-new-york-ny/2-to-3-bedrooms-2-bathrooms-under-7300/"
            "?n=midtown-west_new-york_ny"
        )
        result = _normalize(url)
        assert result == (
            "apartments.com/hudson-yards-new-york-ny/apartments/2-to-3-bedrooms-2-bathrooms-under-7300"
            "?n=midtown-west-new-york-ny"
        )

    def test_apartment_features_left_alone_when_only_one_recognized_present_alongside_unknown_words(self):
        url = "https://www.apartments.com/san-francisco-ca/pet-friendly-air-conditioning-dishwasher/"
        result = _normalize(url)
        assert result == "apartments.com/san-francisco-ca/pet-friendly-air-conditioning-dishwasher"

    def test_apartment_features_sorted_alphabetically_when_multiple_present(self):
        url = "https://www.apartments.com/austin-tx/walk-in-closets-washer_dryer-hookup-laundry-facilities/"
        result = _normalize(url)
        assert result == "apartments.com/austin-tx/laundry-facilities-walk-in-closets-washer_dryer-hookup"

    def test_bb_param_ignored_and_io_ss_params_dropped(self):
        url = "https://www.apartments.com/austin-tx/?bb=1,2,3,4&io=true&ss=1"
        result = _normalize(url)
        assert result == "apartments.com/austin-tx"

    def test_off_domain_url_falls_back_to_basic_normalization(self):
        url = "https://www.example.com/some/path?x=1"
        result = _normalize(url)
        assert result == "example.com/some/path?x=1"

    def test_empty_url_returns_empty_string(self):
        assert _normalize("") == ""

    def test_bare_domain_url(self):
        assert _normalize("https://www.apartments.com/") == "apartments.com"


class TestInit:
    def test_single_string_gt_url_is_wrapped_in_a_list(self):
        metric = ApartmentsUrlMatch(gt_url="https://www.apartments.com/austin-tx")

        assert metric.gt_urls == ["https://www.apartments.com/austin-tx"]

    def test_list_gt_url_is_stored_as_is(self):
        urls = ["https://www.apartments.com/austin-tx", "https://www.apartments.com/dallas-tx"]

        metric = ApartmentsUrlMatch(gt_url=urls)

        assert metric.gt_urls == urls


class TestUpdateAndCompute:
    def test_matching_url_sets_found_match_and_scores_one(self):
        metric = ApartmentsUrlMatch(gt_url="https://www.apartments.com/austin-tx")

        _run(metric.update(url="https://www.apartments.com/austin-tx"))

        assert metric._found_match is True
        assert _run(metric.compute()).score == 1.0

    def test_non_matching_url_leaves_found_match_false_and_scores_zero(self):
        metric = ApartmentsUrlMatch(gt_url="https://www.apartments.com/austin-tx")

        _run(metric.update(url="https://www.apartments.com/dallas-tx"))

        assert metric._found_match is False
        assert _run(metric.compute()).score == 0.0

    def test_no_url_before_any_update_scores_zero(self):
        metric = ApartmentsUrlMatch(gt_url="https://www.apartments.com/austin-tx")

        assert _run(metric.compute()).score == 0.0

    def test_matches_any_url_in_a_list_of_gt_urls(self):
        # `_normalize_url` reorders locations, so the differently-ordered `n=` query
        # below still matches this second gt_url after normalization.
        metric = ApartmentsUrlMatch(
            gt_url=[
                "https://www.apartments.com/hudson-yards-new-york-ny",
                "https://www.apartments.com/austin-tx/?n=dallas-tx",
            ]
        )

        _run(metric.update(url="https://www.apartments.com/austin-tx/?n=dallas-tx"))

        assert metric._found_match is True
        assert _run(metric.compute()).score == 1.0

    def test_once_matched_a_later_non_matching_update_does_not_clear_the_match(self):
        metric = ApartmentsUrlMatch(gt_url="https://www.apartments.com/austin-tx")

        _run(metric.update(url="https://www.apartments.com/austin-tx"))
        _run(metric.update(url="https://www.apartments.com/dallas-tx"))

        assert metric._found_match is True
        assert _run(metric.compute()).score == 1.0

    def test_reset_reverts_compute_to_uncovered(self):
        metric = ApartmentsUrlMatch(gt_url="https://www.apartments.com/austin-tx")
        _run(metric.update(url="https://www.apartments.com/austin-tx"))
        assert _run(metric.compute()).score == 1.0

        _run(metric.reset())

        assert _run(metric.compute()).score == 0.0
