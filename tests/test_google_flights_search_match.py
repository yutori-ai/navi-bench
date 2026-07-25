"""Characterization tests for GoogleFlightsSearchMatch.

Unlike apartments/resy/opentable/craigslist, ``google_flights_search_match.py`` has no
dedicated test file -- ``tests/test_base.py`` only exercises ``compute()`` incidentally,
as a call site of the shared ``all_or_nothing_coverage_result`` helper, with the metric's
``_url_to_flight_info`` populated directly rather than through ``update()``. These tests
pin the module's own logic: ``_decode_google_flights_url``'s ``tfs``-param base64/protobuf
decoding and its guard clauses, ``_create_base_info``'s dict-to-``Info`` construction,
``update()``'s dedup-by-url and invalid-URL-is-ignored behavior, ``resolve_date_references``'
direct/indexed placeholder substitution, and ``generate_task_config``'s end-to-end wiring.
"""

import asyncio
import base64

import pytest

from navi_bench.google_flights import google_flights_pb2 as gf
from navi_bench.google_flights.google_flights_pb2 import Info
from navi_bench.google_flights.google_flights_search_match import (
    GoogleFlightsSearchMatch,
    resolve_date_references,
)


def _run(coro):
    return asyncio.run(coro)


def _encode_tfs(info: Info) -> str:
    """Inverse of ``_decode_google_flights_url``'s base64/protobuf decoding."""
    return base64.urlsafe_b64encode(info.SerializeToString()).decode().rstrip("=")


_ONE_WAY_GT_INFO = [
    {
        "segments": [{"from": "SFO", "to": "MSP", "date": "2025-12-27", "max_stops": 0}],
        "passengers": ["ADULT"],
        "seat": "ECONOMY",
        "trip": "ONE_WAY",
    }
]


class TestCreateBaseInfo:
    def test_builds_info_from_single_segment(self):
        info = GoogleFlightsSearchMatch._create_base_info(_ONE_WAY_GT_INFO[0])

        assert len(info.data) == 1
        assert info.data[0].date == "2025-12-27"
        assert info.data[0].max_stops == 0
        assert info.data[0].from_flight.airport == "SFO"
        assert info.data[0].to_flight.airport == "MSP"
        assert list(info.passengers) == [gf.ADULT]
        assert info.seat == gf.ECONOMY
        assert info.trip == gf.ONE_WAY

    def test_max_stops_omitted_when_not_in_segment(self):
        gt_info = {
            "segments": [{"from": "SFO", "to": "MSP", "date": "2025-12-27"}],
            "passengers": ["ADULT"],
            "seat": "ECONOMY",
            "trip": "ONE_WAY",
        }

        info = GoogleFlightsSearchMatch._create_base_info(gt_info)

        assert not info.data[0].HasField("max_stops")

    def test_multi_segment_and_multi_passenger_round_trip(self):
        gt_info = {
            "segments": [
                {"from": "SFO", "to": "MSP", "date": "2025-12-27"},
                {"from": "MSP", "to": "SFO", "date": "2025-12-30"},
            ],
            "passengers": ["ADULT", "CHILD"],
            "seat": "PREMIUM_ECONOMY",
            "trip": "ROUND_TRIP",
        }

        info = GoogleFlightsSearchMatch._create_base_info(gt_info)

        assert [d.date for d in info.data] == ["2025-12-27", "2025-12-30"]
        assert list(info.passengers) == [gf.ADULT, gf.CHILD]
        assert info.seat == gf.PREMIUM_ECONOMY
        assert info.trip == gf.ROUND_TRIP


