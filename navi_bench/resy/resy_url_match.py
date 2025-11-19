import csv
import functools
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Optional, Protocol, TypedDict, runtime_checkable
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from beartype import beartype
from loguru import logger
from playwright.async_api import Page
from pydantic import BaseModel

from navi_bench.base import BaseMetric, BaseTaskConfig, UserMetadata, get_import_path
from navi_bench.dates import initialize_placeholder_map, initialize_user_metadata, render_task_statement


@runtime_checkable
class PageLike(Protocol):
    """Protocol for page-like objects that can evaluate JavaScript"""

    async def evaluate(self, script: str) -> Any: ...


class InputDict(TypedDict):
    url: str
    page: Page


class FinalResult(BaseModel):
    score: float  # 1.0 if match, 0.0 if no match


@dataclass
class AvailabilitySlot:
    time: str
    is_visible: bool


@dataclass
class ResyQueryState:
    group_index: int
    alt_index: int
    gt_url: str
    base_without_time: str
    gt_time: Optional[str]
    seen_visible_times: set[str] = field(default_factory=set)
    last_known_times: list[str] = field(default_factory=list)


@beartype
class ResyUrlMatch(BaseMetric):
    def __init__(self, queries: list[list[str]]) -> None:
        """
        Args:
            queries: A list of query groups, where each query group is a list of acceptable URLs.
                    All query groups must be satisfied (AND logic across groups).
                    Within a query group, any URL match counts as success (OR logic within group).

                    Example:
                        queries = [
                            ["url1"],           # Query 1 must be satisfied
                            ["url2a", "url2b"], # Query 2 must be satisfied (either url2a OR url2b)
                            ["url3"]            # Query 3 must be satisfied
                        ]
                        Score is 1.0 only if all 3 queries are satisfied.

                    Automatic "no availability" detection:
                        The metric automatically checks if the page shows "no online availability".
                        If detected, only venue and date need to match (seats and time are ignored),
                        since the entire day is unavailable regardless of party size or time.
                        Otherwise, all parameters (venue, date, seats, time) must match.

                    Conditional success when time differs:
                        If the non-time parts of the URL match the ground truth and either
                        (a) the ground-truth slot becomes visible on the page, or
                        (b) the slot is unavailable but the immediately preceding and following
                            slots are observed in the viewport,
                        the query is marked as covered.
        """
        super().__init__()
        self.queries = queries
        # Track which queries have been covered
        self._is_query_covered: list[bool] = [False] * len(queries)
        self._query_states_by_group = self._build_query_states()
        self._coverage_reasons: list[dict[str, Any] | None] = [None] * len(queries)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(queries={self.queries})"

    @functools.cached_property
    def js_script(self) -> str:
        """Load the JavaScript for checking 'no availability' message"""
        with open(Path(__file__).parent / "resy_no_availability_check.js", "r") as f:
            return f.read()

    @functools.cached_property
    def availability_script(self) -> str:
        """Load the JavaScript for extracting availability metadata."""
        with open(Path(__file__).parent / "resy_availability_extractor.js", "r") as f:
            return f.read()

    async def reset(self) -> None:
        self._is_query_covered = [False] * len(self.queries)
        self._query_states_by_group = self._build_query_states()
        self._coverage_reasons = [None] * len(self.queries)

    async def update(self, **kwargs) -> None:
        inputs: InputDict = kwargs
        url = inputs["url"] or ""
        page = inputs["page"]

        # Check if the page has "no availability" message (default behavior)
        try:
            has_no_availability = await page.evaluate(self.js_script)
            logger.info(f"ResyUrlMatch.update: no_availability={has_no_availability} for URL: {url}")
        except Exception as e:
            logger.warning(f"ResyUrlMatch.update: Could not check no_availability: {e}")
            has_no_availability = False

        availabilities = await self._extract_availabilities(page)
        normalized_url = self._normalize_url(url, ignore_seats_time=has_no_availability)
        normalized_url_without_time = self._normalize_url_without_time(url)
        url_time = self._extract_time_from_url(url)

        logger.debug(
            f"ResyUrlMatch.update url={url} normalized={normalized_url} "
            f"normalized_wo_time={normalized_url_without_time} url_time={url_time} "
            f"availabilities={[f'{slot.time}:{int(slot.is_visible)}' for slot in availabilities]}"
        )

        # Normalize the state URL
        # If "no availability" is detected, use relaxed matching (ignore seats and time)
        # Check against all queries
        for group_index in range(len(self.queries)):
            if self._is_query_covered[group_index]:
                continue  # Skip if already covered

            group_states = self._query_states_by_group[group_index]
            strict_match_found = False

            # Check if the URL matches any alternative in this query group
            for state in group_states:
                gt_url = state.gt_url
                normalized_gt_url = self._normalize_url(gt_url, ignore_seats_time=has_no_availability)
                logger.debug(
                    f"ResyUrlMatch.update comparing normalized_url={normalized_url} "
                    f"with normalized_gt_url={normalized_gt_url} (group={group_index}, alt={state.alt_index})"
                )
                if normalized_url == normalized_gt_url:
                    detail = (
                        "relaxed URL match (no availability detected)"
                        if has_no_availability
                        else "strict URL match with identical parameters"
                    )
                    self._record_coverage(
                        group_index,
                        mode="url_match",
                        reason_code="relaxed_url_match" if has_no_availability else "strict_url_match",
                        detail=f"{detail}; matched {url} against ground truth {gt_url}",
                    )
                    logger.info(
                        f"ResyUrlMatch.update coverage group={group_index} via {detail}: "
                        f"browser_url={url} gt_url={gt_url}"
                    )
                    strict_match_found = True
                    break  # Move to next query group once this one is satisfied

            if strict_match_found:
                logger.debug(f"ResyUrlMatch.update group {group_index} satisfied via strict match")
                continue

            # Attempt conditional success when the time differs but the rest matches
            for state in group_states:
                if not state.base_without_time:
                    continue
                if not normalized_url_without_time:
                    continue
                if normalized_url_without_time != state.base_without_time:
                    continue

                self._update_query_state_visibility(state, availabilities)
                success, reason = self._evaluate_condition(
                    state=state,
                    url_time=url_time,
                    availabilities=availabilities,
                )
                logger.debug(
                    "ResyUrlMatch.update conditional check "
                    f"group={group_index} alt={state.alt_index} success={success} reason={reason} "
                    f"gt_time={state.gt_time} base_without_time={state.base_without_time} "
                    f"visible_times={sorted(state.seen_visible_times)} last_known={state.last_known_times}"
                )
                if success:
                    human_reason = self._describe_conditional_reason(
                        reason=reason,
                        state=state,
                        url_time=url_time,
                        has_availabilities=bool(availabilities),
                    )
                    self._record_coverage(
                        group_index,
                        mode="conditional",
                        reason_code=reason,
                        detail=(
                            f"{human_reason}; base_without_time={state.base_without_time}; "
                            f"gt_time={state.gt_time}; url_time={url_time}"
                        ),
                    )
                    logger.info(
                        "ResyUrlMatch.update conditional coverage "
                        f"group={group_index} alt={state.alt_index} reason={human_reason}; url={url}"
                    )
                    break

    async def compute(self) -> FinalResult:
        # Score is 1.0 only if all queries are covered
        all_covered = all(self._is_query_covered)
        score = 1.0 if all_covered else 0.0
        n_covered = sum(self._is_query_covered)
        result = FinalResult(score=score)
        logger.info(f"ResyUrlMatch.compute result: {result} ({n_covered}/{len(self.queries)} queries covered)")
        for idx, info in enumerate(self._coverage_reasons):
            if info is None:
                logger.info(f"ResyUrlMatch.compute coverage detail q{idx}: NOT COVERED")
            else:
                logger.info(
                    "ResyUrlMatch.compute coverage detail "
                    f"q{idx}: mode={info.get('mode')} reason={info.get('reason_code')} detail={info.get('detail')}"
                )
        return result

    def _build_query_states(self) -> list[list[ResyQueryState]]:
        states: list[list[ResyQueryState]] = []
        for group_index, query_group in enumerate(self.queries):
            group_states: list[ResyQueryState] = []
            for alt_index, gt_url in enumerate(query_group):
                base_without_time = self._normalize_url_without_time(gt_url)
                gt_time = self._extract_time_from_url(gt_url)
                group_states.append(
                    ResyQueryState(
                        group_index=group_index,
                        alt_index=alt_index,
                        gt_url=gt_url,
                        base_without_time=base_without_time,
                        gt_time=gt_time,
                    )
                )
            states.append(group_states)
        return states

    def _normalize_url(self, url: str, ignore_seats_time: bool = False) -> str:
        """
        Normalize Resy URL for comparison.

        Resy URLs have the format:
        https://resy.com/cities/{city}/venues/{venue}?date={date}&seats={seats}&time={time}

        Normalization strategy:
        - Convert to lowercase
        - Remove http/https/www prefix
        - Extract city and venue name from path (e.g., /cities/new-york-ny/venues/carbone)
        - Parse and sort query parameters (date, seats, time)
        - Reconstruct in canonical form: resy.com/cities/{city}/venues/{venue}?date=...&seats=...&time=...

        Args:
            url: The URL to normalize
            ignore_seats_time: If True, exclude 'seats' and 'time' parameters from the normalized URL.
                              This is used when "no availability" is detected, since the entire day is
                              unavailable regardless of party size or time slot.
        """
        if not url:
            return ""

        # Basic normalization
        normalized = url.lower().strip()
        normalized = normalized.lstrip("http://").lstrip("https://").lstrip("www.")

        # Parse URL components
        parsed = urlparse("http://" + normalized)

        # Only apply normalization for resy.com
        if "resy.com" not in parsed.netloc:
            # For non-resy.com URLs, just return basic normalization
            result = parsed.netloc + parsed.path
            if parsed.query:
                result += "?" + parsed.query
            return result.rstrip("/")

        # Extract city and venue name from path
        # Expected format: /cities/{city}/venues/{venue}
        path_parts = [p for p in parsed.path.split("/") if p]
        city_name = None
        venue_name = None

        # Find the city name (comes after "cities" in the path)
        # Find the venue name (comes after "venues" in the path)
        for i, part in enumerate(path_parts):
            if part == "cities" and i + 1 < len(path_parts):
                city_name = path_parts[i + 1]
            if part == "venues" and i + 1 < len(path_parts):
                venue_name = path_parts[i + 1]

        if not venue_name or not city_name:
            # If we can't find the city or venue, return the original path
            logger.warning(f"Could not extract city and/or venue name from URL: {url}")
            result = parsed.netloc + parsed.path.rstrip("/")
        else:
            # Reconstruct with city and venue name in canonical form
            result = f"{parsed.netloc}/cities/{city_name}/venues/{venue_name}"

        # Parse query parameters
        query_params = parse_qs(parsed.query)

        # Extract relevant parameters (date, seats, time)
        # parse_qs returns lists, so we take the first value
        normalized_params = {}

        # Determine which parameters to include based on ignore_seats_time flag
        if ignore_seats_time:
            # Only include date (ignore seats and time when no availability detected)
            params_to_include = ["date"]
        else:
            # Include all parameters for strict matching
            params_to_include = ["date", "seats", "time"]

        for key in params_to_include:
            if key in query_params and query_params[key]:
                normalized_params[key] = query_params[key][0]

        # Add query parameters in sorted order for consistency
        if normalized_params:
            # Sort parameters alphabetically for canonical representation
            sorted_params = sorted(normalized_params.items())
            query_string = "&".join(f"{k}={v}" for k, v in sorted_params)
            result += "?" + query_string
        return result

    def _normalize_url_without_time(self, url: str) -> str:
        normalized = self._normalize_url(url, ignore_seats_time=False)
        return self._remove_query_param(normalized, "time")

    def _remove_query_param(self, url: str, param: str) -> str:
        if not url or "?" not in url:
            return url
        base, query = url.split("?", 1)
        if not query:
            return base
        kept = [p for p in query.split("&") if not p.startswith(f"{param}=")]
        if not kept:
            return base
        return f"{base}?{'&'.join(kept)}"

    async def _extract_availabilities(self, page: PageLike) -> list[AvailabilitySlot]:
        try:
            raw_availabilities = await page.evaluate(self.availability_script)
        except Exception as exc:  # noqa: BLE001 - log and continue
            logger.debug(f"ResyUrlMatch.update: Could not extract availabilities: {exc}")
            return []

        if not isinstance(raw_availabilities, list):
            logger.debug("ResyUrlMatch.update: availability extractor returned non-list")
            return []

        slots: list[AvailabilitySlot] = []
        for entry in raw_availabilities:
            if not isinstance(entry, dict):
                continue
            raw_time = entry.get("time_24")
            normalized_time = self._normalize_time_value(raw_time)
            if not normalized_time:
                continue
            is_visible = bool(entry.get("is_visible"))
            slots.append(AvailabilitySlot(time=normalized_time, is_visible=is_visible))

        slots.sort(key=lambda slot: self._time_to_seconds(slot.time))
        logger.debug(
            "ResyUrlMatch._extract_availabilities parsed "
            f"{len(slots)} slots: {[f'{slot.time}:{int(slot.is_visible)}' for slot in slots]}"
        )
        return slots

    def _update_query_state_visibility(self, state: ResyQueryState, availabilities: list[AvailabilitySlot]) -> None:
        if not availabilities:
            # Keep existing last_known_times to allow neighbor inference when the list is empty.
            return

        unique_times: list[str] = []
        seen_set: set[str] = set()
        for slot in availabilities:
            if slot.time not in seen_set:
                seen_set.add(slot.time)
                unique_times.append(slot.time)
            if slot.is_visible:
                state.seen_visible_times.add(slot.time)

        unique_times.sort(key=self._time_to_seconds)
        state.last_known_times = unique_times
        logger.debug(
            "ResyUrlMatch._update_query_state_visibility "
            f"group={state.group_index} alt={state.alt_index} last_known={state.last_known_times} "
            f"seen_visible={sorted(state.seen_visible_times)}"
        )

    def _evaluate_condition(
        self,
        *,
        state: ResyQueryState,
        url_time: Optional[str],
        availabilities: list[AvailabilitySlot],
    ) -> tuple[bool, str]:
        if not state.gt_time:
            return False, "gt_time_missing"

        if not availabilities:
            # Only succeed if the URL time matches the ground truth time
            # or if there's no URL time (meaning we're on a page without time parameter)
            if url_time and url_time != state.gt_time:
                return False, "no_slots_but_wrong_time"
            return True, "no_available_slots"

        availability_map = {slot.time: slot for slot in availabilities}

        if state.gt_time in availability_map:
            slot = availability_map[state.gt_time]
            logger.debug(
                "ResyUrlMatch._evaluate_condition "
                f"gt_time={state.gt_time} available is_visible={slot.is_visible} url_time={url_time}"
            )
            if url_time and url_time == state.gt_time:
                return True, "gt_time_in_url"
            if slot.is_visible or state.gt_time in state.seen_visible_times:
                return True, "gt_time_visible"
            return False, "gt_time_available_not_seen"

        # Ground-truth time is not available; check neighbor visibility.
        sorted_times = state.last_known_times or sorted(availability_map.keys(), key=self._time_to_seconds)
        prev_time, next_time = self._get_neighbor_times(state.gt_time, sorted_times)
        neighbor_times = [t for t in (prev_time, next_time) if t is not None]

        if prev_time is None and next_time is None:
            logger.debug(
                "ResyUrlMatch._evaluate_condition no neighbors for gt_time=%s (unlikely scenario treated as success)",
                state.gt_time,
            )
            return True, "gt_time_outside_available_range"

        if prev_time is None:
            if next_time in state.seen_visible_times:
                logger.debug(
                    "ResyUrlMatch._evaluate_condition boundary success (virtual previous) "
                    f"gt_time={state.gt_time} next={next_time}"
                )
                return True, "boundary_previous_seen_via_next"
            logger.debug(
                "ResyUrlMatch._evaluate_condition boundary failure (virtual previous unseen) "
                f"gt_time={state.gt_time} next={next_time}"
            )
            return False, f"boundary_previous_not_seen:{next_time}"

        if next_time is None:
            if prev_time in state.seen_visible_times:
                logger.debug(
                    "ResyUrlMatch._evaluate_condition boundary success (virtual next) "
                    f"gt_time={state.gt_time} prev={prev_time}"
                )
                return True, "boundary_next_seen_via_prev"
            logger.debug(
                "ResyUrlMatch._evaluate_condition boundary failure (virtual next unseen) "
                f"gt_time={state.gt_time} prev={prev_time}"
            )
            return False, f"boundary_next_not_seen:{prev_time}"

        unseen_neighbors = [t for t in neighbor_times if t not in state.seen_visible_times]
        if not unseen_neighbors:
            return True, "neighbor_times_seen"
        logger.debug(
            "ResyUrlMatch._evaluate_condition "
            f"gt_time={state.gt_time} unseen_neighbors={unseen_neighbors} "
            f"seen_visible={sorted(state.seen_visible_times)}"
        )
        return False, f"neighbors_not_seen:{','.join(unseen_neighbors)}"

    def _get_neighbor_times(self, gt_time: str, sorted_times: list[str]) -> tuple[Optional[str], Optional[str]]:
        previous: Optional[str] = None
        next_time: Optional[str] = None
        gt_seconds = self._time_to_seconds(gt_time)

        for time_str in sorted_times:
            time_seconds = self._time_to_seconds(time_str)
            if time_seconds < gt_seconds:
                previous = time_str
            elif time_seconds > gt_seconds:
                next_time = time_str
                break

        return previous, next_time

    def _extract_time_from_url(self, url: str) -> Optional[str]:
        if not url:
            return None
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        values = query.get("time")
        if not values:
            return None
        for value in values:
            normalized = self._normalize_time_value(value)
            if normalized:
                return normalized
        return None

    def _normalize_time_value(self, raw_time: Any) -> Optional[str]:
        if raw_time is None:
            return None

        if isinstance(raw_time, (int, float)):
            raw = f"{int(raw_time):04d}"
        else:
            raw = str(raw_time).strip()

        if not raw:
            return None

        raw = raw.replace("%3a", ":").replace("%3A", ":")
        if raw.endswith(("Z", "z")):
            raw = raw[:-1]

        hour: Optional[int] = None
        minute: Optional[int] = None
        second: Optional[int] = None

        if raw.isdigit():
            if len(raw) == 4:
                hour = int(raw[:2])
                minute = int(raw[2:])
                second = 0
            elif len(raw) == 6:
                hour = int(raw[:2])
                minute = int(raw[2:4])
                second = int(raw[4:])
        else:
            parts = raw.split(":")
            if len(parts) >= 2:
                try:
                    hour = int(parts[0])
                    minute = int(parts[1])
                    second = int(parts[2]) if len(parts) > 2 else 0
                except ValueError:
                    return None

        if hour is None or minute is None or second is None:
            return None

        if not (0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60):
            return None

        return f"{hour:02d}:{minute:02d}:{second:02d}"

    def _time_to_seconds(self, time_str: str) -> int:
        hour_str, minute_str, second_str = time_str.split(":")
        hour = int(hour_str)
        minute = int(minute_str)
        second = int(second_str)
        return hour * 3600 + minute * 60 + second

    def _record_coverage(
        self,
        group_index: int,
        *,
        mode: str,
        reason_code: str,
        detail: str,
    ) -> None:
        self._is_query_covered[group_index] = True
        self._coverage_reasons[group_index] = {
            "mode": mode,
            "reason_code": reason_code,
            "detail": detail,
        }

    def _describe_conditional_reason(
        self,
        *,
        reason: str,
        state: ResyQueryState,
        url_time: Optional[str],
        has_availabilities: bool,
    ) -> str:
        mapping = {
            "gt_time_in_url": "available slot matched by URL parameter",
            "gt_time_visible": "available slot visible on page",
            "neighbor_times_seen": "unavailable slot inferred from visible neighboring times",
            "boundary_previous_seen_via_next": "unavailable slot inferred before earliest visible availability",
            "boundary_next_seen_via_prev": "unavailable slot inferred after latest visible availability",
            "gt_time_outside_available_range": "unavailable slot outside listed availability range",
            "no_available_slots": "unavailable slot inferred because page lists no availability",
        }
        base = mapping.get(reason)
        if base:
            return base

        if reason == "gt_time_missing":
            return "ground-truth time missing from configuration"
        if reason == "gt_time_available_not_seen":
            return "available slot exists but was not observed"
        if reason == "no_slots_but_wrong_time":
            return "URL time does not match ground truth and no availability data to verify"
        if reason.startswith("neighbors_not_seen"):
            return (
                f"unavailable slot needs adjacent times to be visible (missing neighbors: {reason.split(':', 1)[-1]})"
            )
        if reason.startswith("boundary_previous_not_seen"):
            missing = reason.split(":", 1)[-1]
            return (
                f"unavailable slot earlier than visible range requires earliest time to be visible (missing: {missing})"
            )
        if reason.startswith("boundary_next_not_seen"):
            missing = reason.split(":", 1)[-1]
            return f"unavailable slot later than visible range requires latest time to be visible (missing: {missing})"

        availability_status = "with availabilities" if has_availabilities else "with no availabilities listed"
        return (
            f"conditional coverage reason={reason} ({availability_status}; "
            f"gt_time={state.gt_time}; url_time={url_time})"
        )


