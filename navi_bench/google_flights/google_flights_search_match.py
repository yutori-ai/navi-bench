import base64
import urllib.parse
from collections import defaultdict
from copy import deepcopy
from typing import Any, Dict, TypedDict

from beartype import beartype
from loguru import logger
from pydantic import BaseModel

from navi_bench.base import BaseMetric, BaseTaskConfig, get_import_path
from navi_bench.dates import initialize_placeholder_map, initialize_user_metadata, render_task_statement
from navi_bench.google_flights.google_flights_pb2 import Info


"""
Ensure that google_flights_pb2 is compiled from google_flights.proto.

cd navi_bench/google_flights
protoc --python_out=. google_flights.proto
"""


class InputDict(TypedDict, total=False):
    url: str


class FinalResult(BaseModel):
    score: float  # 1.0 if match, 0.0 if no match


@beartype
class GoogleFlightsSearchMatch(BaseMetric):
    def __init__(self, gt_info: list[dict]) -> None:
        """
        Args:
            gt_info: A list of ground truth flight information, where each element is a dictionary
                    containing the following fields:
                    - segments: A list of segments, where each segment represents a flight
                        We expect that dates are already formatted as YYYY-MM-DD.
                    - passengers: A list of passengers, where each passenger is a string
                    - seat: A string
                    - trip: A string
                    We require that the every instantiated Info object is matched to a decoded URL during validation.

                    Example:
                        "gt_info": [
                            {
                                "segments": [
                                {
                                    "from": "SFO",
                                    "to": "MSP",
                                    "date": "2025-12-27",
                                    "max_stops": 0
                                },
                                {
                                    "from": "MSP",
                                    "to": "SFO",
                                    "date": "2025-12-30",
                                    "max_stops": 0
                                }
                                ],
                                "passengers": [
                                    "ADULT"
                                ],
                                "seat": "PREMIUM_ECONOMY",
                                "trip": "ROUND_TRIP"
                            }
                            ]
                        }

        """
        super().__init__()

        # these must parse successfully, otherwise an exception will be raised
        self._gt_base_info = [self._create_base_info(gt_info) for gt_info in gt_info]

        self._url_to_flight_info = defaultdict(Info)

    def __repr__(self) -> str:
        return f"GoogleFlightsSearchMatch(gt_info={self._gt_base_info})"

    @classmethod
    def _decode_google_flights_url(cls, url: str) -> Info | None:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        tfs_param = query.get("tfs", [None])[0]

        # Must also be a "search" page
        if "/flights/search" not in url:
            return None

        # Prevent value error
        if not tfs_param:
            return None

        flight_info = Info()
        padded = tfs_param + "=" * (-len(tfs_param) % 4)
        try:
            raw_bytes = base64.urlsafe_b64decode(padded)
        except Exception as e:
            raise ValueError(f"Base64 decoding failed: {e}")

        # Note, city locations parse to something like /m/01ly5m but are deterministic and
        # can be used for location matching
        flight_info.ParseFromString(raw_bytes)

        # Unknown fields are ignored for comparison
        flight_info.DiscardUnknownFields()
        return flight_info

    @classmethod
    def _create_base_info(self, gt_info: dict) -> Info:
        info = Info()

        for segment in gt_info["segments"]:
            data = info.data.add()
            data.date = segment["date"]
            if "max_stops" in segment:
                data.max_stops = segment["max_stops"]

            data.from_flight.airport = segment["from"]
            data.to_flight.airport = segment["to"]

        for passenger in gt_info["passengers"]:
            info.passengers.append(passenger)

        info.seat = gt_info["seat"]
        info.trip = gt_info["trip"]

        return info

    async def reset(self) -> None:
        self._url_to_flight_info = defaultdict(Info)

    async def update(self, **kwargs) -> None:
        inputs: InputDict = kwargs
        url = inputs.get("url")
        if url and url not in self._url_to_flight_info:
            flight_info = self._decode_google_flights_url(url)
            if flight_info is None:
                return

            self._url_to_flight_info[url] = flight_info
            logger.info(f"GoogleFlightsUrlMatch.update: {url=}")
            logger.info(f"flight_info: {flight_info}")

    async def compute(self) -> FinalResult:
        # Track which Info objects have been covered
        is_info_covered = [False] * len(self._gt_base_info)

        # Every Info object in `self._gt_base_info` must be covered
        for i, gt_info in enumerate(self._gt_base_info):
            # Iterate over
            for url, flight_info in self._url_to_flight_info.items():
                # compare on the FlightInfo, which compares field by field
                if flight_info == gt_info:
                    is_info_covered[i] = True
                    logger.info(
                        f"GoogleFlightsUrlMatch.compute found match for query {i}: "
                        f"url={url}, flight_info={flight_info}, GT={gt_info}"
                    )
                    break

        # Score is 1.0 only if all infos are covered
        all_covered = all(is_info_covered)
        score = 1.0 if all_covered else 0.0
        n_covered = sum(is_info_covered)
        result = FinalResult(score=score)
        logger.info(
            f"GoogleFlightsUrlMatch.compute result: {result} ({n_covered}/{len(self._gt_base_info)} queries covered)"
        )
        return result


