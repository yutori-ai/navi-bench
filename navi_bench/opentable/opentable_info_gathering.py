import functools
import itertools
import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from beartype import beartype
from loguru import logger
from playwright.async_api import Page
from pydantic import BaseModel
from typing_extensions import TypedDict

from navi_bench.base import BaseMetric, BaseTaskConfig, UserMetadata, get_import_path
from navi_bench.dates import initialize_placeholder_map, initialize_user_metadata, render_task_statement


class SingleCandidateQuery(TypedDict, total=False):
    restaurant_name: str | None = None
    date: str | None = None
    time: str | None = None
    party_size: int | None = None


class MultiCandidateQuery(TypedDict, total=False):
    restaurant_names: list[str] | None = None  # acceptable names ["chez tj", "chez-tj"]
    dates: list[str] | None = None  # acceptable dates ["2025-06-30", "2025-07-01"]
    times: list[str] | None = None  # acceptable times ["21:00:00", "22:00:00"]
    party_sizes: list[int] | None = None  # acceptable party sizes [4, 5]


class InputDict(TypedDict, total=False):
    page: Page


class InfoDict(TypedDict, total=False):
    url: str
    restaurantName: str
    partySize: int
    info: str
    # single date/time availability
    date: str
    time: str
    # range date/time unavailable, [start, end) are unavailable
    startDate: str  # inclusive
    startTime: str  # inclusive
    endDate: str  # exclusive
    endTime: str  # exclusive


class FinalResult(BaseModel):
    score: float
    n_queries: int
    n_covered: int
    queries: list[list[MultiCandidateQuery]]
    is_query_covered: list[bool]