# City to location and timezone mapping
CITY_METADATA = {
    "new york": {"location": "New York, NY, United States", "timezone": "America/New_York", "city_slug": "new-york-ny"},
    "sf": {
        "location": "San Francisco, CA, United States",
        "timezone": "America/Los_Angeles",
        "city_slug": "san-francisco-ca",
    },
}

# Manual mapping of restaurant names to Resy venue slugs
# Based on observed patterns in existing tasks
VENUE_SLUG_MAPPING = {
    "carbone": "carbone",
    "rubirosa": "rubirosa",
    "coqodaq": "coqodaq",
    "lilia": "lilia",
    "4 charles prime rib": "4-charles-prime-rib",
    "torrisi": "torrisi",
    "monkey bar": "monkey-bar-nyc",
    "via carota": "via-carota",
    "le gratin": "le-gratin",
    "crevette": "crevette",
    "pastis": "pastis",
    "misi": "misi",
    "pasquale jones": "pasquale-jones",
    "charlie bird": "charlie-bird",
    "hanoi house": "hanoi-house",
    "laser wolf brooklyn": "laser-wolf-brooklyn",
    "shukette": "shukette",
    "shuka": "shuka",
    "cookshop": "cookshop",
    "jules": "jules",
    "mr. pollo": "mr-pollo",
    "piccino presidio": "piccino-presidio",
    "the morris": "the-morris",
    "collina": "collina",
    "pearl 6101": "pearl",
    "flour + water": "flour-and-water",
    "7 adams": "7-adams",
    "nari": "nari",
    "flour + water pizzeria - north beach": "fw-pizzeria-north-beach",
    "casaro osteria": "casaro-osteria",
    "napizza": "napizza",
    "sisterita": "sisterita",
    "mission chinese food sf": "mission-chinese-food-sf",
    "shuggie's": "shuggies",
    "penny roma": "penny-roma",
    "brenda's meat & three": "brendas-meat-and-three",
    "spqr": "spqr",
    "wizards and wands": "wizards-and-wands",
    "kin khao": "kin-khao",
}