class TestDecodeGoogleFlightsUrl:
    def test_valid_tfs_param_round_trips(self):
        info = GoogleFlightsSearchMatch._create_base_info(_ONE_WAY_GT_INFO[0])
        url = f"https://www.google.com/travel/flights/search?tfs={_encode_tfs(info)}"

        decoded = GoogleFlightsSearchMatch._decode_google_flights_url(url)

        assert decoded == info

    def test_non_search_path_returns_none(self):
        info = GoogleFlightsSearchMatch._create_base_info(_ONE_WAY_GT_INFO[0])
        url = f"https://www.google.com/travel/flights/explore?tfs={_encode_tfs(info)}"

        assert GoogleFlightsSearchMatch._decode_google_flights_url(url) is None

    def test_missing_tfs_param_returns_none(self):
        url = "https://www.google.com/travel/flights/search?curr=USD"

        assert GoogleFlightsSearchMatch._decode_google_flights_url(url) is None

    def test_empty_tfs_param_returns_none(self):
        url = "https://www.google.com/travel/flights/search?tfs="

        assert GoogleFlightsSearchMatch._decode_google_flights_url(url) is None

    def test_invalid_base64_raises_value_error(self):
        # 5 data characters can never be valid base64 (1 more than a multiple of 4), even
        # after the function's own padding-repair step, so this reliably reaches the
        # `binascii.Error` branch rather than being silently repaired or ignored.
        url = "https://www.google.com/travel/flights/search?tfs=abcde"

        with pytest.raises(ValueError, match="Base64 decoding failed"):
            GoogleFlightsSearchMatch._decode_google_flights_url(url)

    def test_unknown_fields_are_discarded(self):
        # A well-formed but unrecognized protobuf field tag/value appended after a valid
        # message should not break decoding, mirroring real Google Flights URLs that may
        # carry fields this schema doesn't model.
        info = GoogleFlightsSearchMatch._create_base_info(_ONE_WAY_GT_INFO[0])
        raw = info.SerializeToString() + bytes([200, 1, 1])  # unknown field tag 25, varint 1
        tfs = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        url = f"https://www.google.com/travel/flights/search?tfs={tfs}"

        decoded = GoogleFlightsSearchMatch._decode_google_flights_url(url)

        assert decoded == info


class TestUpdate:
    def test_new_valid_url_is_recorded(self):
        metric = GoogleFlightsSearchMatch(gt_info=_ONE_WAY_GT_INFO)
        info = GoogleFlightsSearchMatch._create_base_info(_ONE_WAY_GT_INFO[0])
        url = f"https://www.google.com/travel/flights/search?tfs={_encode_tfs(info)}"

        _run(metric.update(url=url))

        assert metric._url_to_flight_info[url] == info

    def test_missing_url_kwarg_is_ignored(self):
        metric = GoogleFlightsSearchMatch(gt_info=_ONE_WAY_GT_INFO)

        _run(metric.update())

        assert metric._url_to_flight_info == {}

    def test_undecodable_url_is_not_recorded(self):
        metric = GoogleFlightsSearchMatch(gt_info=_ONE_WAY_GT_INFO)

        _run(metric.update(url="https://www.google.com/travel/flights/explore?curr=USD"))

        assert metric._url_to_flight_info == {}

    def test_repeat_url_is_not_redecoded(self):
        metric = GoogleFlightsSearchMatch(gt_info=_ONE_WAY_GT_INFO)
        info = GoogleFlightsSearchMatch._create_base_info(_ONE_WAY_GT_INFO[0])
        url = f"https://www.google.com/travel/flights/search?tfs={_encode_tfs(info)}"

        _run(metric.update(url=url))
        _run(metric.update(url=url))

        assert list(metric._url_to_flight_info.keys()) == [url]