@beartype
class OpenTableInfoGathering(BaseMetric):
    """Gather restaurant availability information from OpenTable to evaluate query coverage"""

    def __init__(self, queries: list[list[MultiCandidateQuery]]) -> None:
        super().__init__()
        self.queries = queries

        # all the information gathered along the steps
        self._all_infos: list[list[InfoDict]] = []

        # whether the query is covered
        self._is_query_covered: list[bool] = [False] * len(queries)

        # to claim a query is unavailable, we need to collect the evidences to support the claim
        self._unavailable_evidences: list[list[list[InfoDict]]] = [
            [[] for _ in alternative_conditions] for alternative_conditions in queries
        ]

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(queries={self.queries})"

    @functools.cached_property
    def js_script(self) -> str:
        with open(Path(__file__).parent / "opentable_info_gathering.js", "r") as f:
            return f.read()

    async def reset(self) -> None:
        self._all_infos = []
        self._is_query_covered = [False] * len(self.queries)
        self._unavailable_evidences = [[[] for _ in alternative_conditions] for alternative_conditions in self.queries]

    async def update(self, **kwargs) -> None:
        inputs: InputDict = kwargs
        page = inputs["page"]
        infos: list[InfoDict] = await page.evaluate(self.js_script)
        logger.info(f"OpenTableInfoGathering.update gathered {len(infos)} intermediate infos: {infos}")

        self._all_infos.append(infos)

        # Check for "too far in advance" cases
        for info in infos:
            if "take online reservations that far in advance" in info["info"].lower():
                self._handle_too_far_in_advance(info)

            if "your party is too small" in info["info"].lower():
                self._handle_party_too_small_or_too_large(info, issue="too small")

            if "your party is too large" in info["info"].lower():
                self._handle_party_too_small_or_too_large(info, issue="too large")

        for i, alternative_conditions in enumerate(self.queries):
            if self._is_query_covered[i]:
                continue

            for info in infos:
                if self._check_alternative_conditions(i, alternative_conditions, info):
                    logger.info(
                        f"OpenTableInfoGathering.update found {i}-th query covered: {alternative_conditions=}, {info=}"
                    )
                    self._is_query_covered[i] = True
                    break

    def _handle_too_far_in_advance(self, info: InfoDict) -> None:
        """Handle cases where dates are too far in advance to book.

        If we find evidence that a restaurant doesn't take reservations past a certain date,
        mark queries as covered if ALL their dates are >= that date.
        """
        too_far_date = info["date"]
        too_far_restaurant = info["restaurantName"].lower()

        logger.info(f"OpenTableInfoGathering found 'too far in advance' for {too_far_restaurant} on {too_far_date}")

        for i, alternative_conditions in enumerate(self.queries):
            if self._is_query_covered[i]:
                continue

            # Check if ANY alternative condition can be satisfied by this "too far in advance" evidence
            for alternative_condition in alternative_conditions:
                # Check if restaurant name matches
                if query_names := alternative_condition.get("restaurant_names"):
                    query_names = [name.lower() for name in query_names]
                    if too_far_restaurant not in query_names:
                        continue
                else:
                    # Conditions without restaurant names should not be affected by too far in advance
                    continue

                # Check if ALL dates in this alternative condition are >= too_far_date
                if query_dates := alternative_condition.get("dates"):
                    if all(date >= too_far_date for date in query_dates):
                        logger.info(
                            f"OpenTableInfoGathering marking query {i} as covered due to too far in advance: "
                            f"{alternative_condition=}, all dates >= {too_far_date}"
                        )
                        self._is_query_covered[i] = True
                        break

    def _handle_party_too_small_or_too_large(self, info: InfoDict, issue: str = "too small") -> None:
        """Handle cases where the party is too small or too large to book.

        If we find evidence that a restaurant doesn't take reservations for a certain party size,
        mark queries as covered if ALL their party sizes are <= that size.
        """
        party_issue_size = info["partySize"]
        party_issue_restaurant = info["restaurantName"].lower()

        logger.info(
            f"OpenTableInfoGathering found 'party {issue}' for "
            f"{party_issue_restaurant} with party size {party_issue_size}"
        )

        for i, alternative_conditions in enumerate(self.queries):
            if self._is_query_covered[i]:
                continue
            # Check if ANY alternative condition can be satisfied by this party too small/large evidence
            for alternative_condition in alternative_conditions:
                # Check if restaurant name matches
                if query_names := alternative_condition.get("restaurant_names"):
                    query_names = [name.lower() for name in query_names]
                    if party_issue_restaurant not in query_names:
                        continue
                else:
                    # Conditions without restaurant names should not be affected by party too small/large
                    continue

                # Check if ALL party sizes in this alternative condition are
                # <= party_issue_size (if too small) or >= party_issue_size (if too large)
                if query_party_sizes := alternative_condition.get("party_sizes"):
                    if all(
                        party_size <= party_issue_size if issue == "too small" else party_size >= party_issue_size
                        for party_size in query_party_sizes
                    ):
                        logger.info(
                            f"OpenTableInfoGathering marking query {i} as covered due to party {issue}: "
                            f"{alternative_condition=}, "
                            f"all party sizes {'<=' if issue == 'too small' else '>='} {party_issue_size}"
                        )
                        self._is_query_covered[i] = True
                        break

    async def compute(self) -> FinalResult:
        # At the end, for each uncovered query, check if we have exhausted searching for all the alternative conditions
        for i, alternative_conditions in enumerate(self.queries):
            if self._is_query_covered[i]:
                continue
            for j, alternative_condition in enumerate(alternative_conditions):
                if not self._is_exhausted(alternative_condition, self._unavailable_evidences[i][j]):
                    break
            else:
                logger.info(f"OpenTableInfoGathering.compute found {i}-th query exhausted: {alternative_conditions=}")
                self._is_query_covered[i] = True

        n_queries = len(self.queries)
        n_covered = sum(self._is_query_covered)
        final_result = FinalResult(
            score=n_covered / max(n_queries, 1),
            n_queries=n_queries,
            n_covered=n_covered,
            queries=self.queries,
            is_query_covered=self._is_query_covered,
        )
        logger.info(f"OpenTableInfoGathering.compute final result: {final_result}")
        return final_result

    def _check_alternative_conditions(
        self, i: int, alternative_conditions: list[MultiCandidateQuery], info: InfoDict
    ) -> bool:
        """Check if any of the alternative conditions is available and covered by the info"""
        for j, alternative_condition in enumerate(alternative_conditions):
            evidences = self._unavailable_evidences[i][j]
            if self._check_multi_candidate_query(alternative_condition, info, evidences):
                return True
        return False

    @classmethod
    def _check_multi_candidate_query(
        cls, query: MultiCandidateQuery, info: InfoDict, evidences: list[InfoDict]
    ) -> bool:
        """Check if the multi-candidate query is available and covered by the info

        Returns True if the query is available and covered by the info. Otherwise, if the query is covered by
        the info yet unavailable, we need to collect the info as an evidence, before returning False.
        """
        if query_names := query.get("restaurant_names"):
            query_names = [name.lower() for name in query_names]
            if info["restaurantName"].lower() not in query_names:
                return False

        if party_sizes := query.get("party_sizes"):
            if info["partySize"] not in party_sizes:
                return False

        query_dates = query.get("dates")
        query_times = query.get("times")

        available_info = info["info"].lower()

        if "no online availability" in available_info:
            # restaurant is unavailable during a certain time period
            if query_dates and query_times:
                info_min_ts, info_max_ts = cls._parse_date_time_range(info["date"], info["time"], info["info"])
                for date in query_dates:
                    for time in query_times:
                        query_ts = cls._convert_date_time_to_timestamp(date, time)
                        if info_min_ts <= query_ts <= info_max_ts:
                            evidences.append(info)
                            return False
            elif query_dates:
                if info["date"] in query_dates:
                    evidences.append(info)
                    return False
            elif query_times:
                if info["time"] in query_times:
                    evidences.append(info)
                    return False

            return False

        elif (
            "unavailable" in available_info
            and info.get("startDate")
            and info.get("startTime")
            and info.get("endDate")
            and info.get("endTime")
        ):
            # restaurant is unavailable during a certain time period
            if query_dates and query_times:
                start_ts = cls._convert_date_time_to_timestamp(info["startDate"], info["startTime"])
                end_ts = cls._convert_date_time_to_timestamp(info["endDate"], info["endTime"])
                for date in query_dates:
                    for time in query_times:
                        query_ts = cls._convert_date_time_to_timestamp(date, time)
                        if start_ts <= query_ts < end_ts:
                            evidences.append(info)
                            return False
            elif query_dates:
                for query_date in query_dates:
                    if info["startDate"] <= query_date < info["endDate"]:
                        evidences.append(info)
                        return False
            elif query_times:
                for query_time in query_times:
                    if info["startTime"] <= query_time < info["endTime"]:
                        evidences.append(info)
                        return False
            return False

        elif "unavailable" in available_info or "unfortunately" in available_info:
            # restaurant is unavailable at certain date and time
            if query_dates:
                if info["date"] not in query_dates:
                    return False

            if query_times:
                if info["time"] not in query_times:
                    return False

            evidences.append(info)
            return False

        else:
            # restaurant is available
            if query_dates:
                if info["date"] not in query_dates:
                    return False

            if query_times:
                if info["time"] not in query_times:
                    return False

            return True

    @classmethod
    def _check_single_candidate_query(cls, query: SingleCandidateQuery, info: InfoDict) -> bool:
        """Check if the single-candidate query is covered by the info"""
        if (query_name := query.get("restaurant_name")) is not None:
            if info["restaurantName"].lower() != query_name.lower():
                return False

        if (query_party_size := query.get("party_size")) is not None:
            if info["partySize"] != query_party_size:
                return False

        query_date = query.get("date")
        query_time = query.get("time")

        if "no online availability" in info["info"].lower():
            if query_date and query_time:
                info_min_ts, info_max_ts = cls._parse_date_time_range(info["date"], info["time"], info["info"])
                query_ts = cls._convert_date_time_to_timestamp(query_date, query_time)
                if info_min_ts <= query_ts <= info_max_ts:
                    return True
            elif query_date:
                if info["date"] == query_date:
                    return True
            elif query_time:
                if info["time"] == query_time:
                    return True
            return False

        elif (
            "unavailable" in info["info"].lower()
            and info.get("startDate")
            and info.get("startTime")
            and info.get("endDate")
            and info.get("endTime")
        ):
            # restaurant is unavailable during a certain time period
            if query_date and query_time:
                start_ts = cls._convert_date_time_to_timestamp(info["startDate"], info["startTime"])
                end_ts = cls._convert_date_time_to_timestamp(info["endDate"], info["endTime"])
                query_ts = cls._convert_date_time_to_timestamp(query_date, query_time)
                if start_ts <= query_ts < end_ts:
                    return True
            elif query_date:
                if info["startDate"] <= query_date < info["endDate"]:
                    return True
            elif query_time:
                if info["startTime"] <= query_time < info["endTime"]:
                    return True
            return False

        else:
            # restaurant is available at certain date and time
            if query_date:
                if info["date"] != query_date:
                    return False
            if query_time:
                if info["time"] != query_time:
                    return False
            return True

    @classmethod
    def _is_exhausted(cls, query: MultiCandidateQuery, evidences: list[InfoDict]) -> bool:
        """If the query is unavailable, check if we have exhausted searching for its choices"""
        query_names = query.get("restaurant_names")
        if not query_names:
            query_names = [None]

        query_party_sizes = query.get("party_sizes")
        if not query_party_sizes:
            query_party_sizes = [None]

        query_dates = query.get("dates")
        if not query_dates:
            query_dates = [None]

        query_times = query.get("times")
        if not query_times:
            query_times = [None]

        for query_name, query_party_size, query_date, query_time in itertools.product(
            query_names, query_party_sizes, query_dates, query_times
        ):
            # check if the query slot is covered by any of the evidences
            found_match = False
            for info in evidences:
                if cls._check_single_candidate_query(
                    SingleCandidateQuery(
                        restaurant_name=query_name,
                        party_size=query_party_size,
                        date=query_date,
                        time=query_time,
                    ),
                    info,
                ):
                    found_match = True
                    break

            if not found_match:
                return False

        return True

    @classmethod
    def _convert_date_time_to_timestamp(cls, date: str, time: str) -> float:
        return datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M:%S").timestamp()

    @classmethod
    def _parse_date_time_range(cls, date: str, time: str, info: str) -> tuple[float, float]:
        base_ts = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M:%S").timestamp()

        if match := re.search(r"within ([\d\.]+) hours", info):
            hours = float(match.group(1))
            return (base_ts - hours * 3600, base_ts + hours * 3600)
        else:
            logger.warning(f"OpenTableInfoGathering could not parse date time range from info: {info}")
            return base_ts, base_ts