class RestaurantDict(TypedDict):
    city: str
    name: str
    guests_min: int
    guests_max: int
    days_ahead: int


def load_restaurant_metadata() -> dict:
    """
    Load restaurant metadata from CSV file.
    Returns a dictionary mapping (city, restaurant_name_lower) to metadata dict.
    """
    csv_path = Path(__file__).parent / "resy_restaurant.csv"
    metadata = {}

    try:
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                city = row["city"].strip().lower()
                restaurant = row["Restaurant"].strip()

                # Parse numeric values
                guests_min = int(row["Guests Min"]) if row["Guests Min"] else None
                guests_max = int(row["Guests Max"]) if row["Guests Max"] else None
                days_ahead = int(row["Days Ahead"]) if row["Days Ahead"] else None

                # Parse time strings (may be empty)
                open_time = row["Open Time"].strip() or None
                close_time = row["Close Time"].strip() or None

                # Parse closed days (semicolon-separated)
                closed_days = row["Closed Days"].strip().split(";") if row["Closed Days"].strip() else []

                # Store with (city, restaurant_name_lower) as key
                key = (city, restaurant.lower())
                metadata[key] = {
                    "city": city,
                    "name": restaurant,
                    "guests_min": guests_min,
                    "guests_max": guests_max,
                    "days_ahead": days_ahead,
                    "open_time": open_time,
                    "close_time": close_time,
                    "closed_days": closed_days,
                }
    except FileNotFoundError:
        # If CSV not found, return empty dict (will use defaults)
        pass

    return metadata