class TestComputeAndResetAndRepr:
    def test_matching_url_among_several_scores_one(self):
        metric = GoogleFlightsSearchMatch(gt_info=_ONE_WAY_GT_INFO)
        matching_info = GoogleFlightsSearchMatch._create_base_info(_ONE_WAY_GT_INFO[0])
        other_info = GoogleFlightsSearchMatch._create_base_info(
            {
                "segments": [{"from": "JFK", "to": "LAX", "date": "2025-12-27"}],
                "passengers": ["ADULT"],
                "seat": "ECONOMY",
                "trip": "ONE_WAY",
            }
        )
        _run(metric.update(url=f"https://www.google.com/travel/flights/search?tfs={_encode_tfs(other_info)}"))
        _run(metric.update(url=f"https://www.google.com/travel/flights/search?tfs={_encode_tfs(matching_info)}"))

        result = _run(metric.compute())

        assert result.score == 1.0

    def test_reset_reverts_compute_to_uncovered(self):
        metric = GoogleFlightsSearchMatch(gt_info=_ONE_WAY_GT_INFO)
        info = GoogleFlightsSearchMatch._create_base_info(_ONE_WAY_GT_INFO[0])
        url = f"https://www.google.com/travel/flights/search?tfs={_encode_tfs(info)}"
        _run(metric.update(url=url))
        assert _run(metric.compute()).score == 1.0

        _run(metric.reset())

        assert metric._url_to_flight_info == {}
        assert _run(metric.compute()).score == 0.0

    def test_repr_labels_attribute_as_gt_info(self):
        metric = GoogleFlightsSearchMatch(gt_info=_ONE_WAY_GT_INFO)

        assert repr(metric) == f"GoogleFlightsSearchMatch(gt_info={metric._gt_base_info})"


class TestResolveDateReferences:
    def test_direct_reference_is_substituted(self):
        gt_info = [{"segments": [{"from": "SFO", "to": "MSP", "date": "departureDate"}]}]

        resolved = resolve_date_references(gt_info, {"departureDate": "2026-01-15"})

        assert resolved[0]["segments"][0]["date"] == "2026-01-15"

    def test_indexed_reference_selects_list_element(self):
        gt_info = [
            {
                "segments": [
                    {"from": "SFO", "to": "MSP", "date": "dateRange.0"},
                    {"from": "MSP", "to": "SFO", "date": "dateRange.1"},
                ]
            }
        ]

        resolved = resolve_date_references(gt_info, {"dateRange": ["2026-01-15", "2026-01-20"]})

        assert resolved[0]["segments"][0]["date"] == "2026-01-15"
        assert resolved[0]["segments"][1]["date"] == "2026-01-20"

    def test_original_gt_info_is_not_mutated(self):
        gt_info = [{"segments": [{"from": "SFO", "to": "MSP", "date": "departureDate"}]}]

        resolve_date_references(gt_info, {"departureDate": "2026-01-15"})

        assert gt_info[0]["segments"][0]["date"] == "departureDate"

    def test_multiple_info_items_each_resolved_independently(self):
        gt_info = [
            {"segments": [{"from": "SFO", "to": "MSP", "date": "outboundDate"}]},
            {"segments": [{"from": "MSP", "to": "SFO", "date": "returnDate"}]},
        ]

        resolved = resolve_date_references(gt_info, {"outboundDate": "2026-01-15", "returnDate": "2026-01-20"})

        assert resolved[0]["segments"][0]["date"] == "2026-01-15"
        assert resolved[1]["segments"][0]["date"] == "2026-01-20"


class TestGenerateTaskConfig:
    def test_wires_placeholder_resolution_into_task_and_gt_info(self):
        from navi_bench.google_flights.google_flights_search_match import generate_task_config

        # 2025-07-10 00:00:00 UTC, used as a fixed "now" so the resolved date is deterministic.
        timestamp = 1752105600

        config = generate_task_config(
            task="Find a one-way flight from SZB to URC on {date}.",
            location="San Francisco, CA, United States",
            timezone="UTC",
            timestamp=timestamp,
            gt_info=[
                {
                    "segments": [{"from": "SZB", "to": "URC", "date": "date"}],
                    "passengers": ["ADULT"],
                    "seat": "ECONOMY",
                    "trip": "ONE_WAY",
                }
            ],
            values={"date": "{now() + timedelta(5)}"},
        )

        assert config.url == "https://www.google.com/travel/flights"
        # Task text uses the natural-language rendering of the resolved date...
        assert "Jul 15" in config.task
        # ...while the eval config (used for verification) gets the precise ISO date.
        assert config.eval_config["gt_info"][0]["segments"][0]["date"] == "2025-07-15"
        assert config.eval_config["_target_"].endswith("GoogleFlightsSearchMatch")