def resolve_date_references(gt_info: list[dict], resolved_values: Dict[str, Any]) -> list[dict]:
    """Replace date references like "dateRange.0" with actual dates from resolved_values.

    Args:
        gt_info: List of gt_info dicts with date references
        resolved_values: Dict with resolved dates like {"dateRange": ["2026-06-27", "2026-06-30"]}

    Returns:
        List of gt_info dicts with resolved dates
    """
    resolved_gt_info = deepcopy(gt_info)

    for info_item in resolved_gt_info:
        for segment in info_item["segments"]:
            date_ref = segment["date"]

            # Parse date reference (e.g., "dateRange.0" or "departureDate")
            if "." in date_ref:
                # It's an indexed reference like "dateRange.0"
                key, index = date_ref.split(".", 1)
                index = int(index)
                segment["date"] = resolved_values[key][index]
            else:
                # It's a direct reference like "departureDate"
                segment["date"] = resolved_values[date_ref]

    return resolved_gt_info


def generate_task_config(
    task: str,
    location: str,
    timezone: str,
    timestamp: int | None = None,
    url: str = "https://www.google.com/travel/flights",
    gt_info: list[dict] = [],
    values: dict | None = None,
) -> BaseTaskConfig:
    values = values or {}
    user_metadata = initialize_user_metadata(timezone, location, timestamp)
    resolved_placeholders, _ = initialize_placeholder_map(user_metadata, values)

    # Resolve date references in gt_info with ISO dates
    resolved_values = {}
    for placeholder_key, (_, iso_dates) in resolved_placeholders.items():
        # For gt_info, use endpoints if it's a range, otherwise all dates
        if len(iso_dates) > 2:
            resolved_values[placeholder_key] = [iso_dates[0], iso_dates[-1]]  # endpoints
        elif len(iso_dates) == 2:
            resolved_values[placeholder_key] = iso_dates
        else:
            resolved_values[placeholder_key] = iso_dates[0] if iso_dates else None

    resolved_gt_info = resolve_date_references(gt_info, resolved_values)
    rendered_task = render_task_statement(task, resolved_placeholders)

    eval_target = get_import_path(GoogleFlightsSearchMatch)
    eval_config = {"_target_": eval_target, "gt_info": resolved_gt_info}
    return BaseTaskConfig(url=url, task=rendered_task, user_metadata=user_metadata, eval_config=eval_config)