# Load restaurant metadata at module level
RESTAURANT_METADATA = load_restaurant_metadata()


def parse_time_to_hour(time_str: str) -> float:
    """
    Parse time string like '6:00 AM' or '11:30 PM' to 24-hour format as float.
    Returns hour as float (e.g., 18.0 for 6:00 PM, 18.5 for 6:30 PM).
    """
    if not time_str or not time_str.strip():
        return None

    time_str = time_str.strip()
    # Split into time and period (AM/PM)
    parts = time_str.split()
    if len(parts) != 2:
        return None

    time_part, period = parts
    time_components = time_part.split(":")
    if len(time_components) != 2:
        return None

    hour = int(time_components[0])
    minute = int(time_components[1])

    # Convert to 24-hour format
    if period.upper() == "PM" and hour != 12:
        hour += 12
    elif period.upper() == "AM" and hour == 12:
        hour = 0

    return hour + (minute / 60.0)


def generate_time_slots(open_time: str = None, close_time: str = None) -> list[str]:
    """
    Generate 30-minute time slots between open and close times.
    Returns list of times in HHMM format (e.g., '1800' for 6:00 PM).

    Args:
        open_time: Opening time string like '6:00 AM', or None for default (12pm)
        close_time: Closing time string like '2:00 AM', or None for default (10pm)

    Returns:
        List of time slots in HHMM format
    """
    # Parse times or use defaults (12pm - 10pm)
    open_hour = parse_time_to_hour(open_time) or 12.0
    close_hour = parse_time_to_hour(close_time) or 22.0

    def add_slots(start, end):
        """Helper to add time slots from start to end hour."""
        current, result = start, []
        while current <= end:
            hour, minute = int(current), int((current - int(current)) * 60)
            result.append(f"{hour:02d}{minute:02d}")
            current += 0.5
        return result

    # Handle case where close time is past midnight (e.g., 2:00 AM)
    if close_hour < open_hour:
        return add_slots(open_hour, 23.5) + add_slots(0.0, close_hour)

    return add_slots(open_hour, close_hour)


