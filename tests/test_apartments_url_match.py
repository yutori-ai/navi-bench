"""Characterization tests for ApartmentsUrlMatch._normalize_url.

These tests pin the behavior of URL normalization (location extraction/merging,
apartment-feature reordering, and ignored-parameter stripping), including the
``_STATE_ABBREVIATIONS``/``_APARTMENT_FEATURES`` module-level constants that
``_is_location_part``/``_normalize_apartment_features`` read from.
"""

from navi_bench.apartments.apartments_url_match import ApartmentsUrlMatch


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