if __name__ == "__main__":
    import json

    from navi_bench.base import DatasetItem, instantiate

    dataset_row = {
        "task_id": "navi_bench/google_flights/flight_search_budget/0",
        "task_generation_config_json": json.dumps(
            {
                "_target_": "navi_bench.google_flights.google_flights_search_match.generate_task_config",
                "url": "https://www.google.com/travel/flights",
                "task": (
                    "Are there any one-way flights from SZB to URC on {date} that are less than $450? If flight "
                    "options are found, respond with: 'Yes, there is at least one option' followed by the flight "
                    "number and price of one option. If no options are found, respond with exactly: 'No options found'."
                ),
                "location": "San Francisco, CA, United States",
                "timezone": "America/Los_Angeles",
                "values": {"date": "{now() + timedelta(158)}"},
                "gt_info": [
                    {
                        "segments": [{"from": "SZB", "to": "URC", "date": "date"}],
                        "passengers": ["ADULT"],
                        "seat": "ECONOMY",
                        "trip": "ONE_WAY",
                    }
                ],
            }
        ),
        "env": "real",
        "domain": "google_flights",
        "l1_category": "travel",
        "l2_category": "flight_search_budget",
        "suggested_split": "validation",
        "suggested_difficulty": None,
    }

    dataset_row = {
        "task_id": "navi_bench/google_flights/date_range_search_simplified/0",
        "task_generation_config_json": json.dumps(
            {
                "_target_": "navi_bench.google_flights.google_flights_search_match.generate_task_config",
                "url": "https://www.google.com/travel/flights",
                "task": (
                    "Search for round-trip direct First class flights from MAN to YYZ for 3 adult passengers. I want "
                    "to see flight options for {dateRange1}, {dateRange2}, and {dateRange3}. Extract exact prices, "
                    "flight numbers, and departure/arrival times for the cheapest option for each date range (if "
                    "available)."
                ),
                "location": "San Francisco, CA, United States",
                "timezone": "America/Los_Angeles",
                "values": {
                    "dateRange1": "{now() + timedelta(76, 80)} | range=endpoints",
                    "dateRange2": "{now() + timedelta(79, 83)} | range=endpoints",
                    "dateRange3": "{now() + timedelta(82, 86)} | range=endpoints",
                },
                "gt_info": [
                    {
                        "segments": [
                            {"from": "MAN", "to": "YYZ", "date": "dateRange1.0", "max_stops": 0},
                            {"from": "YYZ", "to": "MAN", "date": "dateRange1.1", "max_stops": 0},
                        ],
                        "passengers": ["ADULT", "ADULT", "ADULT"],
                        "seat": "FIRST",
                        "trip": "ROUND_TRIP",
                    },
                    {
                        "segments": [
                            {"from": "MAN", "to": "YYZ", "date": "dateRange2.0", "max_stops": 0},
                            {"from": "YYZ", "to": "MAN", "date": "dateRange2.1", "max_stops": 0},
                        ],
                        "passengers": ["ADULT", "ADULT", "ADULT"],
                        "seat": "FIRST",
                        "trip": "ROUND_TRIP",
                    },
                    {
                        "segments": [
                            {"from": "MAN", "to": "YYZ", "date": "dateRange3.0", "max_stops": 0},
                            {"from": "YYZ", "to": "MAN", "date": "dateRange3.1", "max_stops": 0},
                        ],
                        "passengers": ["ADULT", "ADULT", "ADULT"],
                        "seat": "FIRST",
                        "trip": "ROUND_TRIP",
                    },
                ],
            }
        ),
        "env": "real",
        "domain": "google_flights",
        "l1_category": "travel",
        "l2_category": "date_range_search_simplified",
        "suggested_split": "validation",
        "suggested_difficulty": None,
    }

    dataset_item = DatasetItem.model_validate(dataset_row)
    task_config = dataset_item.generate_task_config()
    evaluator = instantiate(task_config.eval_config)

    print("Loaded dataset item")
    print("-------------------")
    print(dataset_item)
    print()

    print("Generated task config")
    print("---------------------")
    print(task_config)
    print()

    print("Instantiated evaluator")
    print("----------------------")
    print(evaluator)