def format_time_display(time_hhmm: str) -> str:
    """Convert HHMM format to display format like '6:00 PM' or '12:30 PM'."""
    hour = int(time_hhmm[:2])
    minute = int(time_hhmm[2:])

    period = "AM"
    display_hour = hour

    if hour >= 12:
        period = "PM"
        if hour > 12:
            display_hour = hour - 12
    if hour == 0:
        display_hour = 12

    return f"{display_hour}:{minute:02d} {period}"


def select_valid_date(base_date: datetime, date_range: tuple[int, int], closed_days: list[str]) -> datetime:
    """
    Select a random date within the range that doesn't fall on a closed day.

    Args:
        date_range: Tuple of (min_days, max_days) ahead
        closed_days: List of day codes for closed days (e.g., ["M"] for Monday)

    Returns:
        A datetime object for a valid (open) date

    Raises:
        ValueError: If no valid dates available after filtering closed days
    """
    day_mapping = {"M": 0, "T": 1, "W": 2, "Th": 3, "F": 4, "Sa": 5, "Su": 6}

    # Convert closed day codes to weekday numbers
    closed_weekdays = {day_mapping[day.strip()] for day in closed_days if day.strip() in day_mapping}

    # Generate valid dates (filter as we generate)
    valid_dates = [
        date
        for offset in range(date_range[0], date_range[1] + 1)
        if (date := base_date + timedelta(days=offset)).weekday() not in closed_weekdays
    ]

    if not valid_dates:
        raise ValueError(f"No valid open dates found in range {date_range} with closed days {closed_days}")

    return random.choice(valid_dates)