# City to location and timezone mapping
CITY_METADATA = {
    "SF": {"location": "San Francisco, CA, United States", "timezone": "America/Los_Angeles"},
    "NYC": {"location": "New York, NY, United States", "timezone": "America/New_York"},
    "Boston": {"location": "Boston, MA, United States", "timezone": "America/New_York"},
    "Los Angeles": {"location": "Los Angeles, CA, United States", "timezone": "America/Los_Angeles"},
}

# Meal time definitions with corresponding time ranges (15-minute intervals)
MEAL_TIMES = {
    "breakfast": {
        "times": [
            "06:00:00",
            "06:15:00",
            "06:30:00",
            "06:45:00",
            "07:00:00",
            "07:15:00",
            "07:30:00",
            "07:45:00",
            "08:00:00",
            "08:15:00",
            "08:30:00",
            "08:45:00",
            "09:00:00",
            "09:15:00",
            "09:30:00",
            "09:45:00",
            "10:00:00",
        ]
    },
    "brunch": {
        "times": [
            "10:00:00",
            "10:15:00",
            "10:30:00",
            "10:45:00",
            "11:00:00",
            "11:15:00",
            "11:30:00",
            "11:45:00",
            "12:00:00",
            "12:15:00",
            "12:30:00",
            "12:45:00",
            "13:00:00",
            "13:15:00",
            "13:30:00",
            "13:45:00",
            "14:00:00",
        ]
    },
    "lunch": {
        "times": [
            "12:00:00",
            "12:15:00",
            "12:30:00",
            "12:45:00",
            "13:00:00",
            "13:15:00",
            "13:30:00",
            "13:45:00",
            "14:00:00",
        ]
    },
    "dinner": {
        "times": [
            "17:00:00",
            "17:15:00",
            "17:30:00",
            "17:45:00",
            "18:00:00",
            "18:15:00",
            "18:30:00",
            "18:45:00",
            "19:00:00",
            "19:15:00",
            "19:30:00",
            "19:45:00",
            "20:00:00",
        ]
    },
}