def get_venue_slug(restaurant_name: str) -> str:
    """Get the Resy venue slug for a restaurant name."""
    name_lower = restaurant_name.lower()
    if name_lower in VENUE_SLUG_MAPPING:
        return VENUE_SLUG_MAPPING[name_lower]
    else:
        # Fallback: simple slugification
        slug = name_lower.replace(" ", "-").replace("+", "").replace("'", "").replace("&", "and")
        # Remove special characters
        slug = "".join(c for c in slug if c.isalnum() or c == "-")
        # Remove multiple dashes
        while "--" in slug:
            slug = slug.replace("--", "-")
        return slug.strip("-")


def _get_booking_window_limit(
    restaurant_city: str | None,
    restaurant_name: str | None,
    explicit_limit: int | None = None,
) -> int | None:
    """Resolve the max days ahead allowed, combining CSV metadata and explicit overrides."""
    limit = explicit_limit
    if restaurant_city and restaurant_name:
        metadata = RESTAURANT_METADATA.get((restaurant_city.lower(), restaurant_name.lower()))
        csv_limit = metadata.get("days_ahead") if metadata else None
        if csv_limit:
            limit = min(limit, csv_limit) if limit is not None else csv_limit
    return limit


def _ensure_within_booking_window(
    iso_dates: list[str],
    base_date: date,
    limit: int | None,
    placeholder_key: str,
) -> None:
    """Raise if any resolved date exceeds the allowed booking window."""
    if limit is None:
        return
    invalid = []
    for iso in iso_dates:
        delta = (date.fromisoformat(iso) - base_date).days
        if delta > limit:
            invalid.append((iso, delta))
    if invalid:
        window_desc = f"{limit} days" if limit != 1 else "1 day"
        dates_desc = ", ".join(f"{d} (+{delta})" for d, delta in invalid)
        raise ValueError(
            f"Placeholder '{placeholder_key}' resolved to dates beyond the booking window ({window_desc}): {dates_desc}"
        )


def _render_placeholders_in_queries(
    queries: list[list[str]], template_string: str, rendered_value: str
) -> list[list[str]]:
    """Replace placeholder tokens inside every URL in the nested queries list."""
    new_queries = []
    for group in queries:
        new_group = []
        for url in group:
            new_group.append(url.replace(template_string, rendered_value))
        new_queries.append(new_group)
    return new_queries


def generate_task_config_random(
    restaurant: RestaurantDict,
    date_range: tuple[int, int] | None = None,
    party_size: int | None = None,
    time: str | None = None,
    seed: int | None = None,
    url: str = "https://resy.com",
) -> dict:
    """
    Generate task fields dynamically at runtime.

    This function is designed to be called on-the-fly during training to generate
    fresh task descriptions, eval configs, and user metadata with current dates.

    Args:
        restaurant: Dict with 'city', 'name', 'guests_min', 'guests_max', 'days_ahead',
                   and optionally 'open_time', 'close_time' keys
        date_range: Tuple of (min_days, max_days) ahead to choose from, or None to use
                   restaurant's days_ahead (default: (1, days_ahead))
        party_size: Specific party size, or None to randomly select within range
                   (default: random between guests_min and guests_max-1)
        time: Specific time in HHMM format (e.g., "1800"), or None to randomly select
             from available time slots (respects open/close hours, defaults to 12pm-10pm)
        seed: Random seed for deterministic generation (optional)

    Returns:
        Dict with 'task', 'eval_config', and 'user_metadata' fields to be merged into task object
    """
    # Set random seed if provided for deterministic generation
    if seed is not None:
        random.seed(seed)

    city = restaurant["city"]
    restaurant_name = restaurant["name"]
    guests_min = restaurant["guests_min"]
    guests_max = restaurant["guests_max"]
    days_ahead = restaurant["days_ahead"]

    # Look up restaurant metadata from CSV to get constraints
    csv_metadata = RESTAURANT_METADATA.get((city.lower(), restaurant_name.lower()), {})

    # Cap days_ahead with CSV value (CSV is the max booking window)
    days_ahead = min(days_ahead, csv_metadata["days_ahead"]) if csv_metadata.get("days_ahead") else days_ahead

    # Get optional fields from CSV
    open_time = csv_metadata.get("open_time")
    close_time = csv_metadata.get("close_time")
    closed_days = csv_metadata.get("closed_days", [])

    # Get city metadata
    city_meta = CITY_METADATA.get(city.lower(), CITY_METADATA["sf"])
    city_display = city_meta["location"]
    city_slug = city_meta["city_slug"]

    tz_info = ZoneInfo(city_meta["timezone"])
    today = datetime.now(tz_info)
    timestamp = int(today.timestamp())
    user_metadata = UserMetadata(
        location=city_meta["location"],
        timezone=city_meta["timezone"],
        timestamp=timestamp,
    )

    # Determine party size
    if party_size is None:
        # Randomly select party size (between min and max-1, not inclusive of max)
        party_size = random.randint(guests_min, guests_max - 1) if guests_max > guests_min else guests_min
    else:
        # Clamp to valid range
        party_size = max(guests_min, min(guests_max - 1, party_size))

    # Determine date range
    if date_range is None:
        date_range = (1, days_ahead)
    else:
        # Clamp to days_ahead
        date_range = (max(1, date_range[0]), min(days_ahead, date_range[1]))

    # Select a valid date (avoiding closed days)
    target_date = select_valid_date(today, date_range, closed_days)
    date_str = target_date.strftime("%Y-%m-%d")
    date_display = target_date.strftime("%B %d, %Y")  # e.g., "November 15, 2025"

    # Determine time
    if time is None:
        # Generate time slots using restaurant's hours (or defaults to 12pm-10pm)
        time_slots = generate_time_slots(open_time, close_time)
        time_hhmm = random.choice(time_slots)
    else:
        time_hhmm = time

    time_display = format_time_display(time_hhmm)

    # Get venue slug
    venue_slug = get_venue_slug(restaurant_name)

    # Generate task description
    task_description = (
        f"Check if {restaurant_name} in {city_display.split(',')[0]} has availability on "
        f"{date_display} at {time_display} for {party_size} {'person' if party_size == 1 else 'people'}."
    )

    # Create Resy URL
    resy_url = (
        f"https://resy.com/cities/{city_slug}/venues/{venue_slug}?date={date_str}&seats={party_size}&time={time_hhmm}"
    )

    # Create evaluator config
    eval_target = get_import_path(ResyUrlMatch)
    eval_config = {"_target_": eval_target, "queries": [[resy_url]]}

    return BaseTaskConfig(url=url, task=task_description, user_metadata=user_metadata, eval_config=eval_config)


def _render_placeholders_in_queries_any(
    queries: list[list[str]],
    resolved_placeholders: dict[str, tuple[str, list[str]]],
    base_date: date,
    booking_window: int | None,
) -> list[list[str]]:
    """Replace placeholder template strings in queries with single dates (mode='any').

    Args:
        queries: List of query groups, each containing URL strings
        resolved_placeholders: Dict mapping placeholder keys to (description, iso_dates) tuples
        base_date: Base date for booking window validation
        booking_window: Maximum days ahead for booking (None = no limit)

    Returns:
        Updated queries with placeholders replaced by single dates
    """
    for placeholder_key, (_, dates) in resolved_placeholders.items():
        template_string = "{" + placeholder_key + "}"
        if not dates:
            raise ValueError(f"No future dates resolved for placeholder '{placeholder_key}'")
        if len(dates) != 1:
            raise ValueError(
                "generate_task_config_deterministic (mode='any') expects descriptions resolving to a single date. "
                "Use generate_task_config_deterministic (mode='all') for multi-date placeholders."
            )
        _ensure_within_booking_window(dates, base_date, booking_window, placeholder_key)
        queries = _render_placeholders_in_queries(queries, template_string, dates[0])

    return queries


def _render_placeholders_in_queries_all(
    template_query: list[list[str]],
    resolved_placeholders: dict[str, tuple[str, list[str]]],
    base_date: date,
    booking_window: int | None,
) -> list[list[str]]:
    """Expand queries by creating new URLs for each date (mode='all').

    Args:
        template_query: Single query group with one template URL string
        resolved_placeholders: Dict mapping placeholder keys to (description, iso_dates) tuples
        base_date: Base date for booking window validation
        booking_window: Maximum days ahead for booking (None = no limit)

    Returns:
        Expanded list of queries, one per date
    """
    assert len(template_query) == 1, "Only single query group is supported in the template for multi-date expansion"
    assert len(template_query[0]) == 1, (
        "Only single URL per query group is supported in the template for multi-date expansion"
    )
    template_url = template_query[0][0]

    queries = []
    for placeholder_key, (_, dates) in resolved_placeholders.items():
        template_string = "{" + placeholder_key + "}"
        if not dates:
            raise ValueError(f"No future dates resolved for placeholder '{placeholder_key}'")
        _ensure_within_booking_window(dates, base_date, booking_window, placeholder_key)
        for d in dates:
            queries.append([template_url.replace(template_string, d)])

    return queries