# Date options (relative to today)
DATE_OPTIONS = [
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


class RestaurantDict(TypedDict):
    city: str
    name: str
    max_party_size: int


def time_to_natural_language(time_str: str) -> str:
    """
    Convert time string like "18:00" or "18:30" to natural language like "6pm" or "6:30pm".

    Args:
        time_str: Time in HH:MM:SS or HH:MM format

    Returns:
        Natural language time string
    """
    # Parse the time
    parts = time_str.split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0

    # Convert to 12-hour format
    if hour == 0:
        hour_12 = 12
        period = "am"
    elif hour < 12:
        hour_12 = hour
        period = "am"
    elif hour == 12:
        hour_12 = 12
        period = "pm"
    else:
        hour_12 = hour - 12
        period = "pm"

    # Format the output
    if minute == 0:
        return f"{hour_12}{period}"
    else:
        return f"{hour_12}:{minute:02d}{period}"


def is_time_string(s: str) -> bool:
    """Check if string is a time in HH:MM or HH:MM:SS format."""
    return ":" in s and s.replace(":", "").isdigit()


def normalize_time_string(time_str: str) -> str:
    """
    Normalize time string to HH:MM:SS format.

    Args:
        time_str: Time in HH:MM or HH:MM:SS format

    Returns:
        Time in HH:MM:SS format
    """
    parts = time_str.split(":")
    if len(parts) == 2:
        return f"{parts[0]}:{parts[1]}:00"
    return time_str


def get_next_weekend_offsets(today: datetime) -> list[int]:
    """
    Get the day offsets for the next weekend (Saturday and Sunday).
    If today is Saturday or Sunday, returns the following weekend.

    Args:
        today: Timezone-aware datetime for "today" (must be timezone-aware from config).

    Returns:
        List of two integers: [days_to_saturday, days_to_sunday]
    """
    current_day = today.weekday()  # 0=Monday, 6=Sunday

    # Calculate days until next Saturday
    if current_day < 5:  # Monday-Friday
        days_to_sat = 5 - current_day
    else:  # Saturday (5) or Sunday (6)
        days_to_sat = 6 if current_day == 5 else 5  # Skip to next weekend

    return [days_to_sat, days_to_sat + 1]  # [Saturday, Sunday]


def get_first_weekend_of_next_month_offsets(today: datetime) -> list[int]:
    """
    Get the day offsets for the first weekend of the next calendar month (Saturday and Sunday).

    Args:
        today: Timezone-aware datetime for "today" (must be timezone-aware from config).

    Returns:
        List of two integers: [days_to_first_saturday, days_to_first_sunday]
    """
    # Normalize to date only (remove time component)
    today_date = datetime(today.year, today.month, today.day, tzinfo=today.tzinfo)

    # Get the first day of the next calendar month
    if today.month == 12:
        first_of_next_month = datetime(today.year + 1, 1, 1, tzinfo=today.tzinfo)
    else:
        first_of_next_month = datetime(today.year, today.month + 1, 1, tzinfo=today.tzinfo)

    # Find what day of the week the 1st is (0=Monday, 6=Sunday)
    first_day_weekday = first_of_next_month.weekday()

    # Calculate days from the 1st to the first Saturday
    if first_day_weekday <= 5:  # Monday-Saturday
        days_to_first_sat = 5 - first_day_weekday
    else:  # Sunday (6)
        days_to_first_sat = 6  # Next Saturday is 6 days away

    # First Saturday of the next calendar month
    first_saturday = first_of_next_month + timedelta(days=days_to_first_sat)
    first_sunday = first_saturday + timedelta(days=1)

    # Calculate offsets from today (using normalized date)
    days_to_saturday = (first_saturday - today_date).days
    days_to_sunday = (first_sunday - today_date).days

    return [days_to_saturday, days_to_sunday]


def get_days_until_date(date_label: str, today: datetime) -> list[int]:
    """
    Calculate the number of days until the target date(s) based on the label.

    Args:
        date_label: String like "tomorrow", "day after tomorrow", "for the upcoming Monday",
                   "upcoming weekend", "the following weekend", "the next two weekends",
                   or "the first weekend of the next calendar month"
        today: Timezone-aware datetime for "today" (must be timezone-aware from config).

    Returns:
        List of day offsets from today to the target date(s)
    """
    if date_label == "tomorrow":
        return [1]
    elif date_label == "day after tomorrow":
        return [2]
    elif date_label == "upcoming weekend":
        return get_next_weekend_offsets(today)
    elif date_label == "the following weekend":
        # Get upcoming weekend first, then add 7 days
        upcoming = get_next_weekend_offsets(today)
        return [upcoming[0] + 7, upcoming[1] + 7]
    elif date_label == "the next two weekends":
        # Combine upcoming weekend and following weekend
        upcoming = get_next_weekend_offsets(today)
        following = [upcoming[0] + 7, upcoming[1] + 7]
        return upcoming + following
    elif date_label in {"the first weekend of the next calendar month", "the first weekend of next month"}:
        return get_first_weekend_of_next_month_offsets(today)
    elif date_label.startswith("for the upcoming "):
        # Extract weekday name
        weekday_name = date_label.replace("for the upcoming ", "")
        weekday_map = {
            "Monday": 0,
            "Tuesday": 1,
            "Wednesday": 2,
            "Thursday": 3,
            "Friday": 4,
            "Saturday": 5,
            "Sunday": 6,
        }
        target_day = weekday_map[weekday_name]

        # Calculate days until next occurrence of this weekday
        current_day = today.weekday()  # 0=Monday, 6=Sunday
        days_ahead = (target_day - current_day) % 7

        # If the target day is today, we want next week's occurrence
        if days_ahead == 0:
            days_ahead = 7

        return [days_ahead]
    else:
        raise ValueError(f"Unknown date label: {date_label}")


def generate_task_config_random(
    restaurant: RestaurantDict,
    date_options: list[str] | None = None,
    meal_times: list[str] | None = None,
    party_size_range: tuple[int, int] | None = None,
    seed: int | None = None,
    url: str = "https://www.opentable.com",
) -> dict:
    """
    Generate task fields dynamically at runtime.

    This function is designed to be called on-the-fly during training to generate
    fresh task descriptions, eval configs, and user metadata with current dates.

    Args:
        restaurant: Dict with 'city', 'name', and 'max_party_size' keys
        date_options: List of date option strings to choose from (default: all DATE_OPTIONS)
        meal_times: List of meal time keys (e.g., "dinner", "brunch") OR specific times
                   (e.g., "18:00", "18:30") to choose from (default: all MEAL_TIMES)
        party_size_range: Tuple of (min, max) party size (default: (1, max_party_size))
        seed: Random seed for deterministic generation (optional)

    Returns:
        Dict with 'task', 'eval_config', and 'user_metadata' fields to be merged into task object
    """
    # Set random seed if provided for deterministic generation
    if seed is not None:
        random.seed(seed)

    city = restaurant["city"]
    restaurant_name = restaurant["name"]
    max_party_size = restaurant["max_party_size"]

    # Get location metadata for the city
    city_meta = CITY_METADATA.get(city, CITY_METADATA["SF"])  # Default to SF if city not found
    city_display = CITY_METADATA.get(city, {}).get("location", city)

    tz_info = ZoneInfo(city_meta["timezone"])
    today = datetime.now(tz_info)
    timestamp = int(today.timestamp())
    user_metadata = UserMetadata(
        location=city_meta["location"],
        timezone=city_meta["timezone"],
        timestamp=timestamp,
    )

    # Determine party size range
    if party_size_range is None:
        party_size_range = (1, max_party_size)
    else:
        # Clamp to max_party_size
        party_size_range = (max(1, party_size_range[0]), min(max_party_size, party_size_range[1]))

    # Randomly select party size
    party_size = random.randint(party_size_range[0], party_size_range[1])

    # Determine available meal times
    available_meal_times = meal_times if meal_times is not None else list(MEAL_TIMES.keys())

    # Randomly select meal time
    selected_meal_time = random.choice(available_meal_times)

    # Check if it's a specific time (HH:MM format) or a meal type
    if is_time_string(selected_meal_time):
        # It's a specific time like "18:00" or "18:30"
        meal_time_slots = [normalize_time_string(selected_meal_time)]
        meal_time_natural = time_to_natural_language(selected_meal_time)
    else:
        # It's a meal type like "dinner" or "brunch"
        meal_time_slots = MEAL_TIMES[selected_meal_time]["times"]
        meal_time_natural = selected_meal_time

    # Determine available date options
    available_date_options = date_options if date_options is not None else DATE_OPTIONS

    # Randomly select date option
    date_label = random.choice(available_date_options)
    days_offsets = get_days_until_date(date_label, today)

    # Calculate the actual date(s)
    target_dates = [today + timedelta(days=offset) for offset in days_offsets]
    date_strs = [d.strftime("%Y-%m-%d") for d in target_dates]

    # Format natural language date display
    if len(target_dates) == 1:
        date_natural = target_dates[0].strftime("%B %d, %Y")  # e.g., "October 16, 2025"
    elif len(target_dates) == 2:
        # Weekend: "October 18-19, 2025"
        if target_dates[0].month == target_dates[1].month:
            date_natural = f"{target_dates[0].strftime('%B %d')}-{target_dates[1].day}, {target_dates[0].year}"
        else:
            date_natural = f"{target_dates[0].strftime('%B %d')} - {target_dates[1].strftime('%B %d, %Y')}"
    else:
        # Multiple weekends: "October 18-19 and 25-26, 2025"
        date_natural = (
            f"{target_dates[0].strftime('%B %d')}-{target_dates[1].day} "
            f"and {target_dates[2].strftime('%B %d')}-{target_dates[3].day}, {target_dates[0].year}"
        )

    # Generate task description (using both natural language date and actual date)
    task_description = (
        f"Check {restaurant_name} in {city_display} for {meal_time_natural} availability "
        f"{date_label} ({date_natural}) for {party_size} {'person' if party_size == 1 else 'people'}."
    )

    # Create evaluator queries
    eval_target = get_import_path(OpenTableInfoGathering)
    eval_config = {
        "_target_": eval_target,
        "queries": [
            [
                {
                    "restaurant_names": [restaurant_name.lower()],
                    "dates": date_strs,
                    "times": meal_time_slots,
                    "party_sizes": [party_size],
                }
            ]
        ],
    }

    return BaseTaskConfig(url=url, task=task_description, user_metadata=user_metadata, eval_config=eval_config)


def _render_placeholders_in_queries_any(
    queries: list[list[MultiCandidateQuery]], resolved_placeholders: dict[str, tuple[str, list[str]]]
) -> list[list[MultiCandidateQuery]]:
    """Replace placeholder template strings in queries with actual date lists (mode='any').

    Args:
        queries: List of query groups, each containing MultiCandidateQuery dicts
        resolved_placeholders: Dict mapping placeholder keys to (description, iso_dates) tuples

    Returns:
        Updated queries with placeholders replaced by date lists
    """
    for placeholder_key, (_, dates) in resolved_placeholders.items():
        template_string = "{" + placeholder_key + "}"
        if not dates:
            raise ValueError(f"No future dates resolved for placeholder '{placeholder_key}'")

        for query in queries:
            for candidate_obj in query:
                if "dates" in candidate_obj and candidate_obj["dates"] == template_string:
                    candidate_obj["dates"] = dates

    return queries


def _render_placeholders_in_queries_all(
    template_query: list[list[MultiCandidateQuery]], resolved_placeholders: dict[str, tuple[str, list[str]]]
) -> list[list[MultiCandidateQuery]]:
    """Expand queries by creating new query dicts for each date (mode='all').

    Args:
        template_query: Single query group with one template MultiCandidateQuery dict
        resolved_placeholders: Dict mapping placeholder keys to (description, iso_dates) tuples

    Returns:
        Expanded list of queries, one per date
    """
    assert len(template_query) == 1, "Only support single query for now"
    assert len(template_query[0]) == 1, "Only support one candidate object per query for now"
    template_query_dict = template_query[0][0]

    queries = []
    for placeholder_key, (_, dates) in resolved_placeholders.items():
        if not dates:
            raise ValueError(f"No future dates resolved for placeholder '{placeholder_key}'")

        for date in dates:
            for restaurant_name in template_query_dict.get("restaurant_names", [None]):
                for time in template_query_dict.get("times", [None]):
                    for party_size in template_query_dict.get("party_sizes", [None]):
                        query_dict: MultiCandidateQuery = {
                            "restaurant_names": [restaurant_name],
                            "dates": [date],
                            "times": [time],
                            "party_sizes": [party_size],
                        }
                        queries.append([query_dict])

    return queries


def generate_task_config_deterministic(
    mode: Literal["any", "all"],
    task: str,
    queries: list[list[MultiCandidateQuery]],
    location: str,
    timezone: str,
    timestamp: int | None = None,
    url: str = "https://www.opentable.com",
    values: dict[str, str] | None = None,
) -> BaseTaskConfig:
    values = values or {}
    # Get the task start timestamp in the user's timezone
    user_metadata = initialize_user_metadata(timezone, location, timestamp)
    resolved_placeholders, _ = initialize_placeholder_map(user_metadata, values)

    rendered_task = render_task_statement(task, resolved_placeholders)
    if mode == "any":
        # any mode: replace each placeholder with the actual dates
        queries = _render_placeholders_in_queries_any(queries, resolved_placeholders)
    elif mode == "all":
        queries = _render_placeholders_in_queries_all(queries, resolved_placeholders)
    else:
        raise ValueError(f"Invalid mode: {mode}")

    eval_target = get_import_path(OpenTableInfoGathering)
    eval_config = {"_target_": eval_target, "queries": queries}

    return BaseTaskConfig(url=url, task=rendered_task, user_metadata=user_metadata, eval_config=eval_config)


if __name__ == "__main__":
    import json

    from navi_bench.base import DatasetItem, instantiate

    dataset_row = {
        "task_id": "navi_bench/opentable/any_sr_sd_mt_mp/0",
        "task_generation_config_json": json.dumps(
            {
                "_target_": ("navi_bench.opentable.opentable_info_gathering.generate_task_config_deterministic"),
                "mode": "any",
                "url": "https://www.opentable.com",
                "task": (
                    "Search OpenTable for Abrazo in San Francisco. Check if they have dinner availability on "
                    "{PLACEHOLDER_0} for 3-4 people."
                ),
                "queries": [
                    [
                        {
                            "restaurant_names": ["abrazo"],
                            "dates": "{PLACEHOLDER_0}",
                            "times": [
                                "17:00:00",
                                "17:30:00",
                                "18:00:00",
                                "18:30:00",
                                "19:00:00",
                                "19:30:00",
                                "20:00:00",
                            ],
                            "party_sizes": [3, 4],
                        }
                    ]
                ],
                "location": "San Francisco, CA, United States",
                "timezone": "America/Los_Angeles",
                "timestamp": None,
                "PLACEHOLDER_0": "the next Saturday",
            }
        ),
        "env": "real",
        "domain": "opentable",
        "l1_category": "food",
        "l2_category": "any_sr_sd_mt_mp",
    }

    dataset_row = {
        "task_id": "navi_bench/opentable/all_sr_md_mt_sp/0",
        "task_generation_config_json": json.dumps(
            {
                "_target_": ("navi_bench.opentable.opentable_info_gathering.generate_task_config_deterministic"),
                "mode": "all",
                "url": "https://www.opentable.com",
                "task": (
                    "Search for Russell House Tavern in Boston, MA on OpenTable. For a large group of 6, check "
                    "comprehensive dinner availability between 5:00 PM and 6:00 PM for all {PLACEHOLDER_0}. "
                    "Report all available time slots for every date."
                ),
                "queries": [
                    [
                        {
                            "restaurant_names": ["russell house tavern"],
                            "times": ["17:00:00", "17:30:00", "18:00:00"],
                            "party_sizes": [6],
                        }
                    ]
                ],
                "location": "Boston, MA, United States",
                "timezone": "America/New_York",
                "timestamp": None,
                "PLACEHOLDER_0": "Saturdays in the next calendar month",
            }
        ),
        "env": "real",
        "domain": "opentable",
        "l1_category": "food",
        "l2_category": "all_sr_md_mt_sp",
    }

    dataset_row = {
        "task_id": "navi_bench/opentable/random_sr_sd_mt_sp/0",
        "task_generation_config_json": json.dumps(
            {
                "_target_": ("navi_bench.opentable.opentable_info_gathering.generate_task_config_random"),
                "restaurant": {"city": "SF", "name": "Wayfare Tavern", "max_party_size": 8},
                "date_options": ["for the upcoming Wednesday"],
                "meal_times": ["lunch"],
                "party_size_range": [2, 2],
                "seed": 42,
            }
        ),
        "env": "real",
        "domain": "opentable",
        "l1_category": "food",
        "l2_category": "random_sr_sd_mt_sp",
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