def generate_task_config_deterministic(
    mode: Literal["any", "all"],
    task: str,
    queries: list[list[str]],
    restaurant_city: str,
    restaurant_name: str,
    location: str,
    timezone: str,
    timestamp: int | None = None,
    url: str = "https://resy.com",
    values: dict[str, str] | None = None,
) -> BaseTaskConfig:
    values = values or {}
    if not values:
        raise ValueError("At least one placeholder is required to render the Resy task template.")

    # Get the task start timestamp in the user's timezone
    user_metadata = initialize_user_metadata(timezone, location, timestamp)
    resolved_placeholders, base_date = initialize_placeholder_map(user_metadata, values)

    booking_window = _get_booking_window_limit(restaurant_city, restaurant_name)

    rendered_task = render_task_statement(task, resolved_placeholders)

    if mode == "any":
        # any mode: replace each placeholder with the actual dates
        queries = _render_placeholders_in_queries_any(queries, resolved_placeholders, base_date, booking_window)

    elif mode == "all":
        # all mode: expand the queries to enumerate all combinations
        queries = _render_placeholders_in_queries_all(queries, resolved_placeholders, base_date, booking_window)

    else:
        raise ValueError(f"Invalid mode: {mode}")

    eval_target = get_import_path(ResyUrlMatch)
    eval_config = {"_target_": eval_target, "queries": queries}

    return BaseTaskConfig(url=url, task=rendered_task, user_metadata=user_metadata, eval_config=eval_config)


if __name__ == "__main__":
    import json

    from navi_bench.base import DatasetItem, instantiate

    dataset_row = {
        "task_id": "navi_bench/resy/any_sr_sd_mt_sp/0",
        "task_generation_config_json": json.dumps(
            {
                "_target_": "navi_bench.resy.resy_url_match.generate_task_config_deterministic",
                "mode": "any",
                "task": (
                    "Please look up Flour + Water Pizzeria - North Beach in San Francisco and see if any brunch slots "
                    "are open on {date} for 8 guests."
                ),
                "queries": [
                    [
                        "https://resy.com/cities/san-francisco-ca/venues/fw-pizzeria-north-beach?date={date}&seats=8&time=1000",
                        "https://resy.com/cities/san-francisco-ca/venues/fw-pizzeria-north-beach?date={date}&seats=8&time=1030",
                        "https://resy.com/cities/san-francisco-ca/venues/fw-pizzeria-north-beach?date={date}&seats=8&time=1100",
                        "https://resy.com/cities/san-francisco-ca/venues/fw-pizzeria-north-beach?date={date}&seats=8&time=1130",
                        "https://resy.com/cities/san-francisco-ca/venues/fw-pizzeria-north-beach?date={date}&seats=8&time=1200",
                        "https://resy.com/cities/san-francisco-ca/venues/fw-pizzeria-north-beach?date={date}&seats=8&time=1230",
                        "https://resy.com/cities/san-francisco-ca/venues/fw-pizzeria-north-beach?date={date}&seats=8&time=1300",
                        "https://resy.com/cities/san-francisco-ca/venues/fw-pizzeria-north-beach?date={date}&seats=8&time=1330",
                        "https://resy.com/cities/san-francisco-ca/venues/fw-pizzeria-north-beach?date={date}&seats=8&time=1400",
                    ]
                ],
                "restaurant_city": "sf",
                "restaurant_name": "Flour + Water Pizzeria - North Beach",
                "location": "San Francisco, CA, United States",
                "timezone": "America/Los_Angeles",
                "timestamp": None,
                "values": {"date": "next Thanksgiving"},
            }
        ),
        "env": "real",
        "domain": "resy",
        "l1_category": "food",
        "l2_category": "any_sr_sd_mt_sp",
    }

    dataset_row = {
        "task_id": "navi_bench/resy/all_sr_md_st_sp/0",
        "task_generation_config_json": json.dumps(
            {
                "_target_": "navi_bench.resy.resy_url_match.generate_task_config_deterministic",
                "mode": "all",
                "task": (
                    "See if Torrisi in New York can host 13 people on {dateRange} at 5:30 PM. "
                    "Report the availability for each date."
                ),
                "queries": [
                    [
                        "https://resy.com/cities/new-york-ny/venues/torrisi?date={dateRange}&seats=13&time=1730",
                    ]
                ],
                "restaurant_city": "new york",
                "restaurant_name": "Torrisi",
                "location": "New York, NY, United States",
                "timezone": "America/New_York",
                "timestamp": None,
                "values": {"dateRange": "next Dec 5-10th"},
            }
        ),
        "env": "real",
        "domain": "resy",
        "l1_category": "food",
        "l2_category": "all_sr_md_st_sp",
    }

    dataset_row = {
        "task_id": "navi_bench/resy/any_sr_dd_st_sp/0",
        "task_generation_config_json": json.dumps(
            {
                "_target_": "navi_bench.resy.resy_url_match.generate_task_config_random",
                "restaurant": {
                    "city": "new york",
                    "name": "Carbone",
                    "guests_min": 1,
                    "guests_max": 15,
                    "days_ahead": 28,
                },
                "date_range": [1, 1],
                "party_size": 4,
                "time": "1730",
                "seed": 42,
            }
        ),
        "env": "real",
        "domain": "resy",
        "l1_category": "food",
        "l2_category": "any_sr_dd_st_sp",
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
